# -*- coding: utf-8 -*-
"""CDP 冒烟：本地假 WS 端点（纯标准库）验证握手/掩码/分片重组/大载荷/请求响应配对。

只证明 cdp.py 的 WebSocket 帧层与会话层在协议层面正确；
真实 Edge 的 CDP 域行为（attach/iframe 上下文等）只能真机验，见 README 未验清单。
"""
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import cdp


def _srv_read_exact(conn, buf, n):
    while len(buf) < n:
        chunk = conn.recv(65536)
        if not chunk:
            raise ConnectionError("client closed")
        buf += chunk
    return buf[:n], buf[n:]


def _srv_read_frame(conn, buf):
    head, buf = _srv_read_exact(conn, buf, 2)
    b0, b1 = head
    fin, opcode = bool(b0 & 0x80), b0 & 0x0F
    masked, n = b1 & 0x80, b1 & 0x7F
    if n == 126:
        ext, buf = _srv_read_exact(conn, buf, 2)
        n = struct.unpack(">H", ext)[0]
    elif n == 127:
        ext, buf = _srv_read_exact(conn, buf, 8)
        n = struct.unpack(">Q", ext)[0]
    mask = None
    if masked:
        mask, buf = _srv_read_exact(conn, buf, 4)
    payload, buf = _srv_read_exact(conn, buf, n)
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload, buf


def _srv_frame(payload, opcode=1, fin=True):
    header = bytearray([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += struct.pack(">H", n)
    else:
        header.append(127)
        header += struct.pack(">Q", n)
    return bytes(header) + payload


class FakeCDPServer(threading.Thread):
    """最小假端点：握手后按 CDP 形状回响应；Test.fragment 走分片；连接后先推一个上下文事件。"""

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
        head, _, buf = buf.partition(b"\r\n\r\n")
        key = [l.split(":", 1)[1].strip() for l in head.decode("latin1").split("\r\n")
               if l.lower().startswith("sec-websocket-key")][0]
        accept = base64.b64encode(hashlib.sha1((key + cdp.WS_GUID).encode()).digest()).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
        # 先推一个 executionContextCreated 事件（验证客户端事件分发不炸）
        event = json.dumps({"method": "Runtime.executionContextCreated",
                            "params": {"context": {"id": 7, "auxData": {"isDefault": True, "frameId": "F1"}}},
                            "sessionId": "S1"}).encode()
        conn.sendall(_srv_frame(event))
        while True:
            try:
                fin, opcode, payload, buf = _srv_read_frame(conn, buf)
            except (ConnectionError, OSError):
                return
            if opcode == 8:
                return
            if opcode == 10:
                continue
            msg = json.loads(payload.decode())
            result = {"echo_method": msg["method"], "echo_len": len(json.dumps(msg))}
            resp = json.dumps({"id": msg["id"], "result": result}).encode()
            if msg["method"] == "Test.fragment":
                half = len(resp) // 2
                conn.sendall(_srv_frame(resp[:half], opcode=1, fin=False))
                conn.sendall(_srv_frame(resp[half:], opcode=0, fin=True))
            else:
                conn.sendall(_srv_frame(resp))


class TestCDPSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeCDPServer()
        cls.server.start()
        cls.client = cdp.CDPClient("ws://127.0.0.1:%d/devtools/page/1" % cls.server.port, timeout=10)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.server.sock.close()

    def test_request_response_pairing(self):
        res = self.client.send("Runtime.evaluate", {"expression": "1+1"})
        self.assertEqual(res["echo_method"], "Runtime.evaluate")

    def test_fragmented_response_reassembled(self):
        res = self.client.send("Test.fragment", {"x": "y" * 500})
        self.assertEqual(res["echo_method"], "Test.fragment")
        self.assertGreater(res["echo_len"], 500)

    def test_large_payload_roundtrip(self):
        res = self.client.send("Runtime.evaluate", {"expression": "z" * 100000})
        self.assertGreater(res["echo_len"], 100000)

    def test_context_event_tracked(self):
        # setUpClass 连接后服务器已推送上下文事件；轮询等读线程入账
        import time
        deadline = time.time() + 3
        while time.time() < deadline:
            if self.client._contexts.get("S1", {}).get(7, {}).get("isDefault"):
                return
            time.sleep(0.05)
        self.fail("executionContextCreated 事件未被跟踪")


if __name__ == "__main__":
    unittest.main()
