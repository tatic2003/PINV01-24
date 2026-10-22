"""
Feature extraction for PnP-AI device classification.

Project: PINV01-24 - FIUNA

This module is the SINGLE SOURCE OF TRUTH for turning a device announcement
into a feature vector. Both the training path and the inference path import
from here, so the two can no longer drift apart.

Why this module exists
----------------------
The classifier used to extract features in one place and the synthetic
dataset generator in another. The training set contained "role" features
(is_gateway_role, is_sensor_role, is_edge_ai_role) that a real device does
NOT announce when it registers, so inference crashed with
``KeyError: 'is_edge_ai_role'``. That is a train/serve skew: the fix is not
to add the missing key, but to make it structurally impossible for the two
feature vectors to differ.

Announcement normalization
--------------------------
Real firmware and the synthetic generator use different key names for the
same concept. ``normalize_announcement`` maps everything onto a canonical
form before any feature is computed:

    hardware_type  <-  'hardware_type' | 'hardware'
    device_id      <-  'device_id'     | 'id'
    ip_address     <-  'ip_address'    | 'ip'
    link_type      <-  'link_type'     | 'connection_type' (if a link)
    protocol       <-  'protocol'      | 'connection_type' (if a protocol)

``link_type`` (how the device is physically attached: wifi, ethernet, ...)
and ``protocol`` (what it speaks: mqtt, http, ...) are deliberately kept
apart. Older firmware sends only ``connection_type``, which conflates them.
When that field holds a protocol name we leave the link UNKNOWN rather than
guessing: an ESP32-PoE and an ESP32-WROOM both announce ``mqtt``, and
assuming WiFi would silently mislabel the PoE board.

Feature sets
------------
``FEATURE_SET_RESOURCE`` uses only quantitative, self-reported resource
characteristics. ``FEATURE_SET_RESOURCE_NAME`` adds flags derived from the
hardware model string (has_esp32, has_raspberry, ...).

The server and every evaluation of the article use ``FEATURE_SET_RESOURCE``.
``FEATURE_SET_RESOURCE_NAME`` is kept only for comparison: the name flags
reproduce the lookup of the rule-based baseline inside the ML model, so they
make the comparison between the two approaches circular and push accuracy
toward 1.0.
"""

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: Values of ``connection_type`` that describe the physical/data link.
LINK_TYPES = ('wifi', 'ethernet', 'bluetooth', 'lora', 'zigbee', 'cellular')

#: Values of ``connection_type`` that describe the application protocol.
PROTOCOLS = ('mqtt', 'mqtts', 'http', 'https', 'coap', 'websocket')

#: Link types that get their own binary feature.
_LINK_FEATURES = ('wifi', 'ethernet', 'bluetooth', 'lora')

#: Substrings of the hardware model string that get their own binary feature.
_NAME_FEATURES = {
    'has_esp32': ('esp32',),
    'has_raspberry': ('raspberry', 'rpi'),
    'has_jetson': ('jetson',),
    'has_gateway': ('gateway',),
    'has_sensor': ('sensor',),
}

#: Capability names, declared in the announcement, that mean "GNSS receiver".
GNSS_CAPABILITY_NAMES = ('gnss', 'gps')

# ---------------------------------------------------------------------------
# Feature sets
# ---------------------------------------------------------------------------

#: Quantitative resource characteristics only. Does not look at the model name.
FEATURE_SET_RESOURCE = 'resource'

#: Resource characteristics plus hardware-model-name flags.
FEATURE_SET_RESOURCE_NAME = 'resource+name'

FEATURE_SETS = (FEATURE_SET_RESOURCE, FEATURE_SET_RESOURCE_NAME)

#: Default used by the classifier when none is given.
DEFAULT_FEATURE_SET = FEATURE_SET_RESOURCE_NAME


def _check_feature_set(feature_set: str) -> str:
    if feature_set not in FEATURE_SETS:
        raise ValueError(
            f"Unknown feature_set {feature_set!r}. Expected one of {FEATURE_SETS}."
        )
    return feature_set


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _first(data: Dict[str, Any], *keys: str) -> Any:
    """Return the first key present with a non-empty value."""
    for key in keys:
        value = data.get(key)
        if value not in (None, ''):
            return value
    return None


