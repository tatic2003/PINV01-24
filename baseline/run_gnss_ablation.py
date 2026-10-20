"""
Ablation (test only): how much of GNSSReceiver detection depends on the
declared capability?

A GNSS receiver announces the resources of its host (a Raspberry Pi, an
ESP32), so memory, CPU and link type say nothing about it. The model is
trained exactly as the server trains it, with the ``declares_gnss`` feature
available. At test time the GNSSReceiver announcements are classified twice:
as announced, and with ``declares_gnss`` forced to 0, as if the device had not
declared its capability. The model is never retrained without the feature.

Server configuration reproduced here (see server/app.py, server/config.py):
  - CatalogDatasetGenerator, calibrated memory, default (hardware) labeling
  - feature set 'resource' (the hardware name is NOT a feature)
  - 2000 training announcements with 10% anomalies
  - random forest and decision tree, same hyperparameters as DeviceClassifier
Each seed changes the generator seed of the training set; the test set comes
from a different seed (seed + 10_000), so train and test share no announcement.

Run from the repository root:
    python baseline/run_gnss_ablation.py [--seeds 10] [--train 2000] [--test 400]
                                         [--out baseline/results_gnss_ablation]

Output folder:
    gnss_ablation_summary.json   mean/sd of recall per model and condition, where
                                 the devices go, mean confidence, catalogue version
    gnss_ablation_per_seed.csv   one row per (model, seed, condition)
    gnss_ablation_devices.csv    one row per GNSSReceiver test device and condition
                                 (no coordinates are written)
    conditions.txt               arguments, library versions, platform

Project: PINV01-24 - FIUNA
"""

import argparse
import csv
import json
import platform
import statistics
import sys
import warnings
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import sklearn

from pnp_ai.dataset_generator.catalog import (
    DEFAULT_LABELING, GNSS_LABEL, CatalogDatasetGenerator)
from pnp_ai.device_classifier import train_classifier_from_synthetic_data
from pnp_ai.features import FEATURE_SET_RESOURCE

warnings.filterwarnings("ignore")

MODELS = ("decision_tree", "random_forest")
CONDITIONS = ("with declares_gnss", "without declares_gnss")
CONFIDENCE_THRESHOLD = 0.6  # server default: below it the device stays IoTDevice


def make_generator(seed):
    """Same generator the server uses for training."""
    return CatalogDatasetGenerator(seed=seed, labeling=DEFAULT_LABELING, calibrated=True)


def make_test_devices(n, seed):
    gen = make_generator(seed)
    return gen.generate_anomalies(gen.generate_dataset(n), 0.1), gen


def classify(clf, device, ablate):
    """Predicted class and confidence; optionally with declares_gnss forced to 0."""
    feats = clf._extract_features(device)
    if ablate:
        feats["declares_gnss"] = 0.0
    X = clf._features_to_array(feats).reshape(1, -1)
    proba = clf.model.predict_proba(clf.scaler.transform(X))[0]
    idx = int(np.argmax(proba))
    return str(clf.label_encoder.inverse_transform([idx])[0]), float(proba[idx])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--train", type=int, default=2000)
    ap.add_argument("--test", type=int, default=400)
    ap.add_argument("--out", default=str(Path(__file__).parent / "results_gnss_ablation"))
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    per_seed_rows, device_rows, summary = [], [], {}
    catalog_info = None

    for model in MODELS:
        stats = {c: {"recalls": [], "where": Counter(), "conf": [], "low_conf": 0, "n": 0}
                 for c in CONDITIONS}
        for seed in range(args.seeds):
            # Train as the server does: shared helper, catalogue generator, resource features.
            clf, _ = train_classifier_from_synthetic_data(
                args.train, model, feature_set=FEATURE_SET_RESOURCE,
                generator=make_generator(seed))
            test, test_gen = make_test_devices(args.test, seed + 10_000)
            if catalog_info is None:
                info = test_gen.describe()
                catalog_info = {k: info[k] for k in
                                ("catalog_version", "catalog_boards", "labeling", "calibrated")}
            gnss = [d for d in test if d["device_type"] == GNSS_LABEL]

            for cond in CONDITIONS:
                ablate = cond.startswith("without")
                preds = [classify(clf, d, ablate) for d in gnss]
                hits = sum(p == GNSS_LABEL for p, _ in preds)
                recall = hits / len(gnss)
                s = stats[cond]
                s["recalls"].append(recall)
                s["where"].update(p for p, _ in preds)
                s["conf"].extend(c for _, c in preds)
                s["low_conf"] += sum(c < CONFIDENCE_THRESHOLD for _, c in preds)
                s["n"] += len(preds)
                per_seed_rows.append({"model": model, "seed": seed, "condition": cond,
                                      "n_gnss": len(gnss), "recall": round(recall, 4)})
                for d, (p, c) in zip(gnss, preds):
                    device_rows.append({"model": model, "seed": seed, "condition": cond,
                                        "board_id": d.get("board_id"),
                                        "is_anomaly": d.get("is_anomaly"),
                                        "predicted": p, "confidence": round(c, 4)})
            print(f"{model} seed {seed}: "
                  f"with {per_seed_rows[-2]['recall']:.3f}  without {per_seed_rows[-1]['recall']:.3f}  "
                  f"(n_gnss={len(gnss)})")

        for cond in CONDITIONS:
            s = stats[cond]
            summary[f"{model}|{cond}"] = {
                "recall_mean": statistics.mean(s["recalls"]),
                "recall_sd": statistics.stdev(s["recalls"]) if len(s["recalls"]) > 1 else 0.0,
                "recall_min": min(s["recalls"]),
                "recall_max": max(s["recalls"]),
                "n_gnss_devices_total": s["n"],
                "predicted_as": dict(s["where"]),
                "confidence_mean": statistics.mean(s["conf"]),
                "fraction_below_threshold": s["low_conf"] / s["n"],
            }

    result = {"catalog": catalog_info,
              "protocol": {"ablation": "test only", "feature_set": FEATURE_SET_RESOURCE,
                           "train": args.train, "test": args.test, "seeds": args.seeds,
                           "anomaly_rate": 0.1, "confidence_threshold": CONFIDENCE_THRESHOLD},
              "results": summary}
    (out_dir / "gnss_ablation_summary.json").write_text(json.dumps(result, indent=2))

    for name, rows in (("gnss_ablation_per_seed.csv", per_seed_rows),
                       ("gnss_ablation_devices.csv", device_rows)):
        with open(out_dir / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

    (out_dir / "conditions.txt").write_text(
        f"args: {vars(args)}\npython: {platform.python_version()}\n"
        f"scikit-learn: {sklearn.__version__}\nnumpy: {np.__version__}\n"
        f"platform: {platform.platform()}\n")

    print("\n%-45s %-18s %s" % ("model | condition", "recall", "predicted as"))
    for key, v in summary.items():
        print("%-45s %.3f +- %.3f   %s" % (key, v["recall_mean"], v["recall_sd"], v["predicted_as"]))
    print(f"\nCatalogue {catalog_info['catalog_version']}; results in {out_dir}")


if __name__ == "__main__":
    main()