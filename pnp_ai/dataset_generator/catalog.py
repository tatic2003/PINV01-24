"""
Catalog-based synthetic dataset generator for PnP-AI.

Project: PINV01-24 - FIUNA

Why this module exists
----------------------
The profile generator (generator.py) draws memory and CPU from hand-written
ranges per class. Testing it against real announcements showed where those
ranges are wrong: the 'industrial_gateway' profile (2-8 GB, 1-2.5 GHz)
overlaps the Raspberry Pi profile, so a real Raspberry Pi 5 is sometimes
classified as IoTGateway, while the real closed-design gateways of the catalog
have 64-512 MB and 400-1500 MHz.

This generator samples real boards from datasets/catalog/catalogo_placas.csv
instead, and turns each board's datasheet values into what the board would
actually ANNOUNCE when it registers:

  memory_total  A device does not announce its nominal RAM. An ESP32 announces
                its heap (ESP.getHeapSize(), 61-72 % of the 520 KB SRAM in the
                measured boards) and a Linux board announces MemTotal (95-98 %
                of its RAM). The ratio announced/nominal is estimated from the
                boards measured in the lab (catalog columns
                memoria_anunciada_bytes_medida + enlace_medido), per
                (family, link), because Wi-Fi uses 30-45 KB more heap than
                Ethernet on the same chip.
  cpu_freq_mhz  The factory value most of the time, sometimes one of the
                other frequencies the board can be configured to
                (cpu_mhz_options). A measured value replaces the factory one.
  link_type     One of the network links the base board ships with.

Two modes:
  calibrated=True   announced memory from the measured ratios (default)
  calibrated=False  announced memory = nominal RAM (the naive use of a catalog)

Three labelings:
  labeling='clasificador'  catalog column clase_clasificador
  labeling='hw'            catalog column clase_hw (main processor family)
  labeling='hw_unificado'  clase_hw, with ESP32Device merged into
                           Microcontroller: an ESP32 is a microcontroller with
                           an integrated radio, and the radio is already
                           visible to the classifier through the link flags

Leave-one-board-out
-------------------
``exclude_boards`` removes boards from BOTH the sampling and the calibration
fit. When a real board is used as a test case, the model must not have seen
that board's row, and the memory ratio of its family must not have been
estimated from that very board's own measurement.

GNSS receivers are not catalog rows: as in the profile generator, a receiver
announces the resources of its host, so it is composed from a host board
(a SingleBoardComputer or an ESP32Device of the catalog) plus the declared
capabilities ['gnss', 'position', 'time'].
"""

import csv
import hashlib
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .generator import SyntheticDatasetGenerator

#: Default location of the catalog, relative to the repository root.
DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[2] / 'datasets' / 'catalog' / 'catalogo_placas.csv'
)

LABELINGS = ('clasificador', 'hw', 'hw_unificado')

#: Classes merged by labeling='hw_unificado' (from -> to).
UNIFIED_MERGE = {'ESP32Device': 'Microcontroller'}


def unify_label(label: str) -> str:
    """Class under labeling='hw_unificado' (also used to relabel real data)."""
    return UNIFIED_MERGE.get(label, label)

#: Links a board can announce as its network attachment. 'bluetooth' and
#: 'serial' are used only when the board has none of these.
NETWORK_LINKS = ('ethernet', 'wifi', 'lora', 'zigbee', 'cellular')

GNSS_LABEL = 'GNSSReceiver'
GNSS_CAPABILITIES = ['gnss', 'position', 'time']
#: Classes (clase_hw) of the boards that can host a GNSS receiver.
GNSS_HOST_CLASSES = ('SingleBoardComputer', 'ESP32Device')

#: Probability of announcing the factory CPU frequency instead of another
#: configurable one.
P_FACTORY_CPU = 0.8

#: Relative margin added around the measured memory ratios. With one or two
#: measured boards per level the measured spread is tiny; 5 % covers the
#: board-to-board differences seen in the lab (e.g. Pi 5 0.984 vs Orange Pi
#: 0.946 on Linux, PoE CAM 0.707 vs Olimex 0.684 on ESP32 + Ethernet).
RATIO_MARGIN = 0.05

#: Ratio announced/nominal memory when no board of the group was measured.
#: 'mcu' is an ASSUMPTION, no microcontroller has been measured yet: a firmware
#: without a heap API reports its whole SRAM (ratio 1.0), one with a heap API
#: reports part of it.
DEFAULT_RATIO_RANGES = {
    'esp32': (0.60, 0.75),
    'linux': (0.93, 0.99),
    'mcu': (0.50, 1.00),
}

