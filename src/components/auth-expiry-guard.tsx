'use client';

// 全局 401/token 过期拦截 —— 挂 (app) 布局内，覆盖全站 fetch。
//
// 行为（「登录已过期，重新登录」接管，绝不甩 JWT 报错原文）：
// - 任何 API 回 401 且证据是 token 失效/过期（结构化 code AUTH_TOKEN_* 或
//   JWT 库报错原文）→ 友好弹层 + 清理失效 token + 跳登录并保原路径；
// - 回包统一改写成友好文案（{detail: 友好串}），调用方即便直接展示
//   data.detail 也不会见到「invalid token: Signature has expired.」原文；
// - 无 refresh 机制（登录即签 24h JWT，后端无 /refresh 端点），故走接管
//   引导而非自动续期；将来若加 refresh，替换 beginTakeover 内的跳转即可。
//
// 严格判据（防误杀）：
// - 仅当请求真的带了「当前用户 token」（openmate-token）才接管——企业侧
//   enterprise-token 失效不牵连用户会话；
// - 登录/注册/验证码入口的 401 是「凭据错误」业务回包，不接管；
// - 无 token 时仅归一文案，不动登录态。

import { useEffect } from 'react';
import { useToast } from '@/components/toast-provider';
import { getToken, clearUser } from '@/lib/api-client';
import {
  friendlyAuthMessage,
  isTokenFailure,
  isAuthEntryUrl,
  rememberLoginNext,
  AUTH_INVALID_MESSAGE,
} from '@/lib/auth-errors';

let takeoverInFlight = false;

function sentBearer(input: RequestInfo | URL, init?: RequestInit): string {
  try {
    const fromInit = new Headers(init?.headers).get('authorization') || '';
    if (fromInit) return fromInit;
    if (typeof Request !== 'undefined' && input instanceof Request) {
      return input.headers.get('authorization') || '';
    }
  } catch {
    /* header 不可读则视为未带 token */
  }
  return '';
}

function requestUrl(input: RequestInfo | URL): string {
  try {
    if (typeof input === 'string') return input;
    if (input instanceof URL) return input.toString();
    return input.url;
  } catch {
    return '';
  }
}

function friendlyResponse(friendly: string, code: string | null): Response {
  return new Response(
    JSON.stringify({ detail: friendly, ...(code ? { code } : {}) }),
    { status: 401, statusText: 'Unauthorized', headers: { 'Content-Type': 'application/json' } },
  );
}

export function AuthExpiryGuard() {
  const toast = useToast();

  useEffect(() => {
    const w = window as unknown as { fetch: typeof fetch; __authExpiryGuard?: boolean };
    if (w.__authExpiryGuard) return;
    w.__authExpiryGuard = true;
    const originalFetch = w.fetch.bind(window);

    const guardedFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const res = await originalFetch(input, init);
      try {
        if (res.status !== 401) return res;
        const url = requestUrl(input);
        if (isAuthEntryUrl(url)) return res;

        let body: unknown = null;
        try {
          body = await res.clone().json();
        } catch {
          body = null;
        }
        const detail =
          body && typeof body === 'object'
            ? ((body as Record<string, unknown>).detail ?? (body as Record<string, unknown>).message ?? null)
            : null;
        if (!isTokenFailure(detail)) return res; // 非 token 失效的 401：不动

        const code =
          detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).code === 'string'
            ? ((detail as Record<string, unknown>).code as string)
            : null;
        const friendly = friendlyAuthMessage(detail, AUTH_INVALID_MESSAGE);

        // 仅当本次请求带的是「当前用户 token」才接管用户会话（企业 token 不牵连）
        const myToken = getToken();
        const bearer = sentBearer(input, init);
        const isUserToken = !!myToken && bearer === `Bearer ${myToken}`;

        if (isUserToken && !takeoverInFlight) {
          takeoverInFlight = true;
          clearUser(); // 失效 token 立即清除，禁止继续使用
          rememberLoginNext(); // 保原路径
          toast.warning(friendly, '正在跳转登录页，登录后回到当前页面');
          setTimeout(() => {
            window.location.href = '/login';
          }, 800);
        }

        // 改写回包：任何调用方拿到的 detail 恒为友好文案
        return friendlyResponse(friendly, code);
      } catch {
        return res;
      }
    };

    w.fetch = guardedFetch as unknown as typeof fetch;
    // 全站生命周期常驻；组件卸载不回滚（同 global-websocket 先例）
  }, [toast]);

  return null;
}
