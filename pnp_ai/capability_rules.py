"""
Safety-net capability rule for PnP-AI.

GNSS receivers are classified by the ML model: the training data has a
GNSSReceiver class and the feature set carries the declared capability
(``declares_gnss``). This rule only acts when the loaded classifier does NOT
know the class, for example a model trained before GNSS support was added and
still on disk. In that case a declared GNSS capability types the device
anyway, instead of silently calling a GPS HAT a single-board computer.

When the classifier knows the class, its decision stands, including when it
is wrong; that is what the evaluation measures.

Project: PINV01-24 - FIUNA
"""

from typing import Any, Dict, Iterable, Optional

from .device_classifier import ClassificationResult
from .features import GNSS_CAPABILITY_NAMES, declared_capabilities


def apply_capability_rules(device_data: Dict[str, Any],
                           result: ClassificationResult,
                           known_classes: Optional[Iterable[str]] = None
                           ) -> ClassificationResult:
    """
    Override the classification only if the classifier cannot output
    GNSSReceiver and the announcement declares a GNSS capability.

    Args:
        known_classes: classes the classifier can output. ``None`` or empty
            (rule-based classifier) means "does not know a trained class set";
            the rule-based classifier handles GNSS itself, so nothing changes.
    """
    if result.device_type == 'GNSSReceiver' or not known_classes:
        return result
    if 'GNSSReceiver' in set(known_classes):
        return result
    if declared_capabilities(device_data) & set(GNSS_CAPABILITY_NAMES):
        return ClassificationResult(
            device_type='GNSSReceiver',
            confidence=1.0,
            all_probabilities={'GNSSReceiver': 1.0},
            suggested_role='sensor',
            suggested_capabilities=['gnss', 'position', 'time'],
        )
    return result
