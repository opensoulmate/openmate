# 国内网关接入：飞书 + 钉钉（对齐微信体验）

P2 需求「国内网关补齐——飞书/钉钉接入」。把 SoulMate agent 接到钉钉/飞书，体验对齐
微信网关（DM 对话直连 agent）。**只做国内平台，不做海外。**

两条通道各支持**两级**：

| 级别 | 能力 | 你要提供的 | 典型用途 |
|------|------|-----------|---------|
| **Level 1 · webhook 机器人快路** | 收 **agent 消息推送**（单向：SoulMate → 群） | 只需 1 个 **webhook 地址** | 通知、报告、定时推送、验收 |
| **Level 2 · 自建应用全双工** | **收消息 → SoulMate 处理 → 回消息**（双向对话） | **appid + secret**（+ 公网回调） | 在钉钉/飞书里跟 SoulMate 对话 |

> 推送体走 **echo 口径**（与 OpenMate `/echo` 回声·消息推送页同一套钉钉/飞书负载格式，
> 不另立山头）。格式真源在 `opensoul/src/echo/dispatcher.py`，接入层
> `acp-proxy/agent/domestic_bridge.py::echo_payload` 与之逐字段一致，测试
> `test_echo_payload_matches_opensoul_echo` 用真 dispatcher 比对，杜绝格式漂移。

集成位置：`openmate/acp-proxy/agent/`（SoulMate 通道层，与微信桥 `wechat_bridge.py`、
统一路由 `message_router.py` 同层，全 Python）。不动微信网关现有链路。

---

## Level 1 · webhook 机器人快路（最快上手，5 分钟）

只需在钉钉/飞书群里加一个**自定义机器人**拿到 webhook 地址，即可收到 SoulMate 真消息推送。

### 钉钉：群自定义机器人

1. 打开钉钉，进入要接收消息的**群** → 群设置（右上角 ⋯）→ **机器人** → **添加机器人**。
2. 选 **自定义机器人（Custom）** → 填个名字（如 `SoulMate`）→ 下一步。
3. 安全设置（**三选一或都不选**）：
   - **自定义关键词**：填 `SoulMate`（推送内容含该词才投递）；
   - **加签**：复制 **Secret**（形如 `SECxxx`）→ 一会儿填到 `DINGTALK_WEBHOOK_SECRET`；
   - **IP 段**：填你服务器出口 IP。
   - 不设安全设置也能用（最简单）。
4. 复制 **Webhook 地址**（形如 `https://oapi.dingtalk.com/robot/send?access_token=xxxx`）。

### 飞书：群自定义机器人

1. 打开飞书，进入要接收消息的**群** → 设置（右上角 ⋯）→ **群机器人** → **添加机器人**。
2. 选 **自定义机器人** → 填名字（如 `SoulMate`）→ 添加。
3. （可选）**签名校验**：复制 **Sign Secret** → 一会儿填到 `FEISHU_WEBHOOK_SECRET`。
4. 复制 **Webhook 地址**（形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxx`）。

### 配置 & 验收

```bash
# 二选一：环境变量，或写进 acp-proxy/.env（本地，不进 git）
export DINGTALK_WEBHOOK_URL='https://oapi.dingtalk.com/robot/send?access_token=你的token'
# 若机器人开了加签：
export DINGTALK_WEBHOOK_SECRET='SEC你的加签密钥'

# 验收钩子：给一个 webhook 地址即能收到 SoulMate 真消息
cd openmate/acp-proxy

# ① 秒回自检（推一条指定消息）
python scripts/send_domestic_push.py --webhook "$DINGTALK_WEBHOOK_URL" \
    --message 'SoulMate 网关通道已通'

# ② 真消息验收（跑一轮真实 SoulMate 回合，把 agent 真回复推进群）
python scripts/send_domestic_push.py --webhook "$DINGTALK_WEBHOOK_URL" \
    --turn '用一句话介绍你自己'
```

飞书同理（`FEISHU_WEBHOOK_URL` / `FEISHU_WEBHOOK_SECRET`）；通道按 webhook 域名自动识别，
也可 `--channel feishu` 显式指定。成功会打印 `✓ [dingtalk] 已推送（echo 口径）`，群里即刻可见。

> 加签/签名密钥只在机器人开了安全设置时才需要；不开就别填，推送体与 echo 口径逐字节一致。

---

## Level 2 · 自建应用全双工（在钉钉/飞书里跟 SoulMate 对话）

全双工 = 有人在钉钉/飞书 @ SoulMate 或单聊发消息 → SoulMate 处理 → 回消息。需注册**自建应用**。

### 钉钉：企业内部应用

1. 打开 [钉钉开放平台](https://open-dev.dingtalk.com/) → 登录 → **应用开发** → **企业内部应用** → **创建应用**。
2. 填应用名/描述/图标 → 创建。记下 **AppKey（Client ID）** 和 **AppSecret（Client Secret）**。
   - → 环境变量 `DINGTALK_APP_KEY` / `DINGTALK_APP_SECRET`。
3. 左侧 **添加应用能力** → **机器人** → 开启机器人能力（填机器人名字/头像）。
4. 左侧 **事件订阅**：
   - 订阅方式选 **Stream 模式**（推荐，无需公网回调）或 **HTTP 模式**（需填公网回调地址）；
   - 订阅事件加 **机器人接收消息**（`BotMessageReceived` / 会话消息）。
5. 左侧 **开发配置 → 权限管理**：申请并开通——
   - **机器人发消息单聊**、**机器人发消息群聊**（`Robot:send` 相关）；
   - **根据手机号/用户 ID 查询用户**（可选，用于定位对端）。
6. **发布** → 企业管理员审核通过后生效。记下要对话的用户 **userId**（或群 **chatId**）。

### 飞书：企业自建应用

1. 打开 [飞书开放平台](https://open.feishu.cn/) → 登录 → **创建企业自建应用**。
2. 填应用名/描述/图标 → 创建。记下 **App ID** 和 **App Secret**。
   - → 环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`。
