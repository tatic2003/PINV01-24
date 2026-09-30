"""
Edge Agents Tests
Project: PINV01-24 - FIUNA

Tests for edge agent components: base agent, discovery, classification, sync, and manager.
"""

import pytest
import asyncio
import sys
import os
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from edge_agents.base_agent import EdgeAgent, AgentConfig, AgentMessage, AgentState
from edge_agents.discovery_agent import DiscoveryAgent, DiscoveryConfig
from edge_agents.classification_agent import ClassificationAgent, ClassificationConfig
from edge_agents.sync_agent import SyncAgent, SyncConfig, DAGEvent
from edge_agents.agent_manager import EdgeAgentManager


# =====================
# AgentConfig / AgentMessage / AgentState
# =====================

class TestAgentConfig:
    def test_default_config(self):
        config = AgentConfig()
        assert config.agent_type == "base"
        assert config.node_id == "unknown"
        assert config.update_interval == 10.0
        assert config.retry_interval == 5.0
        assert config.max_retries == 3
        assert config.log_level == "INFO"
        assert isinstance(config.agent_id, str)

    def test_custom_config(self):
        config = AgentConfig(
            agent_id="test-01",
            agent_type="discovery",
            node_id="node-01",
            update_interval=5.0,
            metadata={"key": "value"}
        )
        assert config.agent_id == "test-01"
        assert config.agent_type == "discovery"
        assert config.node_id == "node-01"
        assert config.metadata == {"key": "value"}


class TestAgentMessage:
    def test_create_message(self):
        msg = AgentMessage(
            source_agent="agent-1",
            target_agent="agent-2",
            message_type="test",
            payload={"data": 123}
        )
        assert msg.source_agent == "agent-1"
        assert msg.target_agent == "agent-2"
        assert msg.message_type == "test"
        assert msg.payload == {"data": 123}
        assert isinstance(msg.timestamp, str)
        assert isinstance(msg.message_id, str)

    def test_broadcast_message(self):
        msg = AgentMessage(
            source_agent="agent-1",
            target_agent=None,
            message_type="broadcast",
            payload={}
        )
        assert msg.target_agent is None


class TestAgentState:
    def test_states(self):
        assert AgentState.INITIALIZED.value == "initialized"
        assert AgentState.STARTING.value == "starting"
        assert AgentState.RUNNING.value == "running"
        assert AgentState.PAUSED.value == "paused"
        assert AgentState.STOPPING.value == "stopping"
        assert AgentState.STOPPED.value == "stopped"
        assert AgentState.ERROR.value == "error"


# =====================
# Concrete Agent for testing base class
# =====================

class ConcreteAgent(EdgeAgent):
    """Concrete implementation for testing the abstract base class."""
    def __init__(self, config=None, setup_result=True):
        super().__init__(config)
        self._setup_result = setup_result
        self.process_count = 0

    async def setup(self) -> bool:
        return self._setup_result

    async def process(self) -> None:
        self.process_count += 1

    async def cleanup(self) -> None:
        pass


