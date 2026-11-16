// OpenMate Desktop — Electron 薄壳（对标 Hermes Desktop 架构：壳+同一后端，同步架构自带）
const { app, BrowserWindow, Tray, Menu, shell, nativeImage, nativeTheme, ipcMain, dialog, webContents } = require('electron');
const path = require('path');

// ── 下载引擎子进程（Python 引擎薄壳集成）────────────────────────────
// 铁律：下载逻辑全部在 Python 引擎侧（python -m download_engine / PyInstaller 冻结单文件），
// 本文件只做三件事：spawn 引擎子进程、读首行 JSON 握手（port/token）、IPC 转发给 renderer。
// 禁在 JS 写任何下载逻辑（分段/续传/协议一律 Python）。
const { spawn } = require('child_process');
let engineProc = null;
let engineInfo = null; // {event, port, token, pid, dataDir, origin, version} —— 握手行原样
let engineInfoWaiters = []; // 等握手的 getInfo 调用方（页面加载可能快于 PyInstaller 解包+握手）

function resolveEngineCmd() {
  // 冻结单文件（PyInstaller）优先：env 指定 > 打包资源 > exe 旁 > 开发模式 core/dist
  const frozenCandidates = [
    process.env.OPENMATE_ENGINE_BIN || null,
    process.resourcesPath ? path.join(process.resourcesPath, 'download-engine') : null,
    path.join(path.dirname(process.execPath), 'download-engine'),
    path.join(__dirname, '..', 'core', 'dist', 'download-engine'),
  ].filter(Boolean);
  for (const bin of frozenCandidates) {
    try { if (fs.existsSync(bin)) return { cmd: bin, args: [], env: {} }; } catch (e) { /* 试下一个 */ }
  }
  // 开发模式回退：python -m download_engine（PYTHONPATH 指向 openmate/core）
  const python = process.env.OPENMATE_ENGINE_PYTHON || 'python3';
  return { cmd: python, args: ['-m', 'download_engine'], env: { PYTHONPATH: path.join(__dirname, '..', 'core') } };
}

