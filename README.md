# PURANA_ECUST

> 自主设计的 **QQ 机器人代理网关（Agent Gateway）** —— 把 QQ 消息智能分流到「本地命令 / 本地 AI 网关 / 学校大模型网关」三路，双协议支持，生产可用。Python 从零实现，仅依赖 QQ 官方开放平台协议。

**技术栈**：Python · asyncio · QQ 开放平台官方 WebSocket · OpenAI 兼容 API

**核心特性**：
- 🔀 **智能路由门房**：以 `router.py` 纯函数做消息分流——斜杠命令本地处理、普通对话转发 AI 网关、特定命令直连裸模型，职责单一、可测试
- ⚡ **并发不堵**：消息后台并发分发（同人严格保序），慢消息先回执后答复（`msg_seq` 续接），体验流畅
- 🔒 **权限与防刷**：命令权限分层 + 滑动窗口限流，非管理员动作被严格约束
- ♻️ **健壮连接**：心跳 / 断线重连 / 鉴权 token 缓存，长连接稳定
- 🔧 **兵器级工程配套**：开发自动重载（崩溃退避拉起）、隐藏后台常驻、错误日志分档提示、连通性诊断脚本

**定位**：这是**自研的 QQ 机器人底座**（门房架构）。真正的"人格/记忆/工具"由转发到的 AI 网关承担，本仓库负责连接、路由、并发行、权限、运维。

---

## 架构

```
手机QQ → QQ 开放平台 ──WebSocket──▶ 本程序
                                     ├─ "/" 开头    → bot/commands.py   本地命令，不回
                                     ├─ 其他消息     → bot/agent.py  → localhost:18789  本地 AI 网关（人格/记忆/工具）
                                     └─ /models 等   → bot/ecust.py  → ECUST 大模型网关（裸模型，可配人设）
```

三条路的职责划分：
- **本地命令** —— 快、稳、不算模型，适合状态/诊断类
- **本地 AI 网关**（默认主路）—— 完整人格 + 记忆 + 工具调用
- **ECUST 裸模型**（可选）—— 不经网关直连学校大模型，按 openid 记住上文，供实验/切换/兜底

## 目录

```
run.py              入口
dev.py              开发用：改代码自动重启
dev.bat             双击就能跑 dev.py
check_models.py     诊断：测 ECUST 各模型连通性
bot/
  config.py         读 .env
  log.py            UTF-8 日志（本机 GBK 坑）
  router.py         分流判断（纯函数）
  commands.py       斜杠命令表 / 权限
  dispatch.py       消息并发分发（同人保序）
  reply.py          先回执后答复（慢消息不冷场）
  ratelimit.py      滑动窗口限流（/raw 防刷）
  keys.py           Esc 键监听（优雅退出）
  agent.py          转发给本地 AI 网关（= 人格）
  ecust.py          直连学校网关（裸模型，可配人设）
  history.py        按用户存的对话历史（给裸模型记住上文）
  qq/
    auth.py         取 token + 缓存
    gateway.py      取 WS 地址
    client.py       长连接：心跳 / 事件 / 重连
    api.py          发私聊消息
```

## 跑起来

```powershell
cd D:\CIC\QQbot\PURANA_ECUST
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe run.py
```

跑起来后，在终端按 **Esc** 即可优雅退出（`Ctrl+C` 也可以）。

## 开发（自动重启）

懒得每次手动关了再开？用监督进程：

```powershell
.\venv\Scripts\python.exe dev.py     # 或直接双击 dev.bat
```

- 保存任意 `.py` / `.env` → **自动重启**（venv / __pycache__ / data 不监视）
- 轮询间隔 1 秒（遍历时已剪掉 venv，一次扫描 ~0.3ms，基本无开销；想改就改 `dev.py` 的 `POLL`）
- 子进程崩了自动拉起，且**退避重试**（1→2→4→8→16→30s 封顶，连崩 5 次会提醒）
- 子进程**正常退出（如 `/restart`）→ 1 秒后直接拉起**，不当崩溃处理
- 按 **Esc** 退出（会关掉子进程）
- 子进程里的 Esc 退出会被自动禁用（注入 `NICO_NO_ESC=1`），免得两个进程抢键盘

配置在 `.env`（照着 `.env.example` 填；`.env` 不进 git）。

## 命令

本地命令（不转发）：

- 公开：`/test502`、`/time`、`/help`、`/raw`、`/forget`（可用 `PUBLIC_COMMANDS` 改）
- 仅管理员：`/echo`、`/models`、`/model`、`/ping`、`/restart`

非管理员发管理类命令会被拒；`/raw` 对非管理员有防刷（`RAW_RATE_LIMIT` / `RAW_RATE_WINDOW`，默认 3 次/60 秒）。

