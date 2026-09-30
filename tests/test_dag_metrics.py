"""
Tests for BlockDAG Performance Metrics Collector.

Project: PINV01-24 - FIUNA
"""

import pytest
import threading
from pathlib import Path

from blockdag.dag import BlockDAG, DAGNode
from blockdag.metrics import DAGMetricsCollector


class TestDAGMetricsCollectorLifecycle:
    """Tests for hook attach/detach lifecycle."""

    def test_collector_attaches_hook(self):
        dag = BlockDAG("test")
        assert dag._metrics_hook is None
        collector = DAGMetricsCollector(dag)
        assert dag._metrics_hook is not None
        collector.detach()
        assert dag._metrics_hook is None

    def test_dag_works_without_collector(self):
        """DAG should work normally when no collector is attached."""
        dag = BlockDAG("test")
        assert dag._metrics_hook is None
        dag.add_event("test", {"data": "value"})
        order = dag.get_topological_order()
        assert len(order) == 2  # genesis + 1 event

    def test_reset_clears_records(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        dag.add_event("test", {})
        dag.get_topological_order()

        collector.reset()
        assert collector.get_sort_metrics()["total_sorts"] == 0
        assert collector.get_tip_metrics()["total_snapshots"] == 0


class TestMergeMetrics:
    """Tests for merge operation metrics."""

    def test_merge_metrics_collected(self):
        dag1 = BlockDAG("node-1")
        dag2 = BlockDAG("node-2")
        collector = DAGMetricsCollector(dag2)

        dag1.add_event("test", {"data": "value"})
        sync_data = dag1.get_sync_data()
        dag2.merge_sync_data(sync_data)

        metrics = collector.get_merge_metrics()
        assert metrics["total_merges"] == 1
        assert metrics["total_added"] >= 1

    def test_merge_success_rate(self):
        dag1 = BlockDAG("node-1")
        dag2 = BlockDAG("node-2")
        collector = DAGMetricsCollector(dag2)

        # Add events only to dag1
        for i in range(10):
            dag1.add_event("test", {"i": i})

        sync_data = dag1.get_sync_data()
        dag2.merge_sync_data(sync_data)

        metrics = collector.get_merge_metrics()
        assert metrics["success_rate"] > 0
        assert metrics["avg_nodes_per_sync"] > 0

    def test_merge_failure_tracking(self):
        """Missing parents should be tracked in failure breakdown."""
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        # Craft sync data with node referencing missing parent
        bad_node = DAGNode(
            node_id="bad-001",
            event_type="test",
            payload={},
            timestamp="2026-01-01T00:00:00",
            creator_id="other",
            parent_ids=["nonexistent-parent"]
        )

        bad_sync = {
            "source_node": "other",
            "nodes": [bad_node.to_dict()]
        }
        dag.merge_sync_data(bad_sync)

        metrics = collector.get_merge_metrics()
        assert metrics["total_failed"] >= 1
        assert "missing_parents" in metrics["failure_breakdown"]

    def test_existing_nodes_tracked(self):
        dag1 = BlockDAG("node-1")
        dag2 = BlockDAG("node-2")
        collector = DAGMetricsCollector(dag2)

        dag1.add_event("test", {"data": "value"})
        sync_data = dag1.get_sync_data()

        # Merge twice - second time should have existing nodes
        dag2.merge_sync_data(sync_data)
        dag2.merge_sync_data(sync_data)

        metrics = collector.get_merge_metrics()
        assert metrics["total_merges"] == 2
        assert metrics["total_existing"] > 0

    def test_empty_merge_metrics(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        metrics = collector.get_merge_metrics()
        assert metrics["total_merges"] == 0
        assert metrics["success_rate"] == 0.0


class TestSyncMetrics:
    """Tests for sync/serialization metrics."""

    def test_sync_export_metrics(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        dag.add_event("test", {"data": "x" * 100})
        dag.get_sync_data()

        sync = collector.get_sync_metrics()
        assert sync["total_exports"] == 1
        assert sync["records"][0]["payload_bytes"] > 0

    def test_sync_serialization_time(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        for i in range(50):
            dag.add_event("test", {"i": i})

        dag.get_sync_data()

        sync = collector.get_sync_metrics()
        assert sync["avg_serialization_sec"] >= 0
        assert sync["total_exports"] == 1

    def test_multiple_exports(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        for _ in range(3):
            dag.add_event("test", {})
            dag.get_sync_data()

        sync = collector.get_sync_metrics()
        assert sync["total_exports"] == 3

    def test_avg_bytes_per_node(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        for i in range(10):
            dag.add_event("test", {"i": i})

        dag.get_sync_data()

        sync = collector.get_sync_metrics()
        assert sync["avg_bytes_per_node"] > 0

    def test_empty_sync_metrics(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        metrics = collector.get_sync_metrics()
        assert metrics["total_exports"] == 0


class TestTipMetrics:
    """Tests for tip count tracking."""

    def test_tip_snapshots_recorded(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        for i in range(10):
            dag.add_event("test", {"i": i})

        tips = collector.get_tip_metrics()
        # genesis is created before collector attaches, so only 10 events recorded
        assert tips["total_snapshots"] >= 10

    def test_tip_statistics(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        for i in range(20):
            dag.add_event("test", {"i": i})

        tips = collector.get_tip_metrics()
        assert tips["max_tips"] >= 1
        assert tips["min_tips"] >= 1
        assert tips["avg_tips"] > 0
        assert tips["current_tips"] >= 1

    def test_empty_tip_metrics(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        # Genesis was created before collector attached, so no snapshots yet
        tips = collector.get_tip_metrics()
        assert tips["total_snapshots"] == 0


class TestSortMetrics:
    """Tests for topological sort timing."""

    def test_sort_timing_recorded(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        for i in range(20):
            dag.add_event("test", {"i": i})

        dag.get_topological_order()

        sort_metrics = collector.get_sort_metrics()
        assert sort_metrics["total_sorts"] == 1
        assert sort_metrics["records"][0]["elapsed_sec"] > 0
        assert sort_metrics["records"][0]["node_count"] == 21  # genesis + 20

    def test_multiple_sorts(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        for i in range(10):
            dag.add_event("test", {"i": i})
            dag.get_topological_order()

        sort_metrics = collector.get_sort_metrics()
        assert sort_metrics["total_sorts"] == 10

    def test_node_count_vs_time(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        for i in range(10):
            dag.add_event("test", {"i": i})
            dag.get_topological_order()

        sort_metrics = collector.get_sort_metrics()
        data = sort_metrics["node_count_vs_time"]
        assert len(data) == 10

        # Node count should increase
        assert data[-1]["node_count"] > data[0]["node_count"]

    def test_empty_sort_metrics(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        metrics = collector.get_sort_metrics()
        assert metrics["total_sorts"] == 0


class TestGetAllMetrics:
    """Tests for combined metrics."""

    def test_get_all_metrics_structure(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        all_m = collector.get_all_metrics()
        assert "merge" in all_m
        assert "sync" in all_m
        assert "tips" in all_m
        assert "sort" in all_m


class TestThreadSafety:
    """Tests for concurrent operations."""

    def test_concurrent_events(self):
        dag = BlockDAG("test")
        collector = DAGMetricsCollector(dag)

        def add_events():
            for i in range(50):
                dag.add_event("test", {
                    "thread": threading.current_thread().name,
                    "i": i
                })

        threads = [threading.Thread(target=add_events) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        tips = collector.get_tip_metrics()
        assert tips["total_snapshots"] >= 200  # 4 threads * 50


class TestPlots:
    """Tests for plot generation."""

    def test_plot_all_generates_files(self, tmp_path):
        dag1 = BlockDAG("node-1")
        dag2 = BlockDAG("node-2")
        collector = DAGMetricsCollector(dag2)

        # Add events and perform sync for merge metrics
        for i in range(20):
            dag1.add_event("test", {"i": i})

        sync_data = dag1.get_sync_data()
        dag2.merge_sync_data(sync_data)

        # Add more events for tip metrics
        for i in range(20):
            dag2.add_event("test", {"i": i})

        # Sort for sort metrics
        dag2.get_topological_order()

        # Need sync export metrics too
        dag2.get_sync_data()

        plots = collector.plot_all(str(tmp_path))

        for name, path in plots.items():
            if path is not None:
                assert Path(path).exists(), f"Plot {name} was not created"


class TestConvenienceFunction:
    """Tests for generate_dag_performance_report."""

    def test_end_to_end(self, tmp_path):
        from blockdag.metrics import generate_dag_performance_report

        results = generate_dag_performance_report(
            num_nodes=50,
            num_syncs=3,
            output_dir=str(tmp_path)
        )

        assert "merge" in results
        assert "sync" in results
        assert "tips" in results
        assert "sort" in results
        assert "plots" in results

    def test_selective_metrics(self, tmp_path):
        from blockdag.metrics import generate_dag_performance_report

        results = generate_dag_performance_report(
            num_nodes=30,
            num_syncs=2,
            output_dir=str(tmp_path),
            metrics=['merge', 'sort']
        )

        assert "merge" in results
        assert "sort" in results
        assert "sync" not in results
        assert "tips" not in results
