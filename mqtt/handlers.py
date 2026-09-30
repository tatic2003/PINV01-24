"""
MQTT Message Handlers
Project: PINV01-24 - FIUNA

Handlers for processing MQTT messages from IoT devices.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable
from datetime import datetime

from .broker import TopicRegistry

logger = logging.getLogger(__name__)


class MessageHandler(ABC):
    """Abstract base class for MQTT message handlers."""

    @abstractmethod
    def handle(self, topic: str, payload: dict) -> Optional[dict]:
        """
        Handle incoming message.

        Args:
            topic: MQTT topic
            payload: Message payload as dictionary

        Returns:
            Optional response payload
        """
        pass


class DeviceEventHandler(MessageHandler):
    """Handler for device-related MQTT events."""

    def __init__(self, device_manager=None, classifier=None, dag_manager=None):
        """
        Initialize handler with optional dependencies.

        Args:
            device_manager: DeviceManager instance for device operations
            classifier: DeviceClassifier instance for classification
            dag_manager: BlockDAG instance for event logging
        """
        self.device_manager = device_manager
        self.classifier = classifier
        self.dag_manager = dag_manager

        # Event callbacks
        self._callbacks: Dict[str, Callable] = {}

    def on_event(self, event_type: str, callback: Callable):
        """Register callback for event type."""
        self._callbacks[event_type] = callback

    def handle(self, topic: str, payload: dict) -> Optional[dict]:
        """Process device event message."""
        try:
            # Extract event type from topic
            topic_parts = topic.split('/')

            if 'register' in topic:
                return self._handle_registration(payload)
            elif 'heartbeat' in topic:
                return self._handle_heartbeat(payload)
            elif 'state' in topic:
                return self._handle_state_change(payload)
            elif 'data' in topic:
                return self._handle_device_data(payload)
            elif 'classify' in topic:
                return self._handle_classification_request(payload)
            else:
                logger.debug(f"Unhandled topic: {topic}")
                return None

        except Exception as e:
            logger.error(f"Error handling message: {e}")
            return {'error': str(e)}

    def _handle_registration(self, payload: dict) -> Optional[dict]:
        """Handle device registration request."""
        device_id = payload.get('id') or payload.get('device_id')
        if not device_id:
            return {'error': 'Missing device ID'}

        logger.info(f"Device registration via MQTT: {device_id}")

        result = {
            'device_id': device_id,
            'status': 'registered',
            'timestamp': datetime.utcnow().isoformat()
        }

        # Register with device manager if available
        if self.device_manager:
            try:
                registration = self.device_manager.register_device(payload)
                result['registration'] = registration
            except Exception as e:
                result['error'] = str(e)
                result['status'] = 'failed'

        # Classify device if classifier available
        if self.classifier and result.get('status') == 'registered':
            try:
                classification = self.classifier.classify(payload)
                result['classification'] = classification
            except Exception as e:
                logger.warning(f"Classification failed: {e}")

        # Log to DAG if available
        if self.dag_manager:
            self._log_dag_event('device_registered', {
                'device_id': device_id,
                'payload': payload
            })

        # Trigger callback
        self._trigger_callback('registration', device_id, result)

        return result

    def _handle_heartbeat(self, payload: dict) -> Optional[dict]:
        """Handle device heartbeat."""
        device_id = payload.get('device_id')
        if not device_id:
            return None

        logger.debug(f"Heartbeat from {device_id}")

        if self.device_manager:
            try:
                self.device_manager.update_heartbeat(device_id)
            except Exception as e:
                logger.warning(f"Heartbeat update failed: {e}")

        self._trigger_callback('heartbeat', device_id, payload)

        return {'ack': True, 'device_id': device_id}

    def _handle_state_change(self, payload: dict) -> Optional[dict]:
        """Handle device state change."""
        device_id = payload.get('device_id')
        new_state = payload.get('state')

        if not device_id or not new_state:
            return {'error': 'Missing device_id or state'}

        logger.info(f"State change for {device_id}: {new_state}")

        if self.device_manager:
            try:
                self.device_manager.update_device_state(device_id, new_state)
            except Exception as e:
                return {'error': str(e)}

        # Log to DAG
        if self.dag_manager:
            self._log_dag_event('state_changed', {
                'device_id': device_id,
                'state': new_state
            })

        self._trigger_callback('state_change', device_id, payload)

        return {'ack': True, 'device_id': device_id, 'state': new_state}

    def _handle_device_data(self, payload: dict) -> Optional[dict]:
        """Handle device telemetry/sensor data."""
        device_id = payload.get('device_id')
        data = payload.get('data', {})

        if not device_id:
            return None

        logger.debug(f"Data from {device_id}: {len(data)} fields")

        # Store data if manager available
        if self.device_manager:
            try:
                self.device_manager.store_device_data(device_id, data)
            except AttributeError:
                pass  # Method not available
            except Exception as e:
                logger.warning(f"Data storage failed: {e}")

        self._trigger_callback('device_data', device_id, payload)

        return None  # No response needed for data

    def _handle_classification_request(self, payload: dict) -> Optional[dict]:
        """Handle classification request."""
        if not self.classifier:
            return {'error': 'Classifier not available'}

        device_data = payload.get('device_data', payload)

        try:
            classification = self.classifier.classify(device_data)
            return {
                'device_type': classification.get('device_type'),
                'confidence': classification.get('confidence'),
                'suggested_role': classification.get('suggested_role'),
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            return {'error': str(e)}

    def _log_dag_event(self, event_type: str, data: dict):
        """Log event to BlockDAG."""
        if self.dag_manager:
            try:
                self.dag_manager.add_event(event_type, data)
            except Exception as e:
                logger.warning(f"DAG logging failed: {e}")

    def _trigger_callback(self, event_type: str, device_id: str, data: dict):
        """Trigger registered callback for event."""
        if event_type in self._callbacks:
            try:
                self._callbacks[event_type](device_id, data)
            except Exception as e:
                logger.error(f"Callback error for {event_type}: {e}")


class SyncEventHandler(MessageHandler):
    """Handler for BlockDAG synchronization events."""

    def __init__(self, dag_manager=None, node_id: str = "edge-node"):
        self.dag_manager = dag_manager
        self.node_id = node_id

    def handle(self, topic: str, payload: dict) -> Optional[dict]:
        """Process sync event."""
        if 'broadcast' in topic:
            return self._handle_sync_broadcast(payload)
        elif 'request' in topic:
            return self._handle_sync_request(payload)
        elif 'response' in topic:
            return self._handle_sync_response(payload)
        return None

    def _handle_sync_broadcast(self, payload: dict) -> Optional[dict]:
        """Handle sync broadcast from another node."""
        source_node = payload.get('node_id')

        # Don't process our own broadcasts
        if source_node == self.node_id:
            return None

        events = payload.get('events', [])
        logger.info(f"Sync broadcast from {source_node}: {len(events)} events")

        if self.dag_manager and events:
            try:
                for event in events:
                    self.dag_manager.merge_event(event)
            except Exception as e:
                logger.error(f"Sync merge failed: {e}")

        return None

    def _handle_sync_request(self, payload: dict) -> Optional[dict]:
        """Handle sync request from another node."""
        requesting_node = payload.get('node_id')
        since_hash = payload.get('since_hash')

        logger.info(f"Sync request from {requesting_node}")

        if self.dag_manager:
            try:
                events = self.dag_manager.get_events_since(since_hash)
                return {
                    'node_id': self.node_id,
                    'events': events,
                    'timestamp': datetime.utcnow().isoformat()
                }
            except Exception as e:
                return {'error': str(e)}

        return {'events': []}

    def _handle_sync_response(self, payload: dict) -> Optional[dict]:
        """Handle sync response from another node."""
        source_node = payload.get('node_id')
        events = payload.get('events', [])

        logger.info(f"Sync response from {source_node}: {len(events)} events")

        if self.dag_manager and events:
            try:
                for event in events:
                    self.dag_manager.merge_event(event)
            except Exception as e:
                logger.error(f"Sync merge failed: {e}")

        return None


class CommandHandler(MessageHandler):
    """Handler for device commands."""

    def __init__(self):
        self._command_handlers: Dict[str, Callable] = {}

    def register_command(self, command: str, handler: Callable):
        """Register a command handler."""
        self._command_handlers[command] = handler

    def handle(self, topic: str, payload: dict) -> Optional[dict]:
        """Process command message."""
        command = payload.get('command')
        params = payload.get('params', {})

        if not command:
            return {'error': 'No command specified'}

        if command in self._command_handlers:
            try:
                result = self._command_handlers[command](params)
                return {
                    'command': command,
                    'status': 'completed',
                    'result': result,
                    'timestamp': datetime.utcnow().isoformat()
                }
            except Exception as e:
                return {
                    'command': command,
                    'status': 'error',
                    'error': str(e)
                }
        else:
            return {
                'command': command,
                'status': 'unknown',
                'error': f'Unknown command: {command}'
            }
