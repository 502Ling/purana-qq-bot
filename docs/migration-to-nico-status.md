# PURANA_ECUST → 向 Nico_21599 看齐：现状与迁移计划

> 起稿：2026-11-07（大肥鱼）
> 目的：记录 "把 purana-qq-bot 的功能往 Nico_21599 方向完善" 这件事的现状，
> 免得会话断档后不知道从哪开始。所有结论都是对着两个仓库的代码/README 核过的。

## 一句话结论

**Nico_21599 本来就是 PURANA_ECUST 的分支**（同样的门房逻辑，只是传输层从
「QQ 官方开放平台 WebSocket」换成了「NapCat / OneBot v11 反向 WS」，因涉及非官方
协议仍有封号风险，所以单独开了一份跑小号）。Nico 已经把 purana 当初没做完的
功能全部补上了。

所以「像 Nico 那样完善 purana」= **把 Nico 里已经实现的那批功能，回迁/补到
官方协议版（PURANA_ECUST）上**，而不是从零发明。

## 两个仓库位置

| 项目 | 路径 | 传输层 | 协议风险 |
|---|---|---|---|
| PURANA_ECUST（官方版） | `D:\CIC\QQbot\PURANA_ECUST` | QQ 开放平台官方 WebSocket | 无（官方） |
| Nico_21599（分支/小号版） | `D:\CIC\QQbot\Nico_21599` | NapCat / OneBot v11 反向 WS | 有（非官方，封号风险，只用小号） |

- purana 最近提交：`9e56c7b chore: remove .vscode ...`，`main` 工作区干净。
- git remote：`https://502Ling@github.com/502Ling/purana-qq-bot.git`

## purana 现状（官方版，能力基线）

已实现（README「已完成」勾选部分）：
- 骨架 + 鉴权 + 长连接 + 心跳 + 重连（QQ 官方 WS，`bot/qq/`）
- 斜杠命令本地处理（`bot/commands.py`）
- 非命令消息 → 本地 AI 网关（`bot/agent.py` → localhost:18789，按 openid 分会话）
- ECUST 裸模型命令：`/models` `/model` `/raw` `/forget` `/ping`
  （`bot/ecust.py` + `bot/history.py`，按 openid 记 20 条上文，人设 `.env: ECUST_SYSTEM_PROMPT`）
- `check_models.py` 连通性诊断
- Esc 优雅退出（`bot/keys.py`）
- 开发自动重启（`dev.py` / `dev.bat`，崩溃退避拉起）
- `/restart` 命令（管理员）
- 并发分发 + 先回执（`bot/dispatch.py` / `bot/reply.py`）
- 命令权限分层 + `/raw` 防刷（`bot/ratelimit.py`）
- 后台常驻脚本 `run_hidden.vbs` / `run.cmd` / `stop.cmd` / `logs.cmd` / `status.cmd`

**没做完 / 缺的（README「待办」，也就是本次要补的方向）**：
- ❌ 群消息（**目前只处理私聊**，`bot/qq/api.py` 只有 `C2C_URL` + `send_c2c`，无群接口）
- ❌ 图片 / 富媒体消息
- 以及 Nico 后来加的一整批（见下）

## Nico 已经补上、purana 没有的功能（迁移候选清单）

对照 Nico_21599 的 README「已完成/待办」，下面这些是 purana 缺、但 Nico 做出来了的：

1. **群聊**（`/` 需 @，`Niko `/`/` 前缀免 @；文本走 `send_group_msg`）
2. **图片发送**（`ctx.image`，QQ 官方版对应 `send_media`/富媒体）
3. **QQ 表情回应** `/react`（别名 `/贴个`，`set_msg_emoji_like`）
4. **合并转发**（`bot/forward.py` 节点构造/发送）
5. **B站分享卡片自动解析**（`bot/bili.py`）
6. **复读机**（`bot/repeat.py`，群内连续重复触发，`REPEAT_MIN_COUNT` / `REPEAT_DELAY`）
7. **拍一拍 / 贴表情事件**（`bot/interactions.py`，带冷却）
8. **Phigros 查分**（`bot/phigros/`：绑定 / 刷新 / RKS / B30 / 出图；需大批资源图）
9. **定时推送**（`bot/push.py`）
10. **人设管理** `/system`（`bot/persona.py` + `data/ecust_persona.txt`）
11. **OpenClaw 会话世代** `/forget`（`bot/sessions.py` + `data/agent_sessions.json`，代理 `/raw`）
12. **OpenClaw 余额查询** `/balance`（`bot/openclaw.py`）
13. **命令前缀收口**（`bot/router.py` 的 `split_command()` + `COMMAND_PREFIXES`）
14. **群内普通消息喂复读机、不烧 token** 的总策略

