"""
Semantic Device Manager - High-level API for IoT device management
Combines RDF store and SHACL validation for a complete device management solution.

Project: PINV01-24 - FIUNA
"""

from typing import Optional, Dict, List, Any
from datetime import datetime
import json

from .rdf_store import RDFStore
from .validator import SHACLValidator, ValidationResult


class SemanticDeviceManager:
    """
    High-level manager for IoT devices with semantic capabilities.
    Provides a simple API for device registration, state management,
    and querying with automatic validation.
    """

    def __init__(self, validate_on_register: bool = True):
        """
        Initialize the device manager.

        Args:
            validate_on_register: Whether to validate device data before registration
        """
        self.store = RDFStore(load_ontology=True)
        self.validator = SHACLValidator()
        self.validate_on_register = validate_on_register
        self._callbacks = {
            'on_register': [],
            'on_state_change': [],
            'on_disconnect': []
        }

    def register_device(
        self,
        device_data: Dict[str, Any],
        device_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Register a new IoT device.

        Args:
            device_data: Dictionary with device information
                Required: device_id (or id), ip_address (or ip), hardware_type (or hardware)
                Optional: mac_address, firmware_version, location, etc.
            device_type: Ontology class decided by the PnP-AI classifier. When
                given, it types the device in the graph; otherwise the
                hardware-name rules are used as a fallback.

        Returns:
            Dictionary with registration result including validation info
        """
        # Normalize keys
        normalized = self._normalize_device_data(device_data)

        # Validate if enabled
        if self.validate_on_register:
            validation = self.validator.validate_device_registration(normalized)
            if not validation.is_valid:
                return {
                    'success': False,
                    'error': 'Validation failed',
                    'validation': validation.to_dict()
                }

        # Check if device already exists
        device_id = normalized['device_id']
        existing = self.store.get_device(device_id)
        if existing:
            # Update existing device
            self.store.update_last_seen(device_id)
            self.store.update_device_state(device_id, 'active')
            return {
                'success': True,
                'action': 'updated',
                'device_id': device_id,
                'message': 'Device already registered, updated last_seen'
            }

        # Register new device
        try:
            device_uri = self.store.register_device(normalized, device_type=device_type)

            # Trigger callbacks
            self._trigger_callback('on_register', normalized)

            return {
                'success': True,
                'action': 'registered',
                'device_id': device_id,
                'device_uri': str(device_uri),
                'message': 'Device registered successfully'
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

    def _normalize_device_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize device data keys to standard format."""
        normalized = {}

        # Map alternative keys to standard keys
        key_map = {
            'id': 'device_id',
            'deviceId': 'device_id',
            'device_id': 'device_id',
            'ip': 'ip_address',
            'ipAddress': 'ip_address',
            'ip_address': 'ip_address',
            'hardware': 'hardware_type',
            'hardwareType': 'hardware_type',
            'hardware_type': 'hardware_type',
            'mac': 'mac_address',
            'macAddress': 'mac_address',
            'mac_address': 'mac_address',
            'firmware': 'firmware_version',
            'firmwareVersion': 'firmware_version',
            'firmware_version': 'firmware_version',
            # GNSS receivers
            'lat': 'latitude',
            'lon': 'longitude',
            'lng': 'longitude',
            'satellites': 'satellites_used',
            'satellitesUsed': 'satellites_used',
            'sats': 'satellites_used',
            'fixQuality': 'fix_quality',
            'fix': 'fix_quality',
            'updateRateHz': 'update_rate_hz'
        }

        for key, value in data.items():
            normalized_key = key_map.get(key, key)
            # Don't overwrite if already set
            if normalized_key not in normalized:
                normalized[normalized_key] = value

        return normalized

    def update_state(self, device_id: str, new_state: str) -> Dict[str, Any]:
        """
        Update device state with validation.

        Args:
            device_id: Device identifier
            new_state: New state ('active', 'inactive', 'configuring', 'error')

        Returns:
            Dictionary with update result
        """
        # Get current device
        device = self.store.get_device(device_id)
        if not device:
            return {
                'success': False,
                'error': f'Device {device_id} not found'
            }

        # Validate state transition
        current_state = device.get('state', 'active')
        validation = self.validator.validate_state_transition(current_state, new_state)

        if not validation.is_valid:
            return {
                'success': False,
                'error': 'Invalid state transition',
                'validation': validation.to_dict()
            }

        # Update state
        success = self.store.update_device_state(device_id, new_state)

        if success:
            self._trigger_callback('on_state_change', {
                'device_id': device_id,
                'old_state': current_state,
                'new_state': new_state
            })

            if new_state.lower() == 'inactive':
                self._trigger_callback('on_disconnect', {'device_id': device_id})

        return {
            'success': success,
            'device_id': device_id,
            'old_state': current_state,
            'new_state': new_state,
            'warnings': validation.warnings if validation.has_warnings else []
        }

    def heartbeat(self, device_id: str) -> Dict[str, Any]:
        """
        Process a device heartbeat (update last_seen, ensure active state).

        Args:
            device_id: Device identifier

        Returns:
            Dictionary with heartbeat result
        """
        device = self.store.get_device(device_id)
        if not device:
            return {
                'success': False,
                'error': f'Device {device_id} not found'
            }

        self.store.update_last_seen(device_id)

        # Ensure device is active
        if device.get('state') != 'active':
            self.store.update_device_state(device_id, 'active')

        return {
            'success': True,
            'device_id': device_id,
            'timestamp': datetime.utcnow().isoformat()
        }

    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get device information."""
        return self.store.get_device(device_id)

    def get_all_devices(self) -> List[Dict[str, Any]]:
        """Get all registered devices."""
        return self.store.get_all_devices()

    def get_active_devices(self) -> List[Dict[str, Any]]:
        """Get all active devices."""
        return self.store.get_devices_by_state('active')

    def get_inactive_devices(self) -> List[Dict[str, Any]]:
        """Get all inactive devices."""
        return self.store.get_devices_by_state('inactive')

    def remove_device(self, device_id: str) -> bool:
        """Remove a device from the registry."""
        return self.store.remove_device(device_id)

    def validate_graph(self) -> ValidationResult:
        """Run full SHACL validation on the device graph."""
        return self.validator.validate(self.store.graph)

    def export_json(self) -> str:
        """Export devices as JSON (compatible with existing Flask server format)."""
        return self.store.to_json()

    def export_rdf(self, format: str = 'turtle') -> str:
        """Export devices as RDF in specified format."""
        return self.store.serialize(format=format)

    def save(self, filepath: str, format: str = 'turtle'):
        """Save the device graph to a file."""
        self.store.save(filepath, format)

    def load(self, filepath: str, format: str = None):
        """Load a device graph from a file."""
        self.store.load(filepath, format)

    def on(self, event: str, callback):
        """
        Register a callback for an event.

        Events:
            - on_register: Called when a new device is registered
            - on_state_change: Called when device state changes
            - on_disconnect: Called when device becomes inactive
        """
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _trigger_callback(self, event: str, data: Dict[str, Any]):
        """Trigger callbacks for an event."""
        for callback in self._callbacks.get(event, []):
            try:
                callback(data)
            except Exception as e:
                print(f"Callback error for {event}: {e}")

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about registered devices."""
        all_devices = self.get_all_devices()

        stats = {
            'total_devices': len(all_devices),
            'active_devices': len([d for d in all_devices if d.get('state') == 'active']),
            'inactive_devices': len([d for d in all_devices if d.get('state') == 'inactive']),
            'devices_by_type': {},
            'devices_by_hardware': {}
        }

        for device in all_devices:
            # Count by type
            device_type = device.get('type', 'Unknown')
            stats['devices_by_type'][device_type] = stats['devices_by_type'].get(device_type, 0) + 1

            # Count by hardware
            hardware = device.get('hardware_type', 'Unknown')
            stats['devices_by_hardware'][hardware] = stats['devices_by_hardware'].get(hardware, 0) + 1

        return stats


# Convenience function for quick setup
def create_manager(validate: bool = True) -> SemanticDeviceManager:
    """Create a configured SemanticDeviceManager instance."""
    return SemanticDeviceManager(validate_on_register=validate)
