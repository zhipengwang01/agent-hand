# -*- coding: utf-8 -*-
"""执行器自检（假 CDP/假抓屏/假输入）：observe 拒绝、顺序与停错、shot 落盘、锁屏拒绝、审计落行。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import actions
import audit as audit_mod
import winutil
from PIL import Image


class FakeBridge:
    """假 CDP 桥：记录调用，find 总命中。"""
    def __init__(self):
        self.calls = []

    def connect(self, url):
        self.calls.append(("connect", url))
        return {"connected": True, "title": "t", "url": url}

    def navigate(self, url):
        self.calls.append(("navigate", url))
        return {"navigated": url}

    def find(self, selector=None, text=None):
        self.calls.append(("find", selector, text))
        return "sess-1", {"found": True, "x": 10, "y": 20, "tag": "BUTTON", "text": "发送"}

    def click_found(self, sid, hit):
        self.calls.append(("click", sid))
        return {"clicked": True, "via": "mouse", "x": hit["x"], "y": hit["y"]}

    def insert_text(self, text):
        self.calls.append(("insert", text))
        return {"inserted": len(text)}

    def key(self, k):
        self.calls.append(("key", k))
        return {"key": k}

    def set_files(self, selector, files):
        self.calls.append(("files", selector, tuple(files)))
        return {"files_set": len(files)}


def make_executor(tmp, observe=False):
    cfg = {
        "data_dir": tmp,
        "observe_only": observe,
        "sensitive_title_words": ["password", "密码"],
    }
    audit = audit_mod.AuditLog(tmp)
    bridge = FakeBridge()
    pasted, pressed, clicked = [], [], []
    ex = actions.Executor(
        cfg, audit, cdp_bridge=bridge,
        grabber=lambda bbox=None: Image.new("RGB", (64, 48), (120, 30, 30)),
        paster=lambda t: pasted.append(t) or len(t),
        keyer=lambda k: pressed.append(k),
        clicker=lambda x, y: clicked.append((x, y)),
    )
    return ex, bridge, (pasted, pressed, clicked)


class TestExecutor(unittest.TestCase):
    def setUp(self):
        self._orig_locked = winutil.is_locked
        winutil.is_locked = lambda: False  # 云端探针恒 None，这里钉成未锁屏

    def tearDown(self):
        winutil.is_locked = self._orig_locked

    def test_full_script_ok_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex, bridge, io_ = make_executor(tmp)
            script = [
                {"action": "cdp_connect", "url": "http://127.0.0.1:9222"},
                {"action": "navigate", "url": "https://claude.ai/new"},
                {"action": "find_click", "text": "发送"},
                {"action": "paste_text", "text": "长文本"},
                {"action": "type_text", "text": "abc"},
                {"action": "set_files", "selector": "input[type=file]", "files": ["C:\\a.jpg"]},
                {"action": "key", "key": "Enter"},
                {"action": "click_at", "x": 5, "y": 6},
                {"action": "wait_for", "selector": "textarea", "timeout_ms": 2000},
                {"action": "shot", "rect": [0, 0, 64, 48]},
            ]
            res = ex.run(script)
            self.assertTrue(res["ok"], res)
            self.assertEqual(len(res["results"]), 10)
            shot = res["results"][-1]
            self.assertTrue(shot["evidence"])
            self.assertTrue(os.path.exists(os.path.join(tmp, "evidence", shot["evidence"])))
            # 审计落行：10 个动作各一行
            audit_files = os.listdir(os.path.join(tmp, "audit"))
            self.assertEqual(len(audit_files), 1)
            with open(os.path.join(tmp, "audit", audit_files[0]), encoding="utf-8") as f:
                rows = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(rows), 10)
            self.assertTrue(all(r["ok"] for r in rows))
            self.assertEqual(io_[0], ["长文本"])       # paste 走了剪贴板粘贴通道
            self.assertEqual(io_[2], [(5, 6)])        # click_at 落点正确

    def test_observe_only_refuses_mutating(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex, bridge, _ = make_executor(tmp, observe=True)
            res = ex.run([{"action": "paste_text", "text": "x"},
                          {"action": "shot", "rect": [0, 0, 10, 10]}], stop_on_error=False)
            self.assertFalse(res["ok"])
            self.assertIn("OBSERVE_ONLY", res["results"][0]["detail"])
            self.assertTrue(res["results"][1]["ok"])  # 观察类动作可跑

    def test_stop_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex, bridge, _ = make_executor(tmp)
            bridge.find = lambda selector=None, text=None: (None, None)  # 让 find_click 失败
            res = ex.run([{"action": "find_click", "text": "没有"},
                          {"action": "paste_text", "text": "不该执行"}])
            self.assertFalse(res["ok"])
            self.assertEqual(res["stopped_at"], 0)
            self.assertEqual(len(res["results"]), 1)
            self.assertIn("NOT_FOUND", res["results"][0]["detail"])

    def test_locked_refuses_shot_and_input(self):
        winutil.is_locked = lambda: True
        with tempfile.TemporaryDirectory() as tmp:
            ex, _, _ = make_executor(tmp)
            r1 = ex.run([{"action": "shot", "rect": [0, 0, 10, 10]}])
            self.assertIn("LOCKED", r1["results"][0]["detail"])
            r2 = ex.run([{"action": "paste_text", "text": "x"}])
            self.assertIn("LOCKED", r2["results"][0]["detail"])

    def test_wait_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex, bridge, _ = make_executor(tmp)
            bridge.find = lambda selector=None, text=None: (None, None)
            res = ex.run([{"action": "wait_for", "selector": "#never", "timeout_ms": 700}])
            self.assertIn("WAIT_TIMEOUT", res["results"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
