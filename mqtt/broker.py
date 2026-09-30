"""
MQTT Broker Configuration
Project: PINV01-24 - FIUNA

Configuration for MQTT broker connections and topic management.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MQTTBrokerConfig:
    """MQTT broker connection configuration."""

    host: str = "localhost"
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    keepalive: int = 60
    use_tls: bool = False
    ca_cert: Optional[str] = None

    # QoS levels: 0=at most once, 1=at least once, 2=exactly once
    default_qos: int = 1

    # Topic prefixes
    topic_prefix: str = "its"

    def get_topic(self, *parts: str) -> str:
        """Build topic string with prefix."""
        return "/".join([self.topic_prefix] + list(parts))


class TopicRegistry:
    """Registry of MQTT topics used by ITS."""

    # Device topics
    DEVICE_REGISTER = "devices/register"
    DEVICE_HEARTBEAT = "devices/{device_id}/heartbeat"
    DEVICE_STATE = "devices/{device_id}/state"
    DEVICE_DATA = "devices/{device_id}/data"
    DEVICE_COMMAND = "devices/{device_id}/command"
    DEVICE_RESPONSE = "devices/{device_id}/response"

    # Classification topics
    CLASSIFY_REQUEST = "classify/request"
    CLASSIFY_RESPONSE = "classify/response/{device_id}"

    # Sync topics (BlockDAG)
    SYNC_BROADCAST = "sync/broadcast"
    SYNC_REQUEST = "sync/request"
    SYNC_RESPONSE = "sync/response/{node_id}"

    # System topics
    SYSTEM_STATUS = "system/status"
    SYSTEM_ALERT = "system/alert"

    @classmethod
    def format_topic(cls, topic: str, **kwargs) -> str:
        """Format topic with placeholders."""
        return topic.format(**kwargs)

    @classmethod
    def get_device_topics(cls, device_id: str, prefix: str = "its") -> dict:
        """Get all topics for a specific device."""
        return {
            "heartbeat": f"{prefix}/{cls.DEVICE_HEARTBEAT.format(device_id=device_id)}",
            "state": f"{prefix}/{cls.DEVICE_STATE.format(device_id=device_id)}",
            "data": f"{prefix}/{cls.DEVICE_DATA.format(device_id=device_id)}",
            "command": f"{prefix}/{cls.DEVICE_COMMAND.format(device_id=device_id)}",
            "response": f"{prefix}/{cls.DEVICE_RESPONSE.format(device_id=device_id)}"
        }


@dataclass
class MQTTMessage:
    """Represents an MQTT message."""

    topic: str
    payload: bytes
    qos: int = 1
    retain: bool = False

    @property
    def payload_str(self) -> str:
        """Get payload as string."""
        return self.payload.decode('utf-8')

    @classmethod
    def from_dict(cls, topic: str, data: dict, qos: int = 1, retain: bool = False) -> 'MQTTMessage':
        """Create message from dictionary."""
        import json
        return cls(
            topic=topic,
            payload=json.dumps(data).encode('utf-8'),
            qos=qos,
            retain=retain
        )
