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


# The same three announcements with the generator's clock synchronised to the node's
# (chrony): published at 985, 986 and 987 on the common clock.
GEN_SYNCED = generator([
    ['s', 'burst', 3, 1, 'd0', 'c0', 0, 0, 985.0, 986.0, ''],
    ['s', 'burst', 3, 1, 'd1', 'c1', 0, 0, 986.0, 987.0, ''],
    ['s', 'burst', 3, 1, 'd2', 'c2', 0, 0, 987.0, 988.0, ''],
])


class TestLatency:
    def test_direct_latency_is_registration_minus_publication(self):
        ev = ana.add_latency(ana.event_stages(ONB), GEN_SYNCED)
        lat = dict(zip(ev['device_id'], ev['latency']))
        # done_i = 1010, 1021, 1032 on the node's clock
        assert lat == pytest.approx({'d0': 25.0, 'd1': 35.0, 'd2': 45.0})

    def test_clock_free_estimator_cancels_the_offset_and_counts_the_waiting(self):
        ev = ana.add_latency(ana.event_stages(ONB), GEN)
        lat = dict(zip(ev['device_id'], ev['latency_rel']))
        # d0: done at 1010; d1: done 1021, published 1 ms after d0 -> 20; d2: 1032 - 1000 - 2 = 30
        assert lat == pytest.approx({'d0': 10.0, 'd1': 20.0, 'd2': 30.0})
        # The bridge's own stamp misses the wait before it read d1 and d2
        assert ev['bridge_to_registered'].tolist() == pytest.approx([10.0, 10.0, 10.0])

    def test_summary(self):
        ev, summ = ana.summarize_node('n', ONB, RES, GEN_SYNCED)
        row = summ.iloc[0]
        assert (row['sent'], row['registered'], row['rejected'], row['missing']) == (3, 3, 0, 0)
        assert row['latency_median'] == pytest.approx(35.0)
        assert row['latency_rel_median'] == pytest.approx(20.0)
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
    val = ana.validate(path, 'phys', path, 'load_x_val', 5.0)
    row = val.set_index('stage').loc['bridge_to_registered']
    assert row['n_physical'] == 10 and row['n_emulated'] == 30
    assert bool(row['passes'])


def noisy_rows(session, n, rtt, seed):
    """n accepted registrations of a session with an HTTP round trip around rtt (ms). The server
    stages vary too: with fully tied samples the Mann-Whitney p-value is undefined (NaN)."""
    rng = np.random.default_rng(seed)
    return [[session, f'{session}-{i}', 0.0, 0.0, 200, rtt + rng.normal(0, 1),
             13 + rng.normal(0, 0.5), 5 + rng.normal(0, 0.3), 7 + rng.normal(0, 0.4)] for i in range(n)]


class TestValidationInTwoFiles:
    def test_each_side_is_read_from_its_own_file(self, tmp_path):
        # The physical file also holds a session that matches the prefix, with a very different
        # time: it must be ignored, because the emulated sessions come from the other file.
        phys = noisy_rows('phys', 10, 15, 1) + noisy_rows('load_x_val_r9', 10, 100, 2)
        emu = sum((noisy_rows(f'load_x_val_r{k}', 10, 15, 10 + k) for k in range(3)), [])
        emu += noisy_rows('phys', 10, 100, 3)   # same name as the physical session: also ignored
        onboarding(phys).to_csv(tmp_path / 'phys.csv', index=False)
        onboarding(emu).to_csv(tmp_path / 'emu.csv', index=False)
        val = ana.validate(tmp_path / 'phys.csv', 'phys', tmp_path / 'emu.csv', 'load_x_val', 5.0)
        row = val.set_index('stage').loc['bridge_to_registered']
        assert row['n_physical'] == 10 and row['n_emulated'] == 30
        assert bool(row['passes'])

    def test_the_two_paths_can_be_the_same_file(self, tmp_path):
        rows = noisy_rows('phys', 10, 15, 1) + noisy_rows('load_x_val_r1', 10, 15, 2)
        onboarding(rows).to_csv(tmp_path / 'one.csv', index=False)
        val = ana.validate(tmp_path / 'one.csv', 'phys', tmp_path / 'one.csv', 'load_x_val', 5.0)
        row = val.set_index('stage').loc['bridge_to_registered']
        assert (row['n_physical'], row['n_emulated']) == (10, 10)

    def test_a_difference_above_the_bound_fails_the_criterion(self, tmp_path):
        onboarding(noisy_rows('phys', 40, 22, 1)).to_csv(tmp_path / 'phys.csv', index=False)
        onboarding(noisy_rows('load_x_val_r1', 40, 15, 2)).to_csv(tmp_path / 'emu.csv', index=False)
        val = ana.validate(tmp_path / 'phys.csv', 'phys', tmp_path / 'emu.csv', 'load_x_val',
                           5.0).set_index('stage')
        row = val.loc['bridge_to_registered']
        assert row['diff_median'] == pytest.approx(-7.0, abs=1.0)
        assert not bool(row['passes'])
        # The server stages are drawn from the same distribution in both files and pass
        assert bool(val.loc['srv_total', 'passes'])

    def test_a_small_but_systematic_difference_fails_on_the_p_value(self, tmp_path):
        # 2 ms is under the 5 ms bound, but with 40 + 40 registrations it is significant.
        onboarding(noisy_rows('phys', 40, 17, 1)).to_csv(tmp_path / 'phys.csv', index=False)
        onboarding(noisy_rows('load_x_val_r1', 40, 15, 2)).to_csv(tmp_path / 'emu.csv', index=False)
        row = ana.validate(tmp_path / 'phys.csv', 'phys', tmp_path / 'emu.csv', 'load_x_val',
                           5.0).set_index('stage').loc['bridge_to_registered']
        assert abs(row['diff_median']) < 5.0 and row['p_mannwhitney'] < 0.05
        assert not bool(row['passes'])

    def test_missing_sessions_stop_the_analysis(self, tmp_path):
        onboarding(noisy_rows('phys', 10, 15, 1)).to_csv(tmp_path / 'phys.csv', index=False)
        onboarding(noisy_rows('other', 10, 15, 2)).to_csv(tmp_path / 'emu.csv', index=False)
        with pytest.raises(SystemExit):
            ana.validate(tmp_path / 'phys.csv', 'phys', tmp_path / 'emu.csv', 'load_x_val', 5.0)


