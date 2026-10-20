"""
Benchmarks the distributed PINV01-24 architecture:
    device data → PnP-AI classify → SHACL validate → BlockDAG add_event

Simulates N independent edge nodes each running their own pipeline
and BlockDAG instance, mirroring real distributed deployment.

Run from PINV01-24-main/:
    python baseline/run_distributed_benchmark.py [--nodes 3] [--events 100] [--repetitions 30]

Output CSVs are named so run_benchmark.py can load them automatically:
    baseline/results/distributed_decision_tree_events.csv
    baseline/results/distributed_random_forest_events.csv

Project: PINV01-24 - FIUNA
"""

import time
import json
import csv
import statistics
import sys
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Dict, Any, List
import argparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from pnp_ai.device_classifier import train_classifier_from_synthetic_data
from pnp_ai.dataset_generator import SyntheticDatasetGenerator
from semantic_layer.rdf_store import RDFStore
from semantic_layer.validator import SHACLValidator
from blockdag.dag import BlockDAG
from blockdag import signing
from baseline.central_baseline import (make_generator, training_metadata, DEFAULT_FEATURE_SET,
                                       DEFAULT_TRAINING_SAMPLES, DEFAULT_TRAINING_SOURCE)

import numpy as np


# ---------------------------------------------------------------------------
# Per-event record (same schema as baseline for easy comparison)
# ---------------------------------------------------------------------------

@dataclass
class DistributedEventRecord:
    event_id: str
    node_id: str
    device_id: str
    hardware_type: str
    t_classification_ms: float
    t_shacl_ms: float
    t_dag_ms: float
    t_total_ms: float
    classification_result: str
    classification_confidence: float
    shacl_conforms: bool
    shacl_violations: int


# ---------------------------------------------------------------------------
# Single edge node
# ---------------------------------------------------------------------------

class EdgeNode:
    """
    Simulates one edge node running the full distributed pipeline:
        PnP-AI → SHACL → BlockDAG

    Each node has its own classifier, validator, and DAG instance.
    """

    def __init__(self, node_id: str, model_type: str, training_samples: int = DEFAULT_TRAINING_SAMPLES,
                 feature_set: str = DEFAULT_FEATURE_SET, training_source: str = DEFAULT_TRAINING_SOURCE,
                 sign: bool = False, seed: int = 42):
        self.node_id = node_id
        self.model_type = model_type
        self.feature_set = feature_set

        # Train classifier
        self.classifier, train_metrics = train_classifier_from_synthetic_data(
            num_samples=training_samples,
            model_type=model_type,
            feature_set=feature_set,
            generator=make_generator(training_source, seed)
        )
        self._train_accuracy = train_metrics.get('accuracy', 0.0)

        # SHACL validator
        self.validator = SHACLValidator()

        # BlockDAG — each node has its own instance. With sign=True every node
        # it creates is signed with Ed25519, as the server does with --dag-key-file.
        self.dag = BlockDAG(node_id=node_id,
                            signing_key=signing.generate_private_key() if sign else None)

        # Feature names (set on first inference)
        self._feature_names = None

    def _extract_features(self, device: Dict[str, Any]) -> Dict[str, float]:
        """
        Extract features through the classifier itself.

        Delegating guarantees the vector matches what the model was trained
        on; a local copy of this logic silently drifted in the past.
        """
        return self.classifier._extract_features(device)

    def process_event(self, device: Dict[str, Any], event_idx: int) -> DistributedEventRecord:
        """
        Full distributed pipeline for one device event.
        """
        event_id      = f"{self.node_id}-{event_idx:06d}"
        device_id     = device.get('device_id', device.get('id', 'unknown'))
        hardware_type = device.get('hardware_type', device.get('hardware', 'unknown'))

        t_start = time.perf_counter()

        # --- Step 1: PnP-AI Classification (edge-local) ---
        t0 = time.perf_counter()
        features = self._extract_features(device)
        if self.classifier.feature_names is None:
            self.classifier.feature_names = sorted(features.keys())
        X = np.array([features[k] for k in self.classifier.feature_names]).reshape(1, -1)
        X_scaled = self.classifier.scaler.transform(X)
        proba     = self.classifier.model.predict_proba(X_scaled)[0]
        pred_idx  = int(np.argmax(proba))
        pred_type = self.classifier.label_encoder.inverse_transform([pred_idx])[0]
        pred_conf = float(proba[pred_idx])
        t_classification_ms = (time.perf_counter() - t0) * 1000

        # --- Step 2: SHACL Validation (edge-local, per-device graph) ---
        t0 = time.perf_counter()
        tmp_store = RDFStore(load_ontology=False)
        try:
            tmp_store.register_device(device)
        except Exception:
            pass
        shacl_result = self.validator.validate(tmp_store.graph)
        t_shacl_ms = (time.perf_counter() - t0) * 1000

        # --- Step 3: BlockDAG add_event (distributed ledger) ---
        t0 = time.perf_counter()
        self.dag.add_event(
            event_type='device_register',
            payload={
                'device_id':      device_id,
                'hardware_type':  hardware_type,
                'classification': pred_type,
                'shacl_conforms': shacl_result.is_valid,
                'node_id':        self.node_id
            }
        )
        t_dag_ms = (time.perf_counter() - t0) * 1000

        t_total_ms = (time.perf_counter() - t_start) * 1000

        return DistributedEventRecord(
            event_id=event_id,
            node_id=self.node_id,
            device_id=device_id,
            hardware_type=hardware_type,
            t_classification_ms=t_classification_ms,
            t_shacl_ms=t_shacl_ms,
            t_dag_ms=t_dag_ms,
            t_total_ms=t_total_ms,
            classification_result=pred_type,
            classification_confidence=pred_conf,
            shacl_conforms=shacl_result.is_valid,
            shacl_violations=len(shacl_result.violations)
        )


