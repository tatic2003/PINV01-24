#!/usr/bin/env python3
"""
Linux board agent for IoT-ITS (Raspberry Pi, Orange Pi, any Linux SBC).

Announces the board itself as a device of the ITS network, with the same MQTT
contract as the ESP32 firmware and devices/gnss_agent.py:

  its/devices/register            announcement, on every (re)connection
  its/devices/<id>/state          retained; Last Will = {"state":"inactive"}
  its/devices/<id>/heartbeat      every --heartbeat seconds

What the board announces (same rule as gnss_agent.host_resources):
  memory_total  = MemTotal from /proc/meminfo, in bytes
  memory_free   = MemAvailable (MemFree if the kernel has no MemAvailable)
  cpu_freq_mhz  = cpuinfo_max_freq of cpu0 (maximum, not the current frequency)
  link_type     = physical link of the default route: wifi if the interface is
                  wireless, ethernet otherwise (override with --link)
  hardware      = /proc/device-tree/model (override with --hardware)
No "capabilities" field: a plain SBC does not declare GNSS.

For the onboarding-latency experiment every announcement also carries a
"timing" block (agent start, MQTT connection and publication, as epoch ms of
the system clock, plus the seconds since the OS booted). The MQTT->HTTP bridge
removes it before forwarding, so the server sees the usual announcement. The
system clock must be synchronized (chrony against the Pi 5) for these marks to
be comparable with the bridge's; --no-timing leaves the block out.

The file is self-contained (only needs paho-mqtt), so it can be copied as is
to a board that does not have the repository, such as the Orange Pi.

Usage:
  python3 devices/linux_agent.py                       # broker on this board
  python3 devices/linux_agent.py --mqtt-host 192.168.0.21
  python3 devices/linux_agent.py --once                # announce once and exit

Project: PINV01-24 - FIUNA
"""

import argparse
import json
import logging
import os
import socket
import time

import paho.mqtt.client as mqtt

log = logging.getLogger("linux_agent")


