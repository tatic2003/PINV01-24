#!/usr/bin/env python3
"""
run_lwt_trials.py - Prueba guiada y automatizada de deteccion de baja por LWT
Proyecto PINV01-24 (FIUNA)

Se ejecuta en la Pi 5, donde corren el broker Mosquitto, el puente y este script,
asi que TODAS las marcas de tiempo salen del mismo reloj.

Que automatiza:
  - espera un heartbeat del dispositivo y calcula cuando hay que cortar el cable
    (offset nominal despues del heartbeat);
  - mide el instante del corte con un ping continuo (ultimo eco respondido);
  - mide la llegada del Last Will (estado 'inactive', reason 'lwt') por MQTT;
  - avisa cuando hay que reconectar el cable y mide la recuperacion;
  - guarda una fila por prueba en un CSV y imprime un resumen al final.

Lo unico manual es desenchufar y enchufar el cable cuando el script lo indica.

Uso (en la Pi, con el venv activado, con el broker, el servidor y el puente andando):
    python experiments/run_lwt_trials.py --ip 192.168.0.39
"""

import argparse
import csv
import json
import re
import shutil
import statistics
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

PING_OK = re.compile(r"^\[(\d+\.\d+)\]\s+\d+ bytes from")
PING_LOST = re.compile(r"no answer yet for icmp_seq")

CSV_COLUMNS = [
    "trial",
    "nominal_offset_s",   # offset pedido tras el heartbeat
    "valid",              # True si se midio todo el ciclo
    "t_heartbeat",        # heartbeat de referencia (reloj de la Pi)
    "t_cut",              # ultimo eco respondido antes del corte
    "actual_offset_s",    # t_cut - t_heartbeat
    "t_lwt",              # llegada del LWT al suscriptor
    "detection_s",        # t_lwt - t_cut
    "t_ip_back",          # primer eco respondido tras reconectar
    "t_active",           # estado 'active' tras reconectar
    "recovery_s",         # t_active - t_ip_back
    "note",
]


def iso(ts):
    return datetime.fromtimestamp(ts).isoformat(timespec="milliseconds") if ts else ""


class Monitor:
    """Guarda los eventos MQTT y de ping, y deja esperar por ellos."""

    def __init__(self, device_id):
        self.device_id = device_id
        self.mqtt_events = []   # {"ts", "topic", "data"}
        self.pings = []         # (read_ts, "ok"|"lost", ping_ts)
        self.cond = threading.Condition()

    # ---- entrada de datos -------------------------------------------------
    def add_mqtt(self, ts, topic, payload, retain=False):
        if retain:              # ignorar el estado retenido que llega al suscribirse
            return
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            data = {}
        with self.cond:
            self.mqtt_events.append({"ts": ts, "topic": topic, "data": data})
            self.cond.notify_all()

    def add_ping_line(self, line, now):
        m = PING_OK.match(line)
        if m:
            kind, ping_ts = "ok", float(m.group(1))
        elif PING_LOST.search(line):
            kind, ping_ts = "lost", None
        else:
            return
        with self.cond:
            self.pings.append((now, kind, ping_ts))
            self.cond.notify_all()

    # ---- esperas ----------------------------------------------------------
    def _match(self, event, kind):
        topic, data = event["topic"], event["data"]
        if kind == "heartbeat":
            return topic.endswith(f"/{self.device_id}/heartbeat")
        if topic.endswith(f"/{self.device_id}/state"):
            if kind == "lwt":
                return data.get("state") == "inactive" and data.get("reason") == "lwt"
            if kind == "active":
                return data.get("state") == "active"
        return False

    def wait_mqtt(self, kind, after, timeout):
        """Devuelve el ts del primer evento 'kind' con ts > after, o None."""
        deadline = time.time() + timeout
        with self.cond:
            while True:
                for e in self.mqtt_events:
                    if e["ts"] > after and self._match(e, kind):
                        return e["ts"]
                left = deadline - time.time()
                if left <= 0:
                    return None
                self.cond.wait(timeout=min(left, 0.5))

    def wait_ping_ok(self, timeout):
        deadline = time.time() + timeout
        with self.cond:
            while True:
                if any(k == "ok" for _, k, _ in self.pings):
                    return True
                left = deadline - time.time()
                if left <= 0:
                    return False
                self.cond.wait(timeout=min(left, 0.5))

    def wait_cut(self, after, timeout):
        """Primer corte (>=3 pings seguidos sin respuesta) que empieza despues de 'after'.
        Devuelve (t_cut, t_first_lost_read) o None. t_cut = ultimo eco respondido."""
        deadline = time.time() + timeout
        with self.cond:
            while True:
                last_ok = None
                run = 0
                ok_before = None
                first_lost_read = None
                for read_ts, kind, ping_ts in self.pings:
                    if kind == "ok":
                        last_ok = ping_ts
                        run = 0
                    else:
                        run += 1
                        if run == 1:
                            first_lost_read = read_ts
                            ok_before = last_ok
                        if run >= 3 and first_lost_read > after:
                            return (ok_before or first_lost_read), first_lost_read
                left = deadline - time.time()
                if left <= 0:
                    return None
                self.cond.wait(timeout=min(left, 0.5))

    def wait_ip_back(self, after_read, timeout):
        """Primer eco respondido cuya lectura es posterior a 'after_read'."""
        deadline = time.time() + timeout
        with self.cond:
            while True:
                for read_ts, kind, ping_ts in self.pings:
                    if kind == "ok" and read_ts > after_read:
                        return ping_ts
                left = deadline - time.time()
                if left <= 0:
                    return None
                self.cond.wait(timeout=min(left, 0.5))


