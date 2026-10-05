"""
PnP-AI Classifier Metrics Comparison
=====================================================
Trains all four classifiers (DT, RF, SVM, MLP) on the same
synthetic dataset under identical conditions and generates
four publication-ready figures:

  Fig A — Accuracy & F1-macro bar chart (test set)
  Fig B — Per-class F1-score grouped bar chart
  Fig C — Inference latency vs accuracy scatter
  Fig D — Confusion matrices (2x2 grid)

Usage:
    cd PINV01-24-main
    python baseline/plot_classifier_metrics.py [--samples 1000] [--output-dir baseline/results]

Output:
    baseline/results/
        clsf_fig_a_accuracy_f1.png
        clsf_fig_b_perclass_f1.png
        clsf_fig_c_latency_accuracy.png
        clsf_fig_d_confusion_matrices.png
        clsf_metrics_summary.json

Project: PINV01-24 - FIUNA
"""

import sys
import time
import json
import argparse
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    accuracy_score, f1_score, classification_report, confusion_matrix
)
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import LinearSVC
from sklearn.calibration import CalibratedClassifierCV
from sklearn.neural_network import MLPClassifier

from pnp_ai.dataset_generator import SyntheticDatasetGenerator

# ── Styling ─────────────────────────────────────────────────────────────────

MODEL_STYLES = {
    'Decision Tree': {'color': '#e74c3c', 'short': 'DT'},
    'Random Forest': {'color': '#e67e22', 'short': 'RF'},
    'SVM':           {'color': '#8e44ad', 'short': 'SVM'},
    'MLP':           {'color': '#2980b9', 'short': 'MLP'},
}

FEATURE_NAMES = [
    'memory_total_mb', 'memory_free_pct', 'cpu_freq_mhz',
    'is_wifi', 'is_ethernet', 'is_gateway_role',
    'is_sensor_role', 'is_edge_ai_role'
]

# ── Data preparation ─────────────────────────────────────────────────────────

def prepare_data(num_samples: int, seed: int = 42):
    gen = SyntheticDatasetGenerator(seed=seed)
    features, labels = gen.generate_classification_dataset(num_samples)

    X = np.array([[f[k] for k in FEATURE_NAMES] for f in features])
    le = LabelEncoder()
    y = le.fit_transform(labels)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    X_train, X_test, y_train, y_test = train_test_split(
        X_scaled, y, test_size=0.2, random_state=seed, stratify=y
    )
    return X_train, X_test, y_train, y_test, le.classes_


def build_models(seed: int = 42):
    return {
        'Decision Tree': DecisionTreeClassifier(
            max_depth=10, min_samples_split=5, random_state=seed
        ),
        'Random Forest': RandomForestClassifier(
            n_estimators=50, max_depth=10, min_samples_split=5,
            random_state=seed, n_jobs=1
        ),
        'SVM': CalibratedClassifierCV(
            LinearSVC(C=0.5, max_iter=5000, random_state=seed),
            method='sigmoid', cv=3
        ),
        'MLP': MLPClassifier(
            hidden_layer_sizes=(64, 32), activation='relu', solver='adam',
            max_iter=500, random_state=seed,
            early_stopping=True, validation_fraction=0.1
        ),
    }


def train_and_evaluate(models, X_train, X_test, y_train, y_test, classes):
    results = {}
    for name, model in models.items():
        print(f"  Training {name}...", end=' ', flush=True)

        t0 = time.perf_counter()
        model.fit(X_train, y_train)
        train_ms = (time.perf_counter() - t0) * 1000

        # Inference latency: average over 100 single-sample predictions
        sample = X_test[:1]
        t0 = time.perf_counter()
        for _ in range(100):
            model.predict(sample)
        infer_ms = (time.perf_counter() - t0) * 10   # mean per prediction in ms

        y_pred = model.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        f1m = f1_score(y_test, y_pred, average='macro')
        report = classification_report(
            y_test, y_pred, target_names=classes, output_dict=True
        )
        cm = confusion_matrix(y_test, y_pred)

        results[name] = {
            'accuracy':      acc,
            'f1_macro':      f1m,
            'train_ms':      train_ms,
            'infer_ms':      infer_ms,
            'report':        report,
            'cm':            cm,
            'classes':       list(classes),
            'y_pred':        y_pred,
            'y_test':        y_test,
        }
        print(f"acc={acc:.4f}  f1={f1m:.4f}  infer={infer_ms:.3f}ms")

    return results


