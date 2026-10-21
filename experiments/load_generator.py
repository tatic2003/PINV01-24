#!/usr/bin/env python3
"""
Load generator: N emulated devices that announce themselves over MQTT.

Each emulated device uses the same MQTT contract as the firmware and the
Linux agent (its/devices/register, then its/devices/<id>/state "active"),
with a "timing" block so that mqtt_http_bridge.py writes one onboarding row
per announcement. The block also carries the session label, so a single
bridge run can record many load levels; each level is a separate session.

The generator must run on a different machine from the node under test (for
example on the Orange Pi when the server runs on the Raspberry Pi 5), so that
it does not take CPU from it. All clients are driven from one thread (paho's
manual loop), which keeps the generator light on a small board.

Modes
-----
burst   For each N in --n: connect N clients (one per device), and when all
        are connected publish the N announcements back to back. Measures how
        the node absorbs a fleet that comes online at once. The connection
        phase is excluded from the burst on purpose.
ramp    For each rate in --rates (announcements per second): a pool of
        --pool connections publishes announcements of new devices at that
        rate for --step-s seconds. Measures the sustainable registration rate.

Announcements
-------------
catalog  resources of real boards from the calibrated catalogue (default)
testbed  the four boards of the concurrent physical session (3 ESP32-C3 and
         an ESP32-WROOM-32), from datasets/real/announcements_real.jsonl, to
         validate the emulation against the physical run
Device identifiers are emu-<session>-<k>, unique per announcement, so the
server registers a new device every time (never the "already registered"
path). State messages are not retained, so the broker does not accumulate
thousands of retained topics.

Output: --csv, one row per announcement (connection and publication marks,
PUBACK, errors), used by experiments/analyze_load.py.

Usage (on the machine that generates the load):
  python3 experiments/load_generator.py --host 192.168.0.21 --label pi5 \\
      --mode burst --n 1 5 10 25 50 100 200 --csv resultados/load/gen_pi5_burst.csv
  python3 experiments/load_generator.py --host 192.168.0.21 --label pi5 \\
      --mode ramp --rates 1 2 5 10 20 50 100 --step-s 20 --csv resultados/load/gen_pi5_ramp.csv
  python3 experiments/load_generator.py --host 192.168.0.21 --label pi5val \\
      --mode burst --n 4 --repeat 10 --announcements testbed --csv resultados/load/gen_pi5_val.csv

Project: PINV01-24 - FIUNA
"""

import argparse
import copy
import csv
import json
import logging
import random
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

log = logging.getLogger("load_generator")

CSV_COLUMNS = [
    "session", "mode", "level", "repeat", "device_id", "client",
    "connect_start_ms", "connack_ms", "publish_ms", "puback_ms", "error",
]

#: Devices of the concurrent physical session, as captured in datasets/real.
TESTBED_DEVICES = ("esp32-c3-645ce0", "esp32-c3-65beac", "esp32-c3-38a960", "esp32-wroom32-267a68")


def epoch_ms():
    return time.time() * 1000.0


