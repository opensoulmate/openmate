"use client"

// 轨迹共享展示组件/类型单一真源（2026-10-05 共享化去重专项轮）：
// trajectory-client.tsx 与 system/tabs/trajectory-tab.tsx 两消费者此前逐字镜像
// 本区块（~515行×2份，双编辑成本+漂移风险），现收敛为单一真源两消费者 import
// （cross-search-view.tsx / span-tree.ts 同款路线）。含：
// ① 类型：TrajectoryStats/TrajectorySession/TrajectoryEvent/EventType
// ② 映射常量：EVENT_ICONS/EVENT_COLORS（getEventColor/getEventIcon 单一真源，
//    span-tree-view 经 props 注入消费，不复制映射表）
// ③ 工具函数：timeAgo
// ④ 展示组件：StatsBar/SearchBar/SessionCard/EventCard/ReplayControls
import { useState, useRef } from "react"
import { useTranslation } from "react-i18next"
import {
  Activity, Search, GitBranch, Play, Pause, SkipForward, SkipBack,
  Zap, Hash, Filter, ChevronRight, ChevronDown, Layers,
  CheckCircle, XCircle, Eye, Copy, Terminal, MessageSquare, Bot, Wrench,
} from "lucide-react"
import { formatDuration, formatTokens } from "./span-tree"
import { StatusFilterChips, MatchedText } from "./cross-search-view"

// ── Types ──────────────────────────────────────────────────────

export interface TrajectoryStats {
  total_sessions: number
  running_sessions: number
  total_events: number
  total_tokens: number
}

export interface TrajectorySession {
  id: string
  agent_id: string
  task_description: string
  status: string
  total_events: number
  total_tokens: number
  created_at: string
  ended_at: string | null
}

export interface TrajectoryEvent {
  id: string
  session_id: string
  parent_event_id: string | null
  event_type: string
  agent_id: string
  content: string
  metadata_json: string | null
  token_usage: number
  duration_ms: number
  status: string
  created_at: string
}

export interface EventType {
  value: string
  name: string
}

// ── Constants ──────────────────────────────────────────────────

const EVENT_ICONS: Record<string, React.ElementType> = {
  session_start: Play,
  session_end: Pause,
  user_input: MessageSquare,
  llm_call: Bot,
  llm_response: Bot,
  tool_call: Wrench,
  tool_result: CheckCircle,
  agent_dispatch: Zap,
  agent_result: CheckCircle,
  error: XCircle,
  checkpoint: Eye,
  branch: GitBranch,
  thinking: Terminal,
  input: MessageSquare,
  output: CheckCircle,
}

const EVENT_COLORS: Record<string, string> = {
  session_start: "#22c55e",
  session_end: "#94a3b8",
  user_input: "#3b82f6",
  llm_call: "#a855f7",
  llm_response: "#a855f7",
  tool_call: "#f59e0b",
  tool_result: "#06b6d4",
  agent_dispatch: "#ec4899",
  agent_result: "#ec4899",
  error: "#ef4444",
  checkpoint: "#10b981",
  branch: "#8b5cf6",
  thinking: "#eab308",
  input: "#3b82f6",
  output: "#22c55e",
}

export function getEventColor(type: string): string {
  return EVENT_COLORS[type] || "#6b7280"
}

export function getEventIcon(type: string): React.ElementType {
  return EVENT_ICONS[type] || Activity
}

export function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 60000) return "just now"
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
  return `${Math.floor(diff / 86400000)}d ago`
}

// ── Stats Dashboard ────────────────────────────────────────────

