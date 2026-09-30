"""
Basic Usage Example - Semantic Device Manager
Demonstrates how to use the semantic layer for IoT device management.

Project: PINV01-24 - FIUNA
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from semantic_layer import SemanticDeviceManager


def main():
    print("=" * 60)
    print("IoT-ITS Semantic Device Manager - Basic Usage Example")
    print("=" * 60)

    # Create device manager
    print("\n1. Creating SemanticDeviceManager...")
    manager = SemanticDeviceManager(validate_on_register=True)
    print("   Manager created successfully")

    # Register devices (simulating ESP32 announcements)
    print("\n2. Registering devices...")

    devices = [
        {
            "id": "esp32-wifi-01",
            "ip": "192.168.1.101",
            "hardware": "ESP32-WROOM32"
        },
        {
            "id": "esp32-olimex-01",
            "ip": "192.168.1.102",
            "hardware": "ESP32-OLIMEX-PoE"
        },
        {
            "id": "esp32-nano-01",
            "ip": "192.168.1.103",
            "hardware": "NANO-ESP32"
        },
        {
            "id": "rpi-gateway-01",
            "ip": "192.168.1.1",
            "hardware": "RaspberryPi4",
            "location": "Lab-Central"
        }
    ]

    for device in devices:
        result = manager.register_device(device)
        status = "✓" if result['success'] else "✗"
        print(f"   {status} {device['id']}: {result.get('message', result.get('error', ''))}")

    # Get all devices
    print("\n3. Listing all devices...")
    all_devices = manager.get_all_devices()
    for device in all_devices:
        print(f"   - {device['device_id']} ({device.get('type', 'Unknown')}) - {device.get('state', 'unknown')}")

    # Simulate state changes
    print("\n4. Simulating state changes...")

    # Device goes inactive
    result = manager.update_state("esp32-nano-01", "inactive")
    print(f"   esp32-nano-01 -> inactive: {'✓' if result['success'] else '✗'}")

    # Device in configuring mode
    result = manager.update_state("esp32-wifi-01", "configuring")
    print(f"   esp32-wifi-01 -> configuring: {'✓' if result['success'] else '✗'}")

    # Device back to active
    result = manager.update_state("esp32-wifi-01", "active")
    print(f"   esp32-wifi-01 -> active: {'✓' if result['success'] else '✗'}")

    # Get statistics
    print("\n5. Device statistics...")
    stats = manager.get_statistics()
    print(f"   Total devices: {stats['total_devices']}")
    print(f"   Active: {stats['active_devices']}")
    print(f"   Inactive: {stats['inactive_devices']}")
    print(f"   By hardware: {stats['devices_by_hardware']}")

    # Export as JSON (compatible with Flask server)
    print("\n6. JSON export (Flask-compatible)...")
    json_export = manager.export_json()
    print(json_export[:500] + "..." if len(json_export) > 500 else json_export)

    # Export as RDF (Turtle format)
    print("\n7. RDF export (Turtle format - first 1000 chars)...")
    rdf_export = manager.export_rdf(format='turtle')
    print(rdf_export[:1000] + "..." if len(rdf_export) > 1000 else rdf_export)

    # Validate entire graph
    print("\n8. Running SHACL validation...")
    validation = manager.validate_graph()
    print(f"   Conforms: {validation.conforms}")
    print(f"   Violations: {len(validation.violations)}")
    print(f"   Warnings: {len(validation.warnings)}")
    print(f"   Info: {len(validation.info)}")

    print("\n" + "=" * 60)
    print("Example completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
