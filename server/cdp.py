# -*- coding: utf-8 -*-
"""CDP 线：纯标准库实现 WebSocket(RFC 6455) 客户端 + Chrome DevTools Protocol 最小集。

为什么不用第三方库：目标机只预装 Python＋Pillow，少一个依赖少一处部署故障。
实现范围（v1 最小集）：
  - 连接 http://127.0.0.1:<port>/json/list 发现页面目标，WS 附着
  - Target.attachToTarget(flatten) + setAutoAttach：每个 iframe 一个会话（穿 iframe 的关键）
  - Runtime.evaluate（含 executionContext 跟踪，选默认上下文求值）
  - Page.navigate / Page.captureScreenshot（带 clip）
  - Input.dispatchMouseEvent / Input.insertText / Input.dispatchKeyEvent
已知边界：跨源 iframe 的元素坐标需沿 frame 树逐级加偏移（getFrameOwner 的 quad），
偏移计算失败时 find_click 自动回退为元素 JS .click()，并在结果 detail 里注明。
"""
import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
import urllib.request

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ---------------- WebSocket 帧层 ----------------

def ws_connect(ws_url, timeout=10):
    """ws://host:port/path → 已完成握手的 socket。仅支持 ws://（本机回环，无需 TLS）。"""
    assert ws_url.startswith("ws://"), "只支持 ws:// 本机连接"
    rest = ws_url[5:]
    hostport, _, path = rest.partition("/")
    path = "/" + path
    host, _, port = hostport.partition(":")
    sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        "GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)
    )
    sock.sendall(req.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("WS 握手无响应")
        buf += chunk
    head, _, rest_bytes = buf.partition(b"\r\n\r\n")
    if b" 101 " not in head.split(b"\r\n")[0] + b" ":
        raise ConnectionError("WS 握手失败: %s" % head.split(b"\r\n")[0])
    expect = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    if expect.encode() not in head:
        raise ConnectionError("WS 握手校验失败（Sec-WebSocket-Accept 不匹配）")
    return _WSConn(sock, rest_bytes)


def _encode_frame(payload, opcode=1):
    """客户端→服务端帧：必须掩码。payload 为 bytes。"""
    header = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", n)
    mask = os.urandom(4)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bytes(header) + masked


class _WSConn:
    def __init__(self, sock, buffered=b""):
        self.sock = sock
        self.buf = bytearray(buffered)
        self._frag = bytearray()
        self._frag_opcode = None

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("WS 连接断开")
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def send_text(self, text):
        self.sock.sendall(_encode_frame(text.encode("utf-8"), opcode=1))

    def send_pong(self, payload):
        self.sock.sendall(_encode_frame(payload, opcode=10))

    def recv_message(self):
        """读一条完整消息（处理分片与 ping），返回 str。"""
        while True:
            b0, b1 = self._read(2)
            fin = b0 & 0x80
            opcode = b0 & 0x0F
            masked = b1 & 0x80
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if masked else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 9:  # ping → pong
                self.send_pong(payload)
                continue
            if opcode == 8:
                raise ConnectionError("WS 收到 close 帧")
            if opcode in (1, 2, 0):
                if opcode != 0:
                    self._frag = bytearray(payload)
                    self._frag_opcode = opcode
                else:
                    self._frag += payload
                if fin:
                    data = bytes(self._frag)
                    self._frag = bytearray()
                    return data.decode("utf-8", errors="replace")


# ---------------- CDP 会话层 ----------------

class CDPError(Exception):
    pass


class CDPClient:
    """单连接多会话（flatten）CDP 客户端。读线程分发响应/事件，本类线程安全。"""

    def __init__(self, ws_url, timeout=15):
        self.conn = ws_connect(ws_url, timeout=timeout)
        self.timeout = timeout
        self._id = 0
        self._lock = threading.Lock()
        self._pending = {}          # id -> [Event, result_dict]
        self._contexts = {}         # sessionId -> {contextId: auxData}
        self._sessions = {}         # sessionId -> targetInfo
        self._closed = False
        self._dead = False          # v0.2: 超时/断连后置 True，桥层据此自动重建（B1 自愈）
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # -- 基础设施 --
    def _read_loop(self):
        try:
            while not self._closed:
                raw = self.conn.recv_message()
                msg = json.loads(raw)
                if "id" in msg:
                    with self._lock:
                        slot = self._pending.pop(msg["id"], None)
                    if slot:
                        slot[1].update(msg)
                        slot[0].set()
                elif msg.get("method") == "Runtime.executionContextCreated":
                    sid = msg.get("sessionId")
                    ctx = msg["params"]["context"]
                    self._contexts.setdefault(sid, {})[ctx["id"]] = ctx.get("auxData", {})
                elif msg.get("method") == "Runtime.executionContextDestroyed":
                    sid = msg.get("sessionId")
                    cid = msg["params"].get("executionContextId")
                    self._contexts.get(sid, {}).pop(cid, None)
        except Exception:
            self._dead = True
            with self._lock:
                for slot in self._pending.values():
                    slot[1].update({"error": {"message": "连接断开"}})
                    slot[0].set()
                self._pending.clear()

    def send(self, method, params=None, session_id=None, timeout=None):
        if self._dead:
            raise CDPError("CDP 连接已失效（待桥层重建）: %s" % method)
        with self._lock:
            self._id += 1
            mid = self._id
            slot = [threading.Event(), {}]
            self._pending[mid] = slot
        msg = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        try:
            self.conn.send_text(json.dumps(msg))
        except Exception as e:
            self._dead = True
            with self._lock:
                self._pending.pop(mid, None)
            raise CDPError("CDP 发送失败 %s: %r" % (method, e))
        if not slot[0].wait(timeout if timeout is not None else self.timeout):
            # v0.2 B1: 一次超时即判定桥楔死——标死并关 socket，后续调用快速失败，
            # 由 CDPBridge._require 统一丢弃重建，不再原地干等到服务重启。
            self._dead = True
            with self._lock:
                self._pending.pop(mid, None)
            try:
                self.conn.sock.close()
            except Exception:
                pass
            raise CDPError("CDP 超时: %s" % method)
        resp = slot[1]
        if "error" in resp:
            raise CDPError("CDP 错误 %s: %s" % (method, resp["error"]))
        return resp.get("result", {})

    def close(self):
        self._closed = True
        try:
            self.sock_close()
        except Exception:
            pass

    def sock_close(self):
        self.conn.sock.close()

    # -- 目标/会话管理 --
    def attach_page(self, target_id=None):
        """附着到页面目标（含其全部 iframe 子目标），返回主会话 sessionId。"""
        if target_id is None:
            targets = self.send("Target.getTargets", timeout=8)["targetInfos"]
            pages = [t for t in targets if t["type"] == "page" and not t.get("attached")]
            if not pages:
                pages = [t for t in targets if t["type"] == "page"]
            if not pages:
                raise CDPError("没有可附着的 page 目标")
            target_id = pages[0]["targetId"]
        res = self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        sid = res["sessionId"]
        self._sessions[sid] = {"targetId": target_id, "type": "page"}
        # 子目标（iframe）自动附着，事件经 Target.attachedToTarget 到达——本最小实现采用
        # 轮询 getTargets 补齐未附着的 iframe 目标（简单、确定性够用）。
        self.send("Runtime.enable", session_id=sid)
        self.attach_all_frames()
        return sid

    def attach_all_frames(self):
        targets = self.send("Target.getTargets", timeout=8)["targetInfos"]
        for t in targets:
            if t["type"] in ("iframe", "page") and not t.get("attached"):
                try:
                    res = self.send("Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})
                    sid = res["sessionId"]
                    self._sessions[sid] = {"targetId": t["targetId"], "type": t["type"]}
                    self.send("Runtime.enable", session_id=sid)
                except CDPError:
                    continue

    def sessions(self):
        self.attach_all_frames()
        return list(self._sessions.keys())

    # -- 求值 --
    def evaluate(self, expression, session_id, context_id=None, await_promise=False):
        params = {"expression": expression, "returnByValue": True, "awaitPromise": await_promise}
        if context_id is not None:
            params["contextId"] = context_id
        res = self.send("Runtime.evaluate", params, session_id=session_id)
        result = res.get("result", {})
        if res.get("exceptionDetails"):
            raise CDPError("JS 异常: %s" % res["exceptionDetails"].get("text", ""))
        if result.get("type") == "undefined":
            return None
        return result.get("value")

    def default_context(self, session_id, timeout=5):
        """等该会话的默认执行上下文出现并返回 contextId（iframe 会话就是该 frame 的世界）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for cid, aux in self._contexts.get(session_id, {}).items():
                if aux.get("isDefault"):
                    return cid
            time.sleep(0.05)
        return None  # 拿不到上下文时 evaluate 不带 contextId 也可工作（用主世界）


# ---------------- 高层动作 ----------------

FINDER_JS = r"""
(function(sel, txt){
  function vis(el){ var r = el.getBoundingClientRect(); return r.width>0 && r.height>0; }
  var el = null;
  if (sel) { el = document.querySelector(sel); }
  if (!el && txt) {
    var want = txt.toLowerCase();
    var all = document.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"], div, span');
    for (var i=0;i<all.length;i++){
      var t = (all[i].innerText||all[i].value||'').trim().toLowerCase();
      if (t && t.indexOf(want) !== -1 && vis(all[i])) { el = all[i]; break; }
    }
  }
  if (!el) return {found:false};
  el.scrollIntoView({block:'center', inline:'center'});
  var r = el.getBoundingClientRect();
  return {found:true, x:r.left + r.width/2, y:r.top + r.height/2,
          tag:el.tagName, text:(el.innerText||el.value||'').slice(0,80)};
})"""


class CDPBridge:
    """执行器用的高层封装：连接管理＋跨帧查找点击＋导航＋截图。"""

    def __init__(self):
        self.client = None
        self.main_session = None
        self.debug_url = None
        self._page_url = None       # v0.2: 重建时优先按 URL 找回同一页面
        self.rebuilds = 0           # v0.2: 累计自动重建次数（供状态/审计观察）

    def connect(self, debug_url):
        """debug_url 形如 http://127.0.0.1:9222"""
        self.debug_url = debug_url.rstrip("/")
        with urllib.request.urlopen(self.debug_url + "/json/list", timeout=5) as r:
            targets = json.load(r)
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            raise CDPError("调试端口上没有 page 目标")
        self.client = CDPClient(page["webSocketDebuggerUrl"])
        self.main_session = self.client.attach_page(page["id"])
        self._page_url = page.get("url", "")
        return {"connected": True, "title": page.get("title", ""), "url": page.get("url", "")}

    def _rebuild(self):
        """v0.2 B1 自愈：丢弃楔死的连接，按原 debug_url 重新发现页面并附着（只重建一次）。"""
        old, self.client, self.main_session = self.client, None, None
        if old:
            try:
                old.close()
            except Exception:
                pass
        with urllib.request.urlopen(self.debug_url + "/json/list", timeout=5) as r:
            targets = json.load(r)
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            raise CDPError("CDP 重建失败: 调试端口上没有 page 目标")
        page = next((t for t in pages if t.get("url") == self._page_url), pages[0])
        self.client = CDPClient(page["webSocketDebuggerUrl"])
        self.main_session = self.client.attach_page(page["id"])
        self._page_url = page.get("url", self._page_url)
        self.rebuilds += 1

    def _require(self):
        if not self.client:
            raise CDPError("CDP 未连接（先执行 cdp_connect）")
        if getattr(self.client, "_dead", False):
            self._rebuild()

    def navigate(self, url):
        self._require()
        self.client.send("Page.enable", session_id=self.main_session)
        self.client.send("Page.navigate", {"url": url}, session_id=self.main_session)
        self._page_url = url
        return {"navigated": url}

    def find(self, selector=None, text=None):
        """遍历主文档＋全部 iframe 会话查找元素。返回 (session_id, hit dict) 或 (None, None)。"""
        self._require()
        expr = "(%s)(%s, %s)" % (FINDER_JS, json.dumps(selector), json.dumps(text))
        try:
            sids = self.client.sessions()
        except CDPError:
            if getattr(self.client, "_dead", False):
                self._rebuild()
                sids = self.client.sessions()
            else:
                raise
        for sid in sids:
            ctx = self.client.default_context(sid, timeout=1)
            try:
                hit = self.client.evaluate(expr, sid, context_id=ctx)
            except CDPError:
                continue
            if hit and hit.get("found"):
                return sid, hit
        return None, None

    def click_found(self, session_id, hit):
        """点击已找到的元素：优先真实鼠标事件（坐标需换算到主视口），失败回退 JS click。"""
        self._require()
        # 坐标换算：子帧需加 iframe 在父帧中的偏移；偏移不可得时回退 JS 点击（detail 注明）。
        try:
            x, y = hit["x"], hit["y"]
            sid = session_id
            guard = 0
            while sid != self.main_session and guard < 8:
                guard += 1
                owner = self._frame_owner_quad(sid)
                if owner is None:
                    raise CDPError("偏移未知")
                x += owner[0]
                y += owner[1]
                sid = owner[2]
            for typ, btn in (("mousePressed", "left"), ("mouseReleased", "left")):
                self.client.send("Input.dispatchMouseEvent",
                                 {"type": typ, "x": x, "y": y, "button": btn, "clickCount": 1},
                                 session_id=self.main_session)
            return {"clicked": True, "via": "mouse", "x": x, "y": y}
        except CDPError:
            # 回退：在元素所在会话里按命中文本再查一次并 JS click（事件链完整）
            fallback = ("(function(){ var all=document.querySelectorAll('*');"
                        " for(var i=0;i<all.length;i++){ var t=(all[i].innerText||'');"
                        " if(t && t.indexOf(%s)!==-1){ all[i].click(); return true; } } return false; })()"
                        % json.dumps((hit.get("text") or "")[:40]))
            ok = False
            try:
                ok = self.client.evaluate(fallback, session_id,
                                          context_id=self.client.default_context(session_id, timeout=1))
            except CDPError:
                ok = False
            return {"clicked": bool(ok), "via": "js_fallback"}

    def _frame_owner_quad(self, child_session):
        """查子帧在其父帧中的左上角偏移。返回 (dx, dy, parent_session) 或 None。

        实现：对每个候选父会话，用 DOM.getFrameOwner 需要 frameId——本最小实现改走
        Runtime 侧路线：向浏览器询问 target 层级不可得时返回 None（触发 JS 回退）。
        v1 取舍：跨源 iframe 坐标合成不做（见 README 已知限制），同源 iframe 的嵌套
        由 find 的多会话轮询覆盖点击语义（JS 回退在同/跨源均有效）。
        """
        return None

    def set_files(self, selector, files):
        """给 <input type=file> 设置文件（CDP DOM.setFileInputFiles，不弹系统文件框）。

        files 为目标机上的绝对路径列表——这正是今晚 agent-desktop 卡死的附图环节的解法。
        """
        self._require()
        sid, hit = self.find(selector=selector)
        if not hit:
            raise CDPError("找不到文件输入元素 %s" % selector)
        doc = self.client.send("DOM.getDocument", {"depth": 1}, session_id=sid)
        root_id = doc["root"]["nodeId"]
        node = self.client.send("DOM.querySelector", {"nodeId": root_id, "selector": selector},
                                session_id=sid)
        if not node.get("nodeId"):
            raise CDPError("文件输入元素不可定位")
        self.client.send("DOM.setFileInputFiles", {"files": list(files), "nodeId": node["nodeId"]},
                         session_id=sid)
        return {"files_set": len(files)}

    def insert_text(self, text):
        self._require()
        self.client.send("Input.insertText", {"text": text}, session_id=self.main_session)
        return {"inserted": len(text)}

    def key(self, key_name):
        """key_name 如 'Enter'、'ctrl+v'（组合键以 + 连接）。"""
        self._require()
        parts = [p.strip() for p in key_name.split("+")]
        key = parts[-1]
        mods = 0
        for p in parts[:-1]:
            mods |= {"ctrl": 2, "alt": 1, "shift": 8, "meta": 4}.get(p.lower(), 0)
        vk = {"enter": 13, "tab": 9, "escape": 27, "backspace": 8, "delete": 46,
              "arrowdown": 40, "arrowup": 38, "arrowleft": 37, "arrowright": 39}.get(key.lower())
        params = {"type": "keyDown", "key": key, "modifiers": mods}
        if vk:
            params["windowsVirtualKeyCode"] = vk
            params["nativeVirtualKeyCode"] = vk
        if len(key) == 1 and not mods:
            params["text"] = key
        self.client.send("Input.dispatchKeyEvent", params, session_id=self.main_session)
        params2 = dict(params)
        params2["type"] = "keyUp"
        params2.pop("text", None)
        self.client.send("Input.dispatchKeyEvent", params2, session_id=self.main_session)
        return {"key": key_name}

    def screenshot_clip(self, clip):
        """Page.captureScreenshot，clip={x,y,width,height,scale}，返回 JPEG/PNG bytes(base64 解码后)。"""
        self._require()
        self.client.send("Page.enable", session_id=self.main_session)
        res = self.client.send("Page.captureScreenshot",
                               {"format": "png", "clip": clip, "captureBeyondViewport": False},
                               session_id=self.main_session)
        return base64.b64decode(res["data"])
