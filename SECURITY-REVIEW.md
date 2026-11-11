# SECURITY REVIEW — agent-hand-skill 仓库自审（2026-11-11 夜）

状态：**骨架已备、未 init、未建远程仓、未上传**。本文件为发布前自审记录，上传须经仓库所有者逐项批准。

## 1. 仓库文件清单（拟入库，共 31 个）

| 类别 | 文件 |
|---|---|
| 文档 | SKILL.md、README.md、CHANGELOG.md、LICENSE、SECURITY-REVIEW.md（本文件）、.gitignore |
| 服务端 | server/{agent_hand,actions,audit,capture,cdp,config,scope,state,wininput,winutil}.py、server/config.example.json |
| 调用端 | client/agent_client.py |
| 部署 | deploy/{install,uninstall,disable,enable}.ps1 |
| 测试 | tests/{test_protocol,test_scope,test_state,test_executor,test_encoder,test_cdp_smoke,test_hardening}.py |
| 示例 | examples/notepad-closure.json、examples/web-attach-file.json（全占位符） |

明确**不入库**：内部工作目录的 evidence/（真机截图）、NIGHT-LOG.md、部署记录版 README（内含真机环境细节）、client/fin_replay.json（自家用例，已被中性 examples 取代）、任何 config.json/token/审计/心跳文件（.gitignore 已覆盖 config.json、data/、evidence/、*.log、*token*）。

## 2. 敏感词扫描（实测，2026-11-11 23:3x）

对骨架全目录执行 grep，模式（原文不入档）：所有者的邮箱片段与邮箱域名、真实 Tailscale IP 前缀、真实机器名、真实用户名与姓氏、已部署 token 的前缀、内部连接别名、32 位十六进制串（token 形状）。

结果：
- 主模式组：**零命中**。
- `C:\Users` 命中 2 处，均为 examples 中的 `C:\Users\<USER>\...` 占位符（参数化写法，非真实路径），判定可保留。
- 服务端代码中的 `192.168./10.` 为私网网段判定逻辑（通用代码，非专属值）；tests 中的 `C:\a.jpg` 为假测试数据。均非敏感。

## 3. 代码面复核要点

- server/client/deploy 全部参数化：路径走 `%USERPROFILE%`/`$env:`、主机名运行时取 `platform.node()`、绑定地址配置驱动（auto 探测 Tailscale/局域网/回环），无硬编码个人值（部署期发现的 4 处已在 0.1 阶段改为占位符，本次扫描复核为零）。
- 安全模型即产品的一部分且在 SKILL.md/README 明示：token 鉴权（0.2.0 起含 /status 与 /stream）、默认 observe-only、圈定硬规则、审计日志、一键停手、登录/验证码不代办。
- LICENSE 为 MIT 模板，版权人处为占位符 `<COPYRIGHT HOLDER>`，**发布前必须由所有者定署名**。

## 4. 待仓库所有者批准事项（未批准前不动作）

1. 仓库名与可见性（建议 `agent-hand`，先 private 后转 public，或直接 public）。
2. LICENSE 版权署名（个人名 / 组织名 / 化名）。
3. 是否包含 tests/ 与 deploy/（建议包含：体现成熟度与可复现安装）。
4. SKILL.md 的 status 由 `draft-awaiting-owner-approval` 改正式版、版本号与 tag 口径（v0.2.0）。
5. README 中「真机实测沉淀」类表述是否保留（当前 SKILL.md 第 5 节为通用化后的坑位清单，无具体机器信息）。

## 5. 发布动作边界

本骨架只做到「待批准」：未执行 git init、未建远程仓、未 push。批准后也应先 private 建仓、自审复扫一遍再转 public。

## 6. 决定记录（2026-11-11 23:45）

仓库所有者在主聊天明确授权 Astro 全权决定第 4 节事项，决定如下：①仓库名 `agent-hand`，先 private 建仓、复扫后转 public；②LICENSE 署名 Zhipeng Wang；③包含 tests/ 与 deploy/；④SKILL.md status 改 published，首版 v0.2.0（版本口径以 CHANGELOG 与代码内 VERSION 为准）；⑤README/SKILL 第 5 节的通用化坑位表述保留。
