// OpenMate Desktop — 主题桥：渲染层把明暗同步给壳（标题栏/窗口底色跟随）
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('openmateDesktop', {
  setTheme: (mode) => ipcRenderer.send('openmate-theme-change', mode === 'light' ? 'light' : 'dark'),
  windowControls: {
    minimize: () => ipcRenderer.send('win-minimize'),
    maximize: () => ipcRenderer.send('win-maximize'),
    close: () => ipcRenderer.send('win-close'),
  },
});
