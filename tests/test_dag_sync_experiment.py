"""
Partition/convergence experiment: DAG digest and rejection reasons, the
status and sync endpoints, the synchronization agent, the partition rules
and the analysis.

Project: PINV01-24 - FIUNA
"""

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from blockdag.dag import BlockDAG, DAGNode
from blockdag.signing import generate_private_key, public_key_of

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'experiments' / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dag_sync = load('dag_sync')
rp = load('run_partition')
ana = load('analyze_convergence')


def sync(dst, src):
    return dst.merge_sync_data(src.get_sync_data(peer_tips=dst.get_tips()))


# ------------------------------------------------------------------ BlockDAG

class TestDigestAndReasons:
    def test_digest_equal_only_for_the_same_dag(self):
        a, b = BlockDAG('a'), BlockDAG('b')
        a.add_event('x', {'i': 1})
        b.add_event('y', {'i': 1})
        assert a.state_digest() != b.state_digest()
        sync(a, b)
        sync(b, a)
        assert a.state_digest() == b.state_digest()
        assert sorted(a.get_tips()) == sorted(b.get_tips())
        assert a.node_ids() == b.node_ids()

    def test_digest_covers_node_hashes(self):
        # Same node ids, one node with different content (an equivocation).
        a = BlockDAG('a')
        a.add_event('x', {'i': 1})
        b = BlockDAG.import_from(a.export())
        assert a.state_digest() == b.state_digest()
        node = b.get_node(b.get_tips()[0])
        node._hash = 'f' * 64
        assert a.node_ids() == b.node_ids()
        assert a.state_digest() != b.state_digest()

    def test_failed_reasons_missing_parents(self):
        a, b = BlockDAG('a'), BlockDAG('b')
        for i in range(3):
            a.add_event('x', {'i': i})
        data = a.get_sync_data()
        # Drop a's genesis: its children cannot be attached.
        data['nodes'] = [n for n in data['nodes'] if n['event_type'] != 'genesis']
        result = b.merge_sync_data(data)
        assert result['failed'] == 3
        assert result['failed_reasons'] == {'missing_parents': 3}

    def test_failed_reasons_bad_signature(self):
        ka, kb = generate_private_key(), generate_private_key()
        a = BlockDAG('a', signing_key=ka)
        # b trusts a different key for 'a' and requires signatures.
        b = BlockDAG('b', signing_key=kb, require_signatures=True,
                     trusted_keys={'a': public_key_of(generate_private_key())})
        a.add_event('x', {})
        result = sync(b, a)
        assert result['failed_reasons'].get('bad_signature', 0) >= 1
        assert result['added'] == 0

    def test_clean_merge_has_no_reasons(self):
        a, b = BlockDAG('a'), BlockDAG('b')
        a.add_event('x', {})
        result = sync(b, a)
        assert result['failed'] == 0 and result['failed_reasons'] == {}


# ------------------------------------------------------------------ server

def make_app(node_id):
    from server.app import create_app
    from server.config import Config
    config = Config(debug=False, data_dir='/tmp/its-test-data', model_dir='/tmp/its-test-models',
                    auto_train=False, classifier_type='rule_based', enable_dag=True, node_id=node_id)
    app = create_app(config)
    app.config['TESTING'] = True
    return app


@pytest.fixture
def two_apps():
    return make_app(f'a-{uuid.uuid4().hex[:8]}'), make_app(f'b-{uuid.uuid4().hex[:8]}')


class TestEndpoints:
    def test_status_has_tips_and_digest(self, two_apps):
        app, _ = two_apps
        c = app.test_client()
        st = c.get('/api/dag/status').get_json()
        assert st['tips'] == sorted(app.dag.get_tips())
        assert st['digest'] == app.dag.state_digest()
        assert 'node_ids' not in st
        assert c.get('/api/dag/status?ids=1').get_json()['node_ids'] == app.dag.node_ids()

    def test_get_with_peer_tips_sends_only_the_delta(self, two_apps):
        app, _ = two_apps
        c = app.test_client()
        app.dag.add_event('x', {'i': 1})
        tips_before = app.dag.get_tips()
        app.dag.add_event('x', {'i': 2})
        full = c.post('/api/dag/sync', json={'action': 'get'}).get_json()
        delta = c.post('/api/dag/sync', json={'action': 'get', 'peer_tips': tips_before}).get_json()
        assert len(full['nodes']) == 3
        assert [n['payload'] for n in delta['nodes']] == [{'i': 2}]

    def test_peer_tips_must_be_a_list(self, two_apps):
        c = two_apps[0].test_client()
        r = c.post('/api/dag/sync', json={'action': 'get', 'peer_tips': 'x'})
        assert r.status_code == 400

    def test_merge_returns_reasons(self, two_apps):
        a, b = two_apps
        a.dag.add_event('x', {})
        data = a.dag.get_sync_data()
        r = b.test_client().post('/api/dag/sync', json={'action': 'merge', 'sync_data': data})
        assert r.get_json()['failed_reasons'] == {}


