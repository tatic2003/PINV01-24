"""
PnP-AI Demo - Synthetic Dataset Generation and Device Classification
Demonstrates the complete PnP-AI pipeline for automatic device onboarding.

Project: PINV01-24 - FIUNA
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from pnp_ai.dataset_generator import SyntheticDatasetGenerator
from pnp_ai.device_classifier import DeviceClassifier, train_classifier_from_synthetic_data


def demo_dataset_generation():
    """Demonstrate synthetic dataset generation."""
    print("=" * 60)
    print("1. SYNTHETIC DATASET GENERATION")
    print("=" * 60)

    # Create generator with seed for reproducibility
    generator = SyntheticDatasetGenerator(seed=42)

    # Generate a small dataset
    print("\nGenerating 20 synthetic devices...")
    devices = generator.generate_dataset(num_devices=20)

    # Show sample devices
    print("\nSample devices generated:")
    for device in devices[:5]:
        print(f"  - {device['device_id']}: {device['hardware_type']} ({device['device_type']})")
        print(f"    Memory: {device['memory_total'] / 1024:.0f} KB, Connection: {device['connection_type']}")
        print(f"    Role: {device['role']}, Context: {device['its_context']}")
        print()

    # Show distribution
    type_counts = {}
    for d in devices:
        t = d['device_type']
        type_counts[t] = type_counts.get(t, 0) + 1

    print("Device type distribution:")
    for dtype, count in sorted(type_counts.items()):
        print(f"  {dtype}: {count}")

    return devices


def demo_classification():
    """Demonstrate device classification."""
    print("\n" + "=" * 60)
    print("2. DEVICE CLASSIFICATION")
    print("=" * 60)

    # Train classifier with synthetic data
    print("\nTraining classifier with 500 synthetic samples...")
    classifier, metrics = train_classifier_from_synthetic_data(
        num_samples=500,
        model_type='decision_tree'
    )

    print(f"\nTraining Results:")
    print(f"  Model: {metrics['model']}")
    print(f"  Accuracy: {metrics['accuracy']:.2%}")
    print(f"  Classes: {metrics['classes']}")

    if metrics.get('feature_importance'):
        print(f"\nTop Feature Importance:")
        sorted_features = sorted(
            metrics['feature_importance'].items(),
            key=lambda x: x[1],
            reverse=True
        )
        for name, importance in sorted_features[:5]:
            print(f"  {name}: {importance:.3f}")

    return classifier


def demo_device_onboarding(classifier):
    """Demonstrate automatic device onboarding."""
    print("\n" + "=" * 60)
    print("3. AUTOMATIC DEVICE ONBOARDING")
    print("=" * 60)

    # Simulate incoming device announcements (like from ESP32)
    incoming_devices = [
        {
            'id': 'esp32-wifi-01',
            'ip': '192.168.1.101',
            'hardware': 'ESP32-WROOM32',
            'memory_total': 520000,
            'memory_free': 380000,
            'cpu_freq_mhz': 240,
            'connection_type': 'wifi'
        },
        {
            'id': 'rpi-gateway-01',
            'ip': '192.168.1.1',
            'hardware': 'RaspberryPi4',
            'memory_total': 4294967296,
            'memory_free': 3221225472,
            'cpu_freq_mhz': 1500,
            'connection_type': 'ethernet'
        },
        {
            'id': 'unknown-device-01',
            'ip': '192.168.1.50',
            'hardware': 'CustomBoard-v1',
            'memory_total': 2097152,
            'memory_free': 1572864,
            'cpu_freq_mhz': 400,
            'connection_type': 'wifi'
        }
    ]

    print("\nProcessing incoming device announcements...")
    for device in incoming_devices:
        result = classifier.classify(device)

        print(f"\n  Device: {device['id']} ({device['hardware']})")
        print(f"  Classified as: {result.device_type} (confidence: {result.confidence:.1%})")
        print(f"  Suggested role: {result.suggested_role}")
        print(f"  Capabilities: {', '.join(result.suggested_capabilities)}")


def demo_rule_based_fallback():
    """Demonstrate rule-based classification (no ML required)."""
    print("\n" + "=" * 60)
    print("4. RULE-BASED FALLBACK (No ML Required)")
    print("=" * 60)

    # Create rule-based classifier (works without sklearn)
    classifier = DeviceClassifier(model_type='rule_based')

    devices = [
        {'hardware_type': 'ESP32-WROOM32', 'memory_total': 520000},
        {'hardware_type': 'Raspberry Pi 4', 'memory_total': 4294967296},
        {'hardware_type': 'Jetson-Nano', 'memory_total': 4294967296},
        {'hardware_type': 'Unknown Device', 'memory_total': 65536},
    ]

    print("\nRule-based classification results:")
    for device in devices:
        result = classifier.classify(device)
        print(f"  {device['hardware_type']}: {result.device_type} ({result.confidence:.0%})")


def demo_anomaly_detection():
    """Demonstrate anomaly generation for testing."""
    print("\n" + "=" * 60)
    print("5. ANOMALY DETECTION DATASET")
    print("=" * 60)

    generator = SyntheticDatasetGenerator(seed=123)

    # Generate normal devices
    normal_devices = generator.generate_dataset(num_devices=10)

    # Inject anomalies
    mixed_devices = generator.generate_anomalies(normal_devices, anomaly_rate=0.3)

    print("\nDevices with anomalies injected (30% rate):")
    for device in mixed_devices:
        status = "ANOMALY" if device.get('is_anomaly') else "normal"
        anomaly_type = device.get('anomaly_type', '-')
        print(f"  {device['device_id']}: {status} ({anomaly_type})")


def main():
    print("=" * 60)
    print("PnP-AI Demo - IoT Device Classification for ITS")
    print("Project: PINV01-24 - FIUNA")
    print("=" * 60)

    # Run demos
    devices = demo_dataset_generation()
    classifier = demo_classification()
    demo_device_onboarding(classifier)
    demo_rule_based_fallback()
    demo_anomaly_detection()

    print("\n" + "=" * 60)
    print("Demo completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
