/**
 * secret-redaction.ts — P1流式输出脱敏展示：前端敏感数据扫描工具
 *
 * 正则模式移植自 opensoul/src/immune/moderator.py（Warp secret_redaction 24 patterns）。
 * 用途：聊天界面内联展示——检测到敏感数据时显示脱敏徽章，hover显示类型，click揭示原文。
 * 参照：Warp secret_redaction（hover点击揭示UX）+ agent-zero _infection_check（输出侧审计门）。
 *
 * 安全设计：
 * - 本工具仅在浏览器本地扫描，不向任何外部服务发送数据
 * - 原文保留在浏览器内存中（用户自己的数据，本地部署场景可接受）
 * - API端点(/api/immune/scan-text)不返回matched原文，仅返回position+type
 */

export interface SecretFinding {
  type: string;
  label: string;
  risk: 'low' | 'medium' | 'high' | 'critical';
  start: number;
  end: number;
  matched: string; // 仅前端本地扫描时包含，API响应不含此字段
}

interface PatternConfig {
  pattern: RegExp;
  label: string;
  risk: 'low' | 'medium' | 'high' | 'critical';
}

// ── Pattern registry (ported from opensoul/src/immune/moderator.py) ──
const PATTERNS: Record<string, PatternConfig> = {
  // PII patterns (7 original)
  phone_cn: {
    pattern: /(?<!\d)1[3-9]\d{9}(?!\d)/,
    label: '手机号',
    risk: 'medium',
  },
  id_card_cn: {
    pattern: /(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)/,
    label: '身份证号',
    risk: 'high',
  },
  email: {
    pattern: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/,
    label: '邮箱地址',
    risk: 'low',
  },
  bank_card: {
    pattern: /(?<!\d)(?:6[0-9]{15,18}|4[0-9]{12,15}|5[1-5][0-9]{14}|3[47][0-9]{13})(?!\d)/,
    label: '银行卡号',
    risk: 'high',
  },
  password_leak: {
    pattern: /(?:password|passwd|pwd|secret|token|api.?key)\s*[:=]\s*\S+/i,
    label: '密码/密钥泄露',
    risk: 'critical',
  },
  url_with_auth: {
    pattern: /https?:\/\/[^:]+:[^@]+@[^\s]+/,
    label: '带凭证URL',
    risk: 'high',
  },
  // API key patterns (17 from Warp secret_redaction)
  google_api_key: {
    pattern: /\bAIza[0-9A-Za-z\-_]{35}\b/,
    label: 'Google API Key',
    risk: 'critical',
  },
  aws_access_id: {
    pattern: /\b(AKIA|A3T|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{12,}\b/,
    label: 'AWS Access ID',
    risk: 'critical',
  },
  slack_app_token: {
    pattern: /\bxapp-[0-9]+-[A-Za-z0-9_]+-[0-9]+-[a-f0-9]+\b/,
    label: 'Slack App Token',
    risk: 'critical',
  },
  github_classic_pat: {
    pattern: /\bghp_[A-Za-z0-9_]{36}\b/,
    label: 'GitHub PAT',
    risk: 'critical',
  },
  github_fine_grained_pat: {
    pattern: /\bgithub_pat_[A-Za-z0-9_]{82}\b/,
    label: 'GitHub Fine-Grained PAT',
    risk: 'critical',
  },
  github_oauth_token: {
    pattern: /\bgho_[A-Za-z0-9_]{36}\b/,
    label: 'GitHub OAuth Token',
    risk: 'critical',
  },
  github_user_to_server_token: {
    pattern: /\bghu_[A-Za-z0-9_]{36}\b/,
    label: 'GitHub User-to-Server Token',
    risk: 'critical',
  },
  github_server_to_server_token: {
    pattern: /\bghs_[A-Za-z0-9_]{36}\b/,
    label: 'GitHub Server Token',
    risk: 'critical',
  },
  stripe_key: {
    pattern: /\b(?:r|s)k_(?:test|live)_[0-9a-zA-Z]{24}\b/,
    label: 'Stripe Key',
    risk: 'critical',
  },
  jwt: {
    pattern: /\b(?:ey[A-Za-z0-9_\-]{10,}\.){2}[A-Za-z0-9_\-]{10,}\b/,
    label: 'JWT Token',
    risk: 'high',
  },
  openai_api_key: {
    pattern: /\bsk-[a-zA-Z0-9]{48}\b/,
    label: 'OpenAI API Key',
    risk: 'critical',
  },
  anthropic_api_key: {
    pattern: /\bsk-ant-api\d{0,2}-[a-zA-Z0-9\-]{80,120}\b/,
    label: 'Anthropic API Key',
    risk: 'critical',
  },
  generic_sk_api_key: {
    pattern: /\bsk-[a-zA-Z0-9\-]{10,100}\b/,
    label: 'sk- API Key',
    risk: 'critical',
  },
  fireworks_api_key: {
    pattern: /\bfw_[a-zA-Z0-9]{24}\b/,
    label: 'Fireworks API Key',
    risk: 'critical',
  },
};

const RISK_ORDER: Record<string, number> = { low: 0, medium: 1, high: 2, critical: 3 };

/**
 * Scan text for secrets. Returns findings sorted by position.
 * Overlapping matches are merged (highest-risk type wins) — mirrors
 * opensoul ContentModerator._redact merge_sorted_ranges logic.
 */
export function scanForSecrets(text: string, minRisk: 'low' | 'medium' | 'high' | 'critical' = 'low'): SecretFinding[] {
  if (!text || text.length < 4) return [];
  const threshold = RISK_ORDER[minRisk] ?? 0;
  const findings: SecretFinding[] = [];

  for (const [type, config] of Object.entries(PATTERNS)) {
    if ((RISK_ORDER[config.risk] ?? 0) < threshold) continue;
    try {
      const regex = new RegExp(config.pattern.source, config.pattern.flags.includes('g') ? config.pattern.flags : config.pattern.flags + 'g');
      let match;
      while ((match = regex.exec(text)) !== null) {
        findings.push({
          type,
          label: config.label,
          risk: config.risk,
          start: match.index,
          end: match.index + match[0].length,
          matched: match[0],
        });
        if (match[0].length === 0) regex.lastIndex++; // prevent infinite loop on zero-length matches
      }
    } catch {
      // Skip broken patterns (fail-safe, mirrors opensoul moderator)
    }
  }

  // Sort by position, then merge overlapping ranges (highest risk wins)
  findings.sort((a, b) => a.start - b.start);
  const merged: SecretFinding[] = [];
  for (const f of findings) {
    if (merged.length > 0 && f.start < merged[merged.length - 1].end) {
      const prev = merged[merged.length - 1];
      prev.end = Math.max(prev.end, f.end);
      if ((RISK_ORDER[f.risk] ?? 0) > (RISK_ORDER[prev.risk] ?? 0)) {
        prev.type = f.type;
        prev.risk = f.risk;
        prev.label = f.label;
      }
    } else {
      merged.push({ ...f });
    }
  }
  return merged;
}

/** Get highest risk level from findings list. */
export function getHighestRisk(findings: SecretFinding[]): 'low' | 'medium' | 'high' | 'critical' | null {
  if (findings.length === 0) return null;
  let highest: 'low' | 'medium' | 'high' | 'critical' = 'low';
  for (const f of findings) {
    if ((RISK_ORDER[f.risk] ?? 0) > (RISK_ORDER[highest] ?? 0)) {
      highest = f.risk;
    }
  }
  return highest;
}

/** Risk level → Tailwind CSS classes for the badge. */
export function riskToBadgeClasses(risk: string): string {
  switch (risk) {
    case 'critical':
      return 'bg-red-500/15 text-red-600 border-red-500/30';
    case 'high':
      return 'bg-orange-500/15 text-orange-600 border-orange-500/30';
    case 'medium':
      return 'bg-amber-500/15 text-amber-600 border-amber-500/30';
    default:
      return 'bg-gray-500/10 text-gray-500 border-gray-500/20';
  }
}

/** Risk level → short display label. */
export function riskToLabel(risk: string): string {
  switch (risk) {
    case 'critical': return '严重';
    case 'high': return '高危';
    case 'medium': return '中等';
    default: return '低风险';
  }
}

/** Mask a secret for display — show first/last chars only. */
export function maskSecret(text: string): string {
  if (text.length <= 8) return '••••••';
  return text.slice(0, 4) + '••••' + text.slice(-4);
}

// ── P1 流式输出脱敏展示：正文遮蔽（Warp secret_redaction reveal UX）──
// splitSecretSegments 是唯一分段真源：rehype 插件（散文/行内）与
// CodeBlock（围栏代码）都消费它，遮蔽语义单一真源不漂移。

/** A piece of display text: plain text, or one secret finding to mask. */
export interface TextSegment {
  text: string;
  finding?: SecretFinding;
}

/**
 * Split text into alternating plain/secret segments (findings already
 * position-sorted & overlap-merged by scanForSecrets). Secrets become
 * their own segments so callers can render masked pills in place.
 */
export function splitSecretSegments(
  text: string,
  minRisk: 'low' | 'medium' | 'high' | 'critical' = 'high'
): TextSegment[] {
  if (!text) return [];
  const findings = scanForSecrets(text, minRisk);
  if (findings.length === 0) return [{ text }];
  const segments: TextSegment[] = [];
  let cursor = 0;
  for (const f of findings) {
    if (f.start > cursor) segments.push({ text: text.slice(cursor, f.start) });
    segments.push({ text: text.slice(f.start, f.end), finding: f });
    cursor = f.end;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor) });
  return segments;
}

/**
 * Mask every secret inside a code string (fenced-block display path).
 * Each finding is replaced with its maskSecret() preview so line shape
 * stays roughly intact; revealed display uses the original code.
 */
export function maskCodeInText(
  code: string,
  minRisk: 'low' | 'medium' | 'high' | 'critical' = 'high'
): string {
  return splitSecretSegments(code, minRisk)
    .map((seg) => (seg.finding ? maskSecret(seg.text) : seg.text))
    .join('');
}
