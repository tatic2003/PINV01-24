#!/usr/bin/env python3
"""
Invalid-event experiment: how often each validation path rejects a malformed
announcement, and what the rejection costs.

Valid announcements are drawn from the real-board catalog (the paper's
training source). Each event then gets exactly ONE defect from a fixed list of
categories, plus a 'valid' control category with no defect. Every event goes
through two paths:

  server  POST /api/devices/register on the real Flask app (in process, test
          client): the lightweight validator, PnP-AI classification, full
          SHACL (unless --no-shacl-on-register), the RDF graph and the signed
          BlockDAG. Accepted = HTTP 200.
          This is the path a device actually takes when it registers.
  shacl   the full SHACL validation used by the baseline: one RDF graph per
          event, then pyshacl with the ontology hierarchy. If the RDF store
          refuses to build the graph (for example, no device_id), the event
          counts as rejected - it is never validated as an empty graph.

Categories (one defect each):
  valid                   no defect (control: false-rejection rate)
  missing_id              no device_id / id
  missing_ip              no ip_address / ip
  missing_hardware        no hardware_type / hardware
  bad_ip                  ip '999.12.0.1'
  bad_id                  device_id with a space and '#'
  bad_mac                 mac 'ZZ:ZZ:ZZ:ZZ:ZZ:ZZ' (a warning in both paths)
  gnss_missing_fix        GNSS receiver without fix_quality
  gnss_fix_no_position    GNSS receiver with a fix but no latitude/longitude
  gnss_lat_out_of_range   GNSS receiver with latitude 123
  missing_memory          no memory_total
  memory_free_gt_total    memory_free larger than memory_total
  negative_memory         memory_total < 0
  memory_not_a_number     memory_total 'lots'

The server validates before classifying, so a rejected event does not pay for
the classification.

Output (in --output-dir):
  invalid_events.csv          one row per event and path
  invalid_events_summary.csv  per category and path: n, rejected, rate,
                              median / P95 time, most frequent message
  invalid_events_meta.json    arguments, catalog version, versions

Usage (Pi 5, repository folder, server and bridge stopped):
  python experiments/run_invalid_events.py --n 100 --output-dir experiments/results/invalid_events

Project: PINV01-24 - FIUNA
"""

import argparse
import copy
import csv
import json
import logging
import platform
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pnp_ai.dataset_generator.catalog import CatalogDatasetGenerator  # noqa: E402
from semantic_layer.rdf_store import RDFStore                         # noqa: E402
from semantic_layer.validator import SHACLValidator                   # noqa: E402
from server import create_app, Config                                 # noqa: E402

GNSS_KEYS = ('fix_quality', 'latitude', 'longitude')


def _drop(d, *keys):
    for k in keys:
        d.pop(k, None)


def _is_gnss(d):
    return 'fix_quality' in d


# Each mutator takes a deep copy of a valid announcement and returns it with
# one defect. The GNSS ones need a GNSS sample and are only applied to those.
MUTATORS = {
    'valid':                 lambda d: d,
    'missing_id':            lambda d: (_drop(d, 'device_id', 'id'), d)[1],
    'missing_ip':            lambda d: (_drop(d, 'ip_address', 'ip'), d)[1],
    'missing_hardware':      lambda d: (_drop(d, 'hardware_type', 'hardware'), d)[1],
    'bad_ip':                lambda d: (d.update(ip_address='999.12.0.1'), d)[1],
    'bad_id':                lambda d: (d.update(device_id=d['device_id'].replace('-', ' #', 1)), d)[1],
    'bad_mac':               lambda d: (d.update(mac_address='ZZ:ZZ:ZZ:ZZ:ZZ:ZZ'), d)[1],
    'gnss_missing_fix':      lambda d: (_drop(d, 'fix_quality'), d)[1],
    'gnss_fix_no_position':  lambda d: (d.update(fix_quality=1), _drop(d, 'latitude', 'longitude'), d)[2],
    'gnss_lat_out_of_range': lambda d: (d.update(fix_quality=1, latitude=123.0,
                                                 longitude=d.get('longitude', -57.5759)), d)[1],
    'missing_memory':        lambda d: (_drop(d, 'memory_total'), d)[1],
    'memory_free_gt_total':  lambda d: (d.update(memory_free=int(d['memory_total']) * 2), d)[1],
    'negative_memory':       lambda d: (d.update(memory_total=-int(d['memory_total'])), d)[1],
    'memory_not_a_number':   lambda d: (d.update(memory_total='lots'), d)[1],
}
GNSS_ONLY = {'gnss_missing_fix', 'gnss_fix_no_position', 'gnss_lat_out_of_range'}


def build_events(n, seed):
    """n events per category, each from a fresh valid catalog announcement."""
    gen = CatalogDatasetGenerator(seed=seed, calibrated=True)   # server's labeling
    plain, gnss = [], []
    while len(plain) < n * (len(MUTATORS) - len(GNSS_ONLY)) or len(gnss) < n * len(GNSS_ONLY):
        d = gen.generate_device()
        (gnss if _is_gnss(d) else plain).append(d)
    events = []
    for cat, mutate in MUTATORS.items():
        pool = gnss if cat in GNSS_ONLY else plain
        for _ in range(n):
            events.append((cat, mutate(copy.deepcopy(pool.pop()))))
    return events, gen.catalog.version


