#!/usr/bin/env python3
"""
Rule baseline vs ML on boards the model has never seen.

Question: does the ML classifier generalize to hardware it was not trained on,
better than the rule-based baseline?

The rule baseline (DeviceClassifier(model_type='rule_based')) looks for known
substrings in the announced hardware name ('esp32', 'raspberry', 'jetson',
'gateway', 'sensor') and, when none matches, falls back to memory thresholds.
It works when the name is one it knows, and that is exactly what a synthetic
test set built from the same name lists rewards.

Here every board of the catalog is held out in turn (leave-one-board-out):
  * the ML model is trained on synthetic announcements from the OTHER boards
    (calibrated catalog generator, the held-out board removed from sampling
    and from the memory calibration);
  * the held-out board then produces --per-board announcements with its REAL
    product name as hardware string (catalog 'hardware_string', else 'placa'),
    its catalog link and CPU, and its calibrated announced memory (its own
    lab measurement when it has one);
  * the rule baseline and the ML model classify the same announcements;
  * 'hybrid' = the rules when they recognize the name (a name rule fired,
    confidence >= 0.9), otherwise the first ML model: what a deployment would
    do to keep the rules' certainty on known hardware and the ML's
    generalization on unknown hardware.

'rules_noname' is the same rule classifier on the same announcements with
the board and chip names removed (pnp_ai.features.without_hardware_name), so
it decides by declared capabilities and the memory thresholds alone. It tells
how much of the rule baseline comes from recognizing names.

--models accepts every learned type of DeviceClassifier: random_forest,
decision_tree, svm, mlp, logistic_regression and knn.

Scored unit: the board (majority vote over its announcements). Labels: the
catalog labeling chosen with --labeling. GNSS receivers are not included:
both approaches decide them from the declared capability, identically.

Caveat to report: the held-out announcements are generated (from real names
and datasheet values, calibrated with the lab measurements), not captured.
datasets/real/ remains the test on captured data (eval_synth_to_real.py).

Usage (from the repository root):
  python3 experiments/eval_unseen_boards.py
  python3 experiments/eval_unseen_boards.py --seeds 1 --labeling hw   # quick

Outputs (in --out, default experiments/results/unseen_boards/):
  per_board.csv, summary.csv, metadata.json

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

from pnp_ai.dataset_generator.catalog import (                          # noqa: E402
    DEFAULT_LABELING, LABELINGS, CatalogDatasetGenerator, load_catalog)
from pnp_ai.device_classifier import DeviceClassifier                   # noqa: E402
from pnp_ai.features import without_hardware_name                       # noqa: E402

warnings.filterwarnings('ignore')
ANOMALY_RATE = 0.1


def train_ml(catalog, labeling, seed, held_out, model_type, feature_set, samples):
    gen = CatalogDatasetGenerator(seed=seed, catalog=catalog, labeling=labeling,
                                  exclude_boards=[held_out], include_gnss=False)
    devices = gen.generate_anomalies(gen.generate_dataset(samples), ANOMALY_RATE)
    clf = DeviceClassifier(model_type=model_type, feature_set=feature_set)
    clf.train([clf._extract_features(d) for d in devices], [d['device_type'] for d in devices])
    return clf


#: Confidence of the rule baseline when a NAME rule fired (memory fallbacks are 0.5-0.7).
RULE_NAME_CONFIDENCE = 0.9


def vote(clf, announcements):
    results = [clf.classify(a) for a in announcements]
    pred, _ = collections.Counter(r.device_type for r in results).most_common(1)[0]
    return pred


def vote_hybrid(rules, clf, announcements):
    preds = []
    for a in announcements:
        r = rules.classify(a)
        preds.append(r.device_type if r.confidence >= RULE_NAME_CONFIDENCE
                     else clf.classify(a).device_type)
    return collections.Counter(preds).most_common(1)[0][0]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--catalog', default=str(ROOT / 'datasets' / 'catalog' / 'catalogo_placas.csv'))
    ap.add_argument('--out', default=str(ROOT / 'experiments' / 'results' / 'unseen_boards'))
    ap.add_argument('--labeling', default=DEFAULT_LABELING, choices=LABELINGS)
    ap.add_argument('--seeds', type=int, default=10)
    ap.add_argument('--samples', type=int, default=2000)
    ap.add_argument('--per-board', type=int, default=5, help='announcements per held-out board')
    ap.add_argument('--models', nargs='+', default=['random_forest', 'decision_tree'])
    ap.add_argument('--feature-set', default='resource')
    args = ap.parse_args()

    catalog = load_catalog(args.catalog)
    rules = DeviceClassifier(model_type='rule_based')
    boards = list(catalog.boards)
    print(f"catálogo {catalog.version} ({len(boards)} placas) | etiquetado {args.labeling} | "
          f"{args.seeds} semilla(s) x {len(boards)} placas excluidas de a una")

    rows = []
    t0 = time.time()
    for seed in range(args.seeds):
        # Announcements of every board, generated once per seed with the full catalog
        # (the held-out board's own measurement is its ground truth, not training data).
        tester = CatalogDatasetGenerator(seed=1000 + seed, catalog=catalog, labeling=args.labeling,
                                         include_gnss=False)
        for i, board in enumerate(boards):
            truth = board.label(args.labeling)
            test = [tester.device_from_board(board, truth) for _ in range(args.per_board)]
            row = {'seed': seed, 'board_id': board.board_id, 'placa': board.placa,
                   'hardware': board.announced_name, 'label_true': truth,
                   'link_example': test[0]['link_type'],
                   'memory_example': test[0]['memory_total'], 'cpu_example': test[0]['cpu_freq_mhz'],
                   'pred_rules': vote(rules, test),
                   'pred_rules_noname': vote(rules, [without_hardware_name(a) for a in test])}
            for k, model in enumerate(args.models):
                clf = train_ml(catalog, args.labeling, seed, board.board_id, model,
                               args.feature_set, args.samples)
                row[f'pred_{model}'] = vote(clf, test)
                if k == 0:
                    row['pred_hybrid'] = vote_hybrid(rules, clf, test)
            rows.append(row)
            if (i + 1) % 20 == 0:
                print(f"  semilla {seed}: {i + 1}/{len(boards)} placas ({time.time() - t0:.0f} s)")

    methods = ['rules', 'rules_noname'] + args.models + ['hybrid']
    classes = sorted({r['label_true'] for r in rows})

    summary = []
    for m in methods:
        per_seed = collections.defaultdict(list)
        per_class = collections.defaultdict(list)
        for r in rows:
            ok = int(r[f'pred_{m}'] == r['label_true'])
            per_seed[r['seed']].append(ok)
            per_class[r['label_true']].append(ok)
        accs = [sum(v) / len(v) for v in per_seed.values()]
        recalls = {c: round(sum(per_class[c]) / len(per_class[c]), 3) for c in classes}
        summary.append({'method': m, 'acc_mean': round(statistics.mean(accs), 4),
                        'acc_min': round(min(accs), 4), 'acc_max': round(max(accs), 4),
                        'macro_recall': round(statistics.mean(recalls.values()), 4),
                        **{f'recall_{c}': recalls[c] for c in classes}})

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    suffix = f'_{args.labeling}'
    for name, data in ((f'per_board{suffix}.csv', rows), (f'summary{suffix}.csv', summary)):
        with open(out / name, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
    meta = {'date': datetime.now().isoformat(timespec='seconds'), 'catalog_version': catalog.version,
            'catalog_boards': len(boards), 'class_counts': dict(collections.Counter(
                b.label(args.labeling) for b in boards)), 'args': vars(args)}
    (out / f'metadata{suffix}.json').write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                                 encoding='utf-8')

    print(f"\n| método | acierto por placa (mín-máx) | recall macro | " + ' | '.join(classes) + ' |')
    print('|---|---|---|' + '---|' * len(classes))
    for s in summary:
        print(f"| {s['method']} | {s['acc_mean']:.3f} ({s['acc_min']:.3f}-{s['acc_max']:.3f}) | "
              f"{s['macro_recall']:.3f} | " + ' | '.join(f"{s[f'recall_{c}']:.2f}" for c in classes) + ' |')

    # Boards the rules get wrong and the ML gets right (and vice versa), first seed.
    first = [r for r in rows if r['seed'] == 0]
    ml = args.models[0]
    print(f"\nPrimera semilla — reglas fallan y {ml} acierta:")
    for r in first:
        if r['pred_rules'] != r['label_true'] and r[f'pred_{ml}'] == r['label_true']:
            print(f"  {r['placa'][:45]:45s} {r['label_true']:20s} reglas→{r['pred_rules']}")
    print(f"Primera semilla — {ml} falla y reglas aciertan:")
    for r in first:
        if r[f'pred_{ml}'] != r['label_true'] and r['pred_rules'] == r['label_true']:
            print(f"  {r['placa'][:45]:45s} {r['label_true']:20s} {ml}→{r[f'pred_{ml}']}")
    print(f"\nResultados en {out}")


if __name__ == '__main__':
    main()
