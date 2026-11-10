// behavior-sensor — 全端行为感知采集器（Sense 行为感知 · OpenMate 客户端侧）。
//
// 用户定调：「不仅仅是内置浏览器，而是整个 openmate 都能感知」「感知是全方位的」。
// 全方位覆盖：路由导航/点击/输入/滚动/停留/表单提交/复制——全局 window 级监听，
// 一个采集器盖住所有页面（含对话页，但只加壳层监听，不动对话页/AI群组页实现）。
//
// 脱敏在采集点执行（写入链路脱敏——秘密根本不进管道）：
// - 密码/密钥类字段零记录（无值无长度）
// - 输入值仅白名单字段（搜索类）采集，其余只记「字段+长度」
// - 复制只记字数；URL/路由去掉 query（常带 token）
//
// 数据流：事件 → window.__omBehaviorOutbox（浏览器内页事件由 browser-task-runner
// 汇入同一 outbox，source=browser）→ 每 5s 批量 POST /api/sense/behavior/events
// → OpenSense 行为感知（trajectory_events 归位）。

type BehaviorEvent = {
  ts: number;
  type: 'nav' | 'click' | 'input' | 'scroll' | 'dwell' | 'submit' | 'copy' | 'hover';
  source: 'app' | 'browser';
  url: string;
  target: string;
  value?: string;
  length?: number;
  scroll_y?: number;
  seconds?: number;
  title?: string;
  detail?: string;
  tab_id?: string;
  x?: number;
  y?: number;
};

const SHIP_INTERVAL_MS = 5000;
const OUTBOX_MAX = 200;
const INPUT_DEBOUNCE_MS = 1200;

// 采集点脱敏判法（与服务端 behavior.py 同源规则；服务端为最终闸）
const VALUE_OK = /search|query|keyword|^q$|搜索|查找/i;
const SECRET_FIELD = /password|passwd|pwd|secret|token|otp|captcha|验证码|密码|密钥|口令/i;

function safeUrl(u: string): string {
  try {
    const p = new URL(u, window.location.origin);
    return `${p.origin}${p.pathname}`;
  } catch {
    return '';
  }
}

function clip(s: string, n = 80): string {
  return String(s || '').replace(/\s+/g, ' ').trim().slice(0, n);
}

function describe(el: Element | null): string {
  if (!el) return '';
  const e = el as HTMLElement;
  const tag = e.tagName?.toLowerCase() || '';
  const text = clip(e.innerText || (e as HTMLInputElement).value ? '' : '', 24) ||
    clip((e as HTMLInputElement).placeholder || '', 24) ||
    clip(e.getAttribute?.('aria-label') || '', 24) ||
    clip(e.id || (e as HTMLInputElement).name || '', 24);
  return text ? `${tag}「${text}」` : tag;
}

export function pushBehaviorEvent(ev: Partial<BehaviorEvent> & { type: BehaviorEvent['type'] }): void {
  if (typeof window === 'undefined') return;
  const g = window as any;
  g.__omBehaviorOutbox = g.__omBehaviorOutbox || [];
  const box: any[] = g.__omBehaviorOutbox;
  box.push({ ts: Date.now(), source: 'app', url: safeUrl(location.href), ...ev });
  if (box.length > OUTBOX_MAX) box.splice(0, box.length - OUTBOX_MAX);
}

async function ship(): Promise<void> {
  const g = window as any;
  const box: any[] = g.__omBehaviorOutbox || [];
  if (box.length === 0) return;
  const batch = box.splice(0, box.length);
  try {
    const { getApiBaseUrl } = await import('./api-client');
    await fetch(`${getApiBaseUrl()}/api/sense/behavior/events`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ events: batch, session_id: '' }),
      keepalive: true,
    });
  } catch {
    // 发送失败回填 outbox（丢一半=感知断流；cap 自然限高）
    g.__omBehaviorOutbox = batch.concat(g.__omBehaviorOutbox || []).slice(-OUTBOX_MAX);
  }
}

