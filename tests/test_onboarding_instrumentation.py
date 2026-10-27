"""
Tests for the onboarding-latency instrumentation
Project: PINV01-24 - FIUNA

Covers the server's stage timings, the bridge (removes the "timing" block
before forwarding and writes the onboarding CSV), the clock-probe estimates
and the stage arithmetic of experiments/analyze_onboarding.py.
"""

import csv
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TIMING = {
    "seq": 1, "synced": True, "setup_ms": 300, "serial_ms": 1300, "link_ms": 3300,
    "sync_ms": 3500, "sync_rtt_ms": 2.5, "mqtt_ms": 3700, "publish_ms": 3750,
    "epoch_publish_ms": "1760550000000",
}

ANNOUNCEMENT = {
    "id": "esp32-c3-abc123", "hardware": "ESP32-C3-Zero", "ip": "192.168.0.40",
    "location": "Lab-Test", "link_type": "wifi", "protocol": "mqtt",
    "connection_type": "wifi", "memory_total": 295580, "memory_free": 200000,
    "cpu_freq_mhz": 160,
}


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    from server.app import create_app
    from server.config import Config

    config = Config(debug=False, data_dir=str(tmp_path / "data"), model_dir=str(tmp_path / "models"),
                    auto_train=False, classifier_type="rule_based", enable_dag=True,
                    node_id="test-node")
    app = create_app(config)
    app.config["TESTING"] = True
    return app.test_client()


class TestServerTimings:
    def test_register_returns_stage_timings(self, client):
        before = time.time() * 1000.0
        resp = client.post("/api/devices/register", json=dict(ANNOUNCEMENT))
        assert resp.status_code == 200
        body = resp.get_json()
        timing = body["timing_ms"]
        for key in ("classify", "semantic", "dag", "total"):
            assert timing[key] >= 0.0
        assert timing["total"] >= timing["classify"] + timing["semantic"] + timing["dag"] - 1e-6
        assert before - 1.0 <= body["server_recv_epoch_ms"] <= time.time() * 1000.0 + 1.0

    def test_register_response_keeps_existing_fields(self, client):
        body = client.post("/api/devices/register", json=dict(ANNOUNCEMENT)).get_json()
        assert body["status"] == "registered"
        assert "classification" in body and "low_confidence" in body


# --------------------------------------------------------------------------
# Bridge
# --------------------------------------------------------------------------

class FakeResponse:
    ok = True
    status_code = 200
    text = ""

    def json(self):
        return {"classification": {"device_type": "ESP32Device", "confidence": 1.0},
                "server_recv_epoch_ms": 1760550000150.0,
                "timing_ms": {"classify": 1.0, "semantic": 2.0, "dag": 0.5, "total": 4.0}}


class TestBridge:
    def make(self, tmp_path, monkeypatch):
        bridge = load_module("bridge_under_test", "mqtt_http_bridge.py")
        sent = []

        def fake_post(url, timeout=5, **kwargs):
            sent.append((url, kwargs.get("json")))
            return FakeResponse()

        monkeypatch.setattr(bridge.requests, "post", fake_post)
        events = bridge.EventLog(str(tmp_path / "events.csv"))
        onboarding = bridge.EventLog(str(tmp_path / "onb.csv"), bridge.ONBOARDING_COLUMNS)
        handler = bridge.make_on_message("http://server", events, onboarding, "solo_c3")
        return bridge, handler, sent

    def message(self, payload):
        return SimpleNamespace(topic="its/devices/register",
                               payload=json.dumps(payload).encode("utf-8"),
                               timestamp=time.monotonic())

    def test_timing_block_is_not_forwarded(self, tmp_path, monkeypatch):
        _, handler, sent = self.make(tmp_path, monkeypatch)
        handler(None, None, self.message(dict(ANNOUNCEMENT, timing=dict(TIMING))))
        assert len(sent) == 1
        assert "timing" not in sent[0][1]
        assert sent[0][1] == ANNOUNCEMENT

    def test_onboarding_row_is_written(self, tmp_path, monkeypatch):
        _, handler, _ = self.make(tmp_path, monkeypatch)
        handler(None, None, self.message(dict(ANNOUNCEMENT, timing=dict(TIMING))))
        rows = list(csv.DictReader(open(tmp_path / "onb.csv", encoding="utf-8")))
        assert len(rows) == 1
        row = rows[0]
        assert row["session"] == "solo_c3"
        assert row["device_id"] == "esp32-c3-abc123"
        assert row["link_ms"] == "3300" and row["epoch_publish_ms"] == "1760550000000"
        assert row["sync_rtt_ms"] == "2.5"
        assert row["srv_total_ms"] == "4.0" and row["device_type"] == "ESP32Device"
        assert float(row["callback_epoch_ms"]) >= float(row["arrival_epoch_ms"])

    def test_session_from_timing_block_overrides_bridge_session(self, tmp_path, monkeypatch):
        # A load generator labels every load level in the timing block.
        _, handler, _ = self.make(tmp_path, monkeypatch)
        handler(None, None, self.message(dict(ANNOUNCEMENT, timing=dict(TIMING, session="load_pi5_burst_N10"))))
        row = list(csv.DictReader(open(tmp_path / "onb.csv", encoding="utf-8")))[0]
        assert row["session"] == "load_pi5_burst_N10"

    def test_announcement_without_timing_writes_no_onboarding_row(self, tmp_path, monkeypatch):
        _, handler, sent = self.make(tmp_path, monkeypatch)
        handler(None, None, self.message(dict(ANNOUNCEMENT)))
        assert sent[0][1] == ANNOUNCEMENT
        assert not (tmp_path / "onb.csv").read_text(encoding="utf-8").strip().splitlines()[1:]

    def test_message_without_paho_timestamp(self, tmp_path, monkeypatch):
        bridge, _, _ = self.make(tmp_path, monkeypatch)
        msg = SimpleNamespace(topic="x", payload=b"{}")
        assert bridge._arrival_epoch_ms(msg, 123.0) == 123.0


