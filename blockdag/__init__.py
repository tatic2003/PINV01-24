"""
BlockDAG Module - Non-linear ledger for distributed synchronization
Provides immutable event logging without linear blockchain constraints.

Project: PINV01-24 - FIUNA
"""

from .dag import BlockDAG, DAGNode
from .storage import DAGStorage
from .metrics import DAGMetricsCollector
from .signing import build_dag_options

__all__ = ['BlockDAG', 'DAGNode', 'DAGStorage', 'DAGMetricsCollector', 'build_dag_options']
