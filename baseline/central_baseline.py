"""
Centralized Baseline for ARGENCON 2026 Benchmark
=================================================
Replicates the same processing pipeline as the distributed architecture
(PnP-AI classification → SHACL validation → event logging) but operates
as a single centralized node without:
  - Edge agents (DiscoveryAgent, ClassificationAgent, SyncAgent)
  - Distributed BlockDAG synchronization between nodes
  - MQTT communication layer

This baseline enables a fair apples-to-apples comparison against the
distributed PINV01-24 architecture on latency, throughput, and
SHACL overhead metrics.

Project: PINV01-24 - FIUNA
Paper:   ARGENCON 2026
"""

import time
import json
import hashlib
import statistics
import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
import sys

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pnp_ai.device_classifier import DeviceClassifier, train_classifier_from_synthetic_data
from pnp_ai.dataset_generator import SyntheticDatasetGenerator
from semantic_layer.validator import SHACLValidator
from semantic_layer.rdf_store import RDFStore


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EventRecord:
    """Single event processed by the centralized baseline."""
    event_id: str
    device_id: str
    hardware_type: str
    timestamp_start: float        # perf_counter at pipeline entry
    timestamp_end: float          # perf_counter at pipeline exit
    t_classification_ms: float    # PnP-AI inference time
    t_shacl_ms: float             # SHACL validation time
    t_logging_ms: float           # Centralized log write time
    t_total_ms: float             # End-to-end latency
    classification_result: str    # Predicted device type
    classification_confidence: float
    shacl_conforms: bool
    shacl_violations: int


