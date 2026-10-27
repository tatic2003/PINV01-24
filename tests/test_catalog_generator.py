"""
Tests for the catalog-based synthetic generator (pnp_ai/dataset_generator/catalog.py).

Project: PINV01-24 - FIUNA
"""

import pytest

from pnp_ai.dataset_generator.catalog import (
    GNSS_LABEL, CatalogDatasetGenerator, load_catalog)
from pnp_ai.device_classifier import DeviceClassifier
from pnp_ai.features import extract_features


@pytest.fixture(scope='module')
def catalog():
    return load_catalog()


def test_catalog_loads_with_version(catalog):
    assert len(catalog.boards) >= 100
    assert len(catalog.version) == 12
    assert catalog.get('raspberry_pi_5_8gb').ram_nominal == 8589934592


def test_measured_boards_have_a_link(catalog):
    for b in catalog.boards:
        if b.measured_memory:
            assert b.measured_link in b.links, b.board_id


@pytest.mark.parametrize('labeling', ['clasificador', 'hw', 'hw_unificado'])
def test_training_classes_leave_the_receiver_out(catalog, labeling):
    """The class the rule decides is not in the training data by default."""
    gen = CatalogDatasetGenerator(seed=1, catalog=catalog, labeling=labeling)
    assert 'Microcontroller' in gen.classes
    assert GNSS_LABEL not in gen.classes
    labels = {d['device_type'] for d in gen.generate_dataset(600)}
    assert labels == set(gen.classes)


@pytest.mark.parametrize('labeling', ['clasificador', 'hw', 'hw_unificado'])
def test_receivers_on_request(catalog, labeling):
    """Experiments that need receiver announcements ask for them."""
    gen = CatalogDatasetGenerator(seed=1, catalog=catalog, labeling=labeling,
                                  include_gnss=True)
    assert GNSS_LABEL in gen.classes
    labels = {d['device_type'] for d in gen.generate_dataset(600)}
    assert labels == set(gen.classes)


def test_same_seed_same_data(catalog):
    a = CatalogDatasetGenerator(seed=7, catalog=catalog).generate_dataset(50)
    b = CatalogDatasetGenerator(seed=7, catalog=catalog).generate_dataset(50)
    assert a == b


def test_devices_have_the_features_schema(catalog):
    gen = CatalogDatasetGenerator(seed=3, catalog=catalog)
    for d in gen.generate_dataset(100):
        f = extract_features(d, 'resource')
        assert f['memory_total_mb'] > 0
        assert f['cpu_freq_mhz'] > 0
        assert d['catalog_version'] == catalog.version
        assert 'declares_gnss' not in f


def test_nominal_mode_announces_nominal_ram(catalog):
    gen = CatalogDatasetGenerator(seed=3, catalog=catalog, calibrated=False)
    for d in gen.generate_dataset(200):
        assert d['memory_total'] == catalog.get(d['board_id']).ram_nominal


def test_calibrated_mode_uses_measurement_on_its_link(catalog):
    gen = CatalogDatasetGenerator(seed=3, catalog=catalog)
    board = catalog.get('olimex_esp32_poe_16mb')
    seen = set()
    for _ in range(50):
        d = gen.device_from_board(board, 'ESP32Device')
        if d['link_type'] == 'ethernet':
            assert d['memory_total'] == board.measured_memory
            seen.add('ethernet')
        else:
            assert d['memory_total'] < board.ram_nominal  # heap, not SRAM
            seen.add(d['link_type'])
    assert 'ethernet' in seen


def test_exclude_board_removes_it_from_sampling_and_calibration(catalog):
    held_out = 'raspberry_pi_5_8gb'
    gen = CatalogDatasetGenerator(seed=5, catalog=catalog, exclude_boards=[held_out])
    assert held_out not in {b.board_id for b in gen.boards}
    assert held_out not in gen.calibration.measured_boards
    assert held_out not in {d['board_id'] for d in gen.generate_dataset(2000)}


def test_unknown_excluded_board_fails_loudly(catalog):
    with pytest.raises(KeyError):
        CatalogDatasetGenerator(seed=1, catalog=catalog, exclude_boards=['no_such_board'])


def test_model_trained_on_catalog_classifies_real_announcements(catalog):
    """Real payloads captured(datasets/real/announcements_real.jsonl)."""
    gen = CatalogDatasetGenerator(seed=0, catalog=catalog)
    devices = gen.generate_anomalies(gen.generate_dataset(1500), 0.1)
    clf = DeviceClassifier(model_type='random_forest', feature_set='resource')
    clf.train([clf._extract_features(d) for d in devices], [d['device_type'] for d in devices])

    esp32 = {'id': 'esp32-wroom32-267a68', 'hardware': 'ESP32-WROOM32', 'link_type': 'wifi',
             'protocol': 'mqtt', 'memory_total': 331488, 'memory_free': 224096, 'cpu_freq_mhz': 240}
    pi5 = {'id': 'sbc-pi5-01', 'hardware': 'Raspberry Pi 5 Model B Rev 1.0', 'link_type': 'wifi',
           'protocol': 'mqtt', 'memory_total': 8454012928, 'memory_free': 7965753344,
           'cpu_freq_mhz': 2400}
    gnss = dict(pi5, id='gnss-pi5-01', capabilities=['gnss', 'position', 'time'])

    assert clf.classify(esp32).device_type == 'ESP32Device'
    assert clf.classify(pi5).device_type == 'SingleBoardComputer'
    assert clf.classify(gnss).device_type == GNSS_LABEL


def test_unified_labeling_merges_esp32_into_microcontroller(catalog):
    gen = CatalogDatasetGenerator(seed=2, catalog=catalog, labeling='hw_unificado')
    assert 'ESP32Device' not in gen.classes
    esp32_rows = [d for d in gen.generate_dataset(600)
                  if catalog.get(d['board_id']).clase_hw == 'ESP32Device' and 'capabilities' not in d]
    assert esp32_rows and all(d['device_type'] == 'Microcontroller' for d in esp32_rows)