def build_client(client_id):
    """MQTT client on paho 1.x or 2.x (callbacks below accept both)."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------

class AnnouncementSource:
    """Yields announcement payloads (without id and timing)."""

    def __init__(self, kind, seed=42, real_path=None):
        self.kind = kind
        self.rng = random.Random(seed)
        if kind == 'catalog':
            from pnp_ai.dataset_generator.catalog import CatalogDatasetGenerator
            self.gen = CatalogDatasetGenerator(seed=seed, calibrated=True)
        elif kind == 'testbed':
            self.pool = self._load_testbed(real_path or ROOT / 'datasets' / 'real' / 'announcements_real.jsonl')
            self.cycle = 0
        else:
            raise ValueError(f"unknown announcement source {kind!r}")

    @staticmethod
    def _load_testbed(path):
        """The captured announcement of each of the four boards of the
        concurrent physical session (three ESP32-C3 and one WROOM)."""
        by_device = {}
        with open(path, encoding='utf-8') as f:
            for line in f:
                rec = json.loads(line)
                if rec.get('device_id') in TESTBED_DEVICES and rec.get('topic', '').endswith('/register'):
                    by_device.setdefault(rec['device_id'], rec['payload'])
        missing = [d for d in TESTBED_DEVICES if d not in by_device]
        if missing:
            raise SystemExit(f"no captured announcement for {missing} in {path}")
        return [by_device[d] for d in TESTBED_DEVICES]

    def next(self):
        if self.kind == 'catalog':
            d = self.gen.generate_device()
            # Only what a device announces: no label, no generator bookkeeping.
            drop = ('device_type', 'board_id', 'catalog_version', 'device_id', 'memory_source',
                    'role', 'its_context', 'registered_at', 'last_seen', 'state')
            payload = {k: v for k, v in d.items() if k not in drop}
            payload['hardware'] = payload.pop('hardware_type', '')
            payload['ip'] = payload.pop('ip_address', '')
            return payload
        payload = copy.deepcopy(self.pool[self.cycle % len(self.pool)])
        self.cycle += 1
        return payload


def make_announcement(payload, device_id, session, agent_start_ms, connack_ms):
    msg = dict(payload)
    msg.pop('device_id', None)
    msg['id'] = device_id
    msg['timing'] = {
        'session': session,
        'seq': 1,
        'agent_start_epoch_ms': round(agent_start_ms, 3),
        'mqtt_epoch_ms': round(connack_ms, 3) if connack_ms else '',
        'epoch_publish_ms': round(epoch_ms(), 3),   # last: as close to the publish as possible
    }
    return msg


# ---------------------------------------------------------------------------
# Clients driven from one thread
# ---------------------------------------------------------------------------

class EmuClient:
    def __init__(self, name, host, port, keepalive, prefix):
        self.name = name
        self.prefix = prefix
        self.client = build_client(name)
        self.connected = False
        self.connack_ms = None
        self.connect_start_ms = None
        self.error = ''
        self.pending = {}          # mid -> row dict waiting for PUBACK
        self.client.on_connect = self._on_connect
        self.client.on_publish = self._on_publish
        self.host, self.port, self.keepalive = host, port, keepalive

    def _on_connect(self, c, userdata, flags, rc, *extra):
        if rc == 0:
            self.connected = True
            self.connack_ms = epoch_ms()
        else:
            self.error = f'connack rc={rc}'

    def _on_publish(self, c, userdata, mid, *extra):
        row = self.pending.pop(mid, None)
        if row is not None:
            row['puback_ms'] = round(epoch_ms(), 3)

    def connect(self):
        self.connect_start_ms = epoch_ms()
        try:
            self.client.connect(self.host, self.port, keepalive=self.keepalive)
        except OSError as exc:
            self.error = f'connect: {exc}'

    def publish_announcement(self, msg, row):
        topic_reg = f"{self.prefix}/devices/register"
        topic_state = f"{self.prefix}/devices/{msg['id']}/state"
        row['publish_ms'] = msg['timing']['epoch_publish_ms']
        info = self.client.publish(topic_reg, json.dumps(msg), qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            row['error'] = f'publish rc={info.rc}'
        else:
            self.pending[info.mid] = row
        self.client.publish(topic_state, json.dumps({'device_id': msg['id'], 'state': 'active'}), qos=1)

    def loop(self):
        if not self.error:
            self.client.loop(timeout=0.0)

    def close(self):
        try:
            self.client.disconnect()
            self.client.loop(timeout=0.0)
        except Exception:
            pass


def pump(clients, until, deadline):
    """Drive every client's network loop until until() is true or the deadline passes."""
    while time.time() < deadline:
        for c in clients:
            c.loop()
        if until():
            return True
        time.sleep(0.0005)
    return until()


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def run_burst(args, source, writer):
    for n in args.n:
        for rep in range(1, args.repeat + 1):
            session = f"load_{args.label}_burst_N{n}" + (f"_r{rep}" if args.repeat > 1 else '')
            clients = [EmuClient(f"emu-{args.label}-{n}-{rep}-{i}", args.host, args.port,
                                 args.keepalive, args.topic_prefix) for i in range(n)]
            agent_start = epoch_ms()
            for c in clients:
                c.connect()
            ok = pump(clients, lambda: all(c.connected or c.error for c in clients),
                      time.time() + args.timeout)
            if not ok:
                log.warning("%s: not every client connected in %ss", session, args.timeout)
            rows = []
            # The burst: every connected client publishes now, back to back.
            for i, c in enumerate(clients):
                row = dict(session=session, mode='burst', level=n, repeat=rep,
                           device_id=f"emu-{session}-{i}", client=c.name,
                           connect_start_ms=round(c.connect_start_ms, 3),
                           connack_ms=round(c.connack_ms, 3) if c.connack_ms else '',
                           publish_ms='', puback_ms='', error=c.error)
                rows.append(row)
                if c.connected and not c.error:
                    msg = make_announcement(source.next(), row['device_id'], session,
                                            agent_start, c.connack_ms)
                    c.publish_announcement(msg, row)
            pump(clients, lambda: all(not c.pending for c in clients), time.time() + args.timeout)
            for row in rows:
                if row['publish_ms'] and not row['puback_ms'] and not row['error']:
                    row['error'] = 'no puback'
            pump(clients, lambda: False, time.time() + args.hold)   # keep the connections up
            for c in clients:
                c.close()
            for row in rows:
                writer.writerow(row)
            done = sum(1 for r in rows if r['puback_ms'])
            log.info("%s: %d/%d announcements acknowledged by the broker", session, done, n)
            time.sleep(args.settle)


