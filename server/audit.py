# -*- coding: utf-8 -*-
"""审计日志：每动作一行 JSON（JSONL），只存本机，按天分文件。

行结构：{"ts":..., "host":..., "action":"find_click", "target":"...", "ok":true,
        "detail":"...", "evidence":"20261111-213000-shot.jpg"|null, "evidence_sha256": "..."}
"""
import hashlib
import json
import os
import platform
import threading
import time


class AuditLog:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "audit")
        os.makedirs(self.dir, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, ts):
        day = time.strftime("%Y%m%d", time.localtime(ts))
        return os.path.join(self.dir, "audit-%s.jsonl" % day)

    def record(self, action, target, ok, detail="", evidence=None):
        ts = time.time()
        sha = None
        if evidence and os.path.exists(evidence):
            try:
                h = hashlib.sha256()
                with open(evidence, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                sha = h.hexdigest()
            except OSError:
                sha = None
        row = {
            "ts": ts,
            "host": platform.node(),
            "action": action,
            "target": target,
            "ok": bool(ok),
            "detail": detail,
            "evidence": os.path.basename(evidence) if evidence else None,
            "evidence_sha256": sha,
        }
        with self._lock:
            with open(self._path(ts), "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row
