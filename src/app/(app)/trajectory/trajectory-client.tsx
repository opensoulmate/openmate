"use client"

import { useEffect, useState, useCallback, useRef } from "react"
import { cn } from "@/lib/utils"
import { getApiBaseUrl } from "@/lib/api-client"
import { useTranslation } from "react-i18next"
import { useIsMobile } from "@/hooks/use-mobile"
import { PanelLeft } from "lucide-react"
import {
  Activity, Search, GitBranch, Play, Pause, SkipForward, SkipBack,
  RefreshCw, Clock, Zap, Hash, Filter, ChevronRight, ChevronDown,
  BarChart3, Layers, AlertCircle, CheckCircle, XCircle, Eye,
  Copy, Terminal, MessageSquare, Bot, Wrench, ArrowLeft,
} from "lucide-react"
import { PageLayout } from '@/components/page-layout';
import { SpanTreeView } from "./span-tree-view"
import { formatDuration, formatTokens, type SpanNode, type TreeFilter } from "./span-tree"
import {
  buildSearchQueryString, filterFlatEvents, groupEventsBySession, treeFilterFromSearch,
  type CrossSearchQueryInput, type ServerSessionMatch,
} from "./cross-search"
import { StatusFilterChips, SessionGroupHeader, FlatFilterBar, MatchedText } from "./cross-search-view"

// ── Types ──────────────────────────────────────────────────────

interface TrajectoryStats {
  total_sessions: number
  running_sessions: number
  total_events: number
  total_tokens: number
}

interface TrajectorySession {
  id: string
  agent_id: string
  task_description: string
  status: string
  total_events: number
  total_tokens: number
  created_at: string
  ended_at: string | null
}

interface TrajectoryEvent {
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

interface EventType {
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

function getEventColor(type: string): string {
  return EVENT_COLORS[type] || "#6b7280"
}

function getEventIcon(type: string): React.ElementType {
  return EVENT_ICONS[type] || Activity
}

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 60000) return "just now"
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
  return `${Math.floor(diff / 86400000)}d ago`
}

// ── Stats Dashboard ────────────────────────────────────────────