function startDownloadEngine() {
  const { cmd, args, env } = resolveEngineCmd();
  const dataDir = path.join(app.getPath('userData'), 'download-engine');
  const fullArgs = [
    ...args,
    '--dir', app.getPath('downloads'), // 本机默认下载目录=系统下载目录（禁手拼 ~/）
    '--data-dir', dataDir,
    '--port', '0',
    '--origin', 'local',
  ];
  try {
    engineProc = spawn(cmd, fullArgs, { env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (e) {
    console.error('[download-engine] spawn failed:', e.message);
    return;
  }
  engineProc.stderr.on('data', (d) => console.error('[download-engine]', String(d).trim()));
  let buf = '';
  engineProc.stdout.on('data', (d) => {
    if (engineInfo) return; // 只吃握手行
    buf += String(d);
    const nl = buf.indexOf('\n');
    if (nl < 0) return;
    const line = buf.slice(0, nl).trim();
    try {
      engineInfo = JSON.parse(line); // {event:"listening", port, token, pid, ...}
      console.log('[download-engine] listening on 127.0.0.1:' + engineInfo.port);
    } catch (e) {
      console.error('[download-engine] bad handshake line:', line.slice(0, 200));
    }
    for (const r of engineInfoWaiters) r(engineInfo);
    engineInfoWaiters = [];
  });
  engineProc.on('exit', (code) => {
    console.error('[download-engine] exited with code', code);
    engineProc = null;
    engineInfo = null;
    for (const r of engineInfoWaiters) r(null);
    engineInfoWaiters = [];
  });
}

// renderer 取引擎端点（port/token）的唯一通道——转发握手结果，不含任何下载逻辑。
// 页面加载可能快于引擎解包+握手：未就绪时等到握手行（30s 超时如实 null）。
ipcMain.handle('download-engine-info', () => {
  if (engineInfo) return engineInfo;
  if (!engineProc) return null;
  return new Promise((resolve) => {
    engineInfoWaiters.push(resolve);
    setTimeout(() => resolve(null), 30 * 1000);
  });
});

// 地址解析：环境变量 > 同目录 config.json > 本机默认（Windows 免环境变量，改 config.json 即可）
const fs = require('fs');
function resolveUrl() {
  if (process.env.OPENMATE_URL) return process.env.OPENMATE_URL;
  // 打包后 __dirname 在安装包内部——必须优先读 exe 旁边的 config.json（用户可编辑的那个）
  const candidates = [
    path.join(path.dirname(process.execPath), 'config.json'), // 绿色包/安装版：exe 同目录
    path.join(__dirname, 'config.json'),                       // 开发模式
  ];
  for (const p of candidates) {
    try {
      const cfg = JSON.parse(fs.readFileSync(p, 'utf8'));
      if (cfg && cfg.url) return cfg.url;
    } catch (e) { /* 该位置没有或坏了=试下一个 */ }
  }
  return 'http://127.0.0.1:3000';
}
const OPENMATE_URL = resolveUrl();
// 地址回退链：config/env 指定 > 本机 :3000 > Netbird 托管（Windows 端跑本机无前端时自动落到托管版）
const URL_FALLBACKS = Array.from(new Set([
  OPENMATE_URL,
  'http://127.0.0.1:3000',
  'http://100.76.2.155:3000',
]));
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) app.quit();

let win = null;
let tray = null;
let splash = null;

// 加载动画进度驱动（打字机 splash.html 的 window.setSplash(pct, stage)）
function splashProgress(pct, stage) {
  if (!splash || splash.isDestroyed()) return;
  splash.webContents
    .executeJavaScript(`window.setSplash && window.setSplash(${pct}, ${stage})`)
    .catch(() => {});
}

function createSplash() {
  splash = new BrowserWindow({
    width: 560,
    height: 360,
    frame: false,
    resizable: false,
    backgroundColor: '#0b0f14',
    alwaysOnTop: true,
    skipTaskbar: true,
    icon: path.join(__dirname, 'icon.png'),
  });
  splash.loadFile(path.join(__dirname, 'splash.html'));
  createSplash.startedAt = Date.now();
  splash.webContents.once('did-finish-load', () => splashProgress(8, 0));
  splash.on('closed', () => { splash = null; });
}

function createWindow() {
  win = new BrowserWindow({
    width: 1360,
    height: 880,
    minWidth: 900,
    minHeight: 600,
    title: 'OpenMate',
    icon: path.join(__dirname, 'icon.png'),
    // 毛玻璃：Windows=Acrylic 系统材质、macOS=under-window vibrancy；玻璃从半透明的
    // 侧栏/标题栏底下透出来（CSS 端 .om-glass）。Linux 无系统材质，保持实底。
    backgroundColor: (process.platform === 'win32' || process.platform === 'darwin') ? '#00000000' : '#0b0f14',
    ...(process.platform === 'win32' ? { backgroundMaterial: 'acrylic' } : {}),
    ...(process.platform === 'darwin' ? { vibrancy: 'under-window', visualEffectState: 'active' } : {}),
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      webviewTag: true, // 内嵌真浏览器面板（<webview>）：人手动+AI直驱同一扇窗
      preload: path.join(__dirname, 'preload.js'),
    },
    show: false, // 就绪前由 splash 顶着
  });
  win.webContents.on('did-start-loading', () => splashProgress(38, 1));
  win.webContents.on('dom-ready', () => splashProgress(72, 2));
  win.once('ready-to-show', () => {
    splashProgress(100, 3);
    const elapsed = Date.now() - (createSplash.startedAt || 0);
    const wait = Math.max(360, 1400 - elapsed); // 动画最短 1.4s，完整演出
    setTimeout(() => {
      if (splash && !splash.isDestroyed()) splash.close();
      if (win && !win.isDestroyed()) win.show();
    }, wait);
  });
  let urlIdx = 0;
  const loadNext = () => {
    if (urlIdx >= URL_FALLBACKS.length) {
      // 回退链全挂 → 明明白白的错误页（黑窗之谜终结者）
      const html = `data:text/html;charset=utf-8,${encodeURIComponent(`
      <body style="background:#0b0f14;color:#e6edf3;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
      <div style="max-width:520px;line-height:1.9">
        <h2 style="color:#f85149">⚠️ 连不上 OpenMate 服务</h2>
        <p>已尝试地址：<b style="color:#79c0ff">${URL_FALLBACKS.join('、')}</b><br>最后错误：${loadNext.lastDesc || '不可达'} (code ${loadNext.lastCode || '?'})</p>
        <p style="color:#8b949e">排查三步：<br>
        ① 用浏览器打开上面的地址——浏览器也打不开=网络不通（同一 WiFi？NetBird 开着？）<br>
        ② 浏览器打得开=改 exe 同目录 config.json 的 url 后重启本程序<br>
        ③ 服务端在 Linux 机器上，确认它开着</p>
      </div></body>`)}`;
      win.loadURL(html);
      return;
    }
    win.loadURL(URL_FALLBACKS[urlIdx++]);
  };
  win.webContents.on('did-fail-load', (e, code, desc, validated, isMainFrame) => {
    if (!isMainFrame || code === -3) return; // -3=主动跳转噪音
    loadNext.lastCode = code;
    loadNext.lastDesc = desc;
    loadNext(); // 试下一个回退地址；全挂时 loadNext 自己写错误页
  });
  loadNext();

  // 外链/新窗口 → workspace 新标签页（事件桥交渲染层开标签，不再弹独立窗口）
  const toWorkspaceTab = (url) => {
    if (/^https?:/i.test(url)) win.webContents.send('openmate-open-web-tab', url);
  };
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) toWorkspaceTab(url);
    else shell.openExternal(url);
    return { action: 'deny' };
  });
  // 主窗口防漂移：普通外链不许把应用本身导航走
  const APP_ORIGIN = new URL(OPENMATE_URL).origin;
  win.webContents.on('will-navigate', (e, url) => {
    try {
      if (new URL(url).origin === APP_ORIGIN) return;
    } catch (err) { /* 非法 URL=拦 */ }
    e.preventDefault();
    toWorkspaceTab(url);
  });
  win.on('closed', () => { win = null; });
}

