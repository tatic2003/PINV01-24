#!/usr/bin/env python3
"""
Article figures for the centralized vs distributed baseline (DT and RF only).

Reads the per-event CSVs already written by run_distributed_benchmark.py and
run_benchmark.py; it does not re-run any benchmark.

Usage:
    python baseline/plot_centralized_vs_descentralized_fig.py baseline/results_pi5_catalog

Outputs (in the same directory unless --out is given):
    fig_baseline_latency.pdf/.png   boxplot + CDF of end-to-end latency
    fig_baseline_stages.pdf/.png    mean time per pipeline stage
    baseline_article_table.csv      statistics recomputed from the event CSVs
"""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# (key, file prefix, model, label, color)
SERIES = [
    ("central_dt", "baseline", "decision_tree", "Centralized DT", "#1f77b4"),
    ("distrib_dt", "distributed", "decision_tree", "Distributed DT", "#aec7e8"),
    ("central_rf", "baseline", "random_forest", "Centralized RF", "#d62728"),
    ("distrib_rf", "distributed", "random_forest", "Distributed RF", "#ff9896"),
]


def apply_dag_labels(results_dir: Path):
    """Append '(signed DAG)' to distributed labels only if the summary says so."""
    signed_all = True
    for i, (key, prefix, model, label, color) in enumerate(SERIES):
        if prefix != "distributed":
            continue
        summ = results_dir / f"{prefix}_{model}_summary.json"
        signed = bool(json.loads(summ.read_text()).get("dag_signed")) if summ.exists() else False
        signed_all &= signed
        suffix = " (signed DAG)" if signed else " (unsigned DAG)"
        SERIES[i] = (key, prefix, model, label + suffix, color)
    return signed_all


def load(results_dir: Path):
    data = {}
    for key, prefix, model, label, color in SERIES:
        path = results_dir / f"{prefix}_{model}_events.csv"
        if not path.exists():
            raise SystemExit(f"Missing {path}")
        df = pd.read_csv(path)
        if "t_dag_ms" not in df:
            df["t_dag_ms"] = 0.0
        data[key] = df
    return data


def stats_table(data):
    rows = []
    for key, prefix, model, label, color in SERIES:
        df = data[key]
        t = df["t_total_ms"]
        rows.append({
            "series": key,
            "label": label,
            "n": len(df),
            "mean_ms": t.mean(),
            "sd_ms": t.std(),
            "median_ms": t.median(),
            "p95_ms": t.quantile(0.95),
            "p99_ms": t.quantile(0.99),
            "classification_mean_ms": df["t_classification_ms"].mean(),
            "shacl_mean_ms": df["t_shacl_ms"].mean(),
            "dag_mean_ms": df["t_dag_ms"].mean(),
            "shacl_conformance": df["shacl_conforms"].astype(str).str.lower().eq("true").mean(),
        })
    return pd.DataFrame(rows)


def plot_latency(data, out: Path, figsize=(10, 4)):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=figsize)
    keys = [s[0] for s in SERIES]
    labels = [s[3] for s in SERIES]
    colors = [s[4] for s in SERIES]

    # Boxplot without outliers; the p99 tail is shown by the CDF instead.
    bp = ax1.boxplot([data[k]["t_total_ms"] for k in keys], patch_artist=True,
                     showfliers=False, widths=0.6)
    for patch, c in zip(bp["boxes"], colors):
        patch.set_facecolor(c)
    for med in bp["medians"]:
        med.set_color("black")
    ax1.set_xticks(range(1, len(keys) + 1))
    # "Distributed DT (signed DAG)" -> "DT\ndistributed"; the legend of (b) has the full names
    ax1.set_xticklabels([f"{l.split()[1]}\n{l.split()[0].lower()}" for l in labels], fontsize=8)
    ax1.set_ylabel("End-to-end latency per event (ms)")
    ax1.set_ylim(bottom=0)
    ax1.grid(axis="y", alpha=0.3)
    ax1.set_title("(a)", loc="left")

    for k, l, c in zip(keys, labels, colors):
        t = np.sort(data[k]["t_total_ms"].to_numpy())
        ax2.plot(t, np.arange(1, len(t) + 1) / len(t), label=l, color=c, lw=1.6)
    hi = max(data[k]["t_total_ms"].quantile(0.995) for k in keys)
    ax2.set_xlim(0, hi * 1.05)
    ax2.set_ylim(0, 1.01)
    ax2.set_xlabel("End-to-end latency per event (ms)")
    ax2.set_ylabel("Cumulative probability")
    ax2.grid(alpha=0.3)
    ax2.set_title("(b)", loc="left")

    handles, names = ax2.get_legend_handles_labels()
    fig.tight_layout(rect=(0, 0.13, 1, 1))
    fig.legend(handles, names, fontsize=8, loc="lower center", ncol=2, frameon=False)
    for ext in ("pdf", "png"):
        fig.savefig(out / f"fig_baseline_latency.{ext}", dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_stages(data, out: Path, figsize=(6.5, 4)):
    keys = [s[0] for s in SERIES]
    labels = [s[3].replace(" (", "\n(") for s in SERIES]
    cls = np.array([data[k]["t_classification_ms"].mean() for k in keys])
    sh = np.array([data[k]["t_shacl_ms"].mean() for k in keys])
    dag = np.array([data[k]["t_dag_ms"].mean() for k in keys])
    tot = np.array([data[k]["t_total_ms"].mean() for k in keys])
    other = np.clip(tot - cls - sh - dag, 0, None)

    fig, ax = plt.subplots(figsize=figsize)
    x = np.arange(len(keys))
    bottom = np.zeros(len(keys))
    for vals, name, color in [(cls, "PnP-AI classification", "#4c72b0"),
                              (sh, "SHACL validation", "#dd8452"),
                              (dag, "BlockDAG append", "#55a868"),
                              (other, "Other (logging, overhead)", "#bbbbbb")]:
        ax.bar(x, vals, 0.6, bottom=bottom, label=name, color=color)
        bottom += vals
    for xi, t in zip(x, tot):
        ax.text(xi, t + 0.2, f"{t:.2f}", ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("Mean time per event (ms)")
    ax.set_ylim(0, tot.max() * 1.18)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(out / f"fig_baseline_stages.{ext}", dpi=300, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dir", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = args.out or args.results_dir
    out.mkdir(parents=True, exist_ok=True)

    signed = apply_dag_labels(args.results_dir)
    print(f"Distributed DAG signed: {signed}")
    data = load(args.results_dir)
    table = stats_table(data)
    table.to_csv(out / "baseline_article_table.csv", index=False)
    with pd.option_context("display.width", 160, "display.float_format", "{:.2f}".format):
        print(table.drop(columns=["label"]).to_string(index=False))
    plot_latency(data, out)
    plot_stages(data, out)
    print(f"Figures and table written to {out}")


if __name__ == "__main__":
    main()