export function installBehaviorSensor(): void {
  if (typeof window === 'undefined') return;
  const g = window as any;
  if (g.__omBehaviorSensor) return;
  g.__omBehaviorSensor = true;

  // ── 点击（capture 全局，含所有页面）──────────────────────
  window.addEventListener('mousedown', (e) => {
    pushBehaviorEvent({ type: 'click', target: describe(e.target as Element) });
  }, { capture: true, passive: true });

  // ── 输入（按元素防抖 1.2s；采集点脱敏）───────────────────
  const pending = new WeakMap<Element, number>();
  window.addEventListener('input', (e) => {
    const el = e.target as HTMLInputElement;
    if (!el || !el.tagName) return;
    const desc = describe(el);
    if (SECRET_FIELD.test(desc)) {
      pushBehaviorEvent({ type: 'input', target: desc }); // 零记录：无值无长度
      return;
    }
    const fire = () => {
      const value = String(el.value || '');
      const rec: any = { type: 'input', target: desc, length: value.length };
      if (value && VALUE_OK.test(desc)) rec.value = value; // 白名单才记内容
      pushBehaviorEvent(rec);
    };
    const prev = pending.get(el);
    if (prev) window.clearTimeout(prev);
    pending.set(el, window.setTimeout(fire, INPUT_DEBOUNCE_MS) as unknown as number);
  }, { capture: true, passive: true });

  // ── 悬停（指针上下文：元素级 hover 事件——「这个地方」的解析依据）──
  // 鼠标停在某元素 >400ms 记一条（mousemove 海量，只记元素切换后的落定）
  let hoverTimer: number | undefined;
  let lastHoverEl: Element | null = null;
  window.addEventListener('mousemove', (e) => {
    const el = document.elementFromPoint(e.clientX, e.clientY);
    if (el === lastHoverEl) return;
    lastHoverEl = el;
    if (hoverTimer) window.clearTimeout(hoverTimer);
    const x = Math.round(e.clientX), y = Math.round(e.clientY);
    hoverTimer = window.setTimeout(() => {
      pushBehaviorEvent({
        type: 'hover', target: describe(el), x, y,
        detail: el ? `${(el as HTMLElement).tagName?.toLowerCase() || ''}` : '',
      });
    }, 400) as unknown as number;
  }, { capture: true, passive: true });

  // ── 提交 / 复制 ─────────────────────────────────────────
  window.addEventListener('submit', () => {
    pushBehaviorEvent({ type: 'submit', target: 'form' });
  }, { capture: true, passive: true });
  window.addEventListener('copy', () => {
    const sel = String(window.getSelection?.() || '');
    pushBehaviorEvent({ type: 'copy', length: sel.length }); // 只记字数
  }, { capture: true, passive: true });

  // ── 滚动（2s 节流）──────────────────────────────────────
  let lastScroll = 0;
  window.addEventListener('scroll', () => {
    const now = Date.now();
    if (now - lastScroll < 2000) return;
    lastScroll = now;
    pushBehaviorEvent({ type: 'scroll', scroll_y: Math.round(window.scrollY) });
  }, { capture: true, passive: true });

  // ── 路由导航（pushState/replaceState 补丁 + popstate）────
  const recordNav = () => pushBehaviorEvent({
    type: 'nav', url: safeUrl(location.href), title: clip(document.title, 60),
  });
  recordNav();
  const wrap = (fn: any) => function (this: History, ...args: any[]) {
    const r = fn.apply(this, args);
    recordNav();
    return r;
  };
  history.pushState = wrap(history.pushState);
  history.replaceState = wrap(history.replaceState);
  window.addEventListener('popstate', recordNav);

  // ── 停留（页面隐藏时结算）────────────────────────────────
  let enterAt = Date.now();
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      const seconds = Math.round((Date.now() - enterAt) / 1000);
      if (seconds >= 3) pushBehaviorEvent({ type: 'dwell', seconds });
    } else {
      enterAt = Date.now();
    }
  });

  // ── 批量上行 ─────────────────────────────────────────────
  window.setInterval(ship, SHIP_INTERVAL_MS);
  window.addEventListener('beforeunload', () => { void ship(); });
  // 用户按 Enter 发消息=即将引用"这个地方"：立即上行悬停上下文（消 5s 批次时差）
  window.addEventListener('keydown', (e) => {
    if ((e as KeyboardEvent).key === 'Enter') {
      void (async () => {
        await (window as any).__omBrowserBehaviorFlush?.(); // 浏览器子流先清
        void ship();
      })();
    }
  }, { capture: true, passive: true });
}
