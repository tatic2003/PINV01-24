"""
MQTT Module Tests
Project: PINV01-24 - FIUNA

Unit tests for MQTT broker configuration and message handling.
"""

import pytest
import json
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mqtt.broker import MQTTBrokerConfig, TopicRegistry, MQTTMessage


class TestMQTTBrokerConfig:
    """Test MQTTBrokerConfig class."""

    def test_default_config(self):
        """Test default configuration values."""
        config = MQTTBrokerConfig()

        assert config.host == "localhost"
        assert config.port == 1883
        assert config.username is None
        assert config.password is None
        assert config.keepalive == 60
        assert config.use_tls is False
        assert config.default_qos == 1
        assert config.topic_prefix == "its"

    def test_custom_config(self):
        """Test custom configuration."""
        config = MQTTBrokerConfig(
            host="192.168.1.1",
            port=8883,
            username="user",
            password="pass",
            use_tls=True
        )

        assert config.host == "192.168.1.1"
        assert config.port == 8883
        assert config.username == "user"
        assert config.password == "pass"
        assert config.use_tls is True

    def test_get_topic(self):
        """Test topic building with prefix."""
        config = MQTTBrokerConfig(topic_prefix="its")

        topic = config.get_topic("devices", "register")
        assert topic == "its/devices/register"

        topic = config.get_topic("system", "status")
        assert topic == "its/system/status"

    def test_get_topic_custom_prefix(self):
        """Test topic building with custom prefix."""
        config = MQTTBrokerConfig(topic_prefix="iot/test")

        topic = config.get_topic("devices", "register")
        assert topic == "iot/test/devices/register"


class TestTopicRegistry:
    """Test TopicRegistry class."""

    def test_topic_constants(self):
        """Test topic constant values."""
        assert TopicRegistry.DEVICE_REGISTER == "devices/register"
        assert "{device_id}" in TopicRegistry.DEVICE_HEARTBEAT
        assert TopicRegistry.SYSTEM_STATUS == "system/status"

    def test_format_topic(self):
        """Test topic formatting with placeholders."""
        topic = TopicRegistry.format_topic(
            TopicRegistry.DEVICE_HEARTBEAT,
            device_id="esp32-01"
        )
        assert topic == "devices/esp32-01/heartbeat"

        topic = TopicRegistry.format_topic(
            TopicRegistry.DEVICE_COMMAND,
            device_id="rpi-node-01"
        )
        assert topic == "devices/rpi-node-01/command"

    def test_get_device_topics(self):
        """Test getting all topics for a device."""
        topics = TopicRegistry.get_device_topics("test-device")

        assert "heartbeat" in topics
        assert "state" in topics
        assert "data" in topics
        assert "command" in topics
        assert "response" in topics

        assert "test-device" in topics["heartbeat"]
        assert "test-device" in topics["command"]

    def test_get_device_topics_with_prefix(self):
        """Test device topics with custom prefix."""
        topics = TopicRegistry.get_device_topics("esp32-01", prefix="iot/edge")

        assert topics["heartbeat"].startswith("iot/edge/")
        assert "esp32-01" in topics["heartbeat"]


class TestMQTTMessage:
    """Test MQTTMessage class."""

    def test_create_message(self):
        """Test creating a message."""
        msg = MQTTMessage(
            topic="its/test",
            payload=b'{"key": "value"}',
            qos=1
        )

        assert msg.topic == "its/test"
        assert msg.payload == b'{"key": "value"}'
        assert msg.qos == 1
        assert msg.retain is False

    def test_payload_str(self):
        """Test payload string conversion."""
        msg = MQTTMessage(
            topic="test",
            payload=b'{"status": "ok"}'
        )

        assert msg.payload_str == '{"status": "ok"}'

    def test_from_dict(self):
        """Test creating message from dictionary."""
        data = {"device_id": "esp32-01", "state": "active"}

        msg = MQTTMessage.from_dict("its/test", data)

        assert msg.topic == "its/test"
        assert msg.qos == 1

        payload = json.loads(msg.payload_str)
        assert payload["device_id"] == "esp32-01"
        assert payload["state"] == "active"

    def test_from_dict_with_options(self):
        """Test creating message with custom options."""
        data = {"value": 42}

        msg = MQTTMessage.from_dict(
            "its/sensors/temp",
            data,
            qos=2,
            retain=True
        )

        assert msg.qos == 2
        assert msg.retain is True


