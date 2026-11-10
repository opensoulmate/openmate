// OpenMate Desktop — 主题桥：渲染层把明暗同步给壳（标题栏/窗口底色跟随）
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('openmateDesktop', {
  setTheme: (mode) => ipcRenderer.send('openmate-theme-change', mode === 'light' ? 'light' : 'dark'),
  // 更新就绪 → 渲染层主题化弹窗（返回退订句柄）
  onUpdateReady: (cb) => {
    const h = (_e, data) => cb(data);
    ipcRenderer.on('openmate-update-ready', h);
    return () => ipcRenderer.removeListener('openmate-update-ready', h);
  },
  updateAction: (action) => ipcRenderer.send('openmate-update-action', action),
  // 外链/新窗口 → workspace 新标签页（渲染层监听，返回退订句柄）
  onOpenWebTab: (cb) => {
    const h = (_e, url) => cb(url);
    ipcRenderer.on('openmate-open-web-tab', h);
    return () => ipcRenderer.removeListener('openmate-open-web-tab', h);
  },
  // ── 内嵌浏览器 AI 直驱（同一扇窗：executeJavaScript=页面DOM，sendInputEvent=真实键鼠）──
  webviewExec: (tabId, code) => ipcRenderer.invoke('webview-exec', { tabId, code }),
  webviewInput: (tabId, event) => ipcRenderer.invoke('webview-input', { tabId, event }),
  webviewNav: (tabId, url) => ipcRenderer.invoke('webview-nav', { tabId, url }),
  webviewCapture: (tabId) => ipcRenderer.invoke('webview-capture', { tabId }),
});
