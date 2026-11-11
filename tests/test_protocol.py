# -*- coding: utf-8 -*-
"""协议校验自检：动作脚本结构、二选一约束、未知动作、shot 圈定在校验期就拦。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import actions


class TestValidateScript(unittest.TestCase):
    def test_ok_script(self):
        script = [
            {"action": "cdp_connect", "url": "http://127.0.0.1:9222"},
            {"action": "navigate", "url": "https://claude.ai/new"},
            {"action": "find_click", "text": "发送"},
            {"action": "paste_text", "text": "hello"},
            {"action": "wait_for", "selector": "textarea", "timeout_ms": 5000},
            {"action": "shot", "rect": [0, 0, 100, 100]},
        ]
        out = actions.validate_script(script)
        self.assertEqual(len(out), 6)

    def test_empty_and_non_list(self):
        for bad in ([], {}, "x", None):
            with self.assertRaises(actions.ProtocolError):
                actions.validate_script(bad)

    def test_unknown_action(self):
        with self.assertRaises(actions.ProtocolError) as cm:
            actions.validate_script([{"action": "rm_rf"}])
        self.assertEqual(cm.exception.index, 0)

    def test_missing_required(self):
        with self.assertRaises(actions.ProtocolError):
            actions.validate_script([{"action": "navigate"}])
        with self.assertRaises(actions.ProtocolError):
            actions.validate_script([{"action": "paste_text"}])
        with self.assertRaises(actions.ProtocolError):
            actions.validate_script([{"action": "set_files", "selector": "input"}])

    def test_either_or_constraints(self):
        with self.assertRaises(actions.ProtocolError):
            actions.validate_script([{"action": "find_click"}])
        with self.assertRaises(actions.ProtocolError):
            actions.validate_script([{"action": "wait_for"}])
        # text 版 find_click 与 pixel_diff 版 wait_for 应通过
        actions.validate_script([{"action": "find_click", "text": "OK"}])
        actions.validate_script([{"action": "wait_for", "pixel_diff": {"rect": [0, 0, 9, 9]}}])

    def test_shot_scope_checked_at_validate_time(self):
        with self.assertRaises(actions.ProtocolError):
            actions.validate_script([{"action": "shot"}])
        with self.assertRaises(actions.ProtocolError):
            actions.validate_script([{"action": "shot", "rect": [0, 0, -1, 9]}])
        with self.assertRaises(actions.ProtocolError):
            actions.validate_script([{"action": "shot", "window": "x", "rect": [0, 0, 9, 9]}])


if __name__ == "__main__":
    unittest.main()
