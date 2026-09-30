"""
REST API Server for IoT-ITS Interoperability
Production-ready Flask server for device management.

Project: PINV01-24 - FIUNA
"""

from .app import create_app
from .config import Config

__all__ = ['create_app', 'Config']
