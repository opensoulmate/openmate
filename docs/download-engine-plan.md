# OpenMate 下载中心·双目标架构（商业级，对标迅雷）

目标：下载功能达商业状态，可替代迅雷。双目标：本机（装在哪下到哪）/后端（服务器落盘）。支持种子搜索。

## 架构

```
前端下载页（控制面，Next.js）
  └─ DownloadEngine 接口（add/pause/resume/remove/list/events/limits/metadata）
       ├─ LocalEngineAdapter  → Electron preload 桥（端点 port/token）→ 直连 Python 引擎 REST（127.0.0.1:port）
       └─ RemoteEngineAdapter → REST/WS → 后端引擎服务（opensoul 门面 /api/download/*）
任务模型：origin: 'local' | 'remote'，列表双源汇总，行内徽标区分
```

## 选型

- **引擎：纯 Python 自研**（2026-11-16 用户三改定稿：禁 aria2（停维护）禁 Rust（用户铁律 Python 够用、在意内存））：
  - HTTP(S)：asyncio+httpx 分段引擎（Range 多线段、断点续传、限速、Cookie/UA、文件名解析）
  - BT/磁力：自研协议核心（bencode/.torrent 解析、ut_metadata 元数据交换 BEP9/10、HTTP/UDP tracker BEP3/15、rarest-first+endgame 分片、SHA1 校验、做种）；公共 tracker 列表注入；**DHT 二期**（接口预留）
  - FTP 暂缓；thunder://：保留现有快雷链解析→真实 URL→HTTP 引擎
  - 桌面端=**Electron**（`desktop/`：main.js+electron-builder，1.3.0 已发版；根目录 src-tauri/ 为遗留勿用）：Electron main spawn **PyInstaller 冻结的 Python 引擎单文件**，renderer 经 preload IPC；默认目录 `app.getPath('downloads')`（Win/mac/Linux 自动正确，禁手拼 ~/）
  - 后端=同一 Python 引擎（优先同进程挂 OpenSoul FastAPI，否则子进程+REST）——引擎一套、部署两处
- **种子搜索**：可插拔源适配层（"种子搜索协议"：`search(query, category?, limit?)` 统一形状，内置合法公共源：Internet Archive、Academic Torrents；用户自定义 RSS/JSON 源增删）——**单一真源放服务端（2026-11-16 用户钉死"搜索能力主要在服务端实现"：前端只调 REST 不得自建适配器，自定义源存服务端）**，两个面孔：①前端搜索面板调 REST ②SoulMate 调 MCP 工具。搜索结果一键入任务（选本机/后端）。
- **SoulMate 触发面（2026-11-16 用户补充规格）**：自然语言→工具链。MCP 工具组：`torrent_search`（查询→结果列表：标题/大小/做种数/源）、`download_add`（url|magnet|torrent，target=local|remote）、`download_task_status/list`（进度回报）。对话流：「最新有什么好的电影帮我找一下」→搜索→呈现候选（有歧义问一句哪部，默认取最合适）→加任务→汇报。工具注册进 SoulMate（acp-proxy/agent/soulmate_agent），调用后端 /search 与 /download API。
- **网盘资源支持（2026-11-16 用户补充规格）**：①搜索：新增「网盘搜索」源适配器（复用 PluggableSearchSource，结果形状扩 share_url/extract_code；网盘搜索索引为用户自配源，百度无官方搜索 API）②下载：pan_share 任务类型（分享链接+提取码+用户自有百度 Cookie 配置→解析直链→HTTP 引擎；错误细分：链接失效/提取码错/需登录/限速）③SoulMate 触发「找百度网盘资源」。**边界如实**：不破解会员限速/不打码/不绕风控——第三方下载百度限速（非会员 ~100KB/s）是其付费服务墙，如实呈现。
- **默认目录**：本机=Electron `app.getPath('downloads')`（Win/mac/Linux 自动正确，禁手拼 ~/）；后端=配置项（默认 ~/downloads）；移动端=只走后端模式。

## 分期

- **P0（本次）**：引擎层双适配 + aria2 sidecar/后端服务 + 任务 CRUD/暂停继续/进度速度/断点续传 + 新建任务（URL/磁力/.torrent/多行批量）目标选择 + 下载页双源 UI + 种子搜索面板 v1 + 设置（目录/并发/限速）。
- **P1**：BT 元数据预取+文件勾选、任务详情（分片/节点/Tracker）、做种管理、cURL 导入、剪贴板监听、通知。
- **P2**：Tracker 管理与健康、限速计划、仪表板卡片、浏览器扩展对接、插件系统。

## 验收（P0）

1. HTTP 大文件：断点续传（暂停→重启应用→继续→字节连续）。
2. 磁力链：添加→下载成功，速度/进度真实。
3. .torrent：拖入→文件清单→选择性下载。
4. 双目标：同一界面分别落本机/后端各一任务，文件位置正确。
5. Win/mac 目录：本机模式默认目录正确解析（含 Windows）。
6. 种子搜索：搜到结果→一键添加任务。

## 铁律

**全部 Python 实现，禁引入其他语言**（2026-11-16 用户两连令）：引擎/协议栈/后端/工具 handler 全 Python；前端 TS（Next.js 既有栈）与 Electron 壳 JS 仅既有粘合，下载逻辑不得写进 JS；禁 Rust/Go/C。
增量改码（禁全量重写）；每步测试通过再下一步；git 显式路径提交；UI 遵守选中项无下划线/圆角高亮铁律。
