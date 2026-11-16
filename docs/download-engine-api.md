# OpenMate 下载引擎 · 接口契约（facade ↔ 引擎内核）

> 状态：v1（供引擎组对齐）。本文件定义 **后端门面（facade，在 opensoul）** 与 **引擎内核
> （engine kernel，引擎组产出的 Rust 引擎：librqbit BT/磁力 + 自研 HTTP 分段）** 之间的
> REST/WS 契约，以及门面暴露给前端的 DownloadTask 形状。
>
> 分工边界（防撞车）：
> - **引擎组**：协议内核 —— HTTP(S) 分段（asyncio + httpx + Range + 断点续传 + 限速）、
>   BT/磁力（自研 BT 核心）、任务状态机、元数据预取。产出统一引擎（纯 Python）放
>   `~/openmate/core/download_engine/`，实现本契约的 REST/WS。**引擎定稿=纯 Python 自研，
>   禁 Rust/aria2；curl 链仅留作最后降级。**
> - **后端门面（本仓）**：① 引擎进程管理（spawn/健康检查/重启/优雅退出）② REST/WS API 层
>   （字段兼容前端 DownloadTask + `origin:'remote'` + `remove_files`）③ 后端任务表/持久化 +
>   目录配置（默认 `~/downloads`，可配）④ 与 opensoul 现有进程体系集成（lifespan 起停）。
>   门面**不实现协议内核**，只做进程管理 + 转发 + 任务登记表 + 形状对齐。
>
> 桌面壳已确认为 **Electron（desktop/）**，非 Tauri；本契约不含桌面端假设，引擎只跑服务端。
>
> 当前实现：内核尚未就绪，用 **mock 引擎**（`engine/mock_engine.py`，同一 REST 形状、真做
> HTTP 下载与断点续传）跑通门面与验证。引擎组交付 `core/download_engine/`（纯 Python）后：
> opensoul 侧**优先同进程路由直接挂载**（Python FastAPI 可 import，进程管理能省则省），
> 否则以子进程 spawn（`DOWNLOAD_ENGINE_CMD` 指定），契约不变。桌面本机模式=Electron spawn
> PyInstaller 冻结的引擎单文件（另一路，不由本门面负责）。

---

## 1. 进程管理契约（门面 → 引擎二进制）

门面以子进程方式启动引擎（跟随 opensoul 现有 spawn 约定）：

```
<engine-bin> --port <P> --secret <S> --dir <D> --state <F>
```

| 参数 | 含义 |
|------|------|
| `--port` | 引擎 REST 监听端口（门面选空闲端口）；仅绑 `127.0.0.1` |
| `--secret` | 门面生成的随机令牌；所有请求经 `X-Engine-Secret: <S>` 鉴权 |
| `--dir` | 默认下载目录（门面传入，缺省解析后的 `~/downloads`） |
| `--state` | 引擎自状态文件路径（引擎内核的续传/状态持久化，引擎组自理） |

生命周期：
- **spawn**：门面 `start()` 拉起进程，轮询 `GET /health` 直到就绪（超时 8s 记失败）。
- **健康检查**：门面 `is_running()` = 子进程存活 **且** `GET /health` 通；纯探测**不触发 spawn**。
- **重启**：健康检查失败（进程退出/RPC 不通）时门面重新 spawn；连续失败降级到 HTTP fallback。
- **优雅退出**：门面 `stop()` 先 `POST /shutdown`（引擎落盘自状态），再 SIGTERM，超时 SIGKILL。

---

## 2. 引擎内核 REST 契约（门面消费，引擎组实现）

Base：`http://127.0.0.1:<port>`，鉴权头 `X-Engine-Secret: <S>`。所有响应 `application/json`。

