"""
Consistency tests for the BlockDAG:
- tamper-evident hash links
- frontier-based (complete) partial sync
- rebuild of tips on import, independent of timestamps
- collision-free node ids
- deterministic total order
- remove-wins conflict resolution
- randomized convergence of several replicas

Project: PINV01-24 - FIUNA
"""

import random

import pytest

from blockdag.dag import BlockDAG, DAGNode


def _sync(src: BlockDAG, dst: BlockDAG, delta: bool = True) -> dict:
    """Send src's nodes to dst (only the delta dst lacks when delta=True)."""
    data = src.get_sync_data(peer_tips=dst.get_tips()) if delta else src.get_sync_data()
    return dst.merge_sync_data(data)


def _full_sync(replicas) -> None:
    """Exchange everything until nothing changes."""
    changed = True
    while changed:
        changed = False
        for a in replicas:
            for b in replicas:
                if a is b:
                    continue
                if _sync(a, b, delta=False)['added']:
                    changed = True


def _ids(dag: BlockDAG) -> set:
    return set(dag._nodes)


# ----------------------------------------------------------------------
# Hash chain
# ----------------------------------------------------------------------

class TestHashChain:

    @staticmethod
    def _chain():
        dag = BlockDAG('A')
        n1 = dag.add_event('device_register', {'device_id': 'cam1', 'cls': 'camera'})
        n2 = dag.add_event('state_change', {'device_id': 'cam1', 'new_state': 'on'})
        n3 = dag.add_event('state_change', {'device_id': 'cam1', 'new_state': 'off'})
        return dag, n1, n2, n3

    def test_new_nodes_commit_to_parent_hashes(self):
        dag, n1, n2, _ = self._chain()
        assert n2.parent_hashes == {n1.node_id: n1.hash}
        assert dag.verify_integrity() == []

    def test_rewriting_an_ancestor_is_detected(self):
        dag, n1, n2, n3 = self._chain()
        # The attacker rewrites n1 AND recomputes n1's own hash.
        n1.payload['cls'] = 'traffic_light'
        n1._hash = n1.compute_hash()

        assert n1.verify_hash()          # the forged node looks fine on its own...
        problems = dag.verify_integrity()  # ...but its child no longer matches
        assert {'node_id': n2.node_id, 'problem': 'broken_link'} in problems

    def test_receiver_rejects_node_with_wrong_parent_hash(self):
        dag, n1, _, _ = self._chain()
        other = BlockDAG('B')
        _sync(dag, other, delta=False)

        forged = DAGNode(
            node_id='A:forged:000001', event_type='state_change',
            payload={'device_id': 'cam1', 'new_state': 'evil'},
            timestamp='2026-01-01T00:00:00', creator_id='A',
            parent_ids=[n1.node_id], parent_hashes={n1.node_id: 'deadbeef'}
        )
        ok, msg = other.add_external_node(forged)
        assert not ok and 'Parent hash mismatch' in msg

    def test_receiver_rejects_node_without_parent_hashes(self):
        dag, n1, _, _ = self._chain()
        stripped = DAGNode(
            node_id='A:stripped:000001', event_type='state_change', payload={},
            timestamp='2026-01-01T00:00:00', creator_id='A', parent_ids=[n1.node_id]
        )
        ok, msg = dag.add_external_node(stripped)
        assert not ok and 'Parent hash mismatch' in msg

    def test_same_id_with_different_hash_is_reported(self):
        dag, n1, _, _ = self._chain()
        peer = BlockDAG('B')
        _sync(dag, peer, delta=False)

        data = dag.get_sync_data()
        for nd in data['nodes']:
            if nd['node_id'] == n1.node_id:
                nd['payload'] = {'device_id': 'cam1', 'cls': 'traffic_light'}
                forged = DAGNode.from_dict(nd)
                forged._hash = forged.compute_hash()   # hash is self-consistent
                nd['hash'] = forged.hash

        result = peer.merge_sync_data(data)
        assert result['failed'] >= 1
        # the peer keeps its original version
        assert peer.get_node(n1.node_id).payload['cls'] == 'camera'

    def test_import_rejects_tampered_dag(self):
        dag, n1, n2, _ = self._chain()
        data = dag.export()
        for nd in data['nodes']:
            if nd['node_id'] == n1.node_id:
                nd['payload'] = {'device_id': 'cam1', 'cls': 'traffic_light'}
                forged = DAGNode.from_dict(nd)
                forged._hash = forged.compute_hash()
                nd['hash'] = forged.hash

        with pytest.raises(ValueError):
            BlockDAG.import_from(data)
        # explicit opt-out still loads it
        assert len(BlockDAG.import_from(data, verify=False)) == len(dag)

    def test_export_import_roundtrip_is_clean(self):
        dag, *_ = self._chain()
        restored = BlockDAG.import_from(dag.export())
        assert restored.verify_integrity() == []
        assert set(restored.get_tips()) == set(dag.get_tips())


