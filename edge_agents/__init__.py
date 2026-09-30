"""
Edge Agents Module - Distributed agents for IoT device management
Designed to run on edge devices (Raspberry Pi, Jetson Nano, etc.)

Project: PINV01-24 - FIUNA
"""

from .base_agent import EdgeAgent
from .discovery_agent import DiscoveryAgent
from .classification_agent import ClassificationAgent
from .sync_agent import SyncAgent
from .agent_manager import EdgeAgentManager

__all__ = [
    'EdgeAgent',
    'DiscoveryAgent',
    'ClassificationAgent',
    'SyncAgent',
    'EdgeAgentManager'
]
