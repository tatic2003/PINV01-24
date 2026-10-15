#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validador del catálogo de placas (PnP-AI, PINV01-24).

FUENTE DE VERDAD: catalogo_placas.csv. Este script NO escribe el CSV ni toca las
columnas de calibración; solo valida y genera resumen_por_clase.md.

Uso:
    python3 build_catalogo.py [ruta/al/catalogo_placas.csv] [--muestra N] [--semilla S]

Comprobaciones:
  * cabecera exacta (orden de columnas);
  * id entero, único y consecutivo desde 1;
  * board_id único, con forma ^[a-z0-9]+(_[a-z0-9]+)*$;
  * clase_clasificador y clase_hw dentro del vocabulario;
  * ram_nominal_bytes / flash_bytes coherentes con ram_nominal_texto / flash_texto
    en base binaria (1 KB = 1024 B);
  * cpu_mhz_nominal entero o vacío; cpu_mhz_options = enteros separados por "|" que
    contienen el nominal (vacío si el nominal está vacío);
  * enlaces: vocabulario wifi|ethernet|bluetooth|lora|zigbee|cellular|serial, sin repetir;
  * alimentacion_poe, muestra_25pct, revisar en {si,no}; confianza en {alta,media,baja};
    coincide_fuente2 en {si,no,parcial,sin segunda fuente,""};
  * fuente_url y fecha_consulta (AAAA-MM-DD) presentes;
  * confianza baja => algún campo clave (cpu o ram) vacío, y viceversa se avisa;
  * columnas de calibración: solo se comprueba que, si hay valor, sea numérico.