# ------------------------------------------------------------------ agent

class Router:
    """requests.Session look-alike that routes URLs to Flask test clients."""

    def __init__(self, apps):
        self.clients = {f'http://{name}': app.test_client() for name, app in apps.items()}
        self.down = set()

    def _route(self, url):
        for base, client in self.clients.items():
            if url.startswith(base):
                if base in self.down:
                    import requests
                    raise requests.ConnectionError('partitioned')
                return client, url[len(base):]
        raise AssertionError(url)

    @staticmethod
    def _wrap(resp):
        def raise_for_status():
            if resp.status_code >= 400:
                import requests
                raise requests.HTTPError(str(resp.status_code))
        return SimpleNamespace(status_code=resp.status_code, content=resp.data,
                               json=resp.get_json, raise_for_status=raise_for_status)

    def get(self, url, params=None, timeout=None):
        client, path = self._route(url)
        return self._wrap(client.get(path, query_string=params))

    def post(self, url, json=None, timeout=None):
        client, path = self._route(url)
        return self._wrap(client.post(path, json=json))


class TestAgent:
    def test_two_agents_converge_and_partition_is_logged(self, two_apps):
        a, b = two_apps
        router = Router({'a': a, 'b': b})
        agent_a = dag_sync.SyncAgent('http://a', ['http://b'], session=router)
        agent_b = dag_sync.SyncAgent('http://b', ['http://a'], session=router)
        a.dag.add_event('x', {'side': 'a'})
        b.dag.add_event('y', {'side': 'b'})
        ra = agent_a.sync_once('http://b')
        rb = agent_b.sync_once('http://a')
        assert ra['error'] == '' and ra['added'] == 2 and ra['failed'] == 0
        assert rb['added'] == 2
        assert a.dag.state_digest() == b.dag.state_digest()

        # One more round each: what a peer resends of the other's own nodes
        # merged in the meantime is reported as existing, never re-added.
        r2 = agent_a.sync_once('http://b')
        assert r2['added'] == 0 and r2['failed'] == 0
        agent_b.sync_once('http://a')

        # Converged: nothing more to send, even after a local event of a.
        a.dag.add_event('x', {'side': 'a', 'n': 2})
        assert agent_a.sync_once('http://b')['offered'] == 0
        assert agent_b.sync_once('http://a')['offered'] == 1

        # Partition: the round fails and says why.
        router.down.add('http://b')
        row = agent_a.sync_once('http://b')
        assert row['error'] == 'ConnectionError'
        assert set(dag_sync.COLUMNS) == set(row)


# ------------------------------------------------------------------ run_partition

