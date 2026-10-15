#!/usr/bin/env python3
"""
Synthetic -> real evaluation of the PnP-AI classifier.

Trains the classifier ONLY on synthetic announcements and tests it on the real
announcements captured from the lab devices (datasets/real/), comparing three
generators:

  profiles            hand-written ranges per class (generator.py, the current one)
  catalog_nominal     real boards from the catalog, announced memory = nominal RAM
  catalog_calibrated  real boards from the catalog, announced memory estimated
                      from the boards measured in the lab

The two catalog generators are run with the three labelings of the catalog:
clase_clasificador, clase_hw, and clase_hw with ESP32Device merged into
Microcontroller ('hw_unificado'). The profile generator has its own labels.
Under 'hw_unificado' the true label of the real ESP32 boards is relabeled the
same way, so every labeling is scored against its own taxonomy. Accuracies of
different labelings are NOT directly comparable: merging two classes removes a
decision boundary and makes the task easier.

Leave-one-board-out: to test a real board, the catalog generators are rebuilt
WITHOUT that board's row, neither for sampling nor for the memory calibration.
The unit that is held out is the catalog board_id, so the three ESP32-C3-Zero
units (same board) are held out together, and the Pi 5 is held out once for
both of its configurations (sbc-pi5-01 and gnss-pi5-01).

The unit that is scored is the configuration (device_id): its prediction is
the majority vote over its announcements.

Usage (from the repository root):
  python3 experiments/eval_synth_to_real.py
  python3 experiments/eval_synth_to_real.py --seeds 3 --models random_forest   # quick run

Outputs (in --out, default experiments/results/synth_to_real/):
  per_config.csv   one row per generator x labeling x model x seed x device_id
  summary.csv      accuracy per generator x labeling x model (mean/min/max over seeds)
  metadata.json    catalog version, real data counts, arguments, calibration table

Project: PINV01-24 - FIUNA
"""

import argparse
import collections
import csv
import json
import statistics
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pnp_ai.dataset_generator import SyntheticDatasetGenerator           # noqa: E402
from pnp_ai.dataset_generator.catalog import (                          # noqa: E402
    LABELINGS, CatalogDatasetGenerator, load_catalog, unify_label)
from pnp_ai.device_classifier import DeviceClassifier                   # noqa: E402

warnings.filterwarnings('ignore')

GENERATORS = ('profiles', 'catalog_nominal', 'catalog_calibrated')
ANOMALY_RATE = 0.1  # same as train_classifier_from_synthetic_data


# ---------------------------------------------------------------------------
# Real data
# ---------------------------------------------------------------------------

