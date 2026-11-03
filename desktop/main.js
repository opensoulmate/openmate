// OpenMate Desktop — Electron 薄壳（对标 Hermes Desktop 架构：壳+同一后端，同步架构自带）
const { app, BrowserWindow, Tray, Menu, shell, nativeImage, nativeTheme, ipcMain } = require('electron');
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

  // 外链用系统浏览器打开（别把应用窗口当浏览器标签用）
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
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
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('window-all-closed', () => {
  // 留托盘常驻（聊天应用语义）；明确退出走托盘菜单
  if (process.platform !== 'darwin' && !tray) app.quit();
});
