'use client';

/**
 * SecretScanBadge — P1流式输出脱敏展示组件
 *
 * 扫描聊天消息中的敏感数据（API key/密码/PII），检测到时显示安全徽章。
 * 点击展开详情：显示检测到的类型/风险等级/脱敏预览（前4后4字符）。
 * 参照：Warp secret_redaction hover点击揭示UX + agent-zero _infection_check输出侧审计。
 *
 * 使用：<SecretScanBadge text={messageText} />
 * - 无敏感数据时渲染null（零UI干扰）
 * - 有敏感数据时在消息下方显示紧凑徽章
 */

import { useMemo, useState } from 'react';
import { Shield, ShieldAlert, ShieldCheck, ChevronDown, ChevronUp, Eye, EyeOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { scanForSecrets, getHighestRisk, riskToBadgeClasses, riskToLabel, maskSecret, type SecretFinding } from '@/lib/secret-redaction';

interface SecretScanBadgeProps {
  text: string;
  /** Minimum risk level to display (default: 'medium' — skip low-risk noise like IPs/emails) */
  minRisk?: 'low' | 'medium' | 'high' | 'critical';
}

export function SecretScanBadge({ text, minRisk = 'medium' }: SecretScanBadgeProps) {
  const [expanded, setExpanded] = useState(false);
  const [revealed, setRevealed] = useState<Set<number>>(new Set());
  const { t } = useTranslation();

  // i18n key 缺失/未初始化时回落中文原文（defaultValue=zh.json 同值兜底）
  const riskLabel = (r: string) => t(`secretRedaction.risk.${r}`, { defaultValue: riskToLabel(r) });
  const patternLabel = (f: SecretFinding) => t(`secretRedaction.patterns.${f.type}`, { defaultValue: f.label });

  const findings = useMemo(() => scanForSecrets(text, minRisk), [text, minRisk]);
  const highestRisk = useMemo(() => getHighestRisk(findings), [findings]);

  if (findings.length === 0) return null;

  const toggleReveal = (index: number) => {
    setRevealed(prev => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };

  const RiskIcon = highestRisk === 'critical' || highestRisk === 'high'
    ? ShieldAlert
    : Shield;

  return (
    <div className="mt-1.5 inline-block">
      {/* Trigger badge — compact, inline with message */}
      <button
        onClick={() => setExpanded(!expanded)}
        className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium transition-colors hover:opacity-80 ${riskToBadgeClasses(highestRisk || 'low')}`}
        title={t('secretRedaction.badge.title', { n: findings.length, defaultValue: `检测到 ${findings.length} 项敏感数据 — 点击查看详情` })}
      >
        <RiskIcon className="h-3 w-3" />
        <span>{t('secretRedaction.badge.count', { n: findings.length, defaultValue: `${findings.length} 项敏感数据` })}</span>
        <span className="opacity-60">({riskLabel(highestRisk || 'low')})</span>
        {expanded ? <ChevronUp className="h-3 w-3 opacity-60" /> : <ChevronDown className="h-3 w-3 opacity-60" />}
      </button>

      {/* Expanded detail panel */}
      {expanded && (
        <div className="mt-1.5 rounded-lg border border-border bg-background/95 p-2.5 shadow-sm">
          <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <ShieldCheck className="h-3 w-3" />
            <span>{t('secretRedaction.badge.header', { defaultValue: '安全扫描 — 敏感数据检测' })}</span>
          </div>
          <div className="space-y-1">
            {findings.map((f, i) => (
              <div
                key={`${f.type}-${f.start}-${i}`}
                className="flex items-center justify-between gap-2 rounded-md bg-muted/50 px-2 py-1 text-[11px]"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className={`inline-flex shrink-0 items-center rounded border px-1 py-px text-[10px] font-medium ${riskToBadgeClasses(f.risk)}`}>
                    {riskLabel(f.risk)}
                  </span>
                  <span className="truncate text-foreground/80">{patternLabel(f)}</span>
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                    {revealed.has(i) ? f.matched : maskSecret(f.matched)}
                  </span>
                </div>
                <button
                  onClick={() => toggleReveal(i)}
                  className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                  title={revealed.has(i) ? t('secretRedaction.badge.hide', { defaultValue: '隐藏' }) : t('secretRedaction.badge.show', { defaultValue: '显示原文' })}
                >
                  {revealed.has(i)
                    ? <EyeOff className="h-3 w-3" />
                    : <Eye className="h-3 w-3" />}
                </button>
              </div>
            ))}
          </div>
          <div className="mt-1.5 text-[10px] text-muted-foreground/70">
            {t('secretRedaction.badge.note', { defaultValue: '扫描在本地浏览器执行，数据不外发。参考 Warp secret_redaction。' })}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * InlineSecretHighlight — 行内脱敏高亮组件
 *
 * 将文本中的敏感数据替换为内联脱敏标记，hover显示类型，click揭示。
 * 适用于工具调用结果等不需要完整markdown渲染的文本块。
 */
export function InlineSecretHighlight({ text, minRisk = 'high' }: { text: string; minRisk?: 'low' | 'medium' | 'high' | 'critical' }) {
  const [globalReveal, setGlobalReveal] = useState(false);
  const findings = useMemo(() => scanForSecrets(text, minRisk), [text, minRisk]);
  const { t } = useTranslation();
  const riskLabel = (r: string) => t(`secretRedaction.risk.${r}`, { defaultValue: riskToLabel(r) });
  const patternLabel = (f: SecretFinding) => t(`secretRedaction.patterns.${f.type}`, { defaultValue: f.label });

  if (findings.length === 0) return <>{text}</>;

  // Build segments: normal text + redacted spans
  const segments: Array<{ text: string; finding?: SecretFinding }> = [];
  let lastEnd = 0;
  for (const f of findings) {
    if (f.start > lastEnd) {
      segments.push({ text: text.slice(lastEnd, f.start) });
    }
    segments.push({ text: text.slice(f.start, f.end), finding: f });
    lastEnd = f.end;
  }
  if (lastEnd < text.length) {
    segments.push({ text: text.slice(lastEnd) });
  }

  return (
    <span className="font-mono text-xs">
      {segments.map((seg, i) => {
        if (!seg.finding) return <span key={i}>{seg.text}</span>;
        return (
          <span
            key={i}
            className={`inline-flex items-center gap-0.5 rounded border px-1 py-px cursor-pointer transition-colors ${riskToBadgeClasses(seg.finding.risk)}`}
            title={t('secretRedaction.inline.tip', {
              label: patternLabel(seg.finding),
              risk: riskLabel(seg.finding.risk),
              action: globalReveal
                ? t('secretRedaction.inline.hideAction', { defaultValue: '隐藏' })
                : t('secretRedaction.inline.revealAction', { defaultValue: '揭示' }),
              defaultValue: `${seg.finding.label} (${riskToLabel(seg.finding.risk)}) — 点击${globalReveal ? '隐藏' : '揭示'}`,
            })}
            onClick={() => setGlobalReveal(!globalReveal)}
          >
            {globalReveal ? seg.finding.matched : `[REDACTED:${seg.finding.type}]`}
          </span>
        );
      })}
    </span>
  );
}
