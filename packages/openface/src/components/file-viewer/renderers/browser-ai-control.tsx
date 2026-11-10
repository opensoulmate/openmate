'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import {
  Globe, Send, Loader2, X, MousePointer, Type, ArrowRight,
  ChevronLeft, ChevronRight, RotateCw, Home, Sparkles,
} from 'lucide-react';

interface BrowserAIControlProps {
  /** Initial URL to load */
  initialUrl?: string;
  /** AI callback: sends page context + instruction, returns actions to execute */
  onAIAction: (context: PageContext, instruction: string) => Promise<AIAction[] | null>;
}

interface PageContext {
  url: string;
  title: string;
  /** Simplified DOM tree for AI */
  domSummary: string;
  /** Interactive elements */
  interactiveElements: InteractiveElement[];
}

interface InteractiveElement {
  tag: string;
  id: string;
  name: string;
  type: string;
  placeholder: string;
  text: string;
  href: string;
  selector: string;
  rect: { x: number; y: number; width: number; height: number };
}

interface AIAction {
  type: 'click' | 'type' | 'navigate' | 'scroll' | 'wait' | 'extract';
  selector?: string;
  value?: string;
  url?: string;
  description: string;
}

interface ActionLog {
  action: AIAction;
  status: 'pending' | 'success' | 'error';
  error?: string;
}

// ── Electron 内嵌真浏览器（<webview>）引擎 ──
// 同一扇窗双用途：人手动=真渲染真输入；AI=主进程直驱 webContents（页面本体 DOM+真实键鼠）。
const WebViewTag = 'webview' as any;

// 页面本体提取（跨域无碍——在页面自己肚子里执行）：返回与 iframe 版同构的 PageContext
const EXTRACT_SCRIPT = `(() => {
  const selectors = ['a','button','input','textarea','select','[role="button"]','[onclick]'];
  const els = [];
  selectors.forEach(function(sel) {
    document.querySelectorAll(sel).forEach(function(el, i) {
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return;
      let s = el.tagName.toLowerCase();
      if (el.id) { s = '#' + el.id; }
      else if (el.name) { s = el.tagName.toLowerCase() + '[name="' + el.name + '"]'; }
      els.push({
        tag: el.tagName.toLowerCase(), id: el.id || '', name: el.name || '',
        type: el.type || '', placeholder: el.placeholder || '',
        text: (el.textContent || '').trim().slice(0, 50), href: el.href || '',
        selector: s, rect: { x: r.x, y: r.y, width: r.width, height: r.height },
      });
    });
  });
  return {
    url: location.href, title: document.title || '',
    domSummary: (document.body ? document.body.innerText : '').slice(0, 2000),
    interactiveElements: els,
  };
})()`;

async function executeActionElectron(
  action: AIAction, bridge: any, wv: any,
): Promise<{ success: boolean; error?: string }> {
  const tabId = wv.getWebContentsId?.();
  if (!tabId) return { success: false, error: 'webview 未就绪' };
  const exec = (code: string) => bridge.webviewExec(tabId, code);
  switch (action.type) {
    case 'click': {
      if (!action.selector) return { success: false, error: '缺少selector' };
      // 元素中心坐标 → 真实鼠标事件（可信输入，风控识别不出是机器）
      const r = await exec(`(() => { const el = document.querySelector(${JSON.stringify(action.selector)}); if (!el) return null; el.scrollIntoView({ block: 'center' }); const b = el.getBoundingClientRect(); return { x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2) }; })()`);
      if (!r.ok) return { success: false, error: r.error };
      if (!r.result) return { success: false, error: '未找到元素: ' + action.selector };
      const { x, y } = r.result;
      await bridge.webviewInput(tabId, { type: 'mouseDown', x, y, button: 'left', clickCount: 1 });
      await bridge.webviewInput(tabId, { type: 'mouseUp', x, y, button: 'left', clickCount: 1 });
      return { success: true };
    }
    case 'type': {
      if (!action.selector || !action.value) return { success: false, error: '缺少selector或value' };
      const f = await exec(`!!(document.querySelector(${JSON.stringify(action.selector)}) || {}).focus`);
      if (!f.ok) return { success: false, error: f.error };
      await exec(`document.querySelector(${JSON.stringify(action.selector)})?.focus()`);
      await bridge.webviewInput(tabId, { type: 'char', keyCode: action.value });
      return { success: true };
    }
    case 'navigate': {
      if (!action.url) return { success: false, error: '缺少URL' };
      await bridge.webviewNav(tabId, action.url);
      return { success: true };
    }
    case 'scroll': {
      const scrollY = parseInt(action.value || '500') || 500;
      await exec(`window.scrollBy(0, ${scrollY})`);
      return { success: true };
    }
    case 'wait': {
      const ms = parseInt(action.value || '1000') || 1000;
      await new Promise((r) => setTimeout(r, ms));
      return { success: true };
    }
    case 'extract':
      return { success: true };
    default:
      return { success: false, error: '未知操作类型: ' + action.type };
  }
}

