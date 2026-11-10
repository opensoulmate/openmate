// Tauri 桌面壳的 openmateDesktop 桥——与 Electron preload 同款 API（onUpdateReady/updateAction），
// app-shell 的主题化更新弹窗零改动复用。
// 更新源=climbing.top/openmate-releases/tauri-updater.json（Electron 时代同款通道，签名验证由
// tauri-plugin-updater 完成：pubkey 在 tauri.conf.json，私钥本机 ~/.tauri/keys/ 不进 git）。
// 节奏照抄 Electron：启动 10s 后首查、每 4 小时复检；有新版自动下载，就绪后弹提示。

type UpdateListener = (data: { version: string }) => void;

let listener: UpdateListener | null = null;
let pending: { version: string; install: () => Promise<void> } | null = null;
let notifiedVersion = '';

export function initTauriDesktopBridge() {
  const w = window as any;
  // 非 Tauri 壳（web/Electron）不动：Electron 有自己的 preload 桥
  if (typeof window === 'undefined' || !window.__TAURI_INTERNALS__) return;
  if (w.openmateDesktop?.onUpdateReady) return;

  w.openmateDesktop = {
    onUpdateReady(cb: UpdateListener) {
      listener = cb;
      return () => {
        listener = null;
      };
    },
    async updateAction(action: string) {
      if (action !== 'install' || !pending) return;
      await pending.install();
    },
  };

  const run = async () => {
    try {
      const { check } = await import('@tauri-apps/plugin-updater');
      const { relaunch } = await import('@tauri-apps/plugin-process');
      const update = await check();
      if (!update) return;
      if (pending?.version !== update.version) {
        await update.download();
        pending = {
          version: update.version,
          install: async () => {
            await update.install();
            await relaunch();
          },
        };
      }
      if (notifiedVersion !== update.version) {
        notifiedVersion = update.version;
        listener?.({ version: update.version });
      }
    } catch (e) {
      console.error('[updater]', e);
    }
  };
  setTimeout(run, 10 * 1000);
  setInterval(run, 4 * 60 * 60 * 1000);
}
