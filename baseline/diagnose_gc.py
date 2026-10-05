"""
GC Diagnostic — where do the latency outliers in the SHACL stage come from?
===========================================================================
The benchmarks report a standard deviation of ~4 ms with a P95 of ~8.5 ms, which
only happens when a handful of events are far slower than the rest. This script
validates the same kind of single-device graph as the benchmarks, twice:

    1. with Python's cyclic garbage collector enabled (the default, what a real
       edge node runs with)
    2. with it disabled

and records every collector pause through gc.callbacks. If the extreme events
(> 50 ms) disappear with the collector off, and the pauses match the slow events,
the outliers are collector pauses and not the code or the network.

Run from the repository root:
    python baseline/diagnose_gc.py [--events 3000] [--output-dir baseline/results]

Output: gc_diagnostic.json in the output directory.

Project: PINV01-24 - FIUNA
"""

import argparse
import gc
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from semantic_layer.rdf_store import RDFStore
from semantic_layer.validator import SHACLValidator

EXTREME_MS = 50.0


def _validate_once(validator: SHACLValidator, i: int) -> float:
    """Build a throwaway single-device graph (as the benchmarks do) and time SHACL."""
    store = RDFStore(load_ontology=False)
    store.register_device(
        {'device_id': f'esp-{i}', 'ip_address': '10.0.0.1', 'hardware_type': 'ESP32'},
        device_type='ESP32Device',
    )
    t0 = time.perf_counter()
    validator.validate(store.graph)
    return (time.perf_counter() - t0) * 1000.0


def run_series(validator: SHACLValidator, events: int, gc_enabled: bool) -> dict:
    gc.collect()
    (gc.enable if gc_enabled else gc.disable)()

    pauses = []          # (generation, duration_ms)
    started = {}

    def on_gc(phase, info):
        if phase == 'start':
            started['t'] = time.perf_counter()
        else:
            pauses.append((info['generation'], (time.perf_counter() - started['t']) * 1000.0))

    gc.callbacks.append(on_gc)
    try:
        samples = np.array([_validate_once(validator, i) for i in range(events)])
    finally:
        gc.callbacks.remove(on_gc)
        gc.enable()

    median = float(np.median(samples))
    by_gen = {}
    for gen in (0, 1, 2):
        durations = [d for g, d in pauses if g == gen]
        by_gen[f'gen{gen}'] = {
            'count': len(durations),
            'max_ms': round(max(durations), 3) if durations else 0.0,
        }

    return {
        'gc_enabled': gc_enabled,
        'events': events,
        'median_ms': round(median, 3),
        'mean_ms': round(float(samples.mean()), 3),
        'std_ms': round(float(samples.std()), 3),
        'p95_ms': round(float(np.percentile(samples, 95)), 3),
        'p99_ms': round(float(np.percentile(samples, 99)), 3),
        'max_ms': round(float(samples.max()), 3),
        'extreme_events_over_50ms': int((samples > EXTREME_MS).sum()),
        'pct_over_1_5x_median': round(float((samples > 1.5 * median).mean() * 100), 2),
        'gc_pauses': by_gen,
    }


def main():
    parser = argparse.ArgumentParser(description="GC outlier diagnostic for the SHACL stage")
    parser.add_argument('--events', type=int, default=3000)
    parser.add_argument('--output-dir', type=str, default='baseline/results')
    args = parser.parse_args()

    validator = SHACLValidator()
    for i in range(50):                    # warm-up
        _validate_once(validator, i)

    results = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'python': sys.version.split()[0],
        'gc_on': run_series(validator, args.events, gc_enabled=True),
        'gc_off': run_series(validator, args.events, gc_enabled=False),
    }

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / 'gc_diagnostic.json'
    out_path.write_text(json.dumps(results, indent=2))

    print(f"\n{'':22s}{'GC on':>10s}{'GC off':>10s}")
    for key in ('median_ms', 'p95_ms', 'p99_ms', 'max_ms', 'std_ms',
                'extreme_events_over_50ms', 'pct_over_1_5x_median'):
        print(f"{key:22s}{results['gc_on'][key]:>10}{results['gc_off'][key]:>10}")
    g2 = results['gc_on']['gc_pauses']['gen2']
    print(f"\nGC on: gen2 pauses = {g2['count']}, longest = {g2['max_ms']} ms")
    print(f"Saved -> {out_path}")


if __name__ == '__main__':
    main()
