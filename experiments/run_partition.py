#!/usr/bin/env python3
"""
Convergence of two BlockDAG replicas after a network partition.

Runs on one edge node (the Pi 5) while both servers and both synchronization
agents (experiments/dag_sync.py) are running. For each value of M and each
repetition:

  1. waits until both replicas hold the same DAG (equal digest and tips,
     GET /api/dag/status);
  2. partitions: blocks with iptables the HTTP port of the servers between
     this node and the peer, in both directions (MQTT is not touched, so
     each node keeps serving its own devices);
  3. lets each node register M emulated devices: two load generators
     (experiments/load_generator.py, burst of M), one per broker, at the same
     time; then waits until the local DAG stops growing and --settle-s more
     for the peer;
  4. restores the link (removes the iptables rules) and records the instant;
  5. polls both servers every --poll-s until their digests and tips are
     equal: the convergence time is measured from step 4, on this node's
     clock only.

One CSV row per repetition:
  m, repeat, t_block_ms, t_unblock_ms, t_converged_ms, convergence_ms,
  converged (False = --timeout-s reached), polls,
  nodes_local, nodes_peer   DAG nodes each node created during the partition
                            (from the creation time in the node ids)
  late_nodes                nodes created after the link was restored; > 0
                            means the settle time was too short
  total_nodes, gen_local_rc, gen_peer_rc (exit codes of the generators)

Nodes rejected during the merges, by reason, are in the CSVs of the
synchronization agents (analysed by experiments/analyze_convergence.py).

The iptables rules are always removed on exit, also on Ctrl+C or error. They
need root: the script calls 'sudo -n iptables', so run it as the normal user
(which on Raspberry Pi OS has passwordless sudo); running the whole script
with sudo would use root's Python, which may lack the project's packages.

Usage (on the Pi 5):
  python3 experiments/run_partition.py --peer-ip <IP of the Orange Pi> \\
      --m 10 50 200 --repeat 10 --csv resultados/conv/runs.csv \\
      --gen-csv resultados/conv/gen_conv.csv

Project: PINV01-24 - FIUNA
"""

import argparse
import csv
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
COLUMNS = ['m', 'repeat', 't_block_ms', 't_unblock_ms', 't_converged_ms', 'convergence_ms',
           'converged', 'polls', 'nodes_local', 'nodes_peer', 'late_nodes', 'total_nodes',
           'gen_local_rc', 'gen_peer_rc']

log = logging.getLogger('run_partition')


def now_ms():
    return time.time() * 1000.0


# ---------------------------------------------------------------- partition

def is_root():
    """True when running as root (os.geteuid does not exist on Windows)."""
    geteuid = getattr(os, 'geteuid', None)
    return geteuid is not None and geteuid() == 0


class IptablesPartition:
    """Blocks TCP to/from the peer on the servers' HTTP port."""

    def __init__(self, peer_ip, port, mode='reject', runner=subprocess.run):
        target = (['REJECT', '--reject-with', 'tcp-reset'] if mode == 'reject' else ['DROP'])
        self.rules = [
            ['INPUT', '-s', peer_ip, '-p', 'tcp', '--dport', str(port), '-j', *target],
            ['OUTPUT', '-d', peer_ip, '-p', 'tcp', '--dport', str(port), '-j', *target],
        ]
        self.runner = runner
        self.active = False

    def _iptables(self, op, rule, check=True):
        cmd = ['iptables', op, *rule]
        if not is_root():
            cmd = ['sudo', '-n', *cmd]
        return self.runner(cmd, check=check, capture_output=True)

    def block(self):
        for rule in self.rules:
            self._iptables('-I', rule)
        self.active = True

    def unblock(self):
        # Remove every copy of the rules (also leftovers of an interrupted run).
        for rule in self.rules:
            while self._iptables('-C', rule, check=False).returncode == 0:
                self._iptables('-D', rule)
        self.active = False


# ---------------------------------------------------------------- DAG state

def status(http, base, ids=False, timeout=2.0):
    r = http.get(f'{base}/api/dag/status', params={'ids': 1} if ids else None, timeout=timeout)
    r.raise_for_status()
    return r.json()


def same_dag(a, b):
    return a['digest'] == b['digest'] and sorted(a['tips']) == sorted(b['tips'])


def wait_converged(http, local, peer, poll_s, timeout_s):
    """Poll both servers until they hold the same DAG.

    Returns (converged, t_ms, polls); t_ms is the instant of the first poll
    that found them equal (this node's clock).
    """
    deadline = time.time() + timeout_s
    polls = 0
    while time.time() < deadline:
        polls += 1
        try:
            a, b = status(http, local), status(http, peer)
            if same_dag(a, b):
                return True, now_ms(), polls
        except (requests.RequestException, ValueError, KeyError):
            pass
        time.sleep(poll_s)
    return False, now_ms(), polls


def wait_stable(http, base, quiet_s=2.0, poll_s=0.25, timeout_s=300.0):
    """Wait until the node count of a server has not changed for quiet_s."""
    last, since = None, time.time()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        count = status(http, base)['node_count']
        if count != last:
            last, since = count, time.time()
        elif time.time() - since >= quiet_s:
            return count
        time.sleep(poll_s)
    return last


