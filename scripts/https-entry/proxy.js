#!/usr/bin/env node
/**
 * OpenMate HTTPS 入口反向代理（浏览器局域网访问的麦克风/语音输入方案）。
 *
 * 背景：浏览器只在安全上下文（HTTPS 或 localhost）开放 getUserMedia——经
 * http://IP:3000 局域网访问时 navigator.mediaDevices 不存在，语音输入不可用。
 * 本代理在本机起一个 HTTPS 入口（自签证书），把同源流量按路径分发到各服务，
 * 页面即处于安全上下文，麦克风恢复可用。
 *
 * 路由表（单端口单源）：
 *   /ws/acp                    → 127.0.0.1:8092  （ACP WebSocket，含 upgrade）
 *   /api/smart-prompt/* 等 Next 路由 → 127.0.0.1:3000
 *   其余 /api/*                → 127.0.0.1:8090  （OpenSoul REST）
 *   其它                       → 127.0.0.1:3000  （Next.js UI/静态资源）
 *
 * 用法：
 *   node scripts/https-entry/proxy.js            # 默认监听 :3443
 *   PORT=8443 node scripts/https-entry/proxy.js
 * 证书：certs/cert.pem + certs/key.pem（自签；浏览器首次访问需信任一次）。
 * 客户端配置：localStorage.openmate-api-url = "https://<主机>:3443"
 * （HTTPS 页面下 ACP WS 自动回源同域 /ws/acp，无需另配）。
 */
'use strict';

const https = require('https');
const http = require('http');
const net = require('net');
const fs = require('fs');
const path = require('path');

const PORT = Number(process.env.PORT || 3443);
const UI = { host: '127.0.0.1', port: Number(process.env.UI_PORT || 3000) };       // Next.js
const API = { host: '127.0.0.1', port: Number(process.env.API_PORT || 8090) };      // OpenSoul
const ACP = { host: '127.0.0.1', port: Number(process.env.ACP_PORT || 8092) };      // ACP Proxy (WS)
const CERT_DIR = process.env.CERT_DIR || path.join(__dirname, '..', '..', 'certs');

// 透传到 Next.js 的 /api 前缀（route.ts 端点）；其余 /api/* 归 OpenSoul
const NEXT_API_PREFIXES = [
  '/api/smart-prompt', '/api/file', '/api/agent', '/api/execute', '/api/docs',
  '/api/dev-specs', '/api/gene-loop', '/api/trace', '/api/ai-edit',
];

function routeTarget(pathname) {
  if (pathname === '/ws/acp' || pathname.startsWith('/ws/acp?')) return ACP;
  if (pathname === '/ws/acp/') return ACP;
  if (pathname.startsWith('/api/')) {
    return NEXT_API_PREFIXES.some((p) => pathname === p || pathname.startsWith(p + '/')) ? UI : API;
  }
  return UI;
}

const stripHeaders = (headers) => {
  const out = { ...headers };
  delete out.host;
  delete out['accept-encoding']; // 不做压缩透传，避免正文编码错位
  return out;
};

function proxyRequest(req, res) {
  const target = routeTarget(req.url.split('?')[0]);
  const upstream = http.request(
    { host: target.host, port: target.port, path: req.url, method: req.method, headers: stripHeaders(req.headers) },
    (up) => {
      res.writeHead(up.statusCode, up.headers);
      up.pipe(res);
    },
  );
  upstream.on('error', (e) => {
    res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end(`上游服务不可达（${target.host}:${target.port}）：${e.message}`);
  });
  req.pipe(upstream);
}

// WebSocket 升级：原样建 TCP 后双向透传字节（/ws/acp → ACP 8092）
function proxyUpgrade(req, socket, head) {
  const target = routeTarget(req.url.split('?')[0]);
  const up = net.connect(target.port, target.host, () => {
    const lines = [`${req.method} ${req.url} HTTP/1.1`];
    for (let i = 0; i < req.rawHeaders.length; i += 2) {
      const k = req.rawHeaders[i];
      if (k.toLowerCase() === 'host') continue;
      lines.push(`${k}: ${req.rawHeaders[i + 1]}`);
    }
    lines.push(`Host: ${target.host}:${target.port}`, '', '');
    up.write(lines.join('\r\n'));
    if (head && head.length) up.write(head);
    socket.pipe(up);
    up.pipe(socket);
  });
  const fail = () => socket.destroy();
  up.on('error', fail);
  socket.on('error', fail);
}

const server = https.createServer(
  {
    key: fs.readFileSync(path.join(CERT_DIR, 'key.pem')),
    cert: fs.readFileSync(path.join(CERT_DIR, 'cert.pem')),
  },
  proxyRequest,
);
server.on('upgrade', proxyUpgrade);
server.listen(PORT, () => {
  console.log(`[https-entry] https://<本机IP>:${PORT} → UI ${UI.port} / API ${API.port} / ACP-WS ${ACP.port}（证书：${CERT_DIR}）`);
});
