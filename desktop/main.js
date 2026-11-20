// OpenMate Desktop — Electron 薄壳（对标 Hermes Desktop 架构：壳+同一后端，同步架构自带）
const { app, BrowserWindow, Tray, Menu, shell, nativeImage, nativeTheme, ipcMain, dialog, webContents, session } = require('electron');
const path = require('path');

// ── 下载引擎子进程（Python 引擎薄壳集成）────────────────────────────
// 铁律：下载逻辑全部在 Python 引擎侧（python -m download_engine / PyInstaller 冻结单文件），
// 本文件只做三件事：spawn 引擎子进程、读首行 JSON 握手（port/token）、IPC 转发给 renderer。
// 禁在 JS 写任何下载逻辑（分段/续传/协议一律 Python）。
const { spawn } = require('child_process');
const fs = require('fs');
let engineProc = null;
let engineInfo = null; // {event, port, token, pid, dataDir, origin, version} —— 握手行原样
let engineInfoWaiters = []; // 等握手的 getInfo 调用方（页面加载可能快于 PyInstaller 解包+握手）

function resolveEngineCmd() {
  // 冻结单文件（PyInstaller）优先：env 指定 > 打包资源 > exe 旁 > 开发模式 core/dist。
  // Windows 产物名带 .exe（PyInstaller win 行为）——每个目录两种名字都试，
  // 这是「桌面图标/已安装版」启动时引擎解析的关键路径：漏 .exe 会静默掉进 python 回退。
  const names = process.platform === 'win32'
    ? ['download-engine.exe', 'download-engine']
    : ['download-engine', 'download-engine.exe'];
  const dirs = [
    process.resourcesPath || null,              // 打包版：extraResources 落点 resources/
    path.dirname(process.execPath),             // 绿色包/安装版：exe 旁
    path.join(__dirname, '..', 'core', 'dist'), // 开发模式
  ].filter(Boolean);
  const frozenCandidates = [
    process.env.OPENMATE_ENGINE_BIN || null,
    ...dirs.flatMap((dir) => names.map((n) => path.join(dir, n))),
  ].filter(Boolean);
  for (const bin of frozenCandidates) {
    try { if (fs.existsSync(bin)) return { cmd: bin, args: [], env: {} }; } catch (e) { /* 试下一个 */ }
  }
  // 开发模式回退：python -m download_engine（PYTHONPATH 指向 openmate/core）
  const python = process.env.OPENMATE_ENGINE_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
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
  console.log('[download-engine] spawn:', cmd);
  engineProc.on('error', (e) => {
    // 引擎可执行缺失/无执行权限（安装版没带引擎、机器无 python）——如实降级：
    // 不崩壳（unhandled 'error' 会炸整个主进程），getInfo() 如实返回 null。
    console.error('[download-engine] spawn error:', e.message);
    engineProc = null;
    engineInfo = null;
    for (const r of engineInfoWaiters) r(null);
    engineInfoWaiters = [];
  });
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

// Electron 麦克风修复之二：把应用自有 http 回退源标记为可信安全上下文（Chromium 开关）。
// 无本机前端的 Windows 端最终落到 http://100.76.2.155:3000 托管版时，非安全上下文下
// navigator.mediaDevices 直接不存在（Electron 45 实测：noflag secure:false/gum:false，
// 带 flag secure:true/gum:true，getUserMedia 出真实音频轨）。只标记本应用回退链内的
// http 源（config/env 指定 + 回退链），不做全域放开；localhost 类源本就可信，一并列入无害。
{
  const insecureOrigins = [...new Set(URL_FALLBACKS.map((u) => {
    try { return new URL(u).protocol === 'http:' ? new URL(u).origin : null; } catch (e) { return null; }
  }).filter(Boolean))];
  if (insecureOrigins.length) {
    app.commandLine.appendSwitch('unsafely-treat-insecure-origin-as-secure', insecureOrigins.join(','));
  }
}

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
  // origin 比对必须认「配置地址」和「实际加载地址」两个（地址回退链可能把窗口
  // 落到 127.0.0.1/NetBird/配置域名任意一个）——只认配置 origin 时，站内
  // window.location.href 硬跳（登录/登出/过期接管全是这种）会被误判外链拦下、
  // 转进右侧 workspace 标签 =「点退出后登录界面渲染进右侧 workspace」病灶。
  const APP_ORIGIN = new URL(OPENMATE_URL).origin;
  win.webContents.on('will-navigate', (e, url) => {
    try {
      const target = new URL(url);
      const cur = new URL(win.webContents.getURL() || OPENMATE_URL);
      if (target.origin === APP_ORIGIN || target.origin === cur.origin) return;
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

/** 托盘图标加载：平台基准尺寸（Linux 22 / Windows 16 / macOS 16）+ @2x/@3x 高清表示 */
function loadTrayImage() {
  const trayDir = path.join(__dirname, 'tray');
  // Windows 直接用全尺寸 .ico（16/24/32/48/64/256 内嵌，由 shell 选最优尺寸）
  if (process.platform === 'win32') {
    const ico = nativeImage.createFromPath(path.join(trayDir, 'tray.ico'));
    if (!ico.isEmpty()) return ico;
  }
  const base = process.platform === 'darwin' ? 16 : process.platform === 'win32' ? 16 : 22;
  const png = (px) => path.join(trayDir, `tray-${px}.png`);
  const img = nativeImage.createFromPath(png(base));
  if (img.isEmpty()) {
    // 兜底：旧单图（无多分辨率资产时）——仅作不崩保险，不作为正常路径
    return nativeImage.createFromPath(path.join(__dirname, 'icon.png'));
  }
  // HiDPI：把 2x/3x 尺寸挂为高分表示，托盘按显示器 scaleFactor 自动选清晰版
  for (const sf of [2, 3]) {
    const hi = png(Math.min(256, base * sf));
    if (fs.existsSync(hi)) {
      img.addRepresentation({ scaleFactor: sf, dataURL: 'data:image/png;base64,' + fs.readFileSync(hi).toString('base64') });
    }
  }
  return img;
}

function createTray() {
  try {
    // 高清托盘：多分辨率图标集（tray/tray-*.png + tray.ico），按平台基准尺寸取图，
    // HiDPI 挂 2x/3x 表示让托盘按 scaleFactor 选清晰版——禁再单尺寸缩放（缩放=模糊根因）
    const img = loadTrayImage();
    if (img.isEmpty()) return; // 无图标则不建托盘，不报错
    tray = new Tray(img);
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
  // 麦克风/媒体权限显式放行（语音输入）：维持 Electron 原默认（放行全部请求）并显式覆盖
  // media/麦克风。注意：权限放行只解决"权限请求"一层，页面还必须处于安全上下文
  // （localhost/HTTPS）才有 navigator.mediaDevices——config.json 的 url 走
  // http://localhost:3000 回源；经 http://IP:3000 远程加载时浏览器会直接禁用麦克风接口
  // （前端会给出原因+指引，详见 docs/voice-https-entry.md）。
  session.defaultSession.setPermissionRequestHandler((wc, permission, callback) => {
    callback(true);
  });
  session.defaultSession.setPermissionCheckHandler(() => true);
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
