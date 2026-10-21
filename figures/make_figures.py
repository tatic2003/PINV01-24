#!/usr/bin/env python3
"""
Regenerates every figure of the article from the measured data.

Each figure is drawn by one function that reads the raw data files listed in
figures/config.yml (and nothing else): no value shown in a figure is written
in the code. Figures whose data come from a pipeline that already exists in
the repository (baseline, onboarding, load, convergence, placement) are drawn by calling
that pipeline's own functions, so there is a single implementation of each.

Usage (from the repository root):
  python figures/make_figures.py                 # every figure
  python figures/make_figures.py fig_lwt_detection fig_gnss_ttff
  python figures/make_figures.py --list          # figures and their data files

A figure whose data files are missing (an experiment not run yet) is skipped
with a message; --strict turns that into an error.

Outputs (in --out, default figures/out):
  <figure>.pdf / .png      the figure (PDF for the article, PNG to look at)
  <figure>_data.csv        values plotted, where they are computed here
  MANIFEST.csv             for each figure, every data file it read and its SHA-256

Project: PINV01-24 - FIUNA
"""

import argparse
import csv
import glob
import hashlib
import importlib.util
import shutil
import statistics as st
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import style  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402


class MissingData(Exception):
    """The data files of a figure are not there (yet)."""


class DataMismatch(Exception):
    """Two records of the same measurement disagree."""


def path(p):
    return (ROOT / p).resolve()


def rel(p):
    p = Path(p).resolve()
    return p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else str(p)


def need(*paths):
    missing = [rel(p) for p in paths if not Path(p).exists()]
    if missing:
        raise MissingData(", ".join(missing))
    return [Path(p) for p in paths]


