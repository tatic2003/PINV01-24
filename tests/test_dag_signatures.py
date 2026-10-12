"""
Ed25519 signature tests for the BlockDAG:
- signing and verification of nodes
- forged creator / bad signature / unsigned nodes
- strict mode (require_signatures)
- import, storage and key helpers

Project: PINV01-24 - FIUNA
"""

import json
import random

import pytest

from blockdag import signing
from blockdag.dag import BlockDAG, DAGNode
from blockdag.metrics import DAGMetricsCollector
from blockdag.storage import DAGStorage, FileStorageBackend, MemoryStorageBackend


def _keys(*names):
    private = {n: signing.generate_private_key() for n in names}
    public = {n: signing.public_key_of(k) for n, k in private.items()}
    return private, public


def _signed_replicas(names=('A', 'B', 'C'), require=True):
    private, public = _keys(*names)
    return {
        n: BlockDAG(n, signing_key=private[n], trusted_keys=public, require_signatures=require)
        for n in names
    }, private, public


def _full_sync(replicas):
    changed = True
    while changed:
        changed = False
        for a in replicas:
            for b in replicas:
                if a is not b and b.merge_sync_data(a.get_sync_data())['added']:
                    changed = True


# ----------------------------------------------------------------------
# Primitives
# ----------------------------------------------------------------------

class TestSigningPrimitives:

    def test_sign_and_verify(self):
        private = signing.generate_private_key()
        public = signing.public_key_of(private)
        sig = signing.sign(private, b'hello')
        assert signing.verify(public, b'hello', sig)
        assert not signing.verify(public, b'hellO', sig)

    def test_wrong_key_or_garbage_does_not_verify(self):
        a, b = signing.generate_private_key(), signing.generate_private_key()
        sig = signing.sign(a, b'msg')
        assert not signing.verify(signing.public_key_of(b), b'msg', sig)
        assert not signing.verify(signing.public_key_of(a), b'msg', 'basura')
        assert not signing.verify('zz', b'msg', sig)

    def test_load_or_create_is_idempotent(self, tmp_path):
        path = str(tmp_path / 'keys' / 'node.key')
        first = signing.load_or_create_private_key(path)
        again = signing.load_or_create_private_key(path)
        assert first == again and len(first) == 64

    def test_load_trusted_keys_validates(self, tmp_path):
        good = tmp_path / 'good.json'
        good.write_text(json.dumps({'A': signing.public_key_of(signing.generate_private_key())}))
        assert 'A' in signing.load_trusted_keys(str(good))

        bad = tmp_path / 'bad.json'
        bad.write_text(json.dumps({'A': 'abcd'}))
        with pytest.raises(ValueError):
            signing.load_trusted_keys(str(bad))

    def test_build_dag_options(self, tmp_path):
        assert signing.build_dag_options() == {}
        options = signing.build_dag_options(key_file=str(tmp_path / 'k'), require_signatures=True)
        assert set(options) == {'signing_key', 'require_signatures'}


# ----------------------------------------------------------------------
# Nodes
# ----------------------------------------------------------------------

class TestSignedNodes:

    def test_nodes_and_genesis_are_signed_and_verify(self):
        replicas, _, _ = _signed_replicas(('A',))
        a = replicas['A']
        node = a.add_event('e', {'x': 1})
        assert node.signature and a.get_node('A:genesis').signature
        assert a.verify_integrity() == []

    def test_signature_does_not_change_the_hash(self):
        private, _ = _keys('A')
        signed = BlockDAG('A', signing_key=private['A'])
        node = signed.add_event('e', {'x': 1})
        assert node.verify_hash()
        node.signature = 'otra'
        assert node.verify_hash()            # signature is outside the hash...
        assert signed.verify_integrity()     # ...but the DAG still flags it

    def test_trusted_key_for_own_id_must_match_signing_key(self):
        private, public = _keys('A', 'B')
        with pytest.raises(ValueError):
            BlockDAG('A', signing_key=private['A'], trusted_keys={'A': public['B']})

    def test_peer_accepts_valid_signed_nodes(self):
        replicas, _, _ = _signed_replicas(('A', 'B'))
        replicas['A'].add_event('e', {'x': 1})
        result = replicas['B'].merge_sync_data(replicas['A'].get_sync_data())
        assert result['failed'] == 0 and result['added'] >= 2
        assert replicas['B'].verify_integrity() == []

    def test_forged_creator_with_garbage_signature_is_rejected(self):
        replicas, _, _ = _signed_replicas(('A', 'B'))
        b = replicas['B']
        forged = DAGNode(
            node_id='B:fake:000001', event_type='device_register',
            payload={'device_id': 'evil'}, timestamp='2026-01-01T00:00:00',
            creator_id='A', parent_ids=['B:genesis'],
            parent_hashes={'B:genesis': b.get_node('B:genesis').hash},
            signature='basura'
        )
        ok, msg = b.add_external_node(forged)
        assert not ok and 'Signature invalid' in msg

    def test_node_signed_by_another_key_claiming_to_be_a_is_rejected(self):
        replicas, private, public = _signed_replicas(('A', 'B', 'C'))
        c, b = replicas['C'], replicas['B']
        forged = DAGNode(
            node_id='C:claims-a:000001', event_type='state_change',
            payload={'device_id': 'd1'}, timestamp='2026-01-01T00:00:00',
            creator_id='A', parent_ids=['C:genesis'],
            parent_hashes={'C:genesis': c.get_node('C:genesis').hash}
        )
        forged.signature = signing.sign(private['C'], bytes.fromhex(forged.hash))  # C's key, A's name
        b.merge_sync_data(c.get_sync_data())      # B learns C's genesis first
        ok, msg = b.add_external_node(forged)
        assert not ok and 'Signature invalid' in msg

    def test_payload_tampering_after_signing_is_detected(self):
        replicas, _, _ = _signed_replicas(('A', 'B'))
        replicas['A'].add_event('e', {'x': 1})
        data = replicas['A'].get_sync_data()
        victim = next(n for n in data['nodes'] if n['event_type'] == 'e')
        victim['payload'] = {'x': 'changed'}
        result = replicas['B'].merge_sync_data(data)
        assert victim['node_id'] not in replicas['B']
        assert result['failed'] >= 1


