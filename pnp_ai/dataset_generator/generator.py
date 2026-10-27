"""
Synthetic Dataset Generator for PnP-AI
Generates realistic IoT device data for training classification models.

Based on the ITS ontology and real device characteristics from PINV01-24 tests.

Project: PINV01-24 - FIUNA
"""

import random
import json
import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict, field
import hashlib

from ..capability_rules import GNSS_RULE_CLASS
from ..features import FEATURE_SET_RESOURCE_NAME, extract_features


@dataclass
class DeviceProfile:
    """Profile defining device characteristics for generation."""
    device_type: str
    hardware_variants: List[str]
    memory_range: Tuple[int, int]  # bytes
    cpu_freq_range: Tuple[int, int]  # MHz
    connection_types: List[str]
    typical_roles: List[str]
    id_prefix: str
    manufacturer: str
    #: Capabilities the device declares in its announcement (empty = none).
    capabilities: List[str] = field(default_factory=list)


# Device profiles based on real hardware from PINV01-24 report
DEVICE_PROFILES = {
    'esp32_wroom': DeviceProfile(
        device_type='ESP32Device',
        hardware_variants=['ESP32-WROOM32', 'ESP32-WROOM32D', 'ESP32-WROOM32E'],
        memory_range=(320_000, 520_000),  # 320KB-520KB SRAM
        cpu_freq_range=(80, 240),
        connection_types=['wifi', 'bluetooth'],
        typical_roles=['sensor', 'actuator', 'monitor'],
        id_prefix='esp32-wifi',
        manufacturer='Espressif'
    ),
    'esp32_olimex': DeviceProfile(
        device_type='ESP32Device',
        hardware_variants=['ESP32-OLIMEX-PoE', 'ESP32-OLIMEX-PoE-ISO'],
        memory_range=(320_000, 520_000),
        cpu_freq_range=(80, 240),
        connection_types=['ethernet', 'wifi'],
        typical_roles=['gateway', 'sensor', 'controller'],
        id_prefix='esp32-eth',
        manufacturer='Olimex'
    ),
    'esp32_nano': DeviceProfile(
        device_type='ESP32Device',
        hardware_variants=['NANO-ESP32', 'ESP32-S3-NANO'],
        memory_range=(320_000, 512_000),
        cpu_freq_range=(80, 240),
        connection_types=['wifi', 'bluetooth'],
        typical_roles=['sensor', 'actuator'],
        id_prefix='esp32-nano',
        manufacturer='Arduino'
    ),
    'raspberry_pi': DeviceProfile(
        device_type='SingleBoardComputer',
        hardware_variants=['RaspberryPi-2B', 'RaspberryPi-3B', 'RaspberryPi-4B', 'RaspberryPi-5'],
        memory_range=(1_073_741_824, 8_589_934_592),  # 1GB-8GB
        cpu_freq_range=(900, 2400),
        connection_types=['ethernet', 'wifi'],
        typical_roles=['gateway', 'edge_server', 'controller'],
        id_prefix='rpi',
        manufacturer='Raspberry Pi Foundation'
    ),
    'jetson': DeviceProfile(
        device_type='SingleBoardComputer',
        hardware_variants=['Jetson-Nano', 'Jetson-Orin-Nano', 'Jetson-Xavier-NX'],
        memory_range=(4_294_967_296, 16_106_127_360),  # 4GB-15GB
        cpu_freq_range=(1400, 2000),
        connection_types=['ethernet', 'wifi'],
        typical_roles=['edge_ai', 'vision_processor', 'gateway'],
        id_prefix='jetson',
        manufacturer='NVIDIA'
    ),
    # Low-power single-board computers.
    #
    # Added after physical testing: an Orange Pi Zero (484 MB, 1296 MHz) was
    # classified as an ESP32Device with 0.560 confidence, because every
    # profile above jumped straight from 0.5 MB (ESP32) to 1 GB (Raspberry
    # Pi), leaving the whole sub-gigabyte Linux board class unrepresented.
    # That band holds a large part of the real deployed population: Orange Pi
    # Zero, Raspberry Pi Zero, NanoPi Neo, Orange Pi One and similar boards.
    'sbc_low_power': DeviceProfile(
        device_type='SingleBoardComputer',
        hardware_variants=['OrangePi-Zero', 'OrangePi-Zero-LTS', 'OrangePi-One',
                           'RaspberryPi-Zero-W', 'NanoPi-Neo'],
        memory_range=(134_217_728, 1_073_741_824),  # 128MB-1GB
        cpu_freq_range=(600, 1500),
        connection_types=['ethernet', 'wifi'],
        typical_roles=['gateway', 'controller', 'sensor'],
        id_prefix='sbc-low',
        manufacturer='Various'
    ),
    # GNSS receivers (GPS HATs and modules).
    #
    # A receiver has no resources of its own that the network can see: it
    # announces those of its host. Two profiles cover the two usual hosts, a
    # Linux single-board computer and a microcontroller, so that memory and
    # CPU alone cannot separate the class. What does: the capability the
    # device declares. Hardware names deliberately include a host name
    # ('RaspberryPi-5+GPS-HAT') so the name flags do not give the class away.
    'gnss_on_sbc': DeviceProfile(
        device_type='GNSSReceiver',
        hardware_variants=['Adafruit-Ultimate-GPS-HAT', 'RaspberryPi-5+GPS-HAT',
                           'RaspberryPi-4B+GPS-HAT', 'u-blox-NEO-M8N-USB'],
        memory_range=(1_073_741_824, 8_589_934_592),
        cpu_freq_range=(900, 2400),
        connection_types=['ethernet', 'wifi'],
        typical_roles=['sensor'],
        id_prefix='gnss-sbc',
        manufacturer='Various',
        capabilities=['gnss', 'position', 'time']
    ),
    'gnss_on_mcu': DeviceProfile(
        device_type='GNSSReceiver',
        hardware_variants=['ESP32-WROOM32+NEO-6M', 'ESP32-OLIMEX-PoE+L76K',
                           'Quectel-L76K', 'u-blox-NEO-M8N'],
        memory_range=(320_000, 520_000),
        cpu_freq_range=(80, 240),
        connection_types=['wifi', 'ethernet'],
        typical_roles=['sensor'],
        id_prefix='gnss-mcu',
        manufacturer='Various',
        capabilities=['gnss', 'position', 'time']
    ),
    'industrial_gateway': DeviceProfile(
        device_type='IoTGateway',
        hardware_variants=['IIoT-Gateway-ARM', 'IIoT-Gateway-x86', 'Edge-Gateway-Pro'],
        memory_range=(2_147_483_648, 8_589_934_592),  # 2GB-8GB
        cpu_freq_range=(1000, 2500),
        connection_types=['ethernet', 'wifi', 'lora', 'cellular'],
        typical_roles=['gateway', 'protocol_converter', 'data_aggregator'],
        id_prefix='gateway',
        manufacturer='Various'
    ),
    'generic_sensor': DeviceProfile(
        device_type='Sensor',
        hardware_variants=['Temp-Sensor', 'Traffic-Sensor', 'Air-Quality-Sensor', 'Motion-Sensor'],
        memory_range=(32_000, 256_000),
        cpu_freq_range=(8, 80),
        connection_types=['wifi', 'lora', 'zigbee'],
        typical_roles=['sensor'],
        id_prefix='sensor',
        manufacturer='Various'
    )
}