> 注：Nico 自己的 docs/todo.md 还挂着一些 P2/P3 技术债（清死代码、注释与实现矛盾、
> `ctx.forward` 判空不一致、出图计时用 `datetime.now`、`/贴个` 两个小坑等）。
> 若做回迁，最好连同这些坑一起修，别把坏味道也抄过去。

## 产品决策（2026-11-07，502 拍板）

- **Phigros 查分不做全量**，到时候细说（暂缓，不列入当前迁移）。
- **先打群聊地基**，其余群内能力等地基稳了再说。

### 官方接口能力调研（对着 bot.q.qq.com 文档核过）

针对「表情回应 / 合并转发 / 戳一戳官方有没有接口」的结论：
- ✅ **群聊地基 —— 官方支持，可做扎实**：
  - 发群消息：`POST /v2/groups/{group_openid}/messages`（msg_type：0=文本 / 2=Markdown / 7=富媒体）
  - 收群事件：`GROUP_AT_MESSAGE_CREATE` / `GROUP_MESSAGE_CREATE`
  - 撤回群消息：有。
  - 被动回复：群聊 5 分钟内有效、每条最多回 5 次。
  - 主动消息频控：未认证 30/qpm、认证 60/qpm；单关系 20/qpm；每群每天上限 1000 条。
- ⚠️ **表情回应 —— 仅频道内**：文档明写「表情表态仅支持在频道内使用」，
  接口是 `/channels/{channel_id}/.../reactions`。**群聊无表情表态接口** → 群里 `/react` 官方走不通。
- ❌ **合并转发 —— 官方无接口**：群/C2C 方向没找到合并转发 API。
- ❌ **戳一戳 —— 官方无接口**：无主动拍一拍 API。

> 结论：表情回应 / 合并转发 / 戳一戳在官方协议版上**不做**（没接口，别白设计）。
> 群聊地基（收发 + @ 策略 + 可选撤回）是目前唯一能靠官方接口做扎实的方向，也就是当前任务。

## 迁移计划的障碍点（重点看这里）

- **传输层差异是最大变量**。官方 WebSocket（`bot/qq/`）与 OneBot 反向 WS（`bot/onebot/`）
  事件/发送 API 都不一样。群消息、图片、合并转发、拍一拍、贴表情这批功能，
  **底层跟传输层耦合**（事件类型、发消息 API、资源上传），不是简单把 Nico 文件拷过来就能跑。
  - 好消息：门房骨架（router/dispatch/reply/ratelimit/commands 的表结构）是共通的，
    这部分可以对齐 Nico 的写法。
  - 需要逐模块确认：`bot/qq/api.py` 目前只有 `send_c2c`，要补群消息、图片（QQ 官方
    富媒体走 `files`/`media` 上传接口）、表情回应接口等 —— 这些 QQ 官方 API 与
    OneBot 接口名不同，得按官方开放平台文档实现。
- **资源文件**：Phigros 查分要一大批 `resources/`（avatar/曲绘/字体/评级图等），
  在 purana 里得一并带上（体积大，注意 git 策略）。
- **人设/记忆**：`bot/persona.py`、`sessions.py` 与 ECUST/openclaw 网关强相关，回迁时
  确认环境变量（`.env`）里的 token/地址对得上当前跑 purana 的账号。

## 落地进度（阶段 1：群聊地基——进行中）

### ✅ 已改（2026-11-07）
- **`bot/qq/api.py`**：新增 `send_group_msg()` → `POST /v2/groups/{group_openid}/messages`（文本 msg_type=0），带/不带 `msg_id` 分别对应被动/主动消息；失败记 warning。
- **`run.py`**：
  - `compute_reply()` 增加 `session_key` 参数（agent/ECUST 记忆隔离键：私聊=openid，群里=`group_openid:member_openid`，避免群间串上下文）。
  - `handle_message()` 改为场景驱动：`scene=c2c|group`，发送时按场景选 `send_c2c` 或 `send_group_msg`。
  - `on_event()` 支持 `C2C_MESSAGE_CREATE` / `GROUP_AT_MESSAGE_CREATE` / `GROUP_MESSAGE_CREATE`；群里被 @ 时用 `AT_MENTION_RE` 剥掉 `<@!...>` 占位符；并发保序键群里用 `group_openid`（同群顺序发）、私聊用 `openid`。

