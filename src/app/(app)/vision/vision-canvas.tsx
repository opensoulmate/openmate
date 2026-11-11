'use client';
import { useEffect, useRef, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ZoomIn, ZoomOut, Maximize2, Download, Image as ImageIcon,
} from 'lucide-react';

// ── 图表数据（chart kind 的 spec = 本结构的 JSON 文本）────────
interface ChartData {
  type: 'bar' | 'line' | 'pie';
  title?: string;
  labels: string[];
  values: number[];
  xlabel?: string;
  ylabel?: string;
  unit?: string;
}

interface VisionCanvasProps {
  spec: string;
  kind: string;
  title?: string;
}

const PALETTE = ['#6366f1', '#22c55e', '#f59e0b', '#e11d48', '#06b6d4', '#a855f7', '#84cc16', '#f97316'];
const W = 720;
const H = 420;

function fmtNum(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, '');
}

// 纯 SVG 手绘数据图（后端零绘图依赖的对应前端渲染面）
function buildChartSvg(data: ChartData): string {
  const labels = data.labels || [];
  const values = (data.values || []).slice(0, labels.length);
  const n = Math.min(labels.length, values.length);
  const parts: string[] = [];
  parts.push(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" font-family="system-ui, -apple-system, sans-serif">`,
  );
  parts.push(`<rect width="${W}" height="${H}" fill="#0d0d14"/>`);
  if (data.title) {
    parts.push(
      `<text x="${W / 2}" y="28" text-anchor="middle" fill="#cdd6f4" font-size="16" font-weight="600">${data.title}</text>`,
    );
  }

  if (data.type === 'pie') {
    const total = values.reduce((a, b) => a + Math.abs(b), 0) || 1;
    const cx = W / 2 - 90;
    const cy = H / 2 + 10;
    const r = 130;
    let angle = -Math.PI / 2;
    for (let i = 0; i < n; i++) {
      const frac = Math.abs(values[i]) / total;
      const end = angle + frac * Math.PI * 2;
      const x1 = cx + r * Math.cos(angle);
      const y1 = cy + r * Math.sin(angle);
      const x2 = cx + r * Math.cos(end);
      const y2 = cy + r * Math.sin(end);
      const large = end - angle > Math.PI ? 1 : 0;
      if (frac > 0.001) {
        parts.push(
          `<path d="M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z" fill="${PALETTE[i % PALETTE.length]}" stroke="#0d0d14" stroke-width="2"/>`,
        );
      }
      angle = end;
    }
    // 图例
    const lx = W - 220;
    for (let i = 0; i < n; i++) {
      const ly = 80 + i * 28;
      if (ly > H - 30) break;
      parts.push(`<rect x="${lx}" y="${ly - 12}" width="14" height="14" rx="3" fill="${PALETTE[i % PALETTE.length]}"/>`);
      const pct = ((Math.abs(values[i]) / total) * 100).toFixed(1);
      parts.push(
        `<text x="${lx + 22}" y="${ly}" fill="#cdd6f4" font-size="12">${labels[i]}  ${fmtNum(values[i])}${data.unit || ''} (${pct}%)</text>`,
      );
    }
  } else {
    // bar / line：直角坐标系
    const padL = 64;
    const padR = 24;
    const padT = 56;
    const padB = 56;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const maxV = Math.max(...values, 0);
    const minV = Math.min(...values, 0);
    const span = maxV - minV || 1;
    const yOf = (v: number) => padT + plotH - ((v - minV) / span) * plotH;

    // y 轴刻度（4 档）
    for (let i = 0; i <= 4; i++) {
      const v = minV + (span * i) / 4;
      const y = yOf(v);
      parts.push(`<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="#2a2a3d" stroke-width="1"/>`);
      parts.push(`<text x="${padL - 8}" y="${y + 4}" text-anchor="end" fill="#8b8ba3" font-size="11">${fmtNum(v)}</text>`);
    }
    // x 轴基线
    parts.push(`<line x1="${padL}" y1="${yOf(0)}" x2="${W - padR}" y2="${yOf(0)}" stroke="#45475a" stroke-width="1.5"/>`);

    const step = plotW / Math.max(n, 1);
    const tickEvery = Math.ceil(n / 12);
    for (let i = 0; i < n; i++) {
      const x = padL + step * (i + 0.5);
      if (data.type === 'bar') {
        const bw = Math.min(step * 0.6, 56);
        const y = yOf(values[i]);
        const y0 = yOf(0);
        parts.push(
          `<rect x="${x - bw / 2}" y="${Math.min(y, y0)}" width="${bw}" height="${Math.max(Math.abs(y0 - y), 1)}" rx="3" fill="${PALETTE[0]}"/>`,
        );
        parts.push(
          `<text x="${x}" y="${Math.min(y, y0) - 6}" text-anchor="middle" fill="#cdd6f4" font-size="11">${fmtNum(values[i])}</text>`,
        );
      }
      if (i % tickEvery === 0) {
        parts.push(`<text x="${x}" y="${H - padB + 22}" text-anchor="middle" fill="#8b8ba3" font-size="11">${labels[i]}</text>`);
      }
    }
    if (data.type === 'line') {
      const pts = values.map((v, i) => `${padL + step * (i + 0.5)},${yOf(v)}`).join(' ');
      parts.push(`<polyline points="${pts}" fill="none" stroke="${PALETTE[0]}" stroke-width="2.5" stroke-linejoin="round"/>`);
      for (let i = 0; i < n; i++) {
        const x = padL + step * (i + 0.5);
        parts.push(`<circle cx="${x}" cy="${yOf(values[i])}" r="4" fill="${PALETTE[0]}"/>`);
        parts.push(
          `<text x="${x}" y="${yOf(values[i]) - 10}" text-anchor="middle" fill="#cdd6f4" font-size="11">${fmtNum(values[i])}</text>`,
        );
      }
    }
    if (data.ylabel) {
      parts.push(`<text x="18" y="${H / 2}" fill="#8b8ba3" font-size="12" transform="rotate(-90 18 ${H / 2})" text-anchor="middle">${data.ylabel}</text>`);
    }
    if (data.xlabel) {
      parts.push(`<text x="${W / 2}" y="${H - 12}" fill="#8b8ba3" font-size="12" text-anchor="middle">${data.xlabel}</text>`);
    }
  }
  parts.push('</svg>');
  return parts.join('');
}

