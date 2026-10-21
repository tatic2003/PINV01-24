#!/usr/bin/env python3
"""
Analysis of the load experiments (experiments/load_generator.py).

Latency
-------
latency_i = done_i - publish_i: from the generator's publication to the end
of the registration (bridge callback + HTTP round trip). The generator and
the node under test are separate machines, so the two clocks are compared
through chrony; the bound of the difference (|offset| + root delay / 2 +
root dispersion, from `chronyc tracking` on the client machine) must be
reported next to the result. It includes every wait the announcement
suffers on the node, also in the broker and in the socket before the
bridge reads it (bridge_to_registered = queue in the bridge's client + HTTP
round trip is reported as well, for reference).

latency_rel is the former estimator, which needs no common clock:
latency_rel_i = done_i - arrival_first - (publish_i - publish_first). It
depends on the transit of the first announcement of each session, so it is
biased when that transit is large (ramp sessions); it is kept only as a
cross-check in bursts.

Per session (one load level) the script reports what was sent, acknowledged
and registered, the latency (median, P95, max) and its parts (HTTP, server
time, of which classification and SHACL), the makespan of a burst (first
arrival to last registration), the throughput (registrations per second over
that interval), the CPU and memory of the node in that time window, and the
median frequency of CPU 0 in that window (cpu0_mhz_median).

CPU frequency
-------------
With the ondemand governor the frequency follows the load, and the server
stages follow the frequency. Each registration is given the cpu0_mhz of the
resource sample nearest to its arrival at the bridge (within
--freq-tolerance-ms; NaN otherwise), and stages_by_freq.csv gives the median
of the stages per node and frequency. Only CPU 0 is sampled.

Inputs, one --node per node under test:
  --node NAME ONBOARDING_CSV RESOURCES_CSV GENERATOR_CSV
      ONBOARDING_CSV  bridge --onboarding-csv on that node
      RESOURCES_CSV   experiments/resource_monitor.py on that node
      GENERATOR_CSV   experiments/load_generator.py aimed at that node

Validation of the emulation (optional):
  --validate PHYSICAL_CSV PHYSICAL_SESSION EMULATED_CSV EMULATED_SESSION_PREFIX
      compares bridge_to_registered and the server stages of the physical
      session (in PHYSICAL_CSV, an onboarding CSV of the bridge) with the
      emulated sessions of EMULATED_CSV whose name starts with the prefix.
      The two files are read as they are, so every figure comes from the
      raw CSVs. Criterion: no significant difference (Mann-Whitney,
      p > 0.05) and a difference of medians below --bound-ms (column
      passes). validation.csv also gives a 95% bootstrap confidence
      interval of the difference of medians (emulated minus physical;
      --bootstrap resamples, fixed --seed), and whether it lies within
      +-bound (ci_within_bound); it does not change passes. Only stages
      measured on the node's own clock are compared, so the comparison
      needs no chrony bound.

Outputs (in --out): load_events.csv, load_summary.csv, stages_by_freq.csv,
validation.csv, fig_load_burst.{pdf,png}, fig_load_ramp.{pdf,png}.

Usage:
  python experiments/analyze_load.py \\
      --node pi5 res/onb_pi5.csv res/res_pi5.csv res/gen_pi5.csv \\
      --node opi res/onb_opi.csv res/res_opi.csv res/gen_opi.csv \\
      --out experiments/results/load

  Validation of the emulation (physical and emulated sessions in their own files):
  python experiments/analyze_load.py \\
      --node pi5val res/onb_pi5val.csv res/res_pi5val.csv res/gen_pi5val.csv \\
      --validate figures/data/onboarding/onboarding_concurrente.csv concurrente \\
                 figures/data/load/onboarding_pi5val.csv load_pi5val_burst_N4 \\
      --no-plots --out experiments/results/load_pi5val

Project: PINV01-24 - FIUNA
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

STAGES = ['latency', 'latency_rel', 'bridge_to_registered', 'bridge_queue', 'http', 'srv_total',
          'srv_classify', 'srv_shacl']
#: Stages compared in the validation and grouped by CPU frequency (all on the node's clock).
NODE_STAGES = ['bridge_to_registered', 'bridge_queue', 'http', 'srv_total', 'srv_classify', 'srv_shacl']
FREQ_TOLERANCE_MS = 1000.0   # resource samples are taken every 0.5 s


def num(df, col):
    return pd.to_numeric(df[col], errors='coerce') if col in df else pd.Series(np.nan, index=df.index)


def event_stages(onb):
    """Single-clock stages of every registration in an onboarding CSV."""
    out = pd.DataFrame(index=onb.index)
    out['session'] = onb['session'].astype(str)
    out['device_id'] = onb['device_id']
    out['http_status'] = pd.to_numeric(onb.get('http_status'), errors='coerce')
    arrival, callback, rtt = num(onb, 'arrival_epoch_ms'), num(onb, 'callback_epoch_ms'), num(onb, 'http_rtt_ms')
    out['arrival_ms'] = arrival
    out['done_ms'] = callback + rtt
    out['bridge_queue'] = callback - arrival
    out['http'] = rtt
    out['bridge_to_registered'] = out['bridge_queue'] + rtt
    out['srv_total'] = num(onb, 'srv_total_ms')
    out['srv_classify'] = num(onb, 'srv_classify_ms')
    out['srv_shacl'] = num(onb, 'srv_shacl_ms')
    return out


def window_resources(res, start, end, pad=500.0):
    w = res[(res['epoch_ms'] >= start - pad) & (res['epoch_ms'] <= end + pad)]
    out = {'res_samples': len(w)}
    for col in ['sys_cpu_pct', 'server_cpu_pct', 'bridge_cpu_pct', 'broker_cpu_pct']:
        v = pd.to_numeric(w[col], errors='coerce') if col in w else pd.Series(dtype=float)
        out[col.replace('_pct', '_mean')] = v.mean() if len(v.dropna()) else np.nan
        out[col.replace('_pct', '_max')] = v.max() if len(v.dropna()) else np.nan
    for col in ['server_rss_mb', 'bridge_rss_mb', 'broker_rss_mb', 'temp_c']:
        v = pd.to_numeric(w[col], errors='coerce') if col in w else pd.Series(dtype=float)
        out[col + '_max'] = v.max() if len(v.dropna()) else np.nan
    v = pd.to_numeric(w['cpu0_mhz'], errors='coerce') if 'cpu0_mhz' in w else pd.Series(dtype=float)
    out['cpu0_mhz_median'] = v.median() if len(v.dropna()) else np.nan
    return out


def add_cpu_freq(ev, res, tolerance_ms=FREQ_TOLERANCE_MS):
    """cpu0_mhz of the resource sample nearest to each arrival (NaN if none within tolerance_ms)."""
    ev = ev.copy()
    ev['cpu0_mhz'] = np.nan
    if 'cpu0_mhz' not in res or 'epoch_ms' not in res:
        return ev
    samples = pd.DataFrame({'epoch_ms': pd.to_numeric(res['epoch_ms'], errors='coerce'),
                            'mhz': pd.to_numeric(res['cpu0_mhz'], errors='coerce')}).dropna()
    has_arrival = ev['arrival_ms'].notna()
    if samples.empty or not has_arrival.any():
        return ev
    samples = samples.sort_values('epoch_ms')
    left = ev.loc[has_arrival, ['arrival_ms']].reset_index().sort_values('arrival_ms')
    near = pd.merge_asof(left, samples, left_on='arrival_ms', right_on='epoch_ms',
                         direction='nearest', tolerance=tolerance_ms)
    ev.loc[near['index'].to_numpy(), 'cpu0_mhz'] = near['mhz'].to_numpy()
    return ev


def stages_by_freq(events):
    """Median of the node's stages per node and CPU 0 frequency (accepted registrations only)."""
    ok = events[(events['http_status'] == 200) & events['cpu0_mhz'].notna()]
    rows = []
    for (node, mhz), g in ok.groupby(['node', 'cpu0_mhz']):
        row = dict(node=node, cpu0_mhz=mhz, n=len(g))
        for st in NODE_STAGES:
            v = g[st].dropna()
            row[f'{st}_median'] = v.median() if len(v) else np.nan
        rows.append(row)
    return pd.DataFrame(rows, columns=['node', 'cpu0_mhz', 'n'] + [f'{st}_median' for st in NODE_STAGES])


