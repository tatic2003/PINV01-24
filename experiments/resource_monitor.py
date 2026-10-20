#!/usr/bin/env python3
"""
CPU and memory of the node under test while a load experiment runs.

Samples, every --interval seconds, the whole system (CPU %, memory used,
SoC temperature and current frequency of CPU 0 when the kernel exposes them)
and each process of the pipeline, found by its command line:

  server  run_server.py
  bridge  mqtt_http_bridge.py
  broker  mosquitto

CPU % of a process is relative to one core (100 = one core fully busy), as
in top; the system CPU % is the average over all cores. Processes are looked
up again when they are missing, so the monitor can be started before them.

Usage (on the node under test, during the whole experiment; Ctrl+C to stop):
  python3 experiments/resource_monitor.py --node pi5 --csv resultados/load/res_pi5.csv

Requires psutil (pip install psutil).

Project: PINV01-24 - FIUNA
"""

import argparse
import csv
import time
from pathlib import Path

import psutil

PROCESSES = {
    'server': 'run_server.py',
    'bridge': 'mqtt_http_bridge.py',
    'broker': 'mosquitto',
}
COLUMNS = (['node', 'epoch_ms', 'sys_cpu_pct', 'sys_mem_used_mb', 'sys_mem_avail_mb',
            'temp_c', 'cpu0_mhz']
           + [f'{name}_{m}' for name in PROCESSES for m in ('cpu_pct', 'rss_mb')])


def find(pattern):
    """First process whose command line contains pattern (not this monitor)."""
    for p in psutil.process_iter(['pid', 'cmdline', 'name']):
        try:
            cmd = ' '.join(p.info['cmdline'] or []) or (p.info['name'] or '')
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if pattern in cmd and 'resource_monitor' not in cmd:
            p.cpu_percent(None)   # prime the per-process CPU counter
            return p
    return None


def temperature():
    path = Path('/sys/class/thermal/thermal_zone0/temp')
    try:
        return round(int(path.read_text().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return ''


def cpu0_mhz():
    path = Path('/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq')
    try:
        return round(int(path.read_text().strip()) / 1000.0)
    except (OSError, ValueError):
        return ''


def sample(node, procs):
    vm = psutil.virtual_memory()
    row = dict(node=node, epoch_ms=round(time.time() * 1000.0, 1),
               sys_cpu_pct=psutil.cpu_percent(None),
               sys_mem_used_mb=round((vm.total - vm.available) / 2**20, 1),
               sys_mem_avail_mb=round(vm.available / 2**20, 1),
               temp_c=temperature(), cpu0_mhz=cpu0_mhz())
    for name, pattern in PROCESSES.items():
        p = procs.get(name)
        if p is None or not p.is_running():
            p = procs[name] = find(pattern)
        if p is None:
            row[f'{name}_cpu_pct'] = row[f'{name}_rss_mb'] = ''
            continue
        try:
            row[f'{name}_cpu_pct'] = round(p.cpu_percent(None), 1)
            row[f'{name}_rss_mb'] = round(p.memory_info().rss / 2**20, 1)
        except psutil.NoSuchProcess:
            procs[name] = None
            row[f'{name}_cpu_pct'] = row[f'{name}_rss_mb'] = ''
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description='CPU/memory monitor for the load experiments')
    ap.add_argument('--node', required=True, help='label of this node (e.g. pi5, opi)')
    ap.add_argument('--csv', required=True)
    ap.add_argument('--interval', type=float, default=0.5)
    ap.add_argument('--duration', type=float, default=0, help='seconds; 0 = until Ctrl+C')
    args = ap.parse_args(argv)

    path = Path(args.csv)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    procs = {name: find(pattern) for name, pattern in PROCESSES.items()}
    psutil.cpu_percent(None)
    print('monitoring', {k: (v.pid if v else None) for k, v in procs.items()})
    start = time.time()
    with open(path, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        try:
            while not args.duration or time.time() - start < args.duration:
                time.sleep(args.interval)
                w.writerow(sample(args.node, procs))
                f.flush()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
