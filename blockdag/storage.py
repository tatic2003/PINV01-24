"""
DAG Storage - Persistent storage for BlockDAG
Provides file-based and in-memory storage options.

Project: PINV01-24 - FIUNA
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
from abc import ABC, abstractmethod

from .dag import BlockDAG, DAGNode


class DAGStorageBackend(ABC):
    """Abstract base class for DAG storage backends."""

    @abstractmethod
    def save(self, dag: BlockDAG) -> bool:
        """Save the DAG to storage."""
        pass

    @abstractmethod
    def load(self, node_id: str, **dag_options) -> Optional[BlockDAG]:
        """Load a DAG from storage.

        ``dag_options`` (signing_key, trusted_keys, require_signatures) are
        passed to ``BlockDAG.import_from``.
        """
        pass

    @abstractmethod
    def exists(self, node_id: str) -> bool:
        """Check if a DAG exists in storage."""
        pass


class FileStorageBackend(DAGStorageBackend):
    """File-based storage for BlockDAG."""

    def __init__(self, base_path: str = "./dag_storage"):
        """
        Initialize file storage.

        Args:
            base_path: Directory for DAG files
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_filepath(self, node_id: str) -> Path:
        """Get file path for a DAG."""
        safe_id = node_id.replace('/', '_').replace(':', '_')
        return self.base_path / f"{safe_id}.json"

    def save(self, dag: BlockDAG) -> bool:
        """Save DAG to JSON file."""
        try:
            filepath = self._get_filepath(dag.node_id)
            data = dag.export()
            data['saved_at'] = datetime.utcnow().isoformat()

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

            return True
        except Exception as e:
            print(f"Error saving DAG: {e}")
            return False

    def load(self, node_id: str, **dag_options) -> Optional[BlockDAG]:
        """Load DAG from JSON file."""
        filepath = self._get_filepath(node_id)

        if not filepath.exists():
            return None

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            return BlockDAG.import_from(data, **dag_options)
        except Exception as e:
            print(f"Error loading DAG: {e}")
            return None

    def exists(self, node_id: str) -> bool:
        """Check if DAG file exists."""
        return self._get_filepath(node_id).exists()

    def list_dags(self) -> List[str]:
        """List all stored DAG node IDs."""
        dags = []
        for filepath in self.base_path.glob("*.json"):
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    dags.append(data.get('node_id', filepath.stem))
            except:
                pass
        return dags

    def delete(self, node_id: str) -> bool:
        """Delete a stored DAG."""
        filepath = self._get_filepath(node_id)
        if filepath.exists():
            os.remove(filepath)
            return True
        return False


class MemoryStorageBackend(DAGStorageBackend):
    """In-memory storage for BlockDAG (for testing/caching)."""

    def __init__(self):
        self._storage: Dict[str, Dict[str, Any]] = {}

    def save(self, dag: BlockDAG) -> bool:
        """Save DAG to memory."""
        self._storage[dag.node_id] = dag.export()
        return True

    def load(self, node_id: str, **dag_options) -> Optional[BlockDAG]:
        """Load DAG from memory."""
        data = self._storage.get(node_id)
        if data:
            return BlockDAG.import_from(data, **dag_options)
        return None

    def exists(self, node_id: str) -> bool:
        """Check if DAG exists in memory."""
        return node_id in self._storage

    def clear(self):
        """Clear all stored DAGs."""
        self._storage.clear()


class DAGStorage:
    """
    High-level DAG storage manager.

    Provides unified API for DAG persistence with support
    for multiple backends.
    """

    def __init__(
        self,
        backend: Optional[DAGStorageBackend] = None,
        auto_save_interval: int = 0,
        dag_options: Optional[Dict[str, Any]] = None
    ):
        """
        Initialize DAG storage.

        Args:
            backend: Storage backend (defaults to FileStorageBackend)
            auto_save_interval: Auto-save interval in seconds (0 = disabled)
            dag_options: Keyword arguments for every BlockDAG this storage
                creates or loads (signing_key, trusted_keys,
                require_signatures); see blockdag.signing.build_dag_options
        """
        self.backend = backend or FileStorageBackend()
        self.auto_save_interval = auto_save_interval
        self.dag_options: Dict[str, Any] = dict(dag_options or {})
        self._dags: Dict[str, BlockDAG] = {}

    def create(self, node_id: str) -> BlockDAG:
        """
        Create a new DAG or load existing.

        Args:
            node_id: Unique identifier for the DAG

        Returns:
            BlockDAG instance
        """
        # Check if already loaded
        if node_id in self._dags:
            return self._dags[node_id]

        # Try to load from storage. A stored DAG that cannot be loaded (for
        # example because it fails verification) is never replaced silently:
        # the next save would overwrite the evidence.
        if self.backend.exists(node_id):
            dag = self.backend.load(node_id, **self.dag_options)
            if dag:
                self._dags[node_id] = dag
                return dag
            raise RuntimeError(
                f"Stored DAG '{node_id}' exists but could not be loaded or verified; "
                "refusing to create a new one over it. Move or fix the stored file."
            )

        # Create new
        dag = BlockDAG(node_id, **self.dag_options)
        self._dags[node_id] = dag
        return dag

    def get(self, node_id: str) -> Optional[BlockDAG]:
        """Get a DAG by node ID."""
        return self._dags.get(node_id)

    def save(self, node_id: str) -> bool:
        """Save a specific DAG."""
        dag = self._dags.get(node_id)
        if dag:
            return self.backend.save(dag)
        return False

    def save_all(self) -> Dict[str, bool]:
        """Save all loaded DAGs."""
        return {
            node_id: self.backend.save(dag)
            for node_id, dag in self._dags.items()
        }

    def load(self, node_id: str) -> Optional[BlockDAG]:
        """Load a DAG from storage."""
        dag = self.backend.load(node_id, **self.dag_options)
        if dag:
            self._dags[node_id] = dag
        return dag

    def close(self, node_id: str, save: bool = True) -> bool:
        """
        Close a DAG (optionally saving first).

        Args:
            node_id: DAG to close
            save: Whether to save before closing

        Returns:
            True if successful
        """
        if node_id not in self._dags:
            return False

        if save:
            self.save(node_id)

        del self._dags[node_id]
        return True

    def close_all(self, save: bool = True):
        """Close all DAGs."""
        if save:
            self.save_all()
        self._dags.clear()

    def list_loaded(self) -> List[str]:
        """List currently loaded DAG node IDs."""
        return list(self._dags.keys())

    def get_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        stats = {
            'loaded_dags': len(self._dags),
            'dags': {}
        }

        for node_id, dag in self._dags.items():
            stats['dags'][node_id] = {
                'node_count': len(dag),
                'tips': len(dag.get_tips())
            }

        return stats
