"""
The server can train its classifier from the real-board catalog
(--training-source catalog), the training data used in the paper.

Project: PINV01-24 - FIUNA
"""

import pytest

from server import create_app, Config

UNO = {
    "id": "mcu-uno-test", "hardware": "Arduino Uno R3 (ATmega328P)",
    "ip": "192.168.0.21", "location": "Lab-Test", "link_type": "serial",
    "connection_type": "serial", "protocol": "mqtt",
    "cpu_freq_mhz": 16, "memory_total": 2048, "memory_free": 1818,
}


def _app(source):
    return create_app(Config(classifier_type='random_forest', classifier_feature_set='resource',
                             training_source=source, training_samples=2000, enable_dag=False))


def test_catalog_source_has_microcontroller_class():
    app = _app('catalog')
    status = app.test_client().get('/api/status').get_json()
    assert status['classifier']['training_source'] == 'catalog'
    assert 'Microcontroller' in status['classifier']['classes']


def test_catalog_source_classifies_uno_as_microcontroller():
    app = _app('catalog')
    r = app.test_client().post('/api/devices/register', json=UNO)
    assert r.status_code == 200
    assert r.get_json()['classification']['device_type'] == 'Microcontroller'


def test_profiles_source_unchanged():
    app = _app('profiles')
    status = app.test_client().get('/api/status').get_json()
    assert status['classifier']['training_source'] == 'profiles'
    assert 'Microcontroller' not in status['classifier']['classes']


def test_unknown_source_fails_loudly():
    with pytest.raises(RuntimeError):
        _app('nope')
