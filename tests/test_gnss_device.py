"""
GNSS receiver as a first-class ITS device.
Project: PINV01-24 - FIUNA

Covers: ontology typing, SHACL range validation, lightweight validation on
registration, capability-based classification, and the HTTP registration path.
"""

import os
import sys

import pytest
from rdflib import Graph, RDF, Literal
from rdflib.namespace import XSD

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from semantic_layer.rdf_store import RDFStore, IOT
from semantic_layer.validator import SHACLValidator


def gnss_announcement(**over):
    data = {
        'id': 'gnss-test-01',
        'ip': '192.168.1.60',
        'hardware': 'Adafruit Ultimate GPS HAT (PA1616D)',
        'capabilities': ['gnss', 'position', 'time'],
        'lat': -25.2637,
        'lon': -57.5759,
        'hdop': 0.9,
        'satellites': 9,
        'fix_quality': 1,
        'update_rate_hz': 1,
        # Resources of the host, as the GNSS agent announces them
        'memory_total': 8_454_012_928,
        'memory_free': 6_000_000_000,
    }
    data.update(over)
    return data


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


def shacl_result(store):
    return SHACLValidator().validate(store.graph)


def store_with(**data):
    from semantic_layer.device_manager import SemanticDeviceManager
    mgr = SemanticDeviceManager()
    norm = mgr._normalize_device_data(gnss_announcement(**data))
    store = RDFStore()
    store.register_device(norm, device_type='GNSSReceiver')
    return store


class TestOntology:
    def test_gnss_receiver_typed_in_graph(self):
        store = store_with()
        assert (IOT['device/gnss-test-01'], RDF.type, IOT.GNSSReceiver) in store.graph

    def test_hardware_name_fallback(self):
        store = RDFStore()
        store.register_device({'device_id': 'g2', 'ip_address': '10.0.0.2',
                               'hardware_type': 'Adafruit Ultimate GPS HAT'})
        assert (IOT['device/g2'], RDF.type, IOT.GNSSReceiver) in store.graph


class TestShacl:
    def test_valid_receiver_conforms(self):
        assert shacl_result(store_with()).is_valid

    def test_no_fix_without_position_conforms(self):
        r = shacl_result(store_with(lat=None, lon=None, fix_quality=0, hdop=None, satellites=0))
        assert r.is_valid

    @pytest.mark.parametrize('over', [
        {'lat': 91}, {'lat': -90.5}, {'lon': 180.5}, {'lon': -181},
        {'hdop': -1}, {'satellites': 65}, {'fix_quality': 9},
    ])
    def test_out_of_range_rejected(self, over):
        r = shacl_result(store_with(**over))
        assert not r.is_valid
        assert r.violations

    def test_fix_without_position_rejected(self):
        assert not shacl_result(store_with(lat=None, lon=None, fix_quality=1)).is_valid

    def test_literals_are_typed(self):
        store = store_with()
        uri = IOT['device/gnss-test-01']
        lat = next(store.graph.objects(uri, IOT.latitude))
        assert lat.datatype == XSD.decimal
        fix = next(store.graph.objects(uri, IOT.fixQuality))
        assert fix.datatype == XSD.integer


class TestLightweightValidation:
    def setup_method(self):
        from semantic_layer.device_manager import SemanticDeviceManager
        self.mgr = SemanticDeviceManager()

    def test_valid_registers(self):
        r = self.mgr.register_device(gnss_announcement(), device_type='GNSSReceiver')
        assert r['success'], r
        dev = self.mgr.get_device('gnss-test-01')
        assert dev['type'] == 'GNSSReceiver'
        assert float(dev['latitude']) == pytest.approx(-25.2637)

    @pytest.mark.parametrize('over', [
        {'lat': 95}, {'lon': 200}, {'hdop': -0.1}, {'satellites': 100},
        {'fix_quality': 12}, {'lat': 'abc'}, {'lat': None, 'lon': None},
    ])
    def test_invalid_rejected(self, over):
        r = self.mgr.register_device(gnss_announcement(**over), device_type='GNSSReceiver')
        assert not r['success']
        assert self.mgr.get_device('gnss-test-01') is None


class TestClassificationRule:
    def test_declared_capability_types_device(self, client):
        resp = client.post('/api/devices/register', json=gnss_announcement())
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body['classification']['device_type'] == 'GNSSReceiver'
        assert body['low_confidence'] is False
        assert body['classification']['confidence'] >= 0.9
        dev = client.get('/api/devices/gnss-test-01').get_json()
        assert dev['type'] == 'GNSSReceiver'

    def test_no_capability_leaves_classifier_alone(self, client):
        data = gnss_announcement(capabilities=[], hardware='ESP32-WROOM32', id='esp32-x')
        for k in ('lat', 'lon', 'hdop', 'satellites', 'fix_quality', 'update_rate_hz'):
            data.pop(k)
        resp = client.post('/api/devices/register', json=data)
        assert resp.status_code == 200
        assert resp.get_json()['classification']['device_type'] == 'ESP32Device'

    def test_invalid_announcement_rejected_over_http(self, client):
        resp = client.post('/api/devices/register', json=gnss_announcement(lat=123))
        assert resp.status_code == 400
        assert client.get('/api/devices/gnss-test-01').status_code == 404


class TestMLClassification:
    """The trained classifier, not a rule, decides the GNSSReceiver class."""

    @pytest.fixture(scope='class', params=['resource', 'resource+name'])
    def trained(self, request):
        from pnp_ai.device_classifier import train_classifier_from_synthetic_data
        clf, _ = train_classifier_from_synthetic_data(
            1500, 'decision_tree', request.param, seed=42)
        return clf

    PI5_HOST = {'memory_total': 8_589_934_592, 'memory_free': 6_000_000_000,
                'cpu_freq_mhz': 2400, 'link_type': 'ethernet', 'protocol': 'mqtt'}

    def test_gnss_class_is_in_the_model(self, trained):
        assert 'GNSSReceiver' in list(trained.label_encoder.classes_)

    def test_gps_hat_on_pi5_is_gnss_receiver(self, trained):
        data = dict(gnss_announcement(), **self.PI5_HOST)
        result = trained.classify(data)
        assert result.device_type == 'GNSSReceiver'
        assert result.confidence > 0.9

    def test_gps_on_microcontroller_is_gnss_receiver(self, trained):
        data = dict(gnss_announcement(hardware='ESP32-WROOM32+NEO-6M'),
                    memory_total=400_000, memory_free=250_000, cpu_freq_mhz=240,
                    link_type='wifi')
        assert trained.classify(data).device_type == 'GNSSReceiver'

    def test_same_pi5_without_capability_stays_sbc(self, trained):
        data = {'id': 'rpi5-plain', 'hardware': 'RaspberryPi-5', **self.PI5_HOST}
        assert trained.classify(data).device_type == 'SingleBoardComputer'

    def test_safety_net_only_for_models_without_the_class(self):
        from pnp_ai.capability_rules import apply_capability_rules
        from pnp_ai.device_classifier import ClassificationResult
        old = ClassificationResult('SingleBoardComputer', 0.9, {}, 'gateway', [])
        data = gnss_announcement()
        # Old 4-class model: the rule steps in.
        fixed = apply_capability_rules(data, old, ['ESP32Device', 'SingleBoardComputer'])
        assert fixed.device_type == 'GNSSReceiver'
        # Model that knows the class: its decision stands, even a wrong one.
        kept = apply_capability_rules(data, old, ['GNSSReceiver', 'SingleBoardComputer'])
        assert kept.device_type == 'SingleBoardComputer'
