"""
BlockDAG Core - Non-linear Directed Acyclic Graph for event ordering
Designed for IoT environments with intermittent connectivity.

Features:
- Multiple parent references (non-linear)
- Cryptographic integrity
- Partial synchronization support
- Lightweight for edge devices

Project: PINV01-24 - FIUNA
"""

import hashlib
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional, List, Set, Tuple, Callable
from dataclasses import dataclass, field
from collections import defaultdict
import threading


@dataclass
class DAGNode:
    """
    A node in the BlockDAG representing an event.

    Unlike blockchain blocks, DAG nodes can reference multiple parents,
    allowing for parallel event creation and better concurrency.
    """
    node_id: str
    event_type: str
    payload: Dict[str, Any]
    timestamp: str
    creator_id: str  # ID of the node/device that created this event
    parent_ids: List[str]  # References to parent nodes (can be multiple)
    signature: str = ""  # Optional signature for authenticity
    _hash: str = field(default="", repr=False)

    def __post_init__(self):
        if not self._hash:
            self._hash = self.compute_hash()

    @property
    def hash(self) -> str:
        """Get the node's hash."""
        return self._hash

    def compute_hash(self) -> str:
        """Compute the cryptographic hash of the node."""
        content = {
            'node_id': self.node_id,
            'event_type': self.event_type,
            'payload': self.payload,
            'timestamp': self.timestamp,
            'creator_id': self.creator_id,
            'parent_ids': sorted(self.parent_ids)
        }
        serialized = json.dumps(content, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def verify_hash(self) -> bool:
        """Verify the node's hash is correct."""
        return self._hash == self.compute_hash()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'node_id': self.node_id,
            'event_type': self.event_type,
            'payload': self.payload,
            'timestamp': self.timestamp,
            'creator_id': self.creator_id,
            'parent_ids': self.parent_ids,
            'signature': self.signature,
            'hash': self._hash
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DAGNode':
        """Create a DAGNode from dictionary."""
        node = cls(
            node_id=data['node_id'],
            event_type=data['event_type'],
            payload=data['payload'],
            timestamp=data['timestamp'],
            creator_id=data['creator_id'],
            parent_ids=data['parent_ids'],
            signature=data.get('signature', ''),
            _hash=data.get('hash', '')
        )
        return node


class BlockDAG:
    """
    Non-linear DAG-based ledger for distributed event ordering.

    Key concepts:
    - Nodes can have multiple parents (concurrent events)
    - Tips: Nodes with no children (latest events)
    - Partial sync: Can work with incomplete DAG
    - Topological ordering for event sequencing
    """

    def __init__(self, node_id: str):
        """
        Initialize the BlockDAG.

        Args:
            node_id: Unique identifier for this DAG instance
        """
        self.node_id = node_id
        self._nodes: Dict[str, DAGNode] = {}  # node_id -> DAGNode
        self._children: Dict[str, Set[str]] = defaultdict(set)  # parent_id -> child_ids
        self._tips: Set[str] = set()  # Nodes with no children
        self._node_counter = 0
        self._lock = threading.RLock()
        self._metrics_hook: Optional[Callable] = None  # Optional metrics callback

        # Initialize with genesis node
        self._create_genesis()

    def _create_genesis(self) -> DAGNode:
        """Create the genesis (root) node."""
        genesis = DAGNode(
            node_id=f"{self.node_id}:genesis",
            event_type="genesis",
            payload={"message": "DAG initialized", "version": "1.0"},
            timestamp=datetime.utcnow().isoformat(),
            creator_id=self.node_id,
            parent_ids=[]
        )
        self._add_node_internal(genesis)
        return genesis

    def _generate_node_id(self) -> str:
        """Generate a unique node ID."""
        self._node_counter += 1
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        return f"{self.node_id}:{timestamp}:{self._node_counter:06d}"

    def _add_node_internal(self, node: DAGNode) -> bool:
        """Internal method to add a node without validation."""
        if node.node_id in self._nodes:
            return False

        self._nodes[node.node_id] = node

        # Update children references
        for parent_id in node.parent_ids:
            self._children[parent_id].add(node.node_id)
            # Parent is no longer a tip
            self._tips.discard(parent_id)

        # New node is a tip
        self._tips.add(node.node_id)

        if self._metrics_hook is not None:
            self._metrics_hook("node_added", {
                "tip_count": len(self._tips),
                "total_nodes": len(self._nodes),
                "event_type": node.event_type
            })

        return True

    def add_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        parent_ids: Optional[List[str]] = None
    ) -> DAGNode:
        """
        Add a new event to the DAG.

        Args:
            event_type: Type of event (e.g., 'device_register', 'state_change')
            payload: Event data
            parent_ids: Parent node IDs, or None to use current tips

        Returns:
            The created DAGNode
        """
        with self._lock:
            # Default to current tips if no parents specified
            if parent_ids is None:
                parent_ids = list(self._tips)[:5]  # Limit parent references

            # Ensure we have at least one parent (except for genesis)
            if not parent_ids and self._nodes:
                parent_ids = [list(self._tips)[0]] if self._tips else []

            node = DAGNode(
                node_id=self._generate_node_id(),
                event_type=event_type,
                payload=payload,
                timestamp=datetime.utcnow().isoformat(),
                creator_id=self.node_id,
                parent_ids=parent_ids
            )

            self._add_node_internal(node)
            return node

    def add_external_node(self, node: DAGNode) -> Tuple[bool, str]:
        """
        Add an externally created node (from synchronization).

        Args:
            node: The node to add

        Returns:
            Tuple of (success, message)
        """
        with self._lock:
            # Check if already exists
            if node.node_id in self._nodes:
                return False, "Node already exists"

            # Verify hash integrity
            if not node.verify_hash():
                return False, "Hash verification failed"

            # Check parent references
            missing_parents = [p for p in node.parent_ids if p not in self._nodes]
            if missing_parents:
                return False, f"Missing parents: {missing_parents}"

            self._add_node_internal(node)
            return True, "Node added successfully"

    def get_node(self, node_id: str) -> Optional[DAGNode]:
        """Get a node by ID."""
        return self._nodes.get(node_id)

    def get_tips(self) -> List[str]:
        """Get current DAG tips (latest nodes)."""
        with self._lock:
            return list(self._tips)

    def get_children(self, node_id: str) -> List[str]:
        """Get child node IDs for a given node."""
        return list(self._children.get(node_id, set()))

    def get_ancestors(self, node_id: str, max_depth: int = 100) -> Set[str]:
        """
        Get all ancestors of a node.

        Args:
            node_id: Starting node
            max_depth: Maximum traversal depth

        Returns:
            Set of ancestor node IDs
        """
        ancestors = set()
        to_visit = [node_id]
        depth = 0

        while to_visit and depth < max_depth:
            current = to_visit.pop(0)
            node = self._nodes.get(current)
            if node:
                for parent_id in node.parent_ids:
                    if parent_id not in ancestors:
                        ancestors.add(parent_id)
                        to_visit.append(parent_id)
            depth += 1

        return ancestors

    def get_topological_order(self) -> List[str]:
        """
        Get nodes in topological order (respecting parent-child relationships).

        Returns:
            List of node IDs in topological order
        """
        _t0 = time.perf_counter() if self._metrics_hook else 0

        # Kahn's algorithm
        in_degree = {node_id: len(node.parent_ids) for node_id, node in self._nodes.items()}
        queue = [node_id for node_id, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            node_id = queue.pop(0)
            result.append(node_id)

            for child_id in self._children.get(node_id, set()):
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    queue.append(child_id)

        if self._metrics_hook is not None:
            self._metrics_hook("sort_complete", {
                "node_count": len(self._nodes),
                "result_length": len(result),
                "elapsed_sec": time.perf_counter() - _t0
            })

        return result

    def get_events_by_type(self, event_type: str) -> List[DAGNode]:
        """Get all nodes of a specific event type."""
        return [node for node in self._nodes.values() if node.event_type == event_type]

    def get_events_by_creator(self, creator_id: str) -> List[DAGNode]:
        """Get all nodes created by a specific creator."""
        return [node for node in self._nodes.values() if node.creator_id == creator_id]

    def get_events_since(self, timestamp: str) -> List[DAGNode]:
        """Get all nodes created since a given timestamp."""
        return [node for node in self._nodes.values() if node.timestamp >= timestamp]

    def get_missing_nodes(self, known_node_ids: Set[str]) -> Set[str]:
        """
        Find nodes that are referenced but not present locally.
        Useful for synchronization.

        Args:
            known_node_ids: Set of node IDs we know about

        Returns:
            Set of missing node IDs
        """
        missing = set()
        for node in self._nodes.values():
            for parent_id in node.parent_ids:
                if parent_id not in self._nodes and parent_id not in known_node_ids:
                    missing.add(parent_id)
        return missing

    def get_sync_data(self, since_node_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get data for synchronization with another node.

        Args:
            since_node_id: Only include nodes after this one (optional)

        Returns:
            Dictionary with sync data
        """
        _t0 = time.perf_counter() if self._metrics_hook else 0

        if since_node_id and since_node_id in self._nodes:
            # Get descendants of the given node
            descendants = set()
            to_check = list(self._children.get(since_node_id, set()))
            while to_check:
                current = to_check.pop()
                if current not in descendants:
                    descendants.add(current)
                    to_check.extend(self._children.get(current, set()))

            nodes_to_sync = [self._nodes[nid].to_dict() for nid in descendants]
        else:
            nodes_to_sync = [node.to_dict() for node in self._nodes.values()]

        result = {
            'source_node': self.node_id,
            'tips': list(self._tips),
            'node_count': len(self._nodes),
            'nodes': nodes_to_sync
        }

        if self._metrics_hook is not None:
            payload_bytes = len(json.dumps(nodes_to_sync).encode('utf-8'))
            self._metrics_hook("sync_export", {
                "node_count": len(nodes_to_sync),
                "payload_bytes": payload_bytes,
                "serialization_sec": time.perf_counter() - _t0
            })

        return result

    def merge_sync_data(self, sync_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge synchronization data from another node.

        Args:
            sync_data: Data from get_sync_data()

        Returns:
            Summary of merge results
        """
        _t0 = time.perf_counter() if self._metrics_hook else 0
        added = 0
        failed = 0
        existing = 0
        _fail_reasons: Dict[str, int] = {}

        # A node can only be accepted once all of its parents are present, so
        # the outcome must not depend on the order in which the sender listed
        # the nodes. Nodes whose parents have not arrived yet are held back
        # and retried until a full pass makes no progress. Only nodes whose
        # parents are genuinely absent from both this DAG and the offered
        # batch are reported as failed ("missing_parents"). A node whose hash
        # does not verify is rejected at once and never retried.
        pending: List[DAGNode] = []
        for node_dict in sync_data.get('nodes', []):
            node = DAGNode.from_dict(node_dict)
            if node.node_id in self._nodes:
                existing += 1
            else:
                pending.append(node)

        def _record_failure(reason: str) -> None:
            nonlocal failed
            failed += 1
            if self._metrics_hook is not None:
                if "Missing parents" in reason:
                    key = "missing_parents"
                elif "Hash" in reason:
                    key = "hash_failed"
                else:
                    key = "other"
                _fail_reasons[key] = _fail_reasons.get(key, 0) + 1

        progress = True
        while pending and progress:
            progress = False
            still_pending: List[DAGNode] = []
            for node in pending:
                if node.node_id in self._nodes:
                    # Duplicate of a node added earlier in this same batch.
                    existing += 1
                    progress = True
                    continue
                success, reason = self.add_external_node(node)
                if success:
                    added += 1
                    progress = True
                elif "Missing parents" in reason:
                    still_pending.append(node)
                else:
                    _record_failure(reason)
                    progress = True
            pending = still_pending

        for node in pending:
            _record_failure(
                "Missing parents: "
                f"{[p for p in node.parent_ids if p not in self._nodes]}"
            )

        result = {
            'added': added,
            'failed': failed,
            'existing': existing,
            'total_nodes': len(self._nodes)
        }

        if self._metrics_hook is not None:
            self._metrics_hook("merge_complete", {
                **result,
                "source_node": sync_data.get("source_node", "unknown"),
                "nodes_offered": len(sync_data.get("nodes", [])),
                "failed_reasons": _fail_reasons,
                "elapsed_sec": time.perf_counter() - _t0
            })

        return result

    def export(self) -> Dict[str, Any]:
        """Export the entire DAG."""
        return {
            'node_id': self.node_id,
            'version': '1.0',
            'tips': list(self._tips),
            'nodes': [node.to_dict() for node in self._nodes.values()]
        }

    @classmethod
    def import_from(cls, data: Dict[str, Any]) -> 'BlockDAG':
        """Import a DAG from exported data."""
        dag = cls(node_id=data['node_id'])
        dag._nodes.clear()
        dag._tips.clear()
        dag._children.clear()

        # Sort by timestamp to ensure proper order
        nodes = sorted(data['nodes'], key=lambda x: x['timestamp'])

        for node_dict in nodes:
            node = DAGNode.from_dict(node_dict)
            dag._add_node_internal(node)

        return dag

    def __len__(self) -> int:
        """Return number of nodes in the DAG."""
        return len(self._nodes)

    def __contains__(self, node_id: str) -> bool:
        """Check if a node ID exists in the DAG."""
        return node_id in self._nodes
