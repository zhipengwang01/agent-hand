---
name: agent-hand
description: 给 agent 一只可操作用户 Windows 电脑的「手」：在目标机常驻一个带鉴权、审计与观察模式的本地服务，agent 通过批量动作脚本（CDP 网页线＋坐标线）完成点击、粘贴、附图、截图取证，并有实时屏幕流与心跳状态。Use when an agent needs to see and operate a Windows PC's browser/native apps with auditability and a kill switch.
version: 0.2.0
status: published
---

# AgentHand — Agent 操作手册

你（agent）通过本 skill 在用户的 Windows 电脑上「看屏＋操作」。一切机器专属值都是参数：
`<TARGET_HOST>`（目标机在你网络中的地址，如 Tailscale IP）、`<TOKEN>`（安装时生成的调用口令）、
服务端口默认 `18765`、服务目录 `%USERPROFILE%\agent-hand`。

## 0. 安全模型（先读，不可跳过）

- **鉴权**：所有接口都要 token：HTTP 头 `X-AgentHand-Token: <TOKEN>`；浏览器看流用 `?token=<TOKEN>`。
- **观察先行**：`observe_only=true`（默认）时一切改变状态的动作（点击/输入/导航/附文件）被拒（`OBSERVE_ONLY`）。只有用户批准验收后才可改 `false`，验收完应改回。
- **圈定硬规则**：`shot` 必须带 `window`／`rect`／`selector` 之一，无圈定在校验期被拒（`SCOPE_REQUIRED`）；标题含密码/凭据类词的窗口拒绝截图。**禁止无圈定全屏截图当常规手段**（屏幕流是给用户本人看的数据面，不经 agent）。
- **锁屏如实**：状态机双信号测锁屏；锁屏时 shot/输入直接回 `LOCKED`，不重试、不误诊，先报用户解锁。
- **审计**：每个动作落 JSONL 审计（动作/目标/成败/证据帧 sha256）。出事先查审计，别猜。
- **一键停手**：桌面 `Disable-AgentHand` 置停用标志，服务只报心跳不执行；`Enable-AgentHand` 恢复。
- **登录/验证码**：一律停手请用户本人处理，不代填、不绕验证。

## 1. 安装（目标机，用户在场执行一次）

前置：Windows 11、Python 3.11+（含 pythonw.exe）在 PATH、Pillow 已装（`pip install pillow`，可离线 wheels）。

1. 把本仓库拷到目标机任意目录，PowerShell 进 `deploy\` 运行 `.\install.ps1`（当前用户即可）。
2. 脚本会：拷服务到 `%USERPROFILE%\agent-hand` → 生成 `config.json`（随机 token，**把 token 交给调用端保管**）→ 注册计划任务 `AgentHand`（登录自启、pythonw 无窗口直启、允许电池供电运行）→ 桌面放 Disable/Enable 开关与 `Edge-Debug-AgentHand` 快捷方式（独立 profile 的调试 Edge）→ 启动并打印心跳。
3. 验收前不要改 `observe_only`。

## 2. 状态与屏幕（只读）

- 心跳：`GET http://<TARGET_HOST>:18765/status`（带 token）→ JSON：online/locked/foreground/idle_sec/session/version。心跳文件在服务机 `data\heartbeat.json`（≤5s 刷新；读端以 ts 判活）。
- 屏幕流（给用户看）：`http://<TARGET_HOST>:18765/stream.mjpeg?token=<TOKEN>`，MJPEG，自适应帧率。agent 不消费此流（省 token）。

## 3. 操作（批量动作协议）

调用端：`python client/agent_client.py --host <TARGET_HOST> --token <TOKEN> run <script.json> --save-evidence ./evidence`
脚本是动作数组，**一次发一串、本机连做完、只回结构化结果＋证据帧**（不要一步一往返）：

| 动作 | 字段 | 说明 |
|---|---|---|
| `cdp_connect` | `url` | 连浏览器调试口，如 `http://127.0.0.1:9222` |
| `navigate` | `url` | CDP 导航 |
| `find_click` | `selector` 或 `text` | 跨 frame 查找并点击（穿 iframe；主文档走真实鼠标，iframe 内回退 JS click 并注明） |
| `type_text` | `text` | CDP `Input.insertText`（浏览器内） |
| `set_files` | `selector`, `files[]` | 给文件 input 塞文件（`DOM.setFileInputFiles`，**不弹系统文件框**；路径为目标机绝对路径） |
| `key` | `key` | 如 `Enter`、`ctrl+s`；CDP 未连接时走本机按键 |
| `click_at` | `x`, `y` | 坐标线点击（物理像素，先用证据帧标定） |
| `paste_text` | `text` | 剪贴板粘贴（原生应用/自绘 UI 用） |
| `wait_for` | `selector`/`text`/`pixel_diff`, `timeout_ms` | 本机等待，单次上限 30s；更久分段重发 |
| `shot` | `window`/`rect`/`selector`, `max_width` | 圈定取证；结果带 `sha256` 与 `stale_suspect`（与上一帧相同为 true，重帧不可当新证据） |

纪律：`stop_on_error=true` 默认；失败看 `detail` 的错误码（`NOT_FOUND`/`WAIT_TIMEOUT`/`LOCKED`/`OBSERVE_ONLY`/`SCOPE_REQUIRED`…）再决定换法，同一问题最多换法一次。

## 4. 验收（装完必跑，全过才算交付）

1. 观察模式下发一个含 `paste_text` 的脚本 → 应被 `OBSERVE_ONLY` 拒，审计落行。
2. `/status` 无 token → 401；带 token → 200。
3. 屏幕流取一帧：尺寸正常、非全黑全白。
4. 记事本闭环（见 `examples/notepad-closure.json`）：启动记事本（须在交互会话内启动，如一次性计划任务）→ 点击 → 粘贴 → 另存/保存 → **到文件系统核实文件内容逐字一致**。
5. （可选）浏览器线：调试 Edge 打开测试页 → `cdp_connect` → `wait_for` 命中 → 元素截图。

## 5. 已知坑（真机实测沉淀）

- **Chromium 136+ 在默认 user-data-dir 下可能静默忽略 `--remote-debugging-port`**：改用 profile 副本目录（`Local State`＋`Default`，排除 Cache）启动；个别版本默认目录可直开，先试默认、不行再副本。
- **从 SSH/会话 0 拉起的 GUI 进程会很快整体退出**：GUI 一律经交互会话内的计划任务启动。
- **计划任务包装**：用 pythonw 直启；某些机器上 wscript+VBS 包装会挂死不拉起解释器（无日志无心跳可判）。
- **CDP 桥楔死**（0.2.0 已自愈）：一次调用超时即判定楔死、自动重建连接一次；若仍失败，重启 `AgentHand` 任务。
- **长调用**：单次 HTTP 调用跑完整脚本才回响应，长脚本用 `async:true` 提交（立即回 job_id，客户端 --async 自动轮询 /result），不再被隧道断连腰斩；短脚本直发即可。
- **桌面应用**（Electron 等）多不吃 `--remote-debugging-port`：这类目标走坐标线（截图＋点击＋粘贴）或 UI 树工具，不强求 CDP。

## 6. 卸载

`deploy\uninstall.ps1`（加 `-Purge` 删除服务目录与数据）。