export function BrowserAIControl({ initialUrl, onAIAction }: BrowserAIControlProps) {
  const [url, setUrl] = useState(initialUrl || 'about:blank');
  const [inputUrl, setInputUrl] = useState(initialUrl || '');
  const [loading, setLoading] = useState(false);
  const [aiMode, setAiMode] = useState(false);
  const [aiInstruction, setAiInstruction] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [actionLog, setActionLog] = useState<ActionLog[]>([]);
  const [showLog, setShowLog] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const webviewRef = useRef<any>(null);
  const isElectron = typeof window !== 'undefined' && !!(window as any).openmateDesktop?.webviewExec;
  const [runnerState, setRunnerState] = useState<{ action: string; paused: boolean; at: number } | null>(null);
  // ── ⏺ 录制业务流程（录制→沉淀为配方→SoulMate replay 回放）──
  const [recording, setRecording] = useState(false);
  const [recCount, setRecCount] = useState(0);
  const [flowName, setFlowName] = useState('');
  const [showSaveBar, setShowSaveBar] = useState(false);
  const recOnRef = useRef(false);
  const recStepsRef = useRef<any[]>([]);
  const recTimerRef = useRef<any>(null);
  const webviewIdRef = useRef<number | null>(null);

  const navigate = useCallback((targetUrl: string) => {
    let finalUrl = targetUrl;
    if (!finalUrl.startsWith('http') && !finalUrl.startsWith('about:')) {
      finalUrl = 'https://' + finalUrl;
    }
    setUrl(finalUrl);
    setInputUrl(finalUrl);
    setLoading(true);
  }, []);

  // initialUrl 变化即跳转（tab 切换/外部驱动的 URL 更新都要生效——防实例复用吃掉新地址）
  useEffect(() => {
    if (initialUrl && initialUrl !== url) navigate(initialUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialUrl]);

  // webview 加载状态/URL 同步（Electron 真浏览器的事件流，替代 iframe 的 onLoad）
  useEffect(() => {
    const wv = webviewRef.current;
    if (!isElectron || !wv?.addEventListener) return;
    const onNav = (e: any) => {
      const u = e?.url || wv.getAttribute?.('src');
      if (u) { setUrl(u); setInputUrl(u); }
      setLoading(false);
      // 录制：导航=goto 步骤（与页内步骤同一时间轴）
      if (recOnRef.current && u) {
        const steps = recStepsRef.current;
        const last = steps[steps.length - 1];
        if (!last || last.action !== 'goto' || last.url !== u) {
          steps.push({ action: 'goto', url: u });
          setRecCount(steps.length);
        }
      }
    };
    wv.addEventListener('did-stop-loading', onNav);
    wv.addEventListener('did-navigate', onNav);
    wv.addEventListener('did-navigate-in-page', onNav);
    return () => {
      wv.removeEventListener?.('did-stop-loading', onNav);
      wv.removeEventListener?.('did-navigate', onNav);
      wv.removeEventListener?.('did-navigate-in-page', onNav);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isElectron]);

  // SoulMate 自动执行状态（接管可见性：▶ 自动执行中 / ⏸ 已接管+继续按钮）
  useEffect(() => {
    const onState = (e: any) => setRunnerState(e?.detail || null);
    window.addEventListener('om-runner-state', onState);
    return () => window.removeEventListener('om-runner-state', onState);
  }, []);

  // ── ⏺ 录制控制（页内步骤 300ms 收取 + 导航 goto 步骤同轴合并）──
  const recCtl = () => (window as any).__omRecordingCtl;
  const startRecording = async () => {
    const id = webviewIdRef.current;
    if (!id || !recCtl()) return;
    recStepsRef.current = [];
    setRecCount(0);
    recOnRef.current = true;
    setRecording(true);
    await recCtl().start(id);
    recTimerRef.current = window.setInterval(async () => {
      const id2 = webviewIdRef.current;
      if (!id2 || !recCtl()) return;
      const evs: any[] = (await recCtl().take(id2).catch(() => [])) || [];
      if (evs.length) {
        recStepsRef.current.push(...evs);
        setRecCount(recStepsRef.current.length);
      }
    }, 300);
  };
  const stopRecording = async () => {
    recOnRef.current = false;
    setRecording(false);
    if (recTimerRef.current) { window.clearInterval(recTimerRef.current); recTimerRef.current = null; }
    const id = webviewIdRef.current;
    if (id && recCtl()) {
      const left: any[] = (await recCtl().stop(id).catch(() => [])) || [];
      if (left.length) recStepsRef.current.push(...left);
    }
    setRecCount(recStepsRef.current.length);
    if (recStepsRef.current.length > 0) setShowSaveBar(true);
  };
  const saveFlow = async () => {
    const name = flowName.trim();
    if (!name) return;
    try {
      const host = location.hostname || '127.0.0.1';
      await fetch(`http://${host}:8092/api/browser/flows`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, steps: recStepsRef.current }),
      });
    } catch { /* 保存失败保留界面可重试 */ }
    recStepsRef.current = [];
    setRecCount(0);
    setShowSaveBar(false);
    setFlowName('');
  };

  // webview 注册进全局标签注册表（SoulMate 自动化执行器经它定位内置浏览器）
  useEffect(() => {
    if (!isElectron) return;
    const wv = webviewRef.current;
    const g = window as any;
    g.__omWebviews = g.__omWebviews || {};
    const register = () => {
      try {
        const id = wv?.getWebContentsId?.();
        if (id) { g.__omWebviews[id] = wv; webviewIdRef.current = id; }
      } catch { /* webview 未就绪 */ }
    };
    register();
    wv?.addEventListener?.('dom-ready', register);
    return () => {
      try {
        const id = wv?.getWebContentsId?.();
        if (id) delete g.__omWebviews[id];
      } catch { /* 已销毁 */ }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isElectron]);

  // Extract page context from iframe
  const extractPageContext = useCallback(async (): Promise<PageContext | null> => {
    // Electron 真浏览器：在页面本体里提取（跨域无碍），与 iframe 版同构
    if (isElectron && webviewRef.current) {
      const bridge = (window as any).openmateDesktop;
      const tabId = webviewRef.current.getWebContentsId?.();
      if (!tabId) return null;
      const res = await bridge.webviewExec(tabId, EXTRACT_SCRIPT);
      return res?.ok ? res.result : null;
    }
    const iframe = iframeRef.current;
    if (!iframe) return null;

    try {
      const doc = iframe.contentDocument || iframe.contentWindow?.document;
      if (!doc) return null;

      const title = doc.title || '';
      const iframeUrl = iframe.contentWindow?.location?.href || url;

      // Extract interactive elements
      const interactiveElements: InteractiveElement[] = [];
      const selectors = ['a', 'button', 'input', 'textarea', 'select', '[role="button"]', '[onclick]'];

      selectors.forEach(selector => {
        doc.querySelectorAll(selector).forEach((el, i) => {
          const htmlEl = el as HTMLElement;
          const rect = htmlEl.getBoundingClientRect();
          const tag = htmlEl.tagName.toLowerCase();
          const id = htmlEl.id || '';
          const name = (htmlEl as HTMLInputElement).name || '';
          const type = (htmlEl as HTMLInputElement).type || '';
          const placeholder = (htmlEl as HTMLInputElement).placeholder || '';
          const text = htmlEl.textContent?.trim().slice(0, 50) || '';
          const href = (htmlEl as HTMLAnchorElement).href || '';

          // Generate a unique selector
          let uniqueSelector = tag;
          if (id) uniqueSelector = `#${id}`;
          else if (name) uniqueSelector = `${tag}[name="${name}"]`;
          else uniqueSelector = `${tag}:nth-of-type(${i + 1})`;

          if (rect.width > 0 && rect.height > 0) {
            interactiveElements.push({
              tag, id, name, type, placeholder, text, href,
              selector: uniqueSelector,
              rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
            });
          }
        });
      });

      // Build DOM summary for AI
      const bodyText = doc.body?.textContent?.slice(0, 2000) || '';
      const domSummary = `页面标题: ${title}\nURL: ${iframeUrl}\n\n页面内容摘要:\n${bodyText.slice(0, 1500)}\n\n可交互元素 (${interactiveElements.length}个):\n${interactiveElements.slice(0, 30).map((el, i) =>
        `${i + 1}. [${el.tag}] ${el.text || el.placeholder || el.href || el.name || el.id || '(无文本)'} ${el.type ? `type=${el.type}` : ''}`
      ).join('\n')}`;

      return { url: iframeUrl, title, domSummary, interactiveElements };
    } catch (e) {
      // Cross-origin - can't access
      return {
        url, title: '', domSummary: `无法访问页面内容（跨域限制）。URL: ${url}`,
        interactiveElements: [],
      };
    }
  }, [url]);

  // Execute AI action on the page
  const executeAction = useCallback(async (action: AIAction): Promise<{ success: boolean; error?: string }> => {
    // Electron 真浏览器：主进程直驱（真实键鼠+页面本体 DOM）
    if (isElectron && webviewRef.current) {
      return executeActionElectron(action, (window as any).openmateDesktop, webviewRef.current);
    }
    const iframe = iframeRef.current;
    if (!iframe) return { success: false, error: 'iframe not found' };

    try {
      const doc = iframe.contentDocument || iframe.contentWindow?.document;
      if (!doc) return { success: false, error: '无法访问页面（跨域）' };

      switch (action.type) {
        case 'click': {
          if (!action.selector) return { success: false, error: '缺少selector' };
          const el = doc.querySelector(action.selector) as HTMLElement;
          if (!el) return { success: false, error: `未找到元素: ${action.selector}` };
          el.click();
          return { success: true };
        }
        case 'type': {
          if (!action.selector || !action.value) return { success: false, error: '缺少selector或value' };
          const input = doc.querySelector(action.selector) as HTMLInputElement;
          if (!input) return { success: false, error: `未找到输入框: ${action.selector}` };
          input.focus();
          input.value = action.value;
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
          return { success: true };
        }
        case 'navigate': {
          if (!action.url) return { success: false, error: '缺少URL' };
          iframe.src = action.url;
          setUrl(action.url);
          setInputUrl(action.url);
          return { success: true };
        }
        case 'scroll': {
          const scrollY = parseInt(action.value || '500') || 500;
          iframe.contentWindow?.scrollBy(0, scrollY);
          return { success: true };
        }
        case 'wait': {
          const ms = parseInt(action.value || '1000') || 1000;
          await new Promise(r => setTimeout(r, ms));
          return { success: true };
        }
        case 'extract': {
          if (!action.selector) return { success: false, error: '缺少selector' };
          doc.querySelector(action.selector);
          return { success: true };
        }
        default:
          return { success: false, error: `未知操作类型: ${action.type}` };
      }
    } catch (e) {
      return { success: false, error: String(e) };
    }
  }, []);

  // AI handles the instruction
  const handleAIInstruction = useCallback(async () => {
    if (!aiInstruction.trim() || aiLoading) return;
    setAiLoading(true);
    setActionLog([]);
    setShowLog(true);

    try {
      const context = await extractPageContext();
      if (!context) {
        setActionLog([{ action: { type: 'wait', description: '无法读取页面内容' }, status: 'error', error: '页面未加载或跨域限制' }]);
        return;
      }

      const actions = await onAIAction(context, aiInstruction);
      if (!actions || actions.length === 0) {
        setActionLog([{ action: { type: 'wait', description: 'AI未返回操作' }, status: 'error' }]);
        return;
      }

      // Execute actions sequentially
      const log: ActionLog[] = [];
      for (const action of actions) {
        log.push({ action, status: 'pending' });
        setActionLog([...log]);

        const result = await executeAction(action);
        log[log.length - 1]!.status = result.success ? 'success' : 'error';
        log[log.length - 1]!.error = result.error;
        setActionLog([...log]);

        // Wait between actions
        if (result.success && action.type !== 'wait') {
          await new Promise(r => setTimeout(r, 500));
        }
      }
    } finally {
      setAiLoading(false);
    }
  }, [aiInstruction, aiLoading, extractPageContext, onAIAction, executeAction]);

  // Keyboard shortcut
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k' && aiMode) {
        e.preventDefault();
        handleAIInstruction();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [aiMode, handleAIInstruction]);

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* ── Navigation bar ── */}
      <div className="flex items-center gap-1.5 px-2 py-1.5 border-b border-border bg-muted/30">
        <button onClick={() => iframeRef.current?.contentWindow?.history.back()}
          className="p-1.5 rounded hover:bg-muted/50 text-muted-foreground transition-colors" title="后退">
          <ChevronLeft className="w-3.5 h-3.5" />
        </button>
        <button onClick={() => iframeRef.current?.contentWindow?.history.forward()}
          className="p-1.5 rounded hover:bg-muted/50 text-muted-foreground transition-colors" title="前进">
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
        <button onClick={() => { iframeRef.current?.contentWindow?.location.reload(); setLoading(true); }}
          className="p-1.5 rounded hover:bg-muted/50 text-muted-foreground transition-colors" title="刷新">
          <RotateCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
        <button onClick={() => navigate(initialUrl || 'about:blank')}
          className="p-1.5 rounded hover:bg-muted/50 text-muted-foreground transition-colors" title="首页">
          <Home className="w-3.5 h-3.5" />
        </button>

        <div className="flex-1 flex items-center bg-[var(--color-background)] border border-border rounded px-2 py-1 gap-1.5">
          <Globe className="w-3 h-3 text-muted-foreground shrink-0" />
          <input
            value={inputUrl}
            onChange={e => setInputUrl(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && navigate(inputUrl)}
            placeholder="输入网址..."
            className="flex-1 bg-transparent text-xs text-foreground outline-none"
          />
        </div>

        <button onClick={() => navigate(inputUrl)}
          className="p-1.5 rounded bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
          <ArrowRight className="w-3.5 h-3.5" />
        </button>

        <div className="w-px h-4 bg-border mx-0.5" />

        <button onClick={() => setAiMode(!aiMode)}
          className={`flex items-center gap-1 px-2 py-1.5 rounded text-xs transition-colors ${aiMode ? 'bg-primary/20 text-primary' : 'hover:bg-muted/50 text-muted-foreground'}`}>
          <Sparkles className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">AI</span>
        </button>
      </div>

      {/* ── AI instruction bar ── */}
      {aiMode && (
        <div className="flex items-center gap-1.5 px-2 py-1.5 border-b border-border bg-primary/5">
          <Sparkles className="w-3.5 h-3.5 text-primary shrink-0" />
          <input
            value={aiInstruction}
            onChange={e => setAiInstruction(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleAIInstruction()}
            placeholder="告诉AI要做什么... (例：点击登录按钮、在搜索框输入Python)"
            className="flex-1 bg-transparent text-xs text-foreground outline-none"
            disabled={aiLoading}
          />
          <button onClick={handleAIInstruction} disabled={aiLoading || !aiInstruction.trim()}
            className="p-1.5 rounded bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors">
            {aiLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
          </button>
        </div>
      )}

      {/* ── Action log ── */}
      {showLog && actionLog.length > 0 && (
        <div className="border-b border-border bg-[var(--color-secondary)] max-h-[120px] overflow-auto">
          <div className="flex items-center justify-between px-2 py-1 border-b border-border/30">
            <span className="text-[10px] text-muted-foreground font-medium">AI操作日志</span>
            <button onClick={() => setShowLog(false)} className="p-0.5 rounded hover:bg-muted/30">
              <X className="w-3 h-3 text-muted-foreground" />
            </button>
          </div>
          {actionLog.map((log, i) => (
            <div key={i} className="flex items-center gap-2 px-2 py-1 text-xs">
              {log.status === 'pending' && <Loader2 className="w-3 h-3 animate-spin text-blue-400" />}
              {log.status === 'success' && <span className="w-3 h-3 text-green-400">✓</span>}
              {log.status === 'error' && <span className="w-3 h-3 text-red-400">✗</span>}
              <span className="text-muted-foreground">
                {log.action.type === 'click' && <MousePointer className="w-3 h-3 inline mr-1" />}
                {log.action.type === 'type' && <Type className="w-3 h-3 inline mr-1" />}
                {log.action.type === 'navigate' && <Globe className="w-3 h-3 inline mr-1" />}
                {log.action.description}
              </span>
              {log.error && <span className="text-red-400 text-[10px] ml-auto">{log.error}</span>}
            </div>
          ))}
        </div>
      )}

      {/* ⏺ 录制业务流程：录一段操作→沉淀为配方→SoulMate replay 重复执行 */}
      {isElectron && (
        <div className="flex items-center gap-2 border-t border-border px-3 py-1.5 text-xs">
          {!recording ? (
            <button
              onClick={startRecording}
              className="rounded-md px-2 py-1 hover:bg-primary/10 transition-colors"
              title="录制一段业务操作，沉淀为可回放流程"
            >
              ⏺ 录制流程
            </button>
          ) : (
            <>
              <span className="text-red-500 animate-pulse">⏺ 录制中（{recCount} 步）</span>
              <button
                onClick={stopRecording}
                className="ml-auto rounded-md bg-red-500/15 px-2.5 py-1 text-red-500 hover:bg-red-500/25 transition-colors"
              >
                停止
              </button>
            </>
          )}
          {showSaveBar && (
            <>
              <input
                value={flowName}
                onChange={(e) => setFlowName(e.target.value)}
                placeholder={`流程名称（已录 ${recCount} 步，如：录入订单）`}
                className="flex-1 rounded-md border border-border bg-transparent px-2 py-1 outline-none focus:border-primary"
              />
              <button
                onClick={saveFlow}
                disabled={!flowName.trim()}
                className="rounded-md bg-primary/10 px-2.5 py-1 text-primary hover:bg-primary/20 transition-colors disabled:opacity-40"
              >
                保存流程
              </button>
              <button
                onClick={() => { recStepsRef.current = []; setRecCount(0); setShowSaveBar(false); }}
                className="rounded-md px-2 py-1 hover:bg-primary/10 transition-colors"
              >
                丢弃
              </button>
            </>
          )}
        </div>
      )}

      {/* SoulMate 自动执行状态条：接管时冻结可见+一键交还 */}
      {isElectron && runnerState && (runnerState.paused || Date.now() - runnerState.at < 15000) && (
        <div className={`flex items-center gap-2 border-t border-border px-3 py-1.5 text-xs ${runnerState.paused ? 'bg-amber-500/10 text-amber-600' : 'bg-primary/10 text-primary'}`}>
          {runnerState.paused ? (
            <>
              <span>⏸ 已接管浏览器（SoulMate 已停手，随时说"继续"或点按钮）</span>
              <button
                onClick={() => (window as any).__omBrowserRunnerResume?.()}
                className="ml-auto rounded-md bg-amber-500/15 px-2.5 py-1 font-medium hover:bg-amber-500/25 transition-colors"
              >
                继续自动执行
              </button>
            </>
          ) : (
            <span>▶ SoulMate 自动执行中（{runnerState.action}）——你现在也能直接操作</span>
          )}
        </div>
      )}

      {/* ── Page content ── */}
      <div className="flex-1 relative">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-background/80 z-10">
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
          </div>
        )}
        {isElectron ? (
          <WebViewTag
            ref={webviewRef}
            src={url}
            allowpopups="true"
            className="w-full h-full border-0"
          />
        ) : (
          <iframe
            ref={iframeRef}
            src={url}
            className="w-full h-full border-0"
            title="Browser"
            sandbox="allow-same-origin allow-scripts allow-forms allow-popups"
            onLoad={() => setLoading(false)}
          />
        )}
      </div>
    </div>
  );
}
