#!/usr/bin/env python3
"""
Captura de anuncios reales para el conjunto de prueba "de sintético a real".

Step 1: guardar los anuncios de los dispositivos físicos, tal como
llegan por MQTT, para evaluar después el clasificador entrenado con datos
sintéticos contra ellos.

El script se suscribe a <prefijo>/devices/register y agrega cada anuncio a un
archivo JSONL (una línea por anuncio) con:
  * la hora de la Pi (captured_at) y un identificador de sesión;
  * el escenario de la prueba (--scenario) y una nota libre (--note);
  * la placa del catálogo (board_id) y la etiqueta verdadera (label_true),
    tomadas de datasets/real/dispositivos.csv según el id que anuncia el equipo;
  * el anuncio completo (payload), sin modificar.

No clasifica nada: la evaluación se hace después, fuera de línea, para que el
mismo conjunto sirva para cualquier modelo o versión del generador.

Uso (en la Pi 5, desde la carpeta del repositorio):
    python3 experiments/capture_announcements.py --scenario ethernet
    python3 experiments/capture_announcements.py --scenario reset --note "RST x5 Olimex"
    Ctrl+C para terminar: muestra el resumen de la sesión.

El archivo de salida nunca se sobrescribe: cada sesión agrega líneas al final.

Project: PINV01-24 - FIUNA
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

LABELS = ("ESP32Device", "Microcontroller", "Sensor", "SingleBoardComputer",
          "IoTGateway", "GNSSReceiver")
SCENARIO_RE = re.compile(r"^[a-z0-9_]+$")
DEVICE_COLUMNS = ("device_id", "board_id", "label_true")


def now_iso() -> str:
    """Hora local de esta máquina, con zona horaria y milisegundos."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def load_devices(path: Path, catalog: Path = None) -> dict:
    """Lee dispositivos.csv: device_id -> {board_id, label_true}."""
    if not path.exists():
        sys.exit(f"ERROR: no existe {path}. Crealo con las columnas {', '.join(DEVICE_COLUMNS)}.")
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in DEVICE_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            sys.exit(f"ERROR: a {path} le faltan columnas: {missing}")
        devices = {}
        for n, row in enumerate(reader, start=2):
            did = row["device_id"].strip()
            if not did:
                continue
            label = row["label_true"].strip()
            if label not in LABELS:
                sys.exit(f"ERROR: {path} línea {n}: label_true={label!r} no es una de {LABELS}")
            if did in devices:
                sys.exit(f"ERROR: {path} línea {n}: device_id {did!r} repetido")
            devices[did] = {"board_id": row["board_id"].strip(), "label_true": label}

    if catalog is not None:
        with open(catalog, encoding="utf-8", newline="") as fh:
            known = {r["board_id"] for r in csv.DictReader(fh)}
        for did, d in devices.items():
            if d["board_id"] not in known:
                print(f"AVISO: board_id {d['board_id']!r} ({did}) no está en el catálogo {catalog}")
    return devices


def make_client(client_id: str) -> mqtt.Client:
    """Cliente paho compatible con las versiones 1.x y 2.x.

    Los callbacks de abajo aceptan ambas firmas, así que en paho 2.x se usa la
    API nueva (VERSION2) y no aparece el aviso de API obsoleta.
    """
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)


