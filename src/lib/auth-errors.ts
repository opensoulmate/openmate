// 鉴权错误归一 + token 过期接管 —— 任何 API 鉴权失败都不向用户甩 JWT 报错原文。
//
// 线上事故：创建���作流时 PyJWT 过期原文「invalid token: Signature has expired.」
// 经 res.json().detail 直出 toast。本模块是前端单点收口：
// - 后端（opensoul tenant_binding）已按「错误归一」纪律返回结构化 code
//   （AUTH_TOKEN_EXPIRED / AUTH_TOKEN_INVALID / AUTH_TOKEN_IDENTITY）+ 友好文案；
//   前端按 code 决策，不解析文案。
// - 兜底：老回包/其他路径里残留的 JWT 库报错原文（PyJWT/jose）一律归一成
//   友好引导，绝不到达任何展示层。

export const AUTH_TOKEN_EXPIRED = 'AUTH_TOKEN_EXPIRED';
export const AUTH_TOKEN_INVALID = 'AUTH_TOKEN_INVALID';
export const AUTH_TOKEN_IDENTITY = 'AUTH_TOKEN_IDENTITY';

export const AUTH_EXPIRED_MESSAGE = '登录已过期，请重新登录';
export const AUTH_INVALID_MESSAGE = '登录状态无效，请重新登录';
export const AUTH_IDENTITY_MESSAGE = '登录账号不存在或已停用，请重新登录';

const CODE_MESSAGES: Record<string, string> = {
  [AUTH_TOKEN_EXPIRED]: AUTH_EXPIRED_MESSAGE,
  [AUTH_TOKEN_INVALID]: AUTH_INVALID_MESSAGE,
  [AUTH_TOKEN_IDENTITY]: AUTH_IDENTITY_MESSAGE,
};

// JWT 库报错原文特征（PyJWT / jose / 通用 token 报错）：命中即归一
const RAW_JWT_PATTERNS: RegExp[] = [
  /signature has expired/i,
  /expired signature/i,
  /invalid (or expired )?token/i,
  /token (is |has )?(expired|invalid|malformed)/i,
  /malformed token/i,
  /not enough segments/i,
  /invalid (header|signature|string)/i,
  /jwt/i,
];

// 登录/注册/验证码等鉴权入口自身：401 是「凭据错误」的正常业务回包，不接管
const AUTH_ENTRY_RE = /\/api\/(user\/(login|register|verify-email|resend[^/]*|oauth\/)|enterprise\/auth\/)/;

/** 提取结构化 code（detail 为 {code,message} 形或平铺 body.code）。 */
export function authCodeOf(detail: unknown): string | null {
  if (detail && typeof detail === 'object') {
    const code = (detail as Record<string, unknown>).code;
    if (typeof code === 'string' && code.startsWith('AUTH_TOKEN')) return code;
  }
  return null;
}

function rawJwtText(text: string): boolean {
  return RAW_JWT_PATTERNS.some((re) => re.test(text));
}

/**
 * 鉴权失败（或其他错误）的友好展示文案。
 * - 结构化 code → 对应友好文案；
 * - JWT 库报错原文 → 归一（过期→「登录已过期」，其余→「登录状态无效」）；
 * - 普通业务错误（如「Entity not found」）→ 原样透传（说真因）。
 */
export function friendlyAuthMessage(detail: unknown, fallback = ''): string {
  if (detail == null) return fallback;
  if (typeof detail === 'object') {
    const rec = detail as Record<string, unknown>;
    const code = typeof rec.code === 'string' ? rec.code : '';
    if (code && CODE_MESSAGES[code]) return CODE_MESSAGES[code];
    const msg = typeof rec.message === 'string' ? rec.message : '';
    if (msg) return rawJwtText(msg) ? AUTH_INVALID_MESSAGE : msg;
    return fallback;
  }
  const text = String(detail);
  if (!text) return fallback;
  if (rawJwtText(text)) {
    return /expired/i.test(text) ? AUTH_EXPIRED_MESSAGE : AUTH_INVALID_MESSAGE;
  }
  return text;
}

/** 是否「token 失效/过期」类鉴权失败（接管判据，按 code/原文证据判定）。 */
export function isTokenFailure(detail: unknown): boolean {
  const code = authCodeOf(detail);
  if (code) return true;
  if (detail && typeof detail === 'object') {
    const msg = (detail as Record<string, unknown>).message;
    return typeof msg === 'string' && rawJwtText(msg);
  }
  return typeof detail === 'string' && rawJwtText(detail);
}

/** 该 URL 的 401 是登录入口的业务回包（凭据错误），不做过期接管。 */
export function isAuthEntryUrl(url: string): boolean {
  return AUTH_ENTRY_RE.test(url);
}

// ── 跳登录保原路径：401 接管前记住当前路径，登录成功后回跳 ──────────

const LOGIN_NEXT_KEY = 'openmate-login-next';

export function rememberLoginNext(): void {
  if (typeof window === 'undefined') return;
  const next = window.location.pathname + window.location.search;
  if (next && next !== '/login') {
    window.localStorage.setItem(LOGIN_NEXT_KEY, next);
  }
}

/** 取出并清除保存的回跳路径（无则 /chat，与既有登录落点一致）。 */
export function consumeLoginNext(): string {
  if (typeof window === 'undefined') return '/chat';
  const next = window.localStorage.getItem(LOGIN_NEXT_KEY);
  window.localStorage.removeItem(LOGIN_NEXT_KEY);
  // 只允许站内相对路径，防 open redirect
  if (next && next.startsWith('/') && !next.startsWith('//')) return next;
  return '/chat';
}
