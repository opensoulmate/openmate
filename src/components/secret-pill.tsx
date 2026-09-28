'use client';

/**
 * secret-pill.tsx — P1 流式输出脱敏展示：可点击揭示的脱敏胶囊
 *
 * Warp secret_redaction reveal UX：敏感数据默认遮蔽为预览（前4后4），
 * 点击眼睛图标揭示原文，再点隐藏。风险等级chip + 类型 tooltip。
 *
 * SecretAwareSpan 是 ReactMarkdown components.span 的替换实现：
 * rehype-secret-mask 插件把密钥段落包成 span（node.data.secretMeta），
 * 此处识别并渲染 SecretPill；普通 span 原样透传。
 */

import { useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import {
  maskSecret,
  riskToBadgeClasses,
  riskToLabel,
  type SecretFinding,
} from '../lib/secret-redaction';

export function SecretPill({ meta }: { meta: SecretFinding }) {
  const [revealed, setRevealed] = useState(false);
  const riskChip = riskToBadgeClasses(meta.risk);

  if (revealed) {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-md border border-dashed border-red-400/50 bg-red-500/10 px-1.5 py-px align-baseline"
        title={`${meta.label}（${riskToLabel(meta.risk)}）— 点击隐藏`}
      >
        <code className="font-mono text-[11px] text-red-600 dark:text-red-400 break-all">
          {meta.matched}
        </code>
        <button
          type="button"
          onClick={() => setRevealed(false)}
          className="shrink-0 rounded p-0.5 text-red-500/80 transition-colors hover:bg-red-500/15 hover:text-red-600"
          aria-label="隐藏敏感数据"
        >
          <EyeOff className="h-3 w-3" />
        </button>
      </span>
    );
  }

  return (
    <span
      className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-muted/60 px-1.5 py-px align-baseline"
      title={`${meta.label}（${riskToLabel(meta.risk)}）— 点击显示原文`}
    >
      <code className="font-mono text-[11px] text-muted-foreground break-all select-none">
        {maskSecret(meta.matched)}
      </code>
      <span
        className={`inline-flex shrink-0 items-center rounded border px-1 py-px text-[10px] font-medium ${riskChip}`}
      >
        {meta.label}
      </span>
      <button
        type="button"
        onClick={() => setRevealed(true)}
        className="shrink-0 rounded p-0.5 text-muted-foreground/70 transition-colors hover:bg-foreground/10 hover:text-foreground"
        aria-label="显示敏感数据"
      >
        <Eye className="h-3 w-3" />
      </button>
    </span>
  );
}

/**
 * ReactMarkdown `components.span` 替换实现。
 * 带 secretMeta 的 span（rehype-secret-mask 产物）→ SecretPill；
 * 其余 span 原样透传（保留 className 等属性）。
 */
export function SecretAwareSpan({ node, children, ...props }: any) {
  const meta = node?.data?.secretMeta as SecretFinding | undefined;
  if (meta) return <SecretPill meta={meta} />;
  return <span {...props}>{children}</span>;
}
