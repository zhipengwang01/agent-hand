# -*- coding: utf-8 -*-
"""圈定硬规则自检：无圈定必拒、三选一互斥、矩形合法性、敏感标题拒绝。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import scope as scope_mod

WORDS = ["password", "密码", "credential"]


class TestScope(unittest.TestCase):
    def test_no_scope_rejected(self):
        with self.assertRaises(scope_mod.ScopeError) as cm:
            scope_mod.extract_scope({"action": "shot"})
        self.assertEqual(cm.exception.code, "SCOPE_REQUIRED")

    def test_each_scope_ok(self):
        self.assertEqual(scope_mod.extract_scope({"window": "记事本"}), {"window": "记事本"})
        self.assertEqual(scope_mod.extract_scope({"rect": [1, 2, 30, 40]}), {"rect": [1, 2, 30, 40]})
        self.assertEqual(scope_mod.extract_scope({"selector": "#app"}), {"selector": "#app"})

    def test_multiple_scopes_rejected(self):
        with self.assertRaises(scope_mod.ScopeError):
            scope_mod.extract_scope({"window": "x", "selector": "#a"})

    def test_bad_rect_rejected(self):
        for bad in ([1, 2, 3], [0, 0, 0, 10], [0, 0, 10, -5], "abcd", [0, 0, "w", 10]):
            with self.assertRaises(scope_mod.ScopeError):
                scope_mod.extract_scope({"rect": bad})

    def test_sensitive_title(self):
        self.assertTrue(scope_mod.is_sensitive_title("1Password - Sign in", WORDS))
        self.assertTrue(scope_mod.is_sensitive_title("修改密码", WORDS))
        self.assertFalse(scope_mod.is_sensitive_title("Claude - 新对话", WORDS))
        self.assertFalse(scope_mod.is_sensitive_title("", WORDS))
        self.assertFalse(scope_mod.is_sensitive_title(None, WORDS))
        with self.assertRaises(scope_mod.ScopeError) as cm:
            scope_mod.assert_not_sensitive("Enter your password", WORDS)
        self.assertEqual(cm.exception.code, "SENSITIVE_WINDOW")


if __name__ == "__main__":
    unittest.main()
