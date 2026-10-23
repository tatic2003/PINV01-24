#!/usr/bin/env python3
"""
Analysis of the placement experiment: centralized vs distributed pipeline.

In each run one load generator (experiments/load_generator.py, ramp mode)
offers the same total rates. In the centralized configuration every
announcement goes to one node; in a distributed configuration the
announcements are split between the nodes (--host with --weights) and each
node registers its own devices and replicates its DAG with
experiments/dag_sync.py.

Runs and blocks. The unit of repetition is the complete run of one
configuration. A run is named CONFIG:BLOCK (for example central:2); a name
without ':' is block 1. Each block holds one run of every configuration, and
the configurations are compared within each block.

For every run and load level (one ramp session) the script reports:

  latency       publication (generator clock) to registration (clock of the
                node that registered it): mean, median and P95. Generator and
                nodes are compared through chrony, so the bound of the clock
                difference must be reported next to the result.
  utilization   CPU of the pipeline processes of each node (server, bridge,
                broker and, when it runs, the DAG synchronization agent), in
                percent of one core, averaged over the level window; the level
                is characterized by its busiest node (util_max).
  CPU time      CPU time of those processes on all the nodes of the run during
                the level window, divided by the registrations of the level
                (ms of CPU per registration), as an energy proxy.

and, per node of the run (placement_node_levels.csv), two complementary
single-clock measures:

  MQTT confirmation latency   publication to PUBACK of the node's broker, on
                the generator clock. It covers the send, the broker's
                processing and waits, and the return of the acknowledgement;
                it does not isolate the network transit, and it is not
                subtracted from the end-to-end latency (the acknowledgement
                and the later processing can overlap).
  in-node latency   arrival at the node's bridge to the end of the
                registration, on the node clock. It leaves out everything
                before the bridge receives the message, but the network can
                still affect it through the arrival pattern.

The level window runs from the first arrival at any bridge to the last
registration of the level, with the 0.5 s margin of experiments/analyze_load.py.

Comparison: within each block, each configuration is compared with the
reference configuration (--reference) level by level. A configuration is
preferable at a level when it is lower on all three measures. The crossover
is the lowest level at which the configuration that is preferable at the
lowest level stops being preferable; it is reported per block. No
statistical test is made.

Aggregation over blocks: for each configuration and level, the measures of
each run are computed first, and then their mean, minimum and maximum over
the runs, every run with the same weight (placement_aggregate.csv). The
consistency of the preference is the number of blocks in which each
configuration of a pair is preferable at each level
(placement_consistency.csv).

Inputs:
  --run NAME GENERATOR_CSV                      one per run (NAME = CONFIG[:BLOCK])
  --node RUN NODE HOST ONBOARDING_CSV RESOURCES_CSV
        the nodes of each run: HOST is the broker as written in the 'host'
        column of the generator CSV, ONBOARDING_CSV the bridge
        --onboarding-csv of that node, RESOURCES_CSV
        experiments/resource_monitor.py on that node
  --reference CONFIG  (default: the configuration of the first --run)

Outputs (in --out): placement_events.csv, placement_levels.csv,
placement_node_levels.csv, placement_comparison.csv, placement_summary.csv,
placement_aggregate.csv, placement_consistency.csv, fig_placement.{pdf,png}.

Usage:
  python experiments/analyze_placement.py \\
      --run central:1 res/central/generator_central.csv \\
      --node central:1 pi5 localhost res/central/onboarding_pi5.csv res/central/resources_pi5.csv \\
      --run dist50:1 res/dist50/generator_dist50.csv \\
      --node dist50:1 pi5 localhost res/dist50/onboarding_pi5.csv res/dist50/resources_pi5.csv \\
      --node dist50:1 opi 192.168.0.13 res/dist50/onboarding_opi.csv res/dist50/resources_opi.csv \\
      --reference central --out experiments/results/placement/analysis

Project: PINV01-24 - FIUNA
"""

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def _load_analyze_load():
    spec = importlib.util.spec_from_file_location('analyze_load', HERE / 'analyze_load.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault('analyze_load', mod)
    spec.loader.exec_module(mod)
    return mod


al = _load_analyze_load()

PIPELINE = ['server', 'bridge', 'broker', 'sync']
MEASURES = ['latency_mean', 'util_max', 'cpu_ms_per_reg']
#: Per-run values averaged over the blocks in placement_aggregate.csv.
AGGREGATED = MEASURES + ['latency_median', 'latency_p95', 'throughput_per_s']
PAD_MS = 500.0
LABELS = {'central': 'Centralized', 'dist50': 'Distributed, 50/50',
          'distcap': 'Distributed, by capacity'}


def split_run(name):
    """'central:2' -> ('central', 2); a name without ':' is block 1."""
    config, sep, block = str(name).partition(':')
    if not sep:
        return config, 1
    if not config or not block.isdigit() or int(block) < 1:
        raise ValueError(f'run name {name!r} is not CONFIG:BLOCK with BLOCK a positive integer')
    return config, int(block)


def pipeline_cpu(res):
    """Sum of the pipeline processes' CPU (% of one core) per resource sample."""
    out = pd.DataFrame({'epoch_ms': pd.to_numeric(res['epoch_ms'], errors='coerce')})
    total = pd.Series(0.0, index=res.index)
    for p in PIPELINE:
        col = f'{p}_cpu_pct'
        if col in res:
            total = total + pd.to_numeric(res[col], errors='coerce').fillna(0.0)
    out['pipeline_cpu_pct'] = total
    return out.dropna(subset=['epoch_ms'])


def run_events(run, gen, nodes):
    """Registrations of one run, with their node, latency and MQTT confirmation latency."""
    pub = gen[['device_id', 'publish_ms', 'puback_ms', 'host']].copy()
    pub['publish_ms'] = pd.to_numeric(pub['publish_ms'], errors='coerce')
    pub['puback_ms'] = pd.to_numeric(pub['puback_ms'], errors='coerce')
    sessions = set(gen['session'])
    frames = []
    for n in nodes:
        ev = al.event_stages(n['onboarding'])
        ev = ev[ev['session'].isin(sessions)].copy()
        ev['node'] = n['name']
        frames.append(ev)
    ev = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    ev = ev.merge(pub, on='device_id', how='left')
    ev['latency'] = ev['done_ms'] - ev['publish_ms']
    ev['mqtt_confirm'] = ev['puback_ms'] - ev['publish_ms']
    ev['in_node'] = ev['done_ms'] - ev['arrival_ms']
    config, block = split_run(run)
    ev['run'], ev['config'], ev['block'] = run, config, block
    return ev


def _stats(values, prefix):
    v = pd.to_numeric(values, errors='coerce').dropna()
    return {f'{prefix}_n': len(v),
            f'{prefix}_median': v.median() if len(v) else np.nan,
            f'{prefix}_p95': v.quantile(0.95) if len(v) else np.nan}


def level_rows(run, gen, nodes, ev):
    config, block = split_run(run)
    cpu = {n['name']: pipeline_cpu(n['resources']) for n in nodes}
    rows = []
    for session, g in gen.groupby('session', sort=False):
        e = ev[ev['session'] == session]
        ok = e[e['http_status'] == 200]
        sent = int(pd.to_numeric(g['publish_ms'], errors='coerce').notna().sum())
        row = dict(run=run, config=config, block=block, session=session,
                   level=float(g['level'].iloc[0]), sent=sent,
                   acked=int(pd.to_numeric(g['puback_ms'], errors='coerce').notna().sum()),
                   registered=len(ok), rejected=len(e) - len(ok), missing=sent - len(e))
        for n in nodes:
            on_node = g[g['host'].astype(str) == n['host']]
            row[f'sent_{n["name"]}'] = int(pd.to_numeric(on_node['publish_ms'], errors='coerce').notna().sum())
            row[f'registered_{n["name"]}'] = int((ok['node'] == n['name']).sum())
        lat = ok['latency'].dropna()
        row['latency_mean'] = lat.mean() if len(lat) else np.nan
        row['latency_median'] = lat.median() if len(lat) else np.nan
        row['latency_p95'] = lat.quantile(0.95) if len(lat) else np.nan
        if len(ok):
            start, end = ok['arrival_ms'].min(), ok['done_ms'].max()
            duration_s = (end - start) / 1000.0
            row['duration_s'] = duration_s
            row['throughput_per_s'] = len(ok) / duration_s if len(ok) > 1 and duration_s > 0 else np.nan
            cpu_s, utils = 0.0, {}
            for name, c in cpu.items():
                w = c[(c['epoch_ms'] >= start - PAD_MS) & (c['epoch_ms'] <= end + PAD_MS)]
                util = w['pipeline_cpu_pct'].mean() if len(w) else np.nan
                utils[name] = util
                row[f'util_{name}'] = util
                if not np.isnan(util):
                    cpu_s += util / 100.0 * duration_s
            known = {k: v for k, v in utils.items() if not np.isnan(v)}
            row['util_max'] = max(known.values()) if known else np.nan
            row['busiest_node'] = max(known, key=known.get) if known else ''
            row['cpu_ms_per_reg'] = cpu_s * 1000.0 / len(ok) if known else np.nan
        rows.append(row)
    return rows


def node_level_rows(run, gen, nodes, ev):
    """
    Per run, level and node: MQTT confirmation latency of the node's broker
    (all announcements sent to it, generator clock) and in-node latency of
    its registrations (node clock).
    """
    config, block = split_run(run)
    rows = []
    for session, g in gen.groupby('session', sort=False):
        e = ev[(ev['session'] == session) & (ev['http_status'] == 200)]
        for n in nodes:
            on_node = g[g['host'].astype(str) == n['host']]
            confirm = (pd.to_numeric(on_node['puback_ms'], errors='coerce')
                       - pd.to_numeric(on_node['publish_ms'], errors='coerce'))
            mine = e[e['node'] == n['name']]
            rows.append(dict(run=run, config=config, block=block, session=session,
                             level=float(g['level'].iloc[0]), node=n['name'], host=n['host'],
                             sent=int(pd.to_numeric(on_node['publish_ms'], errors='coerce').notna().sum()),
                             registered=len(mine),
                             **_stats(confirm, 'mqtt_confirm'),
                             **_stats(mine['in_node'], 'in_node')))
    return rows


def compare(levels, reference):
    """Level-by-level comparison of every configuration with the reference, within each block."""
    levels = levels.copy()
    if 'config' not in levels:
        parts = levels['run'].map(split_run)
        levels['config'] = parts.map(lambda p: p[0])
        levels['block'] = parts.map(lambda p: p[1])
    rows = []
    for block, lb in levels.groupby('block', sort=True):
        ref = lb[lb['config'] == reference].set_index('level')
        if ref.empty:
            continue
        for config in [c for c in lb['config'].unique() if c != reference]:
            oth = lb[lb['config'] == config].set_index('level')
            for level in sorted(set(ref.index) & set(oth.index)):
                row = dict(block=block, reference=reference, run=config, level=level)
                wins = {reference: 0, config: 0}
                for m in MEASURES:
                    a, b = ref.loc[level, m], oth.loc[level, m]
                    row[f'{m}_{reference}'] = a
                    row[f'{m}_{config}'] = b
                    if pd.isna(a) or pd.isna(b) or a == b:
                        winner = 'tie'
                    else:
                        winner = reference if a < b else config
                        wins[winner] += 1
                    row[f'{m}_better'] = winner
                row['preferable'] = next((k for k, v in wins.items() if v == len(MEASURES)), 'none')
                rows.append(row)
    return pd.DataFrame(rows)


def summarize(comparison):
    """Per block and pair: where each side is preferable, and the crossover."""
    if 'block' not in comparison:
        comparison = comparison.assign(block=1)
    rows = []
    for (block, reference, run), g in comparison.groupby(['block', 'reference', 'run'], sort=False):
        g = g.sort_values('level')
        low = g['preferable'].iloc[0]
        crossover = np.nan
        if low != 'none':
            stops = g[g['preferable'] != low]
            if len(stops):
                crossover = stops['level'].iloc[0]
        rows.append(dict(
            block=block, reference=reference, run=run, levels=len(g),
            preferable_at_lowest=low,
            levels_reference_preferable=' '.join(f'{x:g}' for x in g.loc[g['preferable'] == reference, 'level']),
            levels_run_preferable=' '.join(f'{x:g}' for x in g.loc[g['preferable'] == run, 'level']),
            levels_neither=' '.join(f'{x:g}' for x in g.loc[g['preferable'] == 'none', 'level']),
            crossover_level=crossover))
    return pd.DataFrame(rows)


def aggregate(levels):
    """Mean, min and max over the runs of each configuration and level (one value per run)."""
    rows = []
    for (config, level), g in levels.groupby(['config', 'level'], sort=False):
        row = dict(config=config, level=level, runs=len(g),
                   blocks=' '.join(str(b) for b in sorted(g['block'])),
                   sent_total=int(g['sent'].sum()), registered_total=int(g['registered'].sum()),
                   missing_total=int(g['missing'].sum()), rejected_total=int(g['rejected'].sum()))
        for m in AGGREGATED:
            v = pd.to_numeric(g[m], errors='coerce') if m in g else pd.Series(dtype=float)
            row[f'{m}_mean'] = v.mean() if v.notna().any() else np.nan
            row[f'{m}_min'] = v.min() if v.notna().any() else np.nan
            row[f'{m}_max'] = v.max() if v.notna().any() else np.nan
        busiest = g['busiest_node'].dropna().astype(str) if 'busiest_node' in g else pd.Series(dtype=str)
        row['busiest_nodes'] = ' '.join(sorted(set(busiest) - {''}))
        rows.append(row)
    return pd.DataFrame(rows).sort_values(['config', 'level'], kind='stable').reset_index(drop=True)


def consistency(comparison, summary):
    """Per pair and level: in how many blocks each side was preferable; crossover per block."""
    rows = []
    for (reference, run), g in comparison.groupby(['reference', 'run'], sort=False):
        s = summary[(summary['reference'] == reference) & (summary['run'] == run)].sort_values('block')
        crossovers = ' '.join('none' if pd.isna(c) else f'{c:g}' for c in s['crossover_level'])
        for level, gl in g.groupby('level', sort=True):
            n = len(gl)
            rows.append(dict(reference=reference, run=run, level=level, blocks=n,
                             reference_preferable=int((gl['preferable'] == reference).sum()),
                             run_preferable=int((gl['preferable'] == run).sum()),
                             neither=int((gl['preferable'] == 'none').sum()),
                             crossover_per_block=crossovers))
    return pd.DataFrame(rows)


def make_figure(levels):
    """
    The three panels against the offered rate, one line per configuration; the
    x axis is marked at the levels of the runs. With several runs per
    configuration the line is the mean over the runs and the bars span their
    minimum and maximum.
    """
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams['pdf.fonttype'] = 42   # embedded TrueType fonts (journal artwork rules)
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FixedLocator, FixedFormatter, NullLocator
    levels = levels.copy()
    if 'config' not in levels:
        levels['config'] = levels['run'].map(lambda r: split_run(r)[0])
    palette = ['#0072B2', '#D55E00', '#009E73', '#CC79A7']   # Okabe-Ito
    markers = ['o', 's', '^', 'D']
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.6))
    panels = [('latency_mean', 'Publication to registration, mean (ms)', '(a)'),
              ('util_max', 'Busiest node, pipeline CPU (% of one core)', '(b)'),
              ('cpu_ms_per_reg', 'CPU time per registration (ms)', '(c)')]
    for i, (config, g) in enumerate(levels.groupby('config', sort=False)):
        for ax, (col, _, _) in zip(axes, panels):
            s = pd.to_numeric(g[col], errors='coerce').groupby(g['level']).agg(['mean', 'min', 'max'])
            s = s.sort_index()
            kw = dict(marker=markers[i % 4], color=palette[i % 4], label=LABELS.get(config, config))
            if g.groupby('level').size().max() > 1:
                ax.errorbar(s.index, s['mean'], yerr=[s['mean'] - s['min'], s['max'] - s['mean']],
                            capsize=3, **kw)
            else:
                ax.plot(s.index, s['mean'], **kw)
    # On a log axis matplotlib only labels the powers of ten (one mark, 10^1, for 2-40/s):
    # mark the offered rates themselves instead.
    rates = sorted(levels['level'].dropna().unique())
    for ax, (col, ylabel, tag) in zip(axes, panels):
        ax.set_xscale('log')
        ax.xaxis.set_major_locator(FixedLocator(rates))
        ax.xaxis.set_major_formatter(FixedFormatter([f'{r:g}' for r in rates]))
        ax.xaxis.set_minor_locator(NullLocator())
        ax.set_xlabel('Offered rate, total (announcements/s)')
        ax.set_ylabel(ylabel)
        ax.set_title(tag, loc='left')
        ax.grid(alpha=0.3)
    axes[0].set_yscale('log')
    axes[1].set_ylim(0, None)
    axes[2].set_ylim(0, None)
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    return fig