def run_ramp(args, source, writer):
    pool = [EmuClient(f"emu-{args.label}-ramp-{i}", args.host, args.port,
                      args.keepalive, args.topic_prefix) for i in range(args.pool)]
    start = epoch_ms()
    for c in pool:
        c.connect()
    if not pump(pool, lambda: all(c.connected or c.error for c in pool), time.time() + args.timeout):
        raise SystemExit("the connection pool did not connect")
    live = [c for c in pool if c.connected and not c.error]
    k = 0
    for rate in args.rates:
        session = f"load_{args.label}_ramp_R{rate:g}"
        interval = 1.0 / rate
        rows = []
        t0 = time.time()
        next_t = t0
        i = 0
        while time.time() - t0 < args.step_s:
            now = time.time()
            if now >= next_t:
                c = live[k % len(live)]
                k += 1
                row = dict(session=session, mode='ramp', level=rate, repeat=1,
                           device_id=f"emu-{session}-{i}", client=c.name,
                           connect_start_ms=round(c.connect_start_ms, 3),
                           connack_ms=round(c.connack_ms, 3), publish_ms='', puback_ms='', error='')
                msg = make_announcement(source.next(), row['device_id'], session, start, c.connack_ms)
                c.publish_announcement(msg, row)
                rows.append(row)
                i += 1
                next_t += interval
            for c in live:
                c.loop()
            time.sleep(0.0002)
        pump(live, lambda: all(not c.pending for c in live), time.time() + args.timeout)
        for row in rows:
            if not row['puback_ms'] and not row['error']:
                row['error'] = 'no puback'
            writer.writerow(row)
        log.info("%s: %d announcements in %ss (offered %.1f/s)", session, len(rows), args.step_s, rate)
        # Keep the pool's network loops running during the pause: a plain sleep would let the
        # connections miss their keepalive and the broker would drop them.
        pump(live, lambda: False, time.time() + args.settle)
    for c in pool:
        c.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', default='localhost', help='MQTT broker of the node under test')
    ap.add_argument('--port', type=int, default=1883)
    ap.add_argument('--topic-prefix', default='its')
    ap.add_argument('--label', required=True, help='node label used in the session names (e.g. pi5, opi)')
    ap.add_argument('--mode', choices=['burst', 'ramp'], required=True)
    ap.add_argument('--n', type=int, nargs='+', default=[1, 5, 10, 25, 50, 100, 200])
    ap.add_argument('--repeat', type=int, default=1, help='bursts per N')
    ap.add_argument('--rates', type=float, nargs='+', default=[1, 2, 5, 10, 20, 50, 100])
    ap.add_argument('--step-s', type=float, default=20.0, help='seconds per ramp step')
    ap.add_argument('--pool', type=int, default=20, help='connections used by the ramp')
    ap.add_argument('--announcements', choices=['catalog', 'testbed'], default='catalog')
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--keepalive', type=int, default=15)
    ap.add_argument('--timeout', type=float, default=60.0, help='max seconds to connect / get PUBACKs')
    ap.add_argument('--hold', type=float, default=5.0, help='seconds the burst connections stay up')
    ap.add_argument('--settle', type=float, default=10.0, help='pause between load levels (the ramp keeps its connections attended)')
    ap.add_argument('--csv', required=True)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    source = AnnouncementSource(args.announcements, seed=args.seed)
    path = Path(args.csv)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with open(path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if new:
            writer.writeheader()
        if args.mode == 'burst':
            run_burst(args, source, writer)
        else:
            run_ramp(args, source, writer)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())