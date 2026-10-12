"""
Ablation: how much of GNSSReceiver detection depends on the declared capability?

A GNSS receiver announces the resources of its host (a Raspberry Pi, an
ESP32), so memory, CPU and link type say nothing about it. This script
trains the same models as baseline/run_classifier_eval.py, but with the
``declares_gnss`` feature forced to 0 at both training and test time, and
reports recall on the GNSSReceiver class and where those devices go.

Same protocol as run_classifier_eval.py: --seeds seeds, held-out test set from
a different seed. Run from the repository root:
    python baseline/run_gnss_ablation.py [--seeds 10] [--train 1000] [--test 400]

Output: baseline/results/gnss_ablation.json and a table on stdout.

Project: PINV01-24 - FIUNA
"""

import argparse
import json
import statistics
import sys
import warnings
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pnp_ai.dataset_generator import SyntheticDatasetGenerator
from pnp_ai.device_classifier import DeviceClassifier
from pnp_ai.features import FEATURE_SET_RESOURCE, FEATURE_SET_RESOURCE_NAME

warnings.filterwarnings("ignore")
RESULTS_DIR = Path(__file__).parent / "results"


def make_devices(n, seed):
    gen = SyntheticDatasetGenerator(seed=seed)
    return gen.generate_anomalies(gen.generate_dataset(n), 0.1)


def features(clf, device, ablate):
    f = clf._extract_features(device)
    if ablate:
        f["declares_gnss"] = 0.0
    return f


def predict(clf, device, ablate):
    X = clf._features_to_array(features(clf, device, ablate)).reshape(1, -1)
    return clf.label_encoder.inverse_transform(clf.model.predict(clf.scaler.transform(X)))[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--train", type=int, default=1000)
    ap.add_argument("--test", type=int, default=400)
    args = ap.parse_args()

    out = {}
    for fs in (FEATURE_SET_RESOURCE, FEATURE_SET_RESOURCE_NAME):
        for model in ("decision_tree", "random_forest"):
            for ablate in (False, True):
                recalls, where = [], Counter()
                for seed in range(args.seeds):
                    train, test = make_devices(args.train, seed), make_devices(args.test, seed + 10_000)
                    clf = DeviceClassifier(model_type=model, feature_set=fs)
                    clf.train([features(clf, d, ablate) for d in train], [d["device_type"] for d in train])
                    gnss = [d for d in test if d["device_type"] == "GNSSReceiver"]
                    preds = [predict(clf, d, ablate) for d in gnss]
                    recalls.append(sum(p == "GNSSReceiver" for p in preds) / len(gnss))
                    where.update(preds)
                key = f"{model}|{fs}|{'without declares_gnss' if ablate else 'with declares_gnss'}"
                out[key] = {"recall_mean": statistics.mean(recalls),
                            "recall_sd": statistics.stdev(recalls),
                            "predicted_as": dict(where)}
                print(f"{key:55s} recall {out[key]['recall_mean']:.3f} +- {out[key]['recall_sd']:.3f}  "
                      f"predicted as {dict(where)}")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "gnss_ablation.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