def plot(levels, out):
    import matplotlib.pyplot as plt
    fig = make_figure(levels)
    for ext in ('pdf', 'png'):
        fig.savefig(out / f'fig_placement.{ext}', dpi=300, bbox_inches='tight')
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', nargs=2, action='append', required=True, metavar=('NAME', 'GENERATOR'))
    ap.add_argument('--node', nargs=5, action='append', required=True,
                    metavar=('RUN', 'NODE', 'HOST', 'ONBOARDING', 'RESOURCES'))
    ap.add_argument('--reference', default=None,
                    help='configuration the others are compared with (default: that of the first run)')
    ap.add_argument('--out', default='experiments/results/placement/analysis')
    ap.add_argument('--no-plots', action='store_true')
    args = ap.parse_args(argv)

    runs = [name for name, _ in args.run]
    if len(set(runs)) != len(runs):
        ap.error('each --run needs a different name')
    try:
        parts = {r: split_run(r) for r in runs}
    except ValueError as exc:
        ap.error(str(exc))
    if len(set(parts.values())) != len(parts):
        ap.error('two runs name the same configuration and block (central and central:1 are the same run)')
    configs = list(dict.fromkeys(p[0] for p in parts.values()))
    reference = args.reference or configs[0]
    if reference not in configs:
        ap.error(f'--reference {reference!r} is not the configuration of any --run')
    unknown = sorted({n[0] for n in args.node} - set(runs))
    if unknown:
        ap.error(f'--node for unknown run(s): {", ".join(unknown)}')

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    events, levels, node_levels = [], [], []
    for name, gen_path in args.run:
        gen = pd.read_csv(gen_path)
        if 'host' not in gen:
            ap.error(f'{gen_path} has no host column (generator older than the placement experiment)')
        gen = gen[gen['mode'] == 'ramp']
        nodes = [dict(name=n[1], host=n[2], onboarding=pd.read_csv(n[3]), resources=pd.read_csv(n[4]))
                 for n in args.node if n[0] == name]
        if not nodes:
            ap.error(f'run {name!r} has no --node')
        ev = run_events(name, gen, nodes)
        events.append(ev)
        levels += level_rows(name, gen, nodes, ev)
        node_levels += node_level_rows(name, gen, nodes, ev)
    events = pd.concat(events, ignore_index=True)
    levels = pd.DataFrame(levels)
    node_levels = pd.DataFrame(node_levels)
    comparison = compare(levels, reference)
    summary = summarize(comparison) if len(comparison) else pd.DataFrame()
    agg = aggregate(levels)
    cons = consistency(comparison, summary) if len(comparison) else pd.DataFrame()

    events.to_csv(out / 'placement_events.csv', index=False)
    levels.to_csv(out / 'placement_levels.csv', index=False)
    node_levels.to_csv(out / 'placement_node_levels.csv', index=False)
    comparison.to_csv(out / 'placement_comparison.csv', index=False)
    summary.to_csv(out / 'placement_summary.csv', index=False)
    agg.to_csv(out / 'placement_aggregate.csv', index=False)
    cons.to_csv(out / 'placement_consistency.csv', index=False)

    cols = ['config', 'level', 'runs', 'missing_total', 'latency_mean_mean', 'latency_mean_min',
            'latency_mean_max', 'util_max_mean', 'util_max_min', 'util_max_max',
            'cpu_ms_per_reg_mean', 'cpu_ms_per_reg_min', 'cpu_ms_per_reg_max']
    with pd.option_context('display.width', 220, 'display.max_columns', 30,
                           'display.float_format', '{:.1f}'.format):
        print(agg[[c for c in cols if c in agg]].to_string(index=False))
        if len(cons):
            print('\nBlocks in which each configuration is preferable (reference:', reference + ')')
            print(cons.to_string(index=False))
    if not args.no_plots:
        plot(levels, out)
    print(f'\nResults in {out.resolve()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
