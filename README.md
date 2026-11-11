# AgentHand

**Agent 的最后一公里：从会说，到会做。**

聊天里的 AI 只会动嘴——它有脑子，缺只手。AgentHand 给它装上一只真手：一个常驻在你 Windows 电脑里的服务，让 agent **看得见屏幕**（实时流＋圈定取证帧）、**摸得着鼠标键盘**（坐标线＋剪贴板粘贴）、**钻得进浏览器内部**（纯标准库手写 CDP，穿 iframe 操作）。agent 一次下达一串**批量动作脚本**，它在本机以原生速度连着干完，只回结构化结果——一次任务几百 token，而不是几万。默认只观察、每个动作留审计、桌面留着一键停手开关：**手是 AI 的，方向盘是你的。**

## 架构

```
agent ──HTTP(控制面, 需 token)──► agent_hand.py(目标机常驻, 交互会话)
  client/agent_client.py            ├─ state     状态机/心跳(≤5s): online/locked/foreground/idle
                                    ├─ capture   抓屏 + MJPEG 屏幕流(自适应帧率)
                                    ├─ cdp       纯标准库 WebSocket + CDP 最小集(穿 iframe)
                                    ├─ actions   批量动作协议 + 执行器(审计/观察模式/锁屏拒绝/楔死自愈)
                                    └─ wininput  剪贴板粘贴 + SendInput(坐标线)
用户手机 ──MJPEG 数据面──► GET /stream.mjpeg?token=…  实时屏幕
```

## 设计纪律

- **省 token**：一串动作一次往返；AI 只看圈定证据帧（带 sha256＋是否与上一帧重复的 `stale_suspect` 标记）。
- **观察先行**：`observe_only` 默认开，改变状态的动作一律拒绝，验收后才放行。
- **圈定硬规则**：截图必须圈定窗口/区域/元素；密码类窗口拒截；锁屏如实上报、拒绝输入。
- **可停可查**：桌面一键停手开关；每个动作落审计（含证据哈希）。
- **少依赖**：目标机只需 Python 3.11+ 与 Pillow；CDP 的 WebSocket 为纯标准库实现。

## 快速开始

1. 目标机装 Python 3.11+（含 pythonw）与 Pillow。
2. PowerShell 进 `deploy\` 运行 `.\install.ps1`：生成带随机 token 的 config、注册登录自启任务、放心跳验证。记下 token。
3. 调用端：`python client/agent_client.py --host <TARGET_HOST> --token <TOKEN> status`
4. 按 `SKILL.md` 第 4 节跑验收（记事本闭环示例在 `examples/`）。
5. 浏览器线：用桌面 `Edge-Debug-AgentHand` 快捷方式（独立 profile）打开调试浏览器，用户本人登录目标站点后 agent 再 `cdp_connect`。

## 测试

`python -m unittest discover -s tests`（40 项：协议/圈定/状态机/执行器/编码器/CDP 假端点/硬化项）。

## 版本与限制

当前 0.2.0，变更见 CHANGELOG.md。已知限制：跨源 iframe 坐标合成未做（iframe 内回退 JS click 并注明）；屏幕流为 MJPEG（H.264/WebRTC 在路线图）；长脚本请用异步执行（`async:true` 提交后轮询结果），或分段发送。平台为 Windows 11。

---

# AgentHand (English)

**The last mile for AI agents: from saying to doing.**

A chat model can only talk — it has a brain, but no hands. AgentHand makes it act: a resident service on your Windows PC that lets an agent see the screen, drive mouse and keyboard, and reach inside the browser — then execute batched action scripts at native speed and report back structured results, at a few hundred tokens per task instead of tens of thousands. A small resident service on the target machine — token-authenticated, audited, observe-only by default — executes **batched action scripts** locally: browser work over a pure-stdlib CDP line (iframe-aware), native/self-drawn apps over a coordinate line (real mouse + clipboard paste). The agent receives structured results plus scoped evidence frames only; the live screen is a separate MJPEG data plane for the human owner and never passes through the model.

**Design rules**: batch everything (one round-trip per script); observe-first (mutating actions refused until explicitly enabled); scoped screenshots only (window/rect/element, sensitive windows refused, lock state reported honestly); a desktop kill switch; a full audit log with evidence hashes; minimal dependencies (Python 3.11+ and Pillow on the target; the WebSocket/CDP client is stdlib-only).

**Quick start**: install Python + Pillow → run `deploy\install.ps1` (generates a random token, registers a logon-triggered task, verifies heartbeat) → `python client/agent_client.py --host <TARGET_HOST> --token <TOKEN> status` → run the acceptance flow in `SKILL.md` §4 (a Notepad end-to-end example lives in `examples/`).

**Tests**: `python -m unittest discover -s tests` — 40 tests. **Version** 0.2.0 (see CHANGELOG.md). Windows 11. Known limits: no cross-origin iframe coordinate composition yet (JS-click fallback inside iframes, noted in results); MJPEG stream (H.264/WebRTC planned); for long scripts use async execution (`async:true`, then poll for the result) or split them into segments.
