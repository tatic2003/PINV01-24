#!/usr/bin/env python3
"""
LWT loss-detection experiment for the GNSS device agent.

Each run: start the agent, let it publish for a while, FREEZE it (SIGSTOP, like
a hung device: the TCP link stays open but no packets arrive), and measure how
long the broker takes to publish the Last Will. Then kill the agent.

Everything is timed with one clock (this host), so no clock synchronisation is
involved. Detection time is measured from the last packet this script saw from
the agent to the arrival of the Last Will. The expected value is
1.5 x keepalive (MQTT specification).

Run on the machine that has the broker (the Pi 5), from the repo root:
  python experiments/run_gnss_lwt.py --repeats 10

Use --simulate to run without a GPS.

Output: resultados/gnss_lwt_<fecha>.csv and a summary.

Project: PINV01-24 - FIUNA
"""

import argparse
import csv
import json
import os
import signal
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

REPO = Path(__file__).resolve().parent.parent


def build_client(client_id):
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)


class Watcher:
    """Subscribes to the device topics and timestamps what arrives."""

    def __init__(self, host, port, prefix, device_id):
        self.base = f"{prefix}/devices/{device_id}"
        self.lock = threading.Lock()
        self.last_packet = None
        self.data_count = 0
        self.lwt_time = None
        self.armed = False        # only a Last Will after the freeze counts
        self.client = build_client("gnss-lwt-watcher")
        self.client.on_connect = lambda c, u, f, rc: c.subscribe(f"{self.base}/#", qos=1)
        self.client.on_message = self._on_message
        self.client.connect(host, port, keepalive=60)
        self.client.loop_start()

    def _on_message(self, client, userdata, msg):
        now = time.time()
        topic = msg.topic.rsplit("/", 1)[-1]
        with self.lock:
            if topic in ("data", "heartbeat"):
                self.last_packet = now
                if topic == "data":
                    self.data_count += 1
            elif topic == "state":
                try:
                    body = json.loads(msg.payload)
                except ValueError:
                    return
                if (self.armed and body.get("state") == "inactive"
                        and body.get("reason") == "lwt" and self.lwt_time is None):
                    self.lwt_time = now

    def reset(self):
        with self.lock:
            self.last_packet = None
            self.data_count = 0
            self.lwt_time = None
            self.armed = False

    def snapshot(self):
        with self.lock:
            return self.last_packet, self.data_count, self.lwt_time

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()


def server_state(server, device_id):
    try:
        with urllib.request.urlopen(f"{server}/api/devices/{device_id}", timeout=5) as r:
            return json.load(r).get("state")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--mqtt-host", default="localhost")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--topic-prefix", default="its")
    ap.add_argument("--device-id", default="gnss-lwt-test")
    ap.add_argument("--keepalive", type=int, default=15)
    ap.add_argument("--warmup-data", type=int, default=10, help="data messages before freezing")
    ap.add_argument("--server", default="", help="e.g. http://localhost:5000, to check the final state")
    ap.add_argument("--fault", choices=["freeze", "kill"], default="freeze",
                    help="freeze = SIGSTOP (silent failure, detected by keepalive, the real test); "
                         "kill = SIGKILL (the OS closes the socket, detected at once)")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--out-dir", default="resultados")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"gnss_lwt_{datetime.now():%Y-%m-%d_%H%M%S}.csv"
    expected = 1.5 * args.keepalive

    watcher = Watcher(args.mqtt_host, args.mqtt_port, args.topic_prefix, args.device_id)
    rows = []
    for run in range(1, args.repeats + 1):
        watcher.reset()
        cmd = [sys.executable, str(REPO / "devices" / "gnss_agent.py"),
               "--device-id", args.device_id, "--mqtt-host", args.mqtt_host,
               "--mqtt-port", str(args.mqtt_port), "--topic-prefix", args.topic_prefix,
               "--keepalive", str(args.keepalive), "--wait-fix", "60", "--heartbeat", "5"]
        if args.simulate:
            cmd.append("--simulate")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 90
            while time.time() < deadline and watcher.snapshot()[1] < args.warmup_data:
                time.sleep(0.2)
            if watcher.snapshot()[1] < args.warmup_data:
                print(f"run {run}: agent never reached {args.warmup_data} data messages, skipped")
                continue

            with watcher.lock:
                watcher.armed = True
                last_packet = watcher.last_packet
            os.kill(proc.pid, signal.SIGSTOP if args.fault == "freeze" else signal.SIGKILL)
            t_stop = time.time()

            lwt = None
            while time.time() < t_stop + 4 * expected:
                lwt = watcher.snapshot()[2]
                if lwt:
                    break
                time.sleep(0.05)
            if lwt is None:
                print(f"run {run}: no Last Will within {4 * expected:.0f} s")
                continue

            state = server_state(args.server, args.device_id) if args.server else ""
            rows.append({"run": run, "keepalive_s": args.keepalive,
                         "fault": args.fault, "expected_s": expected,
                         "since_last_packet_s": round(lwt - last_packet, 3),
                         "since_freeze_s": round(lwt - t_stop, 3),
                         "server_state": state})
            print(f"run {run}: LWT {rows[-1]['since_last_packet_s']:.2f} s after the last packet"
                  f" (expected {expected:.1f} s)")
        finally:
            try:
                os.kill(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        time.sleep(3)
    watcher.stop()

    if not rows:
        print("No valid runs.")
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    vals = [r["since_last_packet_s"] for r in rows]
    print(f"\nCSV: {path}")
    print(f"Runs: {len(vals)}   fault: {args.fault}   expected 1.5 x keepalive = {expected:.1f} s"
          f" (only for freeze)")
    print(f"Detection after last packet: mean {statistics.mean(vals):.2f} s, "
          f"median {statistics.median(vals):.2f} s, min {min(vals):.2f} s, max {max(vals):.2f} s")
    if len(vals) > 1:
        print(f"Std dev {statistics.stdev(vals):.2f} s")


if __name__ == "__main__":
    main()
