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

  // Extract page context from iframe
  const extractPageContext = useCallback(async (): Promise<PageContext | null> => {
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

      {/* ── Page content ── */}
      <div className="flex-1 relative">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-background/80 z-10">
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
          </div>
        )}
        <iframe
          ref={iframeRef}
          src={url}
          className="w-full h-full border-0"
          title="Browser"
          sandbox="allow-same-origin allow-scripts allow-forms allow-popups"
          onLoad={() => setLoading(false)}
        />
      </div>
    </div>
  );
}