class TestEdgeAgent:
    def test_init_default(self):
        agent = ConcreteAgent()
        assert agent.state == AgentState.INITIALIZED
        assert isinstance(agent.agent_id, str)
        assert agent.agent_type == "base"
        assert agent.is_running is False

    def test_init_custom_config(self):
        config = AgentConfig(agent_id="test", agent_type="custom", node_id="n1")
        agent = ConcreteAgent(config)
        assert agent.agent_id == "test"
        assert agent.agent_type == "custom"

    def test_get_status(self):
        config = AgentConfig(agent_id="s1", node_id="n1")
        agent = ConcreteAgent(config)
        status = agent.get_status()
        assert status['agent_id'] == "s1"
        assert status['state'] == "initialized"
        assert status['node_id'] == "n1"
        assert 'timestamp' in status

    @pytest.mark.asyncio
    async def test_start_success(self):
        agent = ConcreteAgent(AgentConfig(update_interval=0.01))
        result = await agent.start()
        assert result is True
        assert agent.state == AgentState.RUNNING
        assert agent.is_running is True
        await agent.stop()

    @pytest.mark.asyncio
    async def test_start_already_running(self):
        agent = ConcreteAgent(AgentConfig(update_interval=0.01))
        await agent.start()
        result = await agent.start()
        assert result is False
        await agent.stop()

    @pytest.mark.asyncio
    async def test_start_setup_fails(self):
        agent = ConcreteAgent(setup_result=False)
        result = await agent.start()
        assert result is False
        assert agent.state == AgentState.ERROR

    @pytest.mark.asyncio
    async def test_stop(self):
        agent = ConcreteAgent(AgentConfig(update_interval=0.01))
        await agent.start()
        await agent.stop()
        assert agent.state == AgentState.STOPPED

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self):
        agent = ConcreteAgent()
        await agent.stop()  # Should not raise
        assert agent.state == AgentState.INITIALIZED

    @pytest.mark.asyncio
    async def test_pause_resume(self):
        agent = ConcreteAgent(AgentConfig(update_interval=0.01))
        await agent.start()
        await agent.pause()
        assert agent.state == AgentState.PAUSED
        assert agent.is_running is False
        await agent.resume()
        assert agent.state == AgentState.RUNNING
        await agent.stop()

    @pytest.mark.asyncio
    async def test_pause_when_not_running(self):
        agent = ConcreteAgent()
        await agent.pause()
        assert agent.state == AgentState.INITIALIZED

    @pytest.mark.asyncio
    async def test_resume_when_not_paused(self):
        agent = ConcreteAgent()
        await agent.resume()
        assert agent.state == AgentState.INITIALIZED

    @pytest.mark.asyncio
    async def test_send_message(self):
        agent = ConcreteAgent(AgentConfig(agent_id="sender"))
        msg = await agent.send_message("test_type", {"key": "val"}, "target-1")
        assert msg.source_agent == "sender"
        assert msg.target_agent == "target-1"
        assert msg.message_type == "test_type"

    @pytest.mark.asyncio
    async def test_receive_and_handle_message(self):
        agent = ConcreteAgent(AgentConfig(agent_id="receiver"))
        msg = AgentMessage(
            source_agent="other",
            target_agent="receiver",
            message_type="hello",
            payload={"data": 1}
        )
        agent.receive_message(msg)
        assert not agent._message_queue.empty()

    def test_on_callback(self):
        agent = ConcreteAgent()
        callback = MagicMock()
        agent.on('test_event', callback)
        assert 'test_event' in agent._callbacks
        assert callback in agent._callbacks['test_event']

    @pytest.mark.asyncio
    async def test_emit_sync_callback(self):
        agent = ConcreteAgent()
        results = []
        agent.on('test', lambda data: results.append(data))
        await agent._emit('test', 'hello')
        assert results == ['hello']

    @pytest.mark.asyncio
    async def test_emit_async_callback(self):
        agent = ConcreteAgent()
        results = []

        async def async_cb(data):
            results.append(data)

        agent.on('test', async_cb)
        await agent._emit('test', 42)
        assert results == [42]

    @pytest.mark.asyncio
    async def test_emit_callback_error(self):
        agent = ConcreteAgent()
        agent.on('test', lambda data: 1 / 0)  # Will raise
        await agent._emit('test', None)  # Should not propagate

    @pytest.mark.asyncio
    async def test_process_messages(self):
        agent = ConcreteAgent()
        msg = AgentMessage(
            source_agent="other",
            target_agent=None,
            message_type="ping",
            payload={}
        )
        agent.receive_message(msg)
        await agent._process_messages()
        assert agent._message_queue.empty()


# =====================
# DiscoveryAgent
# =====================

class TestDiscoveryConfig:
    def test_defaults(self):
        config = DiscoveryConfig()
        assert config.agent_type == "discovery"
        assert config.listen_port == 5000
        assert config.device_timeout == 120.0
        assert config.health_check_interval == 30.0

    def test_custom(self):
        config = DiscoveryConfig(listen_port=8080, device_timeout=60.0)
        assert config.listen_port == 8080
        assert config.device_timeout == 60.0


