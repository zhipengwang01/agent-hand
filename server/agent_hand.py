# -*- coding: utf-8 -*-
"""AgentHand常驻服务入口：HTTP 控制面＋心跳线程＋屏幕流。

路由：
  GET  /              状态小页（给人看：状态＋屏幕流入口）
  GET  /status        心跳 JSON（现算现回，永远新鲜）
  GET  /stream.mjpeg  屏幕流（数据面，用户本人看自己屏幕；不经 AI、不花 token）
  POST /actions       批量动作脚本（需 X-AgentHand-Token，config.token 非空时）
  GET  /evidence/<f>  证据帧下载（需 token）

启动守卫：单实例互斥（防 session 0/1 双实例互抢的历史坑）；会话 0 直接退出并落日志；
DISABLED 标志文件存在时只写心跳（disabled:true）不启执行器——Disable 开关语义。
无窗口：由 deploy 的 vbs 以 pythonw 启动，本身也不开任何控制台输出窗口。
"""
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions as actions_mod
import audit as audit_mod
import capture
import config as config_mod
import state as state_mod
import winutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DISABLED_FLAG = os.path.join(BASE_DIR, "DISABLED")


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        os.makedirs(cfg["data_dir"], exist_ok=True)
        self.audit = audit_mod.AuditLog(cfg["data_dir"])
        self.executor = actions_mod.Executor(cfg, self.audit)
        self.probes = state_mod.default_probes()
        self._hb_lock = threading.Lock()
        self._hb = None
        # v0.2 B4: 异步执行——长脚本不堵 HTTP 连接（隧道约 20s 断连的正解）；
        # 执行器非线程安全，同步/异步共用一把执行锁串行化。
        self.jobs = {}
        self._jobs_lock = threading.Lock()
        self._exec_lock = threading.Lock()

    def start_job(self, script, stop_on_error=True):
        import uuid
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "status": "running", "result": None, "ts": time.time()}
        with self._jobs_lock:
            self.jobs[jid] = job
            while len(self.jobs) > 100:
                oldest = min(self.jobs.values(), key=lambda j: j["ts"])
                del self.jobs[oldest["id"]]

        def work():
            try:
                with self._exec_lock:
                    job["result"] = self.executor.run(script, stop_on_error=stop_on_error)
                job["status"] = "done"
            except actions_mod.ProtocolError as e:
                job["result"] = {"ok": False, "detail": "PROTOCOL: %s (index=%s)" % (e, e.index)}
                job["status"] = "done"
            except Exception as e:  # 兜底：job 必须落终态，调用方轮询不能干等
                job["result"] = {"ok": False, "detail": "ERROR: %r" % e}
                job["status"] = "error"

        threading.Thread(target=work, daemon=True).start()
        return job

    def get_job(self, jid):
        with self._jobs_lock:
            return self.jobs.get(jid)

    def disabled(self):
        return os.path.exists(DISABLED_FLAG)

    def heartbeat(self):
        with self._hb_lock:
            return self._hb

    def heartbeat_loop(self):
        interval = max(1, min(5, self.cfg["heartbeat_interval"]))
        hb_path = os.path.join(self.cfg["data_dir"], "heartbeat.json")
        while True:
            hb = state_mod.build_heartbeat(self.probes, disabled=self.disabled())
            with self._hb_lock:
                self._hb = hb
            try:
                state_mod.write_heartbeat_atomic(hb_path, hb)
            except OSError:
                pass
            time.sleep(interval)

    def cleanup_loop(self):
        """证据帧与审计按保留天数自清（每日一查足够）。"""
        keep = self.cfg["evidence_keep_days"] * 86400
        while True:
            now = time.time()
            for sub in ("evidence", "audit"):
                d = os.path.join(self.cfg["data_dir"], sub)
                if os.path.isdir(d):
                    for f in os.listdir(d):
                        p = os.path.join(d, f)
                        try:
                            if now - os.path.getmtime(p) > keep:
                                os.remove(p)
                        except OSError:
                            pass
            time.sleep(3600)