class TestCommandLine:
    def run_cli(self, tmp_path, validate):
        # Node under test: the three announcements of GEN_SYNCED. The validation files are separate.
        ONB.to_csv(tmp_path / 'onb.csv', index=False)
        RES.to_csv(tmp_path / 'res.csv', index=False)
        GEN_SYNCED.to_csv(tmp_path / 'gen.csv', index=False)
        onboarding(noisy_rows('phys', 10, 15, 4)).to_csv(tmp_path / 'phys.csv', index=False)
        onboarding(noisy_rows('load_x_val_r1', 10, 15, 3)).to_csv(tmp_path / 'emu.csv', index=False)
        argv = ['--node', 'n', str(tmp_path / 'onb.csv'), str(tmp_path / 'res.csv'), str(tmp_path / 'gen.csv'),
                '--no-plots', '--out', str(tmp_path / 'out')] + validate
        return ana.main(argv)

    def test_validation_reads_the_two_raw_files(self, tmp_path):
        v = ['--validate', str(tmp_path / 'phys.csv'), 'phys', str(tmp_path / 'emu.csv'), 'load_x_val']
        assert self.run_cli(tmp_path, v) == 0
        val = pd.read_csv(tmp_path / 'out' / 'validation.csv').set_index('stage')
        assert val.loc['bridge_to_registered', 'n_physical'] == 10
        assert val.loc['bridge_to_registered', 'n_emulated'] == 10
        assert {'bridge_to_registered', 'srv_total', 'srv_classify', 'srv_shacl'} <= set(val.index)
        assert (tmp_path / 'out' / 'load_summary.csv').exists()

    def test_validate_needs_four_arguments(self, tmp_path):
        with pytest.raises(SystemExit):
            self.run_cli(tmp_path, ['--validate', str(tmp_path / 'phys.csv'), 'phys', 'load_x_val'])

    def test_without_validate_nothing_is_validated(self, tmp_path):
        assert self.run_cli(tmp_path, []) == 0
        assert not (tmp_path / 'out' / 'validation.csv').exists()


