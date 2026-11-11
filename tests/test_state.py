# -*- coding: utf-8 -*-
"""状态机纯逻辑自检：心跳格式、探针异常→None（未知不猜）、过期判定、原子写回读。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import state


def probes(**over):
    base = {
        "is_locked": lambda: False,
        "foreground": lambda: {"hwnd": 42, "title": "Claude"},
        "idle_sec": lambda: 3.5,
        "session": lambda: 1,
    }
    base.update(over)
    return base


class TestHeartbeat(unittest.TestCase):
    def test_format(self):
        hb = state.build_heartbeat(probes(), disabled=False, now=1000.0)
        self.assertEqual(hb["schema"], "agent-hand/heartbeat/1")
        self.assertEqual(hb["ts"], 1000.0)
        self.assertTrue(hb["online"])
        self.assertFalse(hb["disabled"])
        self.assertFalse(hb["locked"])
        self.assertEqual(hb["foreground"]["title"], "Claude")
        self.assertEqual(hb["idle_sec"], 3.5)
        self.assertEqual(hb["session"], 1)
        self.assertIn("version", hb)

    def test_probe_failure_becomes_none_not_guess(self):
        def boom():
            raise RuntimeError("探针挂了")
        hb = state.build_heartbeat(probes(is_locked=boom, foreground=boom), now=1.0)
        self.assertIsNone(hb["locked"])
        self.assertIsNone(hb["foreground"])

    def test_missing_probes_none(self):
        hb = state.build_heartbeat({}, now=1.0)
        self.assertIsNone(hb["locked"])
        self.assertIsNone(hb["session"])

    def test_stale(self):
        hb = {"ts": 100.0}
        self.assertFalse(state.is_stale(hb, now=105.0, max_age=10.0))
        self.assertTrue(state.is_stale(hb, now=111.0, max_age=10.0))
        self.assertTrue(state.is_stale(None))
        self.assertTrue(state.is_stale({}))

    def test_atomic_write_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "heartbeat.json")
            hb = state.build_heartbeat(probes(), now=7.0)
            state.write_heartbeat_atomic(path, hb)
            with open(path, encoding="utf-8") as f:
                back = json.load(f)
            self.assertEqual(back["ts"], 7.0)
            self.assertFalse(os.path.exists(path + ".tmp"))


if __name__ == "__main__":
    unittest.main()
