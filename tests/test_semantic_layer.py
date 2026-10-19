"""
Tests for the Semantic Layer
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from semantic_layer import SemanticDeviceManager, RDFStore, SHACLValidator


class TestRDFStore:
    """Tests for RDFStore class."""

    def test_register_device_basic(self):
        """Test basic device registration."""
        store = RDFStore(load_ontology=False)

        device_data = {
            'device_id': 'test-device-01',
            'ip_address': '192.168.1.100',
            'hardware_type': 'ESP32-WROOM32'
        }

        uri = store.register_device(device_data)

        assert uri is not None
        assert 'test-device-01' in str(uri)

    def test_register_device_alternative_keys(self):
        """Test registration with alternative key names."""
        store = RDFStore(load_ontology=False)

        # Using 'id' instead of 'device_id', 'ip' instead of 'ip_address'
        device_data = {
            'id': 'esp32-wifi-01',
            'ip': '192.168.1.101',
            'hardware': 'ESP32-WROOM32'
        }

        uri = store.register_device(device_data)
        device = store.get_device('esp32-wifi-01')

        assert device is not None
        assert device['device_id'] == 'esp32-wifi-01'

    def test_get_device_not_found(self):
        """Test getting a non-existent device."""
        store = RDFStore(load_ontology=False)
        device = store.get_device('nonexistent-device')

        assert device is None

    def test_update_device_state(self):
        """Test updating device state."""
        store = RDFStore(load_ontology=False)

        store.register_device({
            'device_id': 'test-device',
            'ip_address': '192.168.1.1',
            'hardware_type': 'ESP32'
        })

        success = store.update_device_state('test-device', 'inactive')
        assert success

        device = store.get_device('test-device')
        assert device['state'] == 'inactive'

    def test_get_all_devices(self):
        """Test getting all devices."""
        store = RDFStore(load_ontology=False)

        for i in range(3):
            store.register_device({
                'device_id': f'device-{i}',
                'ip_address': f'192.168.1.{i+1}',
                'hardware_type': 'ESP32'
            })

        devices = store.get_all_devices()
        assert len(devices) == 3

    def test_remove_device(self):
        """Test device removal."""
        store = RDFStore(load_ontology=False)

        store.register_device({
            'device_id': 'to-remove',
            'ip_address': '192.168.1.1',
            'hardware_type': 'ESP32'
        })

        success = store.remove_device('to-remove')
        assert success

        device = store.get_device('to-remove')
        assert device is None

    def test_json_export(self):
        """Test JSON export compatibility."""
        store = RDFStore(load_ontology=False)

        store.register_device({
            'id': 'esp32-test',
            'ip': '192.168.1.50',
            'hardware': 'ESP32-WROOM32'
        })

        json_str = store.to_json()
        assert 'esp32-test' in json_str
        assert '192.168.1.50' in json_str


class TestSHACLValidator:
    """Tests for SHACLValidator class."""

    def test_validate_device_registration_valid(self):
        """Test validation of valid device data."""
        validator = SHACLValidator(shapes_path=None)

        device_data = {
            'device_id': 'valid-device-01',
            'ip_address': '192.168.1.100',
            'hardware_type': 'ESP32-WROOM32',
            'memory_total': 331488
        }

        result = validator.validate_device_registration(device_data)

        assert result.is_valid
        assert len(result.violations) == 0

    def test_validate_device_registration_missing_id(self):
        """Test validation with missing device_id."""
        validator = SHACLValidator(shapes_path=None)

        device_data = {
            'ip_address': '192.168.1.100',
            'hardware_type': 'ESP32'
        }

        result = validator.validate_device_registration(device_data)

        assert not result.is_valid
        assert any('device_id' in v.get('path', '') for v in result.violations)

    def test_validate_device_registration_invalid_ip(self):
        """Test validation with invalid IP address."""
        validator = SHACLValidator(shapes_path=None)

        device_data = {
            'device_id': 'test-device',
            'ip_address': '999.999.999.999',
            'hardware_type': 'ESP32'
        }

        result = validator.validate_device_registration(device_data)

        assert not result.is_valid
        assert any('ip_address' in v.get('path', '') for v in result.violations)

    def test_validate_state_transition_valid(self):
        """Test valid state transition."""
        validator = SHACLValidator(shapes_path=None)

        result = validator.validate_state_transition('active', 'inactive')

        assert result.is_valid

    def test_validate_state_transition_invalid_state(self):
        """Test invalid state value."""
        validator = SHACLValidator(shapes_path=None)

        result = validator.validate_state_transition('active', 'unknown_state')

        assert not result.is_valid


class TestSemanticDeviceManager:
    """Tests for SemanticDeviceManager class."""

    def test_register_device_full_flow(self):
        """Test complete device registration flow."""
        manager = SemanticDeviceManager(validate_on_register=True)

        result = manager.register_device({
            'id': 'esp32-wifi-01',
            'ip': '192.168.1.101',
            'hardware': 'ESP32-WROOM32',
            'memory_total': 331488
        })

        assert result['success']
        assert result['device_id'] == 'esp32-wifi-01'

    def test_register_device_validation_failure(self):
        """Test registration with invalid data."""
        manager = SemanticDeviceManager(validate_on_register=True)

        result = manager.register_device({
            'id': 'test-device',
            'ip': 'invalid-ip',
            'hardware': 'ESP32'
        })

        assert not result['success']

    def test_heartbeat(self):
        """Test device heartbeat."""
        manager = SemanticDeviceManager(validate_on_register=False)

        manager.register_device({
            'device_id': 'heartbeat-test',
            'ip_address': '192.168.1.1',
            'hardware_type': 'ESP32'
        })

        result = manager.heartbeat('heartbeat-test')

        assert result['success']
        assert 'timestamp' in result

    def test_get_statistics(self):
        """Test getting statistics."""
        manager = SemanticDeviceManager(validate_on_register=False)

        for i in range(5):
            manager.register_device({
                'device_id': f'device-{i}',
                'ip_address': f'192.168.1.{i}',
                'hardware_type': 'ESP32'
            })

        stats = manager.get_statistics()

        assert stats['total_devices'] == 5
        assert stats['active_devices'] == 5


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