# --------------------------------------------------------------------------
# Clock probe
# --------------------------------------------------------------------------

class TestClockProbe:
    def setup_method(self):
        self.cp = load_module("clock_probe_under_test", "experiments/clock_probe.py")

    def test_symmetric_paths_give_exact_offset(self):
        # Device clock 7 ms ahead; 3 ms each way; 1 ms inside the device.
        t1 = 1000.0
        t2 = t1 + 3 + 7
        t3 = t2 + 1
        t4 = (t3 - 7) + 3
        offset, delay = self.cp.probe_estimates(t1, t2, t3, t4)
        assert offset == pytest.approx(7.0)
        assert delay == pytest.approx(6.0)

    def test_asymmetric_paths_stay_within_bound(self):
        true_offset = -4.0
        t1 = 0.0
        t2 = t1 + 9 + true_offset     # slow way in
        t3 = t2 + 2
        t4 = (t3 - true_offset) + 1   # fast way back
        offset, delay = self.cp.probe_estimates(t1, t2, t3, t4)
        assert abs(offset - true_offset) <= delay / 2 + 1e-9

    def test_summary_uses_minimum_delay_probe(self):
        rows = [
            {"device_id": "d", "answered": True, "offset_ms": 5.0, "delay_ms": 20.0, "synced": True},
            {"device_id": "d", "answered": True, "offset_ms": 2.0, "delay_ms": 4.0, "synced": True},
            {"device_id": "d", "answered": False, "offset_ms": "", "delay_ms": "", "synced": ""},
        ]
        s = self.cp.summarize(rows)["d"]
        assert s["n"] == 2
        assert s["best_offset_ms"] == 2.0
        assert s["bound_ms"] == 2.0 + self.cp.RESOLUTION_MS


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

