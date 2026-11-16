# OpenMate 本机下载引擎（openmate-engine）

纯 Python 自研下载引擎（无 Rust / 无 aria2 依赖）：
**HTTP(S) 分段下载**（asyncio + httpx）+ **自研 BT 内核**（bencode / .torrent / 磁力 +
ut_metadata 元数据交换（BEP9/10）/ HTTP·UDP tracker（BEP3/15）/ rarest-first + endgame
分片调度 / SHA1 逐片校验 / 做种）+ **token 鉴权 REST**（127.0.0.1）+ **JSON 任务持久化**。

同一套代码两种部署：Electron 桌面壳 spawn 为子进程（PyInstaller 单文件），或后端以服务方式运行。

## 运行

```bash
cd ~/openmate/core
python -m download_engine --data-dir /path/to/state --dir /path/to/downloads
```

启动后 stdout 打一行 JSON（供 Electron main 捕获握手）：

```json
{"event":"listening","port":38123,"token":"…","pid":1234,"dataDir":"…","origin":"local","version":"0.1.0"}
```

CLI 参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--data-dir` | `./openmate-engine-data` | 任务/会话持久化目录 |
| `--dir` | （无） | 默认下载目录。**引擎内不硬编码 ~/Downloads**：Electron 传 `app.getPath('downloads')`，后端传配置目录 |
| `--host` | `127.0.0.1` | 监听地址（仅本机） |
| `--port` | `0` | 监听端口，0=随机 |
| `--token` | 环境变量 `OPENMATE_ENGINE_TOKEN` 或随机生成 | REST token |
| `--concurrency` | `5` | HTTP 分段并发 |
| `--speed-limit` | `0` | 全局限速 bytes/s，0=不限 |
| `--origin` | `local` | 任务 origin 值（后端部署可传 `remote`） |

**PyInstaller 冻结单文件**（Electron spawn 用）：

```bash
pyinstaller -F -n openmate-engine --paths core core/download_engine/__main__.py
# 产物 dist/openmate-engine；Electron main child_process.spawn 后读第一行 JSON 取 port/token
```

## 目录解析（调用方参数化）

`显式 dir（每次请求） > 配置项 dir（PUT /api/config） > 启动参数 --dir > 报错`。
没有任何来源时任务报错「未指定下载目录」——引擎绝不自造默认目录。

## REST API（全部 JSON；除 /health 外需 `Authorization: Bearer *** 或 `X-Auth-Token`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 存活探测（免 token） |
| GET | `/api/tasks` | 任务列表 |
| POST | `/api/tasks` | 新建 `{url, dir?, filename?, options?}`；url 支持 http(s)/magnet/.torrent 路径/thunder·flashget·qqdl 快雷链 |
| POST | `/api/tasks/{id}/pause` | 暂停 |
| POST | `/api/tasks/{id}/resume` | 继续（error 任务=重试） |
| POST | `/api/tasks/{id}/remove` | 移除 `{remove_files?}`（true 连文件一起删） |
| GET | `/api/events?after=N` | 轮询事件 `{events, cursor}`（added/progress/status/error/removed） |
| GET | `/api/config` | 当前配置 |
| PUT | `/api/config` | `{dir?, concurrency?, speedLimit?}`（dir 空串=重置） |
| POST | `/api/metadata` | `{source}` 磁力/torrent → 文件清单 `{files:[{path,length}]}`（磁力经 ut_metadata 拉取） |
| POST | `/api/shutdown` | 优雅停机（保存会话后退出） |

`options`：`{rename, select_files("1-3,5"), user_agent, referer, cookie, headers}`。

### 任务结构（DownloadTask 契约）

```json
{
  "id": "http-1700000001", "url": "…", "filename": "a.zip", "dest": "/dl/a.zip",
  "totalBytes": 1048576, "downloadedBytes": 409600, "speed": 262144, "eta": 3,
  "progress": 39.06, "status": "downloading", "type": "http",
  "addedAt": 1700000000.0, "origin": "local", "remove_files": false, "error": null
}
```

`status`: `pending | downloading | paused | done | error`（内部另有终态 `removed`）；
`type`: `http | bt | video | thunder`（video 走后端/插件，本机引擎拒绝）。

## 持久化与断点续传

- `engine-state.json`：任务 + HTTP 分段进度（每段 done 字节）+ BT 位图/元数据（原子写）。
- HTTP：重启恢复分段进度续传（带 `If-Range`/ETag 校验；服务器忽略 Range 时自动退回单流）。
- BT：重启恢复位图继续下载；元数据（info dict 原始字节）本地留存，重启无需重新拉取。

## BT 内核说明

- 磁力：公共 tracker 列表注入（`trackers.PUBLIC_TRACKERS`）+ 种子自带 announce → ut_metadata 拉元数据
  （sha1 == infohash 校验）→ 分片下载（rarest-first，尾部 endgame 重复借还 + cancel）→ 逐片 SHA1 校验落盘 → 做种。
- **DHT 二期**：`dht.py` 预留 `DhtClient.get_peers(info_hash)` 接口位，当前磁力 peer 全部来自 tracker。

## 测试

```bash
cd ~/openmate
python -m pytest core/download_engine/tests/ -q
```

覆盖：bencode、分段规划/断点续传、BT 分片调度（rarest-first+endgame）、状态机、
tracker announce（HTTP/UDP 假服务端）、peer wire（假做种端真实 TCP 下载回传）、
ut_metadata 元数据交换、REST API 全路由、任务持久化恢复。