# ── Fig A — Accuracy & F1-macro ──────────────────────────────────────────────

def plot_accuracy_f1(results, output_path):
    names  = list(results.keys())
    accs   = [results[n]['accuracy'] for n in names]
    f1s    = [results[n]['f1_macro'] for n in names]
    colors = [MODEL_STYLES[n]['color'] for n in names]

    x = np.arange(len(names))
    w = 0.35

    fig, ax = plt.subplots(figsize=(8, 4))
    bars1 = ax.bar(x - w/2, accs, w, label='Accuracy',
                   color=colors, alpha=0.85)
    bars2 = ax.bar(x + w/2, f1s,  w, label='F1-macro',
                   color=colors, alpha=0.50, hatch='//')

    for bar in list(bars1) + list(bars2):
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2, h + 0.002,
                f'{h:.3f}', ha='center', va='bottom', fontsize=8)

    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_ylim(0.85, 1.02)
    ax.set_ylabel('Score')
    ax.set_title('PnP-AI Classifier — Accuracy and F1-macro (test set)')
    ax.legend()
    ax.grid(axis='y', alpha=0.3)
    ax.axhline(y=0.95, color='gray', linestyle='--', linewidth=0.8, alpha=0.6)
    ax.text(len(names)-0.5, 0.951, '0.95 threshold', fontsize=8,
            color='gray', va='bottom')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  [fig A] saved → {output_path}")


# ── Fig B — Per-class F1 ─────────────────────────────────────────────────────