// ── 内嵌浏览器（<webview>）支撑 ──
// 语义：workspace 浏览器标签=真 Chromium 面板（真渲染/真输入），AI 走同一扇窗的
// webContents 直驱（executeJavaScript=页面本体 DOM，sendInputEvent=真实键鼠），
// 折叠面板=webview 隐藏但照跑（Electron 语义天然）。
app.on('web-contents-created', (_e, wc) => {
  if (wc.getType() !== 'webview') return;
  // 滚动条与对话框同款（globals.css 同一真源）：webview 是独立渲染进程，主界面 CSS 进不去，
  // 每页加载完成后注入同一样式（6px 定宽/透明轨/hover 显影）——样式一致性，非守卫
  const SCROLLBAR_CSS = `::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:transparent;border-radius:3px;transition:background .3s ease}
*:hover::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15)}
::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.3)}`;
  const injectScrollbarCss = () => { wc.insertCSS(SCROLLBAR_CSS).catch(() => {}); };
  wc.on('did-finish-load', injectScrollbarCss);
  injectScrollbarCss();
  // webview 里的 target=_blank / window.open → workspace 新标签（与主窗同款语义，不弹独立窗）
  wc.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) {
      const w = BrowserWindow.getAllWindows()[0];
      if (w && !w.isDestroyed()) w.webContents.send('openmate-open-web-tab', url);
    }
    return { action: 'deny' };
  });
});

const getWebviewWc = (tabId) => {
  const wc = webContents.fromId(tabId);
  return wc && !wc.isDestroyed() ? wc : null;
};
// 页面本体求值（AI 读页面/改 DOM 的主通道）
ipcMain.handle('webview-exec', async (_e, { tabId, code }) => {
  const wc = getWebviewWc(tabId);
  if (!wc) return { ok: false, error: 'webview 已关闭' };
  try { return { ok: true, result: await wc.executeJavaScript(code, true) }; }
  catch (err) { return { ok: false, error: String(err && err.message || err) }; }
});
// 真实键鼠（风控识别不出是机器）
ipcMain.handle('webview-input', (_e, { tabId, event }) => {
  const wc = getWebviewWc(tabId);
  if (!wc) return { ok: false, error: 'webview 已关闭' };
  wc.sendInputEvent(event);
  return { ok: true };
});
// 导航 / 取证截图
ipcMain.handle('webview-nav', (_e, { tabId, url }) => {
  const wc = getWebviewWc(tabId);
  if (!wc) return { ok: false, error: 'webview 已关闭' };
  wc.loadURL(url);
  return { ok: true };
});
ipcMain.handle('webview-capture', async (_e, { tabId }) => {
  const wc = getWebviewWc(tabId);
  if (!wc) return { ok: false, error: 'webview 已关闭' };
  // 窗口最小化/无合成表面时 capturePage 会永久挂起（实测）——8 秒显式报错，不挂死调用方
  const img = await Promise.race([
    wc.capturePage(),
    new Promise((_, rej) => setTimeout(() => rej(new Error('截图超时：窗口最小化或页面不可见时无合成表面可截')), 8000)),
  ]).catch((e) => ({ __err: e.message }));
  if (img && img.__err) return { ok: false, error: img.__err };
  return { ok: true, dataUrl: img.toDataURL() };
});

