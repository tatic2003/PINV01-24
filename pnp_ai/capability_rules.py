"""
Capability rule for PnP-AI: the GNSS receiver.

A GNSS receiver has no resources of its own that the network can see. A GPS
HAT on a Raspberry Pi announces the memory, the CPU and the link of the Pi, so
nothing in the resources separates the receiver from its host. What separates
them is the capability the device declares.

That makes the class a decision for a rule, not for a model: when an
announcement declares ``gnss`` (or ``gps``), the device is a GNSS receiver,
and a model trained on a feature that repeats the declaration would only
restate it. The classifier is therefore trained without the class and without
the feature, and this rule runs before the model, in
``DeviceClassifier.classify``.

A receiver that does not declare its capability is not recognized: it is typed
by its host resources, like any other board. That limit belongs to the
announcement, not to the model, and it is reported as such.

Project: PINV01-24 - FIUNA
"""

from typing import Any, Dict

from .features import GNSS_CAPABILITY_NAMES, declared_capabilities

#: Confidence reported when the rule decides. The declaration is categorical,
#: so there is nothing to be uncertain about; a low-confidence flag would be
#: misleading here.
GNSS_RULE_CONFIDENCE = 1.0

#: Class the rule assigns.
GNSS_RULE_CLASS = 'GNSSReceiver'


def declares_gnss(device_data: Dict[str, Any]) -> bool:
    """True when the announcement declares a GNSS capability."""
    return bool(declared_capabilities(device_data) & set(GNSS_CAPABILITY_NAMES))