def server_path(app, event):
    client = app.test_client()
    t0 = time.perf_counter()
    try:
        resp = client.post('/api/devices/register', json=event)
        status = resp.status_code
        body = resp.get_json(silent=True) or {}
    except Exception as exc:          # the test client re-raises unhandled errors
        status, body = 500, {'error': f'{type(exc).__name__}: {exc}'}
    ms = (time.perf_counter() - t0) * 1000.0
    msg = ''
    if status != 200:
        viol = (body.get('validation') or {}).get('violations') or body.get('violations') or []
        msg = (viol[0].get('message') if viol and isinstance(viol[0], dict) else '') \
            or str(body.get('error') or body.get('message') or '')[:120]
    return status == 200, status, ms, msg


def shacl_path(validator, event):
    t0 = time.perf_counter()
    store = RDFStore(load_ontology=False)
    try:
        store.register_device(event)
    except Exception as exc:
        return False, 'rdf_error', (time.perf_counter() - t0) * 1000.0, f'{type(exc).__name__}: {exc}'[:120]
    result = validator.validate(store.graph)
    ms = (time.perf_counter() - t0) * 1000.0
    msg = ''
    if not result.is_valid and result.violations:
        v = result.violations[0]
        msg = str(v.get('message') if isinstance(v, dict) else v)[:120]
    return result.is_valid, 'conforms' if result.is_valid else 'violation', ms, msg


def pct(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * q))] if s else float('nan')


def main():
    ap = argparse.ArgumentParser(description='Invalid-event rejection rate and cost')
    ap.add_argument('--n', type=int, default=100, help='events per category (default 100)')
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--training-samples', type=int, default=2000)
    ap.add_argument('--shacl-on-register', action=argparse.BooleanOptionalAction, default=True,
                    help='server path also runs full SHACL, as the server does by default')
    ap.add_argument('--output-dir', default='experiments/results/invalid_events')
    args = ap.parse_args()
    logging.disable(logging.WARNING)   # the server logs every registration

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    key_file = Path(tempfile.mkdtemp()) / 'dag_key.hex'
    app = create_app(Config(classifier_type='random_forest', classifier_feature_set='resource',
                            training_source='catalog', training_samples=args.training_samples,
                            enable_dag=True, dag_key_file=str(key_file),
                            shacl_on_register=args.shacl_on_register))
    app.testing = False
    validator = SHACLValidator()

    events, catalog_version = build_events(args.n, args.seed)
    print(f'{len(events)} events ({args.n} per category), catalog {catalog_version}')

    # Warm-up: first calls pay one-off costs (imports, caches).
    for _, ev in events[:10]:
        server_path(app, copy.deepcopy(ev))
        shacl_path(validator, copy.deepcopy(ev))

    rows = []
    for i, (cat, ev) in enumerate(events):
        acc, status, ms, msg = server_path(app, copy.deepcopy(ev))
        rows.append(dict(category=cat, path='server', accepted=int(acc), outcome=status,
                         time_ms=round(ms, 3), message=msg))
        acc, status, ms, msg = shacl_path(validator, copy.deepcopy(ev))
        rows.append(dict(category=cat, path='shacl', accepted=int(acc), outcome=status,
                         time_ms=round(ms, 3), message=msg))
        if (i + 1) % 200 == 0:
            print(f'  {i + 1}/{len(events)}')

    with open(out / 'invalid_events.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    groups = defaultdict(list)
    for r in rows:
        groups[(r['category'], r['path'])].append(r)
    summary = []
    for cat in MUTATORS:
        for path in ('server', 'shacl'):
            g = groups[(cat, path)]
            rejected = [r for r in g if not r['accepted']]
            times = [r['time_ms'] for r in g]
            outcomes = Counter(str(r['outcome']) for r in g)
            msgs = Counter(r['message'] for r in rejected if r['message'])
            summary.append(dict(
                category=cat, path=path, n=len(g), rejected=len(rejected),
                rejection_rate=round(len(rejected) / len(g), 3),
                time_median_ms=round(statistics.median(times), 3),
                time_p95_ms=round(pct(times, 0.95), 3),
                outcomes=';'.join(f'{k}:{v}' for k, v in outcomes.most_common()),
                top_message=msgs.most_common(1)[0][0] if msgs else ''))
    with open(out / 'invalid_events_summary.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)

    meta = dict(generated_at=datetime.now().astimezone().isoformat(timespec='seconds'),
                args=vars(args), catalog_version=catalog_version,
                python=platform.python_version(), machine=platform.machine(),
                categories=list(MUTATORS))
    (out / 'invalid_events_meta.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')

    print(f"\n{'category':<24}{'path':<8}{'rejected':>10}{'median ms':>11}{'P95 ms':>9}  outcome")
    for s in summary:
        print(f"{s['category']:<24}{s['path']:<8}{s['rejected']:>5}/{s['n']:<4}"
              f"{s['time_median_ms']:>11.2f}{s['time_p95_ms']:>9.2f}  {s['outcomes']}")
    print(f'\nResults in {out.resolve()}')


if __name__ == '__main__':
    main()