class TestDiscoveryAgent:
    @pytest.mark.asyncio
    async def test_setup_without_aiohttp(self):
        config = DiscoveryConfig(enable_http_server=False)
        agent = DiscoveryAgent(config)
        result = await agent.setup()
        assert result is True

    @pytest.mark.asyncio
    async def test_register_device_direct(self):
        config = DiscoveryConfig(enable_http_server=False)
        agent = DiscoveryAgent(config)
        await agent.setup()

        result = await agent.register_device_direct({
            'id': 'dev-01',
            'ip': '10.0.0.1',
            'hardware': 'ESP32'
        })
        assert result['success'] is True
        assert result['device_id'] == 'dev-01'
        assert result['is_new'] is True

    @pytest.mark.asyncio
    async def test_register_device_update(self):
        config = DiscoveryConfig(enable_http_server=False)
        agent = DiscoveryAgent(config)
        await agent.setup()

        await agent.register_device_direct({'id': 'dev-01', 'ip': '10.0.0.1', 'hardware': 'ESP32'})
        result = await agent.register_device_direct({'id': 'dev-01', 'ip': '10.0.0.2', 'hardware': 'ESP32'})
        assert result['is_new'] is False

    @pytest.mark.asyncio
    async def test_register_device_missing_id(self):
        config = DiscoveryConfig(enable_http_server=False)
        agent = DiscoveryAgent(config)
        await agent.setup()

        result = await agent.register_device_direct({'ip': '10.0.0.1'})
        assert result['success'] is False

    def test_get_registered_devices(self):
        agent = DiscoveryAgent(DiscoveryConfig(enable_http_server=False))
        assert agent.get_registered_devices() == {}

    @pytest.mark.asyncio
    async def test_get_active_devices(self):
        agent = DiscoveryAgent(DiscoveryConfig(enable_http_server=False))
        await agent.setup()
        await agent.register_device_direct({'id': 'a1', 'ip': '1.1.1.1', 'hardware': 'x'})
        active = agent.get_active_devices()
        assert len(active) == 1
        assert active[0]['state'] == 'active'

    @pytest.mark.asyncio
    async def test_get_device(self):
        agent = DiscoveryAgent(DiscoveryConfig(enable_http_server=False))
        await agent.setup()
        await agent.register_device_direct({'id': 'x1', 'ip': '1.1.1.1', 'hardware': 'y'})
        device = agent.get_device('x1')
        assert device is not None
        assert device['id'] == 'x1'
        assert agent.get_device('nonexistent') is None

    @pytest.mark.asyncio
    async def test_process_registrations(self):
        agent = DiscoveryAgent(DiscoveryConfig(enable_http_server=False))
        await agent.setup()
        await agent._pending_registrations.put({'id': 'q1', 'ip': '1.1.1.1', 'hardware': 'z'})
        await agent._process_registrations()
        assert 'q1' in agent._registered_devices

    def test_should_health_check(self):
        agent = DiscoveryAgent(DiscoveryConfig(enable_http_server=False, health_check_interval=0.0))
        assert agent._should_health_check() is True

    @pytest.mark.asyncio
    async def test_check_device_health_timeout(self):
        agent = DiscoveryAgent(DiscoveryConfig(enable_http_server=False, device_timeout=0.0))
        await agent.setup()
        await agent.register_device_direct({'id': 'timeout-dev', 'ip': '1.1.1.1', 'hardware': 'x'})
        # Force last-seen to past
        agent._registered_devices['timeout-dev']['last-seen'] = (
            datetime.utcnow() - timedelta(seconds=300)
        ).isoformat()
        await agent._check_device_health()
        assert agent._registered_devices['timeout-dev']['state'] == 'inactive'

    @pytest.mark.asyncio
    async def test_cleanup_no_server(self):
        agent = DiscoveryAgent(DiscoveryConfig(enable_http_server=False))
        await agent.cleanup()  # Should not raise

    def test_get_status(self):
        agent = DiscoveryAgent(DiscoveryConfig(enable_http_server=False))
        status = agent.get_status()
        assert status['total_devices'] == 0
        assert status['active_devices'] == 0
        assert 'listen_port' in status

    @pytest.mark.asyncio
    async def test_register_optional_fields(self):
        agent = DiscoveryAgent(DiscoveryConfig(enable_http_server=False))
        await agent.setup()
        result = await agent.register_device_direct({
            'id': 'opt-dev',
            'ip': '1.1.1.1',
            'hardware': 'ESP32',
            'mac_address': 'AA:BB:CC:DD:EE:FF',
            'firmware_version': '1.0',
            'location': 'Lab',
            'role': 'sensor'
        })
        assert result['success'] is True
        device = agent.get_device('opt-dev')
        assert device['mac_address'] == 'AA:BB:CC:DD:EE:FF'
        assert device['role'] == 'sensor'


