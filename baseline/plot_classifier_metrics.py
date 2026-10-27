"""
PnP-AI Classifier Metrics Comparison
=====================================================
Trains DT, RF, SVM and MLP exactly as the server and the baseline benchmark do
(train_classifier_from_synthetic_data: same generator, same feature extractor,
same hyper-parameters, same 80/20 split with random_state=42) and evaluates
each model on that held-out 20 %. The reported accuracy therefore equals the
'classification_accuracy' written by the baseline benchmark for the same
--training-source / --samples / --feature-set; the script checks this.

Figures:
  Fig A - Accuracy & F1-macro bar chart (test split)
  Fig B - Per-class F1-score grouped bar chart
  Fig C - Inference latency vs accuracy scatter
  Fig D - Normalized confusion matrices

Usage (article settings are the defaults: catalog, 2000 samples, resource):
    cd PINV01-24-main
    python baseline/plot_classifier_metrics.py --output-dir baseline/results_pi5_catalog

Output (in --output-dir), each figure as .png and .pdf:
    clsf_fig_a_accuracy_f1, clsf_fig_b_perclass_f1,
    clsf_fig_c_latency_accuracy, clsf_fig_d_confusion_matrices,
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

from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    accuracy_score, f1_score, classification_report, confusion_matrix
)

from pnp_ai.device_classifier import train_classifier_from_synthetic_data
from pnp_ai.features import FEATURE_SETS, FEATURE_SET_RESOURCE, features_to_vector
from baseline.central_baseline import TRAINING_SOURCES, make_generator

# ── Styling ─────────────────────────────────────────────────────────────────

MODEL_STYLES = {
    'Decision Tree':       {'color': '#e74c3c', 'short': 'DT'},
    'Random Forest':       {'color': '#e67e22', 'short': 'RF'},
    'SVM':                 {'color': '#8e44ad', 'short': 'SVM'},
    'MLP':                 {'color': '#2980b9', 'short': 'MLP'},
    'Logistic Regression': {'color': '#16a085', 'short': 'LR'},
    'k-NN':                {'color': '#7f8c8d', 'short': 'kNN'},
}

# The six model families DeviceClassifier supports, so that this table covers
# the same ones as the two experiments on real and unseen boards.
MODEL_TYPES = {
    'dt':   ('Decision Tree', 'decision_tree'),
    'rf':   ('Random Forest', 'random_forest'),
    'svm':  ('SVM', 'svm'),
    'mlp':  ('MLP', 'mlp'),
    'lr':   ('Logistic Regression', 'logistic_regression'),
    'knn':  ('k-NN', 'knn'),
}


def save_fig(fig, output_path: Path):
    for ext in ('.png', '.pdf'):
        fig.savefig(output_path.with_suffix(ext), dpi=300, bbox_inches='tight')
    plt.close(fig)


# ── Training and evaluation (same path as the server) ────────────────────────

def held_out_split(clf, training_source, num_samples, seed):
    """
    Rebuild the 80/20 split that DeviceClassifier.train() used internally.

    A fresh generator with the same seed yields the same announcements, and
    the classifier's own extractor and fitted scaler give the same matrix.
    """
    gen = make_generator(training_source, seed)
    devices = gen.generate_dataset(num_samples)
    devices = gen.generate_anomalies(devices, 0.1)
    X = np.array([features_to_vector(clf._extract_features(d), clf.feature_names)
                  for d in devices])
    y = clf.label_encoder.transform([d['device_type'] for d in devices])
    X_scaled = clf.scaler.transform(X)
    _, X_test, _, y_test = train_test_split(
        X_scaled, y, test_size=0.2, random_state=42, stratify=y
    )
    return X_test, y_test


def train_and_evaluate(models, training_source, num_samples, feature_set, seed):
    results = {}
    for key in models:
        name, model_type = MODEL_TYPES[key]
        print(f"  Training {name}...", end=' ', flush=True)

        t0 = time.perf_counter()
        clf, metrics = train_classifier_from_synthetic_data(
            num_samples=num_samples, model_type=model_type,
            feature_set=feature_set, seed=seed,
            generator=make_generator(training_source, seed)
        )
        train_ms = (time.perf_counter() - t0) * 1000

        X_test, y_test = held_out_split(clf, training_source, num_samples, seed)
        classes = [str(c) for c in clf.label_encoder.classes_]

        # Inference latency: mean over 100 single-sample predictions
        sample = X_test[:1]
        clf.model.predict(sample)  # warm-up
        t0 = time.perf_counter()
        for _ in range(100):
            clf.model.predict(sample)
        infer_ms = (time.perf_counter() - t0) * 10

        y_pred = clf.model.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        if abs(acc - metrics['accuracy']) > 1e-9:
            raise RuntimeError(f"{name}: rebuilt split gives accuracy {acc:.4f}, "
                               f"training reported {metrics['accuracy']:.4f}")
        f1m = f1_score(y_test, y_pred, average='macro')
        report = classification_report(
            y_test, y_pred, labels=range(len(classes)), target_names=classes,
            output_dict=True, zero_division=0
        )
        cm = confusion_matrix(y_test, y_pred, labels=range(len(classes)))

        results[name] = {
            'accuracy':      acc,
            'f1_macro':      f1m,
            'train_ms':      train_ms,
            'infer_ms':      infer_ms,
            'report':        report,
            'cm':            cm,
            'classes':       classes,
            'y_pred':        y_pred,
            'y_test':        y_test,
        }
        print(f"acc={acc:.4f}  f1={f1m:.4f}  infer={infer_ms:.3f}ms")

    return results


def lower_ylim(values):
    """Lowest 0.05 step below the smallest value (at most 0.0)."""
    return max(0.0, np.floor((min(values) - 0.03) * 20) / 20)


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
    ax.set_ylim(lower_ylim(accs + f1s), 1.02)
    ax.set_ylabel('Score')
    ax.legend()
    ax.grid(axis='y', alpha=0.3)

    plt.tight_layout()
    save_fig(fig, output_path)
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
    all_f1 = []

    for i, name in enumerate(names):
        f1s = [results[name]['report'].get(c, {}).get('f1-score', 0) for c in cls_keys]
        all_f1 += f1s
        ax.bar(x + offsets[i], f1s, w,
               label=name,
               color=MODEL_STYLES[name]['color'],
               alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels(cls_keys, rotation=20, ha='right')
    ax.set_ylim(lower_ylim(all_f1), 1.02)
    ax.set_ylabel('F1-score')
    ax.legend()
    ax.grid(axis='y', alpha=0.3)

    plt.tight_layout()
    save_fig(fig, output_path)
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
    ax.grid(alpha=0.3)
    ax.legend(loc='lower right')


    plt.tight_layout()
    save_fig(fig, output_path)
    print(f"  [fig C] saved → {output_path}")


# ── Fig D — Confusion matrices (2×2) ─────────────────────────────────────────

def plot_confusion_matrices(results, output_path):
    names = list(results.keys())
    classes = results[names[0]]['classes']
    n_classes = len(classes)

    ncols = 2 if len(names) > 1 else 1
    nrows = int(np.ceil(len(names) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(6 * ncols, 5 * nrows), squeeze=False)
    axes = axes.flatten()
    for ax in axes[len(names):]:
        ax.axis('off')

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

    plt.tight_layout()
    save_fig(fig, output_path)
    print(f"  [fig D] saved → {output_path}")


# ── Summary JSON ─────────────────────────────────────────────────────────────

def save_summary(results, output_path, meta):
    summary = {'_meta': meta}
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
    print(f"  {'Model':<20} {'Accuracy':>9} {'F1-macro':>9} {'Infer (ms)':>11}")
    print(f"  {'-'*60}")
    for name, s in summary.items():
        if name == '_meta':
            continue
        print(f"  {name:<20} {s['accuracy']:>9.4f} {s['f1_macro']:>9.4f} {s['infer_ms']:>11.4f}")
    print(f"{'='*65}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='PnP-AI classifier metrics comparison (same training path as the server)'
    )
    parser.add_argument('--samples', type=int, default=2000,
                        help='training announcements (article/server: 2000)')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--training-source', choices=TRAINING_SOURCES, default='catalog',
                        help='article/server: catalog')
    parser.add_argument('--feature-set', choices=FEATURE_SETS, default=FEATURE_SET_RESOURCE,
                        help='article/server: resource')
    parser.add_argument('--models', nargs='+', choices=list(MODEL_TYPES),
                        default=list(MODEL_TYPES))
    parser.add_argument('--output-dir', type=str, default='baseline/results')
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"\n[1/2] Training and evaluating {args.models} "
          f"(source={args.training_source}, samples={args.samples}, "
          f"features={args.feature_set}, seed={args.seed})...")
    results = train_and_evaluate(args.models, args.training_source, args.samples,
                                 args.feature_set, args.seed)
    first = next(iter(results.values()))
    print(f"      Test split: {len(first['y_test'])}  Classes: {first['classes']}")

    print(f"\n[2/2] Generating figures...")
    plot_accuracy_f1(results,         out / 'clsf_fig_a_accuracy_f1.png')
    plot_perclass_f1(results,         out / 'clsf_fig_b_perclass_f1.png')
    plot_latency_accuracy(results,    out / 'clsf_fig_c_latency_accuracy.png')
    plot_confusion_matrices(results,  out / 'clsf_fig_d_confusion_matrices.png')
    meta = {
        'training_source': args.training_source,
        'samples': args.samples,
        'test_split': len(first['y_test']),
        'feature_set': args.feature_set,
        'seed': args.seed,
        'classes': first['classes'],
    }
    save_summary(results, out / 'clsf_metrics_summary.json', meta)

    print(f"\nDone. All outputs in: {out.resolve()}")


if __name__ == '__main__':
    main()
