"""
Load experiment: announcement generation, latency without a common clock,
per-session summary and the physical-vs-emulated validation.

Project: PINV01-24 - FIUNA
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'experiments' / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gen_mod = load('load_generator')
ana = load('analyze_load')


class TestAnnouncements:
    def test_catalog_payload_is_only_what_a_device_announces(self):
        p = gen_mod.AnnouncementSource('catalog', seed=1).next()
        for leaked in ('device_type', 'board_id', 'catalog_version', 'role', 'memory_source'):
            assert leaked not in p
        assert p['memory_total'] > 0 and p['hardware'] and p['ip']

    def test_testbed_payloads_are_the_four_concurrent_boards(self):
        src = gen_mod.AnnouncementSource('testbed')
        hw = [src.next()['hardware'] for _ in range(4)]
        assert hw.count('ESP32-C3-Zero') == 3 and len(set(hw)) == 2

    def test_announcement_carries_id_and_session(self):
        msg = gen_mod.make_announcement({'hardware': 'x', 'device_id': 'old'}, 'emu-s-1', 'load_x_burst_N1',
                                        1000.0, 1010.0)
        assert msg['id'] == 'emu-s-1' and 'device_id' not in msg
        assert msg['timing']['session'] == 'load_x_burst_N1'
        assert msg['timing']['seq'] == 1
        assert msg['timing']['epoch_publish_ms'] >= 1010.0


def onboarding(rows):
    cols = ['session', 'device_id', 'arrival_epoch_ms', 'callback_epoch_ms', 'http_status',
            'http_rtt_ms', 'srv_total_ms', 'srv_classify_ms', 'srv_shacl_ms']
    return pd.DataFrame(rows, columns=cols)


def generator(rows):
    cols = ['session', 'mode', 'level', 'repeat', 'device_id', 'client', 'connect_start_ms',
            'connack_ms', 'publish_ms', 'puback_ms', 'error']
    return pd.DataFrame(rows, columns=cols)


# Three announcements published 1 ms apart on the generator's clock (offset
# +50 s from the node's), served one after the other by the node.
GEN = generator([
    ['s', 'burst', 3, 1, 'd0', 'c0', 0, 0, 50_000.0, 50_001.0, ''],
    ['s', 'burst', 3, 1, 'd1', 'c1', 0, 0, 50_001.0, 50_002.0, ''],
    ['s', 'burst', 3, 1, 'd2', 'c2', 0, 0, 50_002.0, 50_003.0, ''],
])
ONB = onboarding([
    ['s', 'd0', 1000.0, 1000.0, 200, 10.0, 8.0, 3.0, 4.0],
    ['s', 'd1', 1011.0, 1011.0, 200, 10.0, 8.0, 3.0, 4.0],   # read after d0 was served
    ['s', 'd2', 1022.0, 1022.0, 200, 10.0, 8.0, 3.0, 4.0],
])
RES = pd.DataFrame({'epoch_ms': [990.0, 1020.0], 'sys_cpu_pct': [10.0, 30.0],
                    'server_cpu_pct': [20.0, 60.0], 'server_rss_mb': [200.0, 201.0]})


class TestLatency:
    def test_clock_offset_cancels_and_waiting_is_counted(self):
        ev = ana.add_latency(ana.event_stages(ONB), GEN)
        lat = dict(zip(ev['device_id'], ev['latency']))
        # d0: done at 1010; d1: done 1021, published 1 ms after d0 -> 20; d2: 1032 - 1000 - 2 = 30
        assert lat == pytest.approx({'d0': 10.0, 'd1': 20.0, 'd2': 30.0})
        # The bridge's own stamp misses the wait before it read d1 and d2
        assert ev['bridge_to_registered'].tolist() == pytest.approx([10.0, 10.0, 10.0])

    def test_summary(self):
        ev, summ = ana.summarize_node('n', ONB, RES, GEN)
        row = summ.iloc[0]
        assert (row['sent'], row['registered'], row['rejected'], row['missing']) == (3, 3, 0, 0)
        assert row['latency_median'] == pytest.approx(20.0)
        assert row['makespan_ms'] == pytest.approx(32.0)
        assert row['throughput_per_s'] == pytest.approx(3 / 0.032)
        assert row['server_cpu_max'] == 60.0

    def test_missing_and_rejected_are_counted(self):
        onb = ONB.copy()
        onb.loc[1, 'http_status'] = 400
        onb = onb.drop(index=2)
        _, summ = ana.summarize_node('n', onb, RES, GEN)
        row = summ.iloc[0]
        assert (row['registered'], row['rejected'], row['missing']) == (1, 1, 1)


def test_validation(tmp_path):
    rng = np.random.default_rng(0)
    rows = []
    for i in range(10):
        rows.append(['phys', f'p{i}', 0.0, 0.0, 200, 15 + rng.normal(0, 1), 13, 5, 7])
    for i in range(30):
        rows.append([f'load_x_val_r{i % 3}', f'e{i}', 0.0, 0.0, 200, 15 + rng.normal(0, 1), 13, 5, 7])
    path = tmp_path / 'onb.csv'
    onboarding(rows).to_csv(path, index=False)
    val = ana.validate(path, 'phys', 'load_x_val', 5.0)
    row = val.set_index('stage').loc['bridge_to_registered']
    assert row['n_physical'] == 10 and row['n_emulated'] == 30
    assert bool(row['passes'])
