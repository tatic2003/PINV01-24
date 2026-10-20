"""
BlockDAG Core - Non-linear Directed Acyclic Graph for event ordering
Designed for IoT environments with intermittent connectivity.

Features:
- Multiple parent references (non-linear)
- Tamper-evident hash links: every node commits to the *hash* of each parent,
  so rewriting any ancestor invalidates all of its descendants
- Partial synchronization by frontier (tips), complete even with concurrent branches
- Deterministic total order (logical height, then hash) identical on every replica
- Deterministic conflict resolution of device state (remove-wins)
- Lightweight for edge devices

Guarantees (and non-guarantees):
- Integrity: yes (hash chain).
- Authenticity: optional Ed25519 signatures. A node's ``signature`` signs its
  hash (which covers creator_id, payload and parent hashes). A DAG configured
  with ``trusted_keys`` verifies the signatures it receives; with
  ``require_signatures=True`` it rejects unsigned nodes and unknown creators.
  No key rotation or revocation yet.
- Convergence: replicas that hold the same set of nodes compute the same total
  order and the same resolved device state (merge is a set union).
- Consensus: none. There is no voting, leader or finality.

Project: PINV01-24 - FIUNA
"""

import hashlib
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional, List, Set, Tuple, Callable, Iterable
from dataclasses import dataclass, field
from collections import defaultdict, deque
import threading

from . import signing


# Event types that take part in device-state conflict resolution.
DEVICE_REGISTER = "device_register"
DEVICE_STATE_CHANGE = "state_change"
DEVICE_REMOVE = "device_remove"
_DEVICE_EVENTS = {DEVICE_REGISTER, DEVICE_STATE_CHANGE, DEVICE_REMOVE}


@dataclass
class DAGNode:
    """
    A node in the BlockDAG representing an event.

    Unlike blockchain blocks, DAG nodes can reference multiple parents,
    allowing for parallel event creation and better concurrency.

    ``parent_hashes`` maps each parent id to the hash that parent had when this
    node was created. It is part of the node's own hash, which is what makes
    the DAG tamper-evident: changing an ancestor changes its hash, and every
    descendant still commits to the old one.
    """
    node_id: str
    event_type: str
    payload: Dict[str, Any]
    timestamp: str
    creator_id: str  # ID of the node/device that created this event
    parent_ids: List[str]  # References to parent nodes (can be multiple)
    signature: str = ""  # Ed25519 signature (hex) of the node hash; not part of the hash
    parent_hashes: Dict[str, str] = field(default_factory=dict)
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
            'parent_ids': sorted(self.parent_ids),
            'parent_hashes': self.parent_hashes
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
            'parent_hashes': self.parent_hashes,
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
            parent_hashes=dict(data.get('parent_hashes', {})),
            _hash=data.get('hash', '')
        )
        return node