# Sampling weights used when no distribution is given, based on a typical ITS
# deployment. Profiles absent from this table are still sampled, with
# DEFAULT_PROFILE_WEIGHT: adding a profile to DEVICE_PROFILES can never again
# leave it silently out of the generated data.
DEFAULT_PROFILE_WEIGHTS = {
    'esp32_wroom': 0.30,
    'esp32_olimex': 0.15,
    'esp32_nano': 0.15,
    'raspberry_pi': 0.15,
    'sbc_low_power': 0.10,
    'jetson': 0.05,
    'industrial_gateway': 0.10,
    'generic_sensor': 0.10,
    'gnss_on_sbc': 0.05,
    'gnss_on_mcu': 0.05,
}

#: Weight given to a profile that DEFAULT_PROFILE_WEIGHTS does not mention.
DEFAULT_PROFILE_WEIGHT = 0.05


# ITS application contexts
ITS_CONTEXTS = [
    'traffic_monitoring',
    'vehicle_detection',
    'parking_management',
    'emergency_response',
    'public_transit',
    'road_weather',
    'incident_detection',
    'toll_collection',
    'pedestrian_safety',
    'smart_lighting'
]

# Locations for ITS deployment
DEPLOYMENT_LOCATIONS = [
    'intersection_01', 'intersection_02', 'intersection_03',
    'highway_entry_01', 'highway_exit_01',
    'parking_lot_A', 'parking_lot_B',
    'bus_stop_01', 'bus_stop_02',
    'toll_booth_01', 'toll_booth_02',
    'control_center', 'field_cabinet_01',
    'roadside_unit_01', 'roadside_unit_02'
]