def load_module(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def write_rows(out_dir, name, rows):
    if not rows:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{name}_data.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def fmt(x, digits=2):
    return f"{x:.{digits}f}".replace("-", "−")


# ============================================================ baseline

def fig_baseline(cfg, out):
    """Figures 'baseline stages' and 'baseline latency' (baseline/plot_centralized_vs_descentralized_fig.py)."""
    d = path(cfg["baseline"]["results_dir"])
    mod = load_module("plot_baseline", "baseline/plot_centralized_vs_descentralized_fig.py")
    inputs = []
    for _key, prefix, model, _label, _color in mod.SERIES:
        inputs += [d / f"{prefix}_{model}_events.csv", d / f"{prefix}_{model}_summary.json"]
    need(*[p for p in inputs if p.name.endswith(".csv")])
    mod.apply_dag_labels(d)
    data = mod.load(d)
    mod.plot_latency(data, out, figsize=(style.FULL_WIDTH, 3.0))
    mod.plot_stages(data, out, figsize=(style.NARROW_WIDTH, 3.3))
    inputs = [p for p in inputs if p.exists()]
    return {"fig_baseline_stages": inputs, "fig_baseline_latency": inputs}


# ============================================================ TTFF

def read_nmea_runs(log_path):
    """{run number: [(host time 'HH:MM:SS.mmm', sentence), ...]} from an ttff_gps.py log."""
    runs, current = {}, None
    for line in open(log_path, encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if line.startswith("# corrida"):
            current = int(line.split()[2])
            runs[current] = []
        elif current is not None and len(line) > 13 and line[2] == ":" and line[12] == " ":
            runs[current].append((line[:12], line[13:]))
    return runs


def ttff_from_log(start_utc, lines):
    """Same criterion as ttff_gps.py: RMC status A and a GGA with quality >= 1."""
    rmc_ok, gga = False, None
    for hms, sentence in lines:
        fields = sentence.split(",")
        if sentence[3:6] == "RMC" and len(fields) > 2:
            rmc_ok = fields[2] == "A"
        elif sentence[3:6] == "GGA" and len(fields) > 8:
            q = int(fields[6]) if fields[6].isdigit() else 0
            if q >= 1:
                gga = q
        if rmc_ok and gga:
            t = datetime.strptime(hms, "%H:%M:%S.%f").time()
            arrival = datetime.combine(start_utc.date(), t, tzinfo=timezone.utc)
            if arrival < start_utc - timedelta(hours=12):
                arrival += timedelta(days=1)
            return (arrival - start_utc).total_seconds()
    return None


def fig_gnss_ttff(cfg, out):
    c = cfg["ttff"]
    inputs, series, rows = [], [], []
    for r in c["runs"]:
        csv_p, log_p = need(path(r["csv"]), path(r["nmea"]))
        inputs += [csv_p, log_p]
        log_runs = read_nmea_runs(log_p)
        values = []
        for row in csv.DictReader(open(csv_p, encoding="utf-8")):
            n = int(row["corrida"])
            if not row["ttff_s"]:
                raise DataMismatch(f"{csv_p.name} run {n}: no fix")
            start = datetime.fromisoformat(row["inicio_utc"])
            recomputed = ttff_from_log(start, log_runs.get(n, []))
            if recomputed is None or abs(recomputed - float(row["ttff_s"])) > c["tolerance_s"]:
                raise DataMismatch(f"{csv_p.name} run {n}: CSV {row['ttff_s']} s, log {recomputed}")
            values.append(recomputed)
            rows.append(dict(start_type=r["label"], run=n, ttff_csv_s=row["ttff_s"],
                             ttff_log_s=round(recomputed, 3), satellites=row["satelites"],
                             hdop=row["hdop"]))
        series.append((r["label"], values))

    fig, ax = plt.subplots(figsize=(style.NARROW_WIDTH, 0.55 * len(series) + 1.0))
    for i, (label, v) in enumerate(series):
        offs = np.linspace(-0.12, 0.12, len(v)) if len(v) > 1 else [0.0]
        ax.scatter(v, [i + o for o in offs], s=26, color=style.BLUE, edgecolor="white",
                   linewidth=0.8, zorder=3)
        m = st.median(v)
        ax.plot([m, m], [i - 0.28, i + 0.28], color=style.INK, lw=1.8, zorder=4)
        ax.text(m * 1.12, i - 0.3, f"{m:.1f} s", color=style.GREY, fontsize=7.5, va="center")
    ax.set_xscale("log")
    lo = min(min(v) for _, v in series)
    hi = max(max(v) for _, v in series)
    ax.set_xlim(lo / 1.6, hi * 1.9)
    ticks = [t for t in (1, 2, 5, 10, 20, 50, 100, 200) if lo / 1.6 <= t <= hi * 1.9]
    ax.set_xticks(ticks, [str(t) for t in ticks])
    ax.xaxis.set_minor_formatter(plt.NullFormatter())
    ax.set_yticks(range(len(series)), [s[0].replace(" (", "\n(") for s in series])
    ax.set_ylim(len(series) - 0.4, -0.6)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Time to first fix (s, log scale)")
    ax.legend(handles=[Line2D([], [], marker="o", ls="", color=style.BLUE, mec="white", ms=5,
                              label=f"run (n = {len(series[0][1])} per type)"),
                       Line2D([], [], color=style.INK, lw=1.8, label="median")],
              loc="upper right")
    write_rows(out, "fig_gnss_ttff", rows)
    style.save(fig, out, "fig_gnss_ttff")
    return {"fig_gnss_ttff": inputs}


# ============================================================ chrony

def read_chrony(p, kind):
    rows = []
    for line in open(p, encoding="utf-8", errors="replace"):
        f = line.split()
        if len(f) < 12 or len(f[0]) != 10 or f[0][4] != "-":
            continue
        t = datetime.strptime(f"{f[0]} {f[1]}", "%Y-%m-%d %H:%M:%S")
        if kind == "statistics":
            rows.append(dict(t=t, src=f[2], sd=float(f[3]), est=float(f[4])))
        else:
            rows.append(dict(t=t, src=f[2], off=float(f[6])))
    return rows


def window(rows, w, src):
    a, b = (datetime.strptime(x, "%Y-%m-%d %H:%M:%S") for x in w)
    return [r for r in rows if a <= r["t"] <= b and r["src"] == src]


def _chrony(cfg):
    c = cfg["chrony"]
    s_p, t_p = need(path(c["statistics"]), path(c["tracking"]))
    shift = timedelta(hours=c["display_utc_offset_h"])
    return c, read_chrony(s_p, "statistics"), read_chrony(t_p, "tracking"), shift, [s_p, t_p]


def fig_clock_error(cfg, out):
    c, stats, trk, shift, inputs = _chrony(cfg)
    a = window(stats, c["ntp_window"], "PPS")
    b = window(trk, c["pps_window"], "PPS")
    if not a or not b:
        raise DataMismatch("no PPS samples in one of the windows")
    ya = np.array([r["est"] for r in a]) * 1e3
    yb = np.array([r["off"] for r in b]) * 1e6
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(style.FULL_WIDTH, 2.5))
    ax1.plot([r["t"] + shift for r in a], ya, color=style.VERMILION, lw=1.0, marker="o", ms=2.4,
             mec="white", mew=0.4)
    ax2.plot([r["t"] + shift for r in b], yb, color=style.BLUE, lw=0.9, marker="o", ms=2.2,
             mec="white", mew=0.4)
    for ax, y, unit, title, extra in [
            (ax1, ya, "ms", "(a) NTP-disciplined clock", ""),
            (ax2, yb, "µs", "(b) PPS-disciplined clock",
             f"\n99% of |error| < {np.percentile(np.abs(yb), 99):.1f} µs")]:
        ax.axhline(0, color=style.GREY, lw=0.7)
        span = max(abs(y.min()), abs(y.max()))
        ax.set_ylim(min(-0.1 * span, y.min() * 1.15), max(0.1 * span, y.max() * 1.15))
        ax.set_ylabel(f"Error vs. PPS edge ({unit})")
        ax.set_xlabel(c["display_label"])
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.set_title(title, loc="left", pad=6 + 9 * (1 + extra.count("\n")))
        ax.text(0.0, 1.02, f"mean {fmt(y.mean())} {unit}, σ = {fmt(y.std(ddof=1))} {unit}, "
                f"n = {len(y)}" + extra, transform=ax.transAxes, fontsize=7, color=style.GREY,
                va="bottom")
    ax1.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
    ax2.xaxis.set_major_locator(mdates.MinuteLocator(interval=10))
    fig.tight_layout()
    write_rows(out, "fig_clock_error",
               [dict(config="NTP", utc=r["t"].isoformat(), error_ms=r["est"] * 1e3) for r in a]
               + [dict(config="PPS", utc=r["t"].isoformat(), error_ms=r["off"] * 1e3) for r in b])
    style.save(fig, out, "fig_clock_error")
    return {"fig_clock_error": inputs}


def fig_nmea_residual(cfg, out):
    c, stats, _trk, shift, inputs = _chrony(cfg)
    n1 = window(stats, c["ntp_window"], "NMEA")
    n2 = window(stats, c["pps_window"], "NMEA")
    if not n1 or not n2:
        raise DataMismatch("no NMEA samples in one of the windows")
    fig, ax = plt.subplots(figsize=(style.FULL_WIDTH, 2.3))
    for rows, marker, color, label in [(n1, "o", style.VERMILION, "NTP-disciplined clock"),
                                       (n2, "s", style.BLUE, "PPS-disciplined clock")]:
        y = np.array([r["est"] for r in rows]) * 1e3
        ax.plot([r["t"] + shift for r in rows], y, color=color, lw=0.8, marker=marker, ms=2.4,
                mec="white", mew=0.4,
                label=f"{label}: mean {fmt(y.mean(), 1)} ms, σ = {fmt(y.std(ddof=1), 1)} ms")
    ax.axhline(0, color=style.GREY, lw=0.7)
    ax.set_ylabel("NMEA time − host clock (ms)")
    ax.set_xlabel(c["display_label"])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=10))
    ax.legend(loc="upper right", ncol=1)
    fig.tight_layout()
    write_rows(out, "fig_nmea_residual",
               [dict(config="NTP", utc=r["t"].isoformat(), residual_ms=r["est"] * 1e3) for r in n1]
               + [dict(config="PPS", utc=r["t"].isoformat(), residual_ms=r["est"] * 1e3) for r in n2])
    style.save(fig, out, "fig_nmea_residual")
    return {"fig_nmea_residual": inputs}


