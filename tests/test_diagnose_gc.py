"""
baseline/diagnose_gc.py: same events and same work as the baseline's SHACL
stage, collector pauses attributed to the event during which they happened.

Project: PINV01-24 - FIUNA
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from baseline import diagnose_gc as dg  # noqa: E402


def test_pause_attribution():
    rec = dg.GCRecorder()
    rec.pauses = [(1.0, 1.2, 0), (2.5, 2.6, 2), (5.0, 5.1, 1)]
    assert [p[2] for p in rec.during(2.0, 3.0)] == [2]
    assert rec.during(3.0, 4.0) == []


def test_summary_counts_slow_events_with_gen2():
    rows = [dict(t_shacl_ms=8.0, conforms=True, gc_pauses=0, gc_max_generation=''),
            dict(t_shacl_ms=150.0, conforms=True, gc_pauses=1, gc_max_generation=2),
            dict(t_shacl_ms=120.0, conforms=True, gc_pauses=0, gc_max_generation=''),
            dict(t_shacl_ms=60.0, conforms=True, gc_pauses=1, gc_max_generation=1)]
    s = dg.summarize(rows, [(0.0, 0.14, 2), (1.0, 1.01, 1)], True)
    assert s['events_over_100ms'] == 2 and s['events_over_50ms'] == 3
    assert s['slow_events_with_gen2_pause'] == 1 and s['slow_events_with_any_pause'] == 1
    assert s['gc_pauses']['gen2']['count'] == 1


def test_small_run_writes_both_files(tmp_path):
    assert dg.main(['--events', '5', '--repetitions', '2', '--warmup', '1', '--output-dir', str(tmp_path)]) == 0
    rows = list(csv.DictReader(open(tmp_path / 'gc_diagnostic_events.csv', encoding='utf-8')))
    assert len(rows) == 2 * 2 * 5
    assert {r['series'] for r in rows} == {'gc_on', 'gc_off'}
    res = json.loads((tmp_path / 'gc_diagnostic.json').read_text())
    assert res['gc_on']['events'] == 10 and res['gc_off']['gc_enabled'] is False
    assert res['events_source']['training_source'] == 'catalog'
    # The benchmark's announcements are valid devices
    assert res['gc_on']['conforming'] == 10
    # With the collector off no collection runs during the series
    assert res['gc_off']['gc_pauses']['gen2']['count'] == 0