class SyntheticDatasetGenerator:
    """
    Generates synthetic datasets for training PnP-AI models.

    Produces realistic IoT device data including:
    - Device characteristics (hardware, memory, connectivity)
    - Network configurations (IP, MAC, protocols)
    - Operational states and patterns
    - ITS-specific context data
    """

    def __init__(self, seed: Optional[int] = None, include_gnss: bool = False):
        """
        Initialize the generator.

        Args:
            seed: Random seed for reproducibility
            include_gnss: keep the GNSS receiver profiles. Off by default: the
                class is decided by the rule of ``pnp_ai.capability_rules``
                before the model runs, so the model is not trained on it.
                Experiments that need receiver announcements ask for them.
        """
        if seed is not None:
            random.seed(seed)

        # Separate stream for the GNSS receiver fields (fix quality, position,
        # satellites, HDOP). Drawing them from the module RNG would shift every
        # draw that follows, so all earlier datasets for a given seed - and the
        # results computed from them - would change. With their own stream, the
        # rest of a dataset is identical to the one generated before these
        # fields existed.
        self._gnss_rng = random.Random(seed)

        self.profiles = DEVICE_PROFILES if include_gnss else {
            name: profile for name, profile in DEVICE_PROFILES.items()
            if profile.device_type != GNSS_RULE_CLASS
        }
        self.contexts = ITS_CONTEXTS
        self.locations = DEPLOYMENT_LOCATIONS
        self._device_counter = {}

    def _generate_mac_address(self) -> str:
        """Generate a random MAC address."""
        mac = [random.randint(0x00, 0xff) for _ in range(6)]
        mac[0] = mac[0] & 0xfe  # Unicast
        return ':'.join(f'{b:02x}' for b in mac).upper()

    def _generate_ip_address(self, subnet: str = '192.168.1') -> str:
        """Generate an IP address in the given subnet."""
        return f'{subnet}.{random.randint(2, 254)}'

    def _generate_device_id(self, profile: DeviceProfile) -> str:
        """Generate a unique device ID based on profile."""
        prefix = profile.id_prefix
        if prefix not in self._device_counter:
            self._device_counter[prefix] = 0
        self._device_counter[prefix] += 1
        return f'{prefix}-{self._device_counter[prefix]:03d}'

    def _generate_firmware_version(self) -> str:
        """Generate a realistic firmware version."""
        major = random.randint(1, 3)
        minor = random.randint(0, 9)
        patch = random.randint(0, 20)
        return f'v{major}.{minor}.{patch}'

    def generate_device(self, profile_name: str = None) -> Dict[str, Any]:
        """
        Generate a single synthetic device.

        Args:
            profile_name: Specific profile to use, or None for random

        Returns:
            Dictionary with device data
        """
        if profile_name is None:
            profile_name = random.choice(list(self.profiles.keys()))

        profile = self.profiles[profile_name]

        # Base device info
        device = {
            'device_id': self._generate_device_id(profile),
            'device_type': profile.device_type,
            'hardware_type': random.choice(profile.hardware_variants),
            'manufacturer': profile.manufacturer,

            # Network
            'ip_address': self._generate_ip_address(),
            'mac_address': self._generate_mac_address(),
            'connection_type': random.choice(profile.connection_types),

            # Hardware specs
            'memory_total': random.randint(*profile.memory_range),
            'memory_free': 0,  # Will be calculated
            'cpu_freq_mhz': random.randint(*profile.cpu_freq_range),

            # Role and context
            'role': random.choice(profile.typical_roles),
            'its_context': random.choice(self.contexts),
            'location': random.choice(self.locations),

            # Metadata
            'firmware_version': self._generate_firmware_version(),
            'registered_at': self._generate_timestamp(),
            'last_seen': None,  # Will be set
            'state': 'active'
        }

        if profile.capabilities:
            device['capabilities'] = list(profile.capabilities)
            if set(profile.capabilities) & {'gnss', 'gps'}:
                device.update(self._generate_gnss_fields())

        # Calculate free memory (60-95% of total typically free at registration)
        device['memory_free'] = int(device['memory_total'] * random.uniform(0.6, 0.95))

        # Set last_seen close to registered_at
        reg_time = datetime.fromisoformat(device['registered_at'])
        device['last_seen'] = (reg_time + timedelta(seconds=random.randint(0, 60))).isoformat()

        return device

    def _generate_gnss_fields(self) -> Dict[str, Any]:
        """
        Receiver state a GNSS device announces, as devices/gnss_agent.py does.

        The SHACL shape iot:GNSSReceiverShape requires fix_quality and, when
        there is a fix (fix_quality >= 1), a latitude and a longitude. Without
        these fields every synthetic receiver typed as GNSSReceiver would fail
        validation because of the generator, not because of the announcement.
        Positions are a public reference point (centre of Asuncion), jittered,
        like the agent's default privacy mode.
        """
        rng = self._gnss_rng
        fix = rng.choices([0, 1, 2], weights=[0.3, 0.6, 0.1])[0]
        fields: Dict[str, Any] = {'fix_quality': fix, 'update_rate_hz': 1}
        if fix >= 1:
            fields.update(
                latitude=round(-25.2637 + rng.uniform(-1e-3, 1e-3), 6),
                longitude=round(-57.5759 + rng.uniform(-1e-3, 1e-3), 6),
                satellites_used=rng.randint(4, 12),
                hdop=round(rng.uniform(0.7, 2.5), 2),
            )
        else:
            fields['satellites_used'] = rng.randint(0, 3)
        return fields

    def _generate_timestamp(self, days_back: int = 30) -> str:
        """Generate a random timestamp within the last N days."""
        now = datetime.utcnow()
        delta = timedelta(
            days=random.randint(0, days_back),
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59),
            seconds=random.randint(0, 59)
        )
        return (now - delta).isoformat()

    def generate_dataset(
        self,
        num_devices: int = 100,
        profile_distribution: Optional[Dict[str, float]] = None
    ) -> List[Dict[str, Any]]:
        """
        Generate a dataset of synthetic devices.

        Args:
            num_devices: Number of devices to generate
            profile_distribution: Optional dict mapping profile names to weights

        Returns:
            List of device dictionaries
        """
        if profile_distribution is None:
            # Built from self.profiles, so every profile in use is sampled.
            profile_distribution = {
                name: DEFAULT_PROFILE_WEIGHTS.get(name, DEFAULT_PROFILE_WEIGHT)
                for name in self.profiles
            }

        # Normalize weights
        total = sum(profile_distribution.values())
        profile_distribution = {k: v/total for k, v in profile_distribution.items()}

        devices = []
        for _ in range(num_devices):
            # Select profile based on distribution
            r = random.random()
            cumulative = 0
            selected_profile = list(profile_distribution.keys())[0]
            for profile_name, weight in profile_distribution.items():
                cumulative += weight
                if r <= cumulative:
                    selected_profile = profile_name
                    break

            devices.append(self.generate_device(selected_profile))

        return devices

    def generate_time_series(
        self,
        device: Dict[str, Any],
        num_samples: int = 100,
        interval_seconds: int = 60
    ) -> List[Dict[str, Any]]:
        """
        Generate time-series data for a device (state changes, metrics).

        Args:
            device: Base device to generate series for
            num_samples: Number of time points
            interval_seconds: Interval between samples

        Returns:
            List of time-series data points
        """
        series = []
        base_time = datetime.fromisoformat(device['registered_at'])

        current_state = 'active'
        memory_free = device['memory_free']

        for i in range(num_samples):
            timestamp = base_time + timedelta(seconds=i * interval_seconds)

            # Simulate state changes (5% chance per interval)
            if random.random() < 0.05:
                if current_state == 'active':
                    current_state = random.choice(['inactive', 'configuring'])
                else:
                    current_state = 'active'

            # Simulate memory fluctuation
            memory_change = random.uniform(-0.05, 0.05) * device['memory_total']
            memory_free = max(
                int(device['memory_total'] * 0.1),
                min(int(device['memory_total'] * 0.95), int(memory_free + memory_change))
            )

            series.append({
                'device_id': device['device_id'],
                'timestamp': timestamp.isoformat(),
                'state': current_state,
                'memory_free': memory_free,
                'cpu_usage': random.uniform(5, 80) if current_state == 'active' else 0
            })

        return series

    def generate_anomalies(
        self,
        devices: List[Dict[str, Any]],
        anomaly_rate: float = 0.1
    ) -> List[Dict[str, Any]]:
        """
        Generate anomalous device data for testing detection capabilities.

        Args:
            devices: List of normal devices
            anomaly_rate: Fraction of devices to make anomalous

        Returns:
            List of devices with some anomalies injected
        """
        import copy
        result = []

        for device in devices:
            if random.random() < anomaly_rate:
                # Create anomalous copy
                anomaly = copy.deepcopy(device)
                anomaly['is_anomaly'] = True

                # Apply random anomaly type
                anomaly_type = random.choice([
                    'invalid_ip',
                    'memory_overflow',
                    'unknown_hardware',
                    'rapid_state_change',
                    'invalid_mac'
                ])

                anomaly['anomaly_type'] = anomaly_type

                if anomaly_type == 'invalid_ip':
                    anomaly['ip_address'] = '999.999.999.999'
                elif anomaly_type == 'memory_overflow':
                    anomaly['memory_free'] = anomaly['memory_total'] * 2
                elif anomaly_type == 'unknown_hardware':
                    anomaly['hardware_type'] = 'UNKNOWN-DEVICE-XYZ'
                elif anomaly_type == 'rapid_state_change':
                    anomaly['state'] = 'error'
                elif anomaly_type == 'invalid_mac':
                    anomaly['mac_address'] = 'XX:XX:XX:XX:XX:XX'

                result.append(anomaly)
            else:
                device_copy = copy.deepcopy(device)
                device_copy['is_anomaly'] = False
                device_copy['anomaly_type'] = None
                result.append(device_copy)

        return result

    def save_dataset(
        self,
        devices: List[Dict[str, Any]],
        filepath: str,
        format: str = 'json'
    ):
        """
        Save dataset to file.

        Args:
            devices: List of device dictionaries
            filepath: Output file path
            format: 'json' or 'csv'
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        if format == 'json':
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(devices, f, indent=2)
        elif format == 'csv':
            if devices:
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=devices[0].keys())
                    writer.writeheader()
                    writer.writerows(devices)
        else:
            raise ValueError(f"Unknown format: {format}")

    def generate_classification_dataset(
        self,
        num_samples: int = 1000,
        include_anomalies: bool = True,
        anomaly_rate: float = 0.1,
        feature_set: str = FEATURE_SET_RESOURCE_NAME,
        include_role_features: bool = True
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        Generate a dataset specifically for training device classification.

        WARNING - oracle features
        -------------------------
        With ``include_role_features=True`` (the default, kept for backwards
        compatibility with baseline/plot_classifier_metrics.py) the returned
        vectors contain is_gateway_role / is_sensor_role / is_edge_ai_role.
        A device does NOT announce its role when it registers, so a model
        trained on those features cannot classify a real announcement.

        To train a deployable model use
        ``pnp_ai.device_classifier.train_classifier_from_synthetic_data``,
        which extracts features through the classifier itself, or pass
        ``include_role_features=False`` here.

        Args:
            num_samples: Total number of samples
            include_anomalies: Whether to include anomalous data
            anomaly_rate: Rate of anomalies if included
            feature_set: Observable feature set, see pnp_ai.features
            include_role_features: Add the oracle role features on top

        Returns:
            Tuple of (features_list, labels_list)
        """
        devices = self.generate_dataset(num_samples)

        if include_anomalies:
            devices = self.generate_anomalies(devices, anomaly_rate)

        # Extract features and labels for ML
        features = []
        labels = []

        for device in devices:
            # Observable features come from the shared extractor, so they stay
            # identical to what the classifier computes at inference time.
            feature = extract_features(device, feature_set)

            if include_role_features:
                role = device.get('role')
                feature['is_gateway_role'] = 1.0 if role == 'gateway' else 0.0
                feature['is_sensor_role'] = 1.0 if role == 'sensor' else 0.0
                feature['is_edge_ai_role'] = 1.0 if role == 'edge_ai' else 0.0

            features.append(feature)
            labels.append(device['device_type'])

        return features, labels


# Convenience function
def create_generator(seed: int = 42) -> SyntheticDatasetGenerator:
    """Create a seeded generator for reproducible results."""
    return SyntheticDatasetGenerator(seed=seed)