#: Free memory at registration, as a fraction of the announced total. Same
#: range as the profile generator, so that the comparison isolates the effect
#: of the catalog on memory_total, cpu and link.
FREE_FRACTION_RANGE = (0.60, 0.95)

_ROLES = {
    'ESP32Device': ['sensor', 'actuator', 'monitor'],
    'SingleBoardComputer': ['gateway', 'edge_server', 'controller'],
    'IoTGateway': ['gateway', 'protocol_converter', 'data_aggregator'],
    'Sensor': ['sensor'],
    'Microcontroller': ['sensor', 'actuator'],
    GNSS_LABEL: ['sensor'],
}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def _int_or_none(value: str) -> Optional[int]:
    value = (value or '').strip()
    return int(float(value)) if value else None


@dataclass(frozen=True)
class Board:
    """One row of the catalog, with the fields the generator uses."""
    board_id: str
    placa: str
    fabricante: str
    familia: str
    clase_clasificador: str
    clase_hw: str
    hardware_string: str
    cpu_nominal: Optional[int]
    cpu_options: Tuple[int, ...]
    ram_nominal: int
    links: Tuple[str, ...]
    measured_memory: Optional[int]
    measured_cpu: Optional[int]
    measured_link: str

    def label(self, labeling: str) -> str:
        if labeling == 'clasificador':
            return self.clase_clasificador
        if labeling == 'hw':
            return self.clase_hw
        if labeling == 'hw_unificado':
            return unify_label(self.clase_hw)
        raise ValueError(f"Unknown labeling {labeling!r}. Expected one of {LABELINGS}.")

    @property
    def memory_group(self) -> str:
        """How the board reports memory: ESP32 heap, Linux MemTotal, or MCU SRAM."""
        if self.clase_hw == 'ESP32Device':
            return 'esp32'
        if self.clase_hw in ('SingleBoardComputer', 'IoTGateway'):
            return 'linux'
        return 'mcu'

    @property
    def announced_name(self) -> str:
        """Hardware string the board announces (catalog override, else the name)."""
        return self.hardware_string or self.placa


@dataclass(frozen=True)
class Catalog:
    """The parsed catalog plus the identity of the file it came from."""
    boards: Tuple[Board, ...]
    path: str
    version: str  # first 12 hex digits of the file's SHA-256

    def get(self, board_id: str) -> Board:
        for b in self.boards:
            if b.board_id == board_id:
                return b
        raise KeyError(f"board_id {board_id!r} is not in the catalog {self.path}")


def load_catalog(path: Optional[str] = None) -> Catalog:
    """
    Read the catalog CSV.

    Rows without nominal RAM are skipped (the validator only allows that with
    confianza=baja). The version is a hash of the file contents, so every
    result can say exactly which catalog produced it. Line endings are
    normalized before hashing: git on Windows checks the file out with CRLF
    and on Linux with LF, and the same catalog must have the same version on
    both.
    """
    p = Path(path) if path else DEFAULT_CATALOG_PATH
    raw = p.read_bytes().replace(b'\r\n', b'\n')
    version = hashlib.sha256(raw).hexdigest()[:12]

    boards = []
    with open(p, encoding='utf-8', newline='') as f:
        for r in csv.DictReader(f):
            ram = _int_or_none(r['ram_nominal_bytes'])
            if not ram:
                continue
            options = tuple(int(x) for x in (r.get('cpu_mhz_options') or '').split('|') if x.strip())
            boards.append(Board(
                board_id=r['board_id'],
                placa=r['placa'],
                fabricante=r['fabricante'],
                familia=r['familia'],
                clase_clasificador=r['clase_clasificador'],
                clase_hw=r['clase_hw'],
                hardware_string=r.get('hardware_string', ''),
                cpu_nominal=_int_or_none(r['cpu_mhz_nominal']),
                cpu_options=options,
                ram_nominal=ram,
                links=tuple(l.strip() for l in r['enlaces'].split(';') if l.strip()),
                measured_memory=_int_or_none(r.get('memoria_anunciada_bytes_medida', '')),
                measured_cpu=_int_or_none(r.get('cpu_mhz_medido', '')),
                measured_link=(r.get('enlace_medido') or '').strip(),
            ))
    return Catalog(boards=tuple(boards), path=str(p), version=version)


# ---------------------------------------------------------------------------
# Memory calibration
# ---------------------------------------------------------------------------