def add_latency(ev, gen):
    """latency = done - publish (chrony-synchronised clocks); latency_rel = clock-free estimator."""
    pub = gen[['device_id', 'publish_ms']].copy()
    pub['publish_ms'] = pd.to_numeric(pub['publish_ms'], errors='coerce')
    ev = ev.merge(pub, on='device_id', how='left')
    first_arrival = ev.groupby('session')['arrival_ms'].transform('min')
    first_publish = ev.groupby('session')['publish_ms'].transform('min')
    ev['latency_rel'] = ev['done_ms'] - first_arrival - (ev['publish_ms'] - first_publish)
    ev['latency'] = ev['done_ms'] - ev['publish_ms']
    return ev


def summarize_node(node, onb, res, gen, freq_tolerance_ms=FREQ_TOLERANCE_MS):
    ev = event_stages(onb)
    ev = ev[ev['session'].isin(set(gen['session']))].copy()
    ev = add_latency(ev, gen)
    ev = add_cpu_freq(ev, res, freq_tolerance_ms)
    ev['node'] = node
    rows = []
    for session, g in gen.groupby('session', sort=False):
        e = ev[ev['session'] == session]
        ok = e[e['http_status'] == 200]
        sent = int(pd.to_numeric(g['publish_ms'], errors='coerce').notna().sum())
        row = dict(node=node, session=session, mode=g['mode'].iloc[0],
                   level=float(g['level'].iloc[0]), repeat=int(g['repeat'].iloc[0]),
                   devices=len(g), sent=sent,
                   acked=int(pd.to_numeric(g['puback_ms'], errors='coerce').notna().sum()),
                   registered=len(ok), rejected=len(e) - len(ok), missing=sent - len(e))
        for st in STAGES:
            v = ok[st].dropna()
            row[f'{st}_median'] = v.median() if len(v) else np.nan
        for st in ('latency', 'bridge_to_registered'):
            v = ok[st].dropna()
            row[f'{st}_p95'] = v.quantile(0.95) if len(v) else np.nan
            row[f'{st}_max'] = v.max() if len(v) else np.nan
        if len(ok):
            start, end = ok['arrival_ms'].min(), ok['done_ms'].max()
            row['makespan_ms'] = end - start
            row['duration_s'] = (end - start) / 1000.0
            row['throughput_per_s'] = len(ok) / row['duration_s'] if len(ok) > 1 and end > start else np.nan
            row.update(window_resources(res, start, end))
        rows.append(row)
    return ev, pd.DataFrame(rows)


