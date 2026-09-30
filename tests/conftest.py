"""
Pytest Configuration and Fixtures
Project: PINV01-24 - FIUNA
"""

import pytest
import sys
import os
from pathlib import Path

# Add parent directory to path
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))


@pytest.fixture(scope="session")
def test_data_dir(tmp_path_factory):
    """Create temporary directory for test data."""
    return tmp_path_factory.mktemp("data")


@pytest.fixture
def sample_device_data():
    """Sample device data for testing."""
    return {
        'id': 'test-device-001',
        'ip': '192.168.1.100',
        'hardware': 'ESP32-WROOM32',
        'location': 'Test Lab',
        'memory_total': 520000,
        'memory_free': 300000,
        'cpu_freq_mhz': 240
    }


@pytest.fixture
def sample_esp32_data():
    """Sample ESP32 device data."""
    return {
        'id': 'esp32-test-01',
        'ip': '192.168.1.101',
        'hardware': 'ESP32-WROOM32',
        'hardware_type': 'ESP32-WROOM32',
        'memory_total': 520000,
        'memory_free': 300000,
        'cpu_freq_mhz': 240,
        'connection_type': 'wifi'
    }


@pytest.fixture
def sample_raspberry_pi_data():
    """Sample Raspberry Pi device data."""
    return {
        'id': 'rpi-test-01',
        'ip': '192.168.1.102',
        'hardware': 'Raspberry Pi 4 Model B',
        'hardware_type': 'Raspberry Pi 4',
        'memory_total': 4000000000,
        'memory_free': 2000000000,
        'cpu_cores': 4,
        'cpu_freq_mhz': 1500
    }


@pytest.fixture
def sample_jetson_data():
    """Sample Jetson Nano device data."""
    return {
        'id': 'jetson-test-01',
        'ip': '192.168.1.103',
        'hardware': 'NVIDIA Jetson Nano',
        'hardware_type': 'Jetson Nano',
        'memory_total': 4000000000,
        'memory_free': 2000000000,
        'cpu_cores': 4,
        'has_gpu': True
    }


@pytest.fixture
def multiple_devices(sample_esp32_data, sample_raspberry_pi_data, sample_jetson_data):
    """Collection of sample devices."""
    return [
        sample_esp32_data,
        sample_raspberry_pi_data,
        sample_jetson_data
    ]


@pytest.fixture
def app():
    """Create test Flask application."""
    from server.app import create_app
    from server.config import Config

    config = Config()
    config.enable_dag = True
    config.classifier_type = 'rule_based'
    config.auto_train = False

    app = create_app(config)
    app.config['TESTING'] = True

    yield app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def mqtt_config():
    """MQTT broker configuration for testing."""
    from mqtt.broker import MQTTBrokerConfig

    return MQTTBrokerConfig(
        host="localhost",
        port=1883,
        topic_prefix="its-test"
    )


@pytest.fixture
def classifier():
    """Create device classifier for testing."""
    from pnp_ai.device_classifier import DeviceClassifier

    return DeviceClassifier(model_type='rule_based')


@pytest.fixture
def dag():
    """Create BlockDAG for testing."""
    from blockdag import BlockDAG

    return BlockDAG(node_id='test-node')