def node_epoch_s(node_id):
    """Creation time (UTC, 1 s resolution) encoded in a node id, or None."""
    parts = node_id.split(':')
    if len(parts) < 3:
        return None          # genesis
    try:
        dt = datetime.strptime(parts[-2], '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.timestamp()


def count_created(node_ids, creator, t_from_ms, t_to_ms):
    """Nodes of creator created in [t_from, t_to] (second resolution)."""
    lo, hi = int(t_from_ms // 1000), int(t_to_ms // 1000)
    n = 0
    for nid in node_ids:
        if not nid.startswith(f'{creator}:'):
            continue
        t = node_epoch_s(nid)
        if t is not None and lo <= t <= hi:
            n += 1
    return n


def count_after(node_ids, t_ms):
    """Nodes (of any creator) created in a later second than t."""
    lim = int(t_ms // 1000)
    return sum(1 for nid in node_ids if (t := node_epoch_s(nid)) is not None and t > lim)


# ---------------------------------------------------------------- generators

def generator_cmd(host, label, m, gen_csv, port=1883):
    return [sys.executable, str(ROOT / 'experiments' / 'load_generator.py'),
            '--host', host, '--port', str(port), '--label', label, '--mode', 'burst',
            '--n', str(m), '--hold', '1', '--settle', '0', '--csv', gen_csv]


def run_generators(cmds):
    procs = [subprocess.Popen(c, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for c in cmds]
    codes = []
    for p in procs:
        _, err = p.communicate()
        if p.returncode:
            log.warning('generator exited with %s: %s', p.returncode, err.decode(errors='replace')[-300:])
        codes.append(p.returncode)
    return codes


# ---------------------------------------------------------------- experiment

def run(args, partition, http, writer, flush=lambda: None):
    local_id = status(http, args.local)['node_id']
    peer_id = status(http, args.peer_url)['node_id']
    if local_id == peer_id:
        raise SystemExit(f'Both servers have node id {local_id!r}: start them with different --node-id')
    log.info('local %s, peer %s', local_id, peer_id)

    for m in args.m:
        for k in range(1, args.repeat + 1):
            ok, _, _ = wait_converged(http, args.local, args.peer_url, args.poll_s, args.timeout_s)
            if not ok:
                raise SystemExit('The replicas did not converge before the partition; '
                                 'are both dag_sync.py agents running?')
            row = dict(m=m, repeat=k)
            row['t_block_ms'] = round(now_ms(), 1)
            partition.block()
            try:
                cmds = [generator_cmd(args.local_broker, f'conv_{local_id}_M{m}_r{k}', m, args.gen_csv,
                                      args.mqtt_port),
                        generator_cmd(args.peer_broker, f'conv_{peer_id}_M{m}_r{k}', m, args.gen_csv,
                                      args.mqtt_port)]
                row['gen_local_rc'], row['gen_peer_rc'] = run_generators(cmds)
                t_gen = time.time()
                wait_stable(http, args.local)
                time.sleep(max(0.0, args.settle_s - (time.time() - t_gen)))
            finally:
                row['t_unblock_ms'] = round(now_ms(), 1)
                partition.unblock()

            ok, t_conv, polls = wait_converged(http, args.local, args.peer_url, args.poll_s, args.timeout_s)
            row.update(t_converged_ms=round(t_conv, 1), converged=ok, polls=polls,
                       convergence_ms=round(t_conv - row['t_unblock_ms'], 1))
            ids = status(http, args.local, ids=True)['node_ids']
            row['nodes_local'] = count_created(ids, local_id, row['t_block_ms'], row['t_unblock_ms'])
            row['nodes_peer'] = count_created(ids, peer_id, row['t_block_ms'], row['t_unblock_ms'])
            row['late_nodes'] = count_after(ids, row['t_unblock_ms'])
            row['total_nodes'] = len(ids)
            writer.writerow(row)
            flush()
            log.info('M=%s r%s: %s ms (converged %s), nodes %s + %s, late %s', m, k,
                     row['convergence_ms'], ok, row['nodes_local'], row['nodes_peer'], row['late_nodes'])
            time.sleep(args.pause_s)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--peer-ip', required=True, help='IP address of the peer node')
    ap.add_argument('--port', type=int, default=5000, help='HTTP port of both servers')
    ap.add_argument('--local', default=None, help='URL of the local server (default http://localhost:PORT)')
    ap.add_argument('--peer-url', default=None, help='URL of the peer server (default http://PEER_IP:PORT)')
    ap.add_argument('--local-broker', default='localhost')
    ap.add_argument('--peer-broker', default=None, help='default: PEER_IP')
    ap.add_argument('--mqtt-port', type=int, default=1883, help='MQTT port of both brokers')
    ap.add_argument('--m', type=int, nargs='+', default=[10, 50, 200],
                    help='devices each node registers during a partition')
    ap.add_argument('--repeat', type=int, default=10)
    ap.add_argument('--settle-s', type=float, default=10.0,
                    help='minimum wait after the generators finish, for the peer to finish registering')
    ap.add_argument('--poll-s', type=float, default=0.05)
    ap.add_argument('--timeout-s', type=float, default=120.0)
    ap.add_argument('--pause-s', type=float, default=5.0, help='pause between repetitions')
    ap.add_argument('--block', choices=['reject', 'drop'], default='reject',
                    help='reject: connections fail at once (TCP reset); drop: packets are lost')
    ap.add_argument('--csv', required=True)
    ap.add_argument('--gen-csv', required=True)
    args = ap.parse_args(argv)
    args.local = args.local or f'http://localhost:{args.port}'
    args.peer_url = args.peer_url or f'http://{args.peer_ip}:{args.port}'
    args.peer_broker = args.peer_broker or args.peer_ip
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    partition = IptablesPartition(args.peer_ip, args.port, args.block)
    partition.unblock()      # leftovers of an interrupted run
    http = requests.Session()
    path = Path(args.csv)
    path.parent.mkdir(parents=True, exist_ok=True)
    Path(args.gen_csv).parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with open(path, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        try:
            run(args, partition, http, w, f.flush)
        except KeyboardInterrupt:
            pass
        finally:
            partition.unblock()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