def accepted_events(path):
    """Registrations accepted by the server (HTTP 200) in an onboarding CSV, with their stages."""
    ev = event_stages(pd.read_csv(path))
    return ev[ev['http_status'] == 200]


def bootstrap_diff_median_ci(a, b, n_boot, seed, level=0.95):
    """Percentile bootstrap CI of median(b) - median(a), resampling each sample independently."""
    rng = np.random.default_rng(seed)
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    ia = rng.integers(0, len(a), size=(n_boot, len(a)))
    ib = rng.integers(0, len(b), size=(n_boot, len(b)))
    diffs = np.median(b[ib], axis=1) - np.median(a[ia], axis=1)
    tail = (1.0 - level) / 2.0 * 100.0
    return float(np.percentile(diffs, tail)), float(np.percentile(diffs, 100.0 - tail))


def validate(phys_path, physical_session, emu_path, emulated_prefix, bound_ms, n_boot=10000, seed=0):
    """Physical vs emulated stages: the physical session from phys_path, the emulated sessions
    (name starting with emulated_prefix) from emu_path. Both are bridge onboarding CSVs."""
    from scipy.stats import mannwhitneyu
    phys = accepted_events(phys_path)
    phys = phys[phys['session'] == physical_session]
    emu = accepted_events(emu_path)
    emu = emu[emu['session'].str.startswith(emulated_prefix)]
    if phys.empty or emu.empty:
        raise SystemExit(f"validation: {len(phys)} physical and {len(emu)} emulated registrations found")
    rows = []
    for st in NODE_STAGES:
        a, b = phys[st].dropna(), emu[st].dropna()
        if not len(a) or not len(b):
            continue
        p = mannwhitneyu(a, b).pvalue
        diff = b.median() - a.median()
        lo, hi = bootstrap_diff_median_ci(a, b, n_boot, seed)
        rows.append(dict(stage=st, n_physical=len(a), n_emulated=len(b),
                         median_physical=a.median(), median_emulated=b.median(),
                         diff_median=diff, ci95_low=lo, ci95_high=hi, p_mannwhitney=p,
                         passes=bool(p > 0.05 and abs(diff) < bound_ms),
                         ci_within_bound=bool(-bound_ms < lo and hi < bound_ms)))
    return pd.DataFrame(rows)