# ----------------------------------------------------------------------
# Frontier based sync
# ----------------------------------------------------------------------

class TestFrontierSync:

    def test_since_includes_concurrent_branch(self):
        a = BlockDAG('A')
        g = 'A:genesis'
        a1 = a.add_event('e', {'k': 1}, parent_ids=[g])
        a2 = a.add_event('e', {'k': 2}, parent_ids=[g])   # concurrent with a1

        b = BlockDAG('B')
        b.merge_sync_data({'nodes': [a.get_node(g).to_dict(), a1.to_dict()]})

        delta = a.get_sync_data(since_node_id=a1.node_id)
        assert a2.node_id in {n['node_id'] for n in delta['nodes']}
        assert b.merge_sync_data(delta)['failed'] == 0
        assert a2.node_id in b

    def test_delta_excludes_what_the_peer_has(self):
        a = BlockDAG('A')
        for i in range(10):
            a.add_event('e', {'i': i})
        b = BlockDAG('B')
        _sync(a, b)
        a.add_event('e', {'i': 'new'})

        delta = a.get_sync_data(peer_tips=b.get_tips())
        assert len(delta['nodes']) == 1

    def test_unknown_frontier_gives_full_export(self):
        a = BlockDAG('A')
        a.add_event('e', {})
        data = a.get_sync_data(peer_tips=['nope'])
        assert len(data['nodes']) == len(a)

    def test_tips_exchange_alone_converges_after_partition(self):
        a, b = BlockDAG('A'), BlockDAG('B')
        for i in range(15):
            a.add_event('e', {'a': i})
            b.add_event('e', {'b': i})
        _sync(a, b)
        _sync(b, a)
        assert _ids(a) == _ids(b)
        assert set(a.get_tips()) == set(b.get_tips())


# ----------------------------------------------------------------------
# Import / ids
# ----------------------------------------------------------------------

class TestImportAndIds:

    def test_tips_correct_with_skewed_clock(self):
        dag = BlockDAG('D')
        p = dag.add_event('e', {'n': 'parent'}, parent_ids=['D:genesis'])
        c = dag.add_event('e', {'n': 'child'}, parent_ids=[p.node_id])
        before = set(dag.get_tips())

        data = dag.export()
        for nd in data['nodes']:
            if nd['node_id'] == c.node_id:
                nd['timestamp'] = '2020-01-01T00:00:00'   # device with a slow clock
                node = DAGNode.from_dict(nd)
                node._hash = node.compute_hash()
                nd['hash'] = node.hash
        # the child's hash changed, so re-link is not needed: it is a leaf
        restored = BlockDAG.import_from(data)

        assert set(restored.get_tips()) == before
        assert restored.get_height(c.node_id) == 2

    def test_no_id_collision_after_reload(self):
        dag = BlockDAG('E')
        first = dag.add_event('e', {'a': 1})
        restored = BlockDAG.import_from(dag.export())
        second = restored.add_event('e', {'a': 2})

        assert second.node_id != first.node_id
        assert restored.get_node(first.node_id).payload == {'a': 1}
        assert restored.get_node(second.node_id).payload == {'a': 2}

    def test_add_event_rejects_unknown_parent(self):
        dag = BlockDAG('E')
        with pytest.raises(ValueError):
            dag.add_event('e', {}, parent_ids=['ghost'])

    def test_ancestors_are_not_truncated(self):
        dag = BlockDAG('L')
        last = None
        for i in range(250):
            last = dag.add_event('e', {'i': i})
        assert len(dag.get_ancestors(last.node_id)) == 250   # 249 events + genesis