def build_client(client_id):
    """Create an MQTT client on paho 1.x or 2.x (callbacks below accept both)."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
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
    """Memory and CPU of this board, with the same rule as gnss_agent."""
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


def board_model():
    """Model string from the device tree, e.g. 'Raspberry Pi 5 Model B Rev 1.0'."""
    try:
        with open("/proc/device-tree/model", "rb") as f:
            return f.read().replace(b"\x00", b"").decode("utf-8", "replace").strip()
    except OSError:
        return ""


def default_interface():
    """Network interface of the default route (from /proc/net/route)."""
    try:
        with open("/proc/net/route") as f:
            next(f)
            for line in f:
                fields = line.split()
                if len(fields) > 1 and fields[1] == "00000000":
                    return fields[0]
    except (OSError, StopIteration):
        pass
    return ""


def detect_link(iface):
    """'wifi' if the interface is wireless, 'ethernet' if it is wired, '' if unknown."""
    if not iface:
        return ""
    if os.path.isdir(f"/sys/class/net/{iface}/wireless") or os.path.exists(f"/sys/class/net/{iface}/phy80211"):
        return "wifi"
    if iface.startswith(("eth", "en", "end")):
        return "ethernet"
    return ""


def epoch_ms():
    return round(time.time() * 1000.0, 3)


def uptime_s():
    """Seconds since the OS booted, or None where /proc/uptime does not exist."""
    try:
        with open("/proc/uptime") as f:
            return round(float(f.read().split()[0]), 2)
    except (OSError, ValueError, IndexError):
        return None


def main():
    agent_start = epoch_ms()
    default_id = "sbc-" + socket.gethostname().split(".")[0].lower()
    ap = argparse.ArgumentParser(description="Linux board agent for IoT-ITS")
    ap.add_argument("--device-id", default=default_id, help=f"default: {default_id}")
    ap.add_argument("--hardware", default="", help="default: /proc/device-tree/model")
    ap.add_argument("--link", choices=["wifi", "ethernet"], default=None,
                    help="default: detected from the interface of the default route")
    ap.add_argument("--mqtt-host", default="localhost")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--topic-prefix", default="its")
    ap.add_argument("--location", default="Lab-Test")
    ap.add_argument("--keepalive", type=int, default=15)
    ap.add_argument("--heartbeat", type=float, default=30.0)
    ap.add_argument("--once", action="store_true", help="announce once, then disconnect and exit")
    ap.add_argument("--no-timing", action="store_true",
                    help="do not add the 'timing' block used by the onboarding-latency experiment")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    hardware = args.hardware or board_model() or "Linux-SBC"
    iface = default_interface()
    link = args.link or detect_link(iface)
    if not link:
        log.warning("could not detect the link of interface %r; pass --link wifi|ethernet", iface)

    base = f"{args.topic_prefix}/devices"
    t_register, t_state = f"{base}/register", f"{base}/{args.device_id}/state"
    t_hb = f"{base}/{args.device_id}/heartbeat"

    client = build_client(f"linux-{args.device_id}")
    client.will_set(t_state, json.dumps({"device_id": args.device_id, "state": "inactive", "reason": "lwt"}),
                    qos=1, retain=True)
    connected = {"ok": False, "seq": 0, "mqtt_epoch_ms": None}

    def announce():
        msg = {"id": args.device_id, "hardware": hardware, "ip": local_ip(),
               "location": args.location, "link_type": link or None, "protocol": "mqtt",
               "connection_type": link or "mqtt", **host_resources()}
        msg = {k: v for k, v in msg.items() if v is not None}
        if not args.no_timing:
            connected["seq"] += 1
            msg["timing"] = {
                "seq": connected["seq"],
                "agent_start_epoch_ms": agent_start,
                "mqtt_epoch_ms": connected["mqtt_epoch_ms"],
                "uptime_s": uptime_s(),
                "epoch_publish_ms": epoch_ms(),   # last: as close to the publish as possible
            }
        info = client.publish(t_register, json.dumps(msg), qos=1)
        client.publish(t_state, json.dumps({"device_id": args.device_id, "state": "active"}), qos=1, retain=True)
        log.info("announced %s: hardware=%r link=%s (%s) mem=%s free=%s cpu=%s MHz",
                 args.device_id, hardware, link or "?", iface or "?", msg.get("memory_total"),
                 msg.get("memory_free"), msg.get("cpu_freq_mhz"))
        return info

    def on_connect(c, userdata, flags, rc, *extra):
        # rc is an int (paho 1.x) or a ReasonCode (paho 2.x); both compare with 0.
        if rc == 0:
            connected["ok"] = True
            connected["mqtt_epoch_ms"] = epoch_ms()
            announce()   # like the ESP32 firmware: register again after every reconnection
        else:
            log.error("broker refused the connection (rc=%s)", rc)

    client.on_connect = on_connect
    try:
        client.connect(args.mqtt_host, args.mqtt_port, keepalive=args.keepalive)
    except OSError as exc:
        log.error("cannot reach the broker at %s:%s (%s)", args.mqtt_host, args.mqtt_port, exc)
        return 1
    client.loop_start()

    try:
        if args.once:
            deadline = time.time() + 10
            while not connected["ok"] and time.time() < deadline:
                time.sleep(0.1)
            if not connected["ok"]:
                log.error("no connection to the broker after 10 s")
                return 1
            time.sleep(1.0)   # let the register message go out
        else:
            last_hb = time.time()
            while True:
                if connected["ok"] and time.time() - last_hb >= args.heartbeat:
                    client.publish(t_hb, json.dumps({"device_id": args.device_id, "timestamp": time.time()}))
                    last_hb = time.time()
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        # Clean shutdown: say so explicitly, then disconnect (no LWT on a clean disconnect).
        client.publish(t_state, json.dumps({"device_id": args.device_id, "state": "inactive",
                                            "reason": "shutdown"}), qos=1, retain=True).wait_for_publish(timeout=5)
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
