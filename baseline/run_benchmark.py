"""
Benchmark Runner
=================================
Runs the centralized baseline and produces comparison figures for the paper:

  Fig 1 — Boxplot: E2E latency  (Distributed vs Centralized, DT vs RF)
  Fig 2 — CDF:     E2E latency  (all four configurations)
  Fig 3 — Bar:     Stage breakdown (classification / SHACL / logging)
  Fig 4 — Bar:     Throughput comparison

Usage:
    cd PINV01-24-main
    python baseline/run_benchmark.py [--events 100] [--repetitions 30]

Output:
    baseline/results/
        baseline_decision_tree_events.csv
        baseline_random_forest_events.csv
        baseline_decision_tree_summary.json
        baseline_random_forest_summary.json
        fig1_latency_boxplot.png
        fig2_latency_cdf.png
        fig3_stage_breakdown.png
        fig4_throughput.png
        comparison_table.json

Project: PINV01-24 - FIUNA
"""

import argparse
import json
import csv
import sys
import time
import statistics
from pathlib import Path
from typing import Dict, List, Any, Optional

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).parent.parent))

from baseline.central_baseline import (CentralizedBaseline, BaselineResults, DEFAULT_FEATURE_SET,
                                       DEFAULT_TRAINING_SAMPLES, DEFAULT_TRAINING_SOURCE)


# ---------------------------------------------------------------------------
# Plotting helpers
# ---------------------------------------------------------------------------

COLORS = {
    'central_dt':  '#e74c3c',
    'central_rf':  '#e67e22',
    'central_svm': '#8e44ad',
    'central_mlp': '#16a085',
    'distrib_dt':  '#2980b9',
    'distrib_rf':  '#27ae60',
    'distrib_svm': '#6c3483',
    'distrib_mlp': '#0e6655',
}

LABELS = {
    'central_dt':  'Centralized (DT)',
    'central_rf':  'Centralized (RF)',
    'central_svm': 'Centralized (SVM)',
    'central_mlp': 'Centralized (MLP)',
    'distrib_dt':  'Distributed (DT)',
    'distrib_rf':  'Distributed (RF)',
    'distrib_svm': 'Distributed (SVM)',
    'distrib_mlp': 'Distributed (MLP)',
}