# ----------------------------------------------------------------------
# Deterministic order
# ----------------------------------------------------------------------

class TestDeterministicOrder:

    @staticmethod
    def _wide_dag():
        rnd = random.Random(5)
        dag = BlockDAG('S')
        for i in range(60):
            tips = sorted(dag.get_tips())
            parents = rnd.sample(tips, k=min(len(tips), rnd.randint(1, 2)))
            dag.add_event('e', {'i': i}, parent_ids=parents)
        # many siblings of the genesis => lots of concurrency
        for i in range(20):
            dag.add_event('e', {'sib': i}, parent_ids=['S:genesis'])
        return dag

    def test_same_nodes_same_order_whatever_the_arrival(self):
        source = self._wide_dag()
        data = source.get_sync_data()
        orders = set()
        for seed in range(8):
            nodes = data['nodes'][:]
            random.Random(seed).shuffle(nodes)
            replica = BlockDAG('R')
            replica._nodes.clear()
            replica._rebuild_indexes()
            replica.merge_sync_data({**data, 'nodes': nodes})
            orders.add(tuple(replica.get_total_order()))
        assert len(orders) == 1
        assert list(orders.pop()) == source.get_total_order()

    def test_order_is_topological(self):
        dag = self._wide_dag()
        position = {nid: i for i, nid in enumerate(dag.get_topological_order())}
        for node in dag._nodes.values():
            for parent in node.parent_ids:
                assert position[parent] < position[node.node_id]

    def test_height_is_longest_path_from_a_root(self):
        dag = BlockDAG('H')
        a = dag.add_event('e', {}, parent_ids=['H:genesis'])
        b = dag.add_event('e', {}, parent_ids=[a.node_id])
        c = dag.add_event('e', {}, parent_ids=['H:genesis'])
        d = dag.add_event('e', {}, parent_ids=[b.node_id, c.node_id])
        assert [dag.get_height(n.node_id) for n in (a, b, c, d)] == [1, 2, 1, 3]


# ----------------------------------------------------------------------
# remove-wins
# ----------------------------------------------------------------------

def _two_replicas_with_registered_device():
    a, b = BlockDAG('A'), BlockDAG('B')
    reg = a.add_event('device_register', {'device_id': 'd1', 'hardware': 'esp32'})
    _sync(a, b)
    return a, b, reg


