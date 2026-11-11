# CHANGELOG

## 0.2.0（2026-11-11 夜，硬化版）

- **B1 CDP 桥自愈**：`CDPClient.send` 支持单调用超时；一次超时即判定桥楔死（标死＋关 socket，后续调用快速失败），`CDPBridge` 在下一次调用时自动丢弃旧连接、按原调试地址重新发现页面（优先按 URL 找回同一页）并重建一次，不再需要重启服务。`Target.getTargets` 超时收紧到 8s。`wait_for` 单次上限 30s（内部 0.3s 短轮询），更长等待由调用方分段下发。
- **B2 鉴权补齐**：`GET /status`、`GET /`、`GET /stream.mjpeg` 现均校验 token（此前仅 /actions 与 /evidence 校验，状态页会漏出前台窗口标题、屏幕流为全屏画面）。校验方式：`X-AgentHand-Token` 头，或 `?token=` 查询参数（手机浏览器看流用）；config.token 为空时仍不校验（本机调试口径不变）。无/错 token 返回 401。
- **B3 证据帧新鲜度**：`shot` 结果新增 `sha256` 与 `stale_suspect`——与上一帧哈希相同时标 `stale_suspect=true`（同帧复用/抓帧延迟嫌疑），调用方据此决定是否重取。
- **B4 服务端异步执行**：`POST /actions` 带 `async:true` 立即返回 `202 + job_id`，后台串行执行，`GET /result/<job_id>`（需 token）轮询终态——长脚本不再被中间隧道约 20 秒断连腰斩。客户端 `run --async` 自动轮询。
- **测试**：新增 tests/test_hardening.py 11 项（静默 WS 端点超时标死、桥重建、路由 401/200/查询参数、shot 重帧判定、异步 job 往返/404/401）；全量 40/40 通过。

## 0.1.0（2026-11-11，首版验证）

- v1 骨架：常驻服务（状态机＋心跳＋审计）＋MJPEG 屏幕流＋纯标准库 CDP 线（穿 iframe）＋批量动作协议＋剪贴板粘贴；observe-only 默认、圈定硬规则、锁屏如实上报、单实例＋会话 0 守卫。
- 笔记本真机首部署并完成验收闭环（记事本全链＋已登录 ChatGPT 网页发送-回复全链）；真机修复 wininput 剪贴板 64 位句柄截断、client 证据帧 basename。
