// browser-task-runner — 内置浏览器自动化执行器（SoulMate ↔ OpenMate 应用的客户端侧）。
//
// 连 acp-proxy 的 /ws/browser 长连接领任务，在 Electron <webview>（workspace 浏览器
// 标签，真 Chromium 面板）里执行：真实键鼠（sendInputEvent）+ 页面本体 DOM（executeJavaScript）。
// 同一扇窗语义：用户随时手动接管（真实输入立刻生效）；接管检测=页面输入时间戳 > AI 动作
// 结束时刻 → 执行器暂停（⏸），直到 agent 调 resume（用户说"继续"）。
// 窗口折叠=webview 隐藏照跑，展开=用户实时观看（真浏览器画面，不是截图流）。

type Bridge = {
  webviewExec: (tabId: number, code: string) => Promise<{ ok: boolean; result?: any; error?: string }>;
  webviewInput: (tabId: number, event: any) => Promise<{ ok: boolean; error?: string }>;
  webviewNav: (tabId: number, url: string) => Promise<{ ok: boolean; error?: string }>;
  webviewCapture: (tabId: number) => Promise<{ ok: boolean; dataUrl?: string; error?: string }>;
};

const INJECT_INPUT_LOG = `(() => {
  if (window.__omInputHooked) return true;
  window.__omInputHooked = true;
  window.__omInputLog = [];
  ['mousedown','keydown','wheel'].forEach(function (t) {
    window.addEventListener(t, function () {
      window.__omInputLog.push(Date.now());
      if (window.__omInputLog.length > 30) window.__omInputLog.shift();
    }, { capture: true, passive: true });
  });
  return true;
})()`;

const SNAPSHOT_SCRIPT = `(() => {
  const links = [];
  let linksTotal = 0;
  document.querySelectorAll('a[href]').forEach(function (a) {
    linksTotal += 1;
    if (links.length < 40) {
      links.push({ text: (a.textContent || '').trim().slice(0, 60), href: a.href });
    }
  });
  const body = (document.body ? document.body.innerText : '');
  const text = body.slice(0, 6000);
  return {
    title: document.title || '', url: location.href,
    text: text, total_chars: body.length, truncated: body.length > text.length,
    links: links, links_total: linksTotal,
  };
})()`;

function bridge(): Bridge | null {
  const b = (window as any).openmateDesktop;
  return b?.webviewExec ? b : null;
}

function tabRegistry(): Record<number, any> {
  const g = window as any;
  g.__omWebviews = g.__omWebviews || {};
  return g.__omWebviews;
}

async function pickTab(b: Bridge, params: any): Promise<number | null> {
  const reg = tabRegistry();
  const ids = Object.keys(reg).map(Number);
  if (ids.length > 0) return ids[ids.length - 1]; // 最近注册的标签
  // 没有内置浏览器标签 → 开一个（workspace 落点，与外链新标签同款）
  const opener = (window as any).__omOpenWebTab;
  if (!opener) return null;
  opener(String(params?.url || 'about:blank'));
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 300));
    const ids2 = Object.keys(tabRegistry()).map(Number);
    if (ids2.length > 0) return ids2[ids2.length - 1];
  }
  return null;
}

async function execOn(b: Bridge, tabId: number, code: string): Promise<any> {
  const res = await b.webviewExec(tabId, code);
  if (!res?.ok) throw new Error(res?.error || 'webview 求值失败');
  return res.result;
}

async function pageTitleUrl(b: Bridge, tabId: number): Promise<{ title: string; url: string }> {
  const r = await execOn(b, tabId, '({ title: document.title || "", url: location.href })');
  return { title: r?.title || '', url: r?.url || '' };
}

async function waitSettled(b: Bridge, tabId: number, timeoutMs = 15000): Promise<void> {
  const start = Date.now();
  let last = '';
  while (Date.now() - start < timeoutMs) {
    try {
      const state = await execOn(b, tabId, 'document.readyState + "|" + location.href');
      if (state.startsWith('complete') && state === last) return;
      last = state;
    } catch { /* 过渡期求值失败=还在导航 */ }
    await new Promise((r) => setTimeout(r, 250));
  }
}

class TaskRunner {
  private ws: WebSocket | null = null;
  private paused = false;
  private lastActionEnd = 0;
  private seenHumanAt = 0;
  private reconnectTimer: any = null;

