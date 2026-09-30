"""
Tests for PnP-AI Module
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pnp_ai.dataset_generator import SyntheticDatasetGenerator, DEVICE_PROFILES
from pnp_ai.device_classifier import DeviceClassifier, ClassificationResult


class TestSyntheticDatasetGenerator:
    """Tests for SyntheticDatasetGenerator class."""

    def test_generator_initialization(self):
        """Test generator initialization with seed."""
        gen = SyntheticDatasetGenerator(seed=42)
        assert gen is not None

    def test_generate_single_device(self):
        """Test generating a single device."""
        gen = SyntheticDatasetGenerator(seed=42)

        device = gen.generate_device('esp32_wroom')

        assert device is not None
        assert 'device_id' in device
        assert 'ip_address' in device
        assert 'hardware_type' in device
        assert 'ESP32' in device['hardware_type']

    def test_generate_dataset(self):
        """Test generating a dataset."""
        gen = SyntheticDatasetGenerator(seed=42)

        devices = gen.generate_dataset(num_devices=50)

        assert len(devices) == 50

        # Check device types distribution
        types = [d['device_type'] for d in devices]
        assert 'ESP32Device' in types or 'SingleBoardComputer' in types

    def test_generate_with_distribution(self):
        """Test generating with custom distribution."""
        gen = SyntheticDatasetGenerator(seed=42)

        distribution = {
            'esp32_wroom': 0.8,
            'raspberry_pi': 0.2
        }

        devices = gen.generate_dataset(num_devices=100, profile_distribution=distribution)

        esp32_count = sum(1 for d in devices if d['device_type'] == 'ESP32Device')
        rpi_count = sum(1 for d in devices if d['device_type'] == 'SingleBoardComputer')

        # Should be roughly 80% ESP32, 20% RPi (with some variance)
        assert esp32_count > 50
        assert rpi_count > 10

    def test_generate_anomalies(self):
        """Test anomaly injection."""
        gen = SyntheticDatasetGenerator(seed=42)

        normal_devices = gen.generate_dataset(num_devices=20)
        with_anomalies = gen.generate_anomalies(normal_devices, anomaly_rate=0.5)

        anomaly_count = sum(1 for d in with_anomalies if d.get('is_anomaly'))

        assert anomaly_count > 0
        assert anomaly_count <= 20

    def test_generate_classification_dataset(self):
        """Test generating classification dataset."""
        gen = SyntheticDatasetGenerator(seed=42)

        features, labels = gen.generate_classification_dataset(num_samples=100)

        assert len(features) == 100
        assert len(labels) == 100
        assert all(isinstance(f, dict) for f in features)

    def test_generate_time_series(self):
        """Test generating time series data."""
        gen = SyntheticDatasetGenerator(seed=42)

        device = gen.generate_device()
        series = gen.generate_time_series(device, num_samples=50)

        assert len(series) == 50
        assert all('timestamp' in s for s in series)
        assert all('state' in s for s in series)


class TestDeviceClassifier:
    """Tests for DeviceClassifier class."""

    def test_rule_based_classifier(self):
        """Test rule-based classification."""
        classifier = DeviceClassifier(model_type='rule_based')

        result = classifier.classify({
            'hardware_type': 'ESP32-WROOM32',
            'memory_total': 520000
        })

        assert isinstance(result, ClassificationResult)
        assert result.device_type == 'ESP32Device'
        assert result.confidence > 0.9

    def test_classify_raspberry_pi(self):
        """Test classifying Raspberry Pi."""
        classifier = DeviceClassifier(model_type='rule_based')

        result = classifier.classify({
            'hardware_type': 'RaspberryPi4',
            'memory_total': 4294967296
        })

        assert result.device_type == 'SingleBoardComputer'
        assert result.confidence > 0.9

    def test_classify_unknown_device(self):
        """Test classifying unknown device."""
        classifier = DeviceClassifier(model_type='rule_based')

        result = classifier.classify({
            'hardware_type': 'UnknownDevice',
            'memory_total': 1000000
        })

        assert result.device_type is not None
        assert result.confidence > 0

    def test_suggested_role(self):
        """Test role suggestion."""
        classifier = DeviceClassifier(model_type='rule_based')

        result = classifier.classify({
            'hardware_type': 'ESP32-WROOM32',
            'memory_total': 520000
        })

        assert result.suggested_role is not None
        assert result.suggested_role in ['sensor', 'actuator', 'monitor', 'gateway']

    def test_suggested_capabilities(self):
        """Test capability suggestion."""
        classifier = DeviceClassifier(model_type='rule_based')

        result = classifier.classify({
            'hardware_type': 'ESP32-WROOM32',
            'memory_total': 520000
        })

        assert len(result.suggested_capabilities) > 0

    def test_classification_result_to_dict(self):
        """Test ClassificationResult serialization."""
        classifier = DeviceClassifier(model_type='rule_based')

        result = classifier.classify({
            'hardware_type': 'ESP32',
            'memory_total': 520000
        })

        result_dict = result.to_dict()

        assert 'device_type' in result_dict
        assert 'confidence' in result_dict
        assert 'suggested_role' in result_dict

    def test_ml_classifier_training(self):
        """Test ML classifier training (if sklearn available)."""
        try:
            from sklearn.tree import DecisionTreeClassifier
            sklearn_available = True
        except ImportError:
            sklearn_available = False

        if not sklearn_available:
            pytest.skip("sklearn not available")

        gen = SyntheticDatasetGenerator(seed=42)
        features, labels = gen.generate_classification_dataset(num_samples=200)

        classifier = DeviceClassifier(model_type='decision_tree')
        metrics = classifier.train(features, labels)

        assert metrics['accuracy'] > 0.5
        assert classifier.is_trained


class TestDeviceProfiles:
    """Tests for device profiles."""

    def test_all_profiles_exist(self):
        """Test that all expected profiles exist."""
        expected = ['esp32_wroom', 'esp32_olimex', 'raspberry_pi', 'jetson']

        for profile_name in expected:
            assert profile_name in DEVICE_PROFILES

    def test_profile_structure(self):
        """Test profile structure."""
        profile = DEVICE_PROFILES['esp32_wroom']

        assert hasattr(profile, 'device_type')
        assert hasattr(profile, 'hardware_variants')
        assert hasattr(profile, 'memory_range')
        assert hasattr(profile, 'connection_types')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
