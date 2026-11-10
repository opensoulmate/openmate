// OpenMate Desktop — Electron 薄壳（对标 Hermes Desktop 架构：壳+同一后端，同步架构自带）
const { app, BrowserWindow, Tray, Menu, shell, nativeImage, nativeTheme, ipcMain, dialog, webContents } = require('electron');
const path = require('path');

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
    backgroundColor: '#0b0f14',
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
  win.loadURL(OPENMATE_URL);

  // 自诊断：连不上就在窗口里写清原因（黑窗之谜→明明白白的错误页）
  win.webContents.on('did-fail-load', (e, code, desc, url, isMainFrame) => {
    if (!isMainFrame || code === -3) return; // -3=主动跳转噪音
    const html = `data:text/html;charset=utf-8,${encodeURIComponent(`
      <body style="background:#0b0f14;color:#e6edf3;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
      <div style="max-width:520px;line-height:1.9">
        <h2 style="color:#f85149">⚠️ 连不上 OpenMate 服务</h2>
        <p>目标地址：<b style="color:#79c0ff">${OPENMATE_URL}</b><br>错误：${desc} (code ${code})</p>
        <p style="color:#8b949e">排查三步：<br>
        ① 用浏览器打开上面的地址——浏览器也打不开=网络不通（同一 WiFi？NetBird 开着？）<br>
        ② 浏览器打得开=改本目录 config.json 的 url 后重启本程序<br>
        ③ 服务端在 Linux 机器上，确认它开着</p>
      </div></body>`)}`;
    win.loadURL(html);
  });

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
  const img = await wc.capturePage();
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
  createSplash();
  createWindow();
  createTray();
  initUpdater();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('window-all-closed', () => {
  // 留托盘常驻（聊天应用语义）；明确退出走托盘菜单
  if (process.platform !== 'darwin' && !tray) app.quit();
});
