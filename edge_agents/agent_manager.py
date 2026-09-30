"""
Edge Agent Manager - Orchestrates multiple agents on an edge node
Provides unified management and inter-agent communication.

Project: PINV01-24 - FIUNA
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Type
from datetime import datetime

from .base_agent import EdgeAgent, AgentConfig, AgentMessage, AgentState
from .discovery_agent import DiscoveryAgent, DiscoveryConfig
from .classification_agent import ClassificationAgent, ClassificationConfig
from .sync_agent import SyncAgent, SyncConfig


class EdgeAgentManager:
    """
    Manager for coordinating multiple edge agents.

    Features:
    - Unified agent lifecycle management
    - Inter-agent message routing
    - Status monitoring
    - Graceful startup/shutdown
    """

    def __init__(self, node_id: str = "edge-node-01"):
        """
        Initialize the agent manager.

        Args:
            node_id: Unique identifier for this edge node
        """
        self.node_id = node_id
        self.agents: Dict[str, EdgeAgent] = {}
        self._running = False
        self._message_router_task: Optional[asyncio.Task] = None

        # Setup logging
        self.logger = logging.getLogger(f"agent_manager.{node_id}")

    def add_agent(self, agent: EdgeAgent) -> None:
        """
        Add an agent to the manager.

        Args:
            agent: The agent to add
        """
        if agent.agent_id in self.agents:
            raise ValueError(f"Agent {agent.agent_id} already exists")

        # Set node ID in agent config
        agent.config.node_id = self.node_id

        self.agents[agent.agent_id] = agent
        self.logger.info(f"Added agent: {agent.agent_id} ({agent.agent_type})")

    def remove_agent(self, agent_id: str) -> bool:
        """
        Remove an agent from the manager.

        Args:
            agent_id: ID of the agent to remove

        Returns:
            True if removed, False if not found
        """
        if agent_id in self.agents:
            del self.agents[agent_id]
            self.logger.info(f"Removed agent: {agent_id}")
            return True
        return False

    def get_agent(self, agent_id: str) -> Optional[EdgeAgent]:
        """Get an agent by ID."""
        return self.agents.get(agent_id)

    def get_agents_by_type(self, agent_type: str) -> List[EdgeAgent]:
        """Get all agents of a specific type."""
        return [a for a in self.agents.values() if a.agent_type == agent_type]

    async def start_all(self) -> Dict[str, bool]:
        """
        Start all managed agents.

        Returns:
            Dictionary mapping agent IDs to start success
        """
        self.logger.info(f"Starting all agents on node {self.node_id}")
        self._running = True

        results = {}
        for agent_id, agent in self.agents.items():
            try:
                success = await agent.start()
                results[agent_id] = success
                if success:
                    self._setup_agent_routing(agent)
            except Exception as e:
                self.logger.error(f"Failed to start {agent_id}: {e}")
                results[agent_id] = False

        # Start message router
        self._message_router_task = asyncio.create_task(self._route_messages())

        return results

    async def stop_all(self) -> None:
        """Stop all managed agents."""
        self.logger.info("Stopping all agents")
        self._running = False

        # Stop message router
        if self._message_router_task:
            self._message_router_task.cancel()

        # Stop all agents
        for agent in self.agents.values():
            try:
                await agent.stop()
            except Exception as e:
                self.logger.error(f"Error stopping {agent.agent_id}: {e}")

    def _setup_agent_routing(self, agent: EdgeAgent) -> None:
        """Setup message routing for an agent."""
        # Route messages from this agent to others
        async def on_message_sent(message: AgentMessage):
            await self._deliver_message(message)

        agent.on('message_sent', on_message_sent)

        # Setup discovery -> classification pipeline
        if agent.agent_type == 'discovery':
            async def on_device_discovered(device: Dict[str, Any]):
                # Forward to classification agents
                for classifier in self.get_agents_by_type('classification'):
                    classifier.queue_classification(device)

                # Forward to sync agents
                msg = AgentMessage(
                    source_agent=agent.agent_id,
                    target_agent=None,
                    message_type='device_discovered',
                    payload=device
                )
                for sync in self.get_agents_by_type('sync'):
                    sync.receive_message(msg)

            agent.on('device_discovered', on_device_discovered)

        # Setup classification -> sync pipeline
        if agent.agent_type == 'classification':
            async def on_device_classified(data: Dict[str, Any]):
                msg = AgentMessage(
                    source_agent=agent.agent_id,
                    target_agent=None,
                    message_type='device_classified',
                    payload=data
                )
                for sync in self.get_agents_by_type('sync'):
                    sync.receive_message(msg)

            agent.on('device_classified', on_device_classified)

    async def _deliver_message(self, message: AgentMessage) -> None:
        """Deliver a message to target agent(s)."""
        if message.target_agent:
            # Targeted message
            target = self.agents.get(message.target_agent)
            if target:
                target.receive_message(message)
        else:
            # Broadcast
            for agent in self.agents.values():
                if agent.agent_id != message.source_agent:
                    agent.receive_message(message)

    async def _route_messages(self) -> None:
        """Background task for message routing."""
        while self._running:
            await asyncio.sleep(0.1)  # Small delay to prevent busy loop

    def get_status(self) -> Dict[str, Any]:
        """Get status of all agents."""
        return {
            'node_id': self.node_id,
            'running': self._running,
            'agent_count': len(self.agents),
            'agents': {
                agent_id: agent.get_status()
                for agent_id, agent in self.agents.items()
            },
            'timestamp': datetime.utcnow().isoformat()
        }

    @classmethod
    def create_default_setup(
        cls,
        node_id: str = "edge-node-01",
        discovery_port: int = 5000
    ) -> 'EdgeAgentManager':
        """
        Create a manager with default agent configuration.

        Args:
            node_id: Node identifier
            discovery_port: Port for HTTP discovery server

        Returns:
            Configured EdgeAgentManager
        """
        manager = cls(node_id=node_id)

        # Add Discovery Agent
        discovery_config = DiscoveryConfig(
            agent_id=f"{node_id}-discovery",
            node_id=node_id,
            listen_port=discovery_port
        )
        manager.add_agent(DiscoveryAgent(discovery_config))

        # Add Classification Agent
        classification_config = ClassificationConfig(
            agent_id=f"{node_id}-classifier",
            node_id=node_id,
            model_type='rule_based'  # Default to rule-based for edge
        )
        manager.add_agent(ClassificationAgent(classification_config))

        # Add Sync Agent
        sync_config = SyncConfig(
            agent_id=f"{node_id}-sync",
            node_id=node_id
        )
        manager.add_agent(SyncAgent(sync_config))

        return manager


async def run_edge_node(node_id: str = "edge-node-01", port: int = 5000):
    """
    Run a complete edge node with all agents.

    Args:
        node_id: Unique identifier for this node
        port: HTTP server port for discovery
    """
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Create and configure manager
    manager = EdgeAgentManager.create_default_setup(node_id, port)

    print(f"Starting edge node: {node_id}")
    print(f"Discovery server will listen on port {port}")

    try:
        # Start all agents
        results = await manager.start_all()
        print(f"Agent start results: {results}")

        # Print status
        status = manager.get_status()
        print(f"Node status: {status['agent_count']} agents running")

        # Keep running
        print("Edge node running. Press Ctrl+C to stop.")
        while True:
            await asyncio.sleep(60)
            # Periodic status update
            status = manager.get_status()
            for agent_id, agent_status in status['agents'].items():
                print(f"  {agent_id}: {agent_status['state']}")

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await manager.stop_all()
        print("Edge node stopped.")


# Entry point for running as script
if __name__ == "__main__":
    asyncio.run(run_edge_node())