| 方法 | 路径 | 说明 | 返回 |
|------|------|------|------|
| GET | `/health` | 存活探针 | `{"status":"ok","engine":"<name>","version":"<v>"}` |
| GET | `/status` | 引擎概况 | `{"engine","version","download_dir","active":N,"waiting":N}` |
| GET | `/tasks` | 全部任务 | `{"tasks":[Task...]}` |
| POST | `/tasks` | 新建下载 | `Task`（201）。body `{"url","dest"?,"filename"?}` |
| GET | `/tasks/{id}` | 单任务 | `Task` |
| GET | `/tasks/{id}/metadata` | 元数据（BT 文件清单/分片/Tracker 等） | `{"task":Task,"metadata":{...}}` |
| POST | `/tasks/{id}/pause` | 暂停 | `Task` |
| POST | `/tasks/{id}/resume` | 继续 | `Task` |
| DELETE | `/tasks/{id}?remove_files=<bool>` | 移除；`remove_files=true` 连带删已下载文件 | `{"status":"removed","removed_file":bool}` |
| GET | `/config` | 引擎配置 | `Config` |
| POST | `/config` | 改配置 | `Config`。body `{"key","value"}` |
| POST | `/shutdown` | 优雅退出（落自状态） | `{"status":"ok"}` |

错误：非 2xx 时 `{"error":{"code":"<细分码>","message":"<人读>"}}`。细分码至少区分：
`bad_request` / `not_found` / `unsupported_scheme`（如 FTP 暂缓）/ `io_error` /
`network_error` / `range_unsupported` / `internal`。**错误信息必须细分**，不接受单一模糊串。

### WS（进度事件，可选但推荐）

`GET /ws`（升级 WebSocket）：引擎周期性（≥1s）或状态变更时推送 JSON 帧：

```json
{"type":"task","task":{ ...Task... }}
{"type":"removed","id":"<task_id>"}
```

门面侧同样提供 `WS /api/download/ws` 把任务快照推给前端（前端现存 2s 轮询不受影响）。

---

## 3. Task 对象（DownloadTask 形状，前后端一致）

引擎内核返回、门面透传/对齐、前端消费，均用此形状：

```json
{
  "id": "d3adb33f00000001",
  "url": "https://example.com/file.bin",
  "filename": "file.bin",
  "dest": "/home/u/downloads/file.bin",
  "totalBytes": 10485760,
  "downloadedBytes": 163840,
  "speed": 262144,
  "eta": 39,
  "progress": 1.6,
  "status": "downloading",
  "type": "http",
  "addedAt": 1794809399.33,
  "origin": "remote",
  "error": null
}
```

字段约定：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 稳定任务 id（门面/引擎一致，重启不变） |
| `url` | str | 原始 URL |
| `filename` | str | 落盘文件名 |
| `dest` | str | 绝对落盘路径 |
| `totalBytes` | int | 总字节（未知=0） |
| `downloadedBytes` | int | 已下载字节 |
| `speed` | int | 瞬时速度 B/s |
| `eta` | int | 剩余秒（未知=0） |
| `progress` | float | 0–100 |
| `status` | str | `pending`\|`downloading`\|`paused`\|`done`\|`error` |
| `type` | str | `http`\|`bt`\|`video`\|`thunder`（前端枚举，按 URL 判定） |
| `addedAt` | float | 入列 unix 时间 |
| `origin` | str | 固定 `"remote"`（服务端落盘这条路） |
| `error` | str\|null | 失败细分错误信息 |

门面对前端**额外**附 snake_case 别名（前端 `normalizeTask` 兼容，非契约必须但保留）：
`task_id`、`total_bytes`、`downloaded_bytes`、`progress_pct`。

---

## 4. Config 对象

```json
{
  "download_dir": "/home/u/downloads",
  "threads": 8,
  "speed_limit": 0,
  "proxy": null,
  "engine": "<name>",
  "engine_running": true
}
```

- `download_dir`：**门面可配，缺省 `~/downloads`，启动时建目录**（门面职责③；引擎只接收 `--dir`）。
- `threads`：并发/分段数（映射引擎 `max-concurrent`/`split`）。
- `speed_limit`：全局限速 B/s（0=不限）。
- `proxy`：可选代理。

---

## 5. 门面对前端的 REST/WS（既有 `/api/download/*` 形状兼容）

前端 `src/app/(app)/download/download-client.tsx` 现有调用，门面必须兼容：

