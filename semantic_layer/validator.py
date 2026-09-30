"""
SHACL Validator - Lightweight semantic validation for IoT devices
Designed to run on edge nodes with limited resources.

Project: PINV01-24 - FIUNA
"""

from rdflib import Graph, Namespace
from pyshacl import validate
from pathlib import Path
from typing import Tuple, List, Dict, Any, Optional
from dataclasses import dataclass


IOT = Namespace("http://pnp-its.fiuna.edu.py/ontology/iot#")


@dataclass
class ValidationResult:
    """Result of SHACL validation."""
    conforms: bool
    violations: List[Dict[str, Any]]
    warnings: List[Dict[str, Any]]
    info: List[Dict[str, Any]]

    @property
    def is_valid(self) -> bool:
        """Check if validation passed (no violations)."""
        return self.conforms or len(self.violations) == 0

    @property
    def has_warnings(self) -> bool:
        """Check if there are any warnings."""
        return len(self.warnings) > 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'conforms': self.conforms,
            'is_valid': self.is_valid,
            'violations': self.violations,
            'warnings': self.warnings,
            'info': self.info,
            'summary': {
                'violations_count': len(self.violations),
                'warnings_count': len(self.warnings),
                'info_count': len(self.info)
            }
        }


class SHACLValidator:
    """
    SHACL Validator for IoT device data.
    Provides lightweight validation suitable for edge computing.
    """

    def __init__(self, shapes_path: Optional[str] = None):
        """
        Initialize the validator.

        Args:
            shapes_path: Path to SHACL shapes file. If None, uses default shapes.
        """
        self.shapes_graph = Graph()
        self._load_shapes(shapes_path)

    def _load_shapes(self, shapes_path: Optional[str] = None):
        """Load SHACL shapes from file."""
        if shapes_path:
            path = Path(shapes_path)
        else:
            path = Path(__file__).parent.parent / "ontology" / "shapes" / "device_shapes.ttl"

        if path.exists():
            self.shapes_graph.parse(str(path), format='turtle')
        else:
            print(f"Warning: SHACL shapes not found at {path}")

    def validate(self, data_graph: Graph, inference: str = 'none') -> ValidationResult:
        """
        Validate an RDF graph against SHACL shapes.

        Args:
            data_graph: The RDF graph to validate
            inference: Inference mode ('none', 'rdfs', 'owlrl')

        Returns:
            ValidationResult with conformance status and any violations
        """
        if len(self.shapes_graph) == 0:
            return ValidationResult(
                conforms=True,
                violations=[],
                warnings=[{"message": "No SHACL shapes loaded - validation skipped"}],
                info=[]
            )

        try:
            conforms, results_graph, results_text = validate(
                data_graph,
                shacl_graph=self.shapes_graph,
                inference=inference,
                abort_on_first=False,
                allow_infos=True,
                allow_warnings=True
            )

            # Parse results
            violations, warnings, info = self._parse_results(results_graph)

            return ValidationResult(
                conforms=conforms,
                violations=violations,
                warnings=warnings,
                info=info
            )

        except Exception as e:
            return ValidationResult(
                conforms=False,
                violations=[{"message": f"Validation error: {str(e)}"}],
                warnings=[],
                info=[]
            )

    def _parse_results(self, results_graph: Graph) -> Tuple[List, List, List]:
        """Parse SHACL validation results graph into categorized lists."""
        violations = []
        warnings = []
        info = []

        SH = Namespace("http://www.w3.org/ns/shacl#")

        for result in results_graph.subjects(None, SH.ValidationResult):
            result_dict = {}

            # Get focus node
            for focus in results_graph.objects(result, SH.focusNode):
                result_dict['focus_node'] = str(focus)

            # Get result path
            for path in results_graph.objects(result, SH.resultPath):
                result_dict['path'] = str(path).split('#')[-1]

            # Get message
            for msg in results_graph.objects(result, SH.resultMessage):
                result_dict['message'] = str(msg)

            # Get value
            for val in results_graph.objects(result, SH.value):
                result_dict['value'] = str(val)

            # Get source shape
            for shape in results_graph.objects(result, SH.sourceShape):
                result_dict['source_shape'] = str(shape).split('#')[-1]

            # Get severity and categorize
            for severity in results_graph.objects(result, SH.resultSeverity):
                severity_str = str(severity)
                if 'Violation' in severity_str:
                    violations.append(result_dict)
                elif 'Warning' in severity_str:
                    warnings.append(result_dict)
                else:
                    info.append(result_dict)

        return violations, warnings, info

    def validate_device_registration(self, device_data: Dict[str, Any]) -> ValidationResult:
        """
        Quick validation of device registration data before adding to graph.
        This is a lightweight check that doesn't require full SHACL validation.

        Args:
            device_data: Dictionary with device information

        Returns:
            ValidationResult
        """
        violations = []
        warnings = []
        info = []

        # Required fields
        required = ['device_id', 'ip_address', 'hardware_type']
        # Also accept alternative names
        alt_names = {
            'device_id': ['id', 'deviceId'],
            'ip_address': ['ip', 'ipAddress'],
            'hardware_type': ['hardware', 'hardwareType']
        }

        for field in required:
            value = device_data.get(field)
            if not value:
                # Check alternative names
                for alt in alt_names.get(field, []):
                    value = device_data.get(alt)
                    if value:
                        break

            if not value:
                violations.append({
                    'path': field,
                    'message': f"Required field '{field}' is missing"
                })

        # Validate IP address format
        ip = device_data.get('ip_address') or device_data.get('ip')
        if ip:
            import re
            ip_pattern = r'^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
            if not re.match(ip_pattern, ip):
                violations.append({
                    'path': 'ip_address',
                    'value': ip,
                    'message': 'Invalid IP address format'
                })

        # Validate device_id format
        device_id = device_data.get('device_id') or device_data.get('id')
        if device_id:
            import re
            id_pattern = r'^[a-zA-Z0-9_-]+$'
            if not re.match(id_pattern, device_id):
                warnings.append({
                    'path': 'device_id',
                    'value': device_id,
                    'message': 'Device ID should only contain alphanumeric characters, hyphens, and underscores'
                })

            # Check naming convention for ESP32
            hardware = device_data.get('hardware_type') or device_data.get('hardware', '')
            if 'esp32' in hardware.lower() and not device_id.lower().startswith('esp32'):
                info.append({
                    'path': 'device_id',
                    'value': device_id,
                    'message': "ESP32 devices conventionally use IDs starting with 'esp32-'"
                })

        # Validate MAC address if present
        mac = device_data.get('mac_address')
        if mac:
            import re
            mac_pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
            if not re.match(mac_pattern, mac):
                warnings.append({
                    'path': 'mac_address',
                    'value': mac,
                    'message': 'Invalid MAC address format (expected XX:XX:XX:XX:XX:XX)'
                })

        conforms = len(violations) == 0
        return ValidationResult(
            conforms=conforms,
            violations=violations,
            warnings=warnings,
            info=info
        )

    def validate_state_transition(self, current_state: str, new_state: str) -> ValidationResult:
        """
        Validate a device state transition.

        Args:
            current_state: Current device state
            new_state: Proposed new state

        Returns:
            ValidationResult
        """
        valid_states = {'active', 'inactive', 'configuring', 'error'}
        violations = []
        warnings = []

        if new_state.lower() not in valid_states:
            violations.append({
                'path': 'state',
                'value': new_state,
                'message': f"Invalid state '{new_state}'. Valid states: {valid_states}"
            })

        # Define valid transitions
        valid_transitions = {
            'active': {'inactive', 'configuring', 'error'},
            'inactive': {'active', 'configuring'},
            'configuring': {'active', 'error', 'inactive'},
            'error': {'inactive', 'configuring'}
        }

        current = current_state.lower()
        new = new_state.lower()

        if current in valid_transitions:
            if new not in valid_transitions[current] and new != current:
                warnings.append({
                    'path': 'state',
                    'message': f"Unusual state transition from '{current}' to '{new}'"
                })

        return ValidationResult(
            conforms=len(violations) == 0,
            violations=violations,
            warnings=warnings,
            info=[]
        )
