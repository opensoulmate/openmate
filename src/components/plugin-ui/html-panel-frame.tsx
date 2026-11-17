"use client";

/**
 * 层② HTML 面板 iframe 壳（Sprint C step ②）— docs/plugin-system-design.md §3
 *
 * - 载体：iframe，`sandbox="allow-scripts"`（同源受限，不给 allow-same-origin
 *   之外能力），内容来自插件 `html/` 只读静态服务（GET /plugins/{id}/html/{file}）。
 * - 通信：postMessage RPC 桥——iframe 内 `{id, method, params}` → 宿主 rpc 回调
 *   （POST /html-rpc，**与 DSL 面同一审批闸**）→ `{id, result|error}` 回帖给 iframe。
 *   只接受本 iframe contentWindow 的消息（防其它帧冒充）。
 * - 设计令牌：加载后 `postMessage('theme', tokens)` 注入 CSS 变量（宿主皮肤一致）。
 */

import { useCallback, useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

/** iframe → 宿主 RPC 请求形状（与宿主 /html-rpc 契约一致） */
export interface PanelRpcRequest {
  id: string | number;
  method: string;
  params?: Record<string, unknown>;
}

/** 宿主 RPC 通道：(method, params, approved) → result | error（与 ToolCall 同族） */
export type PanelRpcCall = (
  method: string,
  params: Record<string, unknown>,
  approved?: boolean
) => Promise<{ result?: Record<string, unknown>; error?: { code: string; message: string } }>;

/**
 * 纯函数：解析 iframe postMessage 消息 → RPC 请求（非 RPC 消息返回 null）。
 * 消息纪律：只认形状完整的 {id, method, params?}，其余（theme/未知）不进桥。
 */
export function parsePanelRpc(data: unknown): PanelRpcRequest | null {
  if (!data || typeof data !== "object") return null;
  const msg = data as Record<string, unknown>;
  if (typeof msg.method !== "string" || !msg.method) return null;
  if (typeof msg.id !== "string" && typeof msg.id !== "number") return null;
  const params = msg.params && typeof msg.params === "object" && !Array.isArray(msg.params)
    ? (msg.params as Record<string, unknown>)
    : {};
  return { id: msg.id, method: msg.method, params };
}

/** theme 令牌注入消息（宿主 → iframe）：{type:'theme', tokens} */
export function buildThemeMessage(tokens: Record<string, string>): unknown {
  return { type: "theme", tokens };
}

interface HtmlPanelFrameProps {
  pluginId: string;
  /** html/ 子树内的文件（如 "panel.html"） */
  file: string;
  /** RPC 通道（宿主接到 /html-rpc 同一审批闸） */
  rpc: PanelRpcCall;
  /** 面板加载完成后注入的设计令牌（CSS 变量名 → 值） */
  themeTokens?: Record<string, string>;
  title?: string;
  className?: string;
}

export function HtmlPanelFrame({
  pluginId,
  file,
  rpc,
  themeTokens,
  title,
  className,
}: HtmlPanelFrameProps) {
  const frameRef = useRef<HTMLIFrameElement | null>(null);

  const handleMessage = useCallback(
    async (event: MessageEvent) => {
      const frame = frameRef.current;
      // 只接受本 iframe 的消息（防其它帧/窗口冒充进桥）
      if (!frame || event.source !== frame.contentWindow) return;
      const req = parsePanelRpc(event.data);
      if (!req) return;
      const { result, error } = await rpc(req.method, req.params ?? {});
      frame.contentWindow?.postMessage(
        { id: req.id, ...(error ? { error } : { result: result ?? {} }) },
        "*"
      );
    },
    [rpc]
  );

  useEffect(() => {
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [handleMessage]);

  const handleLoad = useCallback(() => {
    if (!themeTokens) return;
    frameRef.current?.contentWindow?.postMessage(buildThemeMessage(themeTokens), "*");
  }, [themeTokens]);

  return (
    <iframe
      ref={frameRef}
      sandbox="allow-scripts"
      src={`/plugins/${pluginId}/html/${file}`}
      title={title ?? `${pluginId} 面板`}
      onLoad={handleLoad}
      className={cn("w-full min-h-[24rem] border-0", className)}
      data-slot="plugin-html-panel-frame"
    />
  );
}