# ============================================================ GNSS receiver as a device

def fig_gnss_device(cfg, out):
    c = cfg["gnss_device"]
    lwt_p, val_p = need(path(c["lwt"]), path(c["validation"]))
    lwt = list(csv.DictReader(open(lwt_p, encoding="utf-8")))
    val = list(csv.DictReader(open(val_p, encoding="utf-8")))
    det = [float(r["since_last_packet_s"]) for r in lwt]
    expected = sorted({float(r["expected_s"]) for r in lwt})
    keepalive = sorted({r["keepalive_s"] for r in lwt})
    is_true = lambda v: str(v).strip().lower() == "true"  # noqa: E731
    valid = [float(r["register_ms"]) for r in val if is_true(r["expected_valid"])]
    rejected = [float(r["register_ms"]) for r in val if not is_true(r["expected_valid"])]
    shacl = [float(r["shacl_ms"]) for r in val if r["case"] not in c["cases_without_shacl"]]

    fig, (a, b) = plt.subplots(1, 2, figsize=(style.FULL_WIDTH, 2.6),
                               gridspec_kw={"width_ratios": [1, 1.15]})
    for e in expected:
        a.axhline(e, color=style.GREY, lw=0.8, ls="--")
        a.text(len(det) + 0.4, e, f"1.5 × keepalive = {e:g} s", ha="right", va="bottom",
               fontsize=7, color=style.GREY)
    a.plot(range(1, len(det) + 1), det, "o", color=style.BLUE, ms=4.5, mec="white", mew=0.7)
    lo, hi = min(det + expected), max(det + expected)
    pad = max(0.1, 0.25 * (hi - lo))
    a.set_ylim(lo - pad, hi + pad)
    a.set_xlim(0.4, len(det) + 0.6)
    a.set_xticks(range(1, len(det) + 1))
    a.set_xlabel("Run")
    a.set_ylabel("Last packet → Last Will (s)")
    style.panel_title(a, f"(a) Loss detection, keepalive {', '.join(keepalive)} s")
    a.text(0.03, 0.05, f"mean {det and fmt(st.mean(det))} s, σ = {fmt(st.stdev(det))} s, "
           f"n = {len(det)}", transform=a.transAxes, fontsize=7, color=style.GREY)

    rng = np.random.default_rng(1)
    data = [valid, rejected, shacl]
    colors = [style.GREEN, style.VERMILION, style.BLUE]
    for i, (d, col) in enumerate(zip(data, colors), start=1):
        b.plot(i + rng.uniform(-0.18, 0.18, len(d)), d, "o", color=col, ms=2.2, alpha=0.45, mew=0)
        m = st.median(d)
        b.hlines(m, i - 0.32, i + 0.32, color=style.INK, lw=1.6)
        b.text(i + 0.36, m, f"{m:.1f}", va="center", fontsize=7)
    b.set_xticks([1, 2, 3], [f"Registration\nvalid\n(n = {len(valid)})",
                             f"Registration\nrejected\n(n = {len(rejected)})",
                             f"SHACL\nvalidation\n(n = {len(shacl)})"])
    b.set_xlim(0.5, 3.8)
    b.set_ylim(0, max(max(d) for d in data) * 1.08)
    b.set_ylabel("Time (ms)")
    style.panel_title(b, "(b) Processing time per announcement")
    fig.tight_layout()
    style.save(fig, out, "fig_gnss_device")
    return {"fig_gnss_device": [lwt_p, val_p]}


