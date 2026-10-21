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
that interval), and the CPU and memory of the node in that time window.

Inputs, one --node per node under test:
  --node NAME ONBOARDING_CSV RESOURCES_CSV GENERATOR_CSV
      ONBOARDING_CSV  bridge --onboarding-csv on that node
      RESOURCES_CSV   experiments/resource_monitor.py on that node
      GENERATOR_CSV   experiments/load_generator.py aimed at that node

Validation of the emulation (optional):
  --validate PHYSICAL_ONBOARDING_CSV PHYSICAL_SESSION EMULATED_SESSION_PREFIX
      compares bridge_to_registered and the server stages of the physical
      session with the emulated sessions whose name starts with the prefix
      (both from the onboarding CSV of the first --node). Criterion: no
      significant difference (Mann-Whitney, p > 0.05) and a difference of
      medians below --bound-ms.

Outputs (in --out): load_events.csv, load_summary.csv, validation.csv,
fig_load_burst.{pdf,png}, fig_load_ramp.{pdf,png}.

Usage:
  python experiments/analyze_load.py \\
      --node pi5 res/onb_pi5.csv res/res_pi5.csv res/gen_pi5.csv \\
      --node opi res/onb_opi.csv res/res_opi.csv res/gen_opi.csv \\
      --validate res/onb_pi5.csv concurrente_shacl load_pi5val_burst_N4 \\
      --out experiments/results/load

Project: PINV01-24 - FIUNA
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

STAGES = ['latency', 'latency_rel', 'bridge_to_registered', 'bridge_queue', 'http', 'srv_total',
          'srv_classify', 'srv_shacl']


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
    return out


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


def summarize_node(node, onb, res, gen):
    ev = event_stages(onb)
    ev = ev[ev['session'].isin(set(gen['session']))].copy()
    ev = add_latency(ev, gen)
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


def validate(onb_path, physical_session, emulated_prefix, bound_ms):
    from scipy.stats import mannwhitneyu
    ev = event_stages(pd.read_csv(onb_path))
    ev = ev[ev['http_status'] == 200]
    phys = ev[ev['session'] == physical_session]
    emu = ev[ev['session'].str.startswith(emulated_prefix)]
    if phys.empty or emu.empty:
        raise SystemExit(f"validation: {len(phys)} physical and {len(emu)} emulated registrations found")
    rows = []
    for st in ['bridge_to_registered', 'bridge_queue', 'http', 'srv_total', 'srv_classify', 'srv_shacl']:
        a, b = phys[st].dropna(), emu[st].dropna()
        if not len(a) or not len(b):
            continue
        p = mannwhitneyu(a, b).pvalue
        diff = b.median() - a.median()
        rows.append(dict(stage=st, n_physical=len(a), n_emulated=len(b),
                         median_physical=a.median(), median_emulated=b.median(),
                         diff_median=diff, p_mannwhitney=p,
                         passes=bool(p > 0.05 and abs(diff) < bound_ms)))
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
    ap.add_argument('--validate', nargs=3, metavar=('PHYS_ONBOARDING', 'PHYS_SESSION', 'EMU_PREFIX'))
    ap.add_argument('--bound-ms', type=float, default=5.0)
    ap.add_argument('--out', default='experiments/results/load')
    ap.add_argument('--no-plots', action='store_true')
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    events, summaries = [], []
    for name, onb, res, gen in args.node:
        ev, summ = summarize_node(name, pd.read_csv(onb), pd.read_csv(res), pd.read_csv(gen))
        events.append(ev)
        summaries.append(summ)
    events = pd.concat(events, ignore_index=True)
    summary = pd.concat(summaries, ignore_index=True)
    events.to_csv(out / 'load_events.csv', index=False)
    summary.to_csv(out / 'load_summary.csv', index=False)

    cols = ['node', 'session', 'sent', 'registered', 'rejected', 'missing',
            'latency_median', 'latency_p95', 'makespan_ms', 'throughput_per_s',
            'server_cpu_mean', 'server_rss_mb_max']
    with pd.option_context('display.width', 200, 'display.max_columns', 20,
                           'display.float_format', '{:.1f}'.format):
        print(summary[[c for c in cols if c in summary]].to_string(index=False))

    if args.validate:
        val = validate(*args.validate, args.bound_ms)
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