@dataclass
class BaselineResults:
    """Aggregate results from a full benchmark run."""
    num_events: int
    num_repetitions: int
    model_type: str

    # Latency stats (ms)
    latency_mean_ms: float
    latency_std_ms: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float

    # Throughput
    throughput_events_per_s: float

    # SHACL
    shacl_overhead_mean_ms: float
    shacl_overhead_std_ms: float
    shacl_conformance_rate: float

    # PnP-AI
    classification_mean_ms: float
    classification_std_ms: float
    classification_accuracy: float

    # Per-event records (optional, for CSV export)
    records: List[EventRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Centralized Baseline Node
# ---------------------------------------------------------------------------

class CentralizedBaseline:
    """
    Single-node centralized baseline that mirrors the distributed pipeline:
        device data → PnP-AI classify → SHACL validate → central log

    No DAG, no edge agents, no inter-node synchronization.
    All processing happens sequentially in one process.
    """

    def __init__(
        self,
        model_type: str = 'decision_tree',
        training_samples: int = 1000,
        seed: int = 42,
        feature_set: str = 'resource'
    ):
        self.model_type = model_type
        self.seed = seed
        # 'resource' = only self-reported quantities (deployable signal).
        # 'resource+name' also uses hardware-name flags and is circular with
        # the rule-based baseline; keep it only for comparison.
        self.feature_set = feature_set
        self._log: List[Dict[str, Any]] = []   # Simulated centralized event log

        print(f"[Baseline] Initializing centralized node (model={model_type})")

        # Train PnP-AI classifier
        t0 = time.perf_counter()
        self.classifier, train_metrics = train_classifier_from_synthetic_data(
            num_samples=training_samples,
            model_type=model_type,
            feature_set=feature_set
        )
        self._train_accuracy = train_metrics.get('accuracy', 0.0)
        print(f"[Baseline] Classifier trained in {(time.perf_counter()-t0)*1000:.1f} ms "
              f"(accuracy={self._train_accuracy:.4f})")

        # SHACL validator
        self.validator = SHACLValidator()
        print("[Baseline] SHACL validator ready")

        # RDF store (for building device graph — mirrors semantic_layer usage)
        self.rdf_store = RDFStore(load_ontology=True)
        print("[Baseline] RDF store ready")

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    def _build_device_graph(self, device: Dict[str, Any]):
        """
        Build a fresh single-device RDF graph for SHACL validation.
        Uses a throwaway RDFStore per event so the graph never accumulates,
        which would artificially inflate SHACL overhead over repetitions.
        This mirrors how a real centralized system validates each incoming
        message independently before persisting it.
        """
        from semantic_layer.rdf_store import RDFStore
        tmp_store = RDFStore(load_ontology=False)
        try:
            tmp_store.register_device(device)
        except Exception:
            pass
        return tmp_store.graph

    def _log_event(self, event_id: str, payload: Dict[str, Any]) -> float:
        """
        Simulate centralized event logging (in-memory append + SHA-256 hash).
        Returns elapsed time in ms.
        """
        t0 = time.perf_counter()
        record = {
            'event_id': event_id,
            'timestamp': datetime.utcnow().isoformat(),
            'hash': hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest(),
            'payload': payload
        }
        self._log.append(record)
        return (time.perf_counter() - t0) * 1000

    def _extract_features_for_inference(self, device: Dict[str, Any]) -> Dict[str, float]:
        """
        Extract features through the classifier itself.

        Delegating guarantees the vector matches what the model was trained
        on; a local copy of this logic silently drifted in the past.
        """
        return self.classifier._extract_features(device)

    def process_event(self, device: Dict[str, Any]) -> EventRecord:
        """
        Run one device through the full centralized pipeline and record timing.

        Pipeline:
            1. PnP-AI classification
            2. SHACL validation
            3. Central log write
        """
        event_id = f"central-{len(self._log):06d}"
        device_id = device.get('device_id', device.get('id', 'unknown'))
        hardware_type = device.get('hardware_type', device.get('hardware', 'unknown'))

        t_start = time.perf_counter()

        # --- Step 1: PnP-AI Classification ---
        # Use generator-compatible features to match training schema
        t0 = time.perf_counter()
        features = self._extract_features_for_inference(device)
        import numpy as np
        if self.classifier.feature_names is None:
            self.classifier.feature_names = sorted(features.keys())
        X = np.array([features[k] for k in self.classifier.feature_names]).reshape(1, -1)
        X_scaled = self.classifier.scaler.transform(X)
        proba = self.classifier.model.predict_proba(X_scaled)[0]
        pred_idx = int(np.argmax(proba))
        pred_label = self.classifier.label_encoder.inverse_transform([pred_idx])[0]
        pred_conf  = float(proba[pred_idx])

        # Wrap in a lightweight result object
        class _Result:
            def __init__(self, device_type, confidence):
                self.device_type = device_type
                self.confidence  = confidence

        classification = _Result(pred_label, pred_conf)
        t_classification_ms = (time.perf_counter() - t0) * 1000

        # --- Step 2: SHACL Validation ---
        t0 = time.perf_counter()
        graph = self._build_device_graph(device)
        shacl_result = self.validator.validate(graph)
        t_shacl_ms = (time.perf_counter() - t0) * 1000

        # --- Step 3: Centralized Log Write ---
        t0 = time.perf_counter()
        t_logging_ms = self._log_event(event_id, {
            'device_id': device_id,
            'hardware_type': hardware_type,
            'classification': classification.device_type,
            'shacl_conforms': shacl_result.is_valid
        })

        t_total_ms = (time.perf_counter() - t_start) * 1000

        return EventRecord(
            event_id=event_id,
            device_id=device_id,
            hardware_type=hardware_type,
            timestamp_start=t_start,
            timestamp_end=time.perf_counter(),
            t_classification_ms=t_classification_ms,
            t_shacl_ms=t_shacl_ms,
            t_logging_ms=t_logging_ms,
            t_total_ms=t_total_ms,
            classification_result=classification.device_type,
            classification_confidence=classification.confidence,
            shacl_conforms=shacl_result.is_valid,
            shacl_violations=len(shacl_result.violations)
        )

    # ------------------------------------------------------------------
    # Benchmark runner
    # ------------------------------------------------------------------

    def run_benchmark(
        self,
        num_events: int = 100,
        num_repetitions: int = 30,
        warmup_events: int = 10,
        seed: int = 42
    ) -> BaselineResults:
        """
        Run the centralized baseline benchmark.

        Args:
            num_events:       Events per repetition
            num_repetitions:  Number of independent repetitions (for stability)
            warmup_events:    Events to discard at start (JIT / cache warmup)
            seed:             Random seed for reproducible device generation

        Returns:
            BaselineResults with full statistics
        """
        print(f"\n[Baseline] Starting benchmark: {num_events} events × "
              f"{num_repetitions} repetitions (warmup={warmup_events})")

        generator = SyntheticDatasetGenerator(seed=seed)
        all_records: List[EventRecord] = []

        for rep in range(num_repetitions):
            # Fresh device batch per repetition
            devices = generator.generate_dataset(
                num_devices=num_events + warmup_events
            )

            rep_records = []
            for i, device in enumerate(devices):
                record = self.process_event(device)
                if i >= warmup_events:          # Discard warmup
                    rep_records.append(record)

            all_records.extend(rep_records)

            if (rep + 1) % 5 == 0 or rep == 0:
                rep_latencies = [r.t_total_ms for r in rep_records]
                print(f"  Rep {rep+1:02d}/{num_repetitions} | "
                      f"mean={statistics.mean(rep_latencies):.2f} ms | "
                      f"p95={sorted(rep_latencies)[int(len(rep_latencies)*0.95)]:.2f} ms")

        # --- Aggregate statistics ---
        latencies = [r.t_total_ms for r in all_records]
        shacl_times = [r.t_shacl_ms for r in all_records]
        clf_times = [r.t_classification_ms for r in all_records]
        sorted_lat = sorted(latencies)
        n = len(sorted_lat)

        conformance_rate = sum(1 for r in all_records if r.shacl_conforms) / len(all_records)

        # Throughput: total events / total wall-clock time
        total_wall_ms = sum(latencies)
        throughput = (len(all_records) / total_wall_ms) * 1000  # events/s

        results = BaselineResults(
            num_events=num_events,
            num_repetitions=num_repetitions,
            model_type=self.model_type,
            latency_mean_ms=statistics.mean(latencies),
            latency_std_ms=statistics.stdev(latencies),
            latency_p50_ms=sorted_lat[int(n * 0.50)],
            latency_p95_ms=sorted_lat[int(n * 0.95)],
            latency_p99_ms=sorted_lat[int(n * 0.99)],
            throughput_events_per_s=throughput,
            shacl_overhead_mean_ms=statistics.mean(shacl_times),
            shacl_overhead_std_ms=statistics.stdev(shacl_times),
            shacl_conformance_rate=conformance_rate,
            classification_mean_ms=statistics.mean(clf_times),
            classification_std_ms=statistics.stdev(clf_times),
            classification_accuracy=self._train_accuracy,
            records=all_records
        )

        self._print_summary(results)
        return results

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _print_summary(self, results: BaselineResults):
        print("\n" + "="*55)
        print("  CENTRALIZED BASELINE — BENCHMARK SUMMARY")
        print("="*55)
        print(f"  Model type         : {results.model_type}")
        print(f"  Total events       : {results.num_events * results.num_repetitions}")
        print(f"  Repetitions        : {results.num_repetitions}")
        print(f"  --- Latency (E2E) ---")
        print(f"  Mean               : {results.latency_mean_ms:.3f} ms")
        print(f"  Std Dev            : {results.latency_std_ms:.3f} ms")
        print(f"  P50                : {results.latency_p50_ms:.3f} ms")
        print(f"  P95                : {results.latency_p95_ms:.3f} ms")
        print(f"  P99                : {results.latency_p99_ms:.3f} ms")
        print(f"  --- Throughput ---")
        print(f"  Events/s           : {results.throughput_events_per_s:.1f}")
        print(f"  --- SHACL ---")
        print(f"  Overhead mean      : {results.shacl_overhead_mean_ms:.3f} ms")
        print(f"  Conformance rate   : {results.shacl_conformance_rate:.3f}")
        print(f"  --- PnP-AI ---")
        print(f"  Inference mean     : {results.classification_mean_ms:.3f} ms")
        print(f"  Train accuracy     : {results.classification_accuracy:.4f}")
        print("="*55)

    def save_csv(self, results: BaselineResults, output_path: str):
        """Export per-event records to CSV for analysis."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if not results.records:
            print("[Baseline] No records to export.")
            return

        fields = [
            'event_id', 'device_id', 'hardware_type',
            't_classification_ms', 't_shacl_ms', 't_logging_ms', 't_total_ms',
            'classification_result', 'classification_confidence',
            'shacl_conforms', 'shacl_violations'
        ]

        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in results.records:
                row = asdict(r)
                writer.writerow({k: row[k] for k in fields})

        print(f"[Baseline] Per-event CSV saved → {path}")

    def save_summary_json(self, results: BaselineResults, output_path: str):
        """Export aggregate summary to JSON."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        summary = {k: v for k, v in asdict(results).items() if k != 'records'}
        summary['generated_at'] = datetime.utcnow().isoformat()
        summary['architecture'] = 'centralized_baseline'
        summary['feature_set'] = self.feature_set

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2)

        print(f"[Baseline] Summary JSON saved → {path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Centralized Baseline Benchmark — ARGENCON 2026'
    )
    parser.add_argument('--model', choices=['decision_tree', 'random_forest'],
                        default='decision_tree', help='PnP-AI model type')
    parser.add_argument('--events', type=int, default=100,
                        help='Events per repetition')
    parser.add_argument('--repetitions', type=int, default=30,
                        help='Number of repetitions')
    parser.add_argument('--warmup', type=int, default=10,
                        help='Warmup events to discard')
    parser.add_argument('--seed', type=int, default=42,
                        help='Random seed')
    parser.add_argument('--output-dir', type=str, default='baseline/results',
                        help='Output directory for results')
    args = parser.parse_args()

    baseline = CentralizedBaseline(
        model_type=args.model,
        training_samples=1000,
        seed=args.seed
    )

    results = baseline.run_benchmark(
        num_events=args.events,
        num_repetitions=args.repetitions,
        warmup_events=args.warmup,
        seed=args.seed
    )

    out_dir = Path(args.output_dir)
    baseline.save_csv(results, str(out_dir / f'baseline_{args.model}_events.csv'))
    baseline.save_summary_json(results, str(out_dir / f'baseline_{args.model}_summary.json'))
