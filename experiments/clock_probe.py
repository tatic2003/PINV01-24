#!/usr/bin/env python3
"""
clock_probe.py - Offset of each device's clock with respect to the Pi 5
Project PINV01-24 (FIUNA)

Sends the "time_probe" command to each device over MQTT and records the four
timestamps of an NTP-style exchange:

    t1  Pi clock when the probe is published        (this script)
    t2  device clock when the probe arrives          (firmware)
    t3  device clock when the reply is sent          (firmware)
    t4  Pi clock when the reply arrives              (this script)

    offset = ((t2 - t1) + (t3 - t4)) / 2     positive: device clock ahead
    delay  = (t4 - t1) - (t3 - t2)           round trip without device time

Whatever the asymmetry of the two paths, the true offset lies within
offset +/- delay/2, so the probe with the smallest delay gives the tightest
bound. The firmware reports t2 and t3 in whole milliseconds, which adds up
to RESOLUTION_MS to that bound. That bound is what qualifies the differences between device-side and
Pi-side marks in the onboarding stages (experiments/analyze_onboarding.py).

Run on the Pi 5 (broker and reference clock), with the devices powered and
their firmware synchronized by SNTP against the Pi:

    python experiments/clock_probe.py --discover 5 --n 20
    python experiments/clock_probe.py --devices esp32-c3-645ce0 esp32-cam-af0bec

Output: one CSV row per probe (default resultados/clock_probe.csv) and a
summary per device on screen.
"""

import argparse
import csv
import json
import statistics
import threading
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt

#: Error added to the bound by the 1 ms resolution of the device timestamps
#: (offset off by up to 0.5 ms, delay underestimated by up to 1 ms).
RESOLUTION_MS = 1.0

CSV_COLUMNS = ["session", "device_id", "probe_id", "t1_ms", "t2_ms", "t3_ms", "t4_ms",
               "offset_ms", "delay_ms", "synced", "answered"]


