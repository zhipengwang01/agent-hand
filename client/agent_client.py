# -*- coding: utf-8 -*-
"""AgentHand云端调用端（纯标准库）：发动作脚本、收结果、把证据帧拉回本地存档。

用法：
  python agent_client.py --host <TAILSCALE_IP> --token XXX status
  python agent_client.py --host <TAILSCALE_IP> --token XXX run fin_replay.json --save-evidence ./evidence
主机地址 = 目标机 Tailscale IP；端口与服务 config 一致（默认 18765）。
"""
import argparse
import json
import os
import sys
import urllib.request


class AgentClient:
    def __init__(self, host, port=18765, token=""):
        self.base = "http://%s:%d" % (host, port)
        self.token = token

    def _req(self, method, path, payload=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if self.token:
            req.add_header("X-AgentHand-Token", self.token)
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=300) as r:
            ctype = r.headers.get_content_type()
            body = r.read()
            if ctype == "application/json":
                return json.loads(body.decode("utf-8"))
            return body

    def status(self):
        return self._req("GET", "/status")

    def run_script(self, script, stop_on_error=True):
        """script: list（动作数组）或 dict（完整 payload）。payload 带 async:true 时
        服务端立即回 job_id，本方法自动轮询 /result 直到终态再返回最终结果（v0.2 B4）。"""
        payload = script if isinstance(script, dict) else {"script": script, "stop_on_error": stop_on_error}
        res = self._req("POST", "/actions", payload)
        if isinstance(res, dict) and res.get("job_id"):
            return self.wait_result(res["job_id"])
        return res

    def job_result(self, job_id):
        return self._req("GET", "/result/" + job_id)

    def wait_result(self, job_id, timeout=900):
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.job_result(job_id)
            if r.get("status") in ("done", "error"):
                return r.get("result") or r
            time.sleep(2)
        raise TimeoutError("等待 job %s 超时" % job_id)

    def fetch_evidence(self, name, out_dir):
        data = self._req("GET", "/evidence/" + name)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="AgentHand调用端")
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=18765)
    ap.add_argument("--token", default=os.environ.get("AGENT_HAND_TOKEN", ""))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_run = sub.add_parser("run")
    p_run.add_argument("script_file")
    p_run.add_argument("--save-evidence", default="")
    p_run.add_argument("--no-stop", action="store_true", help="失败也继续跑完")
    p_run.add_argument("--async", dest="async_", action="store_true", help="异步执行（服务端 job＋自动轮询，长脚本防隧道断连）")
    args = ap.parse_args(argv)

    client = AgentClient(args.host, args.port, args.token)
    if args.cmd == "status":
        print(json.dumps(client.status(), ensure_ascii=False, indent=1))
        return 0
    with open(args.script_file, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if args.async_:
        if isinstance(payload, dict):
            payload = dict(payload, **{"async": True})
        else:
            payload = {"script": payload, "async": True}
    result = client.run_script(payload, stop_on_error=not args.no_stop)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if args.save_evidence:
        for row in result.get("results", []):
            if row.get("evidence"):
                # 2026-11-11 真机修复：服务端 evidence 字段回的是全路径，取 basename 再拉
                saved = client.fetch_evidence(os.path.basename(row["evidence"].replace("\\", "/")), args.save_evidence)
                print("证据帧已存: %s" % saved)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