def normalize_announcement(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map a raw device announcement onto the canonical field names.

    Accepts both the synthetic generator's schema and the ESP32 firmware's
    schema. Unknown keys are preserved untouched.

    Args:
        data: Raw announcement as received over MQTT/HTTP.

    Returns:
        A new dict with canonical fields filled in.
    """
    out = dict(data)

    hardware_type = _first(data, 'hardware_type', 'hardware')
    if hardware_type is not None:
        out['hardware_type'] = hardware_type

    device_id = _first(data, 'device_id', 'id')
    if device_id is not None:
        out['device_id'] = device_id

    ip_address = _first(data, 'ip_address', 'ip')
    if ip_address is not None:
        out['ip_address'] = ip_address

    # Split link from protocol.
    link = _first(data, 'link_type')
    protocol = _first(data, 'protocol')
    legacy = _first(data, 'connection_type')

    if legacy is not None:
        legacy_value = str(legacy).lower()
        if link is None and legacy_value in LINK_TYPES:
            link = legacy_value
        elif protocol is None and legacy_value in PROTOCOLS:
            protocol = legacy_value

    if link is not None:
        out['link_type'] = str(link).lower()
    if protocol is not None:
        out['protocol'] = str(protocol).lower()

    return out


def declared_capabilities(data: Dict[str, Any]) -> set:
    """Lower-cased capability names declared in an announcement."""
    caps = data.get('capabilities') or []
    if isinstance(caps, str):
        caps = [caps]
    return {str(c).strip().lower() for c in caps}


def hardware_name(data: Dict[str, Any]) -> str:
    """Lowercased hardware model string, from either accepted key."""
    return str(_first(data, 'hardware_type', 'hardware') or '').lower()


def link_type(data: Dict[str, Any]) -> str:
    """
    Physical link of the device, or '' when it cannot be determined.

    Never inferred from the application protocol.
    """
    return str(normalize_announcement(data).get('link_type') or '')


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_features(
    data: Dict[str, Any],
    feature_set: str = DEFAULT_FEATURE_SET
) -> Dict[str, float]:
    """
    Turn a device announcement into a feature dictionary.

    Args:
        data: Device announcement, raw or already normalized.
        feature_set: One of ``FEATURE_SETS``.

    Returns:
        Feature name -> float. The key set depends only on ``feature_set``,
        never on which optional fields the announcement happened to carry,
        so every vector produced here is directly comparable.
    """
    _check_feature_set(feature_set)
    device = normalize_announcement(data)

    memory_total = float(device.get('memory_total') or 0.0)
    memory_free = float(device.get('memory_free') or 0.0)
    link = device.get('link_type') or ''

    features: Dict[str, float] = {
        'memory_total_mb': memory_total / (1024 * 1024),
        'memory_free_pct': (memory_free / memory_total) if memory_total > 0 else 0.0,
        'cpu_freq_mhz': float(device.get('cpu_freq_mhz') or 0.0),
    }
    for name in _LINK_FEATURES:
        features[f'is_{name}'] = 1.0 if link == name else 0.0

    # Declared capability, present in both feature sets. Resources alone cannot
    # tell a GNSS receiver from the board that hosts it: a GPS HAT on a
    # Raspberry Pi announces the Pi's memory and CPU.
    features['declares_gnss'] = (
        1.0 if declared_capabilities(device) & set(GNSS_CAPABILITY_NAMES) else 0.0
    )

    if feature_set == FEATURE_SET_RESOURCE_NAME:
        hardware = hardware_name(device)
        for feature, needles in _NAME_FEATURES.items():
            features[feature] = 1.0 if any(n in hardware for n in needles) else 0.0

    return features


def feature_names(feature_set: str = DEFAULT_FEATURE_SET) -> List[str]:
    """
    Ordered feature names for a feature set.

    Sorted, so the column order of a trained model is reproducible across
    runs and processes.
    """
    _check_feature_set(feature_set)
    return sorted(extract_features({}, feature_set).keys())


def features_to_vector(
    features: Dict[str, float],
    names: List[str]
) -> List[float]:
    """
    Order a feature dict according to ``names``.

    Raises:
        KeyError: if a required feature is missing, naming the offending
            feature and the full expected set. A missing feature means the
            training and inference paths disagree, which is a bug worth
            failing loudly on rather than silently defaulting to zero.
    """
    missing = [name for name in names if name not in features]
    if missing:
        raise KeyError(
            f"Missing feature(s) {missing} for this model. "
            f"Expected exactly: {names}. Got: {sorted(features.keys())}. "
            "The model was most likely trained with a different feature_set."
        )
    return [float(features[name]) for name in names]