NODE_LABELS = {'pi5': 'Raspberry Pi 5', 'opi': 'Orange Pi Zero'}


def plot(summary, out):
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams['pdf.fonttype'] = 42   # embedded TrueType fonts (journal artwork rules)
    import matplotlib.pyplot as plt
    colors = {}
    palette = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd']

    def color(node):
        return colors.setdefault(node, palette[len(colors) % len(palette)])

    def label(node):
        return NODE_LABELS.get(node, node)

    burst = summary[summary['mode'] == 'burst']
    if not burst.empty:
        fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(12, 3.6))
        for node, g in burst.groupby('node'):
            g = g.groupby('level').agg({'latency_median': 'median',
                                        'latency_p95': 'median',
                                        'throughput_per_s': 'median',
                                        'server_cpu_mean': 'median'}).reset_index()
            c = color(node)
            # median: solid line; P95: dashed line (same convention in the ramp figure)
            a1.plot(g['level'], g['latency_median'], 'o-', color=c, label=f'{label(node)}, median')
            a1.plot(g['level'], g['latency_p95'], 's--', color=c, label=f'{label(node)}, P95')
            a2.plot(g['level'], g['throughput_per_s'], 'o-', color=c, label=label(node))
            a3.plot(g['level'], g['server_cpu_mean'], 'o-', color=c, label=label(node))
        for ax, yl, t in [(a1, 'Publication to registration (ms)', '(a)'),
                          (a2, 'Registrations per second', '(b)'),
                          (a3, 'Server CPU, mean (% of one core)', '(c)')]:
            ax.set_xscale('log')
            ax.set_xlabel('Devices announced at once (N)')
            ax.set_ylabel(yl)
            ax.set_title(t, loc='left')
            ax.grid(alpha=0.3)
        a1.set_yscale('log')
        a2.set_ylim(0, burst['throughput_per_s'].max() * 1.2)   # axis from zero
        a3.set_ylim(0, None)
        a1.legend(fontsize=8)
        a2.legend(fontsize=8, loc='center right')
        fig.tight_layout()
        for ext in ('pdf', 'png'):
            fig.savefig(out / f'fig_load_burst.{ext}', dpi=300, bbox_inches='tight')
        plt.close(fig)

    ramp = summary[summary['mode'] == 'ramp']
    if not ramp.empty:
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.6))
        top = ramp['level'].max()
        a1.plot([0, top], [0, top], ':', color='gray', label='offered = achieved')
        for node, g in ramp.groupby('node'):
            g = g.sort_values('level')
            c = color(node)
            a1.plot(g['level'], g['throughput_per_s'], 'o-', color=c, label=label(node))
            a2.plot(g['level'], g['latency_median'], 'o-', color=c, label=f'{label(node)}, median')
            a2.plot(g['level'], g['latency_p95'], 's--', color=c, label=f'{label(node)}, P95')
        a1.set_xlabel('Offered rate (announcements/s)')
        a1.set_ylabel('Registrations per second')
        a1.set_title('(a)', loc='left')
        a2.set_xlabel('Offered rate (announcements/s)')
        a2.set_ylabel('Publication to registration (ms)')
        a2.set_yscale('log')
        a2.set_title('(b)', loc='left')
        for ax in (a1, a2):
            ax.grid(alpha=0.3)
            ax.legend(fontsize=8)
        fig.tight_layout()
        for ext in ('pdf', 'png'):
            fig.savefig(out / f'fig_load_ramp.{ext}', dpi=300, bbox_inches='tight')
        plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--node', nargs=4, action='append', required=True,
                    metavar=('NAME', 'ONBOARDING', 'RESOURCES', 'GENERATOR'))
    ap.add_argument('--validate', nargs=4,
                    metavar=('PHYS_ONBOARDING', 'PHYS_SESSION', 'EMU_ONBOARDING', 'EMU_PREFIX'))
    ap.add_argument('--bound-ms', type=float, default=5.0)
    ap.add_argument('--bootstrap', type=int, default=10000, help='resamples for the CI of the difference of medians')
    ap.add_argument('--seed', type=int, default=0, help='seed of the bootstrap')
    ap.add_argument('--freq-tolerance-ms', type=float, default=FREQ_TOLERANCE_MS,
                    help='max distance from an arrival to the resource sample that gives its CPU frequency')
    ap.add_argument('--out', default='experiments/results/load')
    ap.add_argument('--no-plots', action='store_true')
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    events, summaries = [], []
    for name, onb, res, gen in args.node:
        ev, summ = summarize_node(name, pd.read_csv(onb), pd.read_csv(res), pd.read_csv(gen),
                                  args.freq_tolerance_ms)
        events.append(ev)
        summaries.append(summ)
    events = pd.concat(events, ignore_index=True)
    summary = pd.concat(summaries, ignore_index=True)
    events.to_csv(out / 'load_events.csv', index=False)
    summary.to_csv(out / 'load_summary.csv', index=False)
    by_freq = stages_by_freq(events)
    by_freq.to_csv(out / 'stages_by_freq.csv', index=False)

    cols = ['node', 'session', 'sent', 'registered', 'rejected', 'missing',
            'latency_median', 'latency_p95', 'makespan_ms', 'throughput_per_s',
            'server_cpu_mean', 'server_rss_mb_max', 'cpu0_mhz_median']
    with pd.option_context('display.width', 200, 'display.max_columns', 20,
                           'display.float_format', '{:.1f}'.format):
        print(summary[[c for c in cols if c in summary]].to_string(index=False))
        if not by_freq.empty:
            print('\nNode stages by CPU 0 frequency (medians, ms):')
            print(by_freq.to_string(index=False))

    if args.validate:
        val = validate(*args.validate, args.bound_ms, n_boot=args.bootstrap, seed=args.seed)
        val.to_csv(out / 'validation.csv', index=False)
        with pd.option_context('display.width', 200, 'display.float_format', '{:.3f}'.format):
            print('\nValidation (physical vs emulated):')
            print(val.to_string(index=False))

    if not args.no_plots:
        plot(summary, out)
    print(f'\nResults in {out.resolve()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())