"""
Full Integration Demo - Complete IoT-ITS Interoperability System
Demonstrates all components working together:
- Semantic Layer (RDF/SHACL)
- PnP-AI (Device Classification)
- Edge Agents (Discovery, Classification, Sync)
- BlockDAG (Event Logging)

Project: PINV01-24 - FIUNA
"""

import sys
import asyncio
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import all components
from semantic_layer import SemanticDeviceManager
from pnp_ai.dataset_generator import SyntheticDatasetGenerator
from pnp_ai.device_classifier import DeviceClassifier
from blockdag import BlockDAG, DAGStorage


def print_section(title: str):
    """Print a section header."""
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


def demo_semantic_layer():
    """Demonstrate the semantic layer."""
    print_section("1. SEMANTIC LAYER (RDF/SHACL)")

    manager = SemanticDeviceManager(validate_on_register=True)

    # Simulate ESP32 device announcements
    devices = [
        {"id": "esp32-wifi-01", "ip": "192.168.1.101", "hardware": "ESP32-WROOM32"},
        {"id": "esp32-olimex-01", "ip": "192.168.1.102", "hardware": "ESP32-OLIMEX-PoE"},
        {"id": "rpi-gateway-01", "ip": "192.168.1.1", "hardware": "RaspberryPi4"}
    ]

    print("\nRegistering devices with semantic validation...")
    for device in devices:
        result = manager.register_device(device)
        status = "OK" if result['success'] else "FAIL"
        print(f"  [{status}] {device['id']}: {result.get('message', result.get('error'))}")

    # Show RDF representation
    print("\nRDF Export (Turtle format - excerpt):")
    rdf = manager.export_rdf(format='turtle')
    # Show first 500 chars
    print(rdf[:500] + "...")

    # Run SHACL validation
    print("\nSHACL Validation:")
    validation = manager.validate_graph()
    print(f"  Conforms: {validation.conforms}")
    print(f"  Violations: {len(validation.violations)}")
    print(f"  Warnings: {len(validation.warnings)}")

    return manager


def demo_pnp_ai():
    """Demonstrate PnP-AI classification."""
    print_section("2. PnP-AI CLASSIFICATION")

    # Create and train classifier
    print("\nTraining classifier with synthetic data...")
    generator = SyntheticDatasetGenerator(seed=42)
    features, labels = generator.generate_classification_dataset(num_samples=300)

    classifier = DeviceClassifier(model_type='decision_tree')
    metrics = classifier.train(features, labels)

    print(f"  Model: {metrics['model']}")
    print(f"  Accuracy: {metrics['accuracy']:.1%}")

    # Classify incoming devices
    print("\nClassifying incoming devices...")
    test_devices = [
        {"hardware_type": "ESP32-WROOM32", "memory_total": 520000, "cpu_freq_mhz": 240, "connection_type": "wifi"},
        {"hardware_type": "RaspberryPi4", "memory_total": 4294967296, "cpu_freq_mhz": 1500, "connection_type": "ethernet"},
        {"hardware_type": "Unknown-Board", "memory_total": 1048576, "cpu_freq_mhz": 100, "connection_type": "wifi"}
    ]

    for device in test_devices:
        result = classifier.classify(device)
        print(f"  {device['hardware_type']}: {result.device_type} ({result.confidence:.0%})")
        print(f"    Suggested role: {result.suggested_role}")

    return classifier


def demo_blockdag():
    """Demonstrate BlockDAG event logging."""
    print_section("3. BLOCKDAG EVENT LOGGING")

    # Create DAG
    dag = BlockDAG(node_id="edge-node-01")
    print(f"\nCreated DAG with genesis node")
    print(f"  Current tips: {dag.get_tips()}")

    # Add device events
    print("\nAdding device events to DAG...")

    events = [
        ("device_register", {"device_id": "esp32-wifi-01", "hardware": "ESP32-WROOM32"}),
        ("device_register", {"device_id": "esp32-olimex-01", "hardware": "ESP32-OLIMEX-PoE"}),
        ("state_change", {"device_id": "esp32-wifi-01", "old": "configuring", "new": "active"}),
        ("classification", {"device_id": "esp32-wifi-01", "type": "ESP32Device", "confidence": 0.95}),
        ("device_register", {"device_id": "rpi-gateway-01", "hardware": "RaspberryPi4"})
    ]

    for event_type, payload in events:
        node = dag.add_event(event_type, payload)
        print(f"  Added: {event_type} -> {node.node_id[:30]}...")

    print(f"\nDAG Statistics:")
    print(f"  Total nodes: {len(dag)}")
    print(f"  Current tips: {len(dag.get_tips())}")

    # Show topological order
    print("\nTopological order (event sequence):")
    for node_id in dag.get_topological_order()[:5]:
        node = dag.get_node(node_id)
        print(f"  {node.event_type}: {node.timestamp}")

    return dag