def load_distributed_results(results_dir: Path, model: str) -> Optional[List[float]]:
    """
    Load per-event latencies from the distributed system's existing CSV output
    (produced by generate_dag_evaluation.py or a previous benchmark run).
    Returns None if not found — caller will skip that series.
    """
    # Look in the results directory itself (produced by run_distributed_benchmark.py)
    candidates = [
        results_dir / f'distributed_{model}_events.csv',
        results_dir.parent / f'distributed_{model}_events.csv',
    ]
    for path in candidates:
        if path.exists():
            latencies = []
            with open(path, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    key = next((k for k in row if 't_total_ms' in k or 'latency' in k.lower()), None)
                    if key:
                        try:
                            latencies.append(float(row[key]))
                        except ValueError:
                            pass
            if latencies:
                print(f"  [load] Distributed {model} latencies loaded from {path} "
                      f"({len(latencies)} events)")
                return latencies
    print(f"  [load] No distributed {model} CSV found — distributed series will be omitted.")
    return None


# ---------------------------------------------------------------------------
# Figure 1 — Boxplot
# ---------------------------------------------------------------------------

def plot_boxplot(data: Dict[str, List[float]], output_path: Path):
    keys = [k for k in ['central_dt', 'central_rf', 'central_svm', 'central_mlp', 'distrib_dt', 'distrib_rf', 'distrib_svm', 'distrib_mlp']
            if k in data and data[k]]
    if not keys:
        return

    fig, ax = plt.subplots(figsize=(12, 5))

    bp = ax.boxplot(
        [data[k] for k in keys],
        tick_labels=[LABELS[k] for k in keys],
        patch_artist=True,
        notch=False,
        showfliers=True,
        flierprops=dict(marker='o', markersize=3, alpha=0.4)
    )

    for patch, key in zip(bp['boxes'], keys):
        patch.set_facecolor(COLORS[key])
        patch.set_alpha(0.75)

    ax.set_ylabel('End-to-End Latency (ms)')
    ax.set_title('E2E Latency Distribution: Distributed vs Centralized')
    ax.grid(axis='y', alpha=0.3)
    ax.set_ylim(bottom=0)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  [fig] Boxplot saved → {output_path}")


# ---------------------------------------------------------------------------
# Figure 2 — CDF
# ---------------------------------------------------------------------------

def plot_cdf(data: Dict[str, List[float]], output_path: Path):
    keys = [k for k in ['central_dt', 'central_rf', 'central_svm', 'central_mlp', 'distrib_dt', 'distrib_rf', 'distrib_svm', 'distrib_mlp']
            if k in data and data[k]]
    if not keys:
        return

    fig, ax = plt.subplots(figsize=(8, 5))

    for key in keys:
        sorted_vals = np.sort(data[key])
        cdf = np.arange(1, len(sorted_vals) + 1) / len(sorted_vals)
        ax.plot(sorted_vals, cdf,
                label=LABELS[key],
                color=COLORS[key],
                linewidth=2)

    ax.axvline(x=15, color='gray', linestyle='--', alpha=0.5, label='15 ms reference')
    ax.set_xlabel('End-to-End Latency (ms)')
    ax.set_ylabel('Cumulative Probability')
    ax.set_title('CDF of E2E Latency')
    ax.legend(loc='lower right')
    ax.grid(alpha=0.3)
    ax.set_xlim(left=0)
    ax.set_ylim(0, 1.02)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  [fig] CDF saved → {output_path}")


# ---------------------------------------------------------------------------
# Figure 3 — Stage breakdown
# ---------------------------------------------------------------------------

def plot_stage_breakdown(stage_data: Dict[str, Dict[str, float]], output_path: Path):
    """
    stage_data = {
        'central_dt': {'classification': x, 'shacl': y, 'logging': z},
        ...
    }
    """
    keys = [k for k in ['central_dt', 'central_rf', 'central_svm', 'central_mlp', 'distrib_dt', 'distrib_rf', 'distrib_svm', 'distrib_mlp']
            if k in stage_data]
    if not keys:
        return

    stages = ['classification', 'shacl', 'logging']
    stage_labels = ['PnP-AI', 'SHACL', 'Logging']
    stage_colors = ['#3498db', '#e74c3c', '#2ecc71']

    x = np.arange(len(keys))
    width = 0.25

    fig, ax = plt.subplots(figsize=(9, 5))

    bottoms = np.zeros(len(keys))
    for stage, label, color in zip(stages, stage_labels, stage_colors):
        vals = np.array([stage_data[k].get(stage, 0) for k in keys])
        ax.bar(x, vals, width=0.6, bottom=bottoms,
               label=label, color=color, alpha=0.85)
        bottoms += vals

    ax.set_xticks(x)
    ax.set_xticklabels([LABELS[k] for k in keys], rotation=15, ha='right')
    ax.set_ylabel('Mean Time (ms)')
    ax.set_title('Pipeline Stage Breakdown')
    ax.legend()
    ax.grid(axis='y', alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  [fig] Stage breakdown saved → {output_path}")


# ---------------------------------------------------------------------------
# Figure 4 — Throughput
# ---------------------------------------------------------------------------

def plot_throughput(throughput_data: Dict[str, float], output_path: Path):
    keys = [k for k in ['central_dt', 'central_rf', 'central_svm', 'central_mlp', 'distrib_dt', 'distrib_rf', 'distrib_svm', 'distrib_mlp']
            if k in throughput_data]
    if not keys:
        return

    fig, ax = plt.subplots(figsize=(7, 4))

    bars = ax.bar(
        [LABELS[k] for k in keys],
        [throughput_data[k] for k in keys],
        color=[COLORS[k] for k in keys],
        alpha=0.85,
        width=0.5
    )

    for bar, key in zip(bars, keys):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2., height + 1,
                f'{height:.1f}', ha='center', va='bottom', fontsize=9)

    ax.set_ylabel('Throughput (events/s)')
    ax.set_title('System Throughput Comparison')
    ax.grid(axis='y', alpha=0.3)
    ax.set_ylim(bottom=0)

    plt.xticks(rotation=15, ha='right')
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  [fig] Throughput saved → {output_path}")


# ---------------------------------------------------------------------------
# Main benchmark runner
# ---------------------------------------------------------------------------

def run(args):
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    latency_data: Dict[str, List[float]] = {}
    stage_data: Dict[str, Dict[str, float]] = {}
    throughput_data: Dict[str, float] = {}
    summary: Dict[str, Any] = {}

    # -----------------------------------------------------------------------
    # 1. Run centralized baseline for both models
    # -----------------------------------------------------------------------
    for model in ['decision_tree', 'random_forest', 'svm', 'mlp']:
        key = f'central_{model[:2]}'   # central_de → central_dt, central_ra → central_rf
        key = {'decision_tree': 'central_dt', 'random_forest': 'central_rf', 'svm': 'central_svm', 'mlp': 'central_mlp'}[model]

        print(f"\n{'='*55}")
        print(f"  Running centralized baseline — {model}")
        print(f"{'='*55}")

        baseline = CentralizedBaseline(
            model_type=model,
            training_samples=args.training_samples,
            seed=args.seed,
            feature_set=args.feature_set,
            training_source=args.training_source
        )

        results = baseline.run_benchmark(
            num_events=args.events,
            num_repetitions=args.repetitions,
            warmup_events=args.warmup,
            seed=args.seed
        )

        # Save per-event CSV and summary JSON
        baseline.save_csv(results, str(out_dir / f'baseline_{model}_events.csv'))
        baseline.save_summary_json(results, str(out_dir / f'baseline_{model}_summary.json'))

        # Collect for figures
        latency_data[key] = [r.t_total_ms for r in results.records]
        stage_data[key] = {
            'classification': results.classification_mean_ms,
            'shacl': results.shacl_overhead_mean_ms,
            'logging': results.latency_mean_ms
                       - results.classification_mean_ms
                       - results.shacl_overhead_mean_ms
        }
        throughput_data[key] = results.throughput_events_per_s
        summary[key] = {
            'architecture': 'centralized',
            'model': model,
            'latency_mean_ms': results.latency_mean_ms,
            'latency_std_ms': results.latency_std_ms,
            'latency_p95_ms': results.latency_p95_ms,
            'throughput_events_per_s': results.throughput_events_per_s,
            'shacl_overhead_mean_ms': results.shacl_overhead_mean_ms,
            'shacl_conformance_rate': results.shacl_conformance_rate,
            'classification_mean_ms': results.classification_mean_ms,
            'classification_accuracy': results.classification_accuracy,
            'training_source': args.training_source,
            'training_samples': args.training_samples,
        }

    # -----------------------------------------------------------------------
    # 2. Load distributed results if available
    # -----------------------------------------------------------------------
    print(f"\n{'='*55}")
    print("  Loading distributed system results (if available)")
    print(f"{'='*55}")

    for model, key in [('decision_tree', 'distrib_dt'), ('random_forest', 'distrib_rf'), ('svm', 'distrib_svm'), ('mlp', 'distrib_mlp')]:
        latencies = load_distributed_results(out_dir, model)
        if latencies:
            latency_data[key] = latencies
            throughput_data[key] = (len(latencies) / sum(latencies)) * 1000
            s = {
                'architecture': 'distributed',
                'model': model,
                'latency_mean_ms': statistics.mean(latencies),
                'latency_std_ms': statistics.stdev(latencies),
                'latency_p95_ms': sorted(latencies)[int(len(latencies) * 0.95)],
                'throughput_events_per_s': throughput_data[key],
            }
            # Load stage breakdown from summary JSON if available
            json_path = out_dir / f'distributed_{model}_summary.json'
            if json_path.exists():
                with open(json_path, encoding='utf-8') as jf:
                    jdata = json.load(jf)
                stage_data[key] = {
                    'classification': jdata.get('classification_mean_ms', 0),
                    'shacl':          jdata.get('shacl_overhead_mean_ms', 0),
                    'logging':        jdata.get('dag_overhead_mean_ms', 0),
                }
                s['shacl_overhead_mean_ms'] = jdata.get('shacl_overhead_mean_ms', 0)
                s['classification_accuracy'] = jdata.get('classification_accuracy', 0)
            summary[key] = s

    # -----------------------------------------------------------------------
    # 3. Generate figures
    # -----------------------------------------------------------------------
    print(f"\n{'='*55}")
    print("  Generating paper figures")
    print(f"{'='*55}")

    plot_boxplot(latency_data,   out_dir / 'fig1_latency_boxplot.png')
    plot_cdf(latency_data,       out_dir / 'fig2_latency_cdf.png')
    plot_stage_breakdown(stage_data, out_dir / 'fig3_stage_breakdown.png')
    plot_throughput(throughput_data, out_dir / 'fig4_throughput.png')

    # -----------------------------------------------------------------------
    # 4. Save comparison table JSON
    # -----------------------------------------------------------------------
    comparison_path = out_dir / 'comparison_table.json'
    with open(comparison_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    print(f"  [out] Comparison table → {comparison_path}")

    # -----------------------------------------------------------------------
    # 5. Print comparison table to console
    # -----------------------------------------------------------------------
    print(f"\n{'='*70}")
    print(f"  COMPARISON TABLE")
    print(f"{'='*70}")
    print(f"  {'Configuration':<22} {'Mean (ms)':>10} {'P95 (ms)':>10} "
          f"{'Tput (ev/s)':>12} {'SHACL (ms)':>11}")
    print(f"  {'-'*65}")
    for key, label in LABELS.items():
        if key in summary:
            s = summary[key]
            shacl = s.get('shacl_overhead_mean_ms', '-')
            shacl_str = f"{shacl:.3f}" if isinstance(shacl, float) else shacl
            print(f"  {label:<22} "
                  f"{s['latency_mean_ms']:>10.3f} "
                  f"{s['latency_p95_ms']:>10.3f} "
                  f"{s['throughput_events_per_s']:>12.1f} "
                  f"{shacl_str:>11}")
    print(f"{'='*70}")
    print(f"\nDone. Results in: {out_dir.resolve()}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='ARGENCON 2026 — Distributed vs Centralized Benchmark'
    )
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
    parser.add_argument('--output-dir', type=str, default='baseline/results',
                        help='Output directory (default: baseline/results)')
    args = parser.parse_args()
    run(args)
