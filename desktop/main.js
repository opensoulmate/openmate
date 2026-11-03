// OpenMate Desktop — Electron 薄壳（对标 Hermes Desktop 架构：壳+同一后端，同步架构自带）
const { app, BrowserWindow, Tray, Menu, shell, nativeImage, nativeTheme, ipcMain, dialog } = require('electron');
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
      preload: path.join(__dirname, 'preload.js'),
    },
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

  // 外链=内置浏览器子窗口（app 内看网页，不再甩系统浏览器）
  const openInternalBrowser = (url) => {
    const b = new BrowserWindow({
      width: 1100,
      height: 780,
      icon: path.join(__dirname, 'icon.png'),
      backgroundColor: nativeTheme.shouldUseDarkColors ? '#0b0f14' : '#ffffff',
      autoHideMenuBar: true,
      webPreferences: { nodeIntegration: false, contextIsolation: true }, // 外部网页=纯沙箱
    });
    b.loadURL(url);
  };
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) openInternalBrowser(url);
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
    if (/^https?:/i.test(url)) openInternalBrowser(url);
  });
  win.on('closed', () => { win = null; });
}

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
    const opts = {
      type: 'info', buttons: ['立即重启', '稍后'], defaultId: 0, cancelId: 1,
      title: 'OpenMate 更新就绪',
      message: `新版本 ${info.version} 已下载完成`,
      detail: '点"立即重启"完成更新；不重启的话，下次退出应用时会自动安装。',
    };
    const w = BrowserWindow.getAllWindows()[0];
    (w ? dialog.showMessageBox(w, opts) : dialog.showMessageBox(opts)).then(({ response }) => {
      if (response === 0) autoUpdater.quitAndInstall();
    });
  });
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
  createWindow();
  createTray();
  initUpdater();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('window-all-closed', () => {
  // 留托盘常驻（聊天应用语义）；明确退出走托盘菜单
  if (process.platform !== 'darwin' && !tray) app.quit();
});
