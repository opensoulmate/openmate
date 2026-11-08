'use client';
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';

/**
 * MessageTimeline —— 消息历史刻度轨（移植自 hermes desktop timeline）
 *
 * 视觉：右侧一列小横线（每条消息一根），悬停处横线加宽并弹出消息预览，
 * 点击跳转到该消息。左侧轨预览 agent 回复，右侧轨预览用户消息。
 *
 * 设计参数（与 hermes 对齐）：pitch=7px（0.4375rem），横线高 1px（0.0625rem），
 * 基础宽 8px（0.5rem），高斯聚焦曲线加宽至 16px（1rem），
 * transition: width 180ms cubic-bezier(0.2, 0.8, 0.2, 1)。
 */

export interface TimelineEntry {
  id: string;
  preview: string;
}

export interface TimelineMessage {
  id: string;
  role: string;
  text: string;
}

const PREVIEW_MAX = 120;
const PITCH = 7; // px — 0.4375rem
const BAR_BASE = 8; // px — 0.5rem
const BAR_SPAN = 8; // px — 聚焦最多再加 0.5rem = 1rem

/** 折叠空白、超长截断（hermes timelinePreview 同款） */
export function timelinePreview(text: string, max: number = PREVIEW_MAX): string {
  const collapsed = text.replace(/\s+/g, ' ').trim();
  if (collapsed.length <= max) return collapsed;
  return `${collapsed.slice(0, max - 1).trimEnd()}…`;
}

/** 从消息里取指定角色的刻度（跳过空文本） */
export function deriveTimelineEntries(
  messages: readonly TimelineMessage[],
  role: 'user' | 'agent'
): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  for (const m of messages) {
    if (m.role !== role) continue;
    const text = m.text.trim();
    if (!text) continue;
    entries.push({ id: m.id, preview: timelinePreview(text) });
  }
  return entries;
}

/** 高斯聚焦曲线（hermes timelineBarWidth 同款）：距离>3 归零，active 恒最大 */
export function timelineBarWidth(index: number, activeIndex: number, hoverIndex: number | null): number {
  const curve = (d: number) => (Math.abs(d) > 3 ? 0 : Math.exp(-(d ** 2) / 1.35));
  return BAR_BASE + BAR_SPAN * Math.max(curve(index - activeIndex), hoverIndex === null ? 0 : curve(index - hoverIndex));
}

/**
 * 滚动位置 → 当前消息刻度索引（hermes activeTimelineIndex 同款）：
 * 取「已滚过视口顶（≤8px 余量）的最后一条」，否则取第一条已渲染的。
 */
export function useTimelineActiveIndex(
  scrollRef: RefObject<HTMLElement | null>,
  entries: readonly TimelineEntry[]
): number {
  const [active, setActive] = useState(0);

  const compute = useCallback(() => {
    const el = scrollRef.current;
    if (!el || entries.length === 0) return;
    const top = el.getBoundingClientRect().top;
    let last = -1;
    let first = -1;
    for (let i = 0; i < entries.length; i++) {
      const node = el.querySelector<HTMLElement>(`[id="msg-${entries[i].id}"]`);
      if (!node) continue;
      if (first === -1) first = i;
      if (node.getBoundingClientRect().top - top <= 8) last = i;
    }
    const next = last !== -1 ? last : first !== -1 ? first : 0;
    setActive(prev => (prev === next ? prev : next));
  }, [entries, scrollRef]);

  useEffect(() => {
    compute();
    const el = scrollRef.current;
    if (!el) return;
    el.addEventListener('scroll', compute, { passive: true });
    return () => el.removeEventListener('scroll', compute);
  }, [compute, scrollRef]);

  return entries.length === 0 ? 0 : Math.min(active, entries.length - 1);
}

interface MessageTimelineRailProps {
  entries: readonly TimelineEntry[];
  activeIndex: number;
  onJump: (id: string) => void;
  /** 悬停预览浮层出现的一侧：right 轨浮层在左（贴对话区），left 轨浮层在右 */
  side: 'left' | 'right';
  className?: string;
  style?: React.CSSProperties;
}

export function MessageTimelineRail({ entries, activeIndex, onJump, side, className = '', style }: MessageTimelineRailProps) {
  const [hover, setHover] = useState<number | null>(null);
  const [tipY, setTipY] = useState(0);
  const trackRef = useRef<HTMLDivElement>(null);

  if (entries.length === 0) return null;

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const track = trackRef.current;
    if (!track) return;
    const tRect = track.getBoundingClientRect();
    const idx = Math.round(Math.max(0, Math.min(entries.length - 1, (e.clientY - tRect.top) / PITCH - 0.5)));
    setHover(idx);
    setTipY(e.clientY - e.currentTarget.getBoundingClientRect().top);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    const base = hover ?? activeIndex;
    const next = { ArrowUp: base - 1, ArrowDown: base + 1, Home: 0, End: entries.length - 1 }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    const idx = Math.max(0, Math.min(entries.length - 1, next));
    setHover(idx);
    onJump(entries[idx].id);
  };

  return (
    <div
      className={`w-9 shrink-0 overflow-y-auto overflow-x-hidden flex flex-col [justify-content:safe_center] ${className}`}
      style={{ position: 'relative', scrollbarWidth: 'none', ...style }}
      onPointerMove={onPointerMove}
      onPointerLeave={() => setHover(null)}
      onKeyDown={onKeyDown}
      onMouseOut={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setHover(null); }}
    >
      <div ref={trackRef} className="relative w-full" style={{ height: entries.length * PITCH }}>
        {entries.map((entry, index) => {
          const w = index === hover ? BAR_BASE + BAR_SPAN : BAR_BASE;
          const isActive = index === activeIndex;
          return (
            <button
              key={entry.id}
              type="button"
              aria-label={entry.preview}
              aria-current={isActive ? 'location' : undefined}
              onClick={() => onJump(entry.id)}
              onMouseEnter={() => { setHover(index); setTipY((trackRef.current?.offsetTop ?? 0) + index * PITCH); }}
              className={`absolute ${side === 'right' ? 'right-0 pr-1.5 justify-end' : 'left-0 pl-1.5 justify-start'} flex items-center w-full outline-none rounded-sm focus-visible:ring-1 focus-visible:ring-primary/50`}
              style={{ top: index * PITCH, height: PITCH }}
            >
              {/* 动态宽度走内联样式（Tailwind 动态类名扫不到） */}
              <span
                aria-hidden
                className={`block h-px rounded-full transition-[width] duration-[180ms] ease-[cubic-bezier(0.2,0.8,0.2,1)] ${
                  isActive ? 'bg-primary opacity-100' : 'bg-muted-foreground/65'
                }`}
                style={{ width: w }}
              />
            </button>
          );
        })}
      </div>

      {/* 悬停预览浮层（hermes Tip placement=right-rail 同款） */}
      {hover !== null && entries[hover] && (
        <div
          role="tooltip"
          className={`pointer-events-none absolute z-20 max-w-[260px] px-2.5 py-1.5 rounded-lg bg-card border border-border shadow-lg text-xs leading-relaxed ${
            side === 'right' ? 'right-full mr-2 text-right' : 'left-full ml-2 text-left'
          }`}
          style={{ top: Math.max(0, tipY - 18) }}
        >
          {entries[hover].preview}
        </div>
      )}
    </div>
  );
}