# ============================================================ Last Will on the Ethernet boards

def _ts(s):
    """Seconds since the epoch of an ISO time; the bridge writes an offset, the trial CSVs do not.

    Both are the local time of the same host, so the offset is dropped from the
    bridge stamps before comparing.
    """
    return datetime.fromisoformat(s[:23]).timestamp()


def lwt_rows(series):
    """Per cut: detection (cut -> Last Will), time from the last device packet to the cut, residual.

    The last device packet is the latest of (i) the last heartbeat or data
    message the bridge received before the Last Will and (ii) the last PINGREQ,
    which never reaches the bridge and is estimated as connection + k * keepalive,
    where the connection is the last 'state=active' before the cut. The MQTT
    model puts the Last Will at last packet + 1.5 * keepalive.
    """
    bridge = list(csv.DictReader(open(series["bridge"], encoding="utf-8")))
    bridge = [r for r in bridge if r["device_id"] == series["device_id"]]
    actives = [_ts(r["ts_iso"]) for r in bridge if r["event"] == "state" and r["detail"] == "state=active"]
    pubs = [_ts(r["ts_iso"]) for r in bridge if r["event"] in ("heartbeat", "data")]
    K = float(series["keepalive_s"])
    out = []
    for r in csv.DictReader(open(series["trials"], encoding="utf-8")):
        if str(r.get("valid", "True")).strip().lower() != "true":
            continue
        cut, lwt = _ts(r["t_cut"]), _ts(r["t_lwt"])
        before = [t for t in actives if t < cut]
        if not before:
            raise DataMismatch(f"{Path(series['trials']).name} trial {r['trial']}: no connection before the cut")
        conn = max(before)
        pings = [conn + K * k for k in range(1, int((cut - conn) / K) + 2) if conn + K * k < cut]
        last = max(pings + [t for t in pubs if t < lwt] + [conn])
        out.append(dict(board=series["board"], keepalive_s=K, trial=int(r["trial"]),
                        detection_s=float(r["detection_s"]), since_last_packet_s=round(cut - last, 3),
                        model_s=round(last + 1.5 * K - cut, 3), residual_s=round(lwt - (last + 1.5 * K), 3)))
    return out