class TestMessageHandlers:
    """Test MQTT message handlers."""

    def test_device_event_handler_import(self):
        """Test importing DeviceEventHandler."""
        from mqtt.handlers import DeviceEventHandler, MessageHandler

        handler = DeviceEventHandler()
        assert isinstance(handler, MessageHandler)

    def test_device_event_handler_registration(self):
        """Test handling device registration."""
        from mqtt.handlers import DeviceEventHandler

        handler = DeviceEventHandler()

        payload = {
            'id': 'test-device',
            'ip': '192.168.1.100',
            'hardware': 'ESP32-WROOM32'
        }

        result = handler.handle('its/devices/register', payload)

        assert result is not None
        assert result.get('device_id') == 'test-device'
        assert result.get('status') in ['registered', 'failed']

    def test_device_event_handler_heartbeat(self):
        """Test handling heartbeat."""
        from mqtt.handlers import DeviceEventHandler

        handler = DeviceEventHandler()

        payload = {
            'device_id': 'test-device',
            'timestamp': '2024-01-01T00:00:00Z'
        }

        result = handler.handle('its/devices/test-device/heartbeat', payload)

        assert result is not None
        assert result.get('ack') is True

    def test_device_event_handler_state_change(self):
        """Test handling state change."""
        from mqtt.handlers import DeviceEventHandler

        handler = DeviceEventHandler()

        payload = {
            'device_id': 'test-device',
            'state': 'inactive'
        }

        result = handler.handle('its/devices/test-device/state', payload)

        assert result is not None

    def test_device_event_handler_callback(self):
        """Test event callbacks."""
        from mqtt.handlers import DeviceEventHandler

        handler = DeviceEventHandler()

        callback_data = {}

        def on_heartbeat(device_id, data):
            callback_data['device_id'] = device_id
            callback_data['called'] = True

        handler.on_event('heartbeat', on_heartbeat)

        payload = {'device_id': 'callback-test', 'timestamp': '2024-01-01T00:00:00Z'}
        handler.handle('its/devices/callback-test/heartbeat', payload)

        assert callback_data.get('called') is True
        assert callback_data.get('device_id') == 'callback-test'

    def test_sync_event_handler(self):
        """Test SyncEventHandler."""
        from mqtt.handlers import SyncEventHandler

        handler = SyncEventHandler(node_id="test-node")

        # Should not process own broadcasts
        payload = {
            'node_id': 'test-node',
            'events': []
        }

        result = handler.handle('its/sync/broadcast', payload)
        assert result is None

        # Should process broadcasts from other nodes
        payload = {
            'node_id': 'other-node',
            'events': []
        }

        result = handler.handle('its/sync/broadcast', payload)
        # No error means success

    def test_command_handler(self):
        """Test CommandHandler."""
        from mqtt.handlers import CommandHandler

        handler = CommandHandler()

        # Register a command
        def ping_handler(params):
            return {'pong': True}

        handler.register_command('ping', ping_handler)

        # Test handling the command
        payload = {'command': 'ping', 'params': {}}
        result = handler.handle('its/command', payload)

        assert result['command'] == 'ping'
        assert result['status'] == 'completed'
        assert result['result']['pong'] is True

    def test_command_handler_unknown(self):
        """Test handling unknown command."""
        from mqtt.handlers import CommandHandler

        handler = CommandHandler()

        payload = {'command': 'unknown_cmd', 'params': {}}
        result = handler.handle('its/command', payload)

        assert result['status'] == 'unknown'


# Run with: pytest tests/test_mqtt.py -v
if __name__ == '__main__':
    pytest.main([__file__, '-v'])
