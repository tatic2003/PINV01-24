#!/usr/bin/env python3
"""
MQTT -> HTTP Bridge for IoT-ITS (PINV01-24)

El servidor Flask (run_server.py) solo expone una API HTTP; no escucha MQTT
directamente. Este puente se suscribe a los topicos MQTT que usan los ESP32
(mqtt_device.ino) y reenvia cada evento como una llamada HTTP al servidor,
para que quede registrado en la misma capa semantica / BlockDAG.

Ademas escribe un CSV con una fila por evento, que es el registro crudo del
que salen las graficas de rendimiento del informe.

Deteccion de baja (Last Will and Testament)
-------------------------------------------
El firmware registra un LWT en el topico de estado del dispositivo. Si la
placa desaparece sin desconectarse (corte de energia, cuelgue, se va de
rango), el broker publica ese mensaje por ella y el puente lo reenvia como
un cambio de estado a 'inactive'. La deteccion es por evento, no por
ausencia de heartbeats, y tarda alrededor de 1.5 x keepalive.

Uso:
    python mqtt_http_bridge.py
    python mqtt_http_bridge.py --mqtt-host localhost --http-host localhost
    python mqtt_http_bridge.py --csv resultados/eventos_bridge.csv
"""

import argparse
import csv
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import requests
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format="%(asctime)s - bridge - %(message)s")
log = logging.getLogger("bridge")

CSV_COLUMNS = [
    "ts_iso",          # cuando el puente recibio el mensaje MQTT
    "device_id",
    "event",           # register | heartbeat | state | data | lwt_departure
    "topic",
    "http_status",     # codigo devuelto por el servidor ('' si no aplica)
    "http_rtt_ms",     # tiempo de la llamada HTTP puente -> servidor
    "detail",
]


class EventLog:
    """Append-only CSV with one row per bridge event."""

    def __init__(self, path):
        self.path = Path(path) if path else None
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists() or self.path.stat().st_size == 0
        self._fh = open(self.path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=CSV_COLUMNS)
        if is_new:
            self._writer.writeheader()
            self._fh.flush()
        log.info("Registrando eventos en %s", self.path)

    def write(self, **row):
        if not self.path:
            return
        full = {column: "" for column in CSV_COLUMNS}
        full.update(row)
        self._writer.writerow(full)
        # Flushed on every row: an experiment that is interrupted still leaves
        # a complete log up to that point.
        self._fh.flush()


def _timed(method, url, **kwargs):
    """Run an HTTP call and return (response | None, elapsed_ms, error)."""
    start = time.perf_counter()
    try:
        response = method(url, timeout=5, **kwargs)
        return response, (time.perf_counter() - start) * 1000, None
    except requests.exceptions.RequestException as exc:
        return None, (time.perf_counter() - start) * 1000, exc


def make_on_connect(topic_prefix):
    def on_connect(client, userdata, flags, rc, *args):
        if rc == 0:
            log.info("Conectado al broker MQTT")
            client.subscribe(f"{topic_prefix}/devices/register")
            client.subscribe(f"{topic_prefix}/devices/+/heartbeat")
            client.subscribe(f"{topic_prefix}/devices/+/state")
            client.subscribe(f"{topic_prefix}/devices/+/data")
            log.info("Suscrito a %s/devices/#", topic_prefix)
        else:
            log.error("Fallo la conexion MQTT, codigo %s", rc)
    return on_connect


