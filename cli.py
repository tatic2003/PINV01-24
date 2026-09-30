#!/usr/bin/env python3
"""
IoT-ITS Command Line Interface
Manage devices and interact with the ITS server from the command line.

Usage:
    python cli.py status                     # Server status
    python cli.py devices list               # List all devices
    python cli.py devices register <json>    # Register a device
    python cli.py devices get <id>           # Get device info
    python cli.py devices remove <id>        # Remove a device
    python cli.py classify <json>            # Classify a device
    python cli.py export rdf                 # Export as RDF

Project: PINV01-24 - FIUNA
"""

import argparse
import json
import sys
import requests
from typing import Optional


class ITSClient:
    """Simple HTTP client for ITS server."""

    def __init__(self, host: str = "localhost", port: int = 5000):
        self.base_url = f"http://{host}:{port}"

    def _request(self, method: str, endpoint: str, data: dict = None) -> dict:
        """Make HTTP request to server."""
        url = f"{self.base_url}{endpoint}"
        try:
            if method == "GET":
                response = requests.get(url, timeout=10)
            elif method == "POST":
                response = requests.post(url, json=data, timeout=10)
            elif method == "PUT":
                response = requests.put(url, json=data, timeout=10)
            elif method == "DELETE":
                response = requests.delete(url, timeout=10)
            else:
                raise ValueError(f"Unknown method: {method}")

            return {
                "success": response.status_code in [200, 201],
                "status_code": response.status_code,
                "data": response.json() if response.text else {}
            }
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection failed. Is the server running?"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def health(self) -> dict:
        return self._request("GET", "/health")

    def status(self) -> dict:
        return self._request("GET", "/api/status")

    def list_devices(self) -> dict:
        return self._request("GET", "/api/devices")

    def get_device(self, device_id: str) -> dict:
        return self._request("GET", f"/api/devices/{device_id}")

    def register_device(self, device_data: dict) -> dict:
        return self._request("POST", "/api/devices/register", device_data)

    def remove_device(self, device_id: str) -> dict:
        return self._request("DELETE", f"/api/devices/{device_id}")

    def update_state(self, device_id: str, state: str) -> dict:
        return self._request("PUT", f"/api/devices/{device_id}/state", {"state": state})

    def classify(self, device_data: dict) -> dict:
        return self._request("POST", "/api/classify", device_data)

    def export_rdf(self, format: str = "turtle") -> dict:
        response = requests.get(f"{self.base_url}/api/semantic/export?format={format}")
        return {"success": True, "data": response.text}

    def validate(self) -> dict:
        return self._request("GET", "/api/semantic/validate")


def print_json(data: dict, indent: int = 2):
    """Pretty print JSON data."""
    print(json.dumps(data, indent=indent))


def cmd_status(client: ITSClient, args):
    """Show server status."""
    result = client.status()
    if result["success"]:
        print_json(result["data"])
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")
        sys.exit(1)


def cmd_health(client: ITSClient, args):
    """Check server health."""
    result = client.health()
    if result["success"]:
        print("Server is healthy")
        print_json(result["data"])
    else:
        print(f"Server is unhealthy: {result.get('error', 'Unknown error')}")
        sys.exit(1)


def cmd_devices_list(client: ITSClient, args):
    """List all devices."""
    result = client.list_devices()
    if result["success"]:
        devices = result["data"].get("devices", [])
        if not devices:
            print("No devices registered")
        else:
            print(f"Found {len(devices)} device(s):\n")
            for device in devices:
                state = device.get("state", "unknown")
                state_icon = "●" if state == "active" else "○"
                print(f"  {state_icon} {device['device_id']}")
                print(f"    Type: {device.get('type', 'Unknown')}")
                print(f"    Hardware: {device.get('hardware_type', 'Unknown')}")
                print(f"    IP: {device.get('ip_address', 'Unknown')}")
                print(f"    State: {state}")
                print()
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")
        sys.exit(1)


def cmd_devices_get(client: ITSClient, args):
    """Get device information."""
    result = client.get_device(args.device_id)
    if result["success"]:
        print_json(result["data"])
    else:
        print(f"Error: Device not found or {result.get('error', 'Unknown error')}")
        sys.exit(1)


def cmd_devices_register(client: ITSClient, args):
    """Register a new device."""
    try:
        device_data = json.loads(args.json_data)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}")
        sys.exit(1)

    result = client.register_device(device_data)
    if result["success"]:
        print("Device registered successfully!")
        print_json(result["data"])
    else:
        print(f"Registration failed: {result.get('error', 'Unknown error')}")
        if "data" in result:
            print_json(result["data"])
        sys.exit(1)