export function StatsBar({ stats, loading }: { stats: TrajectoryStats | null; loading: boolean }) {
  const { t } = useTranslation()
  if (loading || !stats) {
    return (
      <div style={{ display: "flex", gap: 16, marginBottom: 20 }}>
        {[1, 2, 3, 4].map(i => (
          <div key={i} style={{
            flex: 1, padding: "12px 16px", borderRadius: 8,
            background: "hsl(var(--muted) / 0.3)", border: "1px solid hsl(var(--border))",
            height: 64, animation: "pulse 1.5s infinite",
          }} />
        ))}
      </div>
    )
  }

  const cards = [
    { label: t("trajectory.sessions"), value: stats.total_sessions, icon: Layers, color: "#3b82f6" },
    { label: t("trajectory.running"), value: stats.running_sessions, icon: Activity, color: "#22c55e" },
    { label: t("trajectory.events"), value: stats.total_events, icon: Hash, color: "#a855f7" },
    { label: t("trajectory.tokens"), value: formatTokens(stats.total_tokens), icon: Zap, color: "#f59e0b" },
  ]

  return (
    <div style={{ display: "flex", gap: 12, marginBottom: 20 }}>
      {cards.map(c => (
        <div key={c.label} style={{
          flex: 1, padding: "12px 16px", borderRadius: 8,
          background: `linear-gradient(135deg, ${c.color}11, ${c.color}05)`,
          border: `1px solid ${c.color}33`,
          display: "flex", alignItems: "center", gap: 12,
        }}>
          <c.icon size={20} style={{ color: c.color, opacity: 0.8 }} />
          <div>
            <div style={{ fontSize: 11, color: "hsl(var(--muted-foreground))", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              {c.label}
            </div>
            <div style={{ fontSize: 22, fontWeight: 700, color: c.color, lineHeight: 1.2 }}>
              {c.value}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Search Bar ─────────────────────────────────────────────────

export function SearchBar({
  query, onQueryChange, eventTypes, selectedTypes, onTypeToggle, onSearch,
  status, onStatusChange,
}: {
  query: string
  onQueryChange: (q: string) => void
  eventTypes: EventType[]
  selectedTypes: Set<string>
  onTypeToggle: (t: string) => void
  onSearch: () => void
  status: string
  onStatusChange: (s: string) => void
}) {
  const [showFilters, setShowFilters] = useState(false)
  const { t } = useTranslation()

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", gap: 8 }}>
        <div style={{
          flex: 1, display: "flex", alignItems: "center", gap: 8,
          padding: "8px 12px", borderRadius: 8,
          border: "1px solid hsl(var(--border))",
          background: "hsl(var(--background))",
        }}>
          <Search size={16} style={{ color: "hsl(var(--muted-foreground))", flexShrink: 0 }} />
          <input
            value={query}
            onChange={e => onQueryChange(e.target.value)}
            onKeyDown={e => e.key === "Enter" && onSearch()}
            placeholder={t("trajectory.searchEventsPlaceholder")}
            style={{
              flex: 1, border: "none", outline: "none", background: "transparent",
              fontSize: 14, color: "hsl(var(--foreground))",
            }}
          />
        </div>
        <button
          onClick={() => setShowFilters(!showFilters)}
          style={{
            padding: "8px 12px", borderRadius: 8, border: "1px solid hsl(var(--border))",
            background: selectedTypes.size > 0 ? "hsl(var(--primary) / 0.1)" : "transparent",
            cursor: "pointer", display: "flex", alignItems: "center", gap: 6, fontSize: 13,
            color: selectedTypes.size > 0 ? "hsl(var(--primary))" : "hsl(var(--muted-foreground))",
          }}
        >
          <Filter size={14} />
          {selectedTypes.size > 0 ? t("trajectory.filters", { count: selectedTypes.size }) : t("trajectory.filter")}
        </button>
        <button
          onClick={onSearch}
          style={{
            padding: "8px 16px", borderRadius: 8, border: "none",
            background: "hsl(var(--primary))", color: "hsl(var(--primary-foreground))",
            cursor: "pointer", fontSize: 13, fontWeight: 500,
          }}
        >
          {t("trajectory.search")}
        </button>
      </div>
      {showFilters && (
        <>
        {eventTypes.length > 0 && (
        <div style={{
          display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8, padding: "8px 12px",
          borderRadius: 8, background: "hsl(var(--muted) / 0.3)",
          border: "1px solid hsl(var(--border))",
        }}>
          {eventTypes.map(et => {
            const active = selectedTypes.has(et.value)
            const color = getEventColor(et.value)
            return (
              <button
                key={et.value}
                onClick={() => onTypeToggle(et.value)}
                style={{
                  padding: "3px 10px", borderRadius: 12, fontSize: 11, fontWeight: 500,
                  border: `1px solid ${active ? color : "hsl(var(--border))"}`,
                  background: active ? `${color}22` : "transparent",
                  color: active ? color : "hsl(var(--muted-foreground))",
                  cursor: "pointer",
                }}
              >
                {et.value}
              </button>
            )
          })}
        </div>
        )}
        <StatusFilterChips value={status} onChange={onStatusChange} />
        </>
      )}
    </div>
  )
}

// ── Session Card ───────────────────────────────────────────────

export function SessionCard({
  session, selected, onSelect,
}: {
  session: TrajectorySession
  selected: boolean
  onSelect: () => void
}) {
  const { t } = useTranslation()
  const statusColor = session.status === "running" ? "#22c55e" :
    session.status === "completed" ? "#3b82f6" : "#94a3b8"

  return (
    <button
      onClick={onSelect}
      style={{
        width: "100%", textAlign: "left", padding: "10px 12px", borderRadius: 8, marginBottom: 6,
        border: selected ? "1px solid hsl(var(--primary))" : "1px solid hsl(var(--border))",
        background: selected ? "hsl(var(--primary) / 0.08)" : "hsl(var(--background))",
        cursor: "pointer", transition: "all 0.15s",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{
          width: 8, height: 8, borderRadius: "50%", background: statusColor, flexShrink: 0,
        }} />
        <div style={{
          fontSize: 13, fontWeight: 500, overflow: "hidden",
          textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1,
          color: "hsl(var(--foreground))",
        }}>
          {session.task_description || session.agent_id || session.id.slice(0, 8)}
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 4, marginLeft: 16 }}>
        <span style={{
          fontSize: 10, padding: "1px 6px", borderRadius: 4,
          background: `${statusColor}22`, color: statusColor,
        }}>
          {session.status}
        </span>
        <span style={{ fontSize: 10, color: "hsl(var(--muted-foreground))" }}>
          {session.total_events} {t("trajectory.events")}
        </span>
        {session.total_tokens > 0 && (
          <span style={{ fontSize: 10, color: "hsl(var(--muted-foreground))" }}>
            {formatTokens(session.total_tokens)} {t("trajectory.tok")}
          </span>
        )}
        <span style={{ fontSize: 10, color: "hsl(var(--muted-foreground))", marginLeft: "auto" }}>
          {timeAgo(session.created_at)}
        </span>
      </div>
    </button>
  )
}

// ── Event Card ─────────────────────────────────────────────────

export function EventCard({ event, idx, total, highlightQuery = "" }: { event: TrajectoryEvent; idx: number; total: number; highlightQuery?: string }) {
  const [expanded, setExpanded] = useState(false)
  const { t } = useTranslation()
  const color = getEventColor(event.event_type)
  const Icon = getEventIcon(event.event_type)
  const contentRef = useRef<HTMLPreElement>(null)

  let parsedMeta: Record<string, unknown> | null = null
  if (event.metadata_json) {
    try { parsedMeta = JSON.parse(event.metadata_json) } catch {}
  }

  const isLong = event.content.length > 300

  return (
    <div style={{
      padding: "10px 14px", borderRadius: 8,
      borderLeft: `3px solid ${color}`,
      background: `${color}08`,
      border: `1px solid ${color}22`,
      borderLeftWidth: 3,
      transition: "all 0.15s",
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
        <span style={{ fontSize: 10, color: "hsl(var(--muted-foreground))", fontFamily: "monospace" }}>
          #{idx + 1}/{total}
        </span>
        <Icon size={14} style={{ color, flexShrink: 0 }} />
        <span style={{
          fontSize: 11, padding: "2px 8px", borderRadius: 4,
          background: `${color}22`, color, fontWeight: 600,
        }}>
          {event.event_type}
        </span>
        {event.agent_id && (
          <span style={{
            fontSize: 10, padding: "2px 6px", borderRadius: 4,
            background: "hsl(var(--muted))", color: "hsl(var(--muted-foreground))",
          }}>
            {event.agent_id}
          </span>
        )}
        {event.status && event.status !== "ok" && (
          <span style={{
            fontSize: 10, padding: "2px 6px", borderRadius: 4,
            background: "#ef484422", color: "#ef4444",
          }}>
            {event.status}
          </span>
        )}
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          {event.token_usage > 0 && (
            <span style={{ fontSize: 10, color: "hsl(var(--muted-foreground))" }}>
              {formatTokens(event.token_usage)} {t("trajectory.tok")}
            </span>
          )}
          {event.duration_ms > 0 && (
            <span style={{ fontSize: 10, color: "hsl(var(--muted-foreground))" }}>
              {formatDuration(event.duration_ms)}
            </span>
          )}
          <span style={{ fontSize: 10, color: "hsl(var(--muted-foreground))" }}>
            {new Date(event.created_at).toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}
          </span>
        </div>
      </div>

      {/* Content */}
      <pre
        ref={contentRef}
        style={{
          fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-word",
          maxHeight: expanded ? 600 : 120, overflow: "auto",
          margin: 0, fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
          color: "hsl(var(--foreground))", lineHeight: 1.5,
          padding: "6px 8px", borderRadius: 4,
          background: "hsl(var(--muted) / 0.3)",
        }}
      >
        {/* 查询命中片段高亮（MatchedText×splitMatch单一真源）；空查询=原文逐字不变 */}
        {highlightQuery ? <MatchedText text={event.content} query={highlightQuery} /> : event.content}
      </pre>

      {/* Expand / Collapse */}
      {(isLong || parsedMeta) && (
        <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
          {isLong && (
            <button
              onClick={() => setExpanded(!expanded)}
              style={{
                fontSize: 11, color: "hsl(var(--primary))", background: "none",
                border: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: 4,
              }}
            >
              {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              {expanded ? t("trajectory.collapse") : t("trajectory.expand")}
            </button>
          )}
          {parsedMeta && (
            <button
              onClick={() => {
                navigator.clipboard.writeText(JSON.stringify(parsedMeta, null, 2))
              }}
              style={{
                fontSize: 11, color: "hsl(var(--muted-foreground))", background: "none",
                border: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: 4,
              }}
            >
              <Copy size={12} /> {t("trajectory.metadata")}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// ── Replay Controls ────────────────────────────────────────────

export function ReplayControls({
  events, currentIdx, onStep, playing, onTogglePlay, speed, onSpeedChange,
}: {
  events: TrajectoryEvent[]
  currentIdx: number
  onStep: (idx: number) => void
  playing: boolean
  onTogglePlay: () => void
  speed: number
  onSpeedChange: (s: number) => void
}) {
  const { t } = useTranslation()
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, padding: "8px 12px",
      borderRadius: 8, background: "hsl(var(--muted) / 0.5)",
      border: "1px solid hsl(var(--border))", marginBottom: 12,
    }}>
      <button
        onClick={() => onStep(Math.max(0, currentIdx - 1))}
        disabled={currentIdx <= 0}
        style={{
          padding: "4px 8px", borderRadius: 4, border: "1px solid hsl(var(--border))",
          background: "transparent", cursor: currentIdx <= 0 ? "default" : "pointer",
          opacity: currentIdx <= 0 ? 0.3 : 1, color: "hsl(var(--foreground))",
        }}
      >
        <SkipBack size={14} />
      </button>
      <button
        onClick={onTogglePlay}
        style={{
          padding: "4px 10px", borderRadius: 4, border: "none",
          background: playing ? "#ef4444" : "hsl(var(--primary))",
          color: "#fff", cursor: "pointer", display: "flex", alignItems: "center", gap: 4,
          fontSize: 12,
        }}
      >
        {playing ? <Pause size={14} /> : <Play size={14} />}
        {playing ? t("trajectory.pause") : t("trajectory.play")}
      </button>
      <button
        onClick={() => onStep(Math.min(events.length - 1, currentIdx + 1))}
        disabled={currentIdx >= events.length - 1}
        style={{
          padding: "4px 8px", borderRadius: 4, border: "1px solid hsl(var(--border))",
          background: "transparent",
          cursor: currentIdx >= events.length - 1 ? "default" : "pointer",
          opacity: currentIdx >= events.length - 1 ? 0.3 : 1,
          color: "hsl(var(--foreground))",
        }}
      >
        <SkipForward size={14} />
      </button>
      <div style={{
        flex: 1, height: 4, borderRadius: 2, background: "hsl(var(--muted))",
        position: "relative", cursor: "pointer", marginLeft: 8,
      }}
        onClick={e => {
          const rect = e.currentTarget.getBoundingClientRect()
          const pct = (e.clientX - rect.left) / rect.width
          onStep(Math.round(pct * (events.length - 1)))
        }}
      >
        <div style={{
          position: "absolute", top: -2, left: `${(currentIdx / Math.max(1, events.length - 1)) * 100}%`,
          width: 8, height: 8, borderRadius: "50%", background: "hsl(var(--primary))",
          transform: "translateX(-50%)",
        }} />
        <div style={{
          height: "100%", borderRadius: 2,
          width: `${(currentIdx / Math.max(1, events.length - 1)) * 100}%`,
          background: "hsl(var(--primary) / 0.4)",
        }} />
      </div>
      <span style={{ fontSize: 11, color: "hsl(var(--muted-foreground))", minWidth: 40, textAlign: "center" }}>
        {currentIdx + 1}/{events.length}
      </span>
      <select
        value={speed}
        onChange={e => onSpeedChange(Number(e.target.value))}
        style={{
          fontSize: 11, padding: "2px 6px", borderRadius: 4,
          border: "1px solid hsl(var(--border))", background: "hsl(var(--background))",
          color: "hsl(var(--foreground))",
        }}
      >
        <option value={2000}>0.5x</option>
        <option value={1000}>1x</option>
        <option value={500}>2x</option>
        <option value={200}>5x</option>
      </select>
    </div>
  )
}