# =====================
# ClassificationAgent
# =====================

class TestClassificationConfig:
    def test_defaults(self):
        config = ClassificationConfig()
        assert config.agent_type == "classification"
        assert config.model_type == "rule_based"
        assert config.confidence_threshold == 0.6
        assert config.cache_classifications is True


class TestClassificationAgent:
    @pytest.mark.asyncio
    async def test_setup(self):
        agent = ClassificationAgent(ClassificationConfig(model_type='rule_based'))
        result = await agent.setup()
        assert result is True
        assert agent._classifier is not None

    @pytest.mark.asyncio
    async def test_classify_device(self):
        agent = ClassificationAgent(ClassificationConfig(model_type='rule_based'))
        await agent.setup()

        result = await agent.classify({
            'id': 'esp-01',
            'hardware_type': 'ESP32-WROOM32',
            'memory_total': 520000
        })
        assert result.device_type == 'ESP32Device'
        assert result.confidence > 0

    @pytest.mark.asyncio
    async def test_classify_caching(self):
        agent = ClassificationAgent(ClassificationConfig(
            model_type='rule_based',
            cache_classifications=True
        ))
        await agent.setup()

        data = {'id': 'cache-dev', 'hardware_type': 'ESP32', 'memory_total': 520000}
        r1 = await agent.classify(data)
        r2 = await agent.classify(data)
        assert r1.device_type == r2.device_type

    @pytest.mark.asyncio
    async def test_classify_no_cache(self):
        agent = ClassificationAgent(ClassificationConfig(
            model_type='rule_based',
            cache_classifications=False
        ))
        await agent.setup()
        result = await agent.classify({'id': 'nc-dev', 'hardware_type': 'ESP32', 'memory_total': 520000})
        assert result.device_type == 'ESP32Device'

    @pytest.mark.asyncio
    async def test_classify_low_confidence(self):
        agent = ClassificationAgent(ClassificationConfig(
            model_type='rule_based',
            confidence_threshold=0.99
        ))
        await agent.setup()
        result = await agent.classify({'id': 'unknown-dev', 'hardware_type': 'Unknown', 'memory_total': 100})
        assert result.confidence < 0.99

    def test_queue_classification(self):
        agent = ClassificationAgent(ClassificationConfig(model_type='rule_based'))
        agent.queue_classification({'id': 'q1'})
        assert not agent._pending_classifications.empty()

    def test_get_classification_empty(self):
        agent = ClassificationAgent(ClassificationConfig(model_type='rule_based'))
        assert agent.get_classification('nonexistent') is None

    @pytest.mark.asyncio
    async def test_get_cached_classification(self):
        agent = ClassificationAgent(ClassificationConfig(model_type='rule_based'))
        await agent.setup()
        await agent.classify({'id': 'cached', 'hardware_type': 'ESP32', 'memory_total': 520000})
        cached = agent.get_classification('cached')
        assert cached is not None
        assert cached.device_type == 'ESP32Device'

    def test_clear_cache(self):
        agent = ClassificationAgent(ClassificationConfig(model_type='rule_based'))
        agent._classification_cache['test'] = 'value'
        agent.clear_cache()
        assert len(agent._classification_cache) == 0

    @pytest.mark.asyncio
    async def test_handle_message_classify(self):
        agent = ClassificationAgent(ClassificationConfig(model_type='rule_based'))
        await agent.setup()
        msg = AgentMessage(
            source_agent="discovery",
            target_agent=agent.agent_id,
            message_type="classify_device",
            payload={'device': {'id': 'msg-dev', 'hardware_type': 'ESP32'}}
        )
        await agent.handle_message(msg)
        assert not agent._pending_classifications.empty()

    @pytest.mark.asyncio
    async def test_handle_message_discovered(self):
        agent = ClassificationAgent(ClassificationConfig(model_type='rule_based'))
        msg = AgentMessage(
            source_agent="discovery",
            target_agent=None,
            message_type="device_discovered",
            payload={'id': 'disc-dev'}
        )
        await agent.handle_message(msg)
        assert not agent._pending_classifications.empty()

    @pytest.mark.asyncio
    async def test_process_pending(self):
        agent = ClassificationAgent(ClassificationConfig(model_type='rule_based'))
        await agent.setup()
        agent.queue_classification({'id': 'proc-dev', 'hardware_type': 'ESP32', 'memory_total': 520000})
        await agent.process()
        assert agent._classification_count == 1

    @pytest.mark.asyncio
    async def test_cleanup_no_model_path(self):
        agent = ClassificationAgent(ClassificationConfig(model_type='rule_based'))
        await agent.setup()
        await agent.cleanup()  # Should not raise

    def test_get_status(self):
        agent = ClassificationAgent(ClassificationConfig(model_type='rule_based'))
        status = agent.get_status()
        assert status['model_type'] == 'rule_based'
        assert status['cached_classifications'] == 0
        assert status['total_classifications'] == 0


