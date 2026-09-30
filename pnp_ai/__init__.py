"""
PnP-AI Module - Plug-and-Play AI for IoT Device Management
Provides automatic device classification and onboarding.

Project: PINV01-24 - FIUNA
"""

from .dataset_generator import SyntheticDatasetGenerator
from .device_classifier import DeviceClassifier

__all__ = ['SyntheticDatasetGenerator', 'DeviceClassifier']