def cmd_devices_remove(client: ITSClient, args):
    """Remove a device."""
    result = client.remove_device(args.device_id)
    if result["success"]:
        print(f"Device {args.device_id} removed successfully")
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")
        sys.exit(1)


def cmd_devices_state(client: ITSClient, args):
    """Update device state."""
    result = client.update_state(args.device_id, args.state)
    if result["success"]:
        print(f"State updated to: {args.state}")
        print_json(result["data"])
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")
        sys.exit(1)


def cmd_classify(client: ITSClient, args):
    """Classify a device."""
    try:
        device_data = json.loads(args.json_data)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}")
        sys.exit(1)

    result = client.classify(device_data)
    if result["success"]:
        data = result["data"]
        print(f"Classification Result:")
        print(f"  Device Type: {data.get('device_type', 'Unknown')}")
        print(f"  Confidence:  {data.get('confidence', 0) * 100:.1f}%")
        print(f"  Suggested Role: {data.get('suggested_role', 'Unknown')}")
        print(f"  Capabilities: {', '.join(data.get('suggested_capabilities', []))}")
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")
        sys.exit(1)


def cmd_export(client: ITSClient, args):
    """Export data."""
    if args.format == "rdf" or args.format == "turtle":
        result = client.export_rdf("turtle")
        print(result["data"])
    elif args.format == "json":
        result = client.list_devices()
        print_json(result["data"])
    else:
        print(f"Unknown format: {args.format}")
        sys.exit(1)


def cmd_validate(client: ITSClient, args):
    """Run SHACL validation."""
    result = client.validate()
    if result["success"]:
        data = result["data"]
        print(f"Validation Result:")
        print(f"  Conforms: {data.get('conforms', False)}")
        print(f"  Violations: {len(data.get('violations', []))}")
        print(f"  Warnings: {len(data.get('warnings', []))}")

        if data.get('violations'):
            print("\nViolations:")
            for v in data['violations']:
                print(f"  - {v.get('message', v)}")
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="IoT-ITS Command Line Interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py status
  python cli.py devices list
  python cli.py devices register '{"id":"esp32-01","ip":"192.168.1.100","hardware":"ESP32"}'
  python cli.py devices get esp32-01
  python cli.py classify '{"hardware_type":"ESP32-WROOM32","memory_total":520000}'
  python cli.py export rdf
        """
    )

    parser.add_argument("--host", default="localhost", help="Server host")
    parser.add_argument("--port", type=int, default=5000, help="Server port")

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Status command
    subparsers.add_parser("status", help="Show server status")

    # Health command
    subparsers.add_parser("health", help="Check server health")

    # Devices commands
    devices_parser = subparsers.add_parser("devices", help="Device management")
    devices_sub = devices_parser.add_subparsers(dest="devices_command")

    devices_sub.add_parser("list", help="List all devices")

    get_parser = devices_sub.add_parser("get", help="Get device info")
    get_parser.add_argument("device_id", help="Device ID")

    register_parser = devices_sub.add_parser("register", help="Register a device")
    register_parser.add_argument("json_data", help="Device data as JSON")

    remove_parser = devices_sub.add_parser("remove", help="Remove a device")
    remove_parser.add_argument("device_id", help="Device ID")

    state_parser = devices_sub.add_parser("state", help="Update device state")
    state_parser.add_argument("device_id", help="Device ID")
    state_parser.add_argument("state", choices=["active", "inactive", "configuring", "error"])

    # Classify command
    classify_parser = subparsers.add_parser("classify", help="Classify a device")
    classify_parser.add_argument("json_data", help="Device data as JSON")

    # Export command
    export_parser = subparsers.add_parser("export", help="Export data")
    export_parser.add_argument("format", choices=["json", "rdf", "turtle"], help="Export format")

    # Validate command
    subparsers.add_parser("validate", help="Run SHACL validation")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Create client
    client = ITSClient(args.host, args.port)

    # Execute command
    if args.command == "status":
        cmd_status(client, args)
    elif args.command == "health":
        cmd_health(client, args)
    elif args.command == "devices":
        if args.devices_command == "list":
            cmd_devices_list(client, args)
        elif args.devices_command == "get":
            cmd_devices_get(client, args)
        elif args.devices_command == "register":
            cmd_devices_register(client, args)
        elif args.devices_command == "remove":
            cmd_devices_remove(client, args)
        elif args.devices_command == "state":
            cmd_devices_state(client, args)
        else:
            devices_parser.print_help()
    elif args.command == "classify":
        cmd_classify(client, args)
    elif args.command == "export":
        cmd_export(client, args)
    elif args.command == "validate":
        cmd_validate(client, args)


if __name__ == "__main__":
    main()
