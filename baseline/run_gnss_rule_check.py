"""
What happens to a GNSS receiver that does not declare its capability?

A receiver announces the resources of its host (a Raspberry Pi, an ESP32), so
memory, CPU and link type say nothing about it. The capability it declares
does, and that is a decision for a rule, not for a model: the server types a
device that declares ``gnss`` before the classifier runs
(``pnp_ai.capability_rules``), and the classifier is trained without the class
and without a feature for the declaration.

This script measures the two sides of that design on the same test set:

  declared      the announcement as the agent sends it. The rule has to type
                every receiver, with no false positive among the other devices.
  not declared  the same announcements with the capability removed. Nothing is
                left to recognize, so the device is typed by the resources of
                its host; the class it receives and the confidence of that
                prediction are what this script reports.

It replaces the earlier ablation of the ``declares_gnss`` feature, which no
longer exists. The protocol is the same, so the two are comparable.

Server configuration reproduced here (see server/app.py, server/config.py):
  - CatalogDatasetGenerator, calibrated memory, default (hardware) labeling
  - feature set 'resource' (the hardware name is NOT a feature)
  - 2000 training announcements with 10% anomalies, no GNSSReceiver class
  - random forest and decision tree, same hyperparameters as DeviceClassifier
Each seed changes the generator seed of the training set; the test set comes
from a different seed (seed + 10_000), so train and test share no announcement.
The test set is drawn with ``include_gnss=True``: the receivers it contains are
the devices under test, and they are never part of the training data.

Run from the repository root:
    python baseline/run_gnss_rule_check.py [--seeds 10] [--train 2000] [--test 400]
                                           [--out baseline/results_gnss_rule]

Output folder:
    gnss_rule_summary.json   recall per model and condition, where the devices
                             go, mean confidence, rule false positives
    gnss_rule_per_seed.csv   one row per (model, seed, condition)
    gnss_rule_devices.csv    one row per receiver and condition (no coordinates)
    conditions.txt           arguments, library versions, platform

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

from pnp_ai.capability_rules import GNSS_RULE_CLASS, declares_gnss
from pnp_ai.dataset_generator.catalog import (
    DEFAULT_LABELING, GNSS_LABEL, CatalogDatasetGenerator)
from pnp_ai.device_classifier import train_classifier_from_synthetic_data
from pnp_ai.features import FEATURE_SET_RESOURCE, GNSS_CAPABILITY_NAMES

warnings.filterwarnings("ignore")

MODELS = ("decision_tree", "random_forest")
DECLARED, NOT_DECLARED = "declared", "not declared"
CONDITIONS = (DECLARED, NOT_DECLARED)
CONFIDENCE_THRESHOLD = 0.6  # server default: below it the device stays IoTDevice


def make_training_generator(seed):
    """The generator the server trains with: no GNSSReceiver class."""
    return CatalogDatasetGenerator(seed=seed, labeling=DEFAULT_LABELING, calibrated=True)


def make_test_devices(n, seed):
    """Test announcements, receivers included, from an unseen seed."""
    gen = CatalogDatasetGenerator(seed=seed, labeling=DEFAULT_LABELING,
                                  calibrated=True, include_gnss=True)
    return gen.generate_anomalies(gen.generate_dataset(n), 0.1), gen


def undeclare(device):
    """The same announcement without its GNSS capability."""
    out = dict(device)
    caps = [c for c in (out.get("capabilities") or [])
            if str(c).strip().lower() not in set(GNSS_CAPABILITY_NAMES)]
    if caps:
        out["capabilities"] = caps
    else:
        out.pop("capabilities", None)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--train", type=int, default=2000)
    ap.add_argument("--test", type=int, default=400)
    ap.add_argument("--out", default=str(Path(__file__).parent / "results_gnss_rule"))
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    per_seed_rows, device_rows, summary = [], [], {}
    catalog_info = None
    rule_false_positives, non_gnss_total = 0, 0

    for model in MODELS:
        stats = {c: {"recalls": [], "where": Counter(), "conf": [], "low_conf": 0, "n": 0}
                 for c in CONDITIONS}
        for seed in range(args.seeds):
            clf, _ = train_classifier_from_synthetic_data(
                args.train, model, feature_set=FEATURE_SET_RESOURCE,
                generator=make_training_generator(seed))
            test, test_gen = make_test_devices(args.test, seed + 10_000)
            if catalog_info is None:
                info = test_gen.describe()
                catalog_info = {k: info[k] for k in
                                ("catalog_version", "catalog_boards", "labeling", "calibrated")}
            gnss = [d for d in test if d["device_type"] == GNSS_LABEL]

            # The rule must not fire on anything else. Counted once per seed,
            # on the announcements as generated.
            if model == MODELS[0]:
                others = [d for d in test if d["device_type"] != GNSS_LABEL]
                non_gnss_total += len(others)
                rule_false_positives += sum(declares_gnss(d) for d in others)

            for cond in CONDITIONS:
                devices = gnss if cond == DECLARED else [undeclare(d) for d in gnss]
                results = [clf.classify(d) for d in devices]
                preds = [(r.device_type, r.confidence) for r in results]
                hits = sum(p == GNSS_RULE_CLASS for p, _ in preds)
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
                  f"declared {per_seed_rows[-2]['recall']:.3f}  "
                  f"not declared {per_seed_rows[-1]['recall']:.3f}  (n_gnss={len(gnss)})")

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
              "protocol": {"decided_by": "rule on the declared capability",
                           "feature_set": FEATURE_SET_RESOURCE,
                           "train": args.train, "test": args.test, "seeds": args.seeds,
                           "anomaly_rate": 0.1, "confidence_threshold": CONFIDENCE_THRESHOLD},
              "rule_false_positives": {"devices": non_gnss_total,
                                       "declared_gnss": rule_false_positives},
              "results": summary}
    (out_dir / "gnss_rule_summary.json").write_text(json.dumps(result, indent=2))

    for name, rows in (("gnss_rule_per_seed.csv", per_seed_rows),
                       ("gnss_rule_devices.csv", device_rows)):
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
    print(f"\nRule false positives: {rule_false_positives} of {non_gnss_total} other devices")
    print(f"Catalogue {catalog_info['catalog_version']}; results in {out_dir}")


if __name__ == "__main__":
    main()
