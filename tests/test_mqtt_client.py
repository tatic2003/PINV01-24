"""
MQTT Client Tests
Project: PINV01-24 - FIUNA

Tests for mqtt/client.py (ITSMQTTClient) with mocked paho.mqtt.
"""

import pytest
import json
import sys
import os
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mqtt.broker import MQTTBrokerConfig, TopicRegistry


# =====================
# ITSMQTTClient
# =====================

@pytest.fixture
def mqtt_cfg():
    return MQTTBrokerConfig(host="localhost", port=1883, topic_prefix="its-test")


class TestITSMQTTClientInit:
    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_init(self, mqtt_cfg):
        from mqtt.client import ITSMQTTClient
        client = ITSMQTTClient(mqtt_cfg, node_id="test-node")
        assert client.node_id == "test-node"
        assert client._connected is False

    @patch('mqtt.client.MQTT_AVAILABLE', False)
    def test_init_no_paho(self, mqtt_cfg):
        from mqtt.client import ITSMQTTClient
        with pytest.raises(ImportError):
            ITSMQTTClient(mqtt_cfg)


@pytest.fixture
def mock_mqtt_module():
    """Patch paho.mqtt.client for ITSMQTTClient tests."""
    with patch('mqtt.client.mqtt') as mock_mqtt:
        mock_client_instance = MagicMock()
        mock_mqtt.Client.return_value = mock_client_instance
        mock_mqtt.MQTT_ERR_SUCCESS = 0
        yield mock_mqtt, mock_client_instance


