#!/usr/bin/env python3
"""
Time To First Fix (TTFF) of the Adafruit Ultimate GPS HAT (PA1616D module)
on a Raspberry Pi 5, read from /dev/ttyAMA0.

- Queries the antenna status ($PGTOP).
- Forces N restarts (hot, warm, cold or full cold) with MediaTek PMTK commands.
- Measures the time from sending the command until the first fix: a GGA
  sentence with quality >= 1 and an RMC sentence with status A.
- Writes a CSV with one row per run and an NMEA log WITHOUT coordinates
  (latitude and longitude are removed before the line is written).

Output format (kept as in the published data, figures/data/ttff/):
  CSV columns: corrida, tipo, antena_etiqueta, pgtop, inicio_utc, ttff_s,
               calidad_gga, satelites, hdop, nota
               (run, start type, antenna label, $PGTOP code, start time UTC,
               TTFF in s, GGA fix quality, satellites, HDOP, note)
  start types: caliente (hot), tibio (warm), frio (cold), completo (full cold)
  NMEA log:    one line per sentence, prefixed with the host arrival time
               (UTC, HH:MM:SS.mmm); "# corrida <n> tipo=<type>" before each run.

Usage (on the Pi; sudo is needed for the serial port):
  sudo python3 experiments/ttff_gps.py --start cold --n 3 --antenna externa --out /home/<user>/evidencias
  sudo python3 experiments/ttff_gps.py --start hot  --n 3 --antenna externa --out /home/<user>/evidencias

Project: PINV01-24 - FIUNA
"""
import argparse
import csv
import datetime as dt
import os
import statistics
import time

import serial

PORT = "/dev/ttyAMA0"
BAUD = 9600

# Start type: (label written to the CSV and file names, PMTK command)
START_TYPES = {
    "hot": ("caliente", b"$PMTK101*32\r\n"),   # keeps everything
    "warm": ("tibio", b"$PMTK102*31\r\n"),     # discards ephemeris
    "cold": ("frio", b"$PMTK103*30\r\n"),      # discards ephemeris, almanac, time and position
    "full": ("completo", b"$PMTK104*37\r\n"),  # also clears configuration and stored data
}
CMD_ANTENNA = b"$PGCMD,33,1*6C\r\n"
ANTENNA_STATUS = {"1": "antenna short", "2": "internal", "3": "external active"}

CSV_COLUMNS = ["corrida", "tipo", "antena_etiqueta", "pgtop", "inicio_utc",
               "ttff_s", "calidad_gga", "satelites", "hdop", "nota"]


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def strip_coordinates(line):
    """Remove latitude/longitude from GGA, RMC and GLL so that the location is never stored."""
    fields = line.split(",")
    if line.startswith(("$GNGGA", "$GPGGA")) and len(fields) > 5:
        fields[2:6] = ["", "", "", ""]
    elif line.startswith(("$GNRMC", "$GPRMC")) and len(fields) > 6:
        fields[3:7] = ["", "", "", ""]
    elif line.startswith(("$GNGLL", "$GPGLL")) and len(fields) > 4:
        fields[1:5] = ["", "", "", ""]
    return ",".join(fields)


def read_line(port):
    return port.readline().decode(errors="ignore").strip()


def antenna_status(port, seconds=6):
    """Ask the module for its antenna status; returns (code, description)."""
    port.reset_input_buffer()
    port.write(CMD_ANTENNA)
    t = time.monotonic()
    while time.monotonic() - t < seconds:
        line = read_line(port)
        if line.startswith("$PGTOP"):
            parts = line.split(",")
            code = parts[2].split("*")[0] if len(parts) > 2 else "?"
            return code, ANTENNA_STATUS.get(code, "unknown")
    return "", "no answer"


def one_run(port, command, timeout, log):
    """Send the restart command and wait for the first fix.

    Returns (start time UTC, TTFF in s or None, GGA fields of the fix or None).
    """
    port.reset_input_buffer()
    start = now_utc()
    port.write(command)
    t0 = time.monotonic()
    rmc_ok = False
    gga = None
    while time.monotonic() - t0 < timeout:
        line = read_line(port)
        if not line:
            continue
        log.write(f"{now_utc().strftime('%H:%M:%S.%f')[:-3]} {strip_coordinates(line)}\n")
        fields = line.split(",")
        if line[3:6] == "RMC" and len(fields) > 2:
            rmc_ok = fields[2] == "A"
        elif line[3:6] == "GGA" and len(fields) > 8:
            quality = int(fields[6]) if fields[6].isdigit() else 0
            if quality >= 1:
                gga = {"quality": quality, "sats": fields[7], "hdop": fields[8]}
        if rmc_ok and gga:
            return start, time.monotonic() - t0, gga
    return start, None, None


def main():
    p = argparse.ArgumentParser(description="TTFF of the GPS HAT")
    p.add_argument("--start", choices=START_TYPES.keys(), default="cold", help="restart type")
    p.add_argument("--n", type=int, default=3, help="number of runs")
    p.add_argument("--antenna", default="externa", help="antenna label written to the CSV")
    p.add_argument("--timeout", type=int, default=600, help="maximum seconds per run")
    p.add_argument("--pause", type=int, default=15, help="seconds between runs")
    p.add_argument("--note", default="", help="conditions: weather, placement, etc.")
    p.add_argument("--out", default="~/evidencias", help="folder for the CSV and the NMEA log")
    a = p.parse_args()

    label, command = START_TYPES[a.start]
    folder = os.path.expanduser(a.out)
    os.makedirs(folder, exist_ok=True)
    stamp = now_utc().strftime("%Y-%m-%dT%H%MZ")
    csv_path = os.path.join(folder, f"ttff_{label}_{a.antenna}_{stamp}.csv")
    log_path = os.path.join(folder, f"ttff_{label}_{a.antenna}_{stamp}_nmea_sin_coord.txt")

    with serial.Serial(PORT, BAUD, timeout=1) as port, \
         open(csv_path, "w", newline="") as fcsv, open(log_path, "w") as log:
        code, antenna_txt = antenna_status(port)
        print(f"Antenna status (PGTOP): {code or '-'} = {antenna_txt}")

        w = csv.writer(fcsv)
        w.writerow(CSV_COLUMNS)
        results = []
        for i in range(1, a.n + 1):
            print(f"Run {i}/{a.n} ({a.start})... ", end="", flush=True)
            log.write(f"# corrida {i} tipo={label}\n")
            start, ttff, gga = one_run(port, command, a.timeout, log)
            if ttff is None:
                print(f"no fix within {a.timeout} s")
                w.writerow([i, label, a.antenna, code, start.isoformat(), "", "", "", "", a.note])
            else:
                results.append(ttff)
                print(f"TTFF = {ttff:.1f} s, satellites {gga['sats']}, HDOP {gga['hdop']}")
                w.writerow([i, label, a.antenna, code, start.isoformat(), f"{ttff:.1f}",
                            gga["quality"], gga["sats"], gga["hdop"], a.note])
            fcsv.flush()
            if i < a.n:
                time.sleep(a.pause)

    if results:
        print(f"\nTTFF {a.start}: median {statistics.median(results):.1f} s, "
              f"min {min(results):.1f} s, max {max(results):.1f} s (n={len(results)})")
    print(f"CSV: {csv_path}\nNMEA log (no coordinates): {log_path}")


if __name__ == "__main__":
    main()