def fig_lwt_detection(cfg, out):
    inputs, rows = [], []
    for s in cfg["lwt_ethernet"]["series"]:
        t_p, b_p = need(path(s["trials"]), path(s["bridge"]))
        inputs += [t_p, b_p]
        rows += lwt_rows(dict(s, trials=t_p, bridge=b_p))
    keepalives = sorted({r["keepalive_s"] for r in rows}, reverse=True)
    boards = list(dict.fromkeys(r["board"] for r in rows))
    marks = dict(zip(boards, [("o", style.BLUE), ("s", style.VERMILION), ("^", style.GREEN)]))

    fig, axes = plt.subplots(1, len(keepalives), figsize=(style.FULL_WIDTH, 2.9), squeeze=False)
    for ax, K, letter in zip(axes[0], keepalives, "abcdef"):
        ax.plot([0, K], [1.5 * K, 0.5 * K], ls=(0, (4, 3)), lw=1.1, color=style.GREY, zorder=1,
                label="model: 1.5K − time since last packet")
        for board in boards:
            g = [r for r in rows if r["keepalive_s"] == K and r["board"] == board]
            m, col = marks[board]
            ax.scatter([r["since_last_packet_s"] for r in g], [r["detection_s"] for r in g], s=28,
                       marker=m, color=col, edgecolor="white", linewidth=0.8, zorder=3,
                       label=f"{board} (n = {len(g)})")
        ax.set_xlim(-0.05 * K, 1.05 * K)
        ax.set_ylim(0.4 * K, 1.6 * K)
        ax.set_xlabel("Last device packet → cut (s)")
        style.panel_title(ax, f"({letter}) K = {K:g} s")
    axes[0][0].set_ylabel("Cut → Last Will (s)")
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.legend(handles, labels, loc="upper center", ncol=len(labels), bbox_to_anchor=(0.5, 1.0))
    write_rows(out, "fig_lwt_detection", rows)
    style.save(fig, out, "fig_lwt_detection")

    for K in keepalives:
        for board in boards:
            g = [r for r in rows if r["keepalive_s"] == K and r["board"] == board]
            d = [r["detection_s"] for r in g]
            res = [r["residual_s"] for r in g]
            print(f"    {board}, K = {K:g} s: detection median {st.median(d):.2f} s "
                  f"({min(d):.2f}-{max(d):.2f}), residual {min(res):+.2f} to {max(res):+.2f} s")
    return {"fig_lwt_detection": inputs}


