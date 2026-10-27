"""
Classifier evaluation on OBSERVABLE features only, with a held-out test set.

Why this script exists
----------------------
The earlier numbers came from two places that are not a fair measure of what
the deployed classifier does:

* baseline/plot_classifier_metrics.py trains on role features
  (is_gateway_role, is_sensor_role, is_edge_ai_role) that a device does not
  announce when it registers.
* the pipeline benchmarks train with the default 'resource+name' feature set,
  whose hardware-name flags reproduce the rule-based lookup inside the model.

Here every model is trained through DeviceClassifier (the same extractor used
at inference) and evaluated by calling classify() on raw announcements from a
SEPARATE synthetic dataset, drawn with a different seed. Both feature sets are
reported side by side, plus the rule-based baseline, over several seeds.

Caveat that remains: training and test data come from the same synthetic
generator, so the numbers say how well a model recovers the generator's
classes, not how it would do on unseen real hardware.

Run from PINV01-24-main/:
    python baseline/run_classifier_eval.py [--seeds 10] [--train 1000] [--test 400]

Output: baseline/results/classifier_eval.json and classifier_eval_summary.csv

Project: PINV01-24 - FIUNA
"""

import argparse
import csv
import json
import statistics
import sys
import warnings
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, precision_recall_fscore_support, confusion_matrix

from pnp_ai.device_classifier import DeviceClassifier
from pnp_ai.dataset_generator import SyntheticDatasetGenerator
from pnp_ai.features import FEATURE_SET_RESOURCE, FEATURE_SET_RESOURCE_NAME

warnings.filterwarnings("ignore")

MODELS = ["decision_tree", "random_forest", "svm", "mlp"]
FEATURE_SETS = [FEATURE_SET_RESOURCE, FEATURE_SET_RESOURCE_NAME]
# The classes of SyntheticDatasetGenerator, the generator this script uses.
# GNSSReceiver is not among them: the class is decided by the rule of
# pnp_ai.capability_rules before the model, so the model is not trained on it.
CLASSES = ["ESP32Device", "IoTGateway", "Sensor", "SingleBoardComputer"]
RESULTS_DIR = Path(__file__).parent / "results"


def make_devices(n: int, seed: int):
    gen = SyntheticDatasetGenerator(seed=seed)
    devices = gen.generate_dataset(n)
    return gen.generate_anomalies(devices, 0.1)


def evaluate(classifier: DeviceClassifier, test_devices):
    y_true = [d["device_type"] for d in test_devices]
    y_pred = [classifier.classify(d).device_type for d in test_devices]
    labels = sorted(set(y_true) | set(y_pred))
    p, r, f, s = precision_recall_fscore_support(y_true, y_pred, labels=labels, zero_division=0)
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "f1_macro": f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0),
        "per_class": {
            lab: {"precision": float(p[i]), "recall": float(r[i]), "f1": float(f[i]), "support": int(s[i])}
            for i, lab in enumerate(labels)
        },
        "confusion": confusion_matrix(y_true, y_pred, labels=labels).tolist(),
        "labels": labels,
    }


def mean_sd(values):
    return (statistics.mean(values), statistics.stdev(values) if len(values) > 1 else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--train", type=int, default=1000)
    ap.add_argument("--test", type=int, default=400)
    args = ap.parse_args()

    runs = {}  # (model, feature_set) -> list of per-seed results
    for seed in range(args.seeds):
        train = make_devices(args.train, seed)
        test = make_devices(args.test, seed + 10_000)  # different seed -> held out

        for fs in FEATURE_SETS:
            for model in MODELS:
                clf = DeviceClassifier(model_type=model, feature_set=fs)
                clf.train([clf._extract_features(d) for d in train],
                          [d["device_type"] for d in train])
                runs.setdefault((model, fs), []).append(evaluate(clf, test))

        rb = DeviceClassifier(model_type="rule_based")
        runs.setdefault(("rule_based", "-"), []).append(evaluate(rb, test))

    summary, rows = {}, []
    for (model, fs), res in runs.items():
        acc = mean_sd([r["accuracy"] for r in res])
        f1m = mean_sd([r["f1_macro"] for r in res])
        per_class = {}
        for c in CLASSES:
            rec = [r["per_class"][c]["recall"] for r in res if c in r["per_class"]]
            pre = [r["per_class"][c]["precision"] for r in res if c in r["per_class"]]
            per_class[c] = {"recall": mean_sd(rec), "precision": mean_sd(pre)}
        key = f"{model}|{fs}"
        summary[key] = {"accuracy": acc, "f1_macro": f1m, "per_class": per_class}
        rows.append([model, fs, f"{acc[0]:.4f}", f"{acc[1]:.4f}", f"{f1m[0]:.4f}", f"{f1m[1]:.4f}"]
                    + [f"{per_class[c]['recall'][0]:.3f}" for c in CLASSES])

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "seeds": args.seeds, "train_size": args.train, "test_size": args.test,
        "note": "Held-out test set from a separate seed; same synthetic generator for train and test.",
        "summary": summary,
    }
    (RESULTS_DIR / "classifier_eval.json").write_text(json.dumps(out, indent=2))
    with open(RESULTS_DIR / "classifier_eval_summary.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["model", "feature_set", "acc_mean", "acc_sd", "f1macro_mean", "f1macro_sd"]
                   + [f"recall_{c}" for c in CLASSES])
        w.writerows(rows)

    print(f"{'model':14s} {'features':14s} {'accuracy':>15s} {'macro-F1':>15s}  recall per class {CLASSES}")
    for row in rows:
        print(f"{row[0]:14s} {row[1]:14s} {row[2]:>8s}±{row[3]:<6s} {row[4]:>8s}±{row[5]:<6s}  {row[6:]}")


if __name__ == "__main__":
    main()
