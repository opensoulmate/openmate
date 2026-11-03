'use client';
import { useEffect, useRef, useState, useCallback } from 'react';
import { Terminal as TerminalIcon, X, Maximize2, Minimize2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { GitStatusBar } from './git-status-bar';

interface TerminalPanelProps {
  apiBase: string;
  token: string;
}

export function TerminalPanel({ apiBase, token }: TerminalPanelProps) {
  const { t } = useTranslation();
  const termRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [maximized, setMaximized] = useState(false);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const fitAddonRef = useRef<any>(null);

  const initTerminal = useCallback(async () => {
    if (!termRef.current || wsRef.current) return; // 幂等守卫：已有连接不再建（connected 进依赖会触发自杀循环）
    const { Terminal } = await import('@xterm/xterm');
    const { FitAddon } = await import('@xterm/addon-fit');
    const { WebLinksAddon } = await import('@xterm/addon-web-links');

    // 从 CSS 变量读取当前主题的颜色值，让终端配色跟随主题
    const cs = getComputedStyle(document.documentElement);
    const bg = cs.getPropertyValue('--color-background').trim() || '#0a0a0f';
    const fg = cs.getPropertyValue('--color-foreground').trim() || '#e4e4e7';
    const cursor = cs.getPropertyValue('--color-primary').trim() || '#7c3aed';
    const selBg = cs.getPropertyValue('--color-primary').trim() || '#7c3aed';

    const term = new Terminal({
      theme: { background: bg, foreground: fg, cursor: cursor, selectionBackground: selBg + '33' },
      fontFamily: 'monospace',
      fontSize: 13,
      lineHeight: 1.4,
      cursorBlink: true,
    });
    const fitAddon = new FitAddon();
    const webLinks = new WebLinksAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(webLinks);
    term.open(termRef.current);
    fitAddon.fit();
    fitAddonRef.current = fitAddon;

    const wsUrl = `ws://${window.location.hostname}:8090/ws/terminal?token=${token}`;
    const ws = new WebSocket(wsUrl);
    ws.onopen = () => { setConnected(true); term.focus(); };
    ws.onmessage = (e) => term.write(e.data);
    ws.onclose = () => { setConnected(false); term.write(`\r\n\x1b[31m${t('terminal.disconnected')}\x1b[0m\r\n`); };
    ws.onerror = () => term.write(`\r\n\x1b[31m${t('terminal.connectFailed')}\x1b[0m\r\n`);
    wsRef.current = ws;

    term.onData((data) => ws.readyState === 1 && ws.send(JSON.stringify({ type: 'input', data })));
    term.onResize(({ cols, rows }) => ws.readyState === 1 && ws.send(JSON.stringify({ type: 'resize', cols, rows })));

    const onResize = () => fitAddon.fit();
    window.addEventListener('resize', onResize);
    return () => { window.removeEventListener('resize', onResize); ws.close(); term.dispose(); };
  }, [token, t]); // 注意：绝不加 connected —— 连接成功改状态→重渲染→旧清理掐死新连接（实测 3 连接后停尸'断开连接'）

  // 开关从 footbar 右侧按钮来（与左侧用户按钮对称）——事件桥，状态仍在本组件
  useEffect(() => {
    const onToggle = () => setOpen((o) => !o);
    window.addEventListener('openmate-terminal-toggle', onToggle);
    return () => window.removeEventListener('openmate-terminal-toggle', onToggle);
  }, []);

  useEffect(() => {
    if (open) initTerminal();
    return () => { wsRef.current?.close(); wsRef.current = null; };
  }, [open, initTerminal]);

  const handleRunCommand = useCallback(
    (command: string) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'input', data: command + '\n' }));
      }
    },
    []
  );

  return (
    <>
      {/* Terminal panel */}
      {open && (
        <div className={`fixed z-40 bg-[var(--color-background)] border border-border rounded-t-xl shadow-2xl transition-all ${maximized ? 'inset-0' : 'bottom-0 right-2 lg:right-14 w-[calc(100%-1rem)] lg:w-[700px] h-[50vh] lg:h-[400px]'}`}>
          <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-[var(--color-card)] rounded-t-xl">
            <div className="flex items-center gap-2">
              <TerminalIcon className="w-4 h-4 text-muted-foreground" />
              <span className="text-xs text-muted-foreground">{t('terminal.title')}</span>
              <span className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
            </div>
            <div className="flex items-center gap-1">
              <button onClick={() => setMaximized(!maximized)} className="p-1 rounded hover:bg-muted text-muted-foreground">
                {maximized ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
              </button>
              <button onClick={() => setOpen(false)} className="p-1 rounded hover:bg-muted text-muted-foreground">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
          {/* Git Status Bar */}
          <GitStatusBar apiBase={apiBase} token={token} onRunCommand={handleRunCommand} />
          {/* Terminal container */}
          <div ref={termRef} className="w-full h-[calc(100%-90px)]" />
        </div>
      )}
    </>
  );
}