# =====================
# SyncAgent / DAGEvent
# =====================

class TestDAGEvent:
    def test_create_event(self):
        event = DAGEvent(
            event_id="ev-001",
            event_type="test",
            payload={"key": "val"},
            timestamp="2024-01-01T00:00:00",
            node_id="node-01",
            parent_ids=[]
        )
        assert event.event_id == "ev-001"
        assert event.event_type == "test"
        assert event.hash != ""

    def test_hash_computation(self):
        event = DAGEvent(
            event_id="ev-001",
            event_type="test",
            payload={"key": "val"},
            timestamp="2024-01-01T00:00:00",
            node_id="node-01",
            parent_ids=[]
        )
        expected = event._compute_hash()
        assert event.hash == expected

    def test_to_dict(self):
        event = DAGEvent(
            event_id="ev-001",
            event_type="test",
            payload={"key": "val"},
            timestamp="2024-01-01T00:00:00",
            node_id="node-01",
            parent_ids=["parent-01"]
        )
        d = event.to_dict()
        assert d['event_id'] == "ev-001"
        assert d['parent_ids'] == ["parent-01"]
        assert 'hash' in d

    def test_from_dict(self):
        original = DAGEvent(
            event_id="ev-002",
            event_type="register",
            payload={"device": "x"},
            timestamp="2024-01-01T00:00:00",
            node_id="node-01",
            parent_ids=[]
        )
        restored = DAGEvent.from_dict(original.to_dict())
        assert restored.event_id == original.event_id
        assert restored.hash == original.hash


class TestSyncConfig:
    def test_defaults(self):
        config = SyncConfig()
        assert config.agent_type == "sync"
        assert config.sync_interval == 30.0
        assert config.max_local_events == 1000


