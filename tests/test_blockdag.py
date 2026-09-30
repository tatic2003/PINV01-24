"""
Tests for BlockDAG Module
"""

import pytest
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from blockdag import BlockDAG, DAGNode, DAGStorage
from blockdag.storage import FileStorageBackend, MemoryStorageBackend


class TestDAGNode:
    """Tests for DAGNode class."""

    def test_node_creation(self):
        """Test basic node creation."""
        node = DAGNode(
            node_id='test-001',
            event_type='test_event',
            payload={'key': 'value'},
            timestamp='2026-01-28T12:00:00',
            creator_id='node-01',
            parent_ids=[]
        )

        assert node.node_id == 'test-001'
        assert node.event_type == 'test_event'
        assert node.hash is not None

    def test_hash_computation(self):
        """Test hash is computed correctly."""
        node = DAGNode(
            node_id='test-001',
            event_type='test_event',
            payload={'key': 'value'},
            timestamp='2026-01-28T12:00:00',
            creator_id='node-01',
            parent_ids=['parent-001']
        )

        expected_hash = node.compute_hash()
        assert node.hash == expected_hash

    def test_hash_verification(self):
        """Test hash verification."""
        node = DAGNode(
            node_id='test-001',
            event_type='test_event',
            payload={'key': 'value'},
            timestamp='2026-01-28T12:00:00',
            creator_id='node-01',
            parent_ids=[]
        )

        assert node.verify_hash()

    def test_node_serialization(self):
        """Test node to_dict and from_dict."""
        original = DAGNode(
            node_id='test-001',
            event_type='test_event',
            payload={'device_id': 'esp32-01'},
            timestamp='2026-01-28T12:00:00',
            creator_id='node-01',
            parent_ids=['genesis']
        )

        data = original.to_dict()
        restored = DAGNode.from_dict(data)

        assert restored.node_id == original.node_id
        assert restored.event_type == original.event_type
        assert restored.hash == original.hash


class TestBlockDAG:
    """Tests for BlockDAG class."""

    def test_dag_creation(self):
        """Test DAG initialization."""
        dag = BlockDAG(node_id='test-node')

        assert len(dag) == 1  # Genesis node
        assert 'genesis' in dag.get_tips()[0]

    def test_add_event(self):
        """Test adding an event."""
        dag = BlockDAG(node_id='test-node')

        node = dag.add_event('device_register', {
            'device_id': 'esp32-01',
            'hardware': 'ESP32'
        })

        assert node is not None
        assert node.event_type == 'device_register'
        assert len(dag) == 2

    def test_multiple_events(self):
        """Test adding multiple events."""
        dag = BlockDAG(node_id='test-node')

        for i in range(5):
            dag.add_event('test_event', {'index': i})

        assert len(dag) == 6  # Genesis + 5 events

    def test_get_node(self):
        """Test retrieving a node."""
        dag = BlockDAG(node_id='test-node')

        added = dag.add_event('test_event', {'data': 'test'})
        retrieved = dag.get_node(added.node_id)

        assert retrieved is not None
        assert retrieved.node_id == added.node_id

    def test_tips(self):
        """Test DAG tips tracking."""
        dag = BlockDAG(node_id='test-node')

        # Initially, genesis is the only tip
        tips = dag.get_tips()
        assert len(tips) == 1

        # Add event - new event becomes tip
        dag.add_event('event1', {})
        tips = dag.get_tips()
        assert len(tips) == 1

    def test_topological_order(self):
        """Test topological ordering."""
        dag = BlockDAG(node_id='test-node')

        dag.add_event('event1', {})
        dag.add_event('event2', {})
        dag.add_event('event3', {})

        order = dag.get_topological_order()

        # Genesis should be first
        assert 'genesis' in order[0]
        assert len(order) == 4

    def test_events_by_type(self):
        """Test filtering events by type."""
        dag = BlockDAG(node_id='test-node')

        dag.add_event('device_register', {'device': '1'})
        dag.add_event('state_change', {'device': '1'})
        dag.add_event('device_register', {'device': '2'})

        register_events = dag.get_events_by_type('device_register')

        assert len(register_events) == 2

    def test_sync_data_export(self):
        """Test sync data export."""
        dag = BlockDAG(node_id='test-node')

        dag.add_event('event1', {})
        dag.add_event('event2', {})

        sync_data = dag.get_sync_data()

        assert 'nodes' in sync_data
        assert 'tips' in sync_data
        assert sync_data['node_count'] == 3

    def test_sync_merge(self):
        """Test merging sync data between DAGs."""
        dag1 = BlockDAG(node_id='node-1')
        dag2 = BlockDAG(node_id='node-2')

        # Add events to dag1
        dag1.add_event('from_node_1', {'data': '1'})
        dag1.add_event('from_node_1', {'data': '2'})

        # Sync to dag2
        sync_data = dag1.get_sync_data()
        result = dag2.merge_sync_data(sync_data)

        assert result['added'] >= 2  # At least 2 events added
        assert len(dag2) > 1

    def test_add_external_node(self):
        """Test adding an external node."""
        dag = BlockDAG(node_id='test-node')
        genesis_id = dag.get_tips()[0]

        external_node = DAGNode(
            node_id='external-001',
            event_type='external_event',
            payload={'source': 'external'},
            timestamp='2026-01-28T12:00:00',
            creator_id='other-node',
            parent_ids=[genesis_id]
        )

        success, message = dag.add_external_node(external_node)

        assert success
        assert 'external-001' in dag

    def test_export_import(self):
        """Test DAG export and import."""
        original = BlockDAG(node_id='test-node')
        original.add_event('event1', {'key': 'value1'})
        original.add_event('event2', {'key': 'value2'})

        exported = original.export()
        restored = BlockDAG.import_from(exported)

        assert len(restored) == len(original)
        assert restored.node_id == original.node_id


class TestDAGStorage:
    """Tests for DAG storage."""

    def test_memory_storage(self):
        """Test in-memory storage."""
        backend = MemoryStorageBackend()
        storage = DAGStorage(backend=backend)

        dag = storage.create('test-node')
        dag.add_event('test', {})

        storage.save('test-node')

        # Close and reload
        storage.close('test-node', save=False)
        reloaded = storage.load('test-node')

        assert reloaded is not None
        assert len(reloaded) > 1

    def test_file_storage(self):
        """Test file-based storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = FileStorageBackend(base_path=tmpdir)
            storage = DAGStorage(backend=backend)

            dag = storage.create('test-node')
            dag.add_event('test', {'data': 'value'})

            storage.save('test-node')

            # Create new storage and load
            storage2 = DAGStorage(backend=FileStorageBackend(base_path=tmpdir))
            loaded = storage2.load('test-node')

            assert loaded is not None
            assert len(loaded) == len(dag)

    def test_storage_stats(self):
        """Test storage statistics."""
        backend = MemoryStorageBackend()
        storage = DAGStorage(backend=backend)

        storage.create('node-1')
        storage.create('node-2')

        stats = storage.get_stats()

        assert stats['loaded_dags'] == 2
        assert 'node-1' in stats['dags']
        assert 'node-2' in stats['dags']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
