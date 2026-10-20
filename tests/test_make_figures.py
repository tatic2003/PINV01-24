"""
figures/make_figures.py: the values drawn are computed from the raw data, the
two records of the TTFF agree, the Last-Will timing follows the model of the
article, and figures without data are skipped.

Project: PINV01-24 - FIUNA
"""

import csv
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("make_figures", ROOT / "figures" / "make_figures.py")
mf = importlib.util.module_from_spec(spec)
sys.modules["make_figures"] = mf
spec.loader.exec_module(mf)

CONFIG = yaml.safe_load(open(ROOT / "figures" / "config.yml", encoding="utf-8"))


def write_csv(p, rows):
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return p


class TestTTFF:
    LOG = ["# corrida 1 tipo=frio",
           "17:02:03.826 $GNGGA,170203.719,,,,,0,0,,,M,,M,,*5E",
           "17:02:03.942 $GNRMC,170203.719,V,,,,,0.00,0.00,121026,,,N*5D",
           "17:02:20.942 $GNGGA,170220.587,,,,,1,07,1.17,132.3,M,15.1,M,,*7C",
           "17:02:21.125 $GNRMC,170220.587,A,,,,,0.14,0.00,121026,,,A*7B",
           "# corrida 2 tipo=frio",
           "17:03:00.000 $GNGGA,170300.000,,,,,0*79",   # cut by the serial port: ignored
           ]

    def test_fix_needs_rmc_a_and_gga_quality(self, tmp_path):
        p = tmp_path / "log.txt"
        p.write_text("\n".join(self.LOG) + "\n", encoding="utf-8")
        runs = mf.read_nmea_runs(p)
        assert sorted(runs) == [1, 2]
        start = datetime(2026, 10, 12, 17, 2, 2, 414580, tzinfo=timezone.utc)
        assert mf.ttff_from_log(start, runs[1]) == pytest.approx(18.710, abs=1e-3)
        assert mf.ttff_from_log(start, runs[2]) is None

    def test_csv_and_log_must_agree(self, tmp_path):
        log = tmp_path / "log.txt"
        log.write_text("\n".join(self.LOG[:5]) + "\n", encoding="utf-8")
        row = dict(corrida=1, tipo="frio", antena_etiqueta="externa", pgtop="",
                   inicio_utc="2026-10-12T17:02:02.414580+00:00", ttff_s="18.7",
                   calidad_gga=1, satelites="07", hdop="1.17", nota="")
        good = write_csv(tmp_path / "good.csv", [row])
        cfg = {"ttff": {"runs": [{"label": "Cold", "csv": str(good), "nmea": str(log)}], "tolerance_s": 0.15}}
        made = mf.fig_gnss_ttff(cfg, tmp_path / "out")
        assert (tmp_path / "out" / "fig_gnss_ttff.pdf").exists()
        assert len(made["fig_gnss_ttff"]) == 2
        bad = write_csv(tmp_path / "bad.csv", [dict(row, ttff_s="25.0")])
        cfg["ttff"]["runs"][0]["csv"] = str(bad)
        with pytest.raises(mf.DataMismatch):
            mf.fig_gnss_ttff(cfg, tmp_path / "out")


class TestLastWill:
    def test_last_packet_is_the_latest_of_message_and_estimated_ping(self, tmp_path):
        # Connected at 10:00:00.000 with K = 5 s: PINGREQs at :05, :10, :15 ...
        # A heartbeat at :12.000 is later than the ping at :10; the cut is at
        # :13.000, so the last packet is the heartbeat (1.0 s before the cut).
        bridge = write_csv(tmp_path / "bridge.csv", [
            dict(ts_iso="2026-10-06T10:00:00.000-03:00", device_id="d", event="state", detail="state=active"),
            dict(ts_iso="2026-10-06T10:00:12.000-03:00", device_id="d", event="heartbeat", detail="rssi="),
            dict(ts_iso="2026-10-06T10:00:12.500-03:00", device_id="other", event="heartbeat", detail=""),
        ])
        trials = write_csv(tmp_path / "trials.csv", [
            dict(trial=1, valid="True", t_cut="2026-10-06T10:00:13.000", t_lwt="2026-10-06T10:00:19.700",
                 detection_s=6.7),
            # cut at :17: last packet is the estimated ping at :15
            dict(trial=2, valid="True", t_cut="2026-10-06T10:00:17.000", t_lwt="2026-10-06T10:00:22.400",
                 detection_s=5.4),
            dict(trial=3, valid="False", t_cut="2026-10-06T10:00:30.000", t_lwt="2026-10-06T10:00:31.000",
                 detection_s=1.0),
        ])
        rows = mf.lwt_rows(dict(board="B", device_id="d", keepalive_s=5, trials=trials, bridge=bridge))
        assert [r["trial"] for r in rows] == [1, 2]
        assert rows[0]["since_last_packet_s"] == pytest.approx(1.0)
        assert rows[0]["residual_s"] == pytest.approx(19.7 - (12.0 + 7.5))
        assert rows[1]["since_last_packet_s"] == pytest.approx(2.0)
        assert rows[1]["model_s"] == pytest.approx(15.0 + 7.5 - 17.0)

    def test_repository_data_give_the_numbers_of_the_article(self):
        rows = []
        for s in CONFIG["lwt_ethernet"]["series"]:
            rows += mf.lwt_rows(dict(s, trials=ROOT / s["trials"], bridge=ROOT / s["bridge"]))
        assert len(rows) == 40
        res = sorted(r["residual_s"] for r in rows)
        within = [r for r in res if -0.4 <= r <= 0.9]
        assert len(within) == 39
        assert res[0] == pytest.approx(-0.37, abs=0.006) and res[-1] == pytest.approx(1.41, abs=0.006)


class TestMain:
    def test_repository_figures_and_manifest(self, tmp_path):
        out = tmp_path / "out"
        code = mf.main(["fig_gnss_ttff", "fig_clock_error", "fig_nmea_residual", "fig_gnss_device",
                        "fig_lwt_detection", "--out", str(out)])
        assert code == 0
        for name in ("fig_gnss_ttff", "fig_clock_error", "fig_nmea_residual", "fig_gnss_device",
                     "fig_lwt_detection"):
            assert (out / f"{name}.pdf").exists() and (out / f"{name}.png").exists()
        manifest = list(csv.DictReader(open(out / "MANIFEST.csv", encoding="utf-8")))
        inputs = {m["input"] for m in manifest if m["figure"] == "fig_lwt_detection"}
        assert "figures/data/lwt_ethernet/bridge_poecam.csv" in inputs and len(inputs) == 6
        assert all(len(m["sha256"]) == 64 for m in manifest)

    def test_missing_data_is_skipped_or_fails_with_strict(self, tmp_path):
        cfg = dict(CONFIG, convergence={"runs": "figures/data/nowhere/runs.csv",
                                        "agents": [{"name": "a", "csv": "figures/data/nowhere/a.csv"}]})
        p = tmp_path / "config.yml"
        p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        assert mf.main(["fig_convergence", "--config", str(p), "--out", str(tmp_path / "o")]) == 0
        assert not (tmp_path / "o" / "fig_convergence.pdf").exists()
        assert mf.main(["fig_convergence", "--config", str(p), "--out", str(tmp_path / "o"), "--strict"]) == 1

    def test_alias_and_unknown_names(self, tmp_path):
        assert mf.ALIASES["fig_baseline_latency"] == "fig_baseline"
        with pytest.raises(SystemExit):
            mf.main(["fig_nope", "--out", str(tmp_path)])
