# 语音输入的浏览器 HTTPS 入口（局域网访问麦克风方案）

## 背景

浏览器只在**安全上下文**（`https://` 或 `localhost`）开放 `getUserMedia`（麦克风）。
经 `http://100.76.2.155:3000` 这类 **HTTP + 非本机地址** 访问时 `navigator.mediaDevices`
不存在，语音输入会提示"麦克风不可用"——这是浏览器安全策略，不是 bug。

两条真实访问路径的落地：

| 访问路径 | 方案 | 状态 |
|---|---|---|
| Electron 桌面 | **localhost 回源**：`desktop/config.json` 的 `url` 为 `http://localhost:3000`（localhost=安全上下文），`main.js` 显式放行 media/麦克风权限 | 已落地（桌面版目录 `config.url` 即回源地址） |
| 浏览器局域网 | **HTTPS 入口**（本目录 `proxy.js` 反代）或 NetBird 组网走 HTTPS | 已落地（见下） |

> 曾评估 `app://` 自定义协议方案：页面源虽是安全上下文，但对远程 `http://IP:8090`
> 后端的 fetch/WS 会命中混合内容拦截，需再改造前端所有绝对 URL，收益不抵成本。

## 方案一（推荐）：本机 HTTPS 反向代理（scripts/https-entry/proxy.js）

单端口单源，按路径分发到各服务，页面即处于安全上下文：

```
node scripts/https-entry/proxy.js        # 默认 :3443，PORT=8443 可换
```

路由表：

| 路径 | 上游 | 说明 |
|---|---|---|
| `/ws/acp` | 127.0.0.1:8092 | ACP WebSocket（含 upgrade 透传） |
| `/api/smart-prompt/`、`/api/file`、`/api/agent/`、`/api/execute`、`/api/docs/`、`/api/dev-specs/`、`/api/gene-loop`、`/api/trace`、`/api/ai-edit` | 127.0.0.1:3000 | Next.js route handlers |
| 其余 `/api/*` | 127.0.0.1:8090 | OpenSoul REST |
| 其它 | 127.0.0.1:3000 | Next.js UI/静态资源 |

客户端配置（一次性，在浏览器控制台执行或改登录逻辑）：

```js
localStorage.setItem('openmate-api-url', 'https://<主机>:3443');
```

HTTPS 页面下 ACP WebSocket 自动回源同域 `/ws/acp`（`chat-client.tsx` 的
`getAcpProxyUrl` 在 `https:` 协议下取 `window.location.host`），无需另配；
也可用 `localStorage.openmate-acp-url` 显式覆盖。

证书：默认读 `certs/cert.pem` + `certs/key.pem`（自签）。首次访问浏览器会警告，
点"高级→继续访问"信任一次即可；要消除警告用 mkcert/企业 CA 签真证书替换这两个文件。

```bash
# 系统服务化（可选）：systemd --user
# ExecStart=/usr/bin/node /home/climbing/openmate/scripts/https-entry/proxy.js
```

**已知边界（如实）**：终端面板/浏览器面板等页面内直连 `ws://IP:8092` 的 WebSocket
（`terminal-panel`、`browser-task-runner`、`global-websocket`）目前仍按旧地址连接，
HTTPS 入口下这些功能暂不受影响的只有走 `/ws/acp` 的聊天主链路；其余按需把直连
地址改为同域回源即可复用本代理。

## 方案二：NetBird 组网（免自签证书的替代）

NetBird 节点间走 WireGuard + HTTPS 管理面，给每台机器分配 `100.x.x.x` 虚拟 IP。
配合上面的反代（或 Caddy 自动证书）后用 `https://` 访问即可；只是把"网络可达"
交给 NetBird，"安全上下文"仍由 HTTPS 提供。

## 方案三：nginx 反代（等价配置示例）

```nginx
server {
  listen 3443 ssl;
  server_name _;
  ssl_certificate     /home/climbing/openmate/certs/cert.pem;
  ssl_certificate_key /home/climbing/openmate/certs/key.pem;

  location /ws/acp {           # ACP WebSocket
    proxy_pass http://127.0.0.1:8092;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
  }
  location ~ ^/api/(smart-prompt|file|agent|execute|docs|dev-specs|gene-loop|trace|ai-edit)(/|$) {
    proxy_pass http://127.0.0.1:3000;
  }
  location /api/ { proxy_pass http://127.0.0.1:8090; }
  location /    { proxy_pass http://127.0.0.1:3000; }
}
```

## Electron 桌面的麦克风要点

1. **安全上下文**：窗口加载 `http://localhost:3000`（config.json `url`）。
   若配置成 `http://IP:3000` 远程加载，浏览器层直接没有麦克风接口——前端会给出
   具体原因和本页指引。
2. **权限放行**：`main.js` 在 `app.whenReady` 里 `session.defaultSession`
   显式放行 media/麦克风（`setPermissionRequestHandler` / `setPermissionCheckHandler`）。
3. 验证口径：Electron 内点右下角麦克风 → 录一段中文 → 中文转写进输入框 → Enter 发送。