class TestRemoveWins:

    def test_plain_lifecycle_without_remove(self):
        dag = BlockDAG('A')
        dag.add_event('device_register', {'device_id': 'd1', 'hardware': 'esp32'})
        dag.add_event('state_change', {'device_id': 'd1', 'new_state': 'on'})
        dag.add_event('state_change', {'device_id': 'd1', 'new_state': 'off'})
        s = dag.resolve_device_states()['d1']
        assert s['status'] == 'active' and s['state'] == 'off'
        assert s['info'] == {'hardware': 'esp32'}

    def test_remove_beats_concurrent_state_change(self):
        a, b, _ = _two_replicas_with_registered_device()
        rm = a.add_event('device_remove', {'device_id': 'd1'})
        b.add_event('state_change', {'device_id': 'd1', 'new_state': 'on'})   # concurrent
        _sync(a, b); _sync(b, a)

        for dag in (a, b):
            s = dag.resolve_device_states()['d1']
            assert s['status'] == 'removed'
            assert s['overridden_by_remove'] == 1
            assert s['removes'] == [rm.node_id]

    def test_remove_beats_concurrent_register(self):
        a, b = BlockDAG('A'), BlockDAG('B')
        a.add_event('device_remove', {'device_id': 'd1'})
        b.add_event('device_register', {'device_id': 'd1', 'hardware': 'esp32'})
        _sync(a, b); _sync(b, a)
        assert a.resolve_device_states()['d1']['status'] == 'removed'
        assert a.resolve_device_states() == b.resolve_device_states()

    def test_state_change_after_remove_without_register_is_ignored(self):
        dag = BlockDAG('A')
        dag.add_event('device_register', {'device_id': 'd1'})
        dag.add_event('device_remove', {'device_id': 'd1'})
        dag.add_event('state_change', {'device_id': 'd1', 'new_state': 'on'})  # causally after
        assert dag.resolve_device_states()['d1']['status'] == 'removed'

    def test_register_causally_after_remove_revives(self):
        dag = BlockDAG('A')
        dag.add_event('device_register', {'device_id': 'd1', 'hardware': 'old'})
        dag.add_event('device_remove', {'device_id': 'd1'})
        dag.add_event('device_register', {'device_id': 'd1', 'hardware': 'new'})
        dag.add_event('state_change', {'device_id': 'd1', 'new_state': 'on'})
        s = dag.resolve_device_states()['d1']
        assert s['status'] == 'active'
        assert s['info'] == {'hardware': 'new'} and s['state'] == 'on'

    def test_register_concurrent_with_second_remove_does_not_revive(self):
        a, b = BlockDAG('A'), BlockDAG('B')
        first = a.add_event('device_remove', {'device_id': 'd1'})
        _sync(a, b)
        # B re-registers after the first remove; A removes again meanwhile.
        b.add_event('device_register', {'device_id': 'd1', 'hardware': 'again'},
                    parent_ids=[first.node_id])
        a.add_event('device_remove', {'device_id': 'd1'})
        _sync(a, b); _sync(b, a)
        assert a.resolve_device_states()['d1']['status'] == 'removed'

    def test_concurrent_state_changes_resolve_identically(self):
        a, b, _ = _two_replicas_with_registered_device()
        a.add_event('state_change', {'device_id': 'd1', 'new_state': 'A-wrote'})
        b.add_event('state_change', {'device_id': 'd1', 'new_state': 'B-wrote'})
        _sync(a, b); _sync(b, a)

        sa, sb = a.resolve_device_states()['d1'], b.resolve_device_states()['d1']
        assert sa == sb
        assert sa['state'] in ('A-wrote', 'B-wrote')


# ----------------------------------------------------------------------
# Randomized convergence
# ----------------------------------------------------------------------

class TestRandomizedConvergence:

    @pytest.mark.parametrize('seed', range(12))
    def test_replicas_converge_to_same_order_and_state(self, seed):
        rnd = random.Random(seed)
        replicas = [BlockDAG(f'R{i}') for i in range(5)]
        devices = ['d1', 'd2', 'd3']

        for _ in range(120):
            r = rnd.choice(replicas)
            action = rnd.random()
            device = rnd.choice(devices)
            if action < 0.25:
                r.add_event('device_register', {'device_id': device, 'hardware': f'hw-{rnd.randint(0, 9)}'})
            elif action < 0.75:
                r.add_event('state_change', {'device_id': device, 'new_state': f's{rnd.randint(0, 99)}'})
            elif action < 0.85:
                r.add_event('device_remove', {'device_id': device})
            else:
                # partial sync between two random replicas (partitions heal slowly)
                src, dst = rnd.sample(replicas, 2)
                _sync(src, dst)

        _full_sync(replicas)

        first = replicas[0]
        for other in replicas[1:]:
            assert _ids(other) == _ids(first)
            assert set(other.get_tips()) == set(first.get_tips())
            assert other.get_total_order() == first.get_total_order()
            assert other.resolve_device_states() == first.resolve_device_states()
        assert all(r.verify_integrity() == [] for r in replicas)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
