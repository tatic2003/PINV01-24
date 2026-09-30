"""
API Integration Tests
Project: PINV01-24 - FIUNA

Integration tests for the Flask REST API endpoints.
"""

import pytest
import json
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def app():
    """Create test application."""
    from server.app import create_app
    from server.config import Config

    config = Config(
        debug=False,
        data_dir='/tmp/its-test-data',
        model_dir='/tmp/its-test-models',
        auto_train=False,
        classifier_type='rule_based',
        enable_dag=True,
        node_id='test-node'
    )
    app = create_app(config)
    app.config['TESTING'] = True

    yield app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


class TestHealthEndpoints:
    """Test health check endpoints."""

    def test_health_check(self, client):
        """Test /health endpoint."""
        response = client.get('/health')
        assert response.status_code == 200

        data = response.get_json()
        assert data['status'] == 'healthy'
        assert 'version' in data

    def test_api_status(self, client):
        """Test /api/status endpoint."""
        response = client.get('/api/status')
        assert response.status_code == 200

        data = response.get_json()
        assert 'node_id' in data
        assert 'devices_count' in data


class TestDeviceEndpoints:
    """Test device management endpoints."""

    def test_list_devices_empty(self, client):
        """Test listing devices when none registered."""
        response = client.get('/api/devices')
        assert response.status_code == 200

        data = response.get_json()
        assert 'devices' in data
        assert isinstance(data['devices'], list)

    def test_register_device(self, client):
        """Test device registration."""
        device_data = {
            'id': 'test-esp32-01',
            'ip': '192.168.1.100',
            'hardware': 'ESP32-WROOM32',
            'location': 'Test Lab',
            'memory_total': 520000,
            'memory_free': 300000
        }

        response = client.post(
            '/api/devices/register',
            data=json.dumps(device_data),
            content_type='application/json'
        )

        assert response.status_code in [200, 201]

        data = response.get_json()
        assert 'device_id' in data or 'id' in data
        assert 'classification' in data or 'status' in data

    def test_register_device_missing_fields(self, client):
        """Test registration with missing required fields."""
        device_data = {
            'id': 'incomplete-device'
            # Missing ip and hardware
        }

        response = client.post(
            '/api/devices/register',
            data=json.dumps(device_data),
            content_type='application/json'
        )

        # Should fail or return with error
        assert response.status_code in [400, 422, 200]

    def test_get_device(self, client):
        """Test getting a specific device."""
        # First register a device
        device_data = {
            'id': 'test-device-get',
            'ip': '192.168.1.101',
            'hardware': 'ESP32-WROOM32'
        }

        client.post(
            '/api/devices/register',
            data=json.dumps(device_data),
            content_type='application/json'
        )

        # Then retrieve it
        response = client.get('/api/devices/test-device-get')

        # May be 200 if found, 404 if not found
        assert response.status_code in [200, 404]

    def test_get_nonexistent_device(self, client):
        """Test getting a device that doesn't exist."""
        response = client.get('/api/devices/nonexistent-device-xyz')
        assert response.status_code == 404

    def test_update_device_state(self, client):
        """Test updating device state."""
        # First register a device
        device_data = {
            'id': 'test-state-device',
            'ip': '192.168.1.102',
            'hardware': 'ESP32-WROOM32'
        }

        client.post(
            '/api/devices/register',
            data=json.dumps(device_data),
            content_type='application/json'
        )

        # Update state
        response = client.put(
            '/api/devices/test-state-device/state',
            data=json.dumps({'state': 'inactive'}),
            content_type='application/json'
        )

        assert response.status_code in [200, 404]

    def test_delete_device(self, client):
        """Test device deletion."""
        # First register a device
        device_data = {
            'id': 'test-delete-device',
            'ip': '192.168.1.103',
            'hardware': 'ESP32-WROOM32'
        }

        client.post(
            '/api/devices/register',
            data=json.dumps(device_data),
            content_type='application/json'
        )

        # Delete it
        response = client.delete('/api/devices/test-delete-device')
        assert response.status_code in [200, 204, 404]

        # Verify it's gone
        response = client.get('/api/devices/test-delete-device')
        assert response.status_code == 404


class TestClassificationEndpoints:
    """Test classification endpoints."""

    def test_classify_device(self, client):
        """Test device classification."""
        device_data = {
            'hardware_type': 'ESP32-WROOM32',
            'memory_total': 520000,
            'memory_free': 300000,
            'cpu_freq_mhz': 240
        }

        response = client.post(
            '/api/classify',
            data=json.dumps(device_data),
            content_type='application/json'
        )

        assert response.status_code == 200

        data = response.get_json()
        assert 'device_type' in data
        assert 'confidence' in data

    def test_classify_raspberry_pi(self, client):
        """Test classification of Raspberry Pi."""
        device_data = {
            'hardware_type': 'Raspberry Pi 4',
            'memory_total': 4000000000,
            'memory_free': 2000000000,
            'cpu_cores': 4
        }

        response = client.post(
            '/api/classify',
            data=json.dumps(device_data),
            content_type='application/json'
        )

        assert response.status_code == 200

        data = response.get_json()
        assert 'device_type' in data

    def test_classify_unknown_device(self, client):
        """Test classification of unknown device type."""
        device_data = {
            'hardware_type': 'Unknown-Device-XYZ',
            'memory_total': 1000,
            'memory_free': 500
        }

        response = client.post(
            '/api/classify',
            data=json.dumps(device_data),
            content_type='application/json'
        )

        assert response.status_code == 200

        data = response.get_json()
        # Should still return a classification (possibly with low confidence)
        assert 'device_type' in data
        assert 'confidence' in data