class TestRequireSignatures:

    def test_unsigned_node_rejected_in_strict_mode(self):
        replicas, _, _ = _signed_replicas(('A', 'B'))
        b = replicas['B']
        unsigned = DAGNode(
            node_id='A:unsigned:000001', event_type='e', payload={},
            timestamp='2026-01-01T00:00:00', creator_id='A',
            parent_ids=['B:genesis'],
            parent_hashes={'B:genesis': b.get_node('B:genesis').hash}
        )
        ok, msg = b.add_external_node(unsigned)
        assert not ok and 'Signature missing' in msg

    def test_unknown_creator_rejected_in_strict_mode(self):
        private, public = _keys('A', 'B')
        a = BlockDAG('A', signing_key=private['A'], trusted_keys=public, require_signatures=True)
        stranger = BlockDAG('Z', signing_key=signing.generate_private_key())
        stranger.add_event('e', {})
        result = a.merge_sync_data(stranger.get_sync_data())
        assert result['added'] == 0 and result['failed'] >= 1

    def test_optional_mode_accepts_unsigned_but_rejects_bad_signature(self):
        private, public = _keys('A')
        lenient = BlockDAG('B', trusted_keys=public)          # does not require signatures
        plain = BlockDAG('P')
        plain.add_event('e', {})
        assert lenient.merge_sync_data(plain.get_sync_data())['failed'] == 0

        forged = DAGNode(
            node_id='A:forged:000001', event_type='e', payload={},
            timestamp='2026-01-01T00:00:00', creator_id='A',
            parent_ids=['B:genesis'],
            parent_hashes={'B:genesis': lenient.get_node('B:genesis').hash},
            signature='00' * 64
        )
        ok, msg = lenient.add_external_node(forged)
        assert not ok and 'Signature invalid' in msg

    def test_metrics_report_bad_signature(self):
        replicas, _, _ = _signed_replicas(('A', 'B'))
        collector = DAGMetricsCollector(replicas['B'])
        replicas['A'].add_event('e', {})
        data = replicas['A'].get_sync_data()
        for nd in data['nodes']:
            if nd['event_type'] == 'e':
                nd['signature'] = '00' * 64
        replicas['B'].merge_sync_data(data)
        assert 'bad_signature' in collector.get_merge_metrics()['failure_breakdown']

    def test_signed_replicas_converge_and_stay_valid(self):
        replicas, _, _ = _signed_replicas(('A', 'B', 'C'))
        rnd = random.Random(4)
        devices = ['d1', 'd2']
        for _ in range(60):
            r = replicas[rnd.choice('ABC')]
            kind = rnd.choice(['device_register', 'state_change', 'device_remove'])
            r.add_event(kind, {'device_id': rnd.choice(devices), 'new_state': rnd.randint(0, 9)})
        _full_sync(list(replicas.values()))

        a = replicas['A']
        for other in replicas.values():
            assert set(other._nodes) == set(a._nodes)
            assert other.get_total_order() == a.get_total_order()
            assert other.resolve_device_states() == a.resolve_device_states()
            assert other.verify_integrity() == []


# ----------------------------------------------------------------------
# Import / storage
# ----------------------------------------------------------------------

class TestImportAndStorage:

    def test_import_verifies_signatures(self):
        replicas, private, public = _signed_replicas(('A',))
        a = replicas['A']
        a.add_event('e', {'x': 1})
        data = a.export()

        ok = BlockDAG.import_from(data, signing_key=private['A'], trusted_keys=public,
                                  require_signatures=True)
        assert len(ok) == len(a)

        for nd in data['nodes']:
            if nd['event_type'] == 'e':
                nd['signature'] = '00' * 64
        with pytest.raises(ValueError):
            BlockDAG.import_from(data, signing_key=private['A'], trusted_keys=public,
                                 require_signatures=True)

    def test_reloaded_dag_keeps_signing(self):
        private, public = _keys('A')
        options = {'signing_key': private['A'], 'trusted_keys': public, 'require_signatures': True}
        storage = DAGStorage(backend=MemoryStorageBackend(), dag_options=options)
        dag = storage.create('A')
        dag.add_event('e', {'n': 1})
        storage.save('A')
        storage.close('A', save=False)

        reloaded = storage.create('A')
        node = reloaded.add_event('e', {'n': 2})
        assert node.signature
        assert reloaded.verify_integrity() == []

    def test_storage_refuses_to_replace_an_unloadable_dag(self, tmp_path):
        private, public = _keys('A')
        backend = FileStorageBackend(base_path=str(tmp_path))
        plain = DAGStorage(backend=backend)
        plain.create('A').add_event('e', {})
        plain.save('A')

        strict = DAGStorage(
            backend=FileStorageBackend(base_path=str(tmp_path)),
            dag_options={'signing_key': private['A'], 'trusted_keys': public,
                         'require_signatures': True}
        )
        # The stored file is unsigned, so it fails verification in strict mode.
        with pytest.raises(RuntimeError):
            strict.create('A')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
