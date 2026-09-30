"""
Sync Agent - Handles data synchronization with BlockDAG
Manages distributed state synchronization across edge nodes.

Project: PINV01-24 - FIUNA
"""

import asyncio
import json
import hashlib
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass, field
from datetime import datetime
from collections import deque

from .base_agent import EdgeAgent, AgentConfig, AgentMessage


@dataclass
class SyncConfig(AgentConfig):
    """Configuration for the Sync Agent."""
    agent_type: str = "sync"
    sync_interval: float = 30.0  # seconds
    max_local_events: int = 1000  # Max events to keep locally
    peer_timeout: float = 60.0  # seconds before peer considered offline
    enable_compression: bool = True
    batch_size: int = 50


@dataclass
class DAGEvent:
    """Represents an event in the BlockDAG."""
    event_id: str
    event_type: str  # 'device_register', 'state_change', 'config_update', etc.
    payload: Dict[str, Any]
    timestamp: str
    node_id: str
    parent_ids: List[str]  # References to parent events
    hash: str = ""

    def __post_init__(self):
        if not self.hash:
            self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Compute hash of event content."""
        content = json.dumps({
            'event_id': self.event_id,
            'event_type': self.event_type,
            'payload': self.payload,
            'timestamp': self.timestamp,
            'node_id': self.node_id,
            'parent_ids': sorted(self.parent_ids)
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'event_id': self.event_id,
            'event_type': self.event_type,
            'payload': self.payload,
            'timestamp': self.timestamp,
            'node_id': self.node_id,
            'parent_ids': self.parent_ids,
            'hash': self.hash
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DAGEvent':
        return cls(
            event_id=data['event_id'],
            event_type=data['event_type'],
            payload=data['payload'],
            timestamp=data['timestamp'],
            node_id=data['node_id'],
            parent_ids=data['parent_ids'],
            hash=data.get('hash', '')
        )


class SyncAgent(EdgeAgent):
    """
    Agent responsible for BlockDAG synchronization.

    Features:
    - Local event storage (partial DAG)
    - Peer-to-peer synchronization
    - Conflict resolution
    - Event ordering guarantees
    - Works with intermittent connectivity
    """

    def __init__(self, config: Optional[SyncConfig] = None):
        super().__init__(config or SyncConfig())
        self.config: SyncConfig = self.config

        # Local DAG storage
        self._events: Dict[str, DAGEvent] = {}  # event_id -> event
        self._tips: Set[str] = set()  # Current DAG tips (events with no children)
        self._event_order: deque = deque(maxlen=self.config.max_local_events)

        # Peer tracking
        self._known_peers: Dict[str, Dict[str, Any]] = {}
        self._pending_sync: asyncio.Queue = asyncio.Queue()

        # Counters
        self._event_counter = 0
        self._sync_count = 0

    async def setup(self) -> bool:
        """Setup the sync agent."""
        self.logger.info("Setting up Sync Agent")

        # Initialize with genesis event if empty
        if not self._events:
            genesis = self._create_genesis_event()
            self._add_event(genesis)

        return True

    def _create_genesis_event(self) -> DAGEvent:
        """Create the genesis (root) event."""
        return DAGEvent(
            event_id=f"{self.config.node_id}-genesis",
            event_type='genesis',
            payload={'message': 'DAG initialized'},
            timestamp=datetime.utcnow().isoformat(),
            node_id=self.config.node_id,
            parent_ids=[]
        )

    def _generate_event_id(self) -> str:
        """Generate a unique event ID."""
        self._event_counter += 1
        return f"{self.config.node_id}-{self._event_counter:06d}"

    async def process(self) -> None:
        """Main processing loop."""
        # Process pending sync requests
        await self._process_sync_queue()

    async def _process_sync_queue(self) -> None:
        """Process pending synchronization requests."""
        processed = 0
        max_per_cycle = 5

        while not self._pending_sync.empty() and processed < max_per_cycle:
            try:
                sync_request = self._pending_sync.get_nowait()
                await self._handle_sync_request(sync_request)
                processed += 1
            except asyncio.QueueEmpty:
                break

    async def _handle_sync_request(self, request: Dict[str, Any]) -> None:
        """Handle a sync request from a peer."""
        request_type = request.get('type')

        if request_type == 'get_tips':
            # Peer requesting our current tips
            pass  # Would send tips to peer

        elif request_type == 'get_events':
            # Peer requesting specific events
            pass  # Would send requested events

        elif request_type == 'new_events':
            # Peer sending new events
            events = request.get('events', [])
            for event_data in events:
                event = DAGEvent.from_dict(event_data)
                if self._validate_event(event):
                    self._add_event(event)

    def _add_event(self, event: DAGEvent) -> bool:
        """Add an event to the local DAG."""
        if event.event_id in self._events:
            return False  # Already exists

        # Validate parent references
        for parent_id in event.parent_ids:
            if parent_id not in self._events and parent_id != '':
                self.logger.warning(f"Missing parent {parent_id} for event {event.event_id}")
                # In production, would request missing events from peers

        # Add to storage
        self._events[event.event_id] = event
        self._event_order.append(event.event_id)

        # Update tips
        self._tips.add(event.event_id)
        for parent_id in event.parent_ids:
            self._tips.discard(parent_id)

        self.logger.debug(f"Added event {event.event_id} ({event.event_type})")
        return True

    def _validate_event(self, event: DAGEvent) -> bool:
        """Validate an event before adding."""
        # Verify hash
        expected_hash = event._compute_hash()
        if event.hash and event.hash != expected_hash:
            self.logger.warning(f"Invalid hash for event {event.event_id}")
            return False

        return True

    def create_event(
        self,
        event_type: str,
        payload: Dict[str, Any]
    ) -> DAGEvent:
        """
        Create a new event in the DAG.

        Args:
            event_type: Type of event
            payload: Event data

        Returns:
            The created event
        """
        event = DAGEvent(
            event_id=self._generate_event_id(),
            event_type=event_type,
            payload=payload,
            timestamp=datetime.utcnow().isoformat(),
            node_id=self.config.node_id,
            parent_ids=list(self._tips)[:3]  # Reference up to 3 tips
        )

        self._add_event(event)
        return event

    async def record_device_event(
        self,
        device_id: str,
        event_type: str,
        data: Dict[str, Any]
    ) -> DAGEvent:
        """
        Record a device-related event.

        Args:
            device_id: ID of the device
            event_type: Type of event (register, state_change, etc.)
            data: Event data

        Returns:
            The created event
        """
        payload = {
            'device_id': device_id,
            **data
        }
        event = self.create_event(f"device_{event_type}", payload)

        await self._emit('event_created', event.to_dict())
        return event

    async def handle_message(self, message: AgentMessage) -> None:
        """Handle messages from other agents."""
        if message.message_type == 'device_discovered':
            # Record device discovery in DAG
            device = message.payload
            await self.record_device_event(
                device_id=device.get('id', 'unknown'),
                event_type='discovered',
                data={'hardware': device.get('hardware', ''), 'ip': device.get('ip', '')}
            )

        elif message.message_type == 'device_classified':
            # Record classification in DAG
            await self.record_device_event(
                device_id=message.payload.get('device_id', 'unknown'),
                event_type='classified',
                data=message.payload.get('result', {})
            )

        elif message.message_type == 'sync_request':
            await self._pending_sync.put(message.payload)

    async def cleanup(self) -> None:
        """Cleanup resources."""
        self.logger.info(f"Sync agent cleanup. Events in DAG: {len(self._events)}")

    def get_tips(self) -> List[str]:
        """Get current DAG tips."""
        return list(self._tips)

    def get_event(self, event_id: str) -> Optional[DAGEvent]:
        """Get a specific event by ID."""
        return self._events.get(event_id)

    def get_recent_events(self, limit: int = 50) -> List[DAGEvent]:
        """Get most recent events."""
        recent_ids = list(self._event_order)[-limit:]
        return [self._events[eid] for eid in recent_ids if eid in self._events]

    def get_events_by_type(self, event_type: str) -> List[DAGEvent]:
        """Get all events of a specific type."""
        return [e for e in self._events.values() if e.event_type == event_type]

    def get_device_history(self, device_id: str) -> List[DAGEvent]:
        """Get all events for a specific device."""
        return [
            e for e in self._events.values()
            if e.payload.get('device_id') == device_id
        ]

    def export_dag(self) -> Dict[str, Any]:
        """Export the local DAG as JSON-serializable dict."""
        return {
            'node_id': self.config.node_id,
            'tips': list(self._tips),
            'event_count': len(self._events),
            'events': [e.to_dict() for e in self._events.values()]
        }

    def get_status(self) -> Dict[str, Any]:
        """Get agent status with sync-specific info."""
        status = super().get_status()
        status.update({
            'total_events': len(self._events),
            'current_tips': len(self._tips),
            'sync_count': self._sync_count,
            'known_peers': len(self._known_peers)
        })
        return status
