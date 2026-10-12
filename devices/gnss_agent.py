#!/usr/bin/env python3
"""
GNSS device agent for IoT-ITS.

Makes a GPS/GNSS receiver a device of the ITS network, with the same MQTT
contract as the ESP32 firmware:

  its/devices/register            announcement (once, after connecting)
  its/devices/<id>/state          retained; Last Will = {"state":"inactive"}
  its/devices/<id>/heartbeat      every --heartbeat seconds
  its/devices/<id>/data           position and fix quality, every --period s

Position source:
  default       gpsd JSON stream (127.0.0.1:2947), real receiver
  --simulate    synthetic receiver (for tests without hardware)

Privacy: by default the published coordinates are NOT the real ones, but a
fixed public reference point (centre of Asuncion), jittered. The receiver's
fix quality, satellites and HDOP are always real. Use --coords real only on
a private network.

Project: PINV01-24 - FIUNA
"""

import argparse
import json
import logging
import random
import socket
import threading
import time

import paho.mqtt.client as mqtt

log = logging.getLogger("gnss_agent")

EXAMPLE_LAT, EXAMPLE_LON = -25.2637, -57.5759   # public reference: centre of Asuncion


def build_client(client_id):
    """Create an MQTT client on paho 1.x or 2.x."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)


def local_ip():
    """IP used to reach the network (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def host_resources():
    """Memory and CPU of the host board, which is what the network sees."""
    out = {}
    try:
        with open("/proc/meminfo") as f:
            info = {l.split(":")[0]: int(l.split()[1]) * 1024 for l in f if ":" in l}
        out["memory_total"] = info["MemTotal"]
        out["memory_free"] = info.get("MemAvailable", info.get("MemFree", 0))
    except (OSError, KeyError, ValueError):
        pass
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq") as f:
            out["cpu_freq_mhz"] = int(f.read().strip()) // 1000
    except (OSError, ValueError):
        pass
    return out


class Receiver:
    """Latest receiver state, updated by a background thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"fix_quality": 0, "latitude": None, "longitude": None,
                      "hdop": None, "satellites_used": 0, "altitude_m": None}

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def update(self, **kw):
        with self.lock:
            self.state.update(kw)


def gpsd_reader(receiver, host, port, stop):
    """Read TPV and SKY objects from gpsd and keep the receiver state current."""
    while not stop.is_set():
        try:
            with socket.create_connection((host, port), timeout=10) as sock:
                sock.sendall(b'?WATCH={"enable":true,"json":true};\n')
                sock.settimeout(10)
                buf = b""
                while not stop.is_set():
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        cls = obj.get("class")
                        if cls == "TPV":
                            mode = obj.get("mode", 0)
                            fix = 1 if mode >= 2 else 0
                            receiver.update(
                                fix_quality=fix,
                                latitude=obj.get("lat") if fix else None,
                                longitude=obj.get("lon") if fix else None,
                                altitude_m=obj.get("altHAE", obj.get("alt")) if mode >= 3 else None)
                        elif cls == "SKY":
                            receiver.update(
                                hdop=obj.get("hdop"),
                                satellites_used=obj.get("uSat", receiver.snapshot()["satellites_used"]))
        except (OSError, socket.timeout) as exc:
            log.warning("gpsd not reachable (%s); retrying in 3 s", exc)
            stop.wait(3)


def simulator(receiver, stop, fix_after=3.0):
    """Synthetic receiver: acquires a fix after a few seconds, then wanders."""
    t0 = time.time()
    lat, lon = EXAMPLE_LAT, EXAMPLE_LON
    while not stop.is_set():
        if time.time() - t0 >= fix_after:
            lat += random.uniform(-2e-5, 2e-5)
            lon += random.uniform(-2e-5, 2e-5)
            receiver.update(fix_quality=1, latitude=lat, longitude=lon,
                            hdop=round(random.uniform(0.8, 1.3), 2),
                            satellites_used=random.randint(8, 11))
        stop.wait(1.0)


def published_position(snap, mode):
    """Coordinates to publish, honouring the privacy mode."""
    if snap["latitude"] is None:
        return None, None
    if mode == "real":
        return snap["latitude"], snap["longitude"]
    return (round(EXAMPLE_LAT + random.uniform(-1e-4, 1e-4), 6),
            round(EXAMPLE_LON + random.uniform(-1e-4, 1e-4), 6))


def main():
    ap = argparse.ArgumentParser(description="GNSS device agent for IoT-ITS")
    ap.add_argument("--device-id", default="gnss-pi5-01")
    ap.add_argument("--hardware", default="Adafruit Ultimate GPS HAT (PA1616D)")
    ap.add_argument("--mqtt-host", default="localhost")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--topic-prefix", default="its")
    ap.add_argument("--gpsd-host", default="127.0.0.1")
    ap.add_argument("--gpsd-port", type=int, default=2947)
    ap.add_argument("--simulate", action="store_true", help="synthetic receiver, no hardware")
    ap.add_argument("--coords", choices=["example", "real"], default="example")
    ap.add_argument("--keepalive", type=int, default=15)
    ap.add_argument("--heartbeat", type=float, default=30.0)
    ap.add_argument("--period", type=float, default=1.0, help="seconds between data messages")
    ap.add_argument("--wait-fix", type=float, default=0.0,
                    help="seconds to wait for a fix before announcing (0 = announce immediately)")
    ap.add_argument("--duration", type=float, default=0.0, help="stop after N seconds (0 = run forever)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    receiver, stop = Receiver(), threading.Event()
    target, targs = (simulator, (receiver, stop)) if args.simulate else \
        (gpsd_reader, (receiver, args.gpsd_host, args.gpsd_port, stop))
    threading.Thread(target=target, args=targs, daemon=True).start()

    base = f"{args.topic_prefix}/devices"
    t_register, t_state = f"{base}/register", f"{base}/{args.device_id}/state"
    t_hb, t_data = f"{base}/{args.device_id}/heartbeat", f"{base}/{args.device_id}/data"

    client = build_client(f"gnss-{args.device_id}")
    will = json.dumps({"device_id": args.device_id, "state": "inactive", "reason": "lwt"})
    client.will_set(t_state, will, qos=1, retain=True)

    def announce():
        snap = receiver.snapshot()
        lat, lon = published_position(snap, args.coords)
        msg = {"id": args.device_id, "hardware": args.hardware, "ip": local_ip(),
               "link_type": "ethernet", "protocol": "mqtt",
               "capabilities": ["gnss", "position", "time"],
               **host_resources(),
               "fix_quality": snap["fix_quality"], "update_rate_hz": 1}
        if lat is not None:
            msg.update(lat=lat, lon=lon)
        if snap["hdop"] is not None:
            msg["hdop"] = snap["hdop"]
        msg["satellites"] = snap["satellites_used"]
        client.publish(t_register, json.dumps(msg), qos=1)
        client.publish(t_state, json.dumps({"device_id": args.device_id, "state": "active"}),
                       qos=1, retain=True)
        log.info("announced %s (fix_quality=%s)", args.device_id, snap["fix_quality"])

    client.connect(args.mqtt_host, args.mqtt_port, keepalive=args.keepalive)
    client.loop_start()

    deadline = time.time() + args.wait_fix
    while args.wait_fix and time.time() < deadline and receiver.snapshot()["fix_quality"] < 1:
        time.sleep(0.5)
    announce()

    t_end = time.time() + args.duration if args.duration else None
    last_hb = time.time()
    try:
        while t_end is None or time.time() < t_end:
            snap = receiver.snapshot()
            lat, lon = published_position(snap, args.coords)
            data = {"fix_quality": snap["fix_quality"], "satellites_used": snap["satellites_used"],
                    "hdop": snap["hdop"], "latitude": lat, "longitude": lon,
                    "altitude_m": snap["altitude_m"] if args.coords == "real" else None}
            client.publish(t_data, json.dumps({"device_id": args.device_id,
                                               "timestamp": time.time(), "data": data}))
            if time.time() - last_hb >= args.heartbeat:
                client.publish(t_hb, json.dumps({"device_id": args.device_id,
                                                 "timestamp": time.time()}))
                last_hb = time.time()
            time.sleep(args.period)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        # Clean shutdown: say so explicitly, then disconnect (no LWT on a clean disconnect).
        client.publish(t_state, json.dumps({"device_id": args.device_id, "state": "inactive",
                                            "reason": "shutdown"}), qos=1, retain=True).wait_for_publish()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