def plot_perclass_f1(results, output_path):
    names   = list(results.keys())
    classes = results[names[0]]['classes']
    # Exclude 'accuracy', 'macro avg', 'weighted avg' from report keys
    cls_keys = [c for c in classes]

    x   = np.arange(len(cls_keys))
    w   = 0.18
    n   = len(names)
    offsets = np.linspace(-(n-1)*w/2, (n-1)*w/2, n)

    fig, ax = plt.subplots(figsize=(10, 4))

    for i, name in enumerate(names):
        f1s = [results[name]['report'].get(c, {}).get('f1-score', 0) for c in cls_keys]
        ax.bar(x + offsets[i], f1s, w,
               label=name,
               color=MODEL_STYLES[name]['color'],
               alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels(cls_keys, rotation=20, ha='right')
    ax.set_ylim(0.7, 1.02)
    ax.set_ylabel('F1-score')
    ax.set_title('PnP-AI Classifier — Per-class F1-score (test set)')
    ax.legend()
    ax.grid(axis='y', alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  [fig B] saved → {output_path}")


# ── Fig C — Latency vs Accuracy scatter ──────────────────────────────────────

def plot_latency_accuracy(results, output_path):
    fig, ax = plt.subplots(figsize=(6, 4))

    for name, r in results.items():
        ax.scatter(r['infer_ms'], r['accuracy'],
                   s=120, color=MODEL_STYLES[name]['color'],
                   zorder=5, label=name)
        ax.annotate(
            f"  {MODEL_STYLES[name]['short']}",
            (r['infer_ms'], r['accuracy']),
            fontsize=9, va='center'
        )

    ax.set_xlabel('Mean inference latency (ms / sample)')
    ax.set_ylabel('Accuracy (test set)')
    ax.set_title('PnP-AI — Accuracy vs Inference Latency')
    ax.grid(alpha=0.3)
    ax.legend(loc='lower right')

    # Annotate ideal region
    ax.axhline(y=0.95, color='gray', linestyle='--', linewidth=0.7, alpha=0.5)
    ax.text(ax.get_xlim()[0], 0.951, '0.95', fontsize=8,
            color='gray', va='bottom')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  [fig C] saved → {output_path}")


# ── Fig D — Confusion matrices (2×2) ─────────────────────────────────────────

def plot_confusion_matrices(results, output_path):
    names = list(results.keys())
    classes = results[names[0]]['classes']
    n_classes = len(classes)

    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    axes = axes.flatten()

    for ax, name in zip(axes, names):
        cm = results[name]['cm'].astype(float)
        cm_norm = cm / cm.sum(axis=1, keepdims=True)
        cm_norm = np.nan_to_num(cm_norm)

        im = ax.imshow(cm_norm, interpolation='nearest',
                       cmap='Blues', vmin=0, vmax=1)
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

        ax.set_xticks(range(n_classes))
        ax.set_yticks(range(n_classes))
        ax.set_xticklabels(classes, rotation=35, ha='right', fontsize=8)
        ax.set_yticklabels(classes, fontsize=8)
        ax.set_xlabel('Predicted', fontsize=9)
        ax.set_ylabel('True', fontsize=9)

        acc = results[name]['accuracy']
        f1  = results[name]['f1_macro']
        ax.set_title(f"{name}\nacc={acc:.3f}  F1={f1:.3f}", fontsize=10)

        thresh = 0.5
        for i in range(n_classes):
            for j in range(n_classes):
                ax.text(j, i, f'{cm_norm[i,j]:.2f}',
                        ha='center', va='center', fontsize=7,
                        color='white' if cm_norm[i, j] > thresh else 'black')

    fig.suptitle('PnP-AI — Normalized Confusion Matrices (test set)',
                 fontsize=12, y=1.01)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  [fig D] saved → {output_path}")


# ── Summary JSON ─────────────────────────────────────────────────────────────

def save_summary(results, output_path):
    summary = {}
    for name, r in results.items():
        per_class = {
            cls: {
                'precision': r['report'].get(cls, {}).get('precision', 0),
                'recall':    r['report'].get(cls, {}).get('recall', 0),
                'f1':        r['report'].get(cls, {}).get('f1-score', 0),
                'support':   r['report'].get(cls, {}).get('support', 0),
            }
            for cls in r['classes']
        }
        summary[name] = {
            'accuracy':    round(r['accuracy'], 4),
            'f1_macro':    round(r['f1_macro'], 4),
            'infer_ms':    round(r['infer_ms'], 4),
            'train_ms':    round(r['train_ms'], 1),
            'per_class':   per_class,
        }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    print(f"  [json] saved → {output_path}")

    # Print console table
    print(f"\n{'='*65}")
    print(f"  {'Model':<16} {'Accuracy':>9} {'F1-macro':>9} {'Infer (ms)':>11}")
    print(f"  {'-'*60}")
    for name, s in summary.items():
        print(f"  {name:<16} {s['accuracy']:>9.4f} {s['f1_macro']:>9.4f} {s['infer_ms']:>11.4f}")
    print(f"{'='*65}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='ARGENCON 2026 — PnP-AI classifier metrics comparison'
    )
    parser.add_argument('--samples',    type=int, default=1000)
    parser.add_argument('--seed',       type=int, default=42)
    parser.add_argument('--output-dir', type=str, default='baseline/results')
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"\n[1/3] Preparing data ({args.samples} samples, seed={args.seed})...")
    X_train, X_test, y_train, y_test, classes = prepare_data(args.samples, args.seed)
    print(f"      Train: {len(X_train)}  Test: {len(X_test)}  Classes: {list(classes)}")

    print(f"\n[2/3] Training and evaluating 4 classifiers...")
    models  = build_models(args.seed)
    results = train_and_evaluate(models, X_train, X_test, y_train, y_test, classes)

    print(f"\n[3/3] Generating figures...")
    plot_accuracy_f1(results,         out / 'clsf_fig_a_accuracy_f1.png')
    plot_perclass_f1(results,         out / 'clsf_fig_b_perclass_f1.png')
    plot_latency_accuracy(results,    out / 'clsf_fig_c_latency_accuracy.png')
    plot_confusion_matrices(results,  out / 'clsf_fig_d_confusion_matrices.png')
    save_summary(results,             out / 'clsf_metrics_summary.json')

    print(f"\nDone. All outputs in: {out.resolve()}")


if __name__ == '__main__':
    main()
