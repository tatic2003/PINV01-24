"""
experiments/eval_synth_to_real.py: the name-based rule baseline on the real
announcements is computed by the script (not by hand) and scored like the
learned models.

Project: PINV01-24 - FIUNA
"""

import csv
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("eval_synth_to_real", ROOT / "experiments" / "eval_synth_to_real.py")
ev = importlib.util.module_from_spec(spec)
sys.modules["eval_synth_to_real"] = ev
spec.loader.exec_module(ev)


def write_real(d, announcements, devices):
    d.mkdir()
    with open(d / "dispositivos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["device_id", "board_id", "label_true", "notas"])
        w.writerows(devices)
    (d / "exclusiones.csv").write_text("captured_at,device_id,motivo\n", encoding="utf-8")
    with open(d / "announcements_real.jsonl", "w", encoding="utf-8") as f:
        for a in announcements:
            f.write(json.dumps(a) + "\n")


def ann(dev, payload, retained=False):
    return {"captured_at": "t", "device_id": dev, "retained": retained, "payload": payload}


def test_rule_baseline_rows(tmp_path):
    esp = {"id": "e1", "hardware": "ESP32-C3-Zero", "link_type": "wifi", "memory_total": 295580,
           "memory_free": 190000, "cpu_freq_mhz": 160}
    opi = {"id": "o1", "hardware": "Xunlong Orange Pi Zero", "link_type": "ethernet",
           "memory_total": 508035072, "memory_free": 420000000, "cpu_freq_mhz": 1008}
    write_real(tmp_path / "real",
               [ann("e1", esp), ann("e1", esp), ann("o1", opi), ann("o1", opi, retained=True)],
               [["e1", "waveshare_esp32_c3_zero", "ESP32Device", ""],
                ["o1", "orange_pi_zero_lts_512mb", "SingleBoardComputer", ""]])
    records, total = ev.load_real(tmp_path / "real")
    assert total == 4 and len(records) == 3          # the retained one is dropped
    all_rows = ev.rule_rows(records, ["hw"])
    assert {r["model"] for r in all_rows} == {"rules", "rules_noname"}
    assert all(r["generator"] == "rules" and r["seed"] == 0 for r in all_rows)
    rows = [r for r in all_rows if r["model"] == "rules"]
    by_dev = {r["device_id"]: r for r in rows}
    assert set(by_dev) == {"e1", "o1"}
    assert by_dev["e1"]["correct"] == 1
    # No known keyword in its name and under 1 GB: the memory fallback says ESP32Device
    assert by_dev["o1"]["pred"] == "ESP32Device" and by_dev["o1"]["correct"] == 0
    # Without its name the ESP32 falls to the memory thresholds (under 1 MB -> Microcontroller)
    nameless = {r["device_id"]: r for r in all_rows if r["model"] == "rules_noname"}
    assert nameless["e1"]["pred"] == "Microcontroller" and nameless["e1"]["correct"] == 0


def test_rule_baseline_on_the_repository_data():
    records, _ = ev.load_real(ROOT / "datasets" / "real")
    rows = ev.rule_rows(records, ["hw"])
    wrong = {r["device_id"]: r["pred"] for r in rows if not r["correct"]}
    assert wrong.get("sbc-opizero-01") == "ESP32Device"


def test_rule_baseline_keeps_the_captured_payloads(tmp_path):
    records, _ = ev.load_real(ROOT / "datasets" / "real")
    before = [dict(r["payload"]) for r in records]
    ev.rule_rows(records, ["hw"])
    assert [r["payload"] for r in records] == before