def make_on_message(http_base, events):
    def on_message(client, userdata, msg):
        received = datetime.now().astimezone().isoformat(timespec="milliseconds")
        topic = msg.topic

        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            # An empty payload is how a retained message gets cleared, so it is
            # expected rather than an error worth alarming about.
            if not msg.payload:
                log.info("Retenido borrado en %s", topic)
            else:
                log.warning("Payload no-JSON en %s: %s", topic, exc)
            return

        device_id = payload.get("device_id") or payload.get("id")

        if topic.endswith("/devices/register"):
            response, rtt, error = _timed(
                requests.post, f"{http_base}/api/devices/register", json=payload
            )
            if error:
                log.error("REGISTER %s fallo: %s", device_id, error)
                events.write(ts_iso=received, device_id=device_id, event="register",
                             topic=topic, http_rtt_ms=f"{rtt:.1f}", detail=str(error))
                return

            detail = ""
            if response.ok:
                try:
                    body = response.json().get("classification", {})
                    detail = (f"{body.get('device_type')} "
                              f"conf={body.get('confidence'):.3f}")
                except Exception:
                    detail = response.text[:120]
            else:
                detail = response.text[:120]

            log.info("REGISTER %s -> HTTP %s (%.0f ms) %s",
                     device_id, response.status_code, rtt, detail)
            events.write(ts_iso=received, device_id=device_id, event="register",
                         topic=topic, http_status=response.status_code,
                         http_rtt_ms=f"{rtt:.1f}", detail=detail)

        elif topic.endswith("/heartbeat"):
            response, rtt, error = _timed(
                requests.post, f"{http_base}/api/devices/{device_id}/heartbeat"
            )
            if error:
                log.error("HEARTBEAT %s fallo: %s", device_id, error)
                events.write(ts_iso=received, device_id=device_id, event="heartbeat",
                             topic=topic, http_rtt_ms=f"{rtt:.1f}", detail=str(error))
                return

            rssi = (payload.get("status") or {}).get("wifi_rssi", "")
            log.info("HEARTBEAT %s -> HTTP %s (%.0f ms) rssi=%s",
                     device_id, response.status_code, rtt, rssi)
            events.write(ts_iso=received, device_id=device_id, event="heartbeat",
                         topic=topic, http_status=response.status_code,
                         http_rtt_ms=f"{rtt:.1f}", detail=f"rssi={rssi}")

        elif topic.endswith("/state"):
            state = payload.get("state", "active")
            # A state of 'inactive' carrying reason 'lwt' was published by the
            # broker, not by the device: it is a departure, not a state change
            # the device chose to announce.
            is_lwt = payload.get("reason") == "lwt"
            event = "lwt_departure" if is_lwt else "state"

            response, rtt, error = _timed(
                requests.put, f"{http_base}/api/devices/{device_id}/state",
                json={"state": state}
            )
            if error:
                log.error("STATE %s fallo: %s", device_id, error)
                events.write(ts_iso=received, device_id=device_id, event=event,
                             topic=topic, http_rtt_ms=f"{rtt:.1f}", detail=str(error))
                return

            if is_lwt:
                log.warning("BAJA DETECTADA (LWT) %s -> estado '%s', HTTP %s (%.0f ms)",
                            device_id, state, response.status_code, rtt)
            elif response.status_code == 400:
                # Typically a retained state replayed before the device has
                # registered in this server run. Not an error in the test.
                log.info("STATE %s -> HTTP 400 (dispositivo aun no registrado)",
                         device_id)
            else:
                log.info("STATE %s -> '%s', HTTP %s (%.0f ms)",
                         device_id, state, response.status_code, rtt)

            events.write(ts_iso=received, device_id=device_id, event=event,
                         topic=topic, http_status=response.status_code,
                         http_rtt_ms=f"{rtt:.1f}", detail=f"state={state}")

        elif topic.endswith("/data"):
            # There is no dedicated sensor-data endpoint; the reading is kept
            # here as evidence for the report.
            data = payload.get("data")
            log.info("DATA %s: %s", device_id, data)
            events.write(ts_iso=received, device_id=device_id, event="data",
                         topic=topic, detail=json.dumps(data, separators=(",", ":")))

    return on_message


def build_client(client_id):
    """Create an MQTT client on paho 1.x or 2.x."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)


def main():
    parser = argparse.ArgumentParser(description="Puente MQTT -> HTTP para IoT-ITS")
    parser.add_argument("--mqtt-host", default="localhost")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--http-host", default="localhost")
    parser.add_argument("--http-port", type=int, default=5000)
    parser.add_argument("--topic-prefix", default="its")
    parser.add_argument(
        "--csv",
        default="resultados/eventos_bridge.csv",
        help="CSV con un evento por fila (cadena vacia para desactivarlo)"
    )
    args = parser.parse_args()

    http_base = f"http://{args.http_host}:{args.http_port}"
    events = EventLog(args.csv)

    client = build_client("its-mqtt-http-bridge")
    client.on_connect = make_on_connect(args.topic_prefix)
    client.on_message = make_on_message(http_base, events)

    log.info("Conectando a MQTT %s:%s ...", args.mqtt_host, args.mqtt_port)
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=60)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        log.info("Puente detenido por el usuario")


if __name__ == "__main__":
    main()