class TestITSMQTTClientConnect:
    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_connect_success(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        _, mock_client_instance = mock_mqtt_module

        client = ITSMQTTClient(mqtt_cfg, node_id="test-node")
        result = client.connect()
        assert result is True
        mock_client_instance.connect.assert_called_once_with("localhost", 1883, 60)
        mock_client_instance.loop_start.assert_called_once()

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_connect_with_auth(self, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        _, mock_client_instance = mock_mqtt_module

        config = MQTTBrokerConfig(host="localhost", port=1883, username="user", password="pass")
        client = ITSMQTTClient(config)
        client.connect()
        mock_client_instance.username_pw_set.assert_called_once_with("user", "pass")

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_connect_with_tls(self, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        _, mock_client_instance = mock_mqtt_module

        config = MQTTBrokerConfig(host="localhost", port=8883, use_tls=True, ca_cert="/path/ca.pem")
        client = ITSMQTTClient(config)
        client.connect()
        mock_client_instance.tls_set.assert_called_once_with(ca_certs="/path/ca.pem")

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_connect_failure(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        _, mock_client_instance = mock_mqtt_module
        mock_client_instance.connect.side_effect = Exception("Connection refused")

        client = ITSMQTTClient(mqtt_cfg)
        result = client.connect()
        assert result is False


class TestITSMQTTClientDisconnect:
    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_disconnect(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        _, mock_client_instance = mock_mqtt_module

        client = ITSMQTTClient(mqtt_cfg)
        client.connect()
        client.disconnect()
        mock_client_instance.loop_stop.assert_called_once()
        mock_client_instance.disconnect.assert_called_once()
        assert client._connected is False

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_disconnect_no_client(self, mqtt_cfg):
        from mqtt.client import ITSMQTTClient
        client = ITSMQTTClient(mqtt_cfg)
        client._client = None
        client.disconnect()  # Should not raise


class TestITSMQTTClientSubscribe:
    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_subscribe_when_connected(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        _, mock_client_instance = mock_mqtt_module

        client = ITSMQTTClient(mqtt_cfg)
        client._connected = True
        client._client = mock_client_instance

        callback = MagicMock()
        client.subscribe("devices/register", callback)

        expected_topic = mqtt_cfg.get_topic("devices/register")
        assert expected_topic in client._subscriptions
        mock_client_instance.subscribe.assert_called_once()

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_subscribe_when_disconnected(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        _, mock_client_instance = mock_mqtt_module

        client = ITSMQTTClient(mqtt_cfg)
        callback = MagicMock()
        client.subscribe("devices/register", callback)

        expected_topic = mqtt_cfg.get_topic("devices/register")
        assert expected_topic in client._subscriptions
        mock_client_instance.subscribe.assert_not_called()

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_unsubscribe(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        _, mock_client_instance = mock_mqtt_module

        client = ITSMQTTClient(mqtt_cfg)
        client._connected = True
        client._client = mock_client_instance

        client.subscribe("devices/register", MagicMock())
        client.unsubscribe("devices/register")

        expected_topic = mqtt_cfg.get_topic("devices/register")
        assert expected_topic not in client._subscriptions


class TestITSMQTTClientPublish:
    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_publish_when_connected(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        mock_mqtt, mock_client_instance = mock_mqtt_module

        mock_result = MagicMock()
        mock_result.rc = 0  # MQTT_ERR_SUCCESS
        mock_client_instance.publish.return_value = mock_result

        client = ITSMQTTClient(mqtt_cfg)
        client._connected = True
        client._client = mock_client_instance

        result = client.publish("test/topic", {"key": "val"})
        assert result is True

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_publish_when_disconnected(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient

        client = ITSMQTTClient(mqtt_cfg)
        result = client.publish("test/topic", {"key": "val"})
        assert result is False

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_publish_failure(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        _, mock_client_instance = mock_mqtt_module

        mock_result = MagicMock()
        mock_result.rc = 1  # Error
        mock_client_instance.publish.return_value = mock_result

        client = ITSMQTTClient(mqtt_cfg)
        client._connected = True
        client._client = mock_client_instance

        result = client.publish("test/topic", {"key": "val"})
        assert result is False

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_publish_exception(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        _, mock_client_instance = mock_mqtt_module
        mock_client_instance.publish.side_effect = Exception("publish error")

        client = ITSMQTTClient(mqtt_cfg)
        client._connected = True
        client._client = mock_client_instance

        result = client.publish("test/topic", {"key": "val"})
        assert result is False


class TestITSMQTTClientHighLevel:
    """Test high-level publish methods."""

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def _make_connected_client(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        _, mock_client_instance = mock_mqtt_module

        mock_result = MagicMock()
        mock_result.rc = 0
        mock_client_instance.publish.return_value = mock_result

        client = ITSMQTTClient(mqtt_cfg, node_id="test-node")
        client._connected = True
        client._client = mock_client_instance
        return client

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_publish_device_registration(self, mqtt_cfg, mock_mqtt_module):
        client = self._make_connected_client(mqtt_cfg, mock_mqtt_module)
        result = client.publish_device_registration({"id": "dev1", "hardware": "ESP32"})
        assert result is True

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_publish_device_heartbeat(self, mqtt_cfg, mock_mqtt_module):
        client = self._make_connected_client(mqtt_cfg, mock_mqtt_module)
        result = client.publish_device_heartbeat("dev1")
        assert result is True

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_publish_device_state(self, mqtt_cfg, mock_mqtt_module):
        client = self._make_connected_client(mqtt_cfg, mock_mqtt_module)
        result = client.publish_device_state("dev1", "active", {"reason": "boot"})
        assert result is True

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_publish_device_data(self, mqtt_cfg, mock_mqtt_module):
        client = self._make_connected_client(mqtt_cfg, mock_mqtt_module)
        result = client.publish_device_data("dev1", {"temperature": 25.5})
        assert result is True

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_send_command(self, mqtt_cfg, mock_mqtt_module):
        client = self._make_connected_client(mqtt_cfg, mock_mqtt_module)
        result = client.send_command("dev1", "restart", {"delay": 5})
        assert result is True

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_publish_classification_result(self, mqtt_cfg, mock_mqtt_module):
        client = self._make_connected_client(mqtt_cfg, mock_mqtt_module)
        result = client.publish_classification_result("dev1", {"type": "ESP32"})
        assert result is True

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_broadcast_sync(self, mqtt_cfg, mock_mqtt_module):
        client = self._make_connected_client(mqtt_cfg, mock_mqtt_module)
        result = client.broadcast_sync([{"event_id": "e1"}])
        assert result is True


class TestITSMQTTClientCallbacks:
    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_on_connect_success(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        _, mock_client_instance = mock_mqtt_module

        client = ITSMQTTClient(mqtt_cfg)
        client._client = mock_client_instance
        client._subscriptions = {"its-test/topic": [MagicMock()]}

        client._on_connect(mock_client_instance, None, None, 0)
        assert client._connected is True
        mock_client_instance.subscribe.assert_called()

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_on_connect_failure(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient

        client = ITSMQTTClient(mqtt_cfg)
        client._on_connect(None, None, None, 1)
        assert client._connected is False

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_on_disconnect_expected(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient

        client = ITSMQTTClient(mqtt_cfg)
        client._connected = True
        client._on_disconnect(None, None, 0)
        assert client._connected is False

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_on_disconnect_unexpected(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient

        client = ITSMQTTClient(mqtt_cfg)
        client._connected = True
        client._on_disconnect(None, None, 1)
        assert client._connected is False

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_on_message_valid(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient

        client = ITSMQTTClient(mqtt_cfg)
        callback = MagicMock()
        client._subscriptions = {"its-test/devices/register": [callback]}

        msg = MagicMock()
        msg.topic = "its-test/devices/register"
        msg.payload = json.dumps({"id": "dev1"}).encode('utf-8')

        client._on_message(None, None, msg)
        callback.assert_called_once_with("its-test/devices/register", {"id": "dev1"})

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_on_message_invalid_json(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient

        client = ITSMQTTClient(mqtt_cfg)
        msg = MagicMock()
        msg.topic = "test/topic"
        msg.payload = b"not json"

        client._on_message(None, None, msg)  # Should not raise

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_on_message_callback_error(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient

        client = ITSMQTTClient(mqtt_cfg)
        callback = MagicMock(side_effect=Exception("callback error"))
        client._subscriptions = {"its-test/topic": [callback]}

        msg = MagicMock()
        msg.topic = "its-test/topic"
        msg.payload = json.dumps({"key": "val"}).encode('utf-8')

        client._on_message(None, None, msg)  # Should not raise


class TestTopicMatching:
    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_exact_match(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        client = ITSMQTTClient(mqtt_cfg)
        assert client._topic_matches("a/b/c", "a/b/c") is True
        assert client._topic_matches("a/b/c", "a/b/d") is False

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_single_level_wildcard(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        client = ITSMQTTClient(mqtt_cfg)
        assert client._topic_matches("a/+/c", "a/b/c") is True
        assert client._topic_matches("a/+/c", "a/x/c") is True
        assert client._topic_matches("a/+/c", "a/b/d") is False

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_multi_level_wildcard(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        client = ITSMQTTClient(mqtt_cfg)
        assert client._topic_matches("a/#", "a/b/c") is True
        assert client._topic_matches("a/#", "a") is True

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_length_mismatch(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        client = ITSMQTTClient(mqtt_cfg)
        assert client._topic_matches("a/b", "a/b/c") is False
        assert client._topic_matches("a/b/c", "a/b") is False

    @patch('mqtt.client.MQTT_AVAILABLE', True)
    def test_is_connected(self, mqtt_cfg, mock_mqtt_module):
        from mqtt.client import ITSMQTTClient
        client = ITSMQTTClient(mqtt_cfg)
        assert client.is_connected() is False
        client._connected = True
        assert client.is_connected() is True


# =====================
# Extended MQTT handlers coverage
# =====================

class TestDeviceEventHandlerExtended:
    def test_handle_device_data_no_device_id(self):
        from mqtt.handlers import DeviceEventHandler
        handler = DeviceEventHandler()
        result = handler.handle("its/devices/data", {"data": {"temp": 25}})
        assert result is None

    def test_handle_device_data_with_id(self):
        from mqtt.handlers import DeviceEventHandler
        handler = DeviceEventHandler()
        result = handler.handle("its/devices/data", {"device_id": "d1", "data": {"temp": 25}})
        assert result is None  # Data handler returns None

    def test_handle_device_data_with_manager(self):
        from mqtt.handlers import DeviceEventHandler
        mock_manager = MagicMock()
        handler = DeviceEventHandler(device_manager=mock_manager)
        handler.handle("its/devices/data", {"device_id": "d1", "data": {"temp": 25}})
        mock_manager.store_device_data.assert_called_once()

    def test_handle_device_data_manager_no_method(self):
        from mqtt.handlers import DeviceEventHandler
        mock_manager = MagicMock()
        mock_manager.store_device_data.side_effect = AttributeError
        handler = DeviceEventHandler(device_manager=mock_manager)
        result = handler.handle("its/devices/data", {"device_id": "d1", "data": {"temp": 25}})
        assert result is None

    def test_handle_classification_request_no_classifier(self):
        from mqtt.handlers import DeviceEventHandler
        handler = DeviceEventHandler()
        result = handler.handle("its/classify/request", {"hardware_type": "ESP32"})
        assert result == {'error': 'Classifier not available'}

    def test_handle_classification_request_with_classifier(self):
        from mqtt.handlers import DeviceEventHandler
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = {
            'device_type': 'ESP32Device',
            'confidence': 0.95,
            'suggested_role': 'sensor'
        }
        handler = DeviceEventHandler(classifier=mock_classifier)
        result = handler.handle("its/classify/request", {"device_data": {"hardware_type": "ESP32"}})
        assert result is not None
        assert 'device_type' in result

    def test_handle_classification_request_error(self):
        from mqtt.handlers import DeviceEventHandler
        mock_classifier = MagicMock()
        mock_classifier.classify.side_effect = Exception("classify error")
        handler = DeviceEventHandler(classifier=mock_classifier)
        result = handler.handle("its/classify/request", {"hardware_type": "ESP32"})
        assert 'error' in result

    def test_handle_registration_with_dag(self):
        from mqtt.handlers import DeviceEventHandler
        mock_dag = MagicMock()
        handler = DeviceEventHandler(dag_manager=mock_dag)
        result = handler.handle("its/devices/register", {"id": "d1"})
        assert result['status'] == 'registered'
        mock_dag.add_event.assert_called_once()

    def test_handle_state_change_with_dag(self):
        from mqtt.handlers import DeviceEventHandler
        mock_dag = MagicMock()
        handler = DeviceEventHandler(dag_manager=mock_dag)
        result = handler.handle("its/devices/state", {"device_id": "d1", "state": "active"})
        assert result['ack'] is True
        mock_dag.add_event.assert_called_once()

    def test_handle_state_change_missing_fields(self):
        from mqtt.handlers import DeviceEventHandler
        handler = DeviceEventHandler()
        result = handler.handle("its/devices/state", {"device_id": "d1"})
        assert result == {'error': 'Missing device_id or state'}

    def test_handle_state_change_manager_error(self):
        from mqtt.handlers import DeviceEventHandler
        mock_manager = MagicMock()
        mock_manager.update_device_state.side_effect = Exception("state error")
        handler = DeviceEventHandler(device_manager=mock_manager)
        result = handler.handle("its/devices/state", {"device_id": "d1", "state": "active"})
        assert 'error' in result

    def test_handle_heartbeat_missing_device_id(self):
        from mqtt.handlers import DeviceEventHandler
        handler = DeviceEventHandler()
        result = handler.handle("its/devices/heartbeat", {})
        assert result is None

    def test_handle_heartbeat_manager_error(self):
        from mqtt.handlers import DeviceEventHandler
        mock_manager = MagicMock()
        mock_manager.update_heartbeat.side_effect = Exception("heartbeat error")
        handler = DeviceEventHandler(device_manager=mock_manager)
        result = handler.handle("its/devices/heartbeat", {"device_id": "d1"})
        assert result['ack'] is True

    def test_handle_registration_with_manager_error(self):
        from mqtt.handlers import DeviceEventHandler
        mock_manager = MagicMock()
        mock_manager.register_device.side_effect = Exception("register error")
        handler = DeviceEventHandler(device_manager=mock_manager)
        result = handler.handle("its/devices/register", {"id": "d1"})
        assert result['status'] == 'failed'

    def test_handle_registration_with_classifier(self):
        from mqtt.handlers import DeviceEventHandler
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = {"type": "ESP32"}
        handler = DeviceEventHandler(classifier=mock_classifier)
        result = handler.handle("its/devices/register", {"id": "d1"})
        assert 'classification' in result

    def test_handle_unknown_topic(self):
        from mqtt.handlers import DeviceEventHandler
        handler = DeviceEventHandler()
        result = handler.handle("its/unknown/topic", {"data": "test"})
        assert result is None

    def test_handle_exception(self):
        from mqtt.handlers import DeviceEventHandler
        handler = DeviceEventHandler()
        # Force an exception by passing None payload attributes
        result = handler.handle("its/devices/register", None)
        assert 'error' in result

    def test_trigger_callback_error(self):
        from mqtt.handlers import DeviceEventHandler
        handler = DeviceEventHandler()
        handler.on_event('registration', MagicMock(side_effect=Exception("cb error")))
        result = handler.handle("its/devices/register", {"id": "d1"})
        assert result['status'] == 'registered'

    def test_log_dag_event_error(self):
        from mqtt.handlers import DeviceEventHandler
        mock_dag = MagicMock()
        mock_dag.add_event.side_effect = Exception("dag error")
        handler = DeviceEventHandler(dag_manager=mock_dag)
        result = handler.handle("its/devices/register", {"id": "d1"})
        assert result['status'] == 'registered'


class TestSyncEventHandlerExtended:
    def test_handle_sync_request(self):
        from mqtt.handlers import SyncEventHandler
        mock_dag = MagicMock()
        mock_dag.get_events_since.return_value = [{"event_id": "e1"}]
        handler = SyncEventHandler(dag_manager=mock_dag, node_id="n1")
        result = handler.handle("its/sync/request", {"node_id": "n2", "since_hash": "abc"})
        assert result is not None
        assert result['node_id'] == "n1"

    def test_handle_sync_request_no_dag(self):
        from mqtt.handlers import SyncEventHandler
        handler = SyncEventHandler(node_id="n1")
        result = handler.handle("its/sync/request", {"node_id": "n2"})
        assert result == {'events': []}

    def test_handle_sync_request_error(self):
        from mqtt.handlers import SyncEventHandler
        mock_dag = MagicMock()
        mock_dag.get_events_since.side_effect = Exception("dag error")
        handler = SyncEventHandler(dag_manager=mock_dag, node_id="n1")
        result = handler.handle("its/sync/request", {"node_id": "n2"})
        assert 'error' in result

    def test_handle_sync_response(self):
        from mqtt.handlers import SyncEventHandler
        mock_dag = MagicMock()
        handler = SyncEventHandler(dag_manager=mock_dag, node_id="n1")
        result = handler.handle("its/sync/response", {
            "node_id": "n2",
            "events": [{"event_id": "e1"}]
        })
        assert result is None
        mock_dag.merge_event.assert_called_once()

    def test_handle_sync_response_no_events(self):
        from mqtt.handlers import SyncEventHandler
        handler = SyncEventHandler(node_id="n1")
        result = handler.handle("its/sync/response", {"node_id": "n2", "events": []})
        assert result is None

    def test_handle_sync_response_merge_error(self):
        from mqtt.handlers import SyncEventHandler
        mock_dag = MagicMock()
        mock_dag.merge_event.side_effect = Exception("merge error")
        handler = SyncEventHandler(dag_manager=mock_dag, node_id="n1")
        result = handler.handle("its/sync/response", {
            "node_id": "n2",
            "events": [{"event_id": "e1"}]
        })
        assert result is None

    def test_handle_unknown_sync_topic(self):
        from mqtt.handlers import SyncEventHandler
        handler = SyncEventHandler(node_id="n1")
        result = handler.handle("its/sync/unknown", {})
        assert result is None

    def test_handle_own_broadcast(self):
        from mqtt.handlers import SyncEventHandler
        handler = SyncEventHandler(node_id="n1")
        result = handler.handle("its/sync/broadcast", {"node_id": "n1", "events": []})
        assert result is None

    def test_handle_broadcast_with_events(self):
        from mqtt.handlers import SyncEventHandler
        mock_dag = MagicMock()
        handler = SyncEventHandler(dag_manager=mock_dag, node_id="n1")
        result = handler.handle("its/sync/broadcast", {
            "node_id": "n2",
            "events": [{"event_id": "e1"}, {"event_id": "e2"}]
        })
        assert result is None
        assert mock_dag.merge_event.call_count == 2

    def test_handle_broadcast_merge_error(self):
        from mqtt.handlers import SyncEventHandler
        mock_dag = MagicMock()
        mock_dag.merge_event.side_effect = Exception("merge error")
        handler = SyncEventHandler(dag_manager=mock_dag, node_id="n1")
        result = handler.handle("its/sync/broadcast", {
            "node_id": "n2",
            "events": [{"event_id": "e1"}]
        })
        assert result is None


class TestCommandHandlerExtended:
    def test_register_and_execute_command(self):
        from mqtt.handlers import CommandHandler
        handler = CommandHandler()
        handler.register_command("restart", lambda params: "restarting")
        result = handler.handle("its/commands", {"command": "restart", "params": {}})
        assert result['status'] == 'completed'
        assert result['result'] == 'restarting'

    def test_command_handler_exception(self):
        from mqtt.handlers import CommandHandler
        handler = CommandHandler()
        handler.register_command("fail", lambda params: 1 / 0)
        result = handler.handle("its/commands", {"command": "fail"})
        assert result['status'] == 'error'

    def test_no_command_specified(self):
        from mqtt.handlers import CommandHandler
        handler = CommandHandler()
        result = handler.handle("its/commands", {"params": {}})
        assert result == {'error': 'No command specified'}
