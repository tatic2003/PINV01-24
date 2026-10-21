"""
Placement experiment: load split between brokers, the DAG agent in the
resource monitor, the placement analysis and its figure.

Project: PINV01-24 - FIUNA
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gen_mod = load('load_generator', 'experiments/load_generator.py')
ap_mod = load('analyze_placement', 'experiments/analyze_placement.py')


# ------------------------------------------------------------------ generator

class FakeClient:
    """Stands in for EmuClient: always connected and acknowledged; records its broker."""

    def __init__(self, name, host, port, keepalive, prefix):
        self.name, self.host = name, host
        self.connected, self.error = True, ''
        self.connect_start_ms, self.connack_ms = 0.0, 1.0
        self.pending = {}

    def connect(self):
        pass

    def publish_announcement(self, msg, row):
        row['publish_ms'] = 1.0
        row['puback_ms'] = 2.0
        row['client_host'] = self.host

    def loop(self):
        pass

    def close(self):
        pass


class Writer:
    def __init__(self):
        self.rows = []

    def writerow(self, row):
        self.rows.append(dict(row))


def ramp_args(**kw):
    base = dict(label='t', host=['a', 'b'], weights=None, port=1883, keepalive=15, topic_prefix='its',
                pool=3, rates=[200.0], step_s=0.2, settle=0.0, timeout=1.0)
    base.update(kw)
    return SimpleNamespace(**base)


class TestHostPicker:
    def test_exact_split_after_one_cycle(self):
        p = gen_mod.HostPicker(['a', 'b'], [89, 11])
        picks = [p.next() for _ in range(100)]
        assert picks.count('a') == 89 and picks.count('b') == 11

    def test_picks_are_spread_not_grouped(self):
        p = gen_mod.HostPicker(['a', 'b'], [1, 1])
        assert [p.next() for _ in range(6)] == ['a', 'b', 'a', 'b', 'a', 'b']
        q = gen_mod.HostPicker(['a', 'b'], [3, 1])
        assert [q.next() for _ in range(8)] == ['a', 'a', 'b', 'a'] * 2

    def test_single_host_and_bad_weights(self):
        p = gen_mod.HostPicker(['only'])
        assert {p.next() for _ in range(5)} == {'only'}
        with pytest.raises(ValueError):
            gen_mod.HostPicker(['a', 'b'], [1])
        with pytest.raises(ValueError):
            gen_mod.HostPicker(['a', 'b'], [1, 0])


class TestGeneratorSplit:
    def test_ramp_splits_by_weight_and_uses_each_hosts_pool(self, monkeypatch):
        monkeypatch.setattr(gen_mod, 'EmuClient', FakeClient)
        w = Writer()
        gen_mod.run_ramp(ramp_args(weights=[3, 1]), gen_mod.AnnouncementSource('testbed'), w)
        rows = w.rows
        assert len(rows) >= 8
        assert all(r['host'] == r['client_host'] for r in rows)
        n_a = sum(r['host'] == 'a' for r in rows)
        assert abs(n_a - 0.75 * len(rows)) <= 1
        assert {r['client'] for r in rows if r['host'] == 'b'} <= {'emu-t-ramp-1-0', 'emu-t-ramp-1-1',
                                                                    'emu-t-ramp-1-2'}

    def test_burst_assigns_a_broker_to_every_device(self, monkeypatch):
        monkeypatch.setattr(gen_mod, 'EmuClient', FakeClient)
        args = SimpleNamespace(label='t', host=['a', 'b'], weights=None, port=1883, keepalive=15,
                               topic_prefix='its', n=[4], repeat=1, timeout=1.0, hold=0.0, settle=0.0)
        w = Writer()
        gen_mod.run_burst(args, gen_mod.AnnouncementSource('testbed'), w)
        assert [r['host'] for r in w.rows] == ['a', 'b', 'a', 'b']
        assert all(r['host'] == r['client_host'] for r in w.rows)

    def test_one_host_keeps_the_former_client_names(self, monkeypatch):
        monkeypatch.setattr(gen_mod, 'EmuClient', FakeClient)
        w = Writer()
        gen_mod.run_ramp(ramp_args(host='x', pool=2), gen_mod.AnnouncementSource('testbed'), w)
        assert {r['client'] for r in w.rows} <= {'emu-t-ramp-0', 'emu-t-ramp-1'}
        assert {r['host'] for r in w.rows} == {'x'}
        assert 'host' in gen_mod.CSV_COLUMNS

    def test_command_line_rejects_mismatched_weights(self, tmp_path):
        with pytest.raises(SystemExit):
            gen_mod.main(['--host', 'a', 'b', '--weights', '1', '--label', 't', '--mode', 'ramp',
                          '--csv', str(tmp_path / 'g.csv')])


def test_resource_monitor_tracks_the_dag_agent():
    pytest.importorskip('psutil')
    rm = load('resource_monitor', 'experiments/resource_monitor.py')
    assert rm.PROCESSES['sync'] == 'dag_sync.py'
    assert {'sync_cpu_pct', 'sync_rss_mb'} <= set(rm.COLUMNS)


# ------------------------------------------------------------------ analysis

ONB_COLS = ['session', 'device_id', 'arrival_epoch_ms', 'callback_epoch_ms', 'http_status',
            'http_rtt_ms', 'srv_total_ms', 'srv_classify_ms', 'srv_shacl_ms']
GEN_COLS = ['session', 'mode', 'level', 'repeat', 'device_id', 'client', 'connect_start_ms',
            'connack_ms', 'publish_ms', 'puback_ms', 'error', 'host']


def synthetic_run(label, hosts, rtt_by_host, cpu_by_host, levels=(2.0, 20.0), n=10, t0=0.0):
    """Generator, onboarding and resources of one run: n announcements per level, published
    1000 ms apart from t0, each registered after rtt ms on its node, with a constant CPU."""
    gen, onb = [], {h: [] for h in hosts}
    t = t0
    k = 0
    for level in levels:
        s = f'load_{label}_ramp_R{level:g}'
        for i in range(n):
            host = hosts[k % len(hosts)]
            k += 1
            d = f'emu-{s}-{i}'
            gen.append([s, 'ramp', level, 1, d, 'c', 0, 0, t, t + 1, '', host])
            onb[host].append([s, d, t, t, 200, rtt_by_host[host] * (1 + level / 20.0), 1, 1, 1])
            t += 1000.0
        t += 5000.0
    res = {h: pd.DataFrame({'epoch_ms': np.arange(t0 - 1000, t + 1000, 500.0),
                            'server_cpu_pct': cpu_by_host[h], 'bridge_cpu_pct': 0.0,
                            'broker_cpu_pct': 0.0, 'sync_cpu_pct': np.nan}) for h in hosts}
    return (pd.DataFrame(gen, columns=GEN_COLS),
            {h: pd.DataFrame(rows, columns=ONB_COLS) for h, rows in onb.items()}, res)


def write_run(tmp_path, name, gen, onb, res, names):
    gen.to_csv(tmp_path / f'gen_{name}.csv', index=False)
    argv = ['--run', name, str(tmp_path / f'gen_{name}.csv')]
    for host, node in names.items():
        onb[host].to_csv(tmp_path / f'onb_{name}_{node}.csv', index=False)
        res[host].to_csv(tmp_path / f'res_{name}_{node}.csv', index=False)
        argv += ['--node', name, node, host, str(tmp_path / f'onb_{name}_{node}.csv'),
                 str(tmp_path / f'res_{name}_{node}.csv')]
    return argv


class TestPlacementAnalysis:
    def test_level_measures(self):
        gen, onb, res = synthetic_run('c', ['h1'], {'h1': 10.0}, {'h1': 40.0})
        nodes = [dict(name='pi5', host='h1', onboarding=onb['h1'], resources=res['h1'])]
        ev = ap_mod.run_events('c', gen, nodes)
        rows = pd.DataFrame(ap_mod.level_rows('c', gen, nodes, ev)).set_index('level')
        r = rows.loc[2.0]
        assert (r['sent'], r['registered'], r['missing']) == (10, 10, 0)
        assert r['latency_mean'] == pytest.approx(11.0)          # 10 * (1 + 2/20)
        assert r['util_max'] == pytest.approx(40.0) and r['busiest_node'] == 'pi5'
        # window: first arrival to last registration = 9 s + 11 ms; 40 % of a core over it
        assert r['cpu_ms_per_reg'] == pytest.approx(0.4 * 9.011 * 1000 / 10)

    def test_sync_cpu_counts_and_missing_columns_are_zero(self):
        res = pd.DataFrame({'epoch_ms': [0.0, 500.0], 'server_cpu_pct': [10.0, 20.0],
                            'sync_cpu_pct': [5.0, np.nan]})
        assert ap_mod.pipeline_cpu(res)['pipeline_cpu_pct'].tolist() == [15.0, 20.0]

    def test_split_counts_per_node_and_busiest_node(self):
        gen, onb, res = synthetic_run('d', ['h1', 'h2'], {'h1': 10.0, 'h2': 100.0},
                                      {'h1': 20.0, 'h2': 90.0})
        nodes = [dict(name='pi5', host='h1', onboarding=onb['h1'], resources=res['h1']),
                 dict(name='opi', host='h2', onboarding=onb['h2'], resources=res['h2'])]
        ev = ap_mod.run_events('d', gen, nodes)
        r = pd.DataFrame(ap_mod.level_rows('d', gen, nodes, ev)).set_index('level').loc[2.0]
        assert (r['sent_pi5'], r['sent_opi'], r['registered_pi5'], r['registered_opi']) == (5, 5, 5, 5)
        assert r['busiest_node'] == 'opi' and r['util_max'] == pytest.approx(90.0)
        assert r['latency_mean'] == pytest.approx((5 * 11.0 + 5 * 110.0) / 10)

    def test_comparison_and_crossover(self):
        levels = pd.DataFrame([
            dict(run='central', level=2.0, latency_mean=20, util_max=10, cpu_ms_per_reg=15),
            dict(run='central', level=20.0, latency_mean=900, util_max=95, cpu_ms_per_reg=16),
            dict(run='central', level=40.0, latency_mean=9000, util_max=99, cpu_ms_per_reg=17),
            dict(run='dist', level=2.0, latency_mean=60, util_max=12, cpu_ms_per_reg=30),
            dict(run='dist', level=20.0, latency_mean=800, util_max=90, cpu_ms_per_reg=20),
            dict(run='dist', level=40.0, latency_mean=900, util_max=80, cpu_ms_per_reg=14),
        ])
        cmp = ap_mod.compare(levels, 'central').set_index('level')
        assert cmp.loc[2.0, 'preferable'] == 'central'
        assert cmp.loc[20.0, 'preferable'] == 'none'          # dist better on 2 of 3
        assert cmp.loc[40.0, 'preferable'] == 'dist'
        s = ap_mod.summarize(cmp.reset_index()).iloc[0]
        assert s['preferable_at_lowest'] == 'central' and s['crossover_level'] == 20.0
        assert s['levels_run_preferable'] == '40'

    def test_no_crossover_when_the_reference_always_wins(self):
        levels = pd.DataFrame([dict(run=r, level=l, latency_mean=v, util_max=v, cpu_ms_per_reg=v)
                               for l in (2.0, 5.0) for r, v in (('central', 1.0), ('dist', 2.0))])
        s = ap_mod.summarize(ap_mod.compare(levels, 'central')).iloc[0]
        assert s['preferable_at_lowest'] == 'central' and np.isnan(s['crossover_level'])

    def test_command_line_writes_outputs_and_figure(self, tmp_path):
        gc, oc, rc = synthetic_run('central', ['localhost'], {'localhost': 10.0}, {'localhost': 30.0})
        gd, od, rd = synthetic_run('dist50', ['localhost', '10.0.0.2'],
                                   {'localhost': 10.0, '10.0.0.2': 100.0},
                                   {'localhost': 15.0, '10.0.0.2': 60.0}, t0=1e6)
        argv = write_run(tmp_path, 'central', gc, oc, rc, {'localhost': 'pi5'})
        argv += write_run(tmp_path, 'dist50', gd, od, rd, {'localhost': 'pi5', '10.0.0.2': 'opi'})
        assert ap_mod.main(argv + ['--reference', 'central', '--out', str(tmp_path / 'o')]) == 0
        for f in ('placement_events.csv', 'placement_levels.csv', 'placement_comparison.csv',
                  'placement_summary.csv', 'fig_placement.pdf', 'fig_placement.png'):
            assert (tmp_path / 'o' / f).exists(), f
        levels = pd.read_csv(tmp_path / 'o' / 'placement_levels.csv')
        assert set(levels['run']) == {'central', 'dist50'} and len(levels) == 4

    def test_command_line_errors(self, tmp_path):
        gc, oc, rc = synthetic_run('central', ['h'], {'h': 10.0}, {'h': 30.0})
        argv = write_run(tmp_path, 'central', gc, oc, rc, {'h': 'pi5'})
        with pytest.raises(SystemExit):
            ap_mod.main(argv + ['--reference', 'nope', '--out', str(tmp_path / 'o')])
        with pytest.raises(SystemExit):
            ap_mod.main(argv + ['--node', 'ghost', 'n', 'h', 'a.csv', 'b.csv', '--out', str(tmp_path / 'o')])
        gc.drop(columns='host').to_csv(tmp_path / 'gen_central.csv', index=False)
        with pytest.raises(SystemExit):
            ap_mod.main(argv + ['--out', str(tmp_path / 'o')])


# ------------------------------------------------------------------ figure

def test_placement_figure_marks_the_offered_rates():
    levels = pd.DataFrame([dict(run=r, level=l, latency_mean=20.0 + l, util_max=l, cpu_ms_per_reg=25.0)
                           for r in ('central', 'dist50') for l in (2.0, 5.0, 10.0, 20.0, 40.0)])
    fig = ap_mod.make_figure(levels)
    for ax in fig.axes:
        assert ax.get_xscale() == 'log'
        assert list(ax.get_xticks()) == [2.0, 5.0, 10.0, 20.0, 40.0]
        fig.canvas.draw()
        assert [t.get_text() for t in ax.get_xticklabels()] == ['2', '5', '10', '20', '40']
        assert len(ax.xaxis.get_minor_ticks()) == 0

def test_make_figures_placement(tmp_path):
    mf = load('make_figures', 'figures/make_figures.py')
    config = yaml.safe_load(open(ROOT / 'figures' / 'config.yml', encoding='utf-8'))
    assert config['placement']['reference'] == 'central'
    assert mf.ALIASES['fig_placement'] == 'fig_placement'
    # Missing data: skipped, or an error with --strict
    p = tmp_path / 'config.yml'
    missing = dict(config, placement={'reference': 'central', 'runs': [
        {'name': 'central', 'generator': 'figures/data/nowhere/g.csv', 'nodes': []}]})
    p.write_text(yaml.safe_dump(missing), encoding='utf-8')
    assert mf.main(['fig_placement', '--config', str(p), '--out', str(tmp_path / 'o')]) == 0
    assert mf.main(['fig_placement', '--config', str(p), '--out', str(tmp_path / 'o'), '--strict']) == 1
    # With data: the figure is drawn and every input is in the manifest
    data = tmp_path / 'data'
    data.mkdir()
    gc, oc, rc = synthetic_run('central', ['localhost'], {'localhost': 10.0}, {'localhost': 30.0})
    gc.to_csv(data / 'g.csv', index=False)
    oc['localhost'].to_csv(data / 'o.csv', index=False)
    rc['localhost'].to_csv(data / 'r.csv', index=False)
    present = dict(config, placement={'reference': 'central', 'runs': [
        {'name': 'central', 'generator': str(data / 'g.csv'),
         'nodes': [{'name': 'pi5', 'host': 'localhost', 'onboarding': str(data / 'o.csv'),
                    'resources': str(data / 'r.csv')}]}]})
    p.write_text(yaml.safe_dump(present), encoding='utf-8')
    assert mf.main(['fig_placement', '--config', str(p), '--out', str(tmp_path / 'o2'), '--strict']) == 0
    assert (tmp_path / 'o2' / 'fig_placement.pdf').exists()
    manifest = pd.read_csv(tmp_path / 'o2' / 'MANIFEST.csv')
    assert (manifest['figure'] == 'fig_placement').sum() == 3