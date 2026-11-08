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
selftest.py         离线自测（复读机 + B站解析 + 开关，不联网）
bot/
  config.py         读 .env
  log.py            UTF-8 日志（本机 GBK 坑）
  router.py         分流判断（纯函数）
  commands.py       斜杠命令表 / 权限
  dispatch.py       消息并发分发（同人保序）
  reply.py          先回执后答复（慢消息不冷场）
  ratelimit.py      滑动窗口限流（/raw 防刷）
  repeat.py         群复读（连续同文本 + 多人 → 复读）
  bili.py           B站分享识别（找链接 → 查接口 → 发卡片）
  keys.py           Esc 键监听（优雅退出）
  agent.py          转发给本地 AI 网关（= 人格）
  ecust.py          直连学校网关（裸模型，可配人设）
  history.py        按用户存的对话历史（给裸模型记住上文）
  qq/
    auth.py         取 token + 缓存
    gateway.py      取 WS 地址
    client.py       长连接：心跳 / 事件 / 重连
    api.py          发私聊 / 群消息 / 图文卡片
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
- `/bili` — 查看 / 开关本群的 B站识别（仅管理员）
- `/whoami` — 显示你的 openid（加管理员白名单用）
- `/restart` — 重启机器人（仅管理员；无白名单时所有人都是管理员）

对话历史存在 `data/ecust_history.json`；人设可用 `.env` 的 `ECUST_SYSTEM_PROMPT` 配。

## 群聊功能

### 复读机（`bot/repeat.py`）

群里没人 @ 机器人、也没人发 B 站链接的普通消息 → 喂给复读机：

- 连续 `REPEAT_MIN_COUNT` 条**完全相同**的文本，且**至少 2 个不同的人**发过 → 触发
  （一个人单刷不算；默认 3 条）
- 触发后不立即发，等 `REPEAT_DELAY` 秒（默认 30）；期间同群出现**不同文本** → 取消
  （话题继续了就不追）
- **同一串只复读一次**，之后继续发相同内容不再触发，直到出现别的文本
- 每个群各自维护状态，互不串扰

⚠️ 复读走的是**主动消息**（不带 `msg_id`）：官方群被动回复窗口只有 5 分钟，
复读本身是延后触发的，早过期了。主动消息受频控限制（未认证 30/qpm、单群 20/qpm）。

### B站分享识别（`bot/bili.py`）

消息里带 B 站链接 → 自动查视频信息 → 回复**纯文本**：

```
某视频标题
UP：某某某　时长：3:21
BV1GUH56pEHj
```

- **BV 号单独一行、不加任何前缀** → 在 QQ 里双击就能复制 → 粘到 B 站 APP 里直接搜
- **默认不带 URL**：QQ 会把 bilibili.com 的链接渲染成小程序预览（点进去是 B 站小程序，
  不是 APP），而这里的目的就是拿到能复制的 BV 号。需要链接时置 `BILI_LINK=1`
- 想发图文卡片（带封面）置 `BILI_CARD=1`（默认 0）
- 官方协议**没有** OneBot 那套 `raw_message` / CQ 码，所以不能照抄 Nico 版。
  这里的做法是**在事件数据里到处找链接**：`content` / `ark_data.fields.jump_url` /
  `msg_elements[]`（引用消息）/ `attachments[].url`
- 短链（b23.tv）跟一次 302 拿 BV 号（只读 Location，不下载页面）；结果按短链缓存
- 拿不到封面 / 卡片发失败 → 自动退回纯文本，**不会让整条消息发不出去**

开关（`.env`）：

| 变量 | 作用 |
| --- | --- |
| `BILI_ENABLE` | 总闸，`0/false/off` = 全关 |
| `BILI_MODE` | `black`（默认，默认开）/ `white`（默认关，只开名单里的群） |
| `BILI_OFF_GROUPS` | 黑名单初值（群 openid，逗号分隔） |
| `BILI_ONLY_GROUPS` | 白名单初值 |
| `BILI_CARD` | `1` = 发图文卡片；默认 `0` = 发纯文本 |
| `BILI_LINK` | `1` = 回复里带完整 URL；默认 `0` |

名单持久化在 `data/bili_off.json`，运行时用 `/bili on` / `/bili off` 改（仅管理员）。
私聊不受按群开关影响（只看总闸）。

### 群里发命令

群里**不 @ 机器人**也能发命令：

- **管理员**（群主 / 群管理员 / openid 白名单）→ 任何命令都响应
- **其他人** → 只响应 `GROUP_COMMANDS` 里列的公开命令（默认 `help,time,choose,echo,whoami`）
- 不在允许范围内的 → **静默忽略**（不在群里刷屏说「只有管理员能用」）

### 管理员怎么判（两条路，任一成立就算）

1. **群角色**：官方事件的 `author.member_role` 是 `owner`（群主）/ `admin`（管理员）
   → 直接就是管理员。群里最可靠的判据。
2. **openid 白名单**：`.env` 的 `ADMIN_OPENID`，**可以写多个**（逗号分隔）。

⚠️ 官方协议里同一个人在**私聊和群里是两个不同的 openid**（私聊 `user_openid` /
群里 `member_openid`），只有 `author.id` 是跨场景稳定的。所以白名单比对时会拿
`author.id` / `user_openid` / `member_openid` 三个候选值去撞，填哪个都能命中。

不确定自己的 openid 就在 QQ 里发 `/whoami`，会把当前能拿到的所有标识都列出来。

### 群聊上下文隔离

群里按 `group_openid:member_openid` 隔离 session_key，不同群、不同人都不串上下文。

### 群消息的协议限制

- **GROUP_MESSAGE_CREATE**（不 @ 的群消息）需要群主在 QQ 里开启「机器人可获取的群聊消息范围 = 全部消息」，
  否则服务端根本不推这个事件 —— 复读机和 B站识别都依赖它
- 群 @ 机器人（GROUP_AT_MESSAGE_CREATE）永远可用

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
- [x] 群聊地基：群消息收发 / @ 剥离 / 管理员权限 / 引用回复（REFIDX）/ 群上下文隔离
- [x] 群复读机（bot/repeat.py）+ 离线自测（selftest.py）
- [x] B站分享识别（bot/bili.py）+ 图文卡片发送 + /bili 按群开关
- [ ] 图片 / 富媒体消息（msg_type=7，需先上传 file_info）
- [ ] 人设管理 /system、/forget、/balance
- [ ] 定时推送

## 官方协议明确做不到的（别再设计）

- 群聊表情表态 —— 官方仅频道支持
- 合并转发 —— 群/私聊都没有这个接口
- 戳一戳 —— 无接口