"""
Comparison baselines of the classifier: logistic regression, k nearest
neighbours, and the rule classifier without the board name.

Project: PINV01-24 - FIUNA
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pnp_ai.dataset_generator.catalog import CatalogDatasetGenerator, load_catalog  # noqa: E402
from pnp_ai.device_classifier import ML_MODEL_TYPES, DeviceClassifier            # noqa: E402
from pnp_ai.features import NAME_KEYS, without_hardware_name                    # noqa: E402

CATALOG = ROOT / "datasets" / "catalog" / "catalogo_placas.csv"


def _train(model_type):
    gen = CatalogDatasetGenerator(seed=0, catalog=load_catalog(str(CATALOG)), labeling="hw")
    devices = gen.generate_dataset(300)
    clf = DeviceClassifier(model_type=model_type, feature_set="resource")
    clf.train([clf._extract_features(d) for d in devices], [d["device_type"] for d in devices])
    return clf, devices


@pytest.mark.parametrize("model_type", ["logistic_regression", "knn"])
def test_baseline_models_train_and_classify(model_type):
    clf, devices = _train(model_type)
    assert clf.is_trained
    res = clf.classify(devices[0])
    assert res.device_type in set(d["device_type"] for d in devices)
    assert 0.0 <= res.confidence <= 1.0
    assert abs(sum(res.all_probabilities.values()) - 1.0) < 1e-6


@pytest.mark.parametrize("model_type", ["logistic_regression", "knn"])
def test_baseline_models_in_the_evaluation_report(model_type):
    clf, devices = _train(model_type)
    report = clf.generate_evaluation_report(
        [clf._extract_features(d) for d in devices], [d["device_type"] for d in devices])
    assert report["model"] == model_type


def test_known_model_types():
    assert set(ML_MODEL_TYPES) == {"decision_tree", "random_forest", "svm", "mlp",
                                   "logistic_regression", "knn"}
    _, devices = _train("decision_tree")
    clf = DeviceClassifier(model_type="boosting", feature_set="resource")
    with pytest.raises(ValueError):
        clf.train([clf._extract_features(d) for d in devices], [d["device_type"] for d in devices])


def test_without_hardware_name():
    ann = {"id": "x", "hardware": "ESP32-C3-Zero", "hardware_type": "esp32", "mcu": "ESP32-C3",
           "memory_total": 295580, "link_type": "wifi"}
    out = without_hardware_name(ann)
    assert not set(NAME_KEYS) & set(out)
    assert out == {"id": "x", "memory_total": 295580, "link_type": "wifi"}
    assert ann["hardware"] == "ESP32-C3-Zero"          # the original is not changed


def test_nameless_rules_use_capabilities_and_memory():
    rules = DeviceClassifier(model_type="rule_based")
    pi = {"hardware": "Raspberry Pi 5", "memory_total": 8 * 1024**3, "link_type": "ethernet"}
    gnss = {"hardware": "Raspberry Pi 5", "memory_total": 8 * 1024**3, "capabilities": ["gnss"]}
    assert rules.classify(pi).confidence >= 0.9                 # a name rule fired
    assert rules.classify(without_hardware_name(pi)).confidence < 0.9
    assert rules.classify(without_hardware_name(gnss)).device_type == "GNSSReceiver"
