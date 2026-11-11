# -*- coding: utf-8 -*-
"""v0.2 硬化测试：B1 CDP 桥自愈 / B2 路由鉴权 / B3 证据帧新鲜度。"""
import base64
import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import actions
import agent_hand
import audit as audit_mod
import cdp
import winutil
from PIL import Image


# ---------- B1: 静默 WS 端点（握手成功、永不回包） ----------

class SilentWSServer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]

    def run(self):
        conn, _ = self.sock.accept()
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += conn.recv(4096)
        head = buf.split(b"\r\n\r\n")[0]
        key = [l.split(":", 1)[1].strip() for l in head.decode("latin1").split("\r\n")
               if l.lower().startswith("sec-websocket-key")][0]
        accept = base64.b64encode(hashlib.sha1((key + cdp.WS_GUID).encode()).digest()).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
        try:
            while conn.recv(65536):
                pass  # 只收不回，模拟桥楔死
        except OSError:
            pass


class TestB1TimeoutMarksDead(unittest.TestCase):
    def test_send_timeout_marks_dead_and_fails_fast(self):
        srv = SilentWSServer()
        srv.start()
        client = cdp.CDPClient("ws://127.0.0.1:%d/x" % srv.port, timeout=1)
        try:
            with self.assertRaises(cdp.CDPError) as cm:
                client.send("Target.getTargets", timeout=1)
            self.assertIn("超时", str(cm.exception))
            self.assertTrue(client._dead)
            with self.assertRaises(cdp.CDPError) as cm2:
                client.send("Target.getTargets", timeout=5)
            self.assertIn("已失效", str(cm2.exception))
        finally:
            client.close()
            srv.sock.close()


class _FakeClient:
    def __init__(self, dead):
        self._dead = dead
        self.sent = []

    def send(self, method, params=None, session_id=None, timeout=None):
        self.sent.append(method)
        return {}


class TestB1BridgeRebuild(unittest.TestCase):
    def test_require_rebuilds_dead_client_once(self):
        bridge = cdp.CDPBridge()
        bridge.client = _FakeClient(dead=True)
        bridge.main_session = "old"
        calls = []

        def fake_rebuild():
            calls.append(1)
            bridge.client = _FakeClient(dead=False)
            bridge.main_session = "new"
            bridge.rebuilds += 1

        bridge._rebuild = fake_rebuild
        bridge._require()
        self.assertEqual(len(calls), 1)
        self.assertEqual(bridge.main_session, "new")
        bridge._require()  # 健康后不再重建
        self.assertEqual(len(calls), 1)

    def test_require_raises_when_not_connected(self):
        bridge = cdp.CDPBridge()
        with self.assertRaises(cdp.CDPError):
            bridge._require()


# ---------- B2: HTTP 路由鉴权 ----------

class TestB2Auth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        import config as config_mod
        cfg = dict(config_mod.DEFAULTS)
        cfg.update({"token": "test-token-123", "data_dir": cls.tmp, "stream": False})
        cls.app = agent_hand.App(cfg)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), agent_hand.make_handler(cls.app))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _get(self, path, token=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path))
        if token:
            req.add_header("X-AgentHand-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_status_requires_token(self):
        code, _ = self._get("/status")
        self.assertEqual(code, 401)

    def test_status_header_token_ok(self):
        code, body = self._get("/status", token="test-token-123")
        self.assertEqual(code, 200)
        self.assertIn("heartbeat", json.loads(body.decode())["schema"])

    def test_status_query_token_ok(self):
        code, _ = self._get("/status?token=test-token-123")
        self.assertEqual(code, 200)

    def test_status_wrong_token_401(self):
        code, _ = self._get("/status?token=nope")
        self.assertEqual(code, 401)

    def test_stream_wrong_token_401_and_right_token_passes_auth(self):
        code, _ = self._get("/stream.mjpeg?token=nope")
        self.assertEqual(code, 401)
        code, _ = self._get("/stream.mjpeg?token=test-token-123")
        self.assertEqual(code, 404)  # 鉴权已过，是 stream=False 的 404，不是 401


# ---------- B4: 异步执行 job ----------

class TestB4Async(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        import config as config_mod
        cfg = dict(config_mod.DEFAULTS)
        cfg.update({"token": "test-token-123", "data_dir": cls.tmp, "stream": False})
        cls.app = agent_hand.App(cfg)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), agent_hand.make_handler(cls.app))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _req(self, method, path, payload=None, token="test-token-123"):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, method=method)
        if token:
            req.add_header("X-AgentHand-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_async_job_roundtrip(self):
        # cdp_connect 到死端口会快速失败——用来验证 job 管道本身（提交→轮询→终态结果）
        code, body = self._req("POST", "/actions",
                               {"script": [{"action": "cdp_connect", "url": "http://127.0.0.1:9"}],
                                "async": True})
        self.assertEqual(code, 202)
        jid = body["job_id"]
        import time
        deadline = time.time() + 15
        final = None
        while time.time() < deadline:
            code, body = self._req("GET", "/result/" + jid)
            self.assertEqual(code, 200)
            if body["status"] in ("done", "error"):
                final = body
                break
            time.sleep(0.3)
        self.assertIsNotNone(final, "job 未在 15s 内落终态")
        self.assertFalse(final["result"]["ok"])  # 死端口连接必然失败，如实回

    def test_result_unknown_job_404_and_no_token_401(self):
        code, _ = self._req("GET", "/result/deadbeefdead")
        self.assertEqual(code, 404)
        code, _ = self._req("GET", "/result/deadbeefdead", token=None)
        self.assertEqual(code, 401)


# ---------- B3: shot 帧新鲜度 ----------

class TestB3StaleFrame(unittest.TestCase):
    def setUp(self):
        self._orig_locked = winutil.is_locked
        winutil.is_locked = lambda: False

    def tearDown(self):
        winutil.is_locked = self._orig_locked

    def test_same_frame_twice_marks_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"data_dir": tmp, "observe_only": False,
                   "sensitive_title_words": ["password"]}
            ex = actions.Executor(
                cfg, audit_mod.AuditLog(tmp),
                grabber=lambda bbox=None: Image.new("RGB", (64, 48), (10, 20, 30)))
            r1 = ex.run([{"action": "shot", "rect": [0, 0, 64, 48]}])
            r2 = ex.run([{"action": "shot", "rect": [0, 0, 64, 48]}])
            d1, d2 = r1["results"][0]["data"], r2["results"][0]["data"]
            self.assertTrue(d1["sha256"])
            self.assertEqual(d1["sha256"], d2["sha256"])
            self.assertFalse(d1["stale_suspect"])
            self.assertTrue(d2["stale_suspect"])


if __name__ == "__main__":
    unittest.main()
