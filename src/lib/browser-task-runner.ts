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

const INJECT_BEHAVIOR_LOG = `(() => {
  if (window.__omInputHooked) return true;
  window.__omInputHooked = true;
  window.__omInputLog = [];
  window.__omBehaviorLog = [];
  function push(ev) {
    ev.ts = Date.now();
    window.__omBehaviorLog.push(ev);
    if (window.__omBehaviorLog.length > 100) window.__omBehaviorLog.shift();
  }
  function describe(el) {
    if (!el) return '';
    var tag = (el.tagName || '').toLowerCase();
    var text = ((el.innerText || '') + '').trim().replace(/\\s+/g, ' ').slice(0, 24)
      || ((el.placeholder || '') + '').slice(0, 24)
      || ((el.getAttribute && el.getAttribute('aria-label')) || '').slice(0, 24)
      || ((el.id || el.name || '') + '').slice(0, 24);
    return text ? tag + '「' + text + '」' : tag;
  }
  ['mousedown','keydown','wheel'].forEach(function (t) {
    window.addEventListener(t, function () {
      window.__omInputLog.push(Date.now());
      if (window.__omInputLog.length > 30) window.__omInputLog.shift();
    }, { capture: true, passive: true });
  });
  window.addEventListener('mousedown', function (e) {
    push({ type: 'click', target: describe(e.target), url: location.href });
    recStep({ action: 'click', selector: cssPath(e.target), text: describe(e.target) });
  }, { capture: true, passive: true });
  var hoverTimer = null, lastHoverEl = null;
  window.addEventListener('mousemove', function (e) {
    var el = document.elementFromPoint(e.clientX, e.clientY);
    if (el === lastHoverEl) return;
    lastHoverEl = el;
    if (hoverTimer) clearTimeout(hoverTimer);
    var x = Math.round(e.clientX), y = Math.round(e.clientY);
    hoverTimer = setTimeout(function () {
      push({ type: 'hover', target: describe(el), x: x, y: y, url: location.href });
    }, 400);
  }, { capture: true, passive: true });
  var SECRET = /password|passwd|pwd|secret|token|otp|captcha|验证码|密码|密钥|口令/i;
  var VALUE_OK = /search|query|keyword|^q$|搜索|查找/i;
  var timers = new WeakMap();
  window.addEventListener('input', function (e) {
    var el = e.target;
    if (!el || !el.tagName) return;
    var desc = describe(el);
    if (SECRET.test(desc)) { push({ type: 'input', target: desc, url: location.href }); return; }
    var fire = function () {
      var v = ((el.value || '') + '');
      var rec = { type: 'input', target: desc, url: location.href, length: v.length };
      if (v && VALUE_OK.test(desc)) rec.value = v;
      push(rec);
      var isSecret = SECRET.test(desc);
      recStep({ action: 'fill', selector: cssPath(el), text: describe(el),
                value: isSecret ? '{{password}}' : v, secret: isSecret });
    };
    var prev = timers.get(el);
    if (prev) clearTimeout(prev);
    timers.set(el, setTimeout(fire, 1200));
  }, { capture: true, passive: true });
  window.addEventListener('submit', function () {
    push({ type: 'submit', target: 'form', url: location.href });
  }, { capture: true, passive: true });
  window.addEventListener('copy', function () {
    var s = (window.getSelection ? String(window.getSelection()) : '');
    push({ type: 'copy', length: s.length, url: location.href });
  }, { capture: true, passive: true });
  var lastScroll = 0;
  window.addEventListener('scroll', function () {
    var now = Date.now();
    if (now - lastScroll < 2000) return;
    lastScroll = now;
    push({ type: 'scroll', scroll_y: Math.round(window.scrollY), url: location.href });
  }, { capture: true, passive: true });
  // ── 录制（显式开启才捕获输入值：录制=录教学；密码字段参数化 {{password}} 永不落盘）──
  // sessionStorage 承载步骤=跨同源页面导航不丢（业务流程多为同源多页表单）。
  function cssPath(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + el.id;
    var path = [];
    while (el && el.nodeType === 1 && path.length < 5) {
      if (el.id) { path.unshift('#' + el.id); break; }
      var seg = el.tagName.toLowerCase();
      var p = el.parentNode;
      if (p && p.children) {
        var i = 1;
        for (var k = 0; k < p.children.length; k++) { if (p.children[k] === el) { i = k + 1; break; } }
        seg += ':nth-child(' + i + ')';
      }
      path.unshift(seg);
      el = el.parentElement;
    }
    return path.join(' > ');
  }
  function recOn() { try { return sessionStorage.getItem('__omRecOn') === '1'; } catch (e) { return false; } }
  function recStep(step) {
    if (!recOn()) return;
    step.ts = Date.now();
    try {
      var all = JSON.parse(sessionStorage.getItem('__omSteps') || '[]');
      all.push(step);
      sessionStorage.setItem('__omSteps', JSON.stringify(all));
    } catch (e) { /* 隐私模式/存储满 */ }
  }
  window.__omStartRecording = function () {
    try {
      sessionStorage.setItem('__omRecOn', '1');
      if (!sessionStorage.getItem('__omSteps')) sessionStorage.setItem('__omSteps', '[]');
    } catch (e) {}
    return true;
  };
  window.__omTakeSteps = function () {
    try {
      var all = JSON.parse(sessionStorage.getItem('__omSteps') || '[]');
      sessionStorage.setItem('__omSteps', '[]');
      return all;
    } catch (e) { return []; }
  };
  window.__omStopRecording = function () {
    var left = window.__omTakeSteps();
    try { sessionStorage.removeItem('__omRecOn'); } catch (e) {}
    return left;
  };
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

/** 活 webview 定位：逐个探针 webContents 活性，顺手清掉失活注册（webview 卸载时
 *  清理抛错会留幽灵条目 → pickTab 选到死 tab 的根因）。最近注册的优先。 */
async function aliveTabId(b: Bridge): Promise<number | null> {
  const reg = tabRegistry();
  const ids = Object.keys(reg).map(Number);
  for (let i = ids.length - 1; i >= 0; i--) {
    const id = ids[i];
    const r = await b.webviewExec(id, '1 + 1').catch(() => null);
    if (r?.ok) return id;
    delete reg[id]; // webContents 已销毁/未就绪 → 清幽灵注册
  }
  return null;
}

/** 开浏览器标签并等 webview 注册就绪（有界 ~6s，禁无限等）。
 *  与「打开浏览器」按钮/外链新标签同一条代码路径（__omOpenWebTab → workspace web-browser 标签）。 */
async function openWebTabAndWait(b: Bridge, url?: string): Promise<number | null> {
  const opener = (window as any).__omOpenWebTab;
  if (!opener) return null;
  opener(String(url || 'about:blank'));
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 300));
    const id = await aliveTabId(b);
    if (id) return id;
  }
  return null;
}

async function pickTab(b: Bridge, params: any): Promise<number | null> {
  const alive = await aliveTabId(b);
  if (alive) return alive;
  // 没有活的内置浏览器标签（tab 被关/切走卸载）→ 自动开一个（workspace 落点，与外链新标签同款）
  return openWebTabAndWait(b, params?.url);
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
    // 录制控制口（浏览器标签的 ⏺ 录制按钮经它驱动页内步骤捕获）
    (window as any).__omRecordingCtl = {
      start: async (tabId: number) => {
        const b = bridge(); if (!b || !tabId) return false;
        await execOn(b, tabId, '(() => { if (!window.__omBehaviorLog) { ' + INJECT_BEHAVIOR_LOG + ' } return window.__omStartRecording(); })()');
        return true;
      },
      take: async (tabId: number) => {
        const b = bridge(); if (!b || !tabId) return [];
        return (await execOn(b, tabId, '(() => { if (!window.__omBehaviorLog) { ' + INJECT_BEHAVIOR_LOG + ' } return window.__omTakeSteps(); })()')) || [];
      },
      stop: async (tabId: number) => {
        const b = bridge(); if (!b || !tabId) return [];
        return (await execOn(b, tabId, 'window.__omStopRecording ? window.__omStopRecording() : []')) || [];
      },
    };
    this.connect();
    this.startBehaviorPolling();
  }

  /** 全端行为感知的浏览器子流：每 4s 把各 webview 页面内的语义事件汇入 outbox
   *  （由 behavior-sensor 的批量上行统一送往 OpenSense，source=browser）。
   *  __omBrowserBehaviorFlush：用户发话（Enter）时立即清流，消轮询时差。 */
  private async flushBrowserBehavior() {
    const b = bridge();
    if (!b) return;
    const reg = tabRegistry();
    for (const idStr of Object.keys(reg)) {
      try {
        const evs: any[] = await execOn(
          b,
          Number(idStr),
          '(() => { if (!window.__omBehaviorLog) { ' + INJECT_BEHAVIOR_LOG + ' } return (window.__omBehaviorLog = window.__omBehaviorLog || []).splice(0, 100); })()',
        );
        if (Array.isArray(evs) && evs.length) {
          const g = window as any;
          g.__omBehaviorOutbox = g.__omBehaviorOutbox || [];
          for (const ev of evs) g.__omBehaviorOutbox.push({ ...ev, source: 'browser' });
        }
      } catch { /* webview 过渡期（导航中）求值失败=下一拍再取 */ }
    }
  }

  private startBehaviorPolling() {
    (window as any).__omBrowserBehaviorFlush = () => this.flushBrowserBehavior();
    const timer = window.setInterval(() => this.flushBrowserBehavior(), 4000);
    (window as any).__omBehaviorPollTimer = timer;
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
      else if (msg?.type === 'control' && msg?.cmd === 'ensure-webview') this.ensureWebview(msg);
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

  /** 控制消息 ensure-webview（webview 自救）：无活浏览器标签（tab 被关/切走后 webview
   *  卸载）→ 自动创建/切到浏览器标签（复用「打开浏览器」同条代码路径），webview 就绪回
   *  webview-ready。等待有界（~6s），超时如实报因——bridge 侧同样有界，禁无限等。 */
  private async ensureWebview(msg: { request_id?: string; url?: string }) {
    const rid = String(msg?.request_id || '');
    const reply = (ok: boolean, error = '') =>
      this.send({ type: 'control', cmd: 'webview-ready', request_id: rid, ok, error });
    try {
      const b = bridge();
      if (!b) return reply(false, '应用无内置浏览器桥（非 Electron 壳）');
      const alive = await aliveTabId(b);
      if (alive) return reply(true);
      const id = await openWebTabAndWait(b, msg?.url);
      if (id) return reply(true);
      reply(false, 'webview-ready 超时（有界等待）：浏览器标签未就绪（workspace 未创建浏览器标签）');
    } catch (e: any) {
      reply(false, String(e?.message || e));
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
      await execOn(b, tabId, INJECT_BEHAVIOR_LOG).catch(() => null);
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
        await execOn(b, tabId, INJECT_BEHAVIOR_LOG).catch(() => null);
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
        await execOn(b, tabId, INJECT_BEHAVIOR_LOG).catch(() => null);
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
        await execOn(b, tabId, INJECT_BEHAVIOR_LOG).catch(() => null);
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