class TestAnalysis:
    def setup_method(self):
        self.an = load_module("analyze_onboarding_under_test", "experiments/analyze_onboarding.py")

    def frame(self, **overrides):
        import pandas as pd
        row = {k: str(v) for k, v in TIMING.items()}
        row.update(session="s", device_id="esp32-c3-abc123", hardware="ESP32-C3-Zero",
                   arrival_epoch_ms="1760550000040", callback_epoch_ms="1760550000045",
                   http_status="200", http_rtt_ms="10", srv_classify_ms="1", srv_semantic_ms="2",
                   srv_dag_ms="0.5", srv_total_ms="4")
        row.update({k: str(v) for k, v in overrides.items()})
        return pd.DataFrame([row])

    def test_esp32_stages(self):
        st = self.an.compute_stages(self.frame()).iloc[0]
        assert st["device_type"] == "ESP32-C3-Zero"
        assert st["boot_to_setup"] == 300 and st["serial_wait"] == 1000
        assert st["link"] == 2000 and st["sntp"] == 200
        assert st["sync_rtt_ms"] == 2.5
        assert st["mqtt_connect"] == 200 and st["announce"] == 50
        assert st["transit"] == pytest.approx(40) and st["bridge_queue"] == pytest.approx(5)
        assert st["http"] == pytest.approx(6) and st["srv_other"] == pytest.approx(0.5)
        assert st["publish_to_registered"] == pytest.approx(55)
        # boot total excludes the SNTP step
        assert st["boot_to_registered"] == pytest.approx(300 + 1000 + 2000 + 200 + 50 + 55)

    def test_linux_stages(self):
        import pandas as pd
        df = pd.DataFrame([{
            "session": "s", "device_id": "sbc-opizero-01", "hardware": "Orange Pi Zero",
            "seq": "1", "agent_start_epoch_ms": "1000", "mqtt_epoch_ms": "1300",
            "epoch_publish_ms": "1310", "uptime_s": "42.5", "arrival_epoch_ms": "1320",
            "callback_epoch_ms": "1321", "http_status": "200", "http_rtt_ms": "8",
            "srv_classify_ms": "1", "srv_semantic_ms": "2", "srv_dag_ms": "0.5", "srv_total_ms": "4",
        }])
        st = self.an.compute_stages(df).iloc[0]
        assert st["device_type"] == "Orange Pi Zero"
        assert st["agent_to_mqtt"] == 300 and st["mqtt_to_publish"] == 10
        assert st["transit"] == 10
        assert st["publish_to_registered"] == pytest.approx(10 + 1 + 4 + 4)

    def test_trials_split_on_gap(self):
        import pandas as pd
        frames = [self.frame(arrival_epoch_ms=t) for t in
                  ("1760550000000", "1760550001000", "1760550060000")]
        st = self.an.find_trials(self.an.compute_stages(pd.concat(frames, ignore_index=True)), 20.0)
        assert list(st["trial"]) == [1, 1, 2]

    def test_end_to_end_outputs(self, tmp_path):
        import pandas as pd
        src = tmp_path / "onb.csv"
        pd.concat([self.frame(), self.frame(seq="2")], ignore_index=True).to_csv(src, index=False)
        probe = tmp_path / "probe.csv"
        pd.DataFrame([{"device_id": "esp32-c3-abc123", "answered": "True", "offset_ms": "1.5",
                       "delay_ms": "6"}]).to_csv(probe, index=False)
        out = tmp_path / "out"
        sys.argv = ["analyze_onboarding.py", str(src), "--probe", str(probe), "--out", str(out)]
        self.an.main()
        summary = pd.read_csv(out / "summary.csv")
        total = summary[summary["stage"] == "boot_to_registered"].iloc[0]
        assert total["n"] == 1                       # seq == 2 dropped
        assert total["clock_bound_ms"] == 3.0 + self.an.RESOLUTION_MS
        assert (out / "fig_onboarding_stages.pdf").exists()

    def test_several_probe_files(self, tmp_path):
        """Probes of several sessions are joined; each device keeps its smallest delay."""
        import pandas as pd
        src = tmp_path / "onb.csv"
        self.frame().to_csv(src, index=False)
        probe_a = tmp_path / "probe_a.csv"
        probe_b = tmp_path / "probe_b.csv"
        pd.DataFrame([{"session": "a", "device_id": "esp32-c3-abc123", "answered": "True",
                       "offset_ms": "1.5", "delay_ms": "10"}]).to_csv(probe_a, index=False)
        pd.DataFrame([{"session": "b", "device_id": "esp32-c3-abc123", "answered": "True",
                       "offset_ms": "0.5", "delay_ms": "4"},
                      {"session": "b", "device_id": "esp32-c3-def456", "answered": "False",
                       "offset_ms": "", "delay_ms": ""}]).to_csv(probe_b, index=False)
        out = tmp_path / "out"
        sys.argv = ["analyze_onboarding.py", str(src), "--probe", str(probe_a), str(probe_b),
                    "--out", str(out)]
        self.an.main()
        bounds = pd.read_csv(out / "clock_bounds.csv")
        assert len(bounds) == 1                      # unanswered probe dropped
        row = bounds.iloc[0]
        assert row["probes"] == 2
        assert row["bound_ms"] == 2.0 + self.an.RESOLUTION_MS
        assert row["best_offset_ms"] == 0.5
        summary = pd.read_csv(out / "summary.csv")
        assert summary["clock_bound_ms"].dropna().iloc[0] == 2.0 + self.an.RESOLUTION_MS


def test_analysis_serial_agent_stages(tmp_path):
    """Serial-board rows get their USB stages from the agent CSV, joined on the publish time."""
    import pandas as pd
    from experiments.analyze_onboarding import compute_stages, add_serial_agent

    bridge = pd.DataFrame([{
        "session": "uno", "device_id": "mcu-uno-01", "hardware": "Arduino Uno R3 (ATmega328P)",
        "seq": "1", "synced": "", "setup_ms": "0", "epoch_publish_ms": "1000.5",
        "agent_start_epoch_ms": "0", "mqtt_epoch_ms": "20", "arrival_epoch_ms": "1001.0",
        "callback_epoch_ms": "1001.1", "http_status": "200", "http_rtt_ms": "10",
        "srv_classify_ms": "6", "srv_semantic_ms": "0.6", "srv_dag_ms": "0.4", "srv_total_ms": "7.5",
    }])
    agent = tmp_path / "agent.csv"
    agent.write_text("device_id,usb_seen_epoch_ms,serial_open_epoch_ms,announce_rx_epoch_ms,epoch_publish_ms\n"
                     "mcu-uno-01,0,18,1000.45,1000.5\n")
    st = add_serial_agent(compute_stages(bridge), bridge, [str(agent)])
    row = st.iloc[0]
    assert row["device_type"] == "Arduino Uno R3"
    assert row["usb_to_open"] == 18
    assert abs(row["board_boot"] - 982.45) < 1e-6
    assert pd.isna(row["agent_to_mqtt"]) and pd.isna(row["boot_to_setup"])
    assert abs(row["boot_to_registered"] - (1000.5 + row["publish_to_registered"])) < 1e-6