def demo_synchronization():
    """Demonstrate DAG synchronization between nodes."""
    print_section("4. DAG SYNCHRONIZATION")

    # Create two DAGs (simulating two edge nodes)
    dag1 = BlockDAG(node_id="node-1")
    dag2 = BlockDAG(node_id="node-2")

    # Add events to node 1
    print("\nNode 1 adds events...")
    dag1.add_event("device_register", {"device_id": "sensor-01"})
    dag1.add_event("device_register", {"device_id": "sensor-02"})
    print(f"  Node 1 has {len(dag1)} nodes")

    # Add events to node 2
    print("\nNode 2 adds events...")
    dag2.add_event("device_register", {"device_id": "sensor-03"})
    dag2.add_event("state_change", {"device_id": "sensor-03", "state": "active"})
    print(f"  Node 2 has {len(dag2)} nodes")

    # Synchronize node 1 -> node 2
    print("\nSynchronizing Node 1 -> Node 2...")
    sync_data = dag1.get_sync_data()
    result = dag2.merge_sync_data(sync_data)
    print(f"  Added: {result['added']}, Existing: {result['existing']}")
    print(f"  Node 2 now has {len(dag2)} nodes")

    # Synchronize node 2 -> node 1
    print("\nSynchronizing Node 2 -> Node 1...")
    sync_data = dag2.get_sync_data()
    result = dag1.merge_sync_data(sync_data)
    print(f"  Added: {result['added']}, Existing: {result['existing']}")
    print(f"  Node 1 now has {len(dag1)} nodes")

    print("\nBoth nodes synchronized!")


def demo_full_pipeline():
    """Demonstrate the complete device onboarding pipeline."""
    print_section("5. FULL ONBOARDING PIPELINE")

    print("\nSimulating complete device onboarding flow...")
    print("-" * 50)

    # Initialize components
    semantic_manager = SemanticDeviceManager()
    classifier = DeviceClassifier(model_type='rule_based')
    dag = BlockDAG(node_id="gateway-01")

    # Incoming device announcement (from ESP32)
    incoming = {
        "id": "esp32-traffic-01",
        "ip": "192.168.1.150",
        "hardware": "ESP32-WROOM32",
        "memory_total": 520000,
        "cpu_freq_mhz": 240,
        "connection_type": "wifi"
    }

    print(f"\n1. Device announces: {incoming['id']}")

    # Step 1: Classify device
    print("\n2. PnP-AI Classification...")
    classification = classifier.classify(incoming)
    print(f"   Type: {classification.device_type}")
    print(f"   Confidence: {classification.confidence:.0%}")
    print(f"   Role: {classification.suggested_role}")
    print(f"   Capabilities: {classification.suggested_capabilities}")

    # Step 2: Semantic validation and registration
    print("\n3. Semantic Registration...")
    result = semantic_manager.register_device(incoming)
    print(f"   Status: {'OK' if result['success'] else 'FAIL'}")
    print(f"   Message: {result.get('message', result.get('error'))}")

    # Step 3: Log to BlockDAG
    print("\n4. BlockDAG Event Logging...")
    reg_event = dag.add_event("device_register", {
        "device_id": incoming['id'],
        "hardware": incoming['hardware'],
        "ip": incoming['ip']
    })
    print(f"   Event ID: {reg_event.node_id}")
    print(f"   Hash: {reg_event.hash}")

    class_event = dag.add_event("device_classified", {
        "device_id": incoming['id'],
        "type": classification.device_type,
        "confidence": classification.confidence,
        "role": classification.suggested_role
    })
    print(f"   Classification Event: {class_event.node_id}")

    # Final status
    print("\n5. Final Device Status:")
    device = semantic_manager.get_device(incoming['id'])
    if device:
        print(f"   ID: {device['device_id']}")
        print(f"   Type: {device['type']}")
        print(f"   State: {device.get('state', 'unknown')}")
        print(f"   Registered: {device.get('registered_at', 'unknown')}")

    print("\n" + "-" * 50)
    print("Device onboarding complete!")


def main():
    print("=" * 60)
    print(" IoT-ITS Interoperability System - Full Integration Demo")
    print(" Project: PINV01-24 - FIUNA")
    print("=" * 60)

    # Run all demos
    manager = demo_semantic_layer()
    classifier = demo_pnp_ai()
    dag = demo_blockdag()
    demo_synchronization()
    demo_full_pipeline()

    print_section("SUMMARY")
    print("""
Components Demonstrated:
  1. Semantic Layer - RDF/SHACL device representation and validation
  2. PnP-AI - Automatic device classification with ML
  3. BlockDAG - Non-linear event logging with sync support
  4. Full Pipeline - End-to-end device onboarding

Next Steps:
  - Deploy on real hardware (Raspberry Pi, ESP32)
  - Enable HTTP server for device announcements
  - Connect multiple edge nodes for distributed operation
    """)

    print("=" * 60)
    print(" Demo completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