def describe(payload: dict) -> str:
    """Resumen de una línea de los campos que usa el clasificador."""
    if not isinstance(payload, dict):
        return "(anuncio no es un objeto JSON)"
    mem = payload.get("memory_total")
    free = payload.get("memory_free")
    pct = f"{free / mem:.0%}" if isinstance(mem, (int, float)) and mem and isinstance(free, (int, float)) else "?"
    mem_txt = f"{mem / 2**20:.2f} MiB" if isinstance(mem, (int, float)) else "?"
    link = payload.get("link_type") or payload.get("connection_type") or "?"
    caps = payload.get("capabilities")
    extra = f" caps={','.join(map(str, caps))}" if caps else ""
    return f"mem={mem_txt} libre={pct} cpu={payload.get('cpu_freq_mhz', '?')} MHz enlace={link}{extra}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Guarda los anuncios reales de its/devices/register en JSONL.")
    ap.add_argument("--broker", default="127.0.0.1", help="IP del broker MQTT (por defecto, esta máquina)")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--prefix", default="its", help="prefijo de los topics (por defecto its)")
    ap.add_argument("--scenario", required=True,
                    help="escenario de la prueba, en minúsculas: ethernet, wifi, reset, boot, low_mem, gnss, ...")
    ap.add_argument("--note", default="", help="nota libre de la sesión")
    ap.add_argument("--devices", type=Path, default=Path("datasets/real/dispositivos.csv"))
    ap.add_argument("--out", type=Path, default=Path("datasets/real/announcements_real.jsonl"))
    ap.add_argument("--catalog", type=Path, default=None,
                    help="opcional: catalogo_placas.csv para comprobar que cada board_id existe")
    args = ap.parse_args()

    if not SCENARIO_RE.match(args.scenario):
        ap.error("--scenario solo admite minúsculas, dígitos y guion bajo (ej. ethernet, low_mem)")

    devices = load_devices(args.devices, args.catalog)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out = open(args.out, "a", encoding="utf-8")

    session = datetime.now().strftime("%Y%m%dT%H%M%S")
    topic = f"{args.prefix}/devices/register"
    counts = Counter()
    unknown = Counter()

    def on_connect(client, userdata, flags, rc, *extra):
        # rc es un int (paho 1.x) o un ReasonCode (paho 2.x); ambos se comparan con 0.
        if rc == 0:
            client.subscribe(topic, qos=1)
            print(f"[{now_iso()}] Conectado a {args.broker}:{args.port}. Escuchando {topic} "
                  f"(sesión {session}, escenario {args.scenario}). Ctrl+C para terminar.")
        else:
            print(f"[{now_iso()}] ERROR al conectar al broker (rc={rc}).")

    def on_message(client, userdata, msg):
        raw = msg.payload.decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        did = ""
        if isinstance(payload, dict):
            did = str(payload.get("id") or payload.get("device_id") or "")
        dev = devices.get(did, {"board_id": "", "label_true": ""})

        record = {
            "captured_at": now_iso(),
            "session": session,
            "scenario": args.scenario,
            "note": args.note,
            "topic": msg.topic,
            "retained": bool(msg.retain),
            "device_id": did,
            "board_id": dev["board_id"],
            "label_true": dev["label_true"],
            "payload": payload,
        }
        if payload is None:
            record["payload_raw"] = raw
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
        out.flush()

        key = did or "(sin id)"
        counts[key] += 1
        tag = " (retenido: no es un anuncio nuevo)" if msg.retain else ""
        print(f"[{record['captured_at'][11:23]}] {key:22s} {dev['board_id'] or '¿sin placa?':28s} "
              f"{describe(payload)}{tag}")
        if did and did not in devices:
            unknown[did] += 1
            if unknown[did] == 1:
                print(f"   AVISO: {did!r} no está en {args.devices}; se guarda con board_id vacío. "
                      f"Agregalo al CSV y anotá la placa.")

    client = make_client(f"capture-announcements-{session}")
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(args.broker, args.port, keepalive=30)
    except OSError as e:
        out.close()
        sys.exit(f"ERROR: no se pudo conectar a {args.broker}:{args.port} ({e}). ¿Mosquitto está corriendo?")

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        out.close()

    print(f"\nResumen de la sesión {session} (escenario {args.scenario}):")
    if not counts:
        print("  No llegó ningún anuncio.")
    for did, n in sorted(counts.items()):
        board = devices.get(did, {}).get("board_id") or "SIN PLACA"
        print(f"  {did:22s} {board:28s} {n} anuncio(s)")
    print(f"Guardado en {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
