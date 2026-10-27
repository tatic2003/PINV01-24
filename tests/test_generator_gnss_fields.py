"""
Synthetic GNSS receivers carry the fields their SHACL shape requires.

The benchmark (baseline/central_baseline.py, baseline/run_distributed_benchmark.py)
validates every synthetic event against the SHACL shapes. The GNSSReceiver shape
requires fix_quality, and latitude/longitude when there is a fix, so a generator
that omits them makes valid-looking events fail validation.

Project: PINV01-24 - FIUNA
"""

from pnp_ai.dataset_generator import SyntheticDatasetGenerator
from semantic_layer.rdf_store import RDFStore
from semantic_layer.validator import SHACLValidator


def _gnss(devices):
    return [d for d in devices if d['device_type'] == 'GNSSReceiver']


def test_gnss_receivers_have_fix_quality_and_position_when_fixed():
    receivers = _gnss(SyntheticDatasetGenerator(seed=42, include_gnss=True)
                      .generate_dataset(500))
    assert receivers
    for d in receivers:
        assert d['fix_quality'] in (0, 1, 2)
        if d['fix_quality'] >= 1:
            assert -90 <= d['latitude'] <= 90 and -180 <= d['longitude'] <= 180
        else:
            assert 'latitude' not in d and 'longitude' not in d


def test_gnss_fields_do_not_change_the_rest_of_the_dataset():
    """The GNSS fields use their own RNG: other fields keep their previous values."""
    gnss_keys = {'fix_quality', 'update_rate_hz', 'latitude', 'longitude',
                 'satellites_used', 'hdop'}
    a = SyntheticDatasetGenerator(seed=7).generate_dataset(300)
    b = SyntheticDatasetGenerator(seed=7).generate_dataset(300)
    for x, y in zip(a, b):
        assert {k: v for k, v in x.items() if k not in gnss_keys | {'registered_at', 'last_seen'}} == \
               {k: v for k, v in y.items() if k not in gnss_keys | {'registered_at', 'last_seen'}}
    # Non-GNSS devices never get GNSS fields.
    assert not any(gnss_keys & set(d) for d in a if d['device_type'] != 'GNSSReceiver')


def test_every_synthetic_event_conforms_to_the_shapes():
    validator = SHACLValidator()
    for d in SyntheticDatasetGenerator(seed=42).generate_dataset(200):
        store = RDFStore(load_ontology=False)
        store.register_device(d)
        assert validator.validate(store.graph).conforms, (d['device_type'], d['hardware_type'])
