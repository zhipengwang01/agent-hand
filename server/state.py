# -*- coding: utf-8 -*-
"""状态机（纯逻辑层）：探针注入式设计，云端可单测。

心跳 JSON 结构（版本 heartbeat/1）：
  {
    "schema": "agent-hand/heartbeat/1",
    "ts": 1710000000.0,            # 本地生成时刻（epoch 秒）
    "host": "<HOSTNAME>",
    "online": true,                # 本进程活着即 true；离线靠心跳缺失判定（读端比对 ts）
    "disabled": false,             # Disable 开关状态，如实上报「已停用」
    "locked": true/false/null,     # null = 探针不可用（未知，不许猜）
    "foreground": {"hwnd":..,"title":".."} | null,
    "idle_sec": 12.3 | null,       # 距最后键鼠输入秒数
    "session": 1 | null,
    "version": "0.2.0"
  }
"""
import json
import os
import platform
import time

VERSION = "0.2.0"
SCHEMA = "agent-hand/heartbeat/1"


def build_heartbeat(probes, disabled=False, now=None):
    """probes: dict，键 is_locked/foreground/idle_sec/session，值为零参函数（可抛异常→记 None）。"""
    now = time.time() if now is None else now

    def safe(key):
        fn = probes.get(key)
        if fn is None:
            return None
        try:
            return fn()
        except Exception:
            return None

    return {
        "schema": SCHEMA,
        "ts": now,
        "host": platform.node(),
        "online": True,
        "disabled": bool(disabled),
        "locked": safe("is_locked"),
        "foreground": safe("foreground"),
        "idle_sec": safe("idle_sec"),
        "session": safe("session"),
        "version": VERSION,
    }


def is_stale(heartbeat, now=None, max_age=10.0):
    """读端判定心跳是否过期（进程死了就不会再写，ts 自然变老）。"""
    if not heartbeat or "ts" not in heartbeat:
        return True
    now = time.time() if now is None else now
    return (now - heartbeat["ts"]) > max_age


def write_heartbeat_atomic(path, heartbeat):
    """临时文件＋替换，保证读端永远看到完整 JSON。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(heartbeat, f, ensure_ascii=False)
    os.replace(tmp, path)


def default_probes():
    """真机探针（winutil）；云端 import 时 winutil 探针自然返回 None。"""
    import winutil
    return {
        "is_locked": winutil.is_locked,
        "foreground": winutil.foreground_window,
        "idle_sec": winutil.last_input_age_sec,
        "session": winutil.current_session_id,
    }
