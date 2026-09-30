"""
Base Edge Agent - Abstract base class for all edge agents
Provides common functionality for agents running on edge devices.

Project: PINV01-24 - FIUNA
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass, field
from enum import Enum
import uuid


class AgentState(Enum):
    """Possible states for an edge agent."""
    INITIALIZED = "initialized"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class AgentConfig:
    """Configuration for an edge agent."""
    agent_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    agent_type: str = "base"
    node_id: str = "unknown"
    update_interval: float = 10.0  # seconds
    retry_interval: float = 5.0
    max_retries: int = 3
    log_level: str = "INFO"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentMessage:
    """Message format for inter-agent communication."""
    source_agent: str
    target_agent: Optional[str]  # None for broadcast
    message_type: str
    payload: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    message_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])


class EdgeAgent(ABC):
    """
    Abstract base class for edge agents.

    Edge agents are lightweight processes that run on edge devices
    and perform specific tasks like device discovery, classification,
    or data synchronization.

    Features:
    - Async operation for non-blocking execution
    - Event-based communication
    - Configurable update intervals
    - Graceful shutdown handling
    """

    def __init__(self, config: Optional[AgentConfig] = None):
        """
        Initialize the agent.

        Args:
            config: Agent configuration, or use defaults
        """
        self.config = config or AgentConfig()
        self.state = AgentState.INITIALIZED
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._callbacks: Dict[str, List[Callable]] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue()

        # Setup logging
        self.logger = logging.getLogger(f"agent.{self.config.agent_type}.{self.config.agent_id}")
        self.logger.setLevel(getattr(logging, self.config.log_level))

    @property
    def agent_id(self) -> str:
        """Get the agent's unique identifier."""
        return self.config.agent_id

    @property
    def agent_type(self) -> str:
        """Get the agent's type."""
        return self.config.agent_type

    @property
    def is_running(self) -> bool:
        """Check if the agent is currently running."""
        return self.state == AgentState.RUNNING

    @abstractmethod
    async def setup(self) -> bool:
        """
        Setup the agent before starting.
        Override in subclasses to initialize resources.

        Returns:
            True if setup successful, False otherwise
        """
        pass

    @abstractmethod
    async def process(self) -> None:
        """
        Main processing logic.
        Override in subclasses to implement agent behavior.
        Called repeatedly while agent is running.
        """
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """
        Cleanup resources before stopping.
        Override in subclasses to release resources.
        """
        pass

    async def start(self) -> bool:
        """
        Start the agent.

        Returns:
            True if started successfully
        """
        if self.state == AgentState.RUNNING:
            self.logger.warning("Agent already running")
            return False

        self.state = AgentState.STARTING
        self.logger.info(f"Starting agent {self.agent_id}")

        try:
            if not await self.setup():
                self.state = AgentState.ERROR
                return False

            self._stop_event.clear()
            self._task = asyncio.create_task(self._run_loop())
            self.state = AgentState.RUNNING
            self.logger.info(f"Agent {self.agent_id} started successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to start agent: {e}")
            self.state = AgentState.ERROR
            return False

    async def stop(self) -> None:
        """Stop the agent gracefully."""
        if self.state not in [AgentState.RUNNING, AgentState.PAUSED]:
            return

        self.state = AgentState.STOPPING
        self.logger.info(f"Stopping agent {self.agent_id}")

        self._stop_event.set()

        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except asyncio.TimeoutError:
                self.logger.warning("Agent stop timed out, cancelling")
                self._task.cancel()

        await self.cleanup()
        self.state = AgentState.STOPPED
        self.logger.info(f"Agent {self.agent_id} stopped")

    async def pause(self) -> None:
        """Pause the agent temporarily."""
        if self.state == AgentState.RUNNING:
            self.state = AgentState.PAUSED
            self.logger.info(f"Agent {self.agent_id} paused")

    async def resume(self) -> None:
        """Resume a paused agent."""
        if self.state == AgentState.PAUSED:
            self.state = AgentState.RUNNING
            self.logger.info(f"Agent {self.agent_id} resumed")

    async def _run_loop(self) -> None:
        """Main agent loop."""
        while not self._stop_event.is_set():
            if self.state == AgentState.PAUSED:
                await asyncio.sleep(0.5)
                continue

            try:
                await self.process()
                await self._process_messages()
            except Exception as e:
                self.logger.error(f"Error in process loop: {e}")
                await self._emit('error', {'error': str(e)})

            await asyncio.sleep(self.config.update_interval)

    async def _process_messages(self) -> None:
        """Process incoming messages from other agents."""
        while not self._message_queue.empty():
            try:
                message = self._message_queue.get_nowait()
                await self.handle_message(message)
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                self.logger.error(f"Error processing message: {e}")

    async def handle_message(self, message: AgentMessage) -> None:
        """
        Handle an incoming message from another agent.
        Override in subclasses for custom message handling.

        Args:
            message: The received message
        """
        self.logger.debug(f"Received message: {message.message_type} from {message.source_agent}")

    async def send_message(
        self,
        message_type: str,
        payload: Dict[str, Any],
        target_agent: Optional[str] = None
    ) -> AgentMessage:
        """
        Send a message to another agent or broadcast.

        Args:
            message_type: Type of message
            payload: Message data
            target_agent: Target agent ID, or None for broadcast

        Returns:
            The sent message
        """
        message = AgentMessage(
            source_agent=self.agent_id,
            target_agent=target_agent,
            message_type=message_type,
            payload=payload
        )
        await self._emit('message_sent', message)
        return message

    def receive_message(self, message: AgentMessage) -> None:
        """
        Queue a message for processing.

        Args:
            message: Message to queue
        """
        try:
            self._message_queue.put_nowait(message)
        except asyncio.QueueFull:
            self.logger.warning("Message queue full, dropping message")

    def on(self, event: str, callback: Callable) -> None:
        """
        Register a callback for an event.

        Events:
            - started: Agent started
            - stopped: Agent stopped
            - error: Error occurred
            - message_sent: Message was sent
            - device_discovered: Device was discovered (DiscoveryAgent)
            - device_classified: Device was classified (ClassificationAgent)
            - sync_completed: Sync completed (SyncAgent)
        """
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)

    async def _emit(self, event: str, data: Any = None) -> None:
        """Emit an event to registered callbacks."""
        for callback in self._callbacks.get(event, []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            except Exception as e:
                self.logger.error(f"Callback error for {event}: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Get current agent status."""
        return {
            'agent_id': self.agent_id,
            'agent_type': self.agent_type,
            'state': self.state.value,
            'node_id': self.config.node_id,
            'update_interval': self.config.update_interval,
            'timestamp': datetime.utcnow().isoformat()
        }
