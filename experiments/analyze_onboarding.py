#!/usr/bin/env python3
"""
analyze_onboarding.py - Onboarding latency decomposed by stage
Project PINV01-24 (FIUNA)

Input: the onboarding CSV written by mqtt_http_bridge.py (--onboarding-csv),
one row per announcement that carried a "timing" block, and optionally the
CSV of experiments/clock_probe.py.

Stages of an ESP32 onboarding (milliseconds):

  boot_to_setup   millis() at setup(): from the start of the application to
                  setup(); the ROM bootloader before it is not counted
  serial_wait     fixed wait for the serial port in setup()
  link            WiFi association and DHCP
  sntp            clock synchronization (NTP); exists only for the measurement
                  and is excluded from the onboarding totals. The round trip of
                  the NTP sample that set the clock is kept as sync_rtt_ms
  mqtt_connect    TCP + MQTT session, Last Will included
  announce        retained state + building the announcement
  transit         device publish -> MQTT client of the bridge (crosses clocks:
                  qualified by the clock bound of clock_probe.py)
  bridge_queue    wait in the bridge's MQTT client behind earlier messages
  http            bridge -> server and back, minus the server's own work
  srv_classify    PnP-AI classification
  srv_semantic    lightweight validation + RDF graph
  srv_dag         BlockDAG append
  srv_shacl       full SHACL validation (only with --shacl-on-register; else 0)
  srv_other       rest of the server handler

Linux boards (linux_agent.py) report agent_to_mqtt and mqtt_to_publish
instead of the boot stages; their clock is the system clock under chrony.

Serial boards (Arduino Uno through devices/serial_agent.py) have no clock of
their own: every mark is on the host clock. With --serial-agent (the CSV the
agent writes) their rows get, joined on (device_id, epoch_publish_ms):

  usb_to_open          USB port visible -> port opened by the agent
  board_boot           port opened (DTR reset) -> announcement received
  announce_to_publish  announcement received -> MQTT publish

and boot_to_registered = those three + publish_to_registered (from the USB
port becoming visible; USB enumeration before it is not measured).

Transit is only meaningful when the device clock is well synchronized: rows
whose NTP sample had a round trip above --max-sync-rtt (default 30 ms) keep
their totals but get no transit value.

Totals: publish_to_registered (transit .. srv_other) and, for ESP32 boards,
boot_to_registered = all stages except sntp.

Only the first registration after each boot is used (seq == 1) unless
--all-seq is given; reconnections reuse the boot marks.

Usage:
    python experiments/analyze_onboarding.py resultados/onboarding_bridge.csv \
        --probe resultados/clock_probe.csv --out experiments/results/onboarding

Outputs in --out: stages_per_event.csv, summary.csv (n, mean, sd, median, p95
per stage, session and device type), trials.csv (bursts of simultaneous
onboardings) and fig_onboarding_stages.{pdf,png}.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ESP32_STAGES = ["boot_to_setup", "serial_wait", "link", "mqtt_connect", "announce"]
LINUX_STAGES = ["agent_to_mqtt", "mqtt_to_publish"]
SERIAL_STAGES = ["usb_to_open", "board_boot", "announce_to_publish"]
PIPELINE_STAGES = ["transit", "bridge_queue", "http", "srv_classify", "srv_semantic",
                   "srv_shacl", "srv_dag", "srv_other"]

DEVICE_TYPES = [            # (id prefix, label) - first match wins
    ("esp32-wroom32", "ESP32-WROOM-32"),
    ("esp32-c3", "ESP32-C3-Zero"),
    ("esp32-cam", "ESP32-CAM"),
    ("esp32-olimex", "Olimex ESP32-PoE"),
    ("esp32-poecam", "M5Stack PoE CAM-W"),
    ("sbc-opizero", "Orange Pi Zero"),
    ("sbc-pi5", "Raspberry Pi 5"),
    ("mcu-uno", "Arduino Uno R3"),
]


def device_type(device_id, hardware=""):
    for prefix, label in DEVICE_TYPES:
        if str(device_id).startswith(prefix):
            return label
    return hardware or str(device_id)


def num(df, col):
    return pd.to_numeric(df[col], errors="coerce") if col in df else pd.Series(np.nan, index=df.index)


def compute_stages(df):
    """One row per announcement with every stage in ms (NaN where it does not apply)."""
    out = pd.DataFrame(index=df.index)
    out["session"] = df.get("session", "").fillna("")
    out["device_id"] = df["device_id"]
    out["device_type"] = [device_type(d, h) for d, h in zip(df["device_id"], df.get("hardware", ""))]
    out["seq"] = num(df, "seq")
    synced = df["synced"] if "synced" in df else pd.Series("", index=df.index)
    out["synced"] = synced.astype(str).str.lower().isin(["true", "1"])

    setup, serial, link = num(df, "setup_ms"), num(df, "serial_ms"), num(df, "link_ms")
    sync, mqtt_ms, publish = num(df, "sync_ms"), num(df, "mqtt_ms"), num(df, "publish_ms")
    out["boot_to_setup"] = setup
    out["serial_wait"] = serial - setup
    out["link"] = link - serial
    out["sntp"] = sync - link
    out["sync_rtt_ms"] = num(df, "sync_rtt_ms")
    out["mqtt_connect"] = mqtt_ms - sync
    out["announce"] = publish - mqtt_ms

    agent, mqtt_epoch = num(df, "agent_start_epoch_ms"), num(df, "mqtt_epoch_ms")
    pub_epoch = num(df, "epoch_publish_ms")
    out["agent_to_mqtt"] = mqtt_epoch - agent
    out["mqtt_to_publish"] = pub_epoch - mqtt_epoch
    out["uptime_s"] = num(df, "uptime_s")

    arrival, callback = num(df, "arrival_epoch_ms"), num(df, "callback_epoch_ms")
    rtt, srv_total = num(df, "http_rtt_ms"), num(df, "srv_total_ms")
    cls, sem, dag = num(df, "srv_classify_ms"), num(df, "srv_semantic_ms"), num(df, "srv_dag_ms")
    out["transit"] = arrival - pub_epoch
    out["bridge_queue"] = callback - arrival
    out["http"] = rtt - srv_total
    out["srv_classify"] = cls
    out["srv_semantic"] = sem
    out["srv_dag"] = dag
    # Sessions without SHACL on registration (or older CSVs) count it as 0.
    shacl = num(df, "srv_shacl_ms").fillna(0.0)
    out["srv_shacl"] = shacl
    out["srv_other"] = srv_total - (cls + sem + dag + shacl)
    out["arrival_epoch_ms"] = arrival

    out["publish_to_registered"] = out[PIPELINE_STAGES].sum(axis=1, min_count=len(PIPELINE_STAGES))
    esp_boot = out[ESP32_STAGES].sum(axis=1, min_count=len(ESP32_STAGES))
    out["boot_to_registered"] = esp_boot + out["publish_to_registered"]
    out["http_status"] = df.get("http_status", "")
    return out


def add_serial_agent(stages, df, agent_csvs):
    """
    Stages of serial boards from the CSV(s) of devices/serial_agent.py.

    The agent's marks (agent_start / mqtt_epoch in the bridge CSV) mean
    something else for these boards, so agent_to_mqtt and mqtt_to_publish are
    cleared for them whether or not the agent CSV is given.
    """
    serial = stages["device_type"] == "Arduino Uno R3"
    for col in SERIAL_STAGES:
        stages[col] = np.nan
    stages.loc[serial, ["agent_to_mqtt", "mqtt_to_publish", "boot_to_setup"]] = np.nan
    if not agent_csvs or not serial.any():
        return stages
    agent = pd.concat([pd.read_csv(p) for p in agent_csvs], ignore_index=True)
    by_key = {}
    for _, a in agent.iterrows():
        by_key.setdefault((a["device_id"], round(float(a["epoch_publish_ms"]), 3)), a)
    pub = num(df, "epoch_publish_ms").round(3)
    for i in stages.index[serial]:
        a = by_key.get((stages.at[i, "device_id"], pub.at[i]))
        if a is None:
            continue
        stages.at[i, "usb_to_open"] = a["serial_open_epoch_ms"] - a["usb_seen_epoch_ms"]
        stages.at[i, "board_boot"] = a["announce_rx_epoch_ms"] - a["serial_open_epoch_ms"]
        stages.at[i, "announce_to_publish"] = a["epoch_publish_ms"] - a["announce_rx_epoch_ms"]
        stages.at[i, "boot_to_registered"] = (stages.loc[i, SERIAL_STAGES].sum()
                                              + stages.at[i, "publish_to_registered"])
    return stages


def find_trials(stages, gap_s=20.0):
    """Number bursts of onboardings: arrivals more than gap_s apart start a new trial."""
    s = stages.sort_values(["session", "arrival_epoch_ms"]).copy()
    trial, last, last_session = 0, None, None
    ids = []
    for sess, t in zip(s["session"], s["arrival_epoch_ms"]):
        if sess != last_session or last is None or pd.isna(t) or (t - last) > gap_s * 1000.0:
            trial += 1
        ids.append(trial)
        last, last_session = t, sess
    s["trial"] = ids
    return s


def summarize(stages, cols):
    rows = []
    for (session, dtype), g in stages.groupby(["session", "device_type"], sort=True):
        for col in cols:
            v = g[col].dropna()
            if v.empty:
                continue
            rows.append({
                "session": session, "device_type": dtype, "stage": col, "n": len(v),
                "mean_ms": v.mean(), "sd_ms": v.std(ddof=1) if len(v) > 1 else np.nan,
                "median_ms": v.median(), "p95_ms": v.quantile(0.95),
                "min_ms": v.min(), "max_ms": v.max(),
            })
    return pd.DataFrame(rows)


#: Same resolution term as experiments/clock_probe.py (1 ms device timestamps).
RESOLUTION_MS = 1.0


def clock_bounds(probe_csv):
    """Per device: clock bound (minimum delay / 2 + resolution) and median offset."""
    p = pd.read_csv(probe_csv)
    p = p[p["answered"].astype(str).str.lower().isin(["true", "1"])]
    rows = []
    for dev, g in p.groupby("device_id"):
        delay = pd.to_numeric(g["delay_ms"], errors="coerce")
        offset = pd.to_numeric(g["offset_ms"], errors="coerce")
        best = delay.idxmin()
        rows.append({"device_id": dev, "device_type": device_type(dev), "probes": len(g),
                     "median_offset_ms": offset.median(), "best_offset_ms": offset[best],
                     "bound_ms": delay[best] / 2.0 + RESOLUTION_MS})
    return pd.DataFrame(rows)


def plot(summary, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    stages = ESP32_STAGES + LINUX_STAGES + SERIAL_STAGES + PIPELINE_STAGES
    means = summary.pivot_table(index=["session", "device_type"], columns="stage",
                                values="mean_ms", aggfunc="first")
    means = means.reindex(columns=[s for s in stages if s in means.columns]).fillna(0.0)
    if means.empty:
        return
    labels = [f"{t}\n({s})" if s else t for s, t in means.index]
    fig, ax = plt.subplots(figsize=(9, 0.6 * len(means) + 1.8))
    left = np.zeros(len(means))
    cmap = plt.get_cmap("tab20")
    for i, st in enumerate(means.columns):
        vals = means[st].to_numpy() / 1000.0
        ax.barh(range(len(means)), vals, left=left, color=cmap(i % 20), label=st)
        left += vals
    ax.set_yticks(range(len(means)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Mean time (s), first registration after boot; SNTP step excluded")
    ax.legend(fontsize=7, ncol=3, loc="lower right")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(out_dir / f"fig_onboarding_stages.{ext}", dpi=200)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description="Onboarding latency decomposed by stage")
    ap.add_argument("onboarding_csv", nargs="+", help="CSV(s) written by the bridge (--onboarding-csv)")
    ap.add_argument("--probe", help="CSV of experiments/clock_probe.py")
    ap.add_argument("--out", default="experiments/results/onboarding")
    ap.add_argument("--all-seq", action="store_true", help="keep re-registrations (seq > 1)")
    ap.add_argument("--serial-agent", nargs="*", default=[],
                    help="CSV(s) written by devices/serial_agent.py (--csv), for serial boards")
    ap.add_argument("--max-sync-rtt", type=float, default=30.0,
                    help="drop the transit of rows whose NTP round trip exceeds this (ms)")
    ap.add_argument("--trial-gap", type=float, default=20.0,
                    help="seconds without arrivals that separate two trials")
    args = ap.parse_args()

    df = pd.concat([pd.read_csv(p, dtype=str) for p in args.onboarding_csv], ignore_index=True)
    stages = compute_stages(df)
    stages = add_serial_agent(stages, df, args.serial_agent)
    loose_clock = stages["sync_rtt_ms"] > args.max_sync_rtt
    stages.loc[loose_clock, "transit"] = np.nan
    ok = pd.to_numeric(stages["http_status"], errors="coerce") == 200
    dropped_http = int((~ok).sum())
    stages = stages[ok]
    if not args.all_seq:
        stages = stages[(stages["seq"] == 1) | stages["seq"].isna()]
    # ESP32 rows only: serial and Linux boards have no NTP step of their own.
    unsynced = stages[~stages["synced"] & stages["boot_to_setup"].notna()]
    stages = find_trials(stages, args.trial_gap)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stages.to_csv(out / "stages_per_event.csv", index=False)

    cols = (ESP32_STAGES + ["sntp", "sync_rtt_ms"] + LINUX_STAGES + SERIAL_STAGES + PIPELINE_STAGES
            + ["publish_to_registered", "boot_to_registered"])
    summary = summarize(stages, cols)
    if args.probe:
        bounds = clock_bounds(args.probe)
        bounds.to_csv(out / "clock_bounds.csv", index=False)
        worst = bounds.groupby("device_type")["bound_ms"].max()
        summary["clock_bound_ms"] = summary["device_type"].map(worst)
    summary.to_csv(out / "summary.csv", index=False)

    trials = (stages.groupby(["session", "trial"])
              .agg(devices=("device_id", "nunique"),
                   first_arrival=("arrival_epoch_ms", "min"),
                   last_arrival=("arrival_epoch_ms", "max"),
                   max_queue_ms=("bridge_queue", "max"),
                   max_publish_to_registered_ms=("publish_to_registered", "max"))
              .reset_index())
    trials["arrival_spread_ms"] = trials["last_arrival"] - trials["first_arrival"]
    trials.to_csv(out / "trials.csv", index=False)
    plot(summary[summary["stage"].isin(ESP32_STAGES + LINUX_STAGES + SERIAL_STAGES
                                        + PIPELINE_STAGES)], out)

    print(f"{len(stages)} onboardings analysed ({dropped_http} dropped for HTTP status != 200)")
    if int(loose_clock.sum()):
        print(f"{int(loose_clock.sum())} transit value(s) dropped: NTP round trip > {args.max_sync_rtt} ms")
    if len(unsynced):
        print(f"WARNING: {len(unsynced)} ESP32 onboardings with an unsynchronized clock "
              f"(their transit stage is not meaningful)")
    keep = summary[summary["stage"].isin(["boot_to_registered", "publish_to_registered"])]
    if not keep.empty:
        print(keep[["session", "device_type", "stage", "n", "mean_ms", "sd_ms", "median_ms",
                    "p95_ms"]].to_string(index=False, float_format=lambda x: f"{x:.1f}"))
    print(f"Results in {out}")


if __name__ == "__main__":
    main()
