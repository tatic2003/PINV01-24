#!/usr/bin/env python3
"""
DAG synchronization agent: replicates the BlockDAG of the local server with
its peers, without any coordinator.

Runs next to the server of each edge node. Every --interval seconds, for each
peer, it asks the peer for the nodes the local DAG cannot already have
(POST /api/dag/sync {action: get, peer_tips: <local tips and the peer's
tips seen in the last complete pull>}) and merges them
into the local server (POST /api/dag/sync {action: merge}). Each node only
pulls; with the agent running on every node, every node ends up with every
other node's events.

One CSV row per round in which nodes were offered or an error occurred
(with --log-idle, also the rounds with nothing to transfer):

  epoch_ms        start of the round (local clock)
  local, peer     node ids of the two servers
  offered         nodes sent by the peer
  added           nodes accepted
  existing        nodes the local DAG already had
  failed          nodes rejected, and by reason:
  missing_parents, bad_signature, hash_conflict, broken_link, hash_failed, other
  get_ms          round trip of the request to the peer
  merge_ms        round trip of the merge on the local server
  payload_bytes   size of the peer's answer
  total_nodes     nodes in the local DAG after the merge
  error           network or HTTP error, if any (e.g. during a partition)

Usage (on the Pi 5; on the Orange Pi swap the addresses):
  python3 experiments/dag_sync.py --local http://localhost:5000 \\
      --peer http://<IP of the Orange Pi>:5000 --csv resultados/conv/sync_pi5.csv

Project: PINV01-24 - FIUNA
"""

import argparse
import csv
import logging
import time
from pathlib import Path

import requests

REASONS = ['missing_parents', 'bad_signature', 'hash_conflict', 'broken_link',
           'hash_failed', 'other']
COLUMNS = (['epoch_ms', 'local', 'peer', 'offered', 'added', 'existing', 'failed']
           + REASONS + ['get_ms', 'merge_ms', 'payload_bytes', 'total_nodes', 'error'])

log = logging.getLogger('dag_sync')


class SyncAgent:
    def __init__(self, local, peers, timeout=2.0, session=None):
        self.local = local.rstrip('/')
        self.peers = [p.rstrip('/') for p in peers]
        self.timeout = timeout
        self.http = session or requests.Session()
        self.local_id = None
        # Tips of each peer as of the last complete pull: the local DAG holds
        # them and all their ancestors, so the peer need not send those again
        # even when the local DAG has new events of its own (unknown to the
        # peer, which then cannot use the local tips to narrow the delta).
        self.known_peer_tips = {}

    def status(self, base):
        r = self.http.get(f'{base}/api/dag/status', timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def sync_once(self, peer):
        """One pull from peer into the local server; returns the CSV row."""
        row = {c: '' for c in COLUMNS}
        row['epoch_ms'] = round(time.time() * 1000.0, 1)
        row['peer'] = peer
        try:
            local = self.status(self.local)
            self.local_id = local.get('node_id')
            row['local'] = self.local_id

            t0 = time.perf_counter()
            r = self.http.post(f'{peer}/api/dag/sync',
                               json={'action': 'get',
                                     'peer_tips': sorted(set(local.get('tips', []))
                                                         | set(self.known_peer_tips.get(peer, [])))},
                               timeout=self.timeout)
            row['get_ms'] = round((time.perf_counter() - t0) * 1000.0, 2)
            r.raise_for_status()
            row['payload_bytes'] = len(r.content)
            sync_data = r.json()
            row['peer'] = sync_data.get('source_node', peer)
            nodes = sync_data.get('nodes', [])
            row['offered'] = len(nodes)
            if not nodes:
                self.known_peer_tips[peer] = sync_data.get('tips', [])
                row.update(added=0, existing=0, failed=0, merge_ms=0.0,
                           total_nodes=local.get('node_count', ''))
                for k in REASONS:
                    row[k] = 0
                return row

            t0 = time.perf_counter()
            r = self.http.post(f'{self.local}/api/dag/sync',
                               json={'action': 'merge', 'sync_data': sync_data},
                               timeout=max(self.timeout, 30.0))
            row['merge_ms'] = round((time.perf_counter() - t0) * 1000.0, 2)
            r.raise_for_status()
            result = r.json()
            row.update(added=result.get('added', 0), existing=result.get('existing', 0),
                       failed=result.get('failed', 0), total_nodes=result.get('total_nodes', ''))
            reasons = result.get('failed_reasons', {})
            for k in REASONS:
                row[k] = reasons.get(k, 0)
            if not row['failed']:
                self.known_peer_tips[peer] = sync_data.get('tips', [])
        except (requests.RequestException, ValueError) as exc:
            row['error'] = type(exc).__name__
        return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--local', default='http://localhost:5000', help='URL of the local server')
    ap.add_argument('--peer', action='append', required=True, help='URL of a peer server (repeatable)')
    ap.add_argument('--interval', type=float, default=1.0, help='seconds between rounds')
    ap.add_argument('--timeout', type=float, default=2.0, help='HTTP timeout in seconds')
    ap.add_argument('--csv', required=True)
    ap.add_argument('--log-idle', action='store_true', help='also log rounds with nothing to transfer')
    ap.add_argument('--duration', type=float, default=0, help='seconds; 0 = until Ctrl+C')
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    agent = SyncAgent(args.local, args.peer, timeout=args.timeout)
    path = Path(args.csv)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    start = time.time()
    last_error = {}
    with open(path, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        try:
            while not args.duration or time.time() - start < args.duration:
                t_round = time.monotonic()
                for peer in agent.peers:
                    row = agent.sync_once(peer)
                    if row['error'] != last_error.get(peer):
                        if row['error']:
                            log.warning('%s unreachable (%s)', peer, row['error'])
                        elif peer in last_error:
                            log.info('%s reachable again', peer)
                        last_error[peer] = row['error']
                    if row['error'] or args.log_idle or row['offered']:
                        w.writerow(row)
                        f.flush()
                    if row['offered']:
                        log.info('%s: offered %s, added %s, failed %s',
                                 row['peer'], row['offered'], row['added'], row['failed'])
                time.sleep(max(0.0, args.interval - (time.monotonic() - t_round)))
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