export function VisionCanvas({ spec, kind, title }: VisionCanvasProps) {
  const { t } = useTranslation();
  const [svg, setSvg] = useState('');
  const [error, setError] = useState('');
  const [zoom, setZoom] = useState(1);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setSvg('');
    setError('');
    if (!spec || !spec.trim()) return;

    if (kind === 'chart') {
      try {
        const data = JSON.parse(spec) as ChartData;
        if (!Array.isArray(data.labels) || !Array.isArray(data.values)) {
          throw new Error(t('vision.canvas.badChart', '图表数据缺少 labels/values'));
        }
        if (!cancelled) setSvg(buildChartSvg(data));
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
      return;
    }

    // mermaid 只能客户端动态 import——SSR 期 import 必炸（参照 mermaid-diagram.tsx 同款纪律）
    const render = async () => {
      try {
        const mermaid = (await import('mermaid')).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: 'dark',
          themeVariables: {
            primaryColor: '#1e1e2e',
            primaryTextColor: '#cdd6f4',
            primaryBorderColor: '#45475a',
            lineColor: '#89b4fa',
            secondaryColor: '#313244',
            tertiaryColor: '#181825',
            fontFamily: 'system-ui, -apple-system, sans-serif',
          },
        });
        const id = `vision-mermaid-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const { svg: rendered } = await mermaid.render(id, spec);
        if (!cancelled) setSvg(rendered);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    };
    render();
    return () => {
      cancelled = true;
    };
  }, [spec, kind, t]);

  const downloadBlob = useCallback((blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  const exportSvg = useCallback(() => {
    if (!svg) return;
    downloadBlob(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }), `${title || 'vision'}.svg`);
  }, [svg, title, downloadBlob]);

  const exportPng = useCallback(() => {
    if (!svg) return;
    const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const img = new window.Image();
    img.onload = () => {
      const scale = 2;
      const canvas = document.createElement('canvas');
      canvas.width = (img.width || W) * scale;
      canvas.height = (img.height || H) * scale;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.scale(scale, scale);
        ctx.drawImage(img, 0, 0);
        canvas.toBlob((png) => {
          if (png) downloadBlob(png, `${title || 'vision'}.png`);
        }, 'image/png');
      }
      URL.revokeObjectURL(url);
    };
    img.onerror = () => URL.revokeObjectURL(url);
    img.src = url;
  }, [svg, title, downloadBlob]);

  const btnClass =
    'flex items-center justify-center w-7 h-7 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors';

  return (
    <div className="flex h-full min-h-0 flex-col rounded-xl border border-border bg-card overflow-hidden">
      {/* 工具条：缩放 + 导出 */}
      <div className="flex items-center justify-between border-b border-border px-3 py-2 gap-2">
        <div className="flex items-center gap-1">
          <button
            onClick={() => setZoom((z) => Math.max(z - 0.25, 0.25))}
            className={btnClass}
            title={t('vision.canvas.zoomOut', '缩小')}
          >
            <ZoomOut size={14} />
          </button>
          <span className="w-11 text-center text-[10px] tabular-nums text-muted-foreground">{Math.round(zoom * 100)}%</span>
          <button
            onClick={() => setZoom((z) => Math.min(z + 0.25, 3))}
            className={btnClass}
            title={t('vision.canvas.zoomIn', '放大')}
          >
            <ZoomIn size={14} />
          </button>
          <button onClick={() => setZoom(1)} className={btnClass} title={t('vision.canvas.fit', '适配屏幕')}>
            <Maximize2 size={14} />
          </button>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={exportSvg}
            disabled={!svg}
            className={btnClass}
            title={t('vision.canvas.exportSvg', '导出 SVG')}
          >
            <Download size={14} />
          </button>
          <button
            onClick={exportPng}
            disabled={!svg}
            className={btnClass}
            title={t('vision.canvas.exportPng', '导出 PNG')}
          >
            <ImageIcon size={14} />
          </button>
        </div>
      </div>

      {/* 内容：渲染失败显示 spec 原文+错误提示，禁白屏 */}
      <div className="flex-1 min-h-0 overflow-auto bg-[#0d0d14]">
        {!spec || !spec.trim() ? (
          <div className="flex h-full min-h-[200px] items-center justify-center text-xs text-muted-foreground">
            {t('vision.canvas.empty', '暂无内容——先在「生成」区生成，或从「历史」回看')}
          </div>
        ) : error ? (
          <div className="p-4 space-y-3">
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-400">
              <div className="font-medium mb-1">{t('vision.canvas.renderFailed', '渲染失败')}</div>
              <pre className="m-0 whitespace-pre-wrap break-all">{error}</pre>
            </div>
            <div>
              <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                {t('vision.canvas.specRaw', 'spec 原文')}
              </div>
              <pre className="m-0 overflow-x-auto rounded-lg border border-border/50 p-3 text-xs leading-relaxed text-[#cdd6f4]">
                {spec}
              </pre>
            </div>
          </div>
        ) : (
          <div className="flex min-h-full items-start justify-center p-4">
            <div
              style={{ transform: `scale(${zoom})`, transformOrigin: 'top center', transition: 'transform 0.15s ease' }}
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
