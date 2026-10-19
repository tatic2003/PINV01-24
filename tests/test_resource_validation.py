"""
Validation of the announced resources (memory_total, memory_free, cpu_freq_mhz)
in the lightweight validator, the SHACL shapes and the registration endpoint.

Project: PINV01-24 - FIUNA
"""

import pytest

from semantic_layer.device_manager import SemanticDeviceManager
from semantic_layer.rdf_store import RDFStore
from semantic_layer.validator import SHACLValidator


def announcement(**over):
    data = {
        'id': 'esp32-test-01',
        'ip': '192.168.1.50',
        'hardware': 'ESP32-WROOM32',
        'memory_total': 331488,
        'memory_free': 250000,
        'cpu_freq_mhz': 240,
        'link_type': 'wifi',
    }
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


def lightweight(data):
    return SemanticDeviceManager().validate_announcement(data)


def shacl(data, device_type='ESP32Device'):
    mgr = SemanticDeviceManager()
    store = RDFStore(load_ontology=False)
    store.register_device(mgr._normalize_device_data(data), device_type=device_type)
    return SHACLValidator().validate(store.graph)


INVALID = {
    'missing_memory_total': dict(memory_total=None),
    'memory_not_a_number': dict(memory_total='lots'),
    'memory_total_zero': dict(memory_total=0),
    'negative_memory': dict(memory_total=-331488),
    'memory_free_gt_total': dict(memory_free=700000),
    'negative_memory_free': dict(memory_free=-1),
}


class TestLightweight:
    def test_valid_passes(self):
        assert lightweight(announcement()).is_valid

    def test_memory_free_is_optional(self):
        assert lightweight(announcement(memory_free=None)).is_valid

    def test_memory_free_equal_to_total_passes(self):
        assert lightweight(announcement(memory_free=331488)).is_valid

    @pytest.mark.parametrize('case', sorted(INVALID))
    def test_invalid_is_rejected(self, case):
        result = lightweight(announcement(**INVALID[case]))
        assert not result.is_valid
        assert any(v['path'].startswith('memory') for v in result.violations)

    def test_bool_is_not_a_number(self):
        assert not lightweight(announcement(memory_total=True)).is_valid

    def test_nonpositive_cpu_is_rejected(self):
        assert not lightweight(announcement(cpu_freq_mhz=0)).is_valid


class TestShacl:
    def test_valid_conforms(self):
        assert shacl(announcement()).is_valid

    @pytest.mark.parametrize('case', sorted(INVALID))
    def test_invalid_does_not_conform(self, case):
        assert not shacl(announcement(**INVALID[case])).is_valid

    def test_store_keeps_non_numeric_memory_as_string(self):
        # The store must not fail on a bad value; SHACL reports it instead.
        store = RDFStore(load_ontology=False)
        store.register_device({'device_id': 'x-1', 'ip_address': '10.0.0.1',
                               'hardware_type': 'h', 'memory_total': 'lots'})


@pytest.fixture
def client(tmp_path):
    from server.app import create_app
    from server.config import Config
    app = create_app(Config(
        debug=False, data_dir=str(tmp_path / 'data'), model_dir=str(tmp_path / 'models'),
        auto_train=False, classifier_type='rule_based', enable_dag=True,
        node_id='test-node'))
    app.config['TESTING'] = True
    return app.test_client()


class TestEndpoint:
    def test_valid_registers(self, client):
        assert client.post('/api/devices/register', json=announcement()).status_code == 200

    @pytest.mark.parametrize('case', sorted(INVALID))
    def test_invalid_is_400_not_500(self, client, case):
        resp = client.post('/api/devices/register', json=announcement(**INVALID[case]))
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['error'] == 'Validation failed'
        # Rejected before classification: only the validation time is reported.
        assert set(body['timing_ms']) == {'semantic'}

    def test_classifier_error_is_400(self, client):
        # A value the classifier cannot convert must not end in a 500.
        app = client.application

        def broken(_data):
            raise ValueError("could not convert string to float: 'lots'")
        app.classifier.classify = broken
        assert client.post('/api/devices/register', json=announcement()).status_code == 400
        assert client.post('/api/classify', json=announcement()).status_code == 400


@pytest.fixture
def shacl_client(tmp_path):
    from server.app import create_app
    from server.config import Config
    app = create_app(Config(
        debug=False, data_dir=str(tmp_path / 'data'), model_dir=str(tmp_path / 'models'),
        auto_train=False, classifier_type='rule_based', enable_dag=True,
        node_id='test-node', shacl_on_register=True))
    app.config['TESTING'] = True
    return app.test_client()


class TestShaclOnRegister:
    def test_off_by_default(self, client):
        body = client.post('/api/devices/register', json=announcement()).get_json()
        assert 'shacl' not in body['timing_ms']

    def test_valid_registers_and_reports_shacl_time(self, shacl_client):
        resp = shacl_client.post('/api/devices/register', json=announcement())
        assert resp.status_code == 200
        timing = resp.get_json()['timing_ms']
        assert timing['shacl'] > 0
        assert timing['total'] >= timing['shacl']

    def test_shacl_rejection_is_400(self, shacl_client):
        # Whatever passes the lightweight check but fails SHACL is rejected.
        from semantic_layer.validator import ValidationResult
        app = shacl_client.application
        app.shacl_validator.validate = lambda graph: ValidationResult(
            conforms=False, violations=[{'message': 'forced'}], warnings=[], info=[])
        resp = shacl_client.post('/api/devices/register', json=announcement())
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['error'] == 'SHACL validation failed'
        assert 'shacl' in body['timing_ms']

    def test_bad_identifier_is_rejected_by_both(self, shacl_client):
        assert not lightweight(announcement(id='esp32 #bad')).is_valid
        assert not shacl(announcement(id='esp32 #bad')).is_valid
        assert shacl_client.post('/api/devices/register',
                                 json=announcement(id='esp32 #bad')).status_code == 400

    def test_lightweight_rejection_skips_shacl(self, shacl_client):
        resp = shacl_client.post('/api/devices/register', json=announcement(memory_total=None))
        assert resp.status_code == 400
        assert set(resp.get_json()['timing_ms']) == {'semantic'}


class TestMissingHardware:
    def test_shacl_reports_missing_hardware(self):
        assert not shacl(announcement(hardware=None)).is_valid


class TestNoClassSpecificLimits:
    """The shapes must not flag real boards for their announced memory or name."""

    @pytest.mark.parametrize('board, cls, memory', [
        ('esp32-c3-645ce0', 'ESP32Device', 295_580),          # ESP32-C3 free heap
        ('esp32-wroom32-267a68', 'ESP32Device', 331_488),
        ('sbc-opizero-01', 'SingleBoardComputer', 508_035_072),  # Orange Pi Zero, 512 MB
        ('mydevice-01', 'ESP32Device', 331_488),               # no 'esp32-' prefix
    ])
    def test_real_boards_have_no_findings(self, board, cls, memory):
        result = shacl(announcement(id=board, memory_total=memory, memory_free=memory // 2),
                       device_type=cls)
        assert result.is_valid
        assert not result.warnings and not result.info