3. 左侧 **添加应用能力** → **机器人** → 开启（填机器人名字）。
4. 左侧 **事件与回调**：
   - **订阅方式**：选 **将事件发送至开发者服务器**（填**公网回调地址**）或 **使用长连接接收**（无需公网）；
   - 记下 **Encrypt Key** 和 **Verification Token**（回调加密/校验用，本地配置）；
   - 订阅事件加 **接收消息 `im.message.receive_v1`**。
5. 左侧 **权限管理**：申请并开通——
   - **获取与发送单聊、群组消息**（`im:message`）、**以应用的身份发消息**（`im:message:send_as_bot`）；
   - **获取用户基本信息**（可选）。
6. **发布** → 管理员审核通过后生效。记下要对话的用户 **open_id**（或群 **chat_id**）。

### 配置 & 验收

```bash
# 钉钉（或飞书 FEISHU_APP_ID / FEISHU_APP_SECRET），写本地 .env / 环境变量
export DINGTALK_APP_KEY='你的AppKey'
export DINGTALK_APP_SECRET='你的AppSecret'

cd openmate/acp-proxy

# ① 取令牌 + 发一条测试消息（真 API）
python scripts/test_full_duplex.py --channel dingtalk --peer <userId> --text 'SoulMate 全双工自检'

# ② 只验收发回路（不发真消息，无需对端）
python scripts/test_full_duplex.py --channel dingtalk --dry-run
```

脚本会先真调开放平台取访问令牌（`tenant_access_token` / `accessToken`），再发消息。
缺凭据时如实报「待用户提供凭据」并退出（不假装通过）。

**收消息侧**（别人发消息给 SoulMate → 处理 → 回）需要**公网事件回调地址**或 **Stream/长连接**，
在开放平台配好回调（钉钉 Stream 模式 / 飞书长连接最省事，无需公网）。回调接收器把解出的入站
消息喂给 `DomesticBridge`（`agent/domestic_bridge.py`）→ 跑 SoulMate 回合 → 平台 API 回消息。
回调地址/Encrypt Key 到位后接入该接收器即可，收发回路已在 `test_full_duplex.py --dry-run` 跑通。

---

## 配置项参考（全部本地，**不进 git**）

| 环境变量 | 级别 | 说明 |
|---------|------|------|
| `DINGTALK_WEBHOOK_URL` | L1 | 钉钉群机器人 webhook 地址 |
| `FEISHU_WEBHOOK_URL` | L1 | 飞书群机器人 webhook 地址 |
| `DINGTALK_WEBHOOK_SECRET` | L1 | 钉钉加签密钥（机器人开了加签才需） |
| `FEISHU_WEBHOOK_SECRET` | L1 | 飞书签名校验密钥（开了签名校验才需） |
| `DINGTALK_APP_KEY` / `DINGTALK_APP_SECRET` | L2 | 钉钉自建应用 AppKey / AppSecret |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | L2 | 飞书自建应用 App ID / App Secret |
| `SOULMATE_DOMESTIC_DB_PATH` | 运行 | 去重/会话映射库路径（默认 `~/.hermes/soulmate/memory/domestic_bridge.db`） |
| `SOULMATE_DOMESTIC_POLL_S` | 运行 | 入站轮询间隔秒（默认 5） |
| `SOULMATE_DOMESTIC_MAX_BLOCK` | 运行 | 回复单块最大字符（默认 2000） |

模板见 `acp-proxy/.env.example`（占位符，无真实凭据）。

## 安全纪律

- **凭据不进 git**：app secret / webhook 加签密钥 / Encrypt Key 一律本地环境变量或本地 `.env`。
- 不擅自改动/关闭现有登录鉴权；本接入层只做消息收发通道。
- 回调接收需校验签名（钉钉加签 / 飞书 Verification Token + AES 解密），见
  `agent/domestic_bridge.py::dingtalk_verify_sign / feishu_decrypt_event`。

## 代码位置

- 通道桥 + webhook 快路 + echo 口径：`acp-proxy/agent/domestic_bridge.py`
- 统一消息路由接线（通道注册/入站/出站）：`acp-proxy/agent/message_router.py`
- 验收钩子（webhook 快路）：`acp-proxy/scripts/send_domestic_push.py`
- 全双工验收（Level 2）：`acp-proxy/scripts/test_full_duplex.py`
- 测试：`acp-proxy/tests/test_domestic_bridge.py`
- echo 口径真源（格式标准）：`opensoul/src/echo/dispatcher.py`
