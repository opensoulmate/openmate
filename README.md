# OpenMate

**Your Knowledge Companion.**

OpenMate is an open-source AI companion platform that helps knowledge workers organize, connect, and retrieve their knowledge. It features a Web app, a Tauri-based desktop client, and a Chrome browser extension for collecting content on the go.

## Features

| Platform | Description |
|----------|-------------|
| **Web App** | Full-featured Next.js web application with AI chat, knowledge base, knowledge graph, and unified search |
| **Desktop App** | Tauri v2 native desktop client (macOS / Windows / Linux) with system tray and local file access |
| **Browser Extension** | Chrome MV3 extension for one-click web clipping, selection capture, and side panel browsing |

### Core Modules

- **AI Chat** -- Context-aware conversations powered by your personal knowledge base
- **Knowledge Base** -- Organize, tag, and star documents with semantic search
- **Knowledge Graph** -- Visualize connections between concepts, entities, and documents
- **Skills Marketplace** -- Install and manage plugin-style skills to extend AI capabilities
- **MCP Integration** -- Connect to MCP (Model Context Protocol) servers for tool-augmented AI
- **Unified Search** -- Cross-module search across all your knowledge
- **Agent Management** -- Monitor and configure backend agent nodes
- **Workflow Builder** -- Design multi-step AI workflows

## Quick Start

```bash
# Install dependencies
npm install

# Copy environment variables
cp .env.local.example .env.local

# Start development server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

### Desktop App (Tauri)

```bash
# Requires Rust toolchain (https://rustup.rs)
npm run tauri:dev
```

### Browser Extension

```bash
cd browser-extension
npm install
# Load the unpacked extension from chrome://extensions
```

## Tech Stack

| Layer | Technology |
|-------|------------|
| Framework | Next.js 15 (App Router) |
| UI | shadcn/ui + Tailwind CSS v4 |
| State | Zustand |
| Icons | Lucide React |
| Markdown | react-markdown |
| Desktop | Tauri v2 |
| Language | TypeScript 5 |

## Project Structure

```
src/
├── app/
│   ├── (app)/              # Authenticated app shell
│   │   ├── layout.tsx      # Sidebar + topbar layout
│   │   ├── chat/           # AI conversation
│   │   ├── knowledge/      # Document management
│   │   ├── graph/          # Knowledge graph visualization
│   │   ├── search/         # Unified search
│   │   ├── skills/         # Skill marketplace
│   │   ├── mcp/            # MCP server integration
│   │   ├── agents/         # Agent node management
│   │   ├── workflow/       # Workflow builder
│   │   └── settings/       # User preferences
│   ├── layout.tsx          # Root layout
│   ├── page.tsx            # Landing / dashboard
│   └── globals.css         # Theme & global styles
├── components/
│   ├── app-shell.tsx       # Sidebar navigation
│   ├── topbar.tsx          # Top navigation bar
│   └── ui/                 # Reusable UI primitives
├── stores/
│   └── app-store.ts        # Zustand global state
└── lib/
    ├── api.ts              # API client (proxied to Soul backend)
    └── utils.ts            # Utility functions
src-tauri/                  # Tauri desktop app (Rust)
browser-extension/          # Chrome MV3 extension
```

## Environment Variables

Copy `.env.local.example` to `.env.local` and fill in your values:

| Variable | Description | Default |
|----------|-------------|---------|
| `NEXT_PUBLIC_API_URL` | Soul backend server URL | `http://localhost:8000` |
| `NEXT_PUBLIC_WS_URL` | WebSocket server URL | `ws://localhost:8000/ws` |

## API Proxy

In development, the Next.js server proxies `/api/soul/*` requests to the Soul backend. Configure the target URL via the `SOUL_API_URL` environment variable in `next.config.ts`.

## 菜单回归防线（改菜单必跑）

菜单连环事故（520d2bca 重排丢菜单 → 2ea547ef 恢复）之后立的防线：**谁碰菜单，比对不过就不许上**。

```bash
npm run verify:menu                     # 一键回归比对（绿=0 / 红=1）
bash scripts/menu-regression.sh         # 等价入口
bash scripts/menu-regression.sh --update-baseline   # 显式重建基线（改菜单并人工确认后）
```

- 脚本自包含：真浏览器登录（e2e 测试账号）→ 抓顶栏/底栏/左栏**渲染出的**全部菜单项 → 与基线 `docs/reports/menu-baseline/menu-baseline.json` 逐项 diff。
- 失败判据：**缺失 / 降级（标签变化、层级迁移）/ 多余 / 顺序漂移 / 位置偏移 / 数量级差异** → 非零退出，打印逐项差异。
- 基线 = 恢复后的正确态：顶栏 9 直挂（指标/通知/仪表盘/下载/进化/插件/系统/⌘K/设置）+ 底栏直挂全量（含监控、器官为单一 L1 入口、插件一级位）+ 左栏 25 器官 + 11 工具 + 插件分节，另附 menu-audit 55 条基线逐条落位核对（覆盖清单，一条不少）。
- 结构性截图基线：`docs/reports/menu-baseline/shots/`（顶/底/左栏展开态 + 用户弹出菜单 + 移动态），比对元素位置/数量级（容差内不报），**非像素级容噪**。
- **改菜单必跑**：改 `top-bar.tsx` / `bottom-nav.tsx` / `organ-nav-sidebar.tsx` / `menu-domains.ts` / 插件菜单契约后，`npm run verify:menu` 不绿不许合入；确认菜单有意变更时用 `--update-baseline` 显式重建基线并复核 diff。
- 已知边界：3 个 adminOnly 入口（Soma管理/系统管理/权限管理）仅 admin 角色渲染，e2e 账号为 user 角色会跳过核对（脚本会注明）；需全量核对时用 admin 账号跑一次（`MENU_AUDIT_USER` / `MENU_AUDIT_PASSWORD`）。

## License

MIT