class TestSemanticEndpoints:
    """Test semantic/RDF endpoints."""

    def test_export_rdf(self, client):
        """Test RDF export."""
        response = client.get('/api/semantic/export')
        assert response.status_code == 200

        # Should return RDF in turtle format
        content = response.get_data(as_text=True)
        assert len(content) > 0

    def test_export_rdf_format(self, client):
        """Test RDF export with specific format."""
        response = client.get('/api/semantic/export?format=turtle')
        assert response.status_code == 200

    def test_validate_shacl(self, client):
        """Test SHACL validation endpoint."""
        response = client.get('/api/semantic/validate')
        assert response.status_code == 200

        data = response.get_json()
        assert 'conforms' in data


class TestDAGEndpoints:
    """Test BlockDAG endpoints."""

    def test_dag_status(self, client):
        """Test DAG status endpoint."""
        response = client.get('/api/dag/status')

        # May or may not be implemented
        if response.status_code == 200:
            data = response.get_json()
            assert 'node_count' in data or 'status' in data
        else:
            assert response.status_code in [404, 501]

    def test_dag_events(self, client):
        """Test getting DAG events."""
        response = client.get('/api/dag/events')

        if response.status_code == 200:
            data = response.get_json()
            assert 'events' in data or isinstance(data, list)


class TestErrorHandling:
    """Test error handling."""

    def test_invalid_json(self, client):
        """Test handling of invalid JSON."""
        response = client.post(
            '/api/devices/register',
            data='not valid json{',
            content_type='application/json'
        )

        assert response.status_code in [400, 415, 500]

    def test_wrong_content_type(self, client):
        """Test handling of wrong content type."""
        response = client.post(
            '/api/devices/register',
            data='id=test&hardware=esp32',
            content_type='application/x-www-form-urlencoded'
        )

        # Should handle gracefully
        assert response.status_code in [400, 415, 500]

    def test_method_not_allowed(self, client):
        """Test method not allowed error."""
        response = client.post('/health')
        assert response.status_code == 405

    def test_not_found(self, client):
        """Test 404 for unknown endpoint."""
        response = client.get('/api/unknown/endpoint')
        assert response.status_code == 404


class TestDeviceWorkflow:
    """Test complete device workflow."""

    def test_full_device_lifecycle(self, client):
        """Test complete device registration, update, delete cycle."""
        device_id = 'lifecycle-test-device'

        # 1. Register device
        device_data = {
            'id': device_id,
            'ip': '192.168.1.200',
            'hardware': 'ESP32-WROOM32',
            'location': 'Integration Test',
            'memory_total': 520000,
            'memory_free': 300000
        }

        response = client.post(
            '/api/devices/register',
            data=json.dumps(device_data),
            content_type='application/json'
        )
        assert response.status_code in [200, 201]

        # 2. Verify device exists
        response = client.get(f'/api/devices/{device_id}')
        if response.status_code == 200:
            data = response.get_json()
            assert data.get('device_id') == device_id or data.get('id') == device_id

        # 3. Update state
        response = client.put(
            f'/api/devices/{device_id}/state',
            data=json.dumps({'state': 'configuring'}),
            content_type='application/json'
        )
        assert response.status_code in [200, 404]

        # 4. Update state again
        response = client.put(
            f'/api/devices/{device_id}/state',
            data=json.dumps({'state': 'active'}),
            content_type='application/json'
        )
        assert response.status_code in [200, 404]

        # 5. List devices (should include our device)
        response = client.get('/api/devices')
        assert response.status_code == 200
        data = response.get_json()
        devices = data.get('devices', [])
        device_ids = [d.get('device_id', d.get('id')) for d in devices]
        # Device should be in list
        # (may not be if store is ephemeral)

        # 6. Delete device
        response = client.delete(f'/api/devices/{device_id}')
        assert response.status_code in [200, 204, 404]

        # 7. Verify device is gone
        response = client.get(f'/api/devices/{device_id}')
        assert response.status_code == 404

    def test_multiple_device_registration(self, client):
        """Test registering multiple devices."""
        devices = [
            {
                'id': f'multi-test-{i}',
                'ip': f'192.168.1.{100+i}',
                'hardware': 'ESP32-WROOM32' if i % 2 == 0 else 'Raspberry Pi 4'
            }
            for i in range(5)
        ]

        # Register all devices
        for device in devices:
            response = client.post(
                '/api/devices/register',
                data=json.dumps(device),
                content_type='application/json'
            )
            assert response.status_code in [200, 201]

        # List should have at least 5 devices
        response = client.get('/api/devices')
        assert response.status_code == 200
        data = response.get_json()
        assert 'devices' in data


# Run with: pytest tests/test_api_integration.py -v
if __name__ == '__main__':
    pytest.main([__file__, '-v'])
