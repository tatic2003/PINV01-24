#!/usr/bin/env python3
"""
MQTT -> HTTP Bridge for IoT-ITS (PINV01-24)

The Flask server (run_server.py) only exposes an HTTP API; it does not
listen to MQTT directly. This bridge subscribes to the MQTT topics used by the
ESP32 boards (mqtt_device.ino) and forwards each event as an HTTP call to the
server, so that it is recorded in the same semantic layer / BlockDAG.

It also writes a CSV with one row per event, the raw log from which the
performance plots of the report are made.

Departure detection (Last Will and Testament)
---------------------------------------------
The firmware registers an LWT on the device's state topic. If the board
disappears without disconnecting (power loss, crash, out of range), the broker
publishes that message on its behalf and the bridge forwards it as a change of
state to 'inactive'. Detection is event-driven, not based on missing
heartbeats, and takes about 1.5 x keepalive.

Usage:
    python mqtt_http_bridge.py
    python mqtt_http_bridge.py --mqtt-host localhost --http-host localhost
    python mqtt_http_bridge.py --csv resultados/eventos_bridge.csv
    python mqtt_http_bridge.py --session solo_c3 --onboarding-csv resultados/onboarding.csv

Onboarding latency by stage
---------------------------
If the announcement carries a "timing" block (instrumented firmware and
linux_agent), the bridge removes it before forwarding the announcement, so the
server receives exactly the same payload as without instrumentation, and it
writes a row to the onboarding CSV (--onboarding-csv) with the device's marks,
the arrival at the bridge, the wait in the MQTT client's queue and the timings
returned by the server. experiments/analyze_onboarding.py builds the stages
from that CSV.
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
    "ts_iso",          # when the bridge received the MQTT message
    "device_id",
    "event",           # register | heartbeat | state | data | lwt_departure
    "topic",
    "http_status",     # status code returned by the server ('' if not applicable)
    "http_rtt_ms",     # duration of the HTTP call bridge -> server
    "detail",
]


ONBOARDING_COLUMNS = [
    "session",               # session label (--session)
    "device_id",
    "hardware",
    # Device marks ("timing" block of the announcement)
    "seq",                   # 1 = first registration since boot
    "synced",                # the device clock was synchronized
    "setup_ms",              # millis() on entering setup()
    "serial_ms",             # millis() after the serial-port wait
    "link_ms",               # millis() with the link up (IP assigned)
    "sync_ms",               # millis() with the clock synchronized (NTP)
    "sync_rtt_ms",           # round trip of the NTP sample that set the clock
    "mqtt_ms",               # millis() with the MQTT session connected
    "publish_ms",            # millis() just before publishing the announcement
    "epoch_publish_ms",      # wall-clock time (ms) just before publishing
    "agent_start_epoch_ms",  # linux_agent: agent start
    "mqtt_epoch_ms",         # linux_agent: MQTT connection
    "uptime_s",              # linux_agent: seconds since the OS booted
    # Bridge (Pi clock)
    "arrival_epoch_ms",      # the bridge's MQTT client received the message
    "callback_epoch_ms",     # processing started (after paho's queue)
    "http_status",
    "http_rtt_ms",
    # Server (registration response)
    "server_recv_epoch_ms",
    "srv_classify_ms",
    "srv_semantic_ms",
    "srv_dag_ms",
    "srv_shacl_ms",          # full SHACL on registration (server --shacl-on-register)
    "srv_total_ms",
    "device_type",
    "confidence",
]


class EventLog:
    """Append-only CSV with one row per bridge event."""

    def __init__(self, path, columns=None):
        self.path = Path(path) if path else None
        self.columns = columns or CSV_COLUMNS
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists() or self.path.stat().st_size == 0
        self._fh = open(self.path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=self.columns,
                                      extrasaction="ignore")
        if is_new:
            self._writer.writeheader()
            self._fh.flush()
        log.info("Logging events to %s", self.path)

    def write(self, **row):
        if not self.path:
            return
        full = {column: "" for column in self.columns}
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
            log.info("Connected to the MQTT broker")
            client.subscribe(f"{topic_prefix}/devices/register")
            client.subscribe(f"{topic_prefix}/devices/+/heartbeat")
            client.subscribe(f"{topic_prefix}/devices/+/state")
            client.subscribe(f"{topic_prefix}/devices/+/data")
            log.info("Subscribed to %s/devices/#", topic_prefix)
        else:
            log.error("MQTT connection failed, code %s", rc)
    return on_connect


def _arrival_epoch_ms(msg, now_epoch_ms):
    """Epoch (ms) at which paho received the message from the socket.

    paho stamps every message with time.monotonic() on arrival; the gap to the
    start of the callback is the time it waited in the client's queue behind
    earlier messages (the callbacks run one at a time).
    """
    stamp = getattr(msg, "timestamp", None)
    if not stamp:
        return now_epoch_ms
    return now_epoch_ms - (time.monotonic() - stamp) * 1000.0


def make_on_message(http_base, events, onboarding=None, session=""):
    def on_message(client, userdata, msg):
        callback_epoch_ms = time.time() * 1000.0
        arrival_epoch_ms = _arrival_epoch_ms(msg, callback_epoch_ms)
        received = datetime.now().astimezone().isoformat(timespec="milliseconds")
        topic = msg.topic

        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            # An empty payload is how a retained message gets cleared, so it is
            # expected rather than an error worth alarming about.
            if not msg.payload:
                log.info("Retained message cleared on %s", topic)
            else:
                log.warning("Non-JSON payload on %s: %s", topic, exc)
            return

        device_id = payload.get("device_id") or payload.get("id")

        if topic.endswith("/devices/register"):
            # The measurement block is not part of the announcement: removed
            # here, the server sees the same payload as without instrumentation.
            timing = payload.pop("timing", None)
            response, rtt, error = _timed(
                requests.post, f"{http_base}/api/devices/register", json=payload
            )
            if error:
                log.error("REGISTER %s failed: %s", device_id, error)
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

            if onboarding is not None and isinstance(timing, dict):
                try:
                    body = response.json() if response.ok else {}
                except ValueError:
                    body = {}
                srv = body.get("timing_ms") or {}
                cls = body.get("classification") or {}
                row = {k: timing.get(k, "") for k in ONBOARDING_COLUMNS if k in timing}
                row.update(
                    # A load generator labels each load level in the timing
                    # block; otherwise the bridge's --session applies.
                    session=timing.get("session") or session, device_id=device_id,
                    hardware=payload.get("hardware", ""),
                    arrival_epoch_ms=f"{arrival_epoch_ms:.3f}",
                    callback_epoch_ms=f"{callback_epoch_ms:.3f}",
                    http_status=response.status_code, http_rtt_ms=f"{rtt:.3f}",
                    server_recv_epoch_ms=body.get("server_recv_epoch_ms", ""),
                    srv_classify_ms=srv.get("classify", ""),
                    srv_semantic_ms=srv.get("semantic", ""),
                    srv_dag_ms=srv.get("dag", ""),
                    srv_shacl_ms=srv.get("shacl", ""),
                    srv_total_ms=srv.get("total", ""),
                    device_type=cls.get("device_type", ""),
                    confidence=cls.get("confidence", ""),
                )
                onboarding.write(**row)

        elif topic.endswith("/heartbeat"):
            response, rtt, error = _timed(
                requests.post, f"{http_base}/api/devices/{device_id}/heartbeat"
            )
            if error:
                log.error("HEARTBEAT %s failed: %s", device_id, error)
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
                log.error("STATE %s failed: %s", device_id, error)
                events.write(ts_iso=received, device_id=device_id, event=event,
                             topic=topic, http_rtt_ms=f"{rtt:.1f}", detail=str(error))
                return

            if is_lwt:
                log.warning("DEPARTURE DETECTED (LWT) %s -> state '%s', HTTP %s (%.0f ms)",
                            device_id, state, response.status_code, rtt)
            elif response.status_code == 400:
                # Typically a retained state replayed before the device has
                # registered in this server run. Not an error in the test.
                log.info("STATE %s -> HTTP 400 (device not registered yet)",
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
    parser = argparse.ArgumentParser(description="MQTT -> HTTP bridge for IoT-ITS")
    parser.add_argument("--mqtt-host", default="localhost")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--http-host", default="localhost")
    parser.add_argument("--http-port", type=int, default=5000)
    parser.add_argument("--topic-prefix", default="its")
    parser.add_argument(
        "--csv",
        default="resultados/eventos_bridge.csv",
        help="CSV with one event per row (empty string to disable it)"
    )
    parser.add_argument(
        "--onboarding-csv",
        default="resultados/onboarding_bridge.csv",
        help="onboarding CSV, one row per announcement with a 'timing' block "
             "(empty string to disable it)"
    )
    parser.add_argument(
        "--session", default="",
        help="label copied into every row of the onboarding CSV (e.g. individual, concurrent)"
    )
    args = parser.parse_args()

    http_base = f"http://{args.http_host}:{args.http_port}"
    events = EventLog(args.csv)
    onboarding = EventLog(args.onboarding_csv, ONBOARDING_COLUMNS) if args.onboarding_csv else None

    client = build_client("its-mqtt-http-bridge")
    client.on_connect = make_on_connect(args.topic_prefix)
    client.on_message = make_on_message(http_base, events, onboarding, args.session)

    log.info("Connecting to MQTT %s:%s ...", args.mqtt_host, args.mqtt_port)
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=60)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        log.info("Bridge stopped by the user")


if __name__ == "__main__":
    main()
