"""
The baseline benchmarks train the classifier as the server does by default,
and every summary JSON records what the classifier was trained on.

Project: PINV01-24 - FIUNA
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from baseline import central_baseline as cb                         # noqa: E402
from baseline.run_distributed_benchmark import run_distributed_benchmark  # noqa: E402
from server.config import Config                                    # noqa: E402


def test_defaults_are_the_server_defaults():
    c = Config()
    assert cb.DEFAULT_TRAINING_SAMPLES == c.training_samples
    assert cb.DEFAULT_TRAINING_SOURCE == c.training_source
    assert cb.DEFAULT_FEATURE_SET == c.classifier_feature_set


def test_training_metadata_of_the_catalog():
    meta = cb.training_metadata('catalog', 2000, 42)
    assert meta['training_samples'] == 2000 and meta['seed'] == 42
    assert meta['labeling'] == 'hw' and len(meta['catalog_version']) == 12
    assert 'catalog_version' not in cb.training_metadata('profiles', 1000, 1)


def test_summaries_record_the_training(tmp_path):
    base = cb.CentralizedBaseline(model_type='decision_tree', training_samples=200)
    res = base.run_benchmark(num_events=3, num_repetitions=1, warmup_events=1, seed=1)
    base.save_summary_json(res, str(tmp_path / 'central.json'))
    central = json.loads((tmp_path / 'central.json').read_text())
    assert central['training_samples'] == 200 and central['training_source'] == 'catalog'
    assert central['labeling'] == 'hw'

    run_distributed_benchmark(model_type='decision_tree', num_nodes=1, num_events=3, num_repetitions=1,
                              warmup_events=1, training_samples=200, output_dir=str(tmp_path))
    distributed = json.loads((tmp_path / 'distributed_decision_tree_summary.json').read_text())
    assert distributed['training_samples'] == 200 and distributed['training_source'] == 'catalog'
    assert distributed['seed'] == 42