# ============================================================ pipelines of the experiments

def _globs(patterns):
    files = []
    for p in patterns:
        files += sorted(glob.glob(str(path(p))))
    return [Path(f) for f in files]


def fig_onboarding(cfg, out):
    c = cfg["onboarding"]
    onb = _globs(c["onboarding_csvs"])
    if not onb:
        raise MissingData(", ".join(c["onboarding_csvs"]))
    serial = _globs(c.get("serial_agent_csvs", []))
    probe = path(c["probe_csv"]) if c.get("probe_csv") else None
    work = out / "onboarding"
    argv = [str(p) for p in onb] + ["--out", str(work), "--fig-width", str(style.FULL_WIDTH)]
    if serial:
        argv += ["--serial-agent"] + [str(p) for p in serial]
    if probe and probe.exists():
        argv += ["--probe", str(probe)]
    load_module("analyze_onboarding", "experiments/analyze_onboarding.py").main(argv)
    for ext in ("pdf", "png"):
        shutil.copy(work / f"fig_onboarding_stages.{ext}", out / f"fig_onboarding_stages.{ext}")
    return {"fig_onboarding_stages": onb + serial + ([probe] if probe and probe.exists() else [])}


def fig_load(cfg, out):
    nodes = [n for n in cfg["load"]["nodes"]
             if all(path(n[k]).exists() for k in ("onboarding", "resources", "generator"))]
    if not nodes:
        raise MissingData("figures/data/load/ (no node with its three CSVs)")
    argv, inputs = [], []
    for n in nodes:
        files = [path(n[k]) for k in ("onboarding", "resources", "generator")]
        argv += ["--node", n["name"]] + [str(f) for f in files]
        inputs += files
    work = out / "load"
    load_module("analyze_load", "experiments/analyze_load.py").main(argv + ["--out", str(work)])
    made = {}
    for name in ("fig_load_burst", "fig_load_ramp"):
        if (work / f"{name}.pdf").exists():
            for ext in ("pdf", "png"):
                shutil.copy(work / f"{name}.{ext}", out / f"{name}.{ext}")
            made[name] = inputs
    return made


def fig_convergence(cfg, out):
    c = cfg["convergence"]
    runs = path(c["runs"])
    agents = [(a["name"], path(a["csv"])) for a in c["agents"]]
    need(runs, *[p for _, p in agents])
    work = out / "convergence"
    argv = ["--runs", str(runs), "--out", str(work)]
    for name, p in agents:
        argv += ["--agent", name, str(p)]
    load_module("analyze_convergence", "experiments/analyze_convergence.py").main(argv)
    for ext in ("pdf", "png"):
        shutil.copy(work / f"fig_convergence.{ext}", out / f"fig_convergence.{ext}")
    return {"fig_convergence": [runs] + [p for _, p in agents]}


def fig_placement(cfg, out):
    c = cfg["placement"]
    argv, inputs = [], []
    for r in c["runs"]:
        gen = path(r["generator"])
        need(gen)
        argv += ["--run", r["name"], str(gen)]
        inputs.append(gen)
        for n in r["nodes"]:
            files = need(path(n["onboarding"]), path(n["resources"]))
            argv += ["--node", r["name"], n["name"], str(n["host"])] + [str(f) for f in files]
            inputs += files
    work = out / "placement"
    argv += ["--reference", c["reference"], "--out", str(work)]
    load_module("analyze_placement", "experiments/analyze_placement.py").main(argv)
    for ext in ("pdf", "png"):
        shutil.copy(work / f"fig_placement.{ext}", out / f"fig_placement.{ext}")
    return {"fig_placement": inputs}


