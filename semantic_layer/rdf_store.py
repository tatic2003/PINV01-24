"""
RDF Store - Lightweight RDF graph management for edge devices
Designed for low memory footprint and efficient querying.

Project: PINV01-24 - FIUNA
"""

from decimal import Decimal
from rdflib import Graph, Namespace, Literal, URIRef, RDF, RDFS, OWL, XSD
from rdflib.namespace import NamespaceManager
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any
from .owlxml import load_owlxml
import json


# Define namespaces
IOT = Namespace("http://pnp-its.fiuna.edu.py/ontology/iot#")
ITS = Namespace("http://edsplab.us.es/ITSresearch/ITS#")
DC = Namespace("http://purl.org/dc/elements/1.1/")


class RDFStore:
    """
    Lightweight RDF store for IoT device management.
    Optimized for edge computing environments with limited resources.
    """

    def __init__(self, load_ontology: bool = True):
        """
        Initialize the RDF store.

        Args:
            load_ontology: Whether to load the IoT ontology on init
        """
        self.graph = Graph()
        self._bind_namespaces()

        if load_ontology:
            self._load_base_ontology()

    def _bind_namespaces(self):
        """Bind common namespaces for easier querying."""
        self.graph.bind("iot", IOT)
        self.graph.bind("its", ITS)
        self.graph.bind("dc", DC)
        self.graph.bind("xsd", XSD)

    def _load_base_ontology(self):
        """Load the IoT-PnP ontology if available."""
        ontology_path = Path(__file__).parent.parent / "ontology" / "extensions" / "iot_pnp.owl"
        if ontology_path.exists():
            try:
                load_owlxml(ontology_path, self.graph)
                # self.graph.parse(str(ontology_path), format="xml")
            except Exception as e:
                print(f"Warning: Could not load ontology: {e}")

    def register_device(
        self,
        device_data: Dict[str, Any],
        device_type: Optional[str] = None
    ) -> URIRef:
        """
        Register a new IoT device in the RDF graph.

        Args:
            device_data: Dictionary with device information
                Required: device_id, ip_address, hardware_type
                Optional: mac_address, firmware_version, location, etc.
            device_type: Ontology class name decided by the PnP-AI classifier,
                for example 'SingleBoardComputer'. When given, it is what types
                the device. When omitted, the hardware-name rules in
                _determine_device_class are used as a fallback.

                Passing it matters: those rules recognise only a handful of
                model-name substrings, so any board whose name they do not know
                lands on the generic IoTDevice class, no matter what the
                classifier decided.

        Returns:
            URIRef: The URI of the registered device
        """
        device_id = device_data.get('device_id') or device_data.get('id')
        if not device_id:
            raise ValueError("device_id is required")

        # Create device URI
        device_uri = IOT[f"device/{device_id}"]

        # No default: a missing hardware type stays missing, so that the
        # IoTDeviceShape (minCount 1) reports it instead of it conforming as
        # 'Unknown'.
        hardware = device_data.get('hardware_type') or device_data.get('hardware') or ''

        # Prefer the classifier's decision; fall back to the name rules.
        device_class = IOT[device_type] if device_type else self._determine_device_class(hardware)

        # Add type triple
        self.graph.add((device_uri, RDF.type, device_class))

        # Add required properties
        self.graph.add((device_uri, IOT.deviceId, Literal(device_id, datatype=XSD.string)))

        ip = device_data.get('ip_address') or device_data.get('ip')
        if ip:
            self.graph.add((device_uri, IOT.ipAddress, Literal(ip, datatype=XSD.string)))

        if hardware:
            self.graph.add((device_uri, IOT.hardwareType, Literal(hardware, datatype=XSD.string)))

        # Add optional properties
        if 'mac_address' in device_data:
            self.graph.add((device_uri, IOT.macAddress,
                           Literal(device_data['mac_address'], datatype=XSD.string)))

        if 'firmware_version' in device_data:
            self.graph.add((device_uri, IOT.firmwareVersion,
                           Literal(device_data['firmware_version'], datatype=XSD.string)))

        if 'location' in device_data:
            self.graph.add((device_uri, IOT.location,
                           Literal(device_data['location'], datatype=XSD.string)))

        # Announced memory, so that SHACL can check it. A value that is not a
        # number is kept as a string literal: the shape then reports a datatype
        # violation instead of the store failing.
        for key, prop in (('memory_total', IOT.memoryTotal), ('memory_free', IOT.memoryFree)):
            value = device_data.get(key)
            if value is None:
                continue
            try:
                if isinstance(value, bool):
                    raise TypeError
                literal = Literal(int(float(value)), datatype=XSD.integer)
            except (TypeError, ValueError):
                literal = Literal(str(value), datatype=XSD.string)
            self.graph.add((device_uri, prop, literal))

        # GNSS receivers: typed position/quality literals and a capability
        if device_class == IOT.GNSSReceiver:
            self._add_gnss_properties(device_uri, device_id, device_data)

        # Add timestamps
        now = datetime.utcnow().isoformat()
        self.graph.add((device_uri, IOT.registeredAt, Literal(now, datatype=XSD.dateTime)))
        self.graph.add((device_uri, IOT.lastSeen, Literal(now, datatype=XSD.dateTime)))

        # Add initial state (Active)
        state_uri = IOT[f"state/{device_id}/active"]
        self.graph.add((state_uri, RDF.type, IOT.ActiveState))
        self.graph.add((device_uri, IOT.hasState, state_uri))

        return device_uri

    def _add_gnss_properties(self, device_uri: URIRef, device_id: str,
                             device_data: Dict[str, Any]) -> None:
        """Add GNSS-specific triples with their XSD datatypes."""
        decimals = {'latitude': IOT.latitude, 'longitude': IOT.longitude,
                    'hdop': IOT.hdop, 'update_rate_hz': IOT.updateRateHz}
        integers = {'satellites_used': IOT.satellitesUsed,
                    'fix_quality': IOT.fixQuality}
        for key, prop in decimals.items():
            if device_data.get(key) is not None:
                self.graph.add((device_uri, prop,
                                Literal(Decimal(str(device_data[key])), datatype=XSD.decimal)))
        for key, prop in integers.items():
            if device_data.get(key) is not None:
                self.graph.add((device_uri, prop,
                                Literal(int(device_data[key]), datatype=XSD.integer)))
        cap_uri = IOT[f"capability/{device_id}/gnss"]
        self.graph.add((cap_uri, RDF.type, IOT.GNSSCapability))
        self.graph.add((device_uri, IOT.hasCapability, cap_uri))

    def _determine_device_class(self, hardware_type: str) -> URIRef:
        """Determine the appropriate OWL class based on hardware type."""
        hardware_lower = hardware_type.lower()

        if 'gps' in hardware_lower or 'gnss' in hardware_lower:
            return IOT.GNSSReceiver
        elif 'esp32' in hardware_lower:
            return IOT.ESP32Device
        elif 'raspberry' in hardware_lower or 'rpi' in hardware_lower:
            return IOT.SingleBoardComputer
        elif 'jetson' in hardware_lower:
            return IOT.SingleBoardComputer
        elif 'gateway' in hardware_lower:
            return IOT.IoTGateway
        elif 'sensor' in hardware_lower:
            return IOT.Sensor
        elif 'actuator' in hardware_lower:
            return IOT.Actuator
        else:
            return IOT.IoTDevice

    def update_device_state(self, device_id: str, new_state: str) -> bool:
        """
        Update the state of a device.

        Args:
            device_id: The device identifier
            new_state: One of 'active', 'inactive', 'configuring', 'error'

        Returns:
            bool: True if successful
        """
        device_uri = IOT[f"device/{device_id}"]

        # Check if device exists
        if (device_uri, RDF.type, None) not in self.graph:
            return False

        # Remove old state
        for state in self.graph.objects(device_uri, IOT.hasState):
            self.graph.remove((device_uri, IOT.hasState, state))
            # Remove state instance
            for triple in self.graph.triples((state, None, None)):
                self.graph.remove(triple)

        # Add new state
        state_classes = {
            'active': IOT.ActiveState,
            'inactive': IOT.InactiveState,
            'configuring': IOT.ConfiguringState,
            'error': IOT.ErrorState
        }

        state_class = state_classes.get(new_state.lower(), IOT.ActiveState)
        state_uri = IOT[f"state/{device_id}/{new_state.lower()}"]

        self.graph.add((state_uri, RDF.type, state_class))
        self.graph.add((device_uri, IOT.hasState, state_uri))

        # Update last seen
        now = datetime.utcnow().isoformat()
        self.graph.set((device_uri, IOT.lastSeen, Literal(now, datatype=XSD.dateTime)))

        return True

    def update_last_seen(self, device_id: str) -> bool:
        """Update the lastSeen timestamp for a device."""
        device_uri = IOT[f"device/{device_id}"]

        if (device_uri, RDF.type, None) not in self.graph:
            return False

        now = datetime.utcnow().isoformat()
        self.graph.set((device_uri, IOT.lastSeen, Literal(now, datatype=XSD.dateTime)))
        return True

    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Get device information as a dictionary.

        Args:
            device_id: The device identifier

        Returns:
            Dictionary with device data or None if not found
        """
        device_uri = IOT[f"device/{device_id}"]

        # Check if device exists
        device_types = list(self.graph.objects(device_uri, RDF.type))
        if not device_types:
            return None

        device = {
            'device_id': device_id,
            'uri': str(device_uri),
            'type': str(device_types[0]).split('#')[-1] if device_types else 'Unknown'
        }

        # Get all properties
        property_map = {
            IOT.ipAddress: 'ip_address',
            IOT.hardwareType: 'hardware_type',
            IOT.macAddress: 'mac_address',
            IOT.firmwareVersion: 'firmware_version',
            IOT.location: 'location',
            IOT.lastSeen: 'last_seen',
            IOT.registeredAt: 'registered_at',
            IOT.memoryTotal: 'memory_total',
            IOT.memoryFree: 'memory_free',
            IOT.cpuUsage: 'cpu_usage',
            IOT.latitude: 'latitude',
            IOT.longitude: 'longitude',
            IOT.hdop: 'hdop',
            IOT.satellitesUsed: 'satellites_used',
            IOT.fixQuality: 'fix_quality',
            IOT.updateRateHz: 'update_rate_hz'
        }

        for rdf_prop, dict_key in property_map.items():
            values = list(self.graph.objects(device_uri, rdf_prop))
            if values:
                device[dict_key] = str(values[0])

        # Get state
        states = list(self.graph.objects(device_uri, IOT.hasState))
        if states:
            state_types = list(self.graph.objects(states[0], RDF.type))
            if state_types:
                state_name = str(state_types[0]).split('#')[-1].replace('State', '').lower()
                device['state'] = state_name

        return device

    def get_all_devices(self) -> List[Dict[str, Any]]:
        """Get all registered devices."""
        devices = []

        # Query for all IoTDevice instances
        query = """
        PREFIX iot: <http://pnp-its.fiuna.edu.py/ontology/iot#>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

        SELECT DISTINCT ?device ?deviceId WHERE {
            ?device rdf:type ?type .
            ?device iot:deviceId ?deviceId .
            FILTER(STRSTARTS(STR(?type), STR(iot:)))
        }
        """

        try:
            results = self.graph.query(query)
            for row in results:
                device_id = str(row.deviceId)
                device_data = self.get_device(device_id)
                if device_data:
                    devices.append(device_data)
        except Exception as e:
            # Fallback: iterate through deviceId properties
            for device_id_literal in self.graph.objects(None, IOT.deviceId):
                device_data = self.get_device(str(device_id_literal))
                if device_data:
                    devices.append(device_data)

        return devices

    def get_devices_by_state(self, state: str) -> List[Dict[str, Any]]:
        """Get all devices with a specific state."""
        state_classes = {
            'active': IOT.ActiveState,
            'inactive': IOT.InactiveState,
            'configuring': IOT.ConfiguringState,
            'error': IOT.ErrorState
        }

        state_class = state_classes.get(state.lower())
        if not state_class:
            return []

        devices = []
        for device in self.get_all_devices():
            if device.get('state', '').lower() == state.lower():
                devices.append(device)

        return devices

    def remove_device(self, device_id: str) -> bool:
        """Remove a device from the graph."""
        device_uri = IOT[f"device/{device_id}"]

        # Check if device exists
        if (device_uri, RDF.type, None) not in self.graph:
            return False

        # Remove all triples with device as subject
        for triple in list(self.graph.triples((device_uri, None, None))):
            self.graph.remove(triple)

        # Remove state instances
        state_uri_prefix = f"state/{device_id}/"
        for s, p, o in list(self.graph.triples((None, None, None))):
            if state_uri_prefix in str(s):
                self.graph.remove((s, p, o))

        return True

    def serialize(self, format: str = 'turtle') -> str:
        """Serialize the graph to a string."""
        return self.graph.serialize(format=format)

    def save(self, filepath: str, format: str = 'turtle'):
        """Save the graph to a file."""
        self.graph.serialize(destination=filepath, format=format)

    def load(self, filepath: str, format: str = None):
        """Load a graph from a file."""
        self.graph.parse(filepath, format=format)

    def to_json(self) -> str:
        """Export all devices as JSON (for compatibility with existing systems)."""
        devices = {}
        for device in self.get_all_devices():
            device_id = device.get('device_id')
            if device_id:
                devices[device_id] = {
                    'id': device_id,
                    'ip': device.get('ip_address', ''),
                    'hardware': device.get('hardware_type', ''),
                    'state': device.get('state', 'active'),
                    'last-seen': device.get('last_seen', ''),
                    'registered-at': device.get('registered_at', '')
                }
        return json.dumps(devices, indent=2)

    def __len__(self) -> int:
        """Return the number of triples in the graph."""
        return len(self.graph)