# ---------------------------------------------------------------------------
# Distributed benchmark runner
# ---------------------------------------------------------------------------

def run_distributed_benchmark(
    model_type: str = 'decision_tree',
    num_nodes: int = 3,
    num_events: int = 100,
    num_repetitions: int = 30,
    warmup_events: int = 10,
    training_samples: int = DEFAULT_TRAINING_SAMPLES,
    seed: int = 42,
    output_dir: str = 'baseline/results',
    feature_set: str = DEFAULT_FEATURE_SET,
    training_source: str = DEFAULT_TRAINING_SOURCE,
    sign: bool = False
) -> List[DistributedEventRecord]:

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*55}")
    print(f"  Running distributed benchmark — {model_type}")
    print(f"  Nodes: {num_nodes} | Events/rep: {num_events} | Reps: {num_repetitions}")
    print(f"  Training source: {training_source} | DAG signing: {'on' if sign else 'off'}")
    print(f"{'='*55}")

    # Initialize nodes
    print(f"  Initializing {num_nodes} edge nodes...")
    nodes = [
        EdgeNode(
            node_id=f"edge-node-{i+1:02d}",
            model_type=model_type,
            training_samples=training_samples,
            feature_set=feature_set,
            training_source=training_source,
            sign=sign,
            seed=seed
        )
        for i in range(num_nodes)
    ]
    print(f"  Nodes ready (accuracy={nodes[0]._train_accuracy:.4f})")

    generator = make_generator(training_source, seed)
    all_records: List[DistributedEventRecord] = []

    for rep in range(num_repetitions):
        devices = generator.generate_dataset(
            num_devices=num_events + warmup_events
        )

        rep_records = []
        for i, device in enumerate(devices):
            # Round-robin across nodes — mirrors real distributed load balancing
            node = nodes[i % num_nodes]
            record = node.process_event(device, event_idx=len(all_records) + i)
            if i >= warmup_events:
                rep_records.append(record)

        all_records.extend(rep_records)

        if (rep + 1) % 5 == 0 or rep == 0:
            rep_latencies = [r.t_total_ms for r in rep_records]
            print(f"  Rep {rep+1:02d}/{num_repetitions} | "
                  f"mean={statistics.mean(rep_latencies):.2f} ms | "
                  f"p95={sorted(rep_latencies)[int(len(rep_latencies)*0.95)]:.2f} ms")

    # --- Summary ---
    latencies  = [r.t_total_ms for r in all_records]
    shacl_times = [r.t_shacl_ms for r in all_records]
    clf_times   = [r.t_classification_ms for r in all_records]
    dag_times   = [r.t_dag_ms for r in all_records]
    sorted_lat  = sorted(latencies)
    n           = len(sorted_lat)
    conformance = sum(1 for r in all_records if r.shacl_conforms) / len(all_records)
    throughput  = (len(all_records) / sum(latencies)) * 1000

    print(f"\n{'='*55}")
    print(f"  DISTRIBUTED BENCHMARK SUMMARY — {model_type}")
    print(f"{'='*55}")
    print(f"  Total events       : {len(all_records)}")
    print(f"  --- Latency (E2E) ---")
    print(f"  Mean               : {statistics.mean(latencies):.3f} ms")
    print(f"  Std Dev            : {statistics.stdev(latencies):.3f} ms")
    print(f"  P50                : {sorted_lat[int(n*0.50)]:.3f} ms")
    print(f"  P95                : {sorted_lat[int(n*0.95)]:.3f} ms")
    print(f"  P99                : {sorted_lat[int(n*0.99)]:.3f} ms")
    print(f"  --- Throughput ---")
    print(f"  Events/s           : {throughput:.1f}")
    print(f"  --- Stage breakdown ---")
    print(f"  PnP-AI mean        : {statistics.mean(clf_times):.3f} ms")
    print(f"  SHACL mean         : {statistics.mean(shacl_times):.3f} ms")
    print(f"  DAG mean           : {statistics.mean(dag_times):.3f} ms")
    print(f"  --- SHACL ---")
    print(f"  Conformance rate   : {conformance:.3f}")
    print(f"{'='*55}")

    # --- Save CSV (named so run_benchmark.py picks it up automatically) ---
    csv_path = out_dir / f'distributed_{model_type}_events.csv'
    fields = [
        'event_id', 'node_id', 'device_id', 'hardware_type',
        't_classification_ms', 't_shacl_ms', 't_dag_ms', 't_total_ms',
        'classification_result', 'classification_confidence',
        'shacl_conforms', 'shacl_violations'
    ]
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in all_records:
            writer.writerow({k: asdict(r)[k] for k in fields})
    print(f"  CSV saved → {csv_path}")

    # --- Save summary JSON ---
    summary = {
        'architecture': 'distributed',
        'feature_set': feature_set,
        **training_metadata(training_source, training_samples, seed),
        'dag_signed': sign,
        'model': model_type,
        'num_nodes': num_nodes,
        'num_events': num_events,
        'num_repetitions': num_repetitions,
        'latency_mean_ms': statistics.mean(latencies),
        'latency_std_ms': statistics.stdev(latencies),
        'latency_p50_ms': sorted_lat[int(n*0.50)],
        'latency_p95_ms': sorted_lat[int(n*0.95)],
        'latency_p99_ms': sorted_lat[int(n*0.99)],
        'throughput_events_per_s': throughput,
        'shacl_overhead_mean_ms': statistics.mean(shacl_times),
        'classification_mean_ms': statistics.mean(clf_times),
        'dag_overhead_mean_ms': statistics.mean(dag_times),
        'shacl_conformance_rate': conformance,
        'classification_accuracy': nodes[0]._train_accuracy,
        'generated_at': datetime.utcnow().isoformat()
    }
    json_path = out_dir / f'distributed_{model_type}_summary.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    print(f"  JSON saved → {json_path}")

    return all_records


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='ARGENCON 2026 — Distributed System Benchmark'
    )
    parser.add_argument('--model', choices=['decision_tree', 'random_forest', 'svm', 'mlp', 'both', 'all'],
                        default='all', help='Model type (default: all)')
    parser.add_argument('--nodes', type=int, default=3,
                        help='Number of edge nodes (default: 3)')
    parser.add_argument('--events', type=int, default=100,
                        help='Events per repetition (default: 100)')
    parser.add_argument('--repetitions', type=int, default=30,
                        help='Number of repetitions (default: 30)')
    parser.add_argument('--warmup', type=int, default=10,
                        help='Warmup events to discard (default: 10)')
    parser.add_argument('--training-samples', type=int, default=DEFAULT_TRAINING_SAMPLES,
                        help=f'PnP-AI training samples (default: {DEFAULT_TRAINING_SAMPLES}, as the server)')
    parser.add_argument('--seed', type=int, default=42,
                        help='Random seed (default: 42)')
    parser.add_argument('--feature-set', choices=['resource', 'resource+name'],
                        default=DEFAULT_FEATURE_SET,
                        help=f"Classifier features (default: {DEFAULT_FEATURE_SET}, as the server)")
    parser.add_argument('--training-source', choices=['profiles', 'catalog'],
                        default=DEFAULT_TRAINING_SOURCE,
                        help="Training data and benchmark events: 'profiles' (5 classes) or "
                             f"'catalog' (real-board catalog). Default: {DEFAULT_TRAINING_SOURCE}, as the server")
    parser.add_argument('--sign', action='store_true',
                        help='Sign every DAG node with Ed25519, as the server does with --dag-key-file')
    parser.add_argument('--output-dir', type=str, default='baseline/results',
                        help='Output directory (default: baseline/results)')
    args = parser.parse_args()

    models = ['decision_tree', 'random_forest', 'svm', 'mlp'] if args.model in ('both', 'all') else [args.model]

    for model in models:
        run_distributed_benchmark(
            model_type=model,
            num_nodes=args.nodes,
            num_events=args.events,
            num_repetitions=args.repetitions,
            warmup_events=args.warmup,
            training_samples=args.training_samples,
            seed=args.seed,
            output_dir=args.output_dir,
            feature_set=args.feature_set,
            training_source=args.training_source,
            sign=args.sign
        )

    print("\n✓ Done. Now run 'python baseline/run_benchmark.py' to generate comparison figures.")