def default_notify(kind, message):
    bell = "\a" if kind in ("unplug", "plug") else ""
    print(f"{bell}{message}", flush=True)


def run_trials(mon, offsets, csv_path, notify=default_notify, settle_s=5.0,
               hb_timeout=120.0, lwt_timeout=45.0, plug_timeout=120.0):
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not csv_path.exists() or csv_path.stat().st_size == 0
    rows = []
    ref = time.time()
    with open(csv_path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if is_new:
            writer.writeheader()
        n = len(offsets)
        for i, off in enumerate(offsets, 1):
            row = {c: "" for c in CSV_COLUMNS}
            row.update(trial=i, nominal_offset_s=off, valid=False)
            notify("info", f"\n=== Prueba {i}/{n} (cortar a los {off} s del heartbeat) ===")

            # 1) heartbeat de referencia
            t_hb = mon.wait_mqtt("heartbeat", after=ref, timeout=hb_timeout)
            if t_hb is None:
                row["note"] = "sin heartbeat: el dispositivo no publica"
                notify("info", "No llega ningun heartbeat. Reviso el dispositivo y corto el script.")
                writer.writerow(row); fh.flush(); rows.append(row)
                break
            row["t_heartbeat"] = iso(t_hb)
            notify("info", f"Heartbeat recibido. Preparate: desenchufa en {off} s ...")
            time.sleep(max(0.0, t_hb + off - time.time()))
            notify("unplug", ">>> DESENCHUFA EL CABLE AHORA <<<")

            # 2) corte medido por ping
            cut = mon.wait_cut(after=t_hb, timeout=off + 40)
            if cut is None:
                row["note"] = "no se detecto el corte por ping"
                notify("info", "No detecte el corte (el ping sigue respondiendo). Prueba invalida.")
                writer.writerow(row); fh.flush(); rows.append(row)
                ref = time.time()
                continue
            t_cut, lost_read = cut
            row["t_cut"] = iso(t_cut)
            row["actual_offset_s"] = round(t_cut - t_hb, 3)
            notify("info", f"Corte detectado: {row['actual_offset_s']} s despues del heartbeat. Espero el LWT ...")

            # 3) LWT
            t_lwt = mon.wait_mqtt("lwt", after=t_cut - 1.0, timeout=lwt_timeout)
            note = []
            if t_lwt is None:
                note.append("LWT no llego")
                notify("info", "El LWT no llego a tiempo.")
            else:
                row["t_lwt"] = iso(t_lwt)
                row["detection_s"] = round(t_lwt - t_cut, 3)
                notify("info", f"LWT recibido. Deteccion = {row['detection_s']} s")

            # 4) reconexion
            notify("plug", ">>> ENCHUFA EL CABLE AHORA <<<")
            t_back = mon.wait_ip_back(lost_read, timeout=plug_timeout)
            t_active = mon.wait_mqtt("active", after=lost_read, timeout=plug_timeout) if t_back else None
            if t_back is None or t_active is None:
                note.append("sin recuperacion medida")
                notify("info", "No se midio la recuperacion.")
            else:
                row["t_ip_back"] = iso(t_back)
                row["t_active"] = iso(t_active)
                row["recovery_s"] = round(t_active - t_back, 3)
                notify("info", f"Recuperado. IP alcanzable -> estado active: {row['recovery_s']} s")

            row["valid"] = bool(t_lwt) and bool(t_active)
            row["note"] = "; ".join(note)
            writer.writerow(row); fh.flush(); rows.append(row)
            ref = (t_active or time.time()) + settle_s
    return rows


def summarize(rows, say=print):
    def stats(values):
        values = sorted(values)
        return (f"n={len(values)}  min={values[0]:.2f}  mediana={statistics.median(values):.2f}  "
                f"media={statistics.mean(values):.2f}  max={values[-1]:.2f}")
    ok = [r for r in rows if r["valid"]]
    say(f"\nPruebas validas: {len(ok)} de {len(rows)}")
    det = [r["detection_s"] for r in ok if r["detection_s"] != ""]
    rec = [r["recovery_s"] for r in ok if r["recovery_s"] != ""]
    if det:
        say("Deteccion (s):   " + stats(det))
    if rec:
        say("Recuperacion (s): " + stats(rec))


def main():
    import paho.mqtt.client as mqtt

    ap = argparse.ArgumentParser(description="Prueba guiada de baja por LWT")
    ap.add_argument("--ip", required=True, help="IP del dispositivo (para el ping)")
    ap.add_argument("--device", default="esp32-olimex-poe-01")
    ap.add_argument("--mqtt-host", default="localhost")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--offsets", default="3,8,13,18,23,28,5,10,20,25",
                    help="segundos tras un heartbeat en que se corta el cable")
    ap.add_argument("--csv", default="resultados/lwt_trials_2026-10-06.csv")
    ap.add_argument("--ping-log", default="resultados/ping_lwt_trials_2026-10-06.log")
    args = ap.parse_args()
    offsets = [float(x) for x in args.offsets.split(",") if x.strip()]

    mon = Monitor(args.device)

    # ---- MQTT ----
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="lwt-trials-runner")

    def on_connect(c, userdata, *a):
        c.subscribe([(f"its/devices/{args.device}/#", 1)])

    def on_message(c, userdata, msg):
        mon.add_mqtt(time.time(), msg.topic, msg.payload, msg.retain)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
    client.loop_start()

    # ---- ping continuo ----
    Path(args.ping_log).parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ping", "-D", "-O", "-i", "0.2", args.ip]
    if shutil.which("stdbuf"):
        cmd = ["stdbuf", "-oL"] + cmd
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    def ping_reader():
        with open(args.ping_log, "a", encoding="utf-8") as log:
            for line in proc.stdout:
                line = line.strip()
                log.write(line + "\n")
                log.flush()
                mon.add_ping_line(line, time.time())

    threading.Thread(target=ping_reader, daemon=True).start()

    rows = []
    try:
        if not mon.wait_ping_ok(10):
            print("El dispositivo no responde ping. Verifica la IP y el cable.")
            return
        print(f"Dispositivo {args.device} responde ping. Pruebas: {len(offsets)}. CSV: {args.csv}")
        rows = run_trials(mon, offsets, args.csv)
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
    finally:
        proc.terminate()
        client.loop_stop()
        client.disconnect()
        if rows:
            summarize(rows)


if __name__ == "__main__":
    main()
