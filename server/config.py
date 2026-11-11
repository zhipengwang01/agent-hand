# -*- coding: utf-8 -*-
"""AgentHand服务配置：只用自有 JSON（UTF-8 无 BOM），不碰任何第三方软件配置。

配置项（config.json，放在服务目录）：
  bind_host   监听地址；"auto" = 优先 Tailscale(100.64/10) 地址，其次局域网，最后 127.0.0.1
  port        控制面 HTTP 端口（默认 18765）
  token       调用口令（X-AgentHand-Token 头）；空 = 不校验（仅建议本机调试）
  observe_only 观察模式：true 时拒绝一切会改变机器状态的动作（设计稿 §6：新能力先 observe）
  stream      是否开启屏幕流（MJPEG）
  stream_max_width / stream_quality / stream_fps_max  屏幕流参数
  heartbeat_interval  心跳刷新秒数（≤5）
  evidence_keep_days  证据帧/审计保留天数（默认 7，与回流副本同口径）
  data_dir    数据目录（心跳、审计、证据帧）；相对路径以服务目录为根
"""
import json
import os
import socket

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "bind_host": "auto",
    "port": 18765,
    "token": "",
    "observe_only": True,
    "stream": True,
    "stream_max_width": 1280,
    "stream_quality": 60,
    "stream_fps_max": 10,
    "stream_fps_idle": 2,
    "heartbeat_interval": 2,
    "evidence_keep_days": 7,
    "data_dir": "data",
    # 标题含这些词的窗口一律拒绝截图（证据面）；大小写不敏感
    "sensitive_title_words": ["password", "密码", "口令", "credential", "凭据", "1password", "bitwarden", "keepass"],
}


def load_config(path=None):
    """读 config.json 并与默认值合并；文件不存在则用默认值。"""
    cfg = dict(DEFAULTS)
    path = path or os.path.join(BASE_DIR, "config.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        cfg.update(user)
    # data_dir 归一化为绝对路径
    dd = cfg["data_dir"]
    if not os.path.isabs(dd):
        cfg["data_dir"] = os.path.join(BASE_DIR, dd)
    return cfg


def resolve_bind_host(cfg):
    """把 bind_host="auto" 解析成具体地址：Tailscale 网段优先。"""
    host = cfg.get("bind_host", "auto")
    if host != "auto":
        return host
    candidates = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in candidates:
                candidates.append(ip)
    except OSError:
        pass
    for ip in candidates:  # 100.64.0.0/10 是 Tailscale CGNAT 网段
        try:
            first, second = (int(x) for x in ip.split(".")[:2])
            if first == 100 and 64 <= second <= 127:
                return ip
        except ValueError:
            continue
    for ip in candidates:  # 其次局域网私有地址
        if ip.startswith(("192.168.", "10.")) or any(ip.startswith("172.%d." % n) for n in range(16, 32)):
            return ip
    return "127.0.0.1"
