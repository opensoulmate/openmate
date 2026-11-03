// OpenMate Desktop — 主题桥：渲染层把明暗同步给壳（标题栏/窗口底色跟随）
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('openmateDesktop', {
  setTheme: (mode) => ipcRenderer.send('openmate-theme-change', mode === 'light' ? 'light' : 'dark'),
  // 外链/新窗口 → workspace 新标签页（渲染层监听，返回退订句柄）
  onOpenWebTab: (cb) => {
    const h = (_e, url) => cb(url);
    ipcRenderer.on('openmate-open-web-tab', h);
    return () => ipcRenderer.removeListener('openmate-open-web-tab', h);
  },
});