class TestSyncAgent:
    @pytest.mark.asyncio
    async def test_setup_creates_genesis(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        result = await agent.setup()
        assert result is True
        assert len(agent._events) == 1
        genesis_id = f"test-node-genesis"
        assert genesis_id in agent._events

    @pytest.mark.asyncio
    async def test_create_event(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()
        event = agent.create_event("test_type", {"data": 123})
        assert event.event_type == "test_type"
        assert event.node_id == "test-node"
        assert len(agent._events) == 2

    @pytest.mark.asyncio
    async def test_record_device_event(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()
        event = await agent.record_device_event("dev-01", "register", {"ip": "1.1.1.1"})
        assert event.event_type == "device_register"
        assert event.payload['device_id'] == "dev-01"

    @pytest.mark.asyncio
    async def test_get_tips(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()
        tips = agent.get_tips()
        assert len(tips) >= 1

    @pytest.mark.asyncio
    async def test_get_event(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()
        event = agent.get_event("test-node-genesis")
        assert event is not None
        assert event.event_type == "genesis"
        assert agent.get_event("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_recent_events(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()
        agent.create_event("a", {})
        agent.create_event("b", {})
        recent = agent.get_recent_events(limit=2)
        assert len(recent) == 2

    @pytest.mark.asyncio
    async def test_get_events_by_type(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()
        agent.create_event("type_a", {})
        agent.create_event("type_b", {})
        agent.create_event("type_a", {})
        result = agent.get_events_by_type("type_a")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_device_history(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()
        await agent.record_device_event("dev-01", "register", {})
        await agent.record_device_event("dev-01", "state_change", {"state": "active"})
        await agent.record_device_event("dev-02", "register", {})
        history = agent.get_device_history("dev-01")
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_export_dag(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()
        agent.create_event("test", {"x": 1})
        export = agent.export_dag()
        assert export['node_id'] == "test-node"
        assert export['event_count'] == 2
        assert len(export['events']) == 2

    @pytest.mark.asyncio
    async def test_validate_event_valid(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        event = DAGEvent(
            event_id="valid",
            event_type="test",
            payload={},
            timestamp="2024-01-01T00:00:00",
            node_id="test-node",
            parent_ids=[]
        )
        assert agent._validate_event(event) is True

    @pytest.mark.asyncio
    async def test_validate_event_invalid_hash(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        event = DAGEvent(
            event_id="invalid",
            event_type="test",
            payload={},
            timestamp="2024-01-01T00:00:00",
            node_id="test-node",
            parent_ids=[]
        )
        event.hash = "wrong_hash"
        assert agent._validate_event(event) is False

    @pytest.mark.asyncio
    async def test_add_duplicate_event(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()
        event = DAGEvent(
            event_id="test-node-genesis",
            event_type="genesis",
            payload={},
            timestamp="2024-01-01T00:00:00",
            node_id="test-node",
            parent_ids=[]
        )
        assert agent._add_event(event) is False  # Already exists

    @pytest.mark.asyncio
    async def test_handle_message_device_discovered(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()
        msg = AgentMessage(
            source_agent="discovery",
            target_agent=None,
            message_type="device_discovered",
            payload={'id': 'disc-01', 'hardware': 'ESP32', 'ip': '1.1.1.1'}
        )
        await agent.handle_message(msg)
        events = agent.get_events_by_type("device_discovered")
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_handle_message_device_classified(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()
        msg = AgentMessage(
            source_agent="classifier",
            target_agent=None,
            message_type="device_classified",
            payload={'device_id': 'class-01', 'result': {'type': 'ESP32'}}
        )
        await agent.handle_message(msg)
        events = agent.get_events_by_type("device_classified")
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_handle_message_sync_request(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()
        msg = AgentMessage(
            source_agent="peer",
            target_agent=None,
            message_type="sync_request",
            payload={'type': 'get_tips'}
        )
        await agent.handle_message(msg)
        assert not agent._pending_sync.empty()

    @pytest.mark.asyncio
    async def test_handle_sync_new_events(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()

        new_event = DAGEvent(
            event_id="external-001",
            event_type="external",
            payload={"data": "from_peer"},
            timestamp="2024-01-01T00:00:00",
            node_id="other-node",
            parent_ids=[]
        )
        request = {
            'type': 'new_events',
            'events': [new_event.to_dict()]
        }
        await agent._handle_sync_request(request)
        assert "external-001" in agent._events

    @pytest.mark.asyncio
    async def test_cleanup(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        await agent.setup()
        await agent.cleanup()  # Should not raise

    def test_get_status(self):
        agent = SyncAgent(SyncConfig(node_id="test-node"))
        status = agent.get_status()
        assert status['total_events'] == 0
        assert status['current_tips'] == 0
        assert status['sync_count'] == 0


# =====================
# EdgeAgentManager
# =====================

class TestEdgeAgentManager:
    def test_init(self):
        manager = EdgeAgentManager(node_id="mgr-node")
        assert manager.node_id == "mgr-node"
        assert len(manager.agents) == 0
        assert manager._running is False

    def test_add_agent(self):
        manager = EdgeAgentManager(node_id="mgr-node")
        config = AgentConfig(agent_id="a1")
        agent = ConcreteAgent(config)
        manager.add_agent(agent)
        assert "a1" in manager.agents
        assert agent.config.node_id == "mgr-node"

    def test_add_duplicate_agent(self):
        manager = EdgeAgentManager()
        config = AgentConfig(agent_id="dup")
        manager.add_agent(ConcreteAgent(config))
        with pytest.raises(ValueError):
            manager.add_agent(ConcreteAgent(AgentConfig(agent_id="dup")))

    def test_remove_agent(self):
        manager = EdgeAgentManager()
        config = AgentConfig(agent_id="rm")
        manager.add_agent(ConcreteAgent(config))
        assert manager.remove_agent("rm") is True
        assert manager.remove_agent("rm") is False

    def test_get_agent(self):
        manager = EdgeAgentManager()
        config = AgentConfig(agent_id="get")
        agent = ConcreteAgent(config)
        manager.add_agent(agent)
        assert manager.get_agent("get") is agent
        assert manager.get_agent("nonexistent") is None

    def test_get_agents_by_type(self):
        manager = EdgeAgentManager()
        manager.add_agent(ConcreteAgent(AgentConfig(agent_id="b1", agent_type="base")))
        manager.add_agent(ConcreteAgent(AgentConfig(agent_id="b2", agent_type="base")))
        manager.add_agent(ConcreteAgent(AgentConfig(agent_id="x1", agent_type="other")))
        result = manager.get_agents_by_type("base")
        assert len(result) == 2

    def test_get_status(self):
        manager = EdgeAgentManager(node_id="status-node")
        manager.add_agent(ConcreteAgent(AgentConfig(agent_id="s1")))
        status = manager.get_status()
        assert status['node_id'] == "status-node"
        assert status['agent_count'] == 1
        assert 's1' in status['agents']

    @pytest.mark.asyncio
    async def test_start_stop_all(self):
        manager = EdgeAgentManager(node_id="test-node")
        manager.add_agent(ConcreteAgent(AgentConfig(agent_id="a1", update_interval=0.01)))
        manager.add_agent(ConcreteAgent(AgentConfig(agent_id="a2", update_interval=0.01)))

        results = await manager.start_all()
        assert results['a1'] is True
        assert results['a2'] is True
        assert manager._running is True

        await manager.stop_all()
        assert manager._running is False

    @pytest.mark.asyncio
    async def test_start_with_failure(self):
        manager = EdgeAgentManager(node_id="test-node")
        manager.add_agent(ConcreteAgent(AgentConfig(agent_id="ok", update_interval=0.01), setup_result=True))
        manager.add_agent(ConcreteAgent(AgentConfig(agent_id="fail", update_interval=0.01), setup_result=False))

        results = await manager.start_all()
        assert results['ok'] is True
        assert results['fail'] is False

        await manager.stop_all()

    @pytest.mark.asyncio
    async def test_deliver_targeted_message(self):
        manager = EdgeAgentManager(node_id="test-node")
        a1 = ConcreteAgent(AgentConfig(agent_id="a1"))
        a2 = ConcreteAgent(AgentConfig(agent_id="a2"))
        manager.add_agent(a1)
        manager.add_agent(a2)

        msg = AgentMessage(
            source_agent="a1",
            target_agent="a2",
            message_type="direct",
            payload={"test": True}
        )
        await manager._deliver_message(msg)
        assert not a2._message_queue.empty()
        assert a1._message_queue.empty()

    @pytest.mark.asyncio
    async def test_deliver_broadcast_message(self):
        manager = EdgeAgentManager(node_id="test-node")
        a1 = ConcreteAgent(AgentConfig(agent_id="a1"))
        a2 = ConcreteAgent(AgentConfig(agent_id="a2"))
        a3 = ConcreteAgent(AgentConfig(agent_id="a3"))
        manager.add_agent(a1)
        manager.add_agent(a2)
        manager.add_agent(a3)

        msg = AgentMessage(
            source_agent="a1",
            target_agent=None,
            message_type="broadcast",
            payload={}
        )
        await manager._deliver_message(msg)
        assert a1._message_queue.empty()  # Sender doesn't get own broadcast
        assert not a2._message_queue.empty()
        assert not a3._message_queue.empty()

    def test_create_default_setup(self):
        manager = EdgeAgentManager.create_default_setup(node_id="default-node", discovery_port=9000)
        assert manager.node_id == "default-node"
        assert len(manager.agents) == 3
        types = [a.agent_type for a in manager.agents.values()]
        assert 'discovery' in types
        assert 'classification' in types
        assert 'sync' in types