class TestRunPartition:
    def test_iptables_rules_and_cleanup(self, monkeypatch):
        monkeypatch.setattr(rp, 'is_root', lambda: True)
        calls, present = [], []

        def runner(cmd, check=True, capture_output=True):
            calls.append(cmd)
            op, rule = cmd[1], cmd[2:]
            if op == '-I':
                present.append(rule)
            elif op == '-D':
                present.remove(rule)
            elif op == '-C':
                return SimpleNamespace(returncode=0 if rule in present else 1)
            return SimpleNamespace(returncode=0)

        part = rp.IptablesPartition('192.168.0.30', 5000, 'reject', runner=runner)
        part.block()
        assert len(present) == 2
        assert ['INPUT', '-s', '192.168.0.30', '-p', 'tcp', '--dport', '5000',
                '-j', 'REJECT', '--reject-with', 'tcp-reset'] in present
        assert any(r[0] == 'OUTPUT' and r[2] == '192.168.0.30' for r in present)
        part.block()          # a duplicate is also removed
        part.unblock()
        assert present == []
        drop = rp.IptablesPartition('10.0.0.2', 5000, 'drop', runner=runner)
        assert drop.rules[0][-1] == 'DROP'

    def test_sudo_when_not_root(self, monkeypatch):
        monkeypatch.setattr(rp, 'is_root', lambda: False)
        seen = []
        part = rp.IptablesPartition('10.0.0.2', 5000, runner=lambda cmd, **k: seen.append(cmd)
                                    or SimpleNamespace(returncode=1))
        part.block()
        assert seen[0][:3] == ['sudo', '-n', 'iptables']

    def test_counting_nodes_by_creation_time(self):
        ids = ['pi5:genesis', 'opi:genesis',
               'pi5:20261020120000:000001', 'pi5:20261020120005:000002',
               'opi:20261020120003:000001', 'opi:20261020120010:000002']
        t0 = rp.node_epoch_s('pi5:20261020120000:000001') * 1000
        assert rp.node_epoch_s('pi5:genesis') is None
        assert rp.count_created(ids, 'pi5', t0, t0 + 5400) == 2
        assert rp.count_created(ids, 'opi', t0, t0 + 5400) == 1
        assert rp.count_after(ids, t0 + 5400) == 1      # opi at +10 s

    def test_same_dag(self):
        a = {'digest': 'd', 'tips': ['x', 'y']}
        assert rp.same_dag(a, {'digest': 'd', 'tips': ['y', 'x']})
        assert not rp.same_dag(a, {'digest': 'e', 'tips': ['x', 'y']})

    def test_generator_command(self):
        cmd = rp.generator_cmd('10.0.0.2', 'conv_opi_M10_r1', 10, 'g.csv', 1884)
        assert cmd[1].endswith('load_generator.py')
        for flag, val in [('--host', '10.0.0.2'), ('--port', '1884'), ('--n', '10'),
                          ('--mode', 'burst'), ('--label', 'conv_opi_M10_r1')]:
            assert cmd[cmd.index(flag) + 1] == val


# ------------------------------------------------------------------ analysis

def test_analysis(tmp_path):
    runs = pd.DataFrame([
        dict(m=10, repeat=k, t_block_ms=0, t_unblock_ms=10_000 * k, t_converged_ms=10_000 * k + c,
             convergence_ms=c, converged=True, polls=3, nodes_local=20, nodes_peer=20,
             late_nodes=0, total_nodes=100, gen_local_rc=0, gen_peer_rc=0)
        for k, c in [(1, 400.0), (2, 600.0), (3, 800.0)]])
    rows = []
    for k in (1, 2, 3):
        base = dict(offered=20, added=20, existing=0, failed=0, missing_parents=0, bad_signature=0,
                    hash_conflict=0, broken_link=0, hash_failed=0, other=0, error='')
        rows.append(dict(base, epoch_ms=10_000 * k + 300, agent_name='pi5'))
        rows.append(dict(base, epoch_ms=10_000 * k + 350, agent_name='opi'))
    rows.append(dict(rows[0], epoch_ms=10_000 + 200, offered=1, added=0, failed=1, bad_signature=1,
                     agent_name='pi5'))
    rows.append(dict(rows[0], epoch_ms=5_000, offered=0, added=0, error='ConnectionError',
                     agent_name='pi5'))
    df = pd.DataFrame(rows)
    paths = []
    for name in ('pi5', 'opi'):
        p = tmp_path / f'{name}.csv'
        df[df.agent_name == name].drop(columns='agent_name').to_csv(p, index=False)
        paths.append((name, p))
    rp_path = tmp_path / 'runs.csv'
    runs.to_csv(rp_path, index=False)

    agents = ana.load_agents(paths)
    out = ana.attach_recovery(pd.read_csv(rp_path), agents)
    assert out['added_pi5'].tolist() == [20, 20, 20]
    assert out['bad_signature'].tolist() == [1, 0, 0]
    summ = ana.summarize(out).iloc[0]
    assert summ['convergence_median_ms'] == 600.0
    assert summ['converged'] == 3 and summ['bad_signature'] == 1
    totals = ana.total_rejections(agents).set_index('agent')
    assert totals.loc['pi5', 'rounds_with_error'] == 1
    assert ana.main(['--runs', str(rp_path), '--agent', 'pi5', str(paths[0][1]),
                     '--agent', 'opi', str(paths[1][1]), '--out', str(tmp_path / 'o')]) == 0
    assert (tmp_path / 'o' / 'fig_convergence.pdf').exists()