def build_client(client_id):
    """Create an MQTT client on paho 1.x or 2.x."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)


def now_ms():
    return time.time() * 1000.0


def probe_estimates(t1, t2, t3, t4):
    """(offset, delay) in ms from the four timestamps of one probe."""
    offset = ((t2 - t1) + (t3 - t4)) / 2.0
    delay = (t4 - t1) - (t3 - t2)
    return offset, delay


def summarize(rows):
    """Per-device summary: best (minimum-delay) probe and medians."""
    by_dev = {}
    for r in rows:
        if r["answered"]:
            by_dev.setdefault(r["device_id"], []).append(r)
    out = {}
    for dev, rs in by_dev.items():
        best = min(rs, key=lambda r: r["delay_ms"])
        out[dev] = {
            "n": len(rs),
            "median_offset_ms": statistics.median(r["offset_ms"] for r in rs),
            "median_delay_ms": statistics.median(r["delay_ms"] for r in rs),
            "best_offset_ms": best["offset_ms"],
            "bound_ms": best["delay_ms"] / 2.0 + RESOLUTION_MS,
            "all_synced": all(r["synced"] for r in rs),
        }
    return out


class Prober:
    def __init__(self, host, port, prefix):
        self.prefix = prefix
        self.client = build_client(f"clock-probe-{uuid.uuid4().hex[:6]}")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.lock = threading.Lock()
        self.pending = {}          # probe_id -> threading.Event
        self.replies = {}          # probe_id -> (payload, t4)
        self.active = set()        # device ids seen active (for --discover)
        self.connected = threading.Event()
        self.client.connect(host, port, keepalive=30)
        self.client.loop_start()
        if not self.connected.wait(10):
            raise SystemExit(f"no connection to the broker at {host}:{port}")

    def _on_connect(self, client, userdata, flags, rc, *extra):
        if rc == 0:
            client.subscribe(f"{self.prefix}/devices/+/response")
            client.subscribe(f"{self.prefix}/devices/+/state")
            self.connected.set()

    def _on_message(self, client, userdata, msg):
        t4 = now_ms()   # first thing: reception time on the Pi clock
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if msg.topic.endswith("/state"):
            dev = msg.topic.split("/")[-2]
            with self.lock:
                if payload.get("state") == "active":
                    self.active.add(dev)
                else:
                    self.active.discard(dev)
            return
        if payload.get("command") != "time_probe":
            return
        pid = payload.get("probe_id", "")
        with self.lock:
            if pid in self.pending:
                self.replies[pid] = (payload, t4)
                self.pending[pid].set()

    def probe(self, device_id, timeout):
        pid = uuid.uuid4().hex[:10]
        ev = threading.Event()
        with self.lock:
            self.pending[pid] = ev
        t1 = now_ms()
        msg = {"command": "time_probe", "params": {"t1": f"{t1:.0f}", "probe_id": pid}}
        self.client.publish(f"{self.prefix}/devices/{device_id}/command", json.dumps(msg), qos=0)
        ok = ev.wait(timeout)
        with self.lock:
            self.pending.pop(pid, None)
            reply = self.replies.pop(pid, None)
        row = {"device_id": device_id, "probe_id": pid, "t1_ms": round(t1, 3), "answered": False,
               "t2_ms": "", "t3_ms": "", "t4_ms": "", "offset_ms": "", "delay_ms": "", "synced": ""}
        if not ok or reply is None:
            return row
        payload, t4 = reply
        # The device echoes t1 rounded to whole ms; use the exact value we kept.
        t2, t3 = float(payload["t2"]), float(payload["t3"])
        offset, delay = probe_estimates(t1, t2, t3, t4)
        row.update(answered=True, t2_ms=t2, t3_ms=t3, t4_ms=round(t4, 3),
                   offset_ms=round(offset, 3), delay_ms=round(delay, 3),
                   synced=bool(payload.get("synced", False)))
        return row

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


def main():
    ap = argparse.ArgumentParser(description="Clock offset of each device with respect to the Pi")
    ap.add_argument("--mqtt-host", default="localhost")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--topic-prefix", default="its")
    ap.add_argument("--devices", nargs="*", default=[], help="device ids to probe")
    ap.add_argument("--discover", type=float, default=0.0,
                    help="seconds to listen for devices whose retained state is 'active'")
    ap.add_argument("--prefix-filter", default="esp32-",
                    help="with --discover, probe only ids starting with this (default esp32-)")
    ap.add_argument("--n", type=int, default=20, help="probes per device")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between rounds")
    ap.add_argument("--timeout", type=float, default=2.0, help="seconds to wait for each reply")
    ap.add_argument("--session", default="")
    ap.add_argument("--out", default="resultados/clock_probe.csv")
    args = ap.parse_args()

    prober = Prober(args.mqtt_host, args.mqtt_port, args.topic_prefix)
    devices = list(args.devices)
    if args.discover > 0:
        time.sleep(args.discover)
        with prober.lock:
            found = sorted(d for d in prober.active if d.startswith(args.prefix_filter))
        devices = sorted(set(devices) | set(found))
    if not devices:
        prober.close()
        raise SystemExit("no devices to probe (use --devices or --discover)")
    print(f"Probing {len(devices)} device(s), {args.n} probes each: {', '.join(devices)}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    is_new = not out.exists() or out.stat().st_size == 0
    rows = []
    with open(out, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if is_new:
            writer.writeheader()
        try:
            for _ in range(args.n):
                for dev in devices:
                    row = prober.probe(dev, args.timeout)
                    row["session"] = args.session
                    writer.writerow(row)
                    fh.flush()
                    rows.append(row)
                time.sleep(args.interval)
        finally:
            prober.close()

    print(f"\n{'device':<26}{'n':>4}{'offset med':>12}{'delay med':>11}{'best offset':>13}{'bound':>9}  synced")
    for dev, s in sorted(summarize(rows).items()):
        print(f"{dev:<26}{s['n']:>4}{s['median_offset_ms']:>12.2f}{s['median_delay_ms']:>11.2f}"
              f"{s['best_offset_ms']:>13.2f}{s['bound_ms']:>9.2f}  {s['all_synced']}")
    unanswered = [r["device_id"] for r in rows if not r["answered"]]
    if unanswered:
        print(f"\nUnanswered probes: {len(unanswered)} ({', '.join(sorted(set(unanswered)))})")
    print(f"\nRows appended to {out}  (values in ms; positive offset = device clock ahead)")


if __name__ == "__main__":
    main()