  /** 状态广播：浏览器标签的 UI 显示「▶ 自动执行中 / ⏸ 已接管」+ 继续按钮 */
  private notifyState(action: string, paused: boolean) {
    try {
      window.dispatchEvent(new CustomEvent('om-runner-state', {
        detail: { action, paused, at: Date.now() },
      }));
    } catch { /* 非浏览器环境 */ }
  }

  /** UI 的【继续自动执行】按钮：本地立即解除接管冻结 */
  resumeFromUI() {
    this.paused = false;
    this.seenHumanAt = 0;
    this.lastActionEnd = Date.now();
    this.notifyState('resume', false);
  }

  start() {
    if (!bridge() || this.ws) return;
    (window as any).__omBrowserRunnerResume = () => this.resumeFromUI();
    this.connect();
  }

  private connect() {
    const host = typeof location !== 'undefined' ? location.hostname : '127.0.0.1';
    let ws: WebSocket;
    try {
      ws = new WebSocket(`ws://${host}:8092/ws/browser`);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'hello', session_id: '' }));
    };
    ws.onmessage = (e) => {
      let msg: any = null;
      try { msg = JSON.parse(e.data); } catch { return; }
      if (msg?.type === 'task') this.runTask(msg);
    };
    ws.onclose = () => { this.ws = null; this.scheduleReconnect(); };
    ws.onerror = () => { try { ws.close(); } catch { /* 已关 */ } };
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 3000);
  }

  private send(payload: any) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  private async runTask(task: { task_id: string; action: string; params: any }) {
    const reply = (ok: boolean, result: any = {}, error = '') =>
      this.send({ type: 'result', task_id: task.task_id, ok, result, error });
    try {
      const b = bridge();
      if (!b) return reply(false, {}, '应用无内置浏览器桥（非 Electron 壳）');
      if (task.action === 'resume') {
        this.paused = false;
        const tabId = await pickTab(b, {});
        if (tabId) {
          await execOn(b, tabId, 'window.__omInputLog = []').catch(() => null);
          this.seenHumanAt = 0;
        }
        return reply(true, { note: '已恢复自动操作' });
      }
      const tabId = await pickTab(b, task.params);
      if (!tabId) return reply(false, {}, '内置浏览器标签未就绪（workspace 未打开浏览器标签）');
      // 接管检测：AI 动作结束后的真实输入时间戳 → 暂停
      await execOn(b, tabId, INJECT_INPUT_LOG).catch(() => null);
      const log: number[] = (await execOn(b, tabId, 'window.__omInputLog || []').catch(() => [])) || [];
      const humanAt = log.length ? Math.max(...log) : 0;
      if (humanAt > this.lastActionEnd && humanAt > this.seenHumanAt) {
        this.paused = true;
        this.seenHumanAt = humanAt;
        this.notifyState('takeover', true);
      }
      if (this.paused) {
        return reply(false, {}, '⏸ 用户已接管浏览器（正在手动操作）。停手等待，用户说"继续"后调 browser(action=resume) 再继续。');
      }
      this.notifyState(task.action, false);
      const result = await this.execute(b, tabId, task.action, task.params || {});
      this.lastActionEnd = Date.now();
      reply(true, result);
    } catch (e: any) {
      this.lastActionEnd = Date.now();
      reply(false, {}, String(e?.message || e));
    }
  }

  private async execute(b: Bridge, tabId: number, action: string, p: any): Promise<any> {
    switch (action) {
      case 'goto': {
        const url = String(p.url || '').trim();
        if (!url) throw new Error('缺少 url');
        await b.webviewNav(tabId, url);
        await waitSettled(b, tabId);
        return pageTitleUrl(b, tabId);
      }
      case 'snapshot':
        return execOn(b, tabId, SNAPSHOT_SCRIPT);
      case 'click': {
        let sel = String(p.selector || '').trim();
        if (!sel && p.text) {
          // 文本找元素（容错 agent 只给可见文字）
          sel = await execOn(b, tabId, `(() => {
            const t = ${JSON.stringify(String(p.text))};
            const els = Array.from(document.querySelectorAll('a,button,[role="button"],input[type="submit"]'));
            const el = els.find(function (e) { return (e.textContent || '').trim().includes(t) || (e.value || '') === t; });
            if (!el) return '';
            el.id = el.id || '__om_click_' + Math.random().toString(36).slice(2, 8);
            return '#' + el.id;
          })()`);
        }
        if (!sel) throw new Error('缺少 selector（或 text 未匹配到元素）');
        const pt = await execOn(b, tabId, `(() => {
          const el = document.querySelector(${JSON.stringify(sel)});
          if (!el) return null;
          el.scrollIntoView({ block: 'center' });
          const r = el.getBoundingClientRect();
          return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
        })()`);
        if (!pt) throw new Error('未找到元素: ' + sel);
        await b.webviewInput(tabId, { type: 'mouseDown', x: pt.x, y: pt.y, button: 'left', clickCount: 1 });
        await b.webviewInput(tabId, { type: 'mouseUp', x: pt.x, y: pt.y, button: 'left', clickCount: 1 });
        await new Promise((r) => setTimeout(r, 400));
        await execOn(b, tabId, INJECT_INPUT_LOG).catch(() => null);
        const pu = await pageTitleUrl(b, tabId);
        return { clicked: sel, ...pu };
      }
      case 'type': {
        const sel = String(p.selector || '').trim();
        const text = String(p.text ?? p.value ?? '');
        if (!sel || !text) throw new Error('缺少 selector 或 text');
        const ok = await execOn(b, tabId, `!!document.querySelector(${JSON.stringify(sel)})?.focus()`);
        if (ok === undefined || ok === null) throw new Error('未找到输入框: ' + sel);
        await execOn(b, tabId, `document.querySelector(${JSON.stringify(sel)})?.focus()`);
        await b.webviewInput(tabId, { type: 'char', keyCode: text });
        const submit = !!(p.submit || p.press_enter);
        if (submit) {
          await b.webviewInput(tabId, { type: 'keyDown', keyCode: 'Enter' });
          await b.webviewInput(tabId, { type: 'keyUp', keyCode: 'Enter' });
          await waitSettled(b, tabId, 8000).catch(() => null);
        }
        await execOn(b, tabId, INJECT_INPUT_LOG).catch(() => null);
        const pu = await pageTitleUrl(b, tabId);
        return { chars: text.length, typed_into: sel, submitted: submit, ...pu };
      }
      case 'fill': {
        const sel = String(p.selector || '').trim();
        const text = String(p.text ?? p.value ?? '');
        if (!sel || !text) throw new Error('缺少 selector 或 text');
        await execOn(b, tabId, `(() => {
          const el = document.querySelector(${JSON.stringify(sel)});
          if (!el) return false;
          el.focus();
          el.value = ${JSON.stringify(text)};
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          return true;
        })()`);
        await execOn(b, tabId, INJECT_INPUT_LOG).catch(() => null);
        const pu = await pageTitleUrl(b, tabId);
        return { chars: text.length, typed_into: sel, submitted: false, ...pu };
      }
      case 'eval': {
        const script = String(p.script || p.code || '');
        if (!script) throw new Error('缺少 script');
        const raw = await execOn(b, tabId, script);
        const s = typeof raw === 'string' ? raw : JSON.stringify(raw ?? null);
        const out = s.slice(0, 6000);
        return { result: out, total_chars: s.length, truncated: s.length > out.length };
      }
      case 'screenshot': {
        const res = await b.webviewCapture(tabId);
        if (!res?.ok || !res.dataUrl) throw new Error(res?.error || '截图失败');
        return { data_url: res.dataUrl };
      }
      case 'back': {
        await execOn(b, tabId, 'history.back()');
        await waitSettled(b, tabId, 8000).catch(() => null);
        return pageTitleUrl(b, tabId);
      }
      case 'close':
        throw new Error('close 在内置浏览器未启用（标签由 workspace 管理，可直接关闭标签页）');
      default:
        throw new Error('未知 action: ' + action);
    }
  }
}

let runner: TaskRunner | null = null;

/** app-shell 在桌面壳（有 webview 桥）启动时调用一次。 */
export function startBrowserTaskRunner() {
  if (runner) return;
  if (!(window as any).openmateDesktop?.webviewExec) return;
  runner = new TaskRunner();
  runner.start();
}