class MemoryCalibration:
    """
    Ratio announced/nominal memory, estimated from the measured boards.

    Looked up from the most to the least specific level, using the first one
    that has at least one measurement:

        (familia, link)  ->  (memory group, link)  ->  (memory group)  ->  default

    The sampled ratio is uniform between the smallest and the largest measured
    ratio of that level, widened by RATIO_MARGIN on each side.
    """

    def __init__(self, boards: Iterable[Board]):
        self.by_key: Dict[tuple, List[float]] = {}
        self.measured_boards: List[str] = []
        for b in boards:
            if not (b.measured_memory and b.measured_link):
                continue
            ratio = b.measured_memory / b.ram_nominal
            self.measured_boards.append(b.board_id)
            for key in (('familia', b.familia, b.measured_link),
                        ('group', b.memory_group, b.measured_link),
                        ('group', b.memory_group)):
                self.by_key.setdefault(key, []).append(ratio)

    def ratio_range(self, board: Board, link: str) -> Tuple[Tuple[float, float], str]:
        """(low, high) ratio for this board and link, and the level it came from."""
        for key in (('familia', board.familia, link),
                    ('group', board.memory_group, link),
                    ('group', board.memory_group)):
            values = self.by_key.get(key)
            if values:
                lo = min(values) * (1 - RATIO_MARGIN)
                hi = max(values) * (1 + RATIO_MARGIN)
                return (lo, min(hi, 1.0)), '/'.join(key)
        return DEFAULT_RATIO_RANGES[board.memory_group], 'default/' + board.memory_group

    def describe(self) -> Dict[str, Any]:
        """The fitted table, for the results metadata."""
        return {
            'measured_boards': sorted(self.measured_boards),
            'levels': {'/'.join(k): [round(min(v), 4), round(max(v), 4), len(v)]
                       for k, v in sorted(self.by_key.items())},
        }


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class CatalogDatasetGenerator(SyntheticDatasetGenerator):
    """
    Synthetic announcements sampled from the real-board catalog.

    Drop-in replacement for SyntheticDatasetGenerator in training code: the
    devices it returns have the same keys (plus board_id and catalog_version),
    and generate_anomalies() / generate_classification_dataset() are inherited.

    Sampling: a class is chosen uniformly among the classes present (plus
    GNSSReceiver), then a board uniformly within that class, so that classes
    with many catalog rows (ESP32, SBC) do not crowd out the small ones.
    """

    def __init__(
        self,
        seed: Optional[int] = None,
        catalog: Optional[Catalog] = None,
        catalog_path: Optional[str] = None,
        labeling: str = 'clasificador',
        calibrated: bool = True,
        exclude_boards: Sequence[str] = (),
        include_gnss: bool = True,
        class_weights: Optional[Dict[str, float]] = None,
    ):
        super().__init__(seed=seed)  # seeds the module RNG used by generate_anomalies
        if labeling not in LABELINGS:
            raise ValueError(f"Unknown labeling {labeling!r}. Expected one of {LABELINGS}.")
        self.rng = random.Random(seed)
        self.catalog = catalog or load_catalog(catalog_path)
        self.labeling = labeling
        self.calibrated = calibrated
        self.exclude_boards = frozenset(exclude_boards)

        unknown = self.exclude_boards - {b.board_id for b in self.catalog.boards}
        if unknown:
            raise KeyError(f"exclude_boards not in the catalog: {sorted(unknown)}")

        self.boards = [b for b in self.catalog.boards if b.board_id not in self.exclude_boards]
        self.calibration = MemoryCalibration(self.boards)

        self.by_class: Dict[str, List[Board]] = {}
        for b in self.boards:
            self.by_class.setdefault(b.label(labeling), []).append(b)
        self.gnss_hosts = [b for b in self.boards if b.clase_hw in GNSS_HOST_CLASSES]
        if include_gnss and self.gnss_hosts:
            self.by_class.setdefault(GNSS_LABEL, [])  # composed, see _compose_gnss

        weights = class_weights or {}
        self.classes = sorted(self.by_class)
        self.class_weights = [float(weights.get(c, 1.0)) for c in self.classes]

    # -- sampling ------------------------------------------------------------

    def _pick_link(self, board: Board) -> str:
        network = [l for l in board.links if l in NETWORK_LINKS]
        if network:
            return self.rng.choice(network)
        if 'bluetooth' in board.links:
            return 'bluetooth'
        return 'serial'

    def _announced_memory(self, board: Board, link: str) -> Tuple[int, str]:
        if not self.calibrated:
            return board.ram_nominal, 'nominal'
        if board.measured_memory and board.measured_link == link:
            return board.measured_memory, 'measured'
        (lo, hi), level = self.calibration.ratio_range(board, link)
        return int(board.ram_nominal * self.rng.uniform(lo, hi)), level

    def _announced_cpu(self, board: Board) -> int:
        if self.calibrated and board.measured_cpu:
            return board.measured_cpu
        others = [f for f in board.cpu_options if f != board.cpu_nominal]
        if board.cpu_nominal and (not others or self.rng.random() < P_FACTORY_CPU):
            return board.cpu_nominal
        return self.rng.choice(others or list(board.cpu_options))

    def _mac(self) -> str:
        mac = [self.rng.randint(0x00, 0xff) for _ in range(6)]
        mac[0] &= 0xfe
        return ':'.join(f'{b:02x}' for b in mac).upper()

    def _timestamp(self, days_back: int = 30) -> str:
        delta = timedelta(days=self.rng.randint(0, days_back),
                          seconds=self.rng.randint(0, 86399))
        return (datetime(2026, 10, 1) - delta).isoformat()

    def device_from_board(self, board: Board, label: str,
                          capabilities: Optional[List[str]] = None,
                          hardware: Optional[str] = None) -> Dict[str, Any]:
        """Announcement of one board, in the profile generator's schema."""
        link = self._pick_link(board)
        memory_total, memory_source = self._announced_memory(board, link)
        registered = self._timestamp()
        prefix = 'gnss' if label == GNSS_LABEL else board.board_id
        self._device_counter[prefix] = self._device_counter.get(prefix, 0) + 1

        device = {
            'device_id': f'{prefix}-{self._device_counter[prefix]:03d}',
            'device_type': label,
            'hardware_type': hardware or board.announced_name,
            'manufacturer': board.fabricante,
            'ip_address': f'192.168.1.{self.rng.randint(2, 254)}',
            'mac_address': self._mac(),
            'connection_type': link,
            'link_type': link,
            'memory_total': memory_total,
            'memory_free': int(memory_total * self.rng.uniform(*FREE_FRACTION_RANGE)),
            'cpu_freq_mhz': self._announced_cpu(board),
            'role': self.rng.choice(_ROLES.get(label, ['sensor'])),
            'its_context': self.rng.choice(self.contexts),
            'location': self.rng.choice(self.locations),
            'firmware_version': f'v{self.rng.randint(1, 3)}.{self.rng.randint(0, 9)}.{self.rng.randint(0, 20)}',
            'registered_at': registered,
            'last_seen': (datetime.fromisoformat(registered)
                          + timedelta(seconds=self.rng.randint(0, 60))).isoformat(),
            'state': 'active',
            # Traceability: which row and which catalog produced this sample.
            'board_id': board.board_id,
            'catalog_version': self.catalog.version,
            'memory_source': memory_source,
        }
        if capabilities:
            device['capabilities'] = list(capabilities)
        return device

    def _compose_gnss(self) -> Dict[str, Any]:
        host = self.rng.choice(self.gnss_hosts)
        return self.device_from_board(host, GNSS_LABEL, capabilities=GNSS_CAPABILITIES,
                                      hardware=f'{host.announced_name}+GNSS')

    def generate_device(self, profile_name: str = None) -> Dict[str, Any]:
        """One announcement. ``profile_name`` here is a class name (or None for random)."""
        label = profile_name or self.rng.choices(self.classes, weights=self.class_weights)[0]
        if label == GNSS_LABEL:
            return self._compose_gnss()
        if label not in self.by_class:
            raise KeyError(f"class {label!r} has no boards (labeling={self.labeling!r})")
        return self.device_from_board(self.rng.choice(self.by_class[label]), label)

    def generate_dataset(
        self,
        num_devices: int = 100,
        profile_distribution: Optional[Dict[str, float]] = None
    ) -> List[Dict[str, Any]]:
        """``num_devices`` announcements. ``profile_distribution`` maps class -> weight."""
        if profile_distribution:
            classes = list(profile_distribution)
            weights = [profile_distribution[c] for c in classes]
            return [self.generate_device(self.rng.choices(classes, weights=weights)[0])
                    for _ in range(num_devices)]
        return [self.generate_device() for _ in range(num_devices)]

    def describe(self) -> Dict[str, Any]:
        """Configuration and catalog identity, to store next to every result."""
        return {
            'source': 'catalog',
            'catalog_path': self.catalog.path,
            'catalog_version': self.catalog.version,
            'catalog_boards': len(self.catalog.boards),
            'boards_used': len(self.boards),
            'excluded_boards': sorted(self.exclude_boards),
            'labeling': self.labeling,
            'calibrated': self.calibrated,
            'classes': {c: (len(self.gnss_hosts) if c == GNSS_LABEL else len(self.by_class[c]))
                        for c in self.classes},
            'calibration': self.calibration.describe() if self.calibrated else None,
        }
