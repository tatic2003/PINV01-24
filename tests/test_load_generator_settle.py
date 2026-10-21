"""
Load generator: during the pause between ramp levels the connections must keep
being attended (network loop), otherwise they miss their keepalive and the
broker drops them.

Project: PINV01-24 - FIUNA
"""

import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'experiments' / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gen_mod = load('load_generator')


class FakeClient:
    """Stands in for EmuClient: always connected, acknowledges at once, records loop() calls."""
    loop_times = []

    def __init__(self, name, host, port, keepalive, prefix):
        self.name = name
        self.connected = True
        self.error = ''
        self.connect_start_ms = 0.0
        self.connack_ms = 1.0
        self.pending = {}

    def connect(self):
        pass

    def publish_announcement(self, msg, row):
        row['publish_ms'] = 1.0
        row['puback_ms'] = 2.0

    def loop(self):
        FakeClient.loop_times.append(time.time())

    def close(self):
        pass


class Writer:
    def __init__(self):
        self.rows = []

    def writerow(self, row):
        self.rows.append(row)


def test_ramp_keeps_connections_attended_during_the_pause(monkeypatch):
    monkeypatch.setattr(gen_mod, 'EmuClient', FakeClient)
    FakeClient.loop_times = []
    args = SimpleNamespace(label='t', host='x', port=1883, keepalive=15, topic_prefix='its', pool=2,
                           rates=[20.0], step_s=0.05, settle=0.4, timeout=1.0)
    writer = Writer()
    gen_mod.run_ramp(args, gen_mod.AnnouncementSource('testbed'), writer)
    end = time.time()
    assert writer.rows, 'the level published nothing'
    # With a plain sleep, the last loop() call would be about `settle` seconds before the end.
    assert end - max(FakeClient.loop_times) < 0.1