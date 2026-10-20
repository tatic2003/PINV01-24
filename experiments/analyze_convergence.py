#!/usr/bin/env python3
"""
Analysis of the partition experiment (experiments/run_partition.py).

Inputs:
  --runs   CSV of run_partition.py
  --agent  NAME CSV   CSV of the dag_sync.py agent of each node (repeatable)

For each repetition, the merges that belong to its recovery are the agent
rows between the restoration of the link and 1 s after convergence; nodes
added there are the nodes transferred after the partition.

Outputs (in --out):
  convergence_runs.csv     one row per repetition, with the transferred and
                           rejected nodes of its recovery
  convergence_summary.csv  per M: repetitions, converged, convergence time
                           (median, P95, max), nodes created per node
                           (median), late nodes, transferred and rejected
                           nodes by reason
  rejections_total.csv     rejected nodes by reason over the whole run of each
                           agent (also outside the recoveries)
  fig_convergence.pdf/png  convergence time vs M

Usage:
  python experiments/analyze_convergence.py --runs runs.csv \\
      --agent pi5 sync_pi5.csv --agent opi sync_opi.csv --out experiments/results/convergence

Project: PINV01-24 - FIUNA
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

REASONS = ['missing_parents', 'bad_signature', 'hash_conflict', 'broken_link',
           'hash_failed', 'other']
MARGIN_MS = 1000.0


def load_agents(pairs):
    frames = []
    for name, path in pairs:
        df = pd.read_csv(path)
        df['agent'] = name
        frames.append(df)
    agents = pd.concat(frames, ignore_index=True)
    for c in ['offered', 'added', 'existing', 'failed'] + REASONS:
        agents[c] = pd.to_numeric(agents[c], errors='coerce').fillna(0).astype(int)
    agents['error'] = agents['error'].fillna('')
    return agents


def attach_recovery(runs, agents):
    """Transferred and rejected nodes of each repetition's recovery."""
    runs = runs.copy()
    names = sorted(agents['agent'].unique())
    for i, r in runs.iterrows():
        win = agents[(agents['epoch_ms'] >= r['t_unblock_ms'])
                     & (agents['epoch_ms'] <= r['t_converged_ms'] + MARGIN_MS)]
        for name in names:
            runs.loc[i, f'added_{name}'] = int(win.loc[win['agent'] == name, 'added'].sum())
        runs.loc[i, 'merges'] = int((win['offered'] > 0).sum())
        runs.loc[i, 'failed'] = int(win['failed'].sum())
        for k in REASONS:
            runs.loc[i, k] = int(win[k].sum())
    return runs


def p95(x):
    return float(np.percentile(x, 95)) if len(x) else np.nan


def summarize(runs):
    rows = []
    for m, g in runs.groupby('m'):
        ok = g[g['converged'].astype(str).str.lower() == 'true']
        t = ok['convergence_ms']
        row = dict(m=m, repetitions=len(g), converged=len(ok),
                   convergence_median_ms=t.median(), convergence_p95_ms=p95(t),
                   convergence_max_ms=t.max(), convergence_min_ms=t.min(),
                   nodes_local_median=g['nodes_local'].median(),
                   nodes_peer_median=g['nodes_peer'].median(),
                   late_nodes=int(g['late_nodes'].sum()),
                   generator_errors=int((g[['gen_local_rc', 'gen_peer_rc']] != 0).any(axis=1).sum()))
        for c in [c for c in g.columns if c.startswith('added_')]:
            row[f'{c}_median'] = g[c].median()
        row['failed'] = int(g['failed'].sum())
        for k in REASONS:
            row[k] = int(g[k].sum())
        rows.append(row)
    return pd.DataFrame(rows)


def total_rejections(agents):
    g = agents.groupby('agent')
    out = g[['offered', 'added', 'existing', 'failed'] + REASONS].sum()
    out['rounds_with_error'] = g['error'].apply(lambda e: int((e != '').sum()))
    return out.reset_index()


def plot(runs, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    ok = runs[runs['converged'].astype(str).str.lower() == 'true']
    ms = sorted(ok['m'].unique())
    data = [ok.loc[ok['m'] == m, 'convergence_ms'].values / 1000.0 for m in ms]
    fig, ax = plt.subplots(figsize=(4.5, 3.2))
    ax.boxplot(data, positions=range(len(ms)), widths=0.5, showfliers=False)
    for i, d in enumerate(data):
        ax.scatter(np.full(len(d), i) + np.random.default_rng(i).uniform(-0.12, 0.12, len(d)),
                   d, s=10, color='#1f5a96', alpha=0.7, zorder=3)
    ax.set_xticks(range(len(ms)), [str(m) for m in ms])
    ax.set_xlabel('Devices registered per node during the partition ($M$)')
    ax.set_ylabel('Convergence time (s)')
    ax.grid(axis='y', alpha=0.3)
    fig.tight_layout()
    for ext in ('pdf', 'png'):
        fig.savefig(out / f'fig_convergence.{ext}', dpi=200)
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--runs', required=True)
    ap.add_argument('--agent', nargs=2, action='append', required=True, metavar=('NAME', 'CSV'))
    ap.add_argument('--out', default='experiments/results/convergence')
    ap.add_argument('--no-plots', action='store_true')
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runs = pd.read_csv(args.runs)
    agents = load_agents(args.agent)
    runs = attach_recovery(runs, agents)
    summary = summarize(runs)
    totals = total_rejections(agents)
    runs.to_csv(out / 'convergence_runs.csv', index=False)
    summary.to_csv(out / 'convergence_summary.csv', index=False)
    totals.to_csv(out / 'rejections_total.csv', index=False)
    if not args.no_plots:
        plot(runs, out)
    with pd.option_context('display.width', 200, 'display.max_columns', 40):
        print(summary.to_string(index=False))
        print()
        print(totals.to_string(index=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