def make_handler(app):
    cfg = app.cfg

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # 静默，不往控制台打（无窗口服务）
            pass

        def _token_ok(self, query=None):
            """v0.2 B2: 头 X-AgentHand-Token 为主；GET 路由额外接受 ?token= 查询参数
            （手机浏览器看流带不了自定义头）。config.token 为空时不校验（本机调试）。"""
            token = cfg.get("token", "")
            if not token:
                return True
            if self.headers.get("X-AgentHand-Token") == token:
                return True
            if query:
                vals = urllib.parse.parse_qs(query).get("token") or []
                if vals and vals[0] == token:
                    return True
            return False

        def _send_json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            path, query = u.path, u.query
            if path in ("/status", "/", "/stream.mjpeg") and not self._token_ok(query):
                self._send_json({"error": "unauthorized"}, 401)
                return
            if path == "/status":
                hb = app.heartbeat() or state_mod.build_heartbeat(app.probes, disabled=app.disabled())
                self._send_json(hb)
            elif path == "/":
                hb = app.heartbeat() or {}
                _tv = (urllib.parse.parse_qs(query).get("token") or [""])[0]
                _stream_href = "/stream.mjpeg?token=" + _tv if _tv else "/stream.mjpeg"
                html = ("<html><head><meta charset='utf-8'><title>AgentHand</title></head><body>"
                        "<h3>AgentHand · %s</h3><pre>%s</pre>"
                        "<p><a href='%s'>实时屏幕（MJPEG）</a></p></body></html>"
                        % (hb.get("host", ""), json.dumps(hb, ensure_ascii=False, indent=1), _stream_href))
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/stream.mjpeg":
                if not cfg.get("stream"):
                    self._send_json({"error": "stream disabled"}, 404)
                    return
                self._stream()
            elif path.startswith("/result/"):
                if not self._token_ok(query):
                    self._send_json({"error": "unauthorized"}, 401)
                    return
                job = app.get_job(os.path.basename(path[len("/result/"):]))
                if not job:
                    self._send_json({"error": "job not found"}, 404)
                    return
                self._send_json({"job_id": job["id"], "status": job["status"], "result": job["result"]})
            elif path.startswith("/evidence/"):
                if not self._token_ok():
                    self._send_json({"error": "unauthorized"}, 401)
                    return
                name = os.path.basename(path[len("/evidence/"):])
                path = os.path.join(cfg["data_dir"], "evidence", name)
                if not os.path.exists(path):
                    self._send_json({"error": "not found"}, 404)
                    return
                with open(path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send_json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path != "/actions":
                self._send_json({"error": "not found"}, 404)
                return
            if not self._token_ok():
                self._send_json({"error": "unauthorized"}, 401)
                return
            if app.disabled():
                self._send_json({"ok": False, "detail": "DISABLED: 开关已停用，拒绝执行"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                script = payload["script"]
                stop = payload.get("stop_on_error", True)
            except Exception as e:
                self._send_json({"ok": False, "detail": "BAD_REQUEST: %r" % e}, 400)
                return
            if payload.get("async"):
                job = app.start_job(script, stop_on_error=stop)
                self._send_json({"job_id": job["id"], "status": "running"}, 202)
                return
            try:
                with app._exec_lock:
                    result = app.executor.run(script, stop_on_error=stop)
                self._send_json(result)
            except actions_mod.ProtocolError as e:
                self._send_json({"ok": False, "detail": "PROTOCOL: %s (index=%s)" % (e, e.index)}, 400)

        def _stream(self):
            """MJPEG 数据面：自适应帧率（静止降帧），断开即停。"""
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replacement; boundary=frame")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            encoder = capture.MjpegEncoder(quality=cfg["stream_quality"])
            policy = capture.StreamPolicy(fps_max=cfg["stream_fps_max"], fps_idle=cfg["stream_fps_idle"])
            try:
                while True:
                    img = capture.grab_screen()
                    img = capture.downscale(img, cfg["stream_max_width"])
                    sig = capture.frame_signature(img)
                    data = encoder.encode(img)
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
                    self.wfile.flush()
                    time.sleep(policy.next_interval(sig))
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    return Handler


def main():
    cfg = config_mod.load_config()
    log_path = os.path.join(cfg["data_dir"], "service.log")

    def log(msg):
        try:
            os.makedirs(cfg["data_dir"], exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        except OSError:
            pass

    # 会话 0 守卫：服务必须跑在交互会话（计划任务 AtLogOn），session 0 里没有用户桌面
    sid = winutil.current_session_id()
    if sid == 0:
        log("FATAL: 运行在会话 0，退出（应由交互会话的计划任务启动）")
        return 2
    # 单实例互斥
    _handle, exists = winutil.create_mutex("Global\\AgentHandService")
    if exists:
        log("FATAL: 已有实例在运行，退出")
        return 3

    app = App(cfg)
    threading.Thread(target=app.heartbeat_loop, daemon=True).start()
    threading.Thread(target=app.cleanup_loop, daemon=True).start()
    host = config_mod.resolve_bind_host(cfg)
    server = ThreadingHTTPServer((host, cfg["port"]), make_handler(app))
    log("START host=%s port=%s observe_only=%s session=%s" % (host, cfg["port"], cfg["observe_only"], sid))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