function StatsBar({ stats, loading }: { stats: TrajectoryStats | null; loading: boolean }) {
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

function SearchBar({
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

function SessionCard({
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

function EventCard({ event, idx, total, highlightQuery = "" }: { event: TrajectoryEvent; idx: number; total: number; highlightQuery?: string }) {
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

function ReplayControls({
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

// ── Main Component ─────────────────────────────────────────────

export function TrajectoryClient() {
  const apiBase = getApiBaseUrl()
  const { t } = useTranslation()

  // State
  const [stats, setStats] = useState<TrajectoryStats | null>(null)
  const [sessions, setSessions] = useState<TrajectorySession[]>([])
  const [events, setEvents] = useState<TrajectoryEvent[]>([])
  const [eventTypes, setEventTypes] = useState<EventType[]>([])
  const isMobile = useIsMobile()
  const [sessionListOpen, setSessionListOpen] = useState(false)
  const [selectedSession, setSelectedSession] = useState<string | null>(null)
  const [searchResults, setSearchResults] = useState<TrajectoryEvent[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState<"list" | "detail" | "search" | "analytics">("list")

  // Analytics state
  const [toolAnalytics, setToolAnalytics] = useState<{tool_name: string; usage_count: number; success_rate: number; avg_duration_ms: number; total_tokens: number}[]>([])
  const [agentAnalytics, setAgentAnalytics] = useState<{agent_id: string; event_count: number; success_rate: number; total_tokens: number; avg_duration_ms: number}[]>([])
  const [tokenAnalytics, setTokenAnalytics] = useState<{daily: {day: string; tokens: number; events: number}[]; summary: {total_tokens: number; total_events: number; avg_daily_tokens: number; days_tracked: number}} | null>(null)
  const [analyticsLoading, setAnalyticsLoading] = useState(false)

  // Search state
  const [query, setQuery] = useState("")
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set())
  // 跨会话搜索状态（2026-10-04）：status过滤 + /search/sessions分组元数据
  const [searchStatus, setSearchStatus] = useState("")
  const [searchGroupMeta, setSearchGroupMeta] = useState<Record<string, ServerSessionMatch>>({})
  // 搜索→树钻取携带面（2026-10-05"树内搜索与扁平搜索整合"）：doSearch快照
  // 的搜索条件（结果是快照条件产的，钻取过滤面不许跟未搜索的新输入走）+
  // 携带到SpanTreeView的initialFilter（列表入口打开会话=null不过滤）
  const [searchCriteria, setSearchCriteria] = useState<CrossSearchQueryInput | null>(null)
  const [carriedTreeFilter, setCarriedTreeFilter] = useState<TreeFilter | null>(null)

  // Replay state
  const [replayIdx, setReplayIdx] = useState(0)
  const [replayPlaying, setReplayPlaying] = useState(false)
  const [replaySpeed, setReplaySpeed] = useState(1000)
  const [replayMode, setReplayMode] = useState(false)

  // Fork state
  const [forking, setForking] = useState(false)

  // Span树状态（2026-10-02 P1，Warp orchestration树首落地）：
  // treeMode默认开=观察面优先（"我都不知道他们在干嘛"），列表一键可回；
  // traceRoots=GET /sessions/{id}/trace嵌套树（数据单一真源=opensoul get_trace_tree）
  const [treeMode, setTreeMode] = useState(true)
  const [traceRoots, setTraceRoots] = useState<SpanNode[] | null>(null)

  const replayTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Data Fetching ──────────────────────────────────────────

  const fetchStats = useCallback(async () => {
    try {
      const r = await fetch(`${apiBase}/api/trajectory/stats`)
      if (r.ok) setStats(await r.json())
    } catch {}
  }, [apiBase])

  const fetchSessions = useCallback(async () => {
    try {
      setLoading(true)
      const r = await fetch(`${apiBase}/api/trajectory/sessions`)
      if (r.ok) {
        const d = await r.json()
        setSessions(d.sessions || [])
      }
    } catch {} finally {
      setLoading(false)
    }
  }, [apiBase])

  const fetchEventTypes = useCallback(async () => {
    try {
      const r = await fetch(`${apiBase}/api/trajectory/event-types`)
      if (r.ok) {
        const d = await r.json()
        setEventTypes(d.types || [])
      }
    } catch {}
  }, [apiBase])

  const fetchAnalytics = useCallback(async () => {
    setAnalyticsLoading(true)
    try {
      const [toolsR, agentsR, tokensR] = await Promise.all([
        fetch(`${apiBase}/api/trajectory/analytics/tools`),
        fetch(`${apiBase}/api/trajectory/analytics/agents`),
        fetch(`${apiBase}/api/trajectory/analytics/tokens`),
      ])
      if (toolsR.ok) { const d = await toolsR.json(); setToolAnalytics(d.tools || []) }
      if (agentsR.ok) { const d = await agentsR.json(); setAgentAnalytics(d.agents || []) }
      if (tokensR.ok) { setTokenAnalytics(await tokensR.json()) }
    } catch {} finally { setAnalyticsLoading(false) }
  }, [apiBase])

  const fetchEvents = useCallback(async (sessionId: string) => {
    try {
      const r = await fetch(`${apiBase}/api/trajectory/sessions/${sessionId}`)
      if (r.ok) {
        const d = await r.json()
        setEvents(d.events || [])
      }
    } catch {}
  }, [apiBase])

  // span树数据：GET /sessions/{id}/trace（opensoul get_trace_tree嵌套+rollup，
  // 环防护/悬空父提升root都在服务端——前端不重复建树防双实现漂移）
  const fetchTrace = useCallback(async (sessionId: string) => {
    try {
      const r = await fetch(`${apiBase}/api/trajectory/sessions/${sessionId}/trace`)
      if (r.ok) {
        const d = await r.json()
        setTraceRoots(d.roots || [])
      }
    } catch {}
  }, [apiBase])

  useEffect(() => {
    fetchStats()
    fetchSessions()
    fetchEventTypes()
  }, [fetchStats, fetchSessions, fetchEventTypes])

  // ── Search ─────────────────────────────────────────────────

  const doSearch = useCallback(async () => {
    if (!query.trim() && selectedTypes.size === 0 && !searchStatus) {
      setSearchResults(null)
      setSearchGroupMeta({})
      setSearchCriteria(null)
      setView("list")
      return
    }
    try {
      // 参数名钉死在buildSearchQueryString（P0回归钉：曾误传q=→文本搜索哑火）
      const qs = buildSearchQueryString({ query, selectedTypes, status: searchStatus })
      // 快照搜索条件（钻取携带面）：结果=这批条件产的，后续输入框改动不追溯
      setSearchCriteria({ query, selectedTypes: new Set(selectedTypes), status: searchStatus })
      const [evR, grpR] = await Promise.all([
        fetch(`${apiBase}/api/trajectory/search?${qs}`),
        fetch(`${apiBase}/api/trajectory/search/sessions?${qs}`),
      ])
      if (evR.ok) {
        const d = await evR.json()
        setSearchResults(d.events || [])
      }
      if (grpR.ok) {
        const gd = await grpR.json()
        const byId: Record<string, ServerSessionMatch> = {}
        for (const g of gd.sessions || []) byId[g.session_id] = g
        setSearchGroupMeta(byId)
      }
      setView("search")
    } catch {}
  }, [apiBase, query, selectedTypes, searchStatus])

  const toggleType = useCallback((t: string) => {
    setSelectedTypes(prev => {
      const next = new Set(prev)
      if (next.has(t)) next.delete(t)
      else next.add(t)
      return next
    })
  }, [])

  // ── Session Selection ──────────────────────────────────────

  // carry=搜索→树钻取携带的过滤条件（treeFilterFromSearch适配）；列表/侧栏
  // 入口不传=null→树不过滤（既有形态逐字不变）
  const selectSession = useCallback((id: string, carry: TreeFilter | null = null) => {
    setSelectedSession(id)
    setView("detail")
    setSessionListOpen(false)
    setReplayMode(false)
    setReplayPlaying(false)
    setReplayIdx(0)
    setCarriedTreeFilter(carry)
    setTraceRoots(null)  // 清上一棵树（防切换会话闪现旧树）
    fetchEvents(id)
    fetchTrace(id)
  }, [fetchEvents, fetchTrace])

  // ── Fork ───────────────────────────────────────────────────

  const forkSession = useCallback(async () => {
    if (!selectedSession || events.length === 0) return
    setForking(true)
    try {
      const r = await fetch(`${apiBase}/api/trajectory/sessions/${selectedSession}/fork`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fork_point_event_id: events[Math.min(replayIdx, events.length - 1)].id }),
      })
      if (r.ok) {
        const d = await r.json()
        fetchSessions()
        if (d.id) selectSession(d.id)
      }
    } catch {} finally {
      setForking(false)
    }
  }, [apiBase, selectedSession, events, replayIdx, fetchSessions, selectSession])

  // ── Replay ─────────────────────────────────────────────────

  useEffect(() => {
    if (!replayPlaying || !replayMode) {
      if (replayTimer.current) clearInterval(replayTimer.current)
      return
    }
    replayTimer.current = setInterval(() => {
      setReplayIdx(prev => {
        if (prev >= events.length - 1) {
          setReplayPlaying(false)
          return prev
        }
        return prev + 1
      })
    }, replaySpeed)
    return () => { if (replayTimer.current) clearInterval(replayTimer.current) }
  }, [replayPlaying, replayMode, replaySpeed, events.length])

  // ── Display Events ─────────────────────────────────────────

  const displayEvents = view === "search" && searchResults ? searchResults :
    replayMode ? events.slice(0, replayIdx + 1) : events

  // 跨会话分组（"哪个session有error的tool_call"）：扁平命中按session聚合，
  // 组头吃/search/sessions的GROUP BY真值（searchGroupMeta）
  const searchGroups = view === "search" && searchResults
    ? groupEventsBySession(searchResults)
    : null

  // 回放×树协同（2026-10-04，"回放与树互斥"销账）：已揭示事件id集=
  // events[0..replayIdx]，SpanTreeView按此渐进揭示（与扁平回放的
  // events.slice同语义——同一进度两种视图各吃各的渲染面）
  const revealedSpanIds = replayMode
    ? new Set(events.slice(0, replayIdx + 1).map(ev => String(ev.id)))
    : null

  // 扁平面携带过滤（2026-10-05 02:18轮遗留#2销账）：搜索→钻取在无trace树
  // 会话/列表视图同样生效——命中保留（原始序号不重排）+未命中剪掉+命中计数
  // +来源chip，与树面"一个查询两个面一致"（filterFlatEvents单一真源；filter
  // 未携带/未生效=恒等，既有扁平列表形态零变化——回归钉）。
  const flatFiltered = filterFlatEvents(displayEvents, carriedTreeFilter)
  const flatFilterActive = flatFiltered.active

  // ── Render ─────────────────────────────────────────────────

  return (
      <PageLayout title="Trajectory">
        
    <div className="px-3 lg:px-5 py-4 lg:py-5 h-full overflow-auto">
      {/* Title bar */}
      <div className="flex items-center gap-2 lg:gap-3 mb-4 lg:mb-5">
        {view !== "list" && (
          <button
            onClick={() => { setView("list"); setSearchResults(null) }}
            className="p-1.5 rounded-md border border-border hover:bg-muted transition-colors"
          >
            <ArrowLeft size={16} />
          </button>
        )}
        <BarChart3 size={20} className="text-primary" />
        <h2 className="text-lg lg:text-xl font-bold">
        {t("trajectory.title")}
        </h2>
        <button
          onClick={() => { fetchStats(); fetchSessions() }}
          className="ml-auto px-2.5 py-1.5 rounded-md border border-border text-xs text-muted-foreground hover:bg-muted flex items-center gap-1 transition-colors"
        >
          <RefreshCw size={12} /> {t("trajectory.refresh")}
        </button>
        <button
          onClick={() => {
            if (view === "analytics") { setView("list") }
            else { setView("analytics"); fetchAnalytics() }
          }}
          className={cn(
            "px-2.5 py-1.5 rounded-md text-xs flex items-center gap-1 transition-colors",
            view === "analytics"
              ? "border border-primary text-primary bg-primary/10"
              : "border border-border text-muted-foreground hover:bg-muted"
          )}
        >
          <BarChart3 size={12} /> {t("trajectory.analytics")}
        </button>
      </div>

      {/* Stats */}
      <StatsBar stats={stats} loading={loading} />

      {/* Search */}
      <SearchBar
        query={query}
        onQueryChange={setQuery}
        eventTypes={eventTypes}
        selectedTypes={selectedTypes}
        onTypeToggle={toggleType}
        onSearch={doSearch}
        status={searchStatus}
        onStatusChange={setSearchStatus}
      />

      {/* Main content */}
      {view === "analytics" ? (
        <div style={{ padding: "0 0 20px" }}>
          {analyticsLoading ? (
            <div style={{ textAlign: "center", padding: 40, color: "hsl(var(--muted-foreground))" }}>
              <RefreshCw size={24} style={{ animation: "spin 1s linear infinite", marginBottom: 8 }} />
              <p style={{ fontSize: 14 }}>{t("trajectory.loadingAnalytics")}</p>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
              {/* Token Usage Summary */}
              {tokenAnalytics && (
                <div style={{
                  padding: 16, borderRadius: 10,
                  background: "linear-gradient(135deg, hsl(var(--primary) / 0.05), hsl(var(--primary) / 0.02))",
                  border: "1px solid hsl(var(--border))",
                }}>
                  <h3 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 12px", color: "hsl(var(--foreground))", display: "flex", alignItems: "center", gap: 8 }}>
                    <Zap size={16} style={{ color: "#f59e0b" }} /> {t("trajectory.tokenUsageDays", { days: tokenAnalytics.summary.days_tracked })}
                  </h3>
                  <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                    {[
                      { label: t("trajectory.totalTokens"), value: formatTokens(tokenAnalytics.summary.total_tokens), color: "#f59e0b" },
                      { label: t("trajectory.totalEvents"), value: tokenAnalytics.summary.total_events, color: "#a855f7" },
                      { label: t("trajectory.avgDaily"), value: formatTokens(tokenAnalytics.summary.avg_daily_tokens), color: "#3b82f6" },
                    ].map(c => (
                      <div key={c.label} style={{
                        flex: 1, minWidth: 120, padding: "10px 14px", borderRadius: 8,
                        background: `${c.color}11`, border: `1px solid ${c.color}33`,
                      }}>
                        <div style={{ fontSize: 10, color: "hsl(var(--muted-foreground))", textTransform: "uppercase", letterSpacing: "0.05em" }}>{c.label}</div>
                        <div style={{ fontSize: 20, fontWeight: 700, color: c.color, lineHeight: 1.3 }}>{c.value}</div>
                      </div>
                    ))}
                  </div>
                  {/* Daily chart (text-based bar chart) */}
                  {tokenAnalytics.daily.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                      <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 60 }}>
                        {tokenAnalytics.daily.slice(0, 14).reverse().map(d => {
                          const maxTok = Math.max(...tokenAnalytics.daily.map(x => x.tokens), 1)
                          const pct = (d.tokens / maxTok) * 100
                          return (
                            <div key={d.day} title={`${d.day}: ${d.tokens} tokens`} style={{
                              flex: 1, height: `${Math.max(4, pct)}%`, borderRadius: "2px 2px 0 0",
                              background: "hsl(var(--primary))", opacity: 0.7, minWidth: 4,
                            }} />
                          )
                        })}
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4 }}>
                        <span style={{ fontSize: 9, color: "hsl(var(--muted-foreground))" }}>{tokenAnalytics.daily[tokenAnalytics.daily.length - 1]?.day}</span>
                        <span style={{ fontSize: 9, color: "hsl(var(--muted-foreground))" }}>{tokenAnalytics.daily[0]?.day}</span>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Tool Usage */}
              {toolAnalytics.length > 0 && (
                <div style={{
                  padding: 16, borderRadius: 10,
                  background: "hsl(var(--background))",
                  border: "1px solid hsl(var(--border))",
                }}>
                  <h3 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 12px", color: "hsl(var(--foreground))", display: "flex", alignItems: "center", gap: 8 }}>
                    <Wrench size={16} style={{ color: "#f59e0b" }} /> {t("trajectory.toolUsageFrequency")}
                  </h3>
                  <div style={{ overflow: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                      <thead>
                        <tr style={{ borderBottom: "1px solid hsl(var(--border))" }}>
                          {[t("trajectory.tool"), t("trajectory.usage"), t("trajectory.successRate"), t("trajectory.avgDuration"), t("trajectory.tokens")].map(h => (
                            <th key={h} style={{ padding: "6px 8px", textAlign: "left", color: "hsl(var(--muted-foreground))", fontWeight: 500, fontSize: 10, textTransform: "uppercase" }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {toolAnalytics.map(t => (
                          <tr key={t.tool_name} style={{ borderBottom: "1px solid hsl(var(--border) / 0.5)" }}>
                            <td style={{ padding: "6px 8px", fontWeight: 500, color: "hsl(var(--foreground))" }}>{t.tool_name}</td>
                            <td style={{ padding: "6px 8px", color: "hsl(var(--foreground))" }}>{t.usage_count}</td>
                            <td style={{ padding: "6px 8px" }}>
                              <span style={{
                                padding: "1px 6px", borderRadius: 4, fontSize: 10,
                                background: t.success_rate >= 90 ? "#22c55e22" : t.success_rate >= 70 ? "#f59e0b22" : "#ef444422",
                                color: t.success_rate >= 90 ? "#22c55e" : t.success_rate >= 70 ? "#f59e0b" : "#ef4444",
                              }}>{t.success_rate}%</span>
                            </td>
                            <td style={{ padding: "6px 8px", color: "hsl(var(--muted-foreground))" }}>{formatDuration(t.avg_duration_ms)}</td>
                            <td style={{ padding: "6px 8px", color: "hsl(var(--muted-foreground))" }}>{formatTokens(t.total_tokens)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Agent Performance */}
              {agentAnalytics.length > 0 && (
                <div style={{
                  padding: 16, borderRadius: 10,
                  background: "hsl(var(--background))",
                  border: "1px solid hsl(var(--border))",
                }}>
                  <h3 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 12px", color: "hsl(var(--foreground))", display: "flex", alignItems: "center", gap: 8 }}>
                    <Bot size={16} style={{ color: "#a855f7" }} /> {t("trajectory.agentPerformance")}
                  </h3>
                  <div style={{ overflow: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                      <thead>
                        <tr style={{ borderBottom: "1px solid hsl(var(--border))" }}>
                          {[t("trajectory.agent"), t("trajectory.events"), t("trajectory.successRate"), t("trajectory.tokens"), t("trajectory.avgDuration")].map(h => (
                            <th key={h} style={{ padding: "6px 8px", textAlign: "left", color: "hsl(var(--muted-foreground))", fontWeight: 500, fontSize: 10, textTransform: "uppercase" }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {agentAnalytics.map(a => (
                          <tr key={a.agent_id} style={{ borderBottom: "1px solid hsl(var(--border) / 0.5)" }}>
                            <td style={{ padding: "6px 8px", fontWeight: 500, color: "hsl(var(--foreground))" }}>{a.agent_id}</td>
                            <td style={{ padding: "6px 8px", color: "hsl(var(--foreground))" }}>{a.event_count}</td>
                            <td style={{ padding: "6px 8px" }}>
                              <span style={{
                                padding: "1px 6px", borderRadius: 4, fontSize: 10,
                                background: a.success_rate >= 90 ? "#22c55e22" : a.success_rate >= 70 ? "#f59e0b22" : "#ef444422",
                                color: a.success_rate >= 90 ? "#22c55e" : a.success_rate >= 70 ? "#f59e0b" : "#ef4444",
                              }}>{a.success_rate}%</span>
                            </td>
                            <td style={{ padding: "6px 8px", color: "hsl(var(--muted-foreground))" }}>{formatTokens(a.total_tokens)}</td>
                            <td style={{ padding: "6px 8px", color: "hsl(var(--muted-foreground))" }}>{formatDuration(a.avg_duration_ms)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {toolAnalytics.length === 0 && agentAnalytics.length === 0 && !tokenAnalytics && (
                <div style={{ textAlign: "center", padding: 40, color: "hsl(var(--muted-foreground))" }}>
                  <BarChart3 size={32} style={{ opacity: 0.3, marginBottom: 8 }} />
                  <p style={{ fontSize: 14 }}>{t("trajectory.noAnalyticsData")}</p>
                  <p style={{ fontSize: 12, opacity: 0.7 }}>{t("trajectory.analyticsWillAppear")}</p>
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
      <div style={{ display: "flex", gap: 16, minHeight: 400, position: "relative" }}>
        {/* Left: Session List — sidebar-style sliding on mobile, inline on desktop */}
        {view === "list" && (
          <div style={{ width: "100%" }}>
            <div style={{
                display: "grid",
                gridTemplateColumns: isMobile ? "1fr" : "repeat(auto-fill, minmax(320px, 1fr))",
                gap: 8,
              }}>
                {loading ? (
                  <p style={{ color: "hsl(var(--muted-foreground))", fontSize: 13 }}>{t("trajectory.loadingAnalytics")}</p>
                ) : sessions.length === 0 ? (
                  <div style={{
                    gridColumn: "1 / -1", textAlign: "center", padding: 40,
                    color: "hsl(var(--muted-foreground))",
                  }}>
                    <Activity size={32} style={{ opacity: 0.3, marginBottom: 8 }} />
                    <p style={{ fontSize: 14 }}>{t("trajectory.noTrajectorySessions")}</p>
                    <p style={{ fontSize: 12, opacity: 0.7 }}>
                      {t("trajectory.sessionsWillAppear")}
                    </p>
                  </div>
                ) : (
                  sessions.map(s => (
                    <SessionCard
                      key={s.id}
                      session={s}
                      selected={selectedSession === s.id}
                      onSelect={() => selectSession(s.id)}
                    />
                  ))
                )}
              </div>
          </div>
        )}

        {/* Detail view: sidebar session list */}
        {view === "detail" && !isMobile && (
          <div style={{
            width: 300, flexShrink: 0,
            borderRight: "1px solid hsl(var(--border))",
            paddingRight: 16,
          }}>
              <div>
                {sessions.map(s => (
                  <SessionCard
                    key={s.id}
                    session={s}
                    selected={selectedSession === s.id}
                    onSelect={() => selectSession(s.id)}
                  />
                ))}
              </div>
          </div>
        )}

        {/* Mobile: session list sidebar-style sliding for detail view */}
        {view === "detail" && isMobile && sessionListOpen && (
          <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setSessionListOpen(false)} aria-hidden="true" />
        )}
        {view === "detail" && isMobile && sessionListOpen && (
          <div
            className="absolute inset-y-0 left-0 z-10 h-full w-80 min-w-0 border-r border-border transition-[left] duration-200 ease-linear flex flex-col overflow-hidden bg-card"
            style={{ left: 0 }}
          >
            <div className="h-12 shrink-0 flex items-center px-3 border-b border-border">
              <span className="text-sm font-semibold">{t("trajectory.sessions")}</span>
            </div>
            <div className="flex-1 overflow-y-auto p-2">
              {sessions.map(s => (
                <SessionCard
                  key={s.id}
                  session={s}
                  selected={selectedSession === s.id}
                  onSelect={() => selectSession(s.id)}
                />
              ))}
            </div>
          </div>
        )}

        {/* Right: Event Timeline */}
        {(view === "detail" || view === "search") && (
          <div style={{ flex: 1, overflow: "auto" }}>
            {/* Toolbar */}
            {view === "detail" && events.length > 0 && (
              <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
                {isMobile && (
                  <button
                    onClick={() => setSessionListOpen(true)}
                    style={{
                      padding: "5px 12px", borderRadius: 6, fontSize: 12,
                      border: "1px solid hsl(var(--border))",
                      background: "transparent",
                      color: "hsl(var(--muted-foreground))",
                      cursor: "pointer", display: "flex", alignItems: "center", gap: 4,
                    }}
                  >
                    <PanelLeft size={12} /> {t("trajectory.sessions")}
                  </button>
                )}
                <button
                  onClick={() => { setReplayMode(!replayMode); setReplayIdx(0); setReplayPlaying(false) }}
                  style={{
                    padding: "5px 12px", borderRadius: 6, fontSize: 12,
                    border: `1px solid ${replayMode ? "hsl(var(--primary))" : "hsl(var(--border))"}`,
                    background: replayMode ? "hsl(var(--primary) / 0.1)" : "transparent",
                    color: replayMode ? "hsl(var(--primary))" : "hsl(var(--muted-foreground))",
                    cursor: "pointer", display: "flex", alignItems: "center", gap: 4,
                  }}
                >
                  <Play size={12} /> {t("trajectory.replay")}
                </button>
                <button
                  onClick={forkSession}
                  disabled={forking}
                  style={{
                    padding: "5px 12px", borderRadius: 6, fontSize: 12,
                    border: "1px solid hsl(var(--border))", background: "transparent",
                    color: "hsl(var(--muted-foreground))", cursor: "pointer",
                    display: "flex", alignItems: "center", gap: 4,
                    opacity: forking ? 0.5 : 1,
                  }}
                >
                  <GitBranch size={12} /> {forking ? t("trajectory.forking") : t("trajectory.fork")}
                </button>
                {/* span树/列表切换（Warp orchestration树，2026-10-02 P1）——
                    回放×树协同（2026-10-04）：切换视图不再打断回放，
                    树随回放进度渐进揭示 */}
                <button
                  onClick={() => { setTreeMode(!treeMode) }}
                  style={{
                    padding: "5px 12px", borderRadius: 6, fontSize: 12,
                    border: `1px solid ${treeMode ? "hsl(var(--primary))" : "hsl(var(--border))"}`,
                    background: treeMode ? "hsl(var(--primary) / 0.1)" : "transparent",
                    color: treeMode ? "hsl(var(--primary))" : "hsl(var(--muted-foreground))",
                    cursor: "pointer", display: "flex", alignItems: "center", gap: 4,
                  }}
                >
                  <Layers size={12} /> {treeMode ? t("trajectory.listView") : t("trajectory.treeView")}
                </button>
                <div style={{
                  marginLeft: "auto", fontSize: 12, color: "hsl(var(--muted-foreground))",
                  display: "flex", alignItems: "center", gap: 8,
                }}>
                  <span>{events.length} {t("trajectory.events")}</span>
                  {selectedSession && (
                    <span style={{
                      padding: "2px 8px", borderRadius: 4, fontSize: 10,
                      background: "hsl(var(--muted))", fontFamily: "monospace",
                    }}>
                      {selectedSession.slice(0, 8)}
                    </span>
                  )}
                </div>
              </div>
            )}

            {/* Replay Controls */}
            {replayMode && (
              <ReplayControls
                events={events}
                currentIdx={replayIdx}
                onStep={setReplayIdx}
                playing={replayPlaying}
                onTogglePlay={() => setReplayPlaying(!replayPlaying)}
                speed={replaySpeed}
                onSpeedChange={setReplaySpeed}
              />
            )}

            {/* Search results header */}
            {view === "search" && (
              <div style={{
                padding: "8px 12px", marginBottom: 12, borderRadius: 8,
                background: "hsl(var(--muted) / 0.3)",
                border: "1px solid hsl(var(--border))",
                fontSize: 13, color: "hsl(var(--muted-foreground))",
                display: "flex", alignItems: "center", gap: 8,
              }}>
                <Search size={14} />
                {searchResults?.length || 0} {t("trajectory.resultsFor", { count: searchResults?.length || 0, query })}
                {selectedTypes.size > 0 && ` (${t("trajectory.filtered", { types: Array.from(selectedTypes).join(", ") })})`}
                {searchGroups && searchGroups.length > 0 && (
                  <span style={{
                    marginLeft: "auto", padding: "1px 8px", borderRadius: 10, fontSize: 10,
                    background: "hsl(var(--primary) / 0.12)", color: "hsl(var(--primary))",
                  }}>
                    {t("trajectory.matchSessions", { count: searchGroups.length })}
                  </span>
                )}
              </div>
            )}

            {/* Events — span树优先（treeMode且trace数据在场），回放中=树随
                回放进度渐进揭示（revealedIds=已揭示事件id集）；搜索视图仍走
                扁平搜索结果，否则原有扁平事件列表逐字不变 */}
            {view === "detail" && treeMode && traceRoots && traceRoots.length > 0 ? (
              <SpanTreeView
                key={selectedSession}
                roots={traceRoots}
                revealedIds={revealedSpanIds}
                initialFilter={carriedTreeFilter}
                getEventColor={getEventColor}
                getEventIcon={getEventIcon}
              />
            ) : displayEvents.length === 0 ? (
              <div style={{
                textAlign: "center", padding: 40, color: "hsl(var(--muted-foreground))",
              }}>
                <Activity size={32} style={{ opacity: 0.3, marginBottom: 8 }} />
                <p style={{ fontSize: 14 }}>
                  {view === "search" ? t("trajectory.noMatchingEvents") : t("trajectory.noEventsInSession")}
                </p>
              </div>
            ) : searchGroups ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                {searchGroups.map(g => (
                  <div key={g.session_id}>
                    <SessionGroupHeader
                      sessionId={g.session_id}
                      visibleCount={g.visible_count}
                      meta={searchGroupMeta[g.session_id] || null}
                      onOpen={sid =>
                        // 搜索→树钻取"整合"：携带快照搜索条件进树（命中高亮/
                        // 未命中兄弟剪掉），两个搜索面对同一查询一致
                        selectSession(sid, searchCriteria ? treeFilterFromSearch(searchCriteria) : null)
                      }
                    />
                    <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
                      {g.events.map((ev, idx) => (
                        <EventCard
                          key={ev.id || idx}
                          event={ev}
                          idx={idx}
                          total={g.events.length}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {/* 扁平面携带过滤条（未携带/未生效=渲染null，既有形态零变化） */}
                <FlatFilterBar
                  filter={carriedTreeFilter}
                  fromSearch={flatFilterActive}
                  matchCount={flatFiltered.matchCount}
                  total={flatFiltered.total}
                  onClearAll={() => setCarriedTreeFilter(null)}
                />
                {flatFiltered.visible.length === 0 ? (
                  <div style={{
                    textAlign: "center", padding: 40, color: "hsl(var(--muted-foreground))",
                  }}>
                    <Activity size={32} style={{ opacity: 0.3, marginBottom: 8 }} />
                    <p style={{ fontSize: 14 }}>{t("trajectory.noMatchingEvents")}</p>
                  </div>
                ) : flatFiltered.visible.map(({ event: ev, idx }) => (
                  <EventCard
                    key={ev.id || idx}
                    event={ev}
                    idx={replayMode ? replayIdx : idx}
                    total={replayMode ? events.length : displayEvents.length}
                    highlightQuery={flatFilterActive ? (carriedTreeFilter?.query || "") : ""}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      )}
    </div>
  
      </PageLayout>
    )
}
