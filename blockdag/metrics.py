"""
BlockDAG Performance Metrics Collector
Collects and visualizes performance metrics for academic reporting.

Metrics tracked:
- Merge success rate, failure reasons, avg nodes per sync
- Sync payload bytes, serialization time
- Tip count growth over time
- Topological sort time vs node count

Project: PINV01-24 - FIUNA
"""

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import numpy as np

try:
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.use('Agg')
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

from .dag import BlockDAG


class DAGMetricsCollector:
    """
    Collects performance metrics from a BlockDAG instance via hooks.

    Usage:
        dag = BlockDAG("node-1")
        collector = DAGMetricsCollector(dag)
        # ... perform operations ...
        summary = collector.get_merge_metrics()
        collector.plot_all("./output")
    """

    def __init__(self, dag: BlockDAG):
        self._dag = dag
        self._lock = threading.Lock()
        self._merge_records: List[Dict[str, Any]] = []
        self._sync_records: List[Dict[str, Any]] = []
        self._tip_snapshots: List[Dict[str, Any]] = []
        self._sort_records: List[Dict[str, Any]] = []

        # Store bound method reference for reliable identity checks
        self._hook_ref = self._handle_hook
        self._dag._metrics_hook = self._hook_ref

    def detach(self) -> None:
        """Remove hooks from the DAG, stopping collection."""
        if self._dag._metrics_hook is self._hook_ref:
            self._dag._metrics_hook = None

    def reset(self) -> None:
        """Clear all collected records."""
        with self._lock:
            self._merge_records.clear()
            self._sync_records.clear()
            self._tip_snapshots.clear()
            self._sort_records.clear()

    def _handle_hook(self, event_name: str, data: Dict[str, Any]) -> None:
        """Dispatch hook events to appropriate internal recorders."""
        with self._lock:
            timestamp = datetime.utcnow().isoformat()
            if event_name == "merge_complete":
                self._merge_records.append({"timestamp": timestamp, **data})
            elif event_name == "sync_export":
                self._sync_records.append({
                    "timestamp": timestamp,
                    "direction": "export",
                    **data
                })
            elif event_name == "node_added":
                self._tip_snapshots.append({"timestamp": timestamp, **data})
            elif event_name == "sort_complete":
                self._sort_records.append({"timestamp": timestamp, **data})

    # ------------------------------------------------------------------
    # Data accessor methods
    # ------------------------------------------------------------------

    def get_merge_metrics(self) -> Dict[str, Any]:
        """Return aggregate merge metrics."""
        with self._lock:
            records = list(self._merge_records)

        if not records:
            return {
                "total_merges": 0, "total_added": 0, "total_failed": 0,
                "total_existing": 0, "success_rate": 0.0,
                "avg_nodes_per_sync": 0.0, "failure_breakdown": {},
                "records": []
            }

        total_added = sum(r["added"] for r in records)
        total_failed = sum(r["failed"] for r in records)
        total_existing = sum(r["existing"] for r in records)
        total_offered = sum(r.get("nodes_offered", 0) for r in records)

        # Aggregate failure reasons
        failure_breakdown: Dict[str, int] = {}
        for r in records:
            for reason, count in r.get("failed_reasons", {}).items():
                failure_breakdown[reason] = failure_breakdown.get(reason, 0) + count

        success_rate = total_added / total_offered if total_offered > 0 else 0.0

        return {
            "total_merges": len(records),
            "total_added": total_added,
            "total_failed": total_failed,
            "total_existing": total_existing,
            "total_offered": total_offered,
            "success_rate": success_rate,
            "avg_nodes_per_sync": total_offered / len(records) if records else 0.0,
            "failure_breakdown": failure_breakdown,
            "records": records
        }

    def get_sync_metrics(self) -> Dict[str, Any]:
        """Return aggregate sync/serialization metrics."""
        with self._lock:
            records = list(self._sync_records)

        if not records:
            return {
                "total_exports": 0, "total_imports": 0,
                "total_bytes_exported": 0, "avg_serialization_sec": 0.0,
                "avg_bytes_per_node": 0.0, "records": []
            }

        exports = [r for r in records if r["direction"] == "export"]
        imports = [r for r in records if r["direction"] == "import"]

        total_bytes = sum(r.get("payload_bytes", 0) for r in exports)
        total_nodes = sum(r.get("node_count", 0) for r in exports)
        serialization_times = [r.get("serialization_sec", 0) for r in exports]

        return {
            "total_exports": len(exports),
            "total_imports": len(imports),
            "total_bytes_exported": total_bytes,
            "avg_serialization_sec": np.mean(serialization_times) if serialization_times else 0.0,
            "max_serialization_sec": max(serialization_times) if serialization_times else 0.0,
            "avg_bytes_per_node": total_bytes / total_nodes if total_nodes > 0 else 0.0,
            "records": records
        }

    def get_tip_metrics(self) -> Dict[str, Any]:
        """Return tip count history."""
        with self._lock:
            snapshots = list(self._tip_snapshots)

        if not snapshots:
            return {
                "snapshots": [], "current_tips": 0,
                "max_tips": 0, "min_tips": 0, "avg_tips": 0.0,
                "total_snapshots": 0
            }

        tip_counts = [s["tip_count"] for s in snapshots]

        return {
            "snapshots": snapshots,
            "current_tips": tip_counts[-1] if tip_counts else 0,
            "max_tips": max(tip_counts),
            "min_tips": min(tip_counts),
            "avg_tips": float(np.mean(tip_counts)),
            "total_snapshots": len(snapshots)
        }

    def get_sort_metrics(self) -> Dict[str, Any]:
        """Return topological sort timing data."""
        with self._lock:
            records = list(self._sort_records)

        if not records:
            return {
                "total_sorts": 0, "avg_elapsed_sec": 0.0,
                "max_elapsed_sec": 0.0,
                "node_count_vs_time": [], "records": []
            }

        elapsed_times = [r["elapsed_sec"] for r in records]

        return {
            "total_sorts": len(records),
            "avg_elapsed_sec": float(np.mean(elapsed_times)),
            "max_elapsed_sec": max(elapsed_times),
            "node_count_vs_time": [
                {"node_count": r["node_count"], "elapsed_sec": r["elapsed_sec"]}
                for r in records
            ],
            "records": records
        }

    def get_all_metrics(self) -> Dict[str, Any]:
        """Return all four metric categories combined."""
        return {
            "merge": self.get_merge_metrics(),
            "sync": self.get_sync_metrics(),
            "tips": self.get_tip_metrics(),
            "sort": self.get_sort_metrics()
        }

    # ------------------------------------------------------------------
    # Plot methods
    # ------------------------------------------------------------------

    def plot_merge_success_rate(
        self,
        output_path: Optional[str] = None,
        figsize: Tuple[int, int] = (10, 6)
    ) -> Optional[str]:
        """Stacked bar chart showing added/failed/existing per merge operation."""
        if not MATPLOTLIB_AVAILABLE:
            print("Warning: matplotlib not available, cannot generate plot")
            return None

        metrics = self.get_merge_metrics()
        records = metrics["records"]

        if not records:
            print("No merge records to plot")
            return None

        fig, ax = plt.subplots(figsize=figsize)

        indices = np.arange(len(records))
        added = [r["added"] for r in records]
        failed = [r["failed"] for r in records]
        existing = [r["existing"] for r in records]

        ax.bar(indices, added, label='Added', color='#2ecc71')
        ax.bar(indices, existing, bottom=added, label='Existing', color='#95a5a6')
        ax.bar(indices, failed,
               bottom=[a + e for a, e in zip(added, existing)],
               label='Failed', color='#e74c3c')

        ax.set_xlabel('Merge Operation')
        ax.set_ylabel('Number of Nodes')
        ax.set_title(f'BlockDAG Merge Performance\n'
                     f'Overall Success Rate: {metrics["success_rate"]:.1%}')
        ax.legend()
        ax.grid(True, alpha=0.3, axis='y')

        if len(indices) <= 20:
            ax.set_xticks(indices)
            ax.set_xticklabels([f'M{i+1}' for i in indices])

        plt.tight_layout()

        if output_path is None:
            output_path = 'merge_success_rate.png'

        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        return output_path

    def plot_sync_payload_size(
        self,
        output_path: Optional[str] = None,
        figsize: Tuple[int, int] = (10, 6)
    ) -> Optional[str]:
        """Dual-axis line chart: payload bytes and serialization time."""
        if not MATPLOTLIB_AVAILABLE:
            print("Warning: matplotlib not available, cannot generate plot")
            return None

        metrics = self.get_sync_metrics()
        records = [r for r in metrics["records"] if r["direction"] == "export"]

        if not records:
            print("No sync export records to plot")
            return None

        fig, ax1 = plt.subplots(figsize=figsize)

        indices = np.arange(len(records))
        payload_bytes = [r.get("payload_bytes", 0) for r in records]
        ser_times_ms = [r.get("serialization_sec", 0) * 1000 for r in records]

        # Left y-axis: payload bytes
        color1 = '#3498db'
        ax1.plot(indices, payload_bytes, 'o-', color=color1, label='Payload Size')
        ax1.fill_between(indices, payload_bytes, alpha=0.1, color=color1)
        ax1.set_xlabel('Sync Operation')
        ax1.set_ylabel('Payload Size (bytes)', color=color1)
        ax1.tick_params(axis='y', labelcolor=color1)

        # Right y-axis: serialization time
        ax2 = ax1.twinx()
        color2 = '#e67e22'
        ax2.plot(indices, ser_times_ms, 's-', color=color2, label='Serialization Time')
        ax2.set_ylabel('Serialization Time (ms)', color=color2)
        ax2.tick_params(axis='y', labelcolor=color2)

        ax1.set_title(f'Sync Payload Size and Serialization Time\n'
                      f'Avg: {metrics["avg_bytes_per_node"]:.0f} bytes/node, '
                      f'{metrics["avg_serialization_sec"]*1000:.2f} ms/export')
        ax1.grid(True, alpha=0.3)

        # Combined legend
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left')

        plt.tight_layout()

        if output_path is None:
            output_path = 'sync_payload_size.png'

        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        return output_path

    def plot_tip_growth(
        self,
        output_path: Optional[str] = None,
        figsize: Tuple[int, int] = (10, 6)
    ) -> Optional[str]:
        """Dual-axis chart: tip count and total node count over time."""
        if not MATPLOTLIB_AVAILABLE:
            print("Warning: matplotlib not available, cannot generate plot")
            return None

        metrics = self.get_tip_metrics()
        snapshots = metrics["snapshots"]

        if not snapshots:
            print("No tip snapshots to plot")
            return None

        fig, ax1 = plt.subplots(figsize=figsize)

        indices = np.arange(len(snapshots))
        tip_counts = [s["tip_count"] for s in snapshots]
        total_nodes = [s["total_nodes"] for s in snapshots]

        # Left y-axis: tip count
        color1 = '#3498db'
        ax1.fill_between(indices, tip_counts, alpha=0.3, color=color1)
        ax1.plot(indices, tip_counts, '-', color=color1, label='Tip Count', linewidth=1.5)
        ax1.set_xlabel('Node Addition (sequential)')
        ax1.set_ylabel('Tip Count', color=color1)
        ax1.tick_params(axis='y', labelcolor=color1)

        # Right y-axis: total nodes
        ax2 = ax1.twinx()
        color2 = '#e67e22'
        ax2.plot(indices, total_nodes, '-', color=color2, label='Total Nodes', linewidth=1.5)
        ax2.set_ylabel('Total Node Count', color=color2)
        ax2.tick_params(axis='y', labelcolor=color2)

        ax1.set_title(f'DAG Tip Count Growth Over Time\n'
                      f'Avg Tips: {metrics["avg_tips"]:.1f}, '
                      f'Max Tips: {metrics["max_tips"]}')
        ax1.grid(True, alpha=0.3)

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left')

        plt.tight_layout()

        if output_path is None:
            output_path = 'tip_growth.png'

        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        return output_path

    def plot_sort_performance(
        self,
        output_path: Optional[str] = None,
        figsize: Tuple[int, int] = (10, 6)
    ) -> Optional[str]:
        """Scatter plot: topological sort time vs node count."""
        if not MATPLOTLIB_AVAILABLE:
            print("Warning: matplotlib not available, cannot generate plot")
            return None

        metrics = self.get_sort_metrics()
        data = metrics["node_count_vs_time"]

        if not data:
            print("No sort records to plot")
            return None

        fig, ax = plt.subplots(figsize=figsize)

        node_counts = [d["node_count"] for d in data]
        elapsed_ms = [d["elapsed_sec"] * 1000 for d in data]

        ax.scatter(node_counts, elapsed_ms, color='#3498db', alpha=0.7, s=60, edgecolors='white')

        # Linear regression line
        if len(node_counts) >= 2:
            coeffs = np.polyfit(node_counts, elapsed_ms, 1)
            poly = np.poly1d(coeffs)
            x_line = np.linspace(min(node_counts), max(node_counts), 100)
            ax.plot(x_line, poly(x_line), '--', color='#e74c3c', alpha=0.7,
                    label=f'Linear fit (slope: {coeffs[0]:.4f} ms/node)')
            ax.legend()

        ax.set_xlabel('Node Count')
        ax.set_ylabel('Sort Time (ms)')
        ax.set_title(f'Topological Sort Time vs Node Count\n'
                     f'Avg: {metrics["avg_elapsed_sec"]*1000:.3f} ms, '
                     f'Max: {metrics["max_elapsed_sec"]*1000:.3f} ms')
        ax.grid(True, alpha=0.3)

        plt.tight_layout()

        if output_path is None:
            output_path = 'sort_performance.png'

        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        return output_path

    def plot_all(self, output_dir: str = '.') -> Dict[str, Optional[str]]:
        """Generate all four plots. Returns dict of {metric_name: file_path}."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        return {
            'merge_success_rate': self.plot_merge_success_rate(
                str(output_path / 'merge_success_rate.png')),
            'sync_payload_size': self.plot_sync_payload_size(
                str(output_path / 'sync_payload_size.png')),
            'tip_growth': self.plot_tip_growth(
                str(output_path / 'tip_growth.png')),
            'sort_performance': self.plot_sort_performance(
                str(output_path / 'sort_performance.png'))
        }


def generate_dag_performance_report(
    num_nodes: int = 500,
    num_syncs: int = 10,
    output_dir: str = '.',
    metrics: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Generate a synthetic DAG workload and produce performance evaluation plots.

    Args:
        num_nodes: Number of DAG nodes to generate per DAG
        num_syncs: Number of sync operations to simulate
        output_dir: Directory to save plots
        metrics: List of metrics to evaluate (default: all)

    Returns:
        Dictionary with all metrics and plot paths
    """
    import time
    import random

    if metrics is None:
        metrics = ['merge', 'sync', 'tips', 'sort']

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    random.seed(42)

    # Create two DAGs to simulate sync between nodes
    dag1 = BlockDAG("gateway-01")
    dag2 = BlockDAG("gateway-02")
    collector1 = DAGMetricsCollector(dag1)
    collector2 = DAGMetricsCollector(dag2)

    event_types = [
        "device_register", "state_change", "heartbeat",
        "config_update", "sensor_data", "alert"
    ]

    # Phase 1: Add events to both DAGs independently
    nodes_per_dag = num_nodes // 2
    print(f"\n  Phase 1: Adding {nodes_per_dag} events to each DAG...")

    for i in range(nodes_per_dag):
        dag1.add_event(
            random.choice(event_types),
            {"device_id": f"esp32-{random.randint(1, 20):02d}", "value": random.random()}
        )
        dag2.add_event(
            random.choice(event_types),
            {"device_id": f"rpi-{random.randint(1, 10):02d}", "value": random.random()}
        )

    # Phase 2: Perform sync operations between DAGs
    print(f"  Phase 2: Performing {num_syncs} sync operations...")

    for i in range(num_syncs):
        # Sync from dag1 to dag2
        sync_data = dag1.get_sync_data()
        dag2.merge_sync_data(sync_data)

        # Add some more events between syncs
        for _ in range(num_nodes // (num_syncs * 4)):
            dag1.add_event(
                random.choice(event_types),
                {"device_id": f"esp32-{random.randint(1, 20):02d}", "value": random.random()}
            )

        # Sync from dag2 to dag1
        sync_data = dag2.get_sync_data()
        dag1.merge_sync_data(sync_data)

    # Phase 3: Perform topological sorts at different DAG sizes
    print("  Phase 3: Measuring topological sort performance...")

    # Build a fresh DAG incrementally and sort at intervals
    sort_dag = BlockDAG("sort-test")
    sort_collector = DAGMetricsCollector(sort_dag)

    sort_points = list(range(10, num_nodes + 1, max(1, num_nodes // 20)))

    node_idx = 0
    for target_size in sort_points:
        while len(sort_dag) < target_size:
            sort_dag.add_event(
                random.choice(event_types),
                {"i": node_idx}
            )
            node_idx += 1
        sort_dag.get_topological_order()

    # Collect results
    results = {}

    if 'merge' in metrics:
        results["merge"] = collector2.get_merge_metrics()
        # Remove raw records from top-level summary for cleanliness
        results["merge"]["records"] = len(results["merge"]["records"])

    if 'sync' in metrics:
        results["sync"] = collector1.get_sync_metrics()
        results["sync"]["records"] = len(results["sync"]["records"])

    if 'tips' in metrics:
        results["tips"] = collector1.get_tip_metrics()
        results["tips"]["snapshots"] = len(results["tips"]["snapshots"])

    if 'sort' in metrics:
        results["sort"] = sort_collector.get_sort_metrics()

    # Generate plots
    print("  Phase 4: Generating plots...")
    plots = {}

    if 'merge' in metrics:
        plots["merge"] = collector2.plot_merge_success_rate(
            str(output_path / 'merge_success_rate.png'))

    if 'sync' in metrics:
        plots["sync"] = collector1.plot_sync_payload_size(
            str(output_path / 'sync_payload_size.png'))

    if 'tips' in metrics:
        plots["tips"] = collector1.plot_tip_growth(
            str(output_path / 'tip_growth.png'))

    if 'sort' in metrics:
        plots["sort"] = sort_collector.plot_sort_performance(
            str(output_path / 'sort_performance.png'))

    results["plots"] = plots

    # Cleanup
    collector1.detach()
    collector2.detach()
    sort_collector.detach()

    return results