裸模型命令（打学校网关，回复**不是本机人格**，但会记住上文）：

- `/models` — 列出 ECUST 可用模型
- `/model [名字]` — 查看 / 切换当前 ECUST 模型（按 openid 记忆）
- `/raw <内容>` — 用当前 ECUST 模型回答（带上下文，最近 20 条）
- `/ping` — 测当前 ECUST 模型连通性（不记入上下文）
- `/restart` — 重启机器人（仅管理员；无白名单时所有人都是管理员）

对话历史存在 `data/ecust_history.json`；人设可用 `.env` 的 `ECUST_SYSTEM_PROMPT` 配。

## 并发与响应（为什么不会堵）

- **并发分发**（`bot/dispatch.py`）：消息收到就丢后台 task，收消息的循环不被堵；
  **不同人并行**（`MAX_CONCURRENT` 封顶），**同一个人仍严格按顺序**（每人一把锁）
- **先回执后答复**（`bot/reply.py`）：`ACK_AFTER` 秒内没算完，就先回一句 `ACK_TEXT`（`msg_seq=1`），
  正式回复再发（`msg_seq=2`）。快消息不会多这一条

## 诊断

报 `fetch_network / upstream=172.17.0.2:3000 / aborted` 这类错，说明是**学校网关到它上游模型**那段断了（超时/负载），不是本地代码。测一下哪个模型活着：

```powershell
.\venv\Scripts\python.exe check_models.py              # 测 .env 的默认模型
.\venv\Scripts\python.exe check_models.py glm-5.3      # 测指定模型（可多个）
.\venv\Scripts\python.exe check_models.py --all        # 测所有对话模型
```

QQ 里也可以用 `/ping` 快测当前模型。

## 后台常驻（关掉 VSCode 也照跑）

双击 **`run_hidden.vbs`** 即可：以隐藏窗口后台启动（先跑 `run.cmd` → `dev.py` → `run.py`）。
Windows 的 vbs 把进程挂在一个隐藏控制台上。

- 启动：双击 `run_hidden.vbs`
- 停止：双击 `stop.cmd`（隐藏运行后没有 Esc 可用）
- 看状态：双击 `status.cmd`（在看跑不跑 + 两个日志的尾巴）
- 看日志：双击 `logs.cmd`（实时滚 `nico.log`）；`logs.cmd dev` 看 `data\dev.log`
- 日志：`data\dev.log`（监督进程输出，UTF-8）+ `data\logs\nico.log`（bot 自己，UTF-8，2MB × 5 份轮转）

⚠️ `.cmd` / `.vbs` 里**只能写纯 ASCII**：cmd 按 GBK 读批处理，UTF-8 中文会把行读乱（已踩：stop.cmd 报“此时不应有 )”）。中文只出现在程序输出里。

⚠️ 切换时先停旧的再起新的——同一个 AppID 只能有一个消费者。

想开机/登录自启，可建计划任务（需管理员确认）：

```
schtasks /create /tn "PURANA_ECUST Bot" /tr "wscript.exe \"D:\CIC\QQbot\PURANA_ECUST\run_hidden.vbs\"" /sc onlogon
```

## ⚠️ 切换注意

同一个 AppID 只能有**一个**消费者。启动本程序前，先停掉 OpenClaw 的 qqbot 插件，
否则两边抢消息、消息可能被处理两遍。

顺序：写好看好 → 停 C# bot → 停插件 → 起本程序。

## 已完成 / 待办

- [x] 骨架 + 鉴权 + 长连接 + 心跳 + 重连
- [x] 斜杠命令本地处理
- [x] 非命令消息转发本地 AI 网关（按 openid 分会话）
- [x] ECUST 裸模型命令（/models /model /raw /forget /ping，带上下文记忆）
- [x] 连通性诊断脚本 check_models.py
- [x] Esc 优雅退出（bot/keys.py）
- [x] 开发自动重启（dev.py / dev.bat，带崩溃退避）
- [x] /restart 命令（管理员；带 dev.py 时交给它拉起，否则 os.execv）
- [x] 并发分发 + 先回执（bot/dispatch.py / bot/reply.py）
- [x] 命令权限分层（PUBLIC_COMMANDS）+ /raw 防刷（bot/ratelimit.py）
- [x] 后台常驻：run_hidden.vbs / run.cmd / stop.cmd / logs.cmd / status.cmd
- [x] 错误日志带到点：异常类型 / 用时 / 响应片段 + 给用户的提示语分档（超时/5xx/4xx 各说各的）
- [ ] 群消息（目前只处理私聊）
- [ ] 图片 / 富媒体消息