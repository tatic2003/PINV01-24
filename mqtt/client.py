"""
MQTT Client for ITS
Project: PINV01-24 - FIUNA

Async MQTT client for device communication.
"""

import json
import asyncio
import logging
from typing import Callable, Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False
    mqtt = None

from .broker import MQTTBrokerConfig, TopicRegistry, MQTTMessage

logger = logging.getLogger(__name__)


class ITSMQTTClient:
    """MQTT client for ITS device communication."""

    def __init__(self, config: MQTTBrokerConfig, node_id: str = "edge-node"):
        if not MQTT_AVAILABLE:
            raise ImportError("paho-mqtt is required. Install with: pip install paho-mqtt")

        self.config = config
        self.node_id = node_id
        self._client: Optional[mqtt.Client] = None
        self._connected = False
        self._subscriptions: Dict[str, List[Callable]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._message_queue: asyncio.Queue = None

    def connect(self) -> bool:
        """Connect to MQTT broker."""
        try:
            client_id = self.config.client_id or f"its-{self.node_id}"
            self._client = mqtt.Client(client_id=client_id)

            # Set callbacks
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            # Authentication
            if self.config.username:
                self._client.username_pw_set(
                    self.config.username,
                    self.config.password
                )

            # TLS
            if self.config.use_tls:
                self._client.tls_set(ca_certs=self.config.ca_cert)

            # Connect
            self._client.connect(
                self.config.host,
                self.config.port,
                self.config.keepalive
            )

            # Start network loop in background thread
            self._client.loop_start()

            logger.info(f"Connecting to MQTT broker at {self.config.host}:{self.config.port}")
            return True

        except Exception as e:
            logger.error(f"MQTT connection error: {e}")
            return False

    def disconnect(self):
        """Disconnect from MQTT broker."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False
            logger.info("Disconnected from MQTT broker")

    def is_connected(self) -> bool:
        """Check if connected to broker."""
        return self._connected

    def subscribe(self, topic: str, callback: Callable[[str, dict], None], qos: int = None):
        """
        Subscribe to a topic with a callback.

        Args:
            topic: MQTT topic (can include wildcards + and #)
            callback: Function to call when message received (topic, payload_dict)
            qos: Quality of service level
        """
        full_topic = self.config.get_topic(topic)

        if full_topic not in self._subscriptions:
            self._subscriptions[full_topic] = []

        self._subscriptions[full_topic].append(callback)

        if self._connected:
            self._client.subscribe(full_topic, qos or self.config.default_qos)
            logger.debug(f"Subscribed to {full_topic}")

    def unsubscribe(self, topic: str):
        """Unsubscribe from a topic."""
        full_topic = self.config.get_topic(topic)

        if full_topic in self._subscriptions:
            del self._subscriptions[full_topic]

        if self._connected:
            self._client.unsubscribe(full_topic)

    def publish(self, topic: str, payload: dict, qos: int = None, retain: bool = False) -> bool:
        """
        Publish a message to a topic.

        Args:
            topic: MQTT topic
            payload: Dictionary to send as JSON
            qos: Quality of service level
            retain: Whether to retain message on broker

        Returns:
            True if published successfully
        """
        if not self._connected:
            logger.warning("Cannot publish: not connected")
            return False

        full_topic = self.config.get_topic(topic)

        try:
            message = json.dumps(payload)
            result = self._client.publish(
                full_topic,
                message,
                qos=qos or self.config.default_qos,
                retain=retain
            )

            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Published to {full_topic}")
                return True
            else:
                logger.error(f"Publish failed: {result.rc}")
                return False

        except Exception as e:
            logger.error(f"Publish error: {e}")
            return False

    def publish_device_registration(self, device_data: dict) -> bool:
        """Publish device registration message."""
        device_data['timestamp'] = datetime.utcnow().isoformat()
        device_data['node_id'] = self.node_id
        return self.publish(TopicRegistry.DEVICE_REGISTER, device_data)

    def publish_device_heartbeat(self, device_id: str, status: dict = None) -> bool:
        """Publish device heartbeat."""
        topic = TopicRegistry.format_topic(
            TopicRegistry.DEVICE_HEARTBEAT,
            device_id=device_id
        )
        payload = {
            'device_id': device_id,
            'timestamp': datetime.utcnow().isoformat(),
            'status': status or {}
        }
        return self.publish(topic, payload)

    def publish_device_state(self, device_id: str, state: str, metadata: dict = None) -> bool:
        """Publish device state change."""
        topic = TopicRegistry.format_topic(
            TopicRegistry.DEVICE_STATE,
            device_id=device_id
        )
        payload = {
            'device_id': device_id,
            'state': state,
            'timestamp': datetime.utcnow().isoformat(),
            'metadata': metadata or {}
        }
        return self.publish(topic, payload, retain=True)

    def publish_device_data(self, device_id: str, data: dict) -> bool:
        """Publish device sensor/telemetry data."""
        topic = TopicRegistry.format_topic(
            TopicRegistry.DEVICE_DATA,
            device_id=device_id
        )
        payload = {
            'device_id': device_id,
            'timestamp': datetime.utcnow().isoformat(),
            'data': data
        }
        return self.publish(topic, payload)

    def send_command(self, device_id: str, command: str, params: dict = None) -> bool:
        """Send command to a device."""
        topic = TopicRegistry.format_topic(
            TopicRegistry.DEVICE_COMMAND,
            device_id=device_id
        )
        payload = {
            'command': command,
            'params': params or {},
            'timestamp': datetime.utcnow().isoformat(),
            'source': self.node_id
        }
        return self.publish(topic, payload)

    def publish_classification_result(self, device_id: str, classification: dict) -> bool:
        """Publish classification result for a device."""
        topic = TopicRegistry.format_topic(
            TopicRegistry.CLASSIFY_RESPONSE,
            device_id=device_id
        )
        payload = {
            'device_id': device_id,
            'classification': classification,
            'timestamp': datetime.utcnow().isoformat()
        }
        return self.publish(topic, payload)

    def broadcast_sync(self, dag_events: List[dict]) -> bool:
        """Broadcast DAG sync events to other nodes."""
        payload = {
            'node_id': self.node_id,
            'events': dag_events,
            'timestamp': datetime.utcnow().isoformat()
        }
        return self.publish(TopicRegistry.SYNC_BROADCAST, payload)

    # Callbacks

    def _on_connect(self, client, userdata, flags, rc):
        """Handle connection to broker."""
        if rc == 0:
            self._connected = True
            logger.info("Connected to MQTT broker")

            # Resubscribe to all topics
            for topic in self._subscriptions:
                client.subscribe(topic, self.config.default_qos)
                logger.debug(f"Resubscribed to {topic}")
        else:
            logger.error(f"Connection failed with code {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """Handle disconnection from broker."""
        self._connected = False
        if rc != 0:
            logger.warning(f"Unexpected disconnection (code {rc}), will reconnect")

    def _on_message(self, client, userdata, msg):
        """Handle incoming message."""
        try:
            topic = msg.topic
            payload = json.loads(msg.payload.decode('utf-8'))

            logger.debug(f"Received message on {topic}")

            # Find matching subscriptions (handle wildcards)
            for sub_topic, callbacks in self._subscriptions.items():
                if self._topic_matches(sub_topic, topic):
                    for callback in callbacks:
                        try:
                            callback(topic, payload)
                        except Exception as e:
                            logger.error(f"Callback error: {e}")

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON on {msg.topic}")
        except Exception as e:
            logger.error(f"Message handling error: {e}")

    def _topic_matches(self, pattern: str, topic: str) -> bool:
        """Check if topic matches subscription pattern (handles + and # wildcards)."""
        pattern_parts = pattern.split('/')
        topic_parts = topic.split('/')

        for i, p in enumerate(pattern_parts):
            if p == '#':
                return True  # Multi-level wildcard matches rest
            if i >= len(topic_parts):
                return False
            if p != '+' and p != topic_parts[i]:
                return False

        return len(pattern_parts) == len(topic_parts)


class AsyncMQTTClient(ITSMQTTClient):
    """Async wrapper for MQTT client."""

    async def connect_async(self) -> bool:
        """Connect to broker asynchronously."""
        self._loop = asyncio.get_event_loop()
        self._message_queue = asyncio.Queue()
        return self.connect()

    async def publish_async(self, topic: str, payload: dict, qos: int = None) -> bool:
        """Publish message asynchronously."""
        return await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.publish(topic, payload, qos)
        )

    async def wait_for_message(self, timeout: float = None) -> Optional[tuple]:
        """Wait for next message from subscribed topics."""
        try:
            return await asyncio.wait_for(
                self._message_queue.get(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return None
