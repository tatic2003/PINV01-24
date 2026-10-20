#!/usr/bin/env python3
"""
GNSS announcement validation experiment.

Sends valid and deliberately invalid GNSS announcements to a running server
(POST /api/devices/register) and records, per case:
  - the HTTP verdict of the server's registration path (lightweight validator),
  - the verdict of the SHACL GNSSReceiverShape on the same data (in process),
  - registration round-trip time and SHACL validation time.

The two verdicts are compared and disagreements are reported, not hidden.

Usage (server already running):
  python experiments/run_gnss_validation.py --server http://localhost:5000 --repeats 30

Output: resultados/gnss_validacion_<fecha>.csv and a summary on stdout.

Project: PINV01-24 - FIUNA
"""

import argparse
import csv
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from semantic_layer.device_manager import SemanticDeviceManager
from semantic_layer.rdf_store import RDFStore
from semantic_layer.validator import SHACLValidator

# Base announcement: a valid one under the current contract, with the fields
# the real gnss_agent announces (the registration path and the SHACL shape
# require a positive memory_total). Memory values are those measured on the
# Raspberry Pi 5 host. Each case below changes exactly one thing.
BASE = {
    "hardware": "Adafruit Ultimate GPS HAT (PA1616D)", "ip": "192.168.1.60",
    "capabilities": ["gnss", "position", "time"],
    "link_type": "wifi", "memory_total": 8454012928, "memory_free": 7744864256,
    "cpu_freq_mhz": 2400,
    "lat": -25.2637, "lon": -57.5759, "hdop": 0.9, "satellites": 9,
    "fix_quality": 1, "update_rate_hz": 1,
}

# (name, expected_valid, overrides, keys_to_remove)
CASES = [
    ("valid_fix", True, {}, []),
    ("valid_no_fix_no_position", True, {"fix_quality": 0, "satellites": 0}, ["lat", "lon", "hdop"]),
    ("lat_above_90", False, {"lat": 91.0}, []),
    ("lat_below_-90", False, {"lat": -90.5}, []),
    ("lon_above_180", False, {"lon": 180.5}, []),
    ("lon_below_-180", False, {"lon": -181.0}, []),
    ("hdop_negative", False, {"hdop": -1.0}, []),
    ("satellites_above_64", False, {"satellites": 65}, []),
    ("fix_quality_above_8", False, {"fix_quality": 9}, []),
    ("fix_without_position", False, {}, ["lat", "lon"]),
    ("fix_quality_missing", False, {}, ["fix_quality"]),
    ("lat_not_numeric", False, {"lat": "abc"}, []),
]


def build(case_id, overrides, remove):
    data = dict(BASE)
    data["id"] = case_id
    data.update(overrides)
    for k in remove:
        data.pop(k, None)
    return data


def http(method, url, body=None):
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    return status, (time.perf_counter() - t0) * 1000


def shacl_verdict(data, validator, mgr):
    """SHACL verdict on the announcement, built the way the store builds it."""
    norm = mgr._normalize_device_data(data)
    store = RDFStore()
    try:
        store.register_device(norm, device_type="GNSSReceiver")
    except (ValueError, TypeError, ArithmeticError):
        return False, 0.0   # not even representable as typed literals
    t0 = time.perf_counter()
    result = validator.validate(store.graph)
    return result.is_valid, (time.perf_counter() - t0) * 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:5000")
    ap.add_argument("--repeats", type=int, default=30)
    ap.add_argument("--out-dir", default="resultados")
    args = ap.parse_args()

    validator, mgr = SHACLValidator(), SemanticDeviceManager()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"gnss_validacion_{datetime.now():%Y-%m-%d_%H%M%S}.csv"

    rows = []
    for name, expected, over, rem in CASES:
        for i in range(args.repeats):
            dev_id = f"gnssval-{name.replace('_', '-')}-{i:03d}"
            data = build(dev_id, over, rem)
            status, rtt = http("POST", f"{args.server}/api/devices/register", data)
            server_ok = status == 200
            shacl_ok, shacl_ms = shacl_verdict(data, validator, mgr)
            if server_ok:
                http("DELETE", f"{args.server}/api/devices/{dev_id}")
            rows.append({"case": name, "run": i, "expected_valid": expected,
                         "http_status": status, "server_accepts": server_ok,
                         "shacl_accepts": shacl_ok,
                         "register_ms": round(rtt, 3), "shacl_ms": round(shacl_ms, 3)})

    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"\nCSV: {path}\n")
    print(f"{'case':28} {'expect':7} {'server':7} {'shacl':7} {'reg_ms(med)':>11} {'shacl_ms(med)':>13}")
    wrong_server = wrong_shacl = disagree = 0
    for name, expected, _, _ in CASES:
        r = [x for x in rows if x["case"] == name]
        s_ok = sum(x["server_accepts"] for x in r)
        h_ok = sum(x["shacl_accepts"] for x in r)
        n = len(r)
        wrong_server += sum(x["server_accepts"] != expected for x in r)
        wrong_shacl += sum(x["shacl_accepts"] != expected for x in r)
        disagree += sum(x["server_accepts"] != x["shacl_accepts"] for x in r)
        print(f"{name:28} {str(expected):7} {s_ok:>3}/{n:<3} {h_ok:>3}/{n:<3} "
              f"{statistics.median(x['register_ms'] for x in r):>11.2f} "
              f"{statistics.median(x['shacl_ms'] for x in r):>13.2f}")
    total = len(rows)
    print(f"\nTotal {total} announcements")
    print(f"Server verdict differs from expectation: {wrong_server}")
    print(f"SHACL verdict differs from expectation:  {wrong_shacl}")
    print(f"Server and SHACL disagree:               {disagree}")


if __name__ == "__main__":
    main()