Salida: errores (código de salida 1 si hay alguno), avisos, tabla resumen por clase,
lista de filas clase_hw != clase_clasificador y una muestra aleatoria de N filas
NO marcadas como revisar (semilla declarada) para estimar la tasa de error.
"""
import csv, random, re, sys
from collections import Counter
from pathlib import Path

COLS = ["id", "board_id", "placa", "fabricante", "familia", "clase_clasificador", "clase_hw", "hardware_string",
        "cpu_mhz_nominal", "cpu_mhz_options", "ram_nominal_bytes", "ram_nominal_texto", "flash_bytes", "flash_texto",
        "enlaces", "alimentacion_poe", "fuente_url", "fecha_consulta", "confianza", "notas",
        "muestra_25pct", "fuente2_url", "coincide_fuente2", "revisar",
        "memoria_anunciada_bytes_medida", "cpu_mhz_medido", "fecha_medicion", "obs_calibracion",
        "free_pct_lo", "free_pct_hi", "verificado", "nota_verificacion", "enlace_medido"]
CLASES = ("ESP32Device", "SingleBoardComputer", "IoTGateway", "Sensor", "Microcontroller")
ENLACES = ("wifi", "ethernet", "bluetooth", "lora", "zigbee", "cellular", "serial")
UNITS = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}
CALIB = ("memoria_anunciada_bytes_medida", "cpu_mhz_medido", "free_pct_lo", "free_pct_hi")


def to_bytes(txt):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(KB|MB|GB)\s*", txt or "")
    return None if not m else int(round(float(m.group(1)) * UNITS[m.group(2)]))


def fmt(b):
    for u, k in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if b >= k:
            return f"{b / k:g} {u}"
    return f"{b} B"


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    path = Path(args[0]) if args else Path(__file__).with_name("catalogo_placas.csv")
    n_muestra = int(argv[argv.index("--muestra") + 1]) if "--muestra" in argv else 8
    semilla = int(argv[argv.index("--semilla") + 1]) if "--semilla" in argv else 20261006
    errores, avisos = [], []

    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLS:
            errores.append(f"cabecera distinta de la esperada:\n  esperada {COLS}\n  leída    {reader.fieldnames}")
        rows = list(reader)

    ids, bids = [], []
    for i, r in enumerate(rows, start=1):
        tag = f"fila {i} ({r.get('board_id') or r.get('placa')})"
        # id / board_id
        if not r["id"].isdigit() or int(r["id"]) != i:
            errores.append(f"{tag}: id debe ser {i}, es {r['id']!r}")
        ids.append(r["id"])
        if not re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", r["board_id"]):
            errores.append(f"{tag}: board_id inválido {r['board_id']!r}")
        bids.append(r["board_id"])
        # clases
        for c in ("clase_clasificador", "clase_hw"):
            if r[c] not in CLASES:
                errores.append(f"{tag}: {c}={r[c]!r} fuera de {CLASES}")
        # memoria
        for b, t in (("ram_nominal_bytes", "ram_nominal_texto"), ("flash_bytes", "flash_texto")):
            if (r[b] == "") != (r[t] == ""):
                errores.append(f"{tag}: {b} y {t} deben estar ambos vacíos o ambos completos")
            elif r[b]:
                exp = to_bytes(r[t])
                if exp is None:
                    errores.append(f"{tag}: {t}={r[t]!r} no tiene la forma '<n> KB|MB|GB'")
                elif not r[b].isdigit() or int(r[b]) != exp:
                    errores.append(f"{tag}: {b}={r[b]} no coincide con {t}={r[t]!r} (= {exp} en base binaria)")
        # cpu
        cpu = r["cpu_mhz_nominal"]
        if cpu and not cpu.isdigit():
            errores.append(f"{tag}: cpu_mhz_nominal={cpu!r} no es entero")
        opts = r["cpu_mhz_options"]
        if cpu:
            parts = opts.split("|") if opts else []
            if not parts or any(not p.isdigit() for p in parts):
                errores.append(f"{tag}: cpu_mhz_options={opts!r} debe ser enteros separados por '|'")
            elif cpu not in parts:
                errores.append(f"{tag}: cpu_mhz_options={opts!r} no contiene el nominal {cpu}")
        elif opts:
            errores.append(f"{tag}: cpu_mhz_options debe estar vacío si cpu_mhz_nominal está vacío")
        # enlaces
        links = r["enlaces"].split(";") if r["enlaces"] else []
        if not links:
            errores.append(f"{tag}: enlaces vacío")
        for l in links:
            if l not in ENLACES:
                errores.append(f"{tag}: enlace {l!r} fuera del vocabulario {ENLACES}")
        if len(links) != len(set(links)):
            errores.append(f"{tag}: enlaces repetidos")
        # enumerados
        for c in ("alimentacion_poe", "muestra_25pct", "revisar"):
            if r[c] not in ("si", "no"):
                errores.append(f"{tag}: {c}={r[c]!r} debe ser si/no")
        if r["confianza"] not in ("alta", "media", "baja"):
            errores.append(f"{tag}: confianza={r['confianza']!r}")
        if r["coincide_fuente2"] not in ("si", "no", "parcial", "sin segunda fuente", ""):
            errores.append(f"{tag}: coincide_fuente2={r['coincide_fuente2']!r}")
        if r["fuente2_url"] and r["coincide_fuente2"] in ("", "sin segunda fuente"):
            errores.append(f"{tag}: hay fuente2_url pero coincide_fuente2 no está completado")
        # fuente / fecha
        if not r["fuente_url"].startswith("http"):
            errores.append(f"{tag}: fuente_url vacía o inválida")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["fecha_consulta"]):
            errores.append(f"{tag}: fecha_consulta={r['fecha_consulta']!r}")
        # coherencia confianza / campos vacíos
        clave_vacia = (cpu == "" or r["ram_nominal_bytes"] == "")
        if clave_vacia and r["confianza"] != "baja":
            errores.append(f"{tag}: cpu o ram vacíos exigen confianza=baja")
        if clave_vacia and r["revisar"] != "si":
            errores.append(f"{tag}: cpu o ram vacíos exigen revisar=si")
        if r["confianza"] == "baja" and not clave_vacia:
            avisos.append(f"{tag}: confianza baja con cpu y ram completos (revisar criterio)")
        # calibración: solo tipo
        for c in CALIB:
            v = r[c]
            if v and not re.fullmatch(r"\d+(\.\d+)?", v):
                errores.append(f"{tag}: {c}={v!r} debe ser numérico")
        for c in ("free_pct_lo", "free_pct_hi"):
            if r[c] and not 0 <= float(r[c]) <= 100:
                errores.append(f"{tag}: {c}={r[c]} fuera de 0..100")
        if r["free_pct_lo"] and r["free_pct_hi"] and float(r["free_pct_lo"]) > float(r["free_pct_hi"]):
            errores.append(f"{tag}: free_pct_lo > free_pct_hi")
        # enlace con el que se midió la memoria anunciada (v3, 2026-10-15)
        if r["enlace_medido"] and r["enlace_medido"] not in ENLACES:
            errores.append(f"{tag}: enlace_medido {r['enlace_medido']!r} fuera del vocabulario {ENLACES}")
        if r["memoria_anunciada_bytes_medida"] and not r["enlace_medido"]:
            errores.append(f"{tag}: hay memoria medida pero falta enlace_medido")
        if r["enlace_medido"] and r["enlace_medido"] not in r["enlaces"].split(";"):
            errores.append(f"{tag}: enlace_medido {r['enlace_medido']!r} no está en enlaces")

    for b, n in Counter(bids).items():
        if n > 1:
            errores.append(f"board_id repetido: {b} ({n} veces)")

    # --- informe ---------------------------------------------------------
    print(f"Catálogo: {path} — {len(rows)} filas")
    for e in errores:
        print("ERROR:", e)
    for a in avisos:
        print("AVISO:", a)

    lines = ["| clase_clasificador | filas | con CPU | CPU nominal min–max (MHz) | con RAM | RAM nominal min–max | alta/media/baja | revisar |",
             "|---|---|---|---|---|---|---|---|"]
    for c in CLASES:
        rs = [r for r in rows if r["clase_clasificador"] == c]
        cpus = [int(r["cpu_mhz_nominal"]) for r in rs if r["cpu_mhz_nominal"]]
        rams = [int(r["ram_nominal_bytes"]) for r in rs if r["ram_nominal_bytes"]]
        conf = Counter(r["confianza"] for r in rs)
        lines.append(f"| {c} | {len(rs)} | {len(cpus)} | {min(cpus)}–{max(cpus)} | {len(rams)} | "
                     f"{fmt(min(rams))}–{fmt(max(rams))} | {conf['alta']}/{conf['media']}/{conf['baja']} | "
                     f"{sum(1 for r in rs if r['revisar'] == 'si')} |")
    conf = Counter(r["confianza"] for r in rows)
    lines.append(f"| **Total** | {len(rows)} | {sum(1 for r in rows if r['cpu_mhz_nominal'])} | | "
                 f"{sum(1 for r in rows if r['ram_nominal_bytes'])} | | {conf['alta']}/{conf['media']}/{conf['baja']} | "
                 f"{sum(1 for r in rows if r['revisar'] == 'si')} |")
    lines_hw = ["| clase_hw | filas |", "|---|---|"] + [f"| {c} | {sum(1 for r in rows if r['clase_hw'] == c)} |" for c in CLASES]
    diff = [r for r in rows if r["clase_hw"] != r["clase_clasificador"]]
    lines_diff = ["| id | board_id | clase_clasificador | clase_hw |", "|---|---|---|---|"] + \
                 [f"| {r['id']} | {r['board_id']} | {r['clase_clasificador']} | {r['clase_hw']} |" for r in diff]

    cand = [r for r in rows if r["revisar"] == "no"]
    rng = random.Random(semilla)
    muestra = sorted(rng.sample(cand, min(n_muestra, len(cand))), key=lambda r: int(r["id"]))
    lines_m = [f"Semilla {semilla}, {len(muestra)} filas elegidas al azar entre las {len(cand)} con revisar=no "
               "(random.Random(semilla).sample sobre las filas en orden de id).", "",
               "| id | board_id | confianza | fuente_url |", "|---|---|---|---|"] + \
              [f"| {r['id']} | {r['board_id']} | {r['confianza']} | {r['fuente_url']} |" for r in muestra]

    md = ("# Resumen por clase (generado por build_catalogo.py)\n\n"
          "RAM y flash en base binaria (1 KB = 1024 B). Los rangos solo consideran filas con el valor verificado.\n\n"
          "## Por clase_clasificador\n\n" + "\n".join(lines) +
          "\n\n## Por clase_hw\n\n" + "\n".join(lines_hw) +
          f"\n\n## Filas con clase_hw distinta de clase_clasificador ({len(diff)})\n\n" + "\n".join(lines_diff) +
          "\n\n## Muestra aleatoria para estimar la tasa de error\n\n" + "\n".join(lines_m) + "\n")
    out = path.with_name("resumen_por_clase.md")
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"Resumen escrito en {out}")
    print(f"{len(errores)} errores, {len(avisos)} avisos")
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
