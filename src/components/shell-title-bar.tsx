'use client';

// 桌面壳自绘标题栏（仅 Tauri 桌面端渲染；浏览器/移动端不出现）
// 名称+副标题居左、窗口钮居右，32px 薄条（对齐 Electron 观感）。
import { useEffect, useState } from 'react';

declare global {
  interface Window {
    __TAURI_INTERNALS__?: {
      invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
    };
  }
}

const isTauri = () =>
  typeof window !== 'undefined' &&
  !!window.__TAURI_INTERNALS__ &&
  // 移动端（Android/iOS 壳）不显示桌面标题栏
  !/Android|iPhone|iPad/i.test(navigator.userAgent);

export function ShellTitleBar() {
  const [mounted, setMounted] = useState(false);
  const [title, setTitle] = useState('OpenMate');
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    if (!isTauri()) return;
    setMounted(true);
    // 窗口圆角：html 标记 tauri-shell（globals.css 裁圆角），最大化时恢复直角
    const root = document.documentElement;
    root.classList.add('tauri-shell');
    const syncMax = () => {
      window.__TAURI_INTERNALS__
        ?.invoke('is_maximized_window')
        .then((max: unknown) => {
          root.classList.toggle('tauri-maximized', !!max);
          setMaximized(!!max);
        })
        .catch(() => {});
    };
    syncMax();
    window.addEventListener('resize', syncMax);
    // 启动 splash 报到：React 已挂载=RENDERING 完成，Rust 侧走完打字机动画后交接主窗口
    window.__TAURI_INTERNALS__?.invoke('splash_ready').catch(() => {});
    const sync = () => {
      const t = document.title || 'OpenMate';
      setTitle(t);
      // 任务栏/Alt-Tab 标题同步
      window.__TAURI_INTERNALS__?.invoke('set_page_title', { title: t });
    };
    sync();
    const el = document.querySelector('title');
    const obs = el
      ? new MutationObserver(sync)
      : null;
    if (el && obs) obs.observe(el, { childList: true, characterData: true, subtree: true });
    return () => {
      obs?.disconnect();
      window.removeEventListener('resize', syncMax);
      root.classList.remove('tauri-shell');
    };
  }, []);

  if (!mounted) return null;

  const invoke = (cmd: string) => window.__TAURI_INTERNALS__?.invoke(cmd);
  const parts = title.split(/[｜|]/);
  const name = (parts[0] || 'OpenMate').trim();
  const subtitle = parts.slice(1).join(' ').trim();

  return (
    <div
      data-tauri-drag-region
      className="om-chrome flex h-8 shrink-0 select-none items-center justify-between border-b border-border/40 bg-background text-foreground"
    >
      <div data-tauri-drag-region className="flex min-w-0 items-baseline gap-2 pl-3 text-xs">
        <span className="truncate font-semibold">{name}</span>
        {subtitle && <span className="truncate opacity-60">{subtitle}</span>}
      </div>
      <div className="flex h-full">
        <button
          type="button"
          aria-label="最小化"
          onClick={() => invoke('minimize_window')}
          className="h-full w-11 text-[11px] opacity-70 transition-colors hover:bg-foreground/10 hover:opacity-100"
        >
          ─
        </button>
        <button
          type="button"
          aria-label={maximized ? '还原' : '最大化'}
          onClick={() => invoke('toggle_maximize_window')}
          className="h-full w-11 text-[11px] opacity-70 transition-colors hover:bg-foreground/10 hover:opacity-100"
        >
          {maximized ? '❐' : '▢'}
        </button>
        <button
          type="button"
          aria-label="关闭"
          onClick={() => invoke('close_window')}
          className="h-full w-11 text-[11px] opacity-70 transition-colors hover:bg-red-600 hover:text-white hover:opacity-100"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