def load_real(real_dir: Path):
    """Valid real announcements: not retained, not excluded, mapped in dispositivos.csv."""
    with open(real_dir / 'dispositivos.csv', encoding='utf-8', newline='') as f:
        devices = {r['device_id']: r for r in csv.DictReader(f)}
    with open(real_dir / 'exclusiones.csv', encoding='utf-8', newline='') as f:
        exclusions = list(csv.DictReader(f))

    def excluded(rec):
        return any(e['device_id'] == rec['device_id'] and e['captured_at'] in ('*', rec['captured_at'])
                   for e in exclusions)

    total, records = 0, []
    with open(real_dir / 'announcements_real.jsonl', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            total += 1
            rec = json.loads(line)
            if rec.get('retained') or excluded(rec) or rec['device_id'] not in devices:
                continue
            d = devices[rec['device_id']]
            records.append({
                'device_id': rec['device_id'],
                'board_id': d['board_id'],
                'label_true': d['label_true'],
                'scenario': rec.get('scenario', ''),
                'payload': rec['payload'],
            })
    return records, total


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train(generator, model_type, feature_set, num_samples):
    """Same recipe as pnp_ai.device_classifier.train_classifier_from_synthetic_data."""
    clf = DeviceClassifier(model_type=model_type, feature_set=feature_set)
    devices = generator.generate_dataset(num_samples)
    devices = generator.generate_anomalies(devices, ANOMALY_RATE)
    clf.train([clf._extract_features(d) for d in devices], [d['device_type'] for d in devices])
    return clf


def make_generator(name, labeling, seed, catalog, exclude):
    if name == 'profiles':
        return SyntheticDatasetGenerator(seed=seed)
    return CatalogDatasetGenerator(seed=seed, catalog=catalog, labeling=labeling,
                                   calibrated=(name == 'catalog_calibrated'),
                                   exclude_boards=exclude)


def true_label(record, labeling):
    """Real label in the taxonomy of the labeling being evaluated."""
    if labeling == 'hw_unificado':
        return unify_label(record['label_true'])
    return record['label_true']


def score(clf, records, labeling):
    """Majority vote per device_id."""
    out = {}
    by_dev = collections.defaultdict(list)
    for r in records:
        by_dev[r['device_id']].append(r)
    for dev, recs in by_dev.items():
        results = [clf.classify(r['payload']) for r in recs]
        votes = collections.Counter(res.device_type for res in results)
        pred, _ = votes.most_common(1)[0]
        conf = statistics.mean(res.confidence for res in results if res.device_type == pred)
        truth = true_label(recs[0], labeling)
        out[dev] = {
            'board_id': recs[0]['board_id'],
            'label_true': truth,
            'pred': pred,
            'correct': int(pred == truth),
            'confidence': round(conf, 4),
            'n_announcements': len(recs),
            'n_correct_announcements': sum(res.device_type == truth for res in results),
        }
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--catalog', default=str(ROOT / 'datasets' / 'catalog' / 'catalogo_placas.csv'))
    ap.add_argument('--real-dir', default=str(ROOT / 'datasets' / 'real'))
    ap.add_argument('--out', default=str(ROOT / 'experiments' / 'results' / 'synth_to_real'))
    ap.add_argument('--seeds', type=int, default=10)
    ap.add_argument('--samples', type=int, default=2000)
    ap.add_argument('--models', nargs='+', default=['random_forest', 'decision_tree'])
    ap.add_argument('--feature-set', default='resource')
    ap.add_argument('--generators', nargs='+', default=list(GENERATORS), choices=GENERATORS)
    ap.add_argument('--labelings', nargs='+', default=list(LABELINGS), choices=LABELINGS)
    args = ap.parse_args()

    catalog = load_catalog(args.catalog)
    records, total = load_real(Path(args.real_dir))
    boards = sorted({r['board_id'] for r in records})
    missing = [b for b in boards if b not in {x.board_id for x in catalog.boards}]
    if missing:
        sys.exit(f"board_id(s) of the real data missing from the catalog: {missing}")

    print(f"catálogo {catalog.version} ({len(catalog.boards)} placas) | "
          f"anuncios reales válidos {len(records)} de {total} | "
          f"configuraciones {len({r['device_id'] for r in records})} | placas {len(boards)}")

    runs = []
    for gen_name in args.generators:
        for labeling in (['-'] if gen_name == 'profiles' else args.labelings):
            runs.append((gen_name, labeling))

    rows = []
    t0 = time.time()
    for gen_name, labeling in runs:
        for model in args.models:
            for seed in range(args.seeds):
                if gen_name == 'profiles':
                    clf = train(make_generator(gen_name, labeling, seed, catalog, ()),
                                model, args.feature_set, args.samples)
                    scored = score(clf, records, labeling)
                else:
                    scored = {}
                    for held_out in boards:   # leave-one-board-out
                        gen = make_generator(gen_name, labeling, seed, catalog, [held_out])
                        clf = train(gen, model, args.feature_set, args.samples)
                        test = [r for r in records if r['board_id'] == held_out]
                        scored.update(score(clf, test, labeling))
                for dev, s in sorted(scored.items()):
                    rows.append({'generator': gen_name, 'labeling': labeling, 'model': model,
                                 'seed': seed, 'device_id': dev, **s})
            print(f"  {gen_name:19s} {labeling:12s} {model:14s} listo ({time.time() - t0:.0f} s)")

    # -- summary ---------------------------------------------------------------
    summary = []
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r['generator'], r['labeling'], r['model'])].append(r)
    for (gen_name, labeling, model), rs in groups.items():
        per_seed = collections.defaultdict(list)
        fails = collections.Counter()
        for r in rs:
            per_seed[r['seed']].append(r['correct'])
            if not r['correct']:
                fails[f"{r['device_id']}->{r['pred']}"] += 1
        accs = [sum(v) / len(v) for v in per_seed.values()]
        summary.append({
            'generator': gen_name, 'labeling': labeling, 'model': model,
            'seeds': len(accs), 'configs': len(next(iter(per_seed.values()))),
            'acc_mean': round(statistics.mean(accs), 4),
            'acc_min': round(min(accs), 4), 'acc_max': round(max(accs), 4),
            'conf_mean': round(statistics.mean(r['confidence'] for r in rs), 4),
            'failures': '; '.join(f"{k} ({n}/{len(accs)})" for k, n in fails.most_common()),
        })

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / 'per_config.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(out / 'summary.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)

    calib = CatalogDatasetGenerator(seed=0, catalog=catalog).describe()
    meta = {
        'date': datetime.now().isoformat(timespec='seconds'),
        'catalog_path': str(Path(args.catalog).resolve().relative_to(ROOT)) if Path(args.catalog).resolve().is_relative_to(ROOT) else args.catalog,
        'catalog_version': catalog.version,
        'catalog_boards': len(catalog.boards),
        'real_announcements_total': total,
        'real_announcements_valid': len(records),
        'real_configs': sorted({r['device_id'] for r in records}),
        'real_boards': boards,
        'args': vars(args),
        'catalog_generator_full': {k: calib[k] for k in ('classes', 'calibration')},
    }
    (out / 'metadata.json').write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')

    print(f"\n| generador | etiquetado | modelo | acierto medio | mín | máx | conf. media | fallas (semillas) |")
    print("|---|---|---|---|---|---|---|---|")
    for s in summary:
        print(f"| {s['generator']} | {s['labeling']} | {s['model']} | {s['acc_mean']:.3f} | "
              f"{s['acc_min']:.3f} | {s['acc_max']:.3f} | {s['conf_mean']:.2f} | {s['failures'] or '-'} |")
    print(f"\nResultados en {out}")


if __name__ == '__main__':
    main()