### ▶ 当前群策略（v1 最小版）
- 私聊：全处理（照旧）。
- 群：只回 **`GROUP_AT_MESSAGE_CREATE`（被 @）**；`GROUP_MESSAGE_CREATE`（没 @）静默占位，留给后面接复读机。
- 语义/权限：群内命令走 `commands.py`，非管理员调管理命令会被拦（`restart` 二次校验 is_admin），天然安全。
- 引用回复：被 @ 时自动携带 `message_reference`（REFIDX），回复显示为引用。

### ✅ 已改（2026-11-07 第二批）
- **`bot/log.py`**：时间戳改为日期+时间 `%Y-%m-%d %H:%M:%S`（原来只有时分秒）；日志按天轮转，旧文件名 `nico-2026-xx-xx.log`（保留 30 天）。
- **命名统一 nico→purana**（代码 logger 名 10 个模块、环境变量 `NICO_NO_ESC/NICO_SUPERVISED → PURANA_NO_ESC/PURANA_SUPERVISED`、日志基础名 `nico.log → purana.log`、包描述 `NicoPy_ECUST → PURANA_ECUST`）。运维脚本 `run/logs/status.cmd` 同步新环境变量与日志路径（保持纯 ASCII）。
- **`bot/commands.py`（权限调整 + /choose）**：
  - 采用**白名单模型**，公开命令改为 `help, time, choose, echo`（原 `/test502 /raw /forget` 收回给管理员）。
  - `PUBLIC_COMMANDS` 从模块级改为函数式读取 `public_commands()`，修掉 .env 覆盖失效隐患。
  - 新增 `/choose <选项1> <选项2> [...]`：随机选一个；无参/单参/超 10 个各有提示，行为对齐 Nico_21599。
- **`bot/qq/api.py` + `run.py`（引用回复）**：
  - `send_group_msg()` 新增 `msg_ref_id` 参数，携带 `message_reference: {message_id: <REFIDX>}`。
  - `run.py` 新增 `extract_msg_ref_id()`：从群事件 `data.message_scene.ext[]` 解析 `msg_idx=REFIDX_xxx`，一路传入群发送。真机验证：被 @ 的回复能显示为真·引用回复。

### ✅ 已实测确认（2026-11-07）
- 群消息收发、管理员权限正常。
- 群里 `member_openid` 作为管理员标识可用（`ADMIN_OPENID` 在群里匹配正常）。
- 被 @ 时回复为真·引用回复（REFIDX 方案可行）。

### ⏳ 待做（更新）
- [ ] 阶段 2+：图片（富媒体 msg_type=7，需上传 file_info）、B站解析、复读机 等(按下文阶段表续)。

---

## 建议的落地顺序（按价值/依赖排）

**阶段 0：先不对齐传输层，把门房层对齐。**（低风险，纯内部重构）
对齐 Nico 的 `bot/router.py` 前缀收口、`bot/commands.py` 命令表雏形、
`bot/response`（error 分档提示）。保证官方版代码风格和 Nico 一致，方便后续移植。

**阶段 1：群聊——最大缺口。**
给 `bot/qq/` 加群消息的收发（官方 API 群消息接口 + WS 群事件），再把 router 的
群策略（`/` 需 @、前缀免 @、非 @ 喂复读）接上。这是其余群内功能的地基。

**阶段 2：群/富媒体能力。** 图片发送、合并转发、QQ 表情回应、B站卡片解析。

**阶段 3：娱乐/交互。** 复读机、拍一拍/贴表情事件（需群事件就绪）。

**阶段 4：独立服务。** 定时推送（`bot/push.py`）。

**阶段 5（可选，重）**：Phigros 查分 + 资源库。体量最大，单独评估要不要全量带。

> Phigros 是 Nico 的重点功能但和“门房”定位无关；如果 purana 只是想要好用的日常
> QQ 机器人，阶段 1–4 价值更高。建议做完 1–4 再决定 Phigros 要不要。

## 收尾提醒

- 每次会话开工前，先 read 本文件 `docs/migration-to-nico-status.md`，改完同步更新本文件。
- 官方版改完若有破坏性变更，记得 `git status` 确认、提 commit。
- 参考实现来源：`D:\CIC\QQbot\Nico_21599\bot\`（优先看 onebot/、forward.py、
  interactions.py、repeat.py、bili.py、push.py、persona.py、sessions.py）。