# name -> (function, figures it writes, short description)
FIGURES = {
    "fig_baseline": (fig_baseline, ["fig_baseline_stages", "fig_baseline_latency"],
                     "simulated baseline on the Pi 5: stages and end-to-end latency"),
    "fig_gnss_ttff": (fig_gnss_ttff, ["fig_gnss_ttff"], "TTFF by start type"),
    "fig_clock_error": (fig_clock_error, ["fig_clock_error"], "Pi 5 clock error, NTP vs PPS"),
    "fig_nmea_residual": (fig_nmea_residual, ["fig_nmea_residual"], "NMEA time residual"),
    "fig_gnss_device": (fig_gnss_device, ["fig_gnss_device"], "GNSS receiver as an ITS device"),
    "fig_onboarding_stages": (fig_onboarding, ["fig_onboarding_stages"], "onboarding stages per board"),
    "fig_lwt_detection": (fig_lwt_detection, ["fig_lwt_detection"], "Last-Will detection, Ethernet boards"),
    "fig_load": (fig_load, ["fig_load_burst", "fig_load_ramp"], "load with emulated devices"),
    "fig_convergence": (fig_convergence, ["fig_convergence"], "DAG convergence after a partition"),
    "fig_placement": (fig_placement, ["fig_placement"], "centralized vs distributed placement"),
}
ALIASES = {out_name: key for key, (_, outs, _) in FIGURES.items() for out_name in outs}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("figures", nargs="*", help="figures to draw (default: all); see --list")
    ap.add_argument("--config", default=str(HERE / "config.yml"))
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--list", action="store_true", help="list the figures and exit")
    ap.add_argument("--strict", action="store_true", help="fail when the data of a figure are missing")
    args = ap.parse_args(argv)

    if args.list:
        for key, (_, outs, desc) in FIGURES.items():
            print(f"{key:24s} {desc}  ->  {', '.join(outs)}")
        return 0
    wanted = [ALIASES.get(f, f) for f in args.figures] or list(FIGURES)
    unknown = [f for f in wanted if f not in FIGURES]
    if unknown:
        ap.error(f"unknown figure(s): {', '.join(unknown)} (see --list)")
    wanted = list(dict.fromkeys(wanted))

    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    style.apply()

    manifest, skipped, failed = [], [], []
    for key in wanted:
        func = FIGURES[key][0]
        print(f"{key} ...")
        try:
            made = func(cfg, out)
        except MissingData as e:
            print(f"  skipped: missing data ({e})")
            skipped.append(key)
            continue
        except DataMismatch as e:
            print(f"  FAILED: {e}")
            failed.append(key)
            continue
        finally:
            plt.close("all")
        for name, inputs in made.items():
            print(f"  {name}.pdf  <-  {len(inputs)} data file(s)")
            for p in inputs:
                manifest.append(dict(figure=name, output=f"{name}.pdf", input=rel(p), sha256=sha256(p)))

    old = []
    mpath = out / "MANIFEST.csv"
    if mpath.exists():
        done = {m["figure"] for m in manifest}
        old = [r for r in csv.DictReader(open(mpath, encoding="utf-8")) if r["figure"] not in done]
    with open(mpath, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["figure", "output", "input", "sha256"])
        w.writeheader()
        w.writerows(sorted(old + manifest, key=lambda r: (r["figure"], r["input"])))

    print(f"\nFigures in {out}")
    if skipped:
        print(f"Skipped (data not there yet): {', '.join(skipped)}")
    if failed:
        print(f"FAILED: {', '.join(failed)}")
    return 1 if failed or (args.strict and skipped) else 0


if __name__ == "__main__":
    raise SystemExit(main())