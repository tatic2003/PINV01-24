"""
GC diagnostic: are the slow events of the baseline pauses of Python's garbage collector?
=======================================================================================
In the baseline (run_benchmark.py, run_distributed_benchmark.py) a few events
per 3000 take more than 100 ms, and all of that time is in the SHACL stage.
This script repeats exactly that stage on exactly the same events:

  * the same announcements: the benchmark generator (make_generator, same
    training source and seed, 100 events x 30 repetitions after 10 warm-up
    events per repetition);
  * the same work: a throwaway single-device graph built with
    central_baseline.build_device_graph, validated with SHACLValidator, timed
    from the start of the graph build to the end of the validation (as the
    benchmarks' t_shacl_ms);

twice: with the cyclic garbage collector enabled (the default, what a deployed
node runs with) and disabled. Every collector pause is recorded through
gc.callbacks and attributed to the event during which it happened.

If the slow events disappear with the collector off, and with it on they are
the events during which a generation-2 collection ran, the slow events are
collector pauses, not the validation code.

Run from the repository root (on the node whose baseline is being explained):
    python baseline/diagnose_gc.py --output-dir baseline/results_pi5_catalog

Outputs in --output-dir:
  gc_diagnostic_events.csv   one row per event and series: SHACL time, whether
                             it conformed, collector pauses during the event
                             (count, highest generation, total pause time)
  gc_diagnostic.json         per series: n, median, mean, s.d., P95, P99, max,
                             events over 50 and 100 ms, how many slow events
                             had a generation-2 pause, pauses per generation;
                             plus the generator, seed, catalog version, Python
                             version and collector thresholds

Project: PINV01-24 - FIUNA
"""

import argparse
import csv
import gc
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from baseline.central_baseline import (DEFAULT_TRAINING_SOURCE, build_device_graph,  # noqa: E402
                                       make_generator, training_metadata)
from semantic_layer.validator import SHACLValidator  # noqa: E402

SLOW_MS = 100.0
CSV_COLUMNS = ['series', 'repetition', 'event', 'device_id', 't_shacl_ms', 'conforms',
               'gc_pauses', 'gc_max_generation', 'gc_pause_ms']


class GCRecorder:
    """Collector pauses as (start, end, generation), from gc.callbacks."""

    def __init__(self):
        self.pauses = []
        self._start = None

    def __call__(self, phase, info):
        now = time.perf_counter()
        if phase == 'start':
            self._start = now
        elif self._start is not None:
            self.pauses.append((self._start, now, info['generation']))
            self._start = None

    def during(self, t0, t1):
        """Pauses that started within [t0, t1]."""
        return [p for p in self.pauses if t0 <= p[0] <= t1]


def run_series(validator, source, seed, events, repetitions, warmup, gc_enabled, label):
    """One series: the benchmark's events through build + SHACL, collector on or off."""
    gen = make_generator(source, seed)
    rec = GCRecorder()
    rows = []
    gc.collect()
    (gc.enable if gc_enabled else gc.disable)()
    gc.callbacks.append(rec)
    try:
        for rep in range(repetitions):
            devices = gen.generate_dataset(num_devices=events + warmup)
            for i, device in enumerate(devices):
                t0 = time.perf_counter()
                result = validator.validate(build_device_graph(device))
                t1 = time.perf_counter()
                if i < warmup:
                    continue
                pauses = rec.during(t0, t1)
                rows.append({
                    'series': label, 'repetition': rep, 'event': i - warmup,
                    'device_id': device.get('device_id', device.get('id', '')),
                    't_shacl_ms': round((t1 - t0) * 1000.0, 4),
                    'conforms': bool(result.is_valid),
                    'gc_pauses': len(pauses),
                    'gc_max_generation': max((g for _, _, g in pauses), default=''),
                    'gc_pause_ms': round(sum((e - s) for s, e, _ in pauses) * 1000.0, 4),
                })
    finally:
        gc.callbacks.remove(rec)
        gc.enable()
    return rows, rec.pauses


def summarize(rows, pauses, gc_enabled):
    t = np.array([r['t_shacl_ms'] for r in rows])
    slow = [r for r in rows if r['t_shacl_ms'] > SLOW_MS]
    per_gen = {}
    for g in (0, 1, 2):
        d = [(e - s) * 1000.0 for s, e, gen in pauses if gen == g]
        per_gen[f'gen{g}'] = {'count': len(d), 'max_ms': round(max(d), 3) if d else 0.0,
                              'median_ms': round(float(np.median(d)), 3) if d else 0.0}
    return {
        'gc_enabled': gc_enabled,
        'events': len(rows),
        'conforming': sum(r['conforms'] for r in rows),
        'median_ms': round(float(np.median(t)), 3),
        'mean_ms': round(float(t.mean()), 3),
        'sd_ms': round(float(t.std(ddof=1)), 3),
        'p95_ms': round(float(np.percentile(t, 95)), 3),
        'p99_ms': round(float(np.percentile(t, 99)), 3),
        'max_ms': round(float(t.max()), 3),
        'events_over_50ms': int((t > 50.0).sum()),
        'events_over_100ms': len(slow),
        'slow_events_with_gen2_pause': sum(1 for r in slow if r['gc_max_generation'] == 2),
        'slow_events_with_any_pause': sum(1 for r in slow if r['gc_pauses'] > 0),
        'gc_pauses': per_gen,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--events', type=int, default=100, help='events per repetition (as the benchmarks)')
    ap.add_argument('--repetitions', type=int, default=30)
    ap.add_argument('--warmup', type=int, default=10)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--training-source', choices=['profiles', 'catalog'], default=DEFAULT_TRAINING_SOURCE,
                    help=f'generator of the events (default: {DEFAULT_TRAINING_SOURCE}, as the benchmarks)')
    ap.add_argument('--output-dir', default='baseline/results')
    args = ap.parse_args(argv)

    validator = SHACLValidator()
    series = {}
    all_rows = []
    for label, enabled in (('gc_on', True), ('gc_off', False)):
        print(f"{label}: {args.repetitions} x {args.events} events ...", flush=True)
        rows, pauses = run_series(validator, args.training_source, args.seed, args.events,
                                  args.repetitions, args.warmup, enabled, label)
        series[label] = summarize(rows, pauses, enabled)
        all_rows += rows

    meta = training_metadata(args.training_source, None, args.seed)
    meta.pop('training_samples', None)
    results = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'python': sys.version.split()[0],
        'gc_thresholds': list(gc.get_threshold()),
        'events_source': meta,
        'events_per_repetition': args.events, 'repetitions': args.repetitions, 'warmup': args.warmup,
        'slow_threshold_ms': SLOW_MS,
        **series,
    }

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / 'gc_diagnostic_events.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(all_rows)
    (out / 'gc_diagnostic.json').write_text(json.dumps(results, indent=2), encoding='utf-8')

    keys = ('events', 'conforming', 'median_ms', 'p95_ms', 'p99_ms', 'max_ms', 'sd_ms',
            'events_over_50ms', 'events_over_100ms', 'slow_events_with_gen2_pause')
    print(f"\n{'':30s}{'GC on':>10s}{'GC off':>10s}")
    for k in keys:
        print(f"{k:30s}{series['gc_on'][k]:>10}{series['gc_off'][k]:>10}")
    g2 = series['gc_on']['gc_pauses']['gen2']
    print(f"\nGC on: generation-2 pauses = {g2['count']}, median {g2['median_ms']} ms, longest {g2['max_ms']} ms")
    print(f"Saved -> {out / 'gc_diagnostic.json'}, {out / 'gc_diagnostic_events.csv'}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