- `GET  /api/download/tasks` → `{"tasks":[...], "plugin_tasks":[...], "remote_tasks":[...]}`（remote 即引擎任务，DownloadTask 形状 + `origin:'remote'`）
- `POST /api/download/download` `{url,resume}` → 引擎可用走引擎，否则降级；返回任务 ack
- `POST /api/download/tasks/{id}/pause|resume`、`DELETE /api/download/tasks/{id}?remove_files=`
- `GET/POST /api/download/config`（`download_dir` 缺省 `~/downloads`）
- `GET  /api/download/remote/*`：显式引擎面（add/list/metadata/config/status/save-session）
- `WS   /api/download/ws`：任务快照推送（新增，前端可选）

**降级纪律**：引擎不可用时降级到既有 HTTP fallback（native/httpx 多段），并把降级原因细分记进
任务/响应；**不得回退 aria2/curl-wget 为主引擎**，curl 链仅作最后降级。

---

## 6. 后端任务表 / 持久化（门面职责③）

- 门面持 `download_engine_tasks` SQLite 表（与 `download_tasks`/`download_plugin_tasks` 同约定：
  纯 sqlite3、TEXT 列 `str()`、任务体 `json.dumps` 落 `payload` 列）：`task_id PK, status, payload`。
- 语义=状态快照表（同 task_id upsert 覆盖=当前态）；`ARIA2_TASKS_DB`→`DOWNLOAD_ENGINE_TASKS_DB`
  环境变量隔离测试（缺省 `<repo>/data/download_engine_tasks.db`）。
- 重启恢复：门面首触懒回载表 → 与引擎 `/tasks` 对齐（引擎自理字节续传；门面保任务清单/身份稳定）。
- 目录配置持久化在 `download_engine_config.json`（`DOWNLOAD_ENGINE_CONFIG` 覆盖）。

---

## 7. 引擎组对齐清单（交付内核时须满足）

1. 实现 §2 全部 REST + 进程入参（§1，python 进程）；`/health` 可用性是门面判定 `is_running()` 的真源。
2. `Task` 形状与 §3 完全一致（字段名/类型/status 枚举/type 枚举）。
3. HTTP(S)：asyncio + httpx Range 多分段 + 断点续传（`downloadedBytes` 从盘上半成品续）、`speed_limit` 生效、半成品落盘（`.part`/控制文件）供续传。
4. BT/磁力：自研 BT 核心；`type":"bt"`；`/tasks/{id}/metadata` 返回文件清单（供选择性下载）。
5. `thunder://`：门面先解析为真实 URL 再入引擎（引擎可只认 http/https/magnet/.torrent）。
6. FTP：暂缓，遇 `ftp://` 返回 `unsupported_scheme`（细分错误）。
7. 错误码/信息细分（§2），`error` 字段人读可定位。
8. `remove_files=true` 删除已下载文件（含控制文件），`removed_file` 如实回报。
9. **禁 aria2/Rust**；curl 链仅留作最后降级（不作主引擎）。

---

## 8. 搜索面 `/search`（门面职责，非引擎内核）

搜索适配层只在后端写一份（`src/api/download_search.py`），前端不重复实现。统一形状：

```
search(query, category?, limit?) -> { "results": [{title,size,seeders,source,url|magnet}], "errors": [...], "total": N }
```

- 内置适配器：**Internet Archive**（advancedsearch JSON API）+ **Academic Torrents**（RSS browse）。
- 用户自定义源：**RSS / JSON**（配置增删），字段灵活映射到统一形状。
- 结果短 TTL 缓存（默认 120s），按 (source, query, category, limit) 键。
- 适配器失败→该源 0 行 + `errors[]` 记 `{source,error}`，**禁 fabricate**；网络隔离环境下内置源如实回空+错误。

门面 REST：
- `GET  /api/download/search?query=&category=&limit=` → 统一形状结果
- `GET  /api/download/search/sources` → 列源（内置+自定义）
- `POST /api/download/search/sources` `{name,kind:json|rss,url}` → 增自定义源
- `DELETE /api/download/search/sources/{name}` → 删自定义源

MCP 工具面（供 SoulMate 集成组注册）：`torrent_search` / `download_add(url|magnet|torrent,target=local|remote)` /
`download_task_status` / `download_task_list`，schema 见 **`docs/download-mcp-tools.json`**（字段兼容 DownloadTask +
`origin` + `remove_files`）。target=remote 走本后端引擎；target=local 走桌面本机引擎（Electron，另一路）。