class BlockDAG:
    """
    Non-linear DAG-based ledger for distributed event ordering.

    Key concepts:
    - Nodes can have multiple parents (concurrent events)
    - Tips: Nodes with no children (latest events)
    - Height: 0 for roots, 1 + max(parent heights) otherwise (no wall clock)
    - Total order: sort by (height, hash); same node set => same order
    - Partial sync: frontier based delta, complete with concurrent branches
    - Conflict resolution: resolve_device_states() (remove-wins)
    """

    def __init__(
        self,
        node_id: str,
        signing_key: Optional[str] = None,
        trusted_keys: Optional[Dict[str, str]] = None,
        require_signatures: bool = False
    ):
        """
        Initialize the BlockDAG.

        Args:
            node_id: Unique identifier for this DAG instance
            signing_key: Ed25519 private key (hex) used to sign the nodes this
                instance creates. None = nodes are created unsigned.
            trusted_keys: ``{creator_id: public_key_hex}`` used to verify
                signatures. The public key of ``signing_key`` is added for
                ``node_id`` automatically.
            require_signatures: Reject nodes that are unsigned or whose
                creator has no trusted key.
        """
        self.node_id = node_id
        self._signing_key = signing_key
        self._trusted_keys: Dict[str, str] = dict(trusted_keys or {})
        self.require_signatures = require_signatures
        if signing_key is not None:
            own_public = signing.public_key_of(signing_key)
            if self._trusted_keys.setdefault(node_id, own_public) != own_public:
                raise ValueError(
                    f"trusted_keys has a different public key for {node_id!r} "
                    "than the one derived from signing_key"
                )
        self._nodes: Dict[str, DAGNode] = {}  # node_id -> DAGNode
        self._children: Dict[str, Set[str]] = defaultdict(set)  # parent_id -> child_ids
        self._tips: Set[str] = set()  # Nodes with no children
        self._height: Dict[str, int] = {}  # node_id -> logical height
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
            payload={"message": "DAG initialized", "version": "1.1"},
            timestamp=datetime.utcnow().isoformat(),
            creator_id=self.node_id,
            parent_ids=[]
        )
        self._sign_node(genesis)
        self._add_node_internal(genesis)
        return genesis

    def add_trusted_key(self, creator_id: str, public_key_hex: str) -> None:
        """Trust ``public_key_hex`` for nodes created by ``creator_id``."""
        self._trusted_keys[creator_id] = public_key_hex

    def _sign_node(self, node: DAGNode) -> None:
        """Sign the node with this instance's key, if it has one."""
        if self._signing_key is not None:
            node.signature = signing.sign(self._signing_key, bytes.fromhex(node.hash))

    def _signature_problem(self, node: DAGNode) -> Optional[str]:
        """Why the node's signature is not acceptable, or None if it is."""
        public_key = self._trusted_keys.get(node.creator_id)
        if not node.signature:
            return "Signature missing" if self.require_signatures else None
        if public_key is None:
            # Cannot be checked. Acceptable only when signatures are optional.
            return "Unknown creator" if self.require_signatures else None
        if not signing.verify(public_key, bytes.fromhex(node.hash), node.signature):
            return "Signature invalid"
        return None

    def _generate_node_id(self) -> str:
        """Generate a node ID that is guaranteed not to exist in this DAG."""
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        while True:
            self._node_counter += 1
            candidate = f"{self.node_id}:{timestamp}:{self._node_counter:06d}"
            if candidate not in self._nodes:
                return candidate

    def _add_node_internal(self, node: DAGNode) -> bool:
        """Internal method to add a node without validation."""
        if node.node_id in self._nodes:
            return False

        self._nodes[node.node_id] = node

        # Logical height from the parents that are present
        parent_heights = [self._height[p] for p in node.parent_ids if p in self._height]
        self._height[node.node_id] = (max(parent_heights) + 1) if parent_heights else 0

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

    def _rebuild_indexes(self) -> None:
        """
        Rebuild children, tips and heights from ``_nodes`` alone.

        The result does not depend on the order in which nodes were loaded
        (and therefore not on their timestamps).
        """
        self._children = defaultdict(set)
        self._tips = set()
        self._height = {}

        indegree: Dict[str, int] = {}
        for node_id, node in self._nodes.items():
            present = [p for p in node.parent_ids if p in self._nodes]
            indegree[node_id] = len(present)
            for parent_id in present:
                self._children[parent_id].add(node_id)

        self._tips = {nid for nid in self._nodes if not self._children.get(nid)}

        queue = deque(nid for nid, deg in indegree.items() if deg == 0)
        while queue:
            node_id = queue.popleft()
            node = self._nodes[node_id]
            parent_heights = [self._height[p] for p in node.parent_ids if p in self._height]
            self._height[node_id] = (max(parent_heights) + 1) if parent_heights else 0
            for child_id in self._children.get(node_id, ()):
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    queue.append(child_id)

        if len(self._height) != len(self._nodes):
            raise ValueError("DAG contains a cycle")

    def _restore_counter(self) -> None:
        """Continue the id counter after the highest one already used here."""
        prefix = f"{self.node_id}:"
        highest = 0
        for node_id in self._nodes:
            if node_id.startswith(prefix):
                tail = node_id.rsplit(":", 1)[-1]
                if tail.isdigit():
                    highest = max(highest, int(tail))
        self._node_counter = highest

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

        Raises:
            ValueError: if a given parent does not exist in this DAG
        """
        with self._lock:
            # Default to current tips if no parents specified
            if parent_ids is None:
                parent_ids = sorted(self._tips)[:5]  # Limit parent references

            unknown = [p for p in parent_ids if p not in self._nodes]
            if unknown:
                raise ValueError(f"Unknown parents: {unknown}")

            # Ensure we have at least one parent (except for genesis)
            if not parent_ids and self._nodes:
                parent_ids = [sorted(self._tips)[0]] if self._tips else []

            node = DAGNode(
                node_id=self._generate_node_id(),
                event_type=event_type,
                payload=payload,
                timestamp=datetime.utcnow().isoformat(),
                creator_id=self.node_id,
                parent_ids=parent_ids,
                parent_hashes={p: self._nodes[p].hash for p in parent_ids}
            )
            self._sign_node(node)

            if not self._add_node_internal(node):
                raise RuntimeError(f"Node id collision: {node.node_id}")
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

            # Check who created it (the signature covers the hash verified above)
            problem = self._signature_problem(node)
            if problem:
                return False, f"{problem}: {node.creator_id}"

            # Check parent references
            missing_parents = [p for p in node.parent_ids if p not in self._nodes]
            if missing_parents:
                return False, f"Missing parents: {missing_parents}"

            # Check the hash links: the node must commit to exactly the parents
            # it references, with the hashes those parents really have here.
            if set(node.parent_hashes) != set(node.parent_ids):
                return False, "Parent hash mismatch: parent_hashes does not match parent_ids"
            for parent_id in node.parent_ids:
                if node.parent_hashes[parent_id] != self._nodes[parent_id].hash:
                    return False, f"Parent hash mismatch: {parent_id}"

            self._add_node_internal(node)
            return True, "Node added successfully"

    def verify_integrity(self) -> List[Dict[str, str]]:
        """
        Check the whole DAG: node hashes, parent references and hash links.

        Returns:
            A list of problems; empty when the DAG is consistent. Each problem
            is ``{'node_id': ..., 'problem': ...}`` where problem is one of
            ``hash_mismatch``, ``missing_parent``, ``parent_set_mismatch`` or
            ``broken_link``.
        """
        problems: List[Dict[str, str]] = []
        with self._lock:
            for node_id, node in self._nodes.items():
                if not node.verify_hash():
                    problems.append({'node_id': node_id, 'problem': 'hash_mismatch'})
                if self._signature_problem(node):
                    problems.append({'node_id': node_id, 'problem': 'bad_signature'})
                if set(node.parent_hashes) != set(node.parent_ids):
                    problems.append({'node_id': node_id, 'problem': 'parent_set_mismatch'})
                for parent_id in node.parent_ids:
                    parent = self._nodes.get(parent_id)
                    if parent is None:
                        problems.append({'node_id': node_id, 'problem': 'missing_parent'})
                    elif node.parent_hashes.get(parent_id) != parent.hash:
                        problems.append({'node_id': node_id, 'problem': 'broken_link'})
        return problems

    def get_node(self, node_id: str) -> Optional[DAGNode]:
        """Get a node by ID."""
        return self._nodes.get(node_id)

    def node_ids(self) -> List[str]:
        """Ids of every node, sorted."""
        with self._lock:
            return sorted(self._nodes)

    def state_digest(self) -> str:
        """
        SHA-256 over the sorted ``node_id:hash`` pairs of every node.

        Two replicas hold the same DAG exactly when their digests are equal
        (including the hash of each node, so an equivocation, the same id with
        different content, gives different digests).
        """
        with self._lock:
            lines = sorted(f"{nid}:{node.hash}" for nid, node in self._nodes.items())
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()

    def get_tips(self) -> List[str]:
        """Get current DAG tips (latest nodes)."""
        with self._lock:
            return list(self._tips)

    def get_children(self, node_id: str) -> List[str]:
        """Get child node IDs for a given node."""
        return list(self._children.get(node_id, set()))

    def get_height(self, node_id: str) -> Optional[int]:
        """Logical height of a node (0 for roots), or None if unknown."""
        return self._height.get(node_id)

    def get_ancestors(self, node_id: str, max_depth: Optional[int] = None) -> Set[str]:
        """
        Get all ancestors of a node.

        Args:
            node_id: Starting node
            max_depth: Maximum number of generations to climb (None = no limit)

        Returns:
            Set of ancestor node IDs (the node itself is not included)
        """
        ancestors: Set[str] = set()
        level = [node_id]
        depth = 0

        while level and (max_depth is None or depth < max_depth):
            next_level = []
            for current in level:
                node = self._nodes.get(current)
                if node:
                    for parent_id in node.parent_ids:
                        if parent_id not in ancestors:
                            ancestors.add(parent_id)
                            next_level.append(parent_id)
            level = next_level
            depth += 1

        return ancestors

    def _ancestor_closure(self, node_ids: Iterable[str]) -> Set[str]:
        """The given nodes plus all of their ancestors (exact, no depth limit)."""
        seen: Set[str] = set()
        stack = [n for n in node_ids if n in self._nodes]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(p for p in self._nodes[current].parent_ids if p in self._nodes)
        return seen

    def _descendant_closure(self, node_ids: Iterable[str]) -> Set[str]:
        """The given nodes plus all of their descendants."""
        seen: Set[str] = set()
        stack = [n for n in node_ids if n in self._nodes]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self._children.get(current, ()))
        return seen

    def get_total_order(self) -> List[str]:
        """
        Deterministic total order of all nodes.

        Sorted by (logical height, hash). A child always has a greater height
        than its parents, so this is also a valid topological order. Two
        replicas holding the same nodes return exactly the same list,
        whatever the order in which they received them.
        """
        with self._lock:
            return sorted(
                self._nodes,
                key=lambda nid: (self._height.get(nid, 0), self._nodes[nid].hash)
            )

    def get_topological_order(self) -> List[str]:
        """
        Get nodes in topological order (respecting parent-child relationships).

        The order is the deterministic total order of :meth:`get_total_order`.

        Returns:
            List of node IDs in topological order
        """
        _t0 = time.perf_counter() if self._metrics_hook else 0

        result = self.get_total_order()

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

    def get_sync_data(
        self,
        since_node_id: Optional[str] = None,
        peer_tips: Optional[Iterable[str]] = None
    ) -> Dict[str, Any]:
        """
        Get data for synchronization with another node.

        The delta is "everything the peer cannot already have": all nodes that
        are not in the ancestor closure of the peer's frontier. Because a DAG
        only ever accepts a node after its parents, a peer that holds a node
        also holds all of its ancestors, so the delta is complete even when
        the DAG has concurrent branches.

        Args:
            since_node_id: A single node the peer is known to have (shorthand
                for ``peer_tips=[since_node_id]``)
            peer_tips: The peer's tips (as returned by its ``get_tips()``)

        Returns:
            Dictionary with sync data. With no (known) frontier, everything.
        """
        _t0 = time.perf_counter() if self._metrics_hook else 0

        with self._lock:
            frontier: List[str] = list(peer_tips or [])
            if since_node_id:
                frontier.append(since_node_id)
            known = self._ancestor_closure(frontier) if frontier else set()

            nodes_to_sync = [
                node.to_dict() for nid, node in self._nodes.items() if nid not in known
            ]

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
        # does not verify, or whose hash links do not match, is rejected at
        # once and never retried. A node whose id is already known but with a
        # different hash is an equivocation and is reported ("hash_conflict").
        pending: List[DAGNode] = []

        def _record_failure(reason: str) -> None:
            nonlocal failed
            failed += 1
            if "Missing parents" in reason:
                key = "missing_parents"
            elif "Signature" in reason or "Unknown creator" in reason:
                key = "bad_signature"
            elif "Hash conflict" in reason:
                key = "hash_conflict"
            elif "Parent hash" in reason:
                key = "broken_link"
            elif "Hash" in reason:
                key = "hash_failed"
            else:
                key = "other"
            _fail_reasons[key] = _fail_reasons.get(key, 0) + 1

        def _classify_known(node: DAGNode) -> None:
            nonlocal existing
            if self._nodes[node.node_id].hash == node.hash:
                existing += 1
            else:
                _record_failure(f"Hash conflict for known node {node.node_id}")

        for node_dict in sync_data.get('nodes', []):
            node = DAGNode.from_dict(node_dict)
            if node.node_id in self._nodes:
                _classify_known(node)
            else:
                pending.append(node)

        progress = True
        while pending and progress:
            progress = False
            still_pending: List[DAGNode] = []
            for node in pending:
                if node.node_id in self._nodes:
                    # Duplicate of a node added earlier in this same batch.
                    _classify_known(node)
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
            'total_nodes': len(self._nodes),
            # Rejected nodes by reason: missing_parents, bad_signature,
            # hash_conflict, broken_link, hash_failed, other.
            'failed_reasons': dict(_fail_reasons)
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

    # ------------------------------------------------------------------
    # Conflict resolution
    # ------------------------------------------------------------------

    def resolve_device_states(self) -> Dict[str, Dict[str, Any]]:
        """
        Deterministically resolve the state of every device (remove-wins).

        Rules, per ``device_id``:

        * Events are applied in the deterministic total order, so two replicas
          with the same nodes get the same result.
        * A ``device_remove`` supersedes every event that is not causally
          *after* it: both the events it saw (its ancestors) and the events
          concurrent with it. That is the remove-wins policy.
        * An event causally after *every* remove survives. A removed device
          comes back only through a ``device_register`` that is causally after
          every remove; ``state_change`` events with no such register are
          ignored.
        * Among surviving events, the last one in the total order wins
          (``info`` from the last register, ``state`` from the last
          state_change), which makes concurrent writes deterministic.

        Returns:
            ``{device_id: {...}}`` with ``status`` (``active`` or ``removed``),
            ``info``, ``state``, ``last_event``, ``removes`` (ids of the
            device_remove events) and ``overridden_by_remove`` (events that
            were concurrent with a remove and lost to it).
        """
        with self._lock:
            by_device: Dict[str, List[DAGNode]] = defaultdict(list)
            for node_id in self.get_total_order():
                node = self._nodes[node_id]
                if node.event_type in _DEVICE_EVENTS:
                    device_id = node.payload.get('device_id')
                    if device_id is not None:
                        by_device[device_id].append(node)

            resolved: Dict[str, Dict[str, Any]] = {}
            for device_id, events in by_device.items():
                removes = [e for e in events if e.event_type == DEVICE_REMOVE]
                others = [e for e in events if e.event_type != DEVICE_REMOVE]
                survivors = others
                overridden = 0

                if removes:
                    after_all: Optional[Set[str]] = None
                    concurrent: Set[str] = set()
                    for remove in removes:
                        descendants = self._descendant_closure([remove.node_id])
                        ancestors = self._ancestor_closure([remove.node_id])
                        after_all = descendants if after_all is None else after_all & descendants
                        concurrent |= {
                            e.node_id for e in others
                            if e.node_id not in descendants and e.node_id not in ancestors
                        }
                    survivors = [e for e in others if e.node_id in (after_all or set())]
                    # Only a register can bring a removed device back.
                    first_register = next(
                        (i for i, e in enumerate(survivors) if e.event_type == DEVICE_REGISTER),
                        None
                    )
                    survivors = survivors[first_register:] if first_register is not None else []
                    overridden = len(concurrent)

                info: Optional[Dict[str, Any]] = None
                state: Any = None
                for event in survivors:
                    if event.event_type == DEVICE_REGISTER:
                        info = {k: v for k, v in event.payload.items() if k != 'device_id'}
                        state = None
                    elif event.event_type == DEVICE_STATE_CHANGE:
                        state = event.payload.get('new_state', event.payload.get('state'))

                removed = bool(removes) and not survivors
                resolved[device_id] = {
                    'device_id': device_id,
                    'status': 'removed' if removed else 'active',
                    'info': info,
                    'state': state,
                    'last_event': survivors[-1].node_id if survivors else (
                        removes[-1].node_id if removes else None
                    ),
                    'removes': [r.node_id for r in removes],
                    'overridden_by_remove': overridden
                }

            return resolved

    def export(self) -> Dict[str, Any]:
        """Export the entire DAG."""
        return {
            'node_id': self.node_id,
            'version': '1.1',
            'tips': list(self._tips),
            'nodes': [node.to_dict() for node in self._nodes.values()]
        }

    @classmethod
    def import_from(
        cls,
        data: Dict[str, Any],
        verify: bool = True,
        signing_key: Optional[str] = None,
        trusted_keys: Optional[Dict[str, str]] = None,
        require_signatures: bool = False
    ) -> 'BlockDAG':
        """
        Import a DAG from exported data.

        Tips, children and heights are rebuilt from the nodes themselves, so
        neither the order of the list nor the (possibly skewed) timestamps
        matter.

        Args:
            data: Output of :meth:`export`
            verify: Check hashes, parents, hash links and (when configured)
                signatures. A DAG that fails raises ``ValueError`` instead of
                being loaded silently.
            signing_key, trusted_keys, require_signatures: as in the constructor
        """
        dag = cls(
            node_id=data['node_id'],
            signing_key=signing_key,
            trusted_keys=trusted_keys,
            require_signatures=require_signatures
        )
        dag._nodes.clear()

        for node_dict in data['nodes']:
            node = DAGNode.from_dict(node_dict)
            dag._nodes[node.node_id] = node

        dag._rebuild_indexes()
        dag._restore_counter()

        if verify:
            problems = dag.verify_integrity()
            if problems:
                raise ValueError(
                    f"DAG failed integrity verification ({len(problems)} problems, "
                    f"first: {problems[0]})"
                )

        return dag

    def __len__(self) -> int:
        """Return number of nodes in the DAG."""
        return len(self._nodes)

    def __contains__(self, node_id: str) -> bool:
        """Check if a node ID exists in the DAG."""
        return node_id in self._nodes
