"""
BlockDAG Module - Non-linear ledger for distributed synchronization
Provides immutable event logging without linear blockchain constraints.

Project: PINV01-24 - FIUNA
"""

from .dag import BlockDAG, DAGNode
from .storage import DAGStorage
from .metrics import DAGMetricsCollector

__all__ = ['BlockDAG', 'DAGNode', 'DAGStorage', 'DAGMetricsCollector']