class TestCpuFrequency:
    # Resource samples every 500 ms: 1500 MHz, then 2400 MHz.
    RES_FREQ = pd.DataFrame({'epoch_ms': [500.0, 1000.0, 1500.0, 2000.0],
                             'cpu0_mhz': [1500, 1500, 2400, 2400],
                             'server_cpu_pct': [5.0, 5.0, 90.0, 90.0]})

    def events(self, arrivals, srv):
        ev = pd.DataFrame({'node': 'n', 'session': 's', 'device_id': [f'd{i}' for i in range(len(arrivals))],
                           'http_status': 200, 'arrival_ms': arrivals})
        for st in ana.NODE_STAGES:
            ev[st] = srv
        return ev

    def test_each_arrival_takes_the_nearest_sample(self):
        # 1200 is nearer the 1000 ms sample (1500 MHz); 1300 is nearer the 1500 ms one (2400 MHz)
        ev = ana.add_cpu_freq(self.events([1100.0, 1200.0, 1300.0, 1900.0], [20.0, 20.0, 14.0, 14.0]),
                              self.RES_FREQ)
        assert ev['cpu0_mhz'].tolist() == [1500, 1500, 2400, 2400]

    def test_an_arrival_far_from_every_sample_has_no_frequency(self):
        ev = ana.add_cpu_freq(self.events([1100.0, 9000.0], [20.0, 20.0]), self.RES_FREQ)
        assert ev['cpu0_mhz'].iloc[0] == 1500
        assert np.isnan(ev['cpu0_mhz'].iloc[1])

    def test_resources_without_frequency_leave_it_empty(self):
        ev = ana.add_cpu_freq(self.events([1100.0], [20.0]), self.RES_FREQ.drop(columns='cpu0_mhz'))
        assert ev['cpu0_mhz'].isna().all()

    def test_event_order_is_kept(self):
        # merge_asof needs sorted keys; the result must still go back to the original rows
        ev = ana.add_cpu_freq(self.events([1900.0, 1100.0], [14.0, 20.0]), self.RES_FREQ)
        assert dict(zip(ev['device_id'], ev['cpu0_mhz'])) == {'d0': 2400, 'd1': 1500}

    def test_stages_are_grouped_by_frequency(self):
        ev = ana.add_cpu_freq(self.events([1000.0, 1100.0, 1900.0, 2000.0, 9000.0],
                                          [21.0, 19.0, 14.0, 14.0, 99.0]), self.RES_FREQ)
        ev.loc[ev['device_id'] == 'd3', 'http_status'] = 400   # rejected: not counted
        by = ana.stages_by_freq(ev).set_index('cpu0_mhz')
        assert by.loc[1500, 'n'] == 2 and by.loc[1500, 'srv_total_median'] == pytest.approx(20.0)
        assert by.loc[2400, 'n'] == 1 and by.loc[2400, 'srv_total_median'] == pytest.approx(14.0)
        assert len(by) == 2   # the arrival without a sample is left out

    def test_session_summary_has_the_median_frequency(self):
        _, summ = ana.summarize_node('n', ONB, self.RES_FREQ.assign(epoch_ms=[990.0, 1000.0, 1010.0, 1020.0]),
                                     GEN_SYNCED)
        assert summ.iloc[0]['cpu0_mhz_median'] == pytest.approx(1950.0)


class TestBootstrapInterval:
    def test_interval_contains_the_true_difference_and_is_reproducible(self):
        rng = np.random.default_rng(5)
        a, b = rng.normal(20, 1, 40), rng.normal(17, 1, 40)
        lo, hi = ana.bootstrap_diff_median_ci(a, b, 4000, seed=1)
        assert lo < -3.0 < hi and hi - lo < 2.0
        assert (lo, hi) == ana.bootstrap_diff_median_ci(a, b, 4000, seed=1)

    def test_validation_reports_the_interval(self, tmp_path):
        onboarding(noisy_rows('phys', 40, 15, 1)).to_csv(tmp_path / 'phys.csv', index=False)
        onboarding(noisy_rows('load_x_val_r1', 40, 15, 2)).to_csv(tmp_path / 'emu.csv', index=False)
        val = ana.validate(tmp_path / 'phys.csv', 'phys', tmp_path / 'emu.csv', 'load_x_val', 5.0,
                           n_boot=2000).set_index('stage')
        row = val.loc['bridge_to_registered']
        assert row['ci95_low'] <= row['diff_median'] <= row['ci95_high']
        assert bool(row['ci_within_bound'])

    def test_an_interval_that_crosses_the_bound_is_flagged(self, tmp_path):
        # Medians 4 ms apart with a wide spread: the interval reaches past 5 ms.
        rng = np.random.default_rng(7)
        phys = [['phys', f'p{i}', 0.0, 0.0, 200, 15 + rng.normal(0, 6), 13, 5, 7] for i in range(15)]
        emu = [['load_x_val_r1', f'e{i}', 0.0, 0.0, 200, 19 + rng.normal(0, 6), 13, 5, 7] for i in range(15)]
        onboarding(phys).to_csv(tmp_path / 'phys.csv', index=False)
        onboarding(emu).to_csv(tmp_path / 'emu.csv', index=False)
        row = ana.validate(tmp_path / 'phys.csv', 'phys', tmp_path / 'emu.csv', 'load_x_val', 5.0,
                           n_boot=2000).set_index('stage').loc['bridge_to_registered']
        assert row['ci95_high'] > 5.0
        assert not bool(row['ci_within_bound'])

    def test_the_command_line_writes_stages_by_freq(self, tmp_path):
        ONB.to_csv(tmp_path / 'onb.csv', index=False)
        RES.assign(cpu0_mhz=[1500, 2400]).to_csv(tmp_path / 'res.csv', index=False)
        GEN_SYNCED.to_csv(tmp_path / 'gen.csv', index=False)
        assert ana.main(['--node', 'n', str(tmp_path / 'onb.csv'), str(tmp_path / 'res.csv'),
                         str(tmp_path / 'gen.csv'), '--no-plots', '--out', str(tmp_path / 'out')]) == 0
        by = pd.read_csv(tmp_path / 'out' / 'stages_by_freq.csv')
        assert by['n'].sum() == 3 and set(by['cpu0_mhz']) <= {1500, 2400}