function createTray() {
  try {
    const iconPath = path.join(__dirname, 'icon.png');
    const img = nativeImage.createFromPath(iconPath);
    if (img.isEmpty()) return; // 无图标则不建托盘，不报错
    tray = new Tray(img.resize({ width: 22, height: 22 }));
    tray.setToolTip('OpenMate');
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: '显示主窗口', click: () => { if (win) { win.show(); win.focus(); } else createWindow(); } },
      { type: 'separator' },
      { label: '退出', click: () => app.quit() },
    ]));
    tray.on('click', () => { if (win) { win.show(); win.focus(); } else createWindow(); });
  } catch (e) {
    console.error('[tray]', e.message);
  }
}

// 在线更新：装一次，之后发版自动更（更新源=climbing.top/openmate-releases）
function initUpdater() {
  let autoUpdater;
  try {
    ({ autoUpdater } = require('electron-updater'));
  } catch (e) {
    console.error('[updater] electron-updater 不可用:', e.message);
    return;
  }
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true; // 不点重启也在退出时自动装
  const check = () => autoUpdater.checkForUpdates().catch((e) => console.error('[updater]', e.message));
  setTimeout(check, 10 * 1000); // 启动 10 秒后首查
  setInterval(check, 4 * 60 * 60 * 1000); // 之后每 4 小时
  autoUpdater.on('update-downloaded', (info) => {
    // 无感更新语义：后台下载已完成，安装延迟到退出（autoInstallOnAppQuit）——
    // 绝不打断正在跑的 CDP/Agent 任务（唯一硬限制=运行中不能换主 exe，故安装只在
    // 程序不运行时发生）。弹窗=知会型，默认"知道了"，"立即更新"是可选项。
    const w = BrowserWindow.getAllWindows()[0];
    if (w && !w.isDestroyed()) {
      w.webContents.send('openmate-update-ready', { version: info.version });
    } else {
      dialog.showMessageBox({
        type: 'info', buttons: ['知道了', '立即更新'], defaultId: 0, cancelId: 0,
        title: 'OpenMate 更新就绪',
        message: `新版本 ${info.version} 已在后台下载完成`,
        detail: '不打断正在跑的任务：下次退出应用时自动安装，下次打开即新版本。要现在更新点"立即更新"（会重启应用）。',
      }).then(({ response }) => {
        if (response === 1) autoUpdater.quitAndInstall(true, true); // 用户主动选才立即装+自启
      });
    }
  });
  ipcMain.on('openmate-update-action', (e, action) => {
    if (action === 'install') autoUpdater.quitAndInstall(true, true); // 用户主动选才立即装+自启
  });
  // 测试钩子：OPENMATE_FAKE_UPDATE=1 弹一次更新提示（验收/UI 调试）
  if (process.env.OPENMATE_FAKE_UPDATE === '1') {
    setTimeout(() => {
      const w = BrowserWindow.getAllWindows()[0];
      if (w && !w.isDestroyed()) w.webContents.send('openmate-update-ready', { version: '9.9.9' });
    }, 3000);
  }
}

// 主题联动：OpenMate 切主题 → 系统标题栏/窗口底色跟着切
ipcMain.on('openmate-theme-change', (e, mode) => {
  nativeTheme.themeSource = mode; // Windows 原生标题栏明暗由此跟随
  const w = BrowserWindow.fromWebContents(e.sender);
  if (w) w.setBackgroundColor(mode === 'light' ? '#ffffff' : '#0b0f14');
});

app.on('second-instance', () => {
  if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); }
});

app.whenReady().then(() => {
  startDownloadEngine(); // 拉起 Python 引擎子进程（首行 JSON 握手 → IPC 可取 port/token）
  createSplash();
  createWindow();
  createTray();
  initUpdater();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

// 退出时优雅停引擎：SIGTERM → 引擎落盘自状态（断点续传数据不丢）
app.on('will-quit', () => {
  if (engineProc) {
    try { engineProc.kill('SIGTERM'); } catch (e) { /* 已退出 */ }
    engineProc = null;
  }
});

app.on('window-all-closed', () => {
  // 留托盘常驻（聊天应用语义）；明确退出走托盘菜单
  if (process.platform !== 'darwin' && !tray) app.quit();
});
