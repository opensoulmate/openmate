"use client"

import { useEffect, useState, useCallback, useMemo } from "react"
import { useRouter } from "next/navigation"
import { useTranslation } from "react-i18next"
import dynamic from "next/dynamic"
import { assembleECharts } from "flint-chart"
import { getApiBaseUrl } from "@/lib/api-client"
import {
  History, Loader2, Search, RefreshCw, Trash2,
  MessageSquare, Clock, ChevronDown, ChevronRight, ChevronLeft,
  XCircle, Bot, Terminal, Smartphone, Timer,
  Link, Monitor, Wrench, Users, Star, Filter, X,
  Download, FileJson, FileText, Tag, Plus,
  PanelLeft, TrendingUp, BarChart3, PieChart as PieChartIcon, Activity,
} from "lucide-react"
import { useIsMobile } from "@/hooks/use-mobile"
import { PageLayout } from '@/components/page-layout';

import { EChart } from '@/components/echart';

interface Session {
  session_id: string
  title?: string
  user_id?: string
  created_at?: string
  updated_at?: string
  message_count?: number
  status?: string
  source?: string
  tags?: string[]
}

interface Message {
  id: number
  role: string
  content: string
  timestamp?: string
}

interface SessionDetail {
  session_id: string
  title?: string
  messages: Message[]
}

// Source → icon + color (labels come from i18n)
const SOURCE_META: Record<string, { icon: typeof Terminal; color: string; bg: string }> = {
  cli:    { icon: Terminal,    color: "text-green-400",  bg: "bg-green-900/20" },
  weixin: { icon: Smartphone,  color: "text-emerald-400", bg: "bg-emerald-900/20" },
  cron:   { icon: Timer,       color: "text-amber-400",  bg: "bg-amber-900/20" },
  acp:    { icon: Link,        color: "text-blue-400",   bg: "bg-blue-900/20" },
  tui:    { icon: Monitor,     color: "text-purple-400", bg: "bg-purple-900/20" },
  tool:   { icon: Wrench,      color: "text-orange-400", bg: "bg-orange-900/20" },
  subagent: { icon: Users,     color: "text-cyan-400",   bg: "bg-cyan-900/20" },
}

const SOURCE_LABELS: Record<string, string> = {
  cli: "sessions.sourceCli",
  weixin: "sessions.sourceWeixin",
  cron: "sessions.sourceCron",
  acp: "sessions.sourceAcp",
  tui: "sessions.sourceTui",
  tool: "sessions.sourceTool",
  subagent: "sessions.sourceSubagent",
}

const ALL_SOURCES = ["cli", "weixin", "cron", "acp", "tui", "tool", "subagent"]

// ── Session Dashboard Charts ──────────────────────────────

function SessionTrendChart({ sessions }: { sessions: Session[] }) {
  const option = useMemo(() => {
    const dateMap = new Map<string, number>();
    sessions.forEach(s => {
      if (!s.created_at) return;
      const key = new Date(s.created_at).toISOString().slice(0, 10);
      dateMap.set(key, (dateMap.get(key) ?? 0) + 1);
    });
    const dates: string[] = [];
    const daily: number[] = [];
    const cumulative: number[] = [];
    let total = 0;
    const today = new Date();
    for (let i = 29; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      dates.push(key.slice(5));
      const count = dateMap.get(key) ?? 0;
      daily.push(count);
      total += count;
      cumulative.push(total);
    }
    return assembleECharts({
      data: { values: dates.map((d, i) => ({ '日期': d, '累计': cumulative[i], '每日': daily[i] })) },
      semantic_types: { '日期': 'Temporal', '累计': 'Quantity', '每日': 'Quantity' },
      chart_spec: {
        chartType: 'Area Chart',
        encodings: { x: { field: '日期' }, y: { field: '累计' } },
        canvasSize: { width: 500, height: 200 },
      },
    });
  }, [sessions]);
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp size={14} className="text-cyan-400" />
        <h4 className="text-xs font-medium">会话趋势（30 天）</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

function TokenUsageChart({ sessions }: { sessions: Session[] }) {
  const option = useMemo(() => {
    const sorted = [...sessions]
      .filter(s => (s.message_count ?? 0) > 0)
      .sort((a, b) => (b.message_count ?? 0) - (a.message_count ?? 0))
      .slice(0, 10);
    const values = sorted.map(s => ({
      session: (s.title || s.session_id).slice(0, 12) + ((s.title || s.session_id).length > 12 ? '…' : ''),
      messages: s.message_count ?? 0,
    }));
    return assembleECharts({
      data: { values },
      semantic_types: { session: 'Nominal', messages: 'Quantity' },
      chart_spec: {
        chartType: 'Bar Chart',
        encodings: { x: { field: 'session' }, y: { field: 'messages' } },
        canvasSize: { width: 500, height: 200 },
      },
    });
  }, [sessions]);
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <BarChart3 size={14} className="text-blue-400" />
        <h4 className="text-xs font-medium">消息最多的会话</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

function ResponseTimeChart({ sessions }: { sessions: Session[] }) {
  const option = useMemo(() => {
    const buckets = new Map<string, number>();
    const labels = ['<1 min', '1-5 min', '5-30 min', '30 min-2h', '2-12h', '>12h'];
    labels.forEach(l => buckets.set(l, 0));
    sessions.forEach(s => {
      if (!s.created_at || !s.updated_at) return;
      const diffMin = (new Date(s.updated_at).getTime() - new Date(s.created_at).getTime()) / 60000;
      if (diffMin < 1) buckets.set('<1 min', (buckets.get('<1 min') ?? 0) + 1);
      else if (diffMin < 5) buckets.set('1-5 min', (buckets.get('1-5 min') ?? 0) + 1);
      else if (diffMin < 30) buckets.set('5-30 min', (buckets.get('5-30 min') ?? 0) + 1);
      else if (diffMin < 120) buckets.set('30 min-2h', (buckets.get('30 min-2h') ?? 0) + 1);
      else if (diffMin < 720) buckets.set('2-12h', (buckets.get('2-12h') ?? 0) + 1);
      else buckets.set('>12h', (buckets.get('>12h') ?? 0) + 1);
    });
    const values = labels.map(l => ({ bucket: l, count: buckets.get(l) ?? 0 }));
    return assembleECharts({
      data: { values },
      semantic_types: { bucket: 'Ordinal', count: 'Quantity' },
      chart_spec: {
        chartType: 'Bar Chart',
        encodings: { x: { field: 'bucket' }, y: { field: 'count' } },
        canvasSize: { width: 500, height: 200 },
      },
    });
  }, [sessions]);
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <Activity size={14} className="text-green-400" />
        <h4 className="text-xs font-medium">会话时长分布</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

function StatusPieChart({ sessions }: { sessions: Session[] }) {
  const option = useMemo(() => {
    const statusMap = new Map<string, number>();
    sessions.forEach(s => {
      const label = s.status || 'unknown';
      statusMap.set(label, (statusMap.get(label) ?? 0) + 1);
    });
    const values = Array.from(statusMap.entries()).map(([status, count]) => ({ status, count }));
    return assembleECharts({
      data: { values },
      semantic_types: { status: 'Nominal', count: 'Quantity' },
      chart_spec: {
        chartType: 'Pie Chart',
        encodings: { x: { field: 'status' }, y: { field: 'count' } },
        chartProperties: { innerRadius: 35 },
        canvasSize: { width: 300, height: 200 },
      },
    });
  }, [sessions]);
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <PieChartIcon size={14} className="text-amber-400" />
        <h4 className="text-xs font-medium">状态分布</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

function SessionDashboardCharts({ sessions }: { sessions: Session[] }) {
  if (sessions.length === 0) return null;
  return (
    <div className="space-y-3 lg:space-y-4">
      <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
        Session Analytics
      </h3>
      <div className="grid gap-3 lg:gap-4 lg:grid-cols-2">
        <SessionTrendChart sessions={sessions} />
        <TokenUsageChart sessions={sessions} />
        <ResponseTimeChart sessions={sessions} />
        <StatusPieChart sessions={sessions} />
      </div>
    </div>
  );
}

// localStorage helpers for favorites
function loadFavorites(): Set<string> {
  try {
    const saved = localStorage.getItem("sessions-favorites")
    if (saved) return new Set(JSON.parse(saved))
  } catch {}
  return new Set()
}

function saveFavorites(favs: Set<string>) {
  try { localStorage.setItem("sessions-favorites", JSON.stringify([...favs])) } catch {}
}

function groupBySource(sessions: Session[]) {
  const groups: Record<string, Session[]> = {}
  for (const s of sessions) {
    const src = s.source || "unknown"
    if (!groups[src]) groups[src] = []
    groups[src].push(s)
  }
  return groups
}

export function SessionsClient() {
  const { t } = useTranslation()
  const router = useRouter()
  const apiBase = getApiBaseUrl()
  const [sessions, setSessions] = useState<Session[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const [selectedSession, setSelectedSession] = useState<SessionDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)

  // Favorites state
  const [favorites, setFavorites] = useState<Set<string>>(new Set())
  const [showFavoritesOnly, setShowFavoritesOnly] = useState(false)

  // Source filter state
  const [activeSourceFilter, setActiveSourceFilter] = useState<string | null>(null)

  // Tag filter state
  const [activeTagFilter, setActiveTagFilter] = useState<string | null>(null)
  // Mobile: track if we're showing detail view
  const isMobile = useIsMobile()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(true)
  const [allTags, setAllTags] = useState<{name: string; count: number}[]>([])

  // Tag input state (for adding tags to sessions)
  const [taggingSession, setTaggingSession] = useState<string | null>(null)
  const [tagInput, setTagInput] = useState("")

  // Expand state: source-level (persisted to localStorage)
  const [agentExpanded, setAgentExpanded] = useState(true)
  const [sourceExpanded, setSourceExpanded] = useState<Record<string, boolean>>(() => {
    if (typeof window !== "undefined") {
      try {
        const saved = localStorage.getItem("sessions-sourceExpanded")
        if (saved) return JSON.parse(saved)
      } catch {}
    }
    return { cli: false, weixin: false, cron: false, acp: false, tui: false, tool: false, subagent: false }
  })

  // Load favorites from localStorage on mount
  useEffect(() => {
    setFavorites(loadFavorites())
  }, [])

  // Persist sourceExpanded to localStorage on change
  useEffect(() => {
    try { localStorage.setItem("sessions-sourceExpanded", JSON.stringify(sourceExpanded)) } catch {}
  }, [sourceExpanded])

  const toggleFavorite = useCallback((sessionId: string) => {
    setFavorites(prev => {
      const next = new Set(prev)
      if (next.has(sessionId)) {
        next.delete(sessionId)
      } else {
        next.add(sessionId)
      }
      saveFavorites(next)
      return next
    })
  }, [])

  const fetchSessions = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const params = new URLSearchParams()
      if (searchQuery.trim()) params.set("q", searchQuery.trim())
      if (activeTagFilter) params.set("tag", activeTagFilter)
      const qs = params.toString()
      const url = searchQuery.trim()
        ? `${apiBase}/api/sessions/search?${qs}`
        : `${apiBase}/api/sessions${qs ? `?${qs}` : ""}`
      const res = await fetch(url, { signal: AbortSignal.timeout(10000) })
      if (res.ok) {
        const data = await res.json()
        setSessions(Array.isArray(data) ? data : data.sessions || [])
      } else {
        setError(`加载会话失败：${res.status}`)
      }
    } catch (e: unknown) {
      setError(`Sessions error: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [apiBase, searchQuery, activeTagFilter])

  const fetchSessionDetail = async (sessionId: string) => {
    const session = sessions.find(s => s.session_id === sessionId)
    if (session?.source) {
      setSourceExpanded(prev => {
        if (prev[session.source!]) return prev
        const next: Record<string, boolean> = {}
        for (const key of ALL_SOURCES) next[key] = false
        next[session.source!] = true
        return next
      })
    }
    setSidebarOpen(false) // close mobile sidebar
    setDetailLoading(true)
    try {
      const token = typeof window !== "undefined" ? localStorage.getItem("openmate-token") : null
      const headers: Record<string, string> = {}
      if (token) headers["Authorization"] = `Bearer ${token}`
      const res = await fetch(`${apiBase}/api/sessions/${sessionId}/messages`, { signal: AbortSignal.timeout(10000), headers })
      if (res.ok) {
        const data = await res.json()
        setSelectedSession({
          session_id: sessionId,
          title: sessions.find(s => s.session_id === sessionId)?.title,
          messages: Array.isArray(data) ? data : data.messages || [],
        })
      }
    } catch (e: unknown) {
      console.error("Session detail error:", e)
    } finally {
      setDetailLoading(false)
    }
  }

  const deleteSession = async (sessionId: string) => {
    try {
      const res = await fetch(`${apiBase}/api/sessions/${sessionId}`, {
        method: "DELETE",
        signal: AbortSignal.timeout(10000),
      })
      if (res.ok) {
        setSessions(prev => prev.filter(s => s.session_id !== sessionId))
        // Also remove from favorites if present
        setFavorites(prev => {
          const next = new Set(prev)
          next.delete(sessionId)
          saveFavorites(next)
          return next
        })
        if (selectedSession?.session_id === sessionId) setSelectedSession(null)
        setDeleteConfirm(null)
      } else {
        setError(`Delete failed: ${res.status}`)
      }
    } catch (e: unknown) {
      setError(`Delete error: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  useEffect(() => {
    fetchSessions()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch all available tags
  const fetchTags = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/sessions/tags/all`, { signal: AbortSignal.timeout(5000) })
      if (res.ok) {
        const data = await res.json()
        setAllTags(data.tags || [])
      }
    } catch {}
  }, [apiBase])

  useEffect(() => { fetchTags() }, [fetchTags])

  // Add a tag to a session
  const addTag = useCallback(async (sessionId: string, tagName: string) => {
    const name = tagName.trim().toLowerCase()
    if (!name) return
    try {
      const res = await fetch(`${apiBase}/api/sessions/${sessionId}/tags`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag_name: name }),
        signal: AbortSignal.timeout(5000),
      })
      if (res.ok) {
        setSessions(prev => prev.map(s =>
          s.session_id === sessionId
            ? { ...s, tags: [...new Set([...(s.tags || []), name])].sort() }
            : s
        ))
        setTagInput("")
        setTaggingSession(null)
        fetchTags()
      }
    } catch {}
  }, [apiBase, fetchTags])

  // Remove a tag from a session
  const removeTag = useCallback(async (sessionId: string, tagName: string) => {
    try {
      const res = await fetch(`${apiBase}/api/sessions/${sessionId}/tags/${encodeURIComponent(tagName)}`, {
        method: "DELETE",
        signal: AbortSignal.timeout(5000),
      })
      if (res.ok) {
        setSessions(prev => prev.map(s =>
          s.session_id === sessionId
            ? { ...s, tags: (s.tags || []).filter(t => t !== tagName) }
            : s
        ))
        fetchTags()
      }
    } catch {}
  }, [apiBase, fetchTags])

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    fetchSessions()
  }

  const roleColor = (role: string) => {
    switch (role) {
      case "user": return "text-blue-400"
      case "assistant": return "text-green-400"
      case "system": return "text-yellow-400"
      default: return "text-muted-foreground"
    }
  }

  // Shared message list renderer (used by both mobile sliding panel and desktop inline)
  const renderMessageList = (fontSize: string = "text-sm") => {
    if (detailLoading) {
      return <div className="flex items-center justify-center h-40"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
    }
    if (!selectedSession || selectedSession.messages.length === 0) {
      return <div className="text-center py-12 text-muted-foreground text-xs lg:text-sm">{t("sessions.noMessages", "No messages in this session")}</div>
    }
    return selectedSession.messages.map((msg, i) => (
      <div key={msg.id || i} className={`flex gap-3 ${msg.role === "user" ? "justify-end" : ""}`}>
        {msg.role !== "user" && (
          <div className="w-7 h-7 rounded-full bg-muted flex items-center justify-center flex-shrink-0">
            <span className={`text-xs font-medium ${roleColor(msg.role)}`}>
              {msg.role === "assistant" ? "AI" : msg.role[0].toUpperCase()}
            </span>
          </div>
        )}
        <div className={`max-w-[75%] rounded-xl px-3.5 py-2.5 ${fontSize} ${
          msg.role === "user"
            ? "bg-blue-600/20 text-blue-100"
            : msg.role === "system"
            ? "bg-yellow-900/20 text-yellow-200 border border-yellow-800/30"
            : "bg-muted/50 text-foreground"
        }`}>
          <div className="whitespace-pre-wrap break-words">{msg.content}</div>
          {msg.timestamp && (
            <div className="text-[10px] text-muted-foreground/70 mt-1">{new Date(msg.timestamp).toLocaleString('zh-CN', { fractionalSecondDigits: 3 })}</div>
          )}
        </div>
        {msg.role === "user" && (
          <div className="w-7 h-7 rounded-full bg-blue-600/20 flex items-center justify-center flex-shrink-0">
            <span className="text-xs font-medium text-blue-400">U</span>
          </div>
        )}
      </div>
    ))
  }

  // ── Export helpers ──────────────────────────────────────────
  const exportSession = useCallback((format: "json" | "markdown") => {
    if (!selectedSession) return
    const title = selectedSession.title || selectedSession.session_id
    let content: string
    let ext: string
    let mime: string

    if (format === "json") {
      content = JSON.stringify({
        session_id: selectedSession.session_id,
        title,
        exported_at: new Date().toISOString(),
        message_count: selectedSession.messages.length,
        messages: selectedSession.messages,
      }, null, 2)
      ext = "json"
      mime = "application/json"
    } else {
      const lines = [`# ${title}`, "", `Session: \`${selectedSession.session_id}\``, `Exported: ${new Date().toLocaleString('zh-CN', { fractionalSecondDigits: 3 })}`, "", "---", ""]
      for (const msg of selectedSession.messages) {
        const roleLabel = msg.role === "user" ? "**你**" : msg.role === "assistant" ? "**AI**" : `**${msg.role}**`
        const ts = msg.timestamp ? ` _(${new Date(msg.timestamp).toLocaleString('zh-CN', { fractionalSecondDigits: 3 })})_` : ""
        lines.push(`### ${roleLabel}${ts}`, "", msg.content, "")
      }
      content = lines.join("\n")
      ext = "md"
      mime = "text/markdown"
    }

    const blob = new Blob([content], { type: mime })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `session-${selectedSession.session_id}.${ext}`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }, [selectedSession])

  // Apply filters: favorites + source
  const filteredSessions = useMemo(() => {
    let result = sessions
    if (showFavoritesOnly) {
      result = result.filter(s => favorites.has(s.session_id))
    }
    if (activeSourceFilter) {
      result = result.filter(s => s.source === activeSourceFilter)
    }
    return result
  }, [sessions, showFavoritesOnly, favorites, activeSourceFilter])

  const groups = groupBySource(filteredSessions)
  const totalMessages = filteredSessions.reduce((sum, s) => sum + (s.message_count || 0), 0)
  const favCount = sessions.filter(s => favorites.has(s.session_id)).length

  // Count sessions per source (from unfiltered list)
  const sourceCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const s of sessions) {
      const src = s.source || "unknown"
      counts[src] = (counts[src] || 0) + 1
    }
    return counts
  }, [sessions])

  const activeFilterCount = (showFavoritesOnly ? 1 : 0) + (activeSourceFilter ? 1 : 0) + (activeTagFilter ? 1 : 0)

  return (
      <PageLayout title="会话">
        
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-2 lg:px-6 h-12 border-b border-border">
        <div className="flex items-center gap-3">
          <button onClick={() => isMobile ? setSidebarOpen(true) : setDesktopSidebarOpen(v => !v)} className="shrink-0 p-1.5 -ml-1 rounded-lg hover:bg-muted touch-manipulation" aria-label="切换侧边栏">
            <PanelLeft className="w-4 h-4 text-muted-foreground" />
          </button>
          <History className="w-6 h-6 text-cyan-400" />
          <div>
            <h1 className="text-lg lg:text-xl font-semibold text-foreground">{t("sessions.title", "Sessions")}</h1>
            <p className="text-xs lg:text-sm text-muted-foreground">{t("sessions.subtitle", "Browse and manage conversation sessions")}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {activeFilterCount > 0 && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-cyan-900/30 text-cyan-400">
              {activeFilterCount} {t("sessions.filtersActive", "filters")}
            </span>
          )}
          <span className="text-xs lg:text-sm text-muted-foreground">{filteredSessions.length} {t("sessions.count", "sessions")}</span>
          <button onClick={fetchSessions} className="p-2 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors">
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Session List — sidebar-style sliding on mobile, inline on desktop */}
        {isMobile && sidebarOpen && (
          <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setSidebarOpen(false)} aria-hidden="true" />
        )}
        {isMobile ? (
          <div
            className="absolute inset-y-0 left-0 z-10 h-full w-80 min-w-0 border-r border-border transition-[left] duration-200 ease-linear flex flex-col overflow-hidden bg-card"
            style={{ left: sidebarOpen ? 0 : -320 }}
          >
              <div className="h-12 shrink-0 flex items-center px-3 border-b border-border">
                <span className="text-sm font-semibold text-foreground flex items-center gap-2">
                  <History className="w-4 h-4 text-cyan-400" />
                  {t("sessions.title", "Sessions")}
                </span>
              </div>
              {/* Search */}
              <form onSubmit={handleSearch} className="px-3 h-12 flex items-center border-b border-border gap-2">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
                    placeholder={t("sessions.searchPlaceholder", "Search sessions...")}
                    className="w-full pl-10 pr-4 py-2 bg-muted border border-border rounded-lg text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-blue-500" />
                </div>
              </form>
              {/* Filter Bar */}
              <div className="px-3 py-2 border-b border-border space-y-2">
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setShowFavoritesOnly(v => !v)}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                      showFavoritesOnly
                        ? "bg-yellow-900/30 text-yellow-400 border border-yellow-700/50"
                        : "bg-muted text-muted-foreground border border-border hover:text-foreground hover:border-border"
                    }`}
                  >
                    <Star className={`w-3.5 h-3.5 ${showFavoritesOnly ? "fill-yellow-400" : ""}`} />
                    {t("sessions.favorites", "Favorites")}
                    {favCount > 0 && <span className="text-[10px] opacity-70">({favCount})</span>}
                  </button>
                  {activeFilterCount > 0 && (
                    <button
                      onClick={() => { setShowFavoritesOnly(false); setActiveSourceFilter(null); setActiveTagFilter(null) }}
                      className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                    >
                      <X className="w-3 h-3" />
                      {t("sessions.clearFilters", "Clear")}
                    </button>
                  )}
                </div>
                <div className="flex flex-nowrap gap-1.5 overflow-x-auto">
                  {ALL_SOURCES.map(src => {
                    const count = sourceCounts[src] || 0
                    if (count === 0) return null
                    const meta = SOURCE_META[src]
                    const Icon = meta.icon
                    const active = activeSourceFilter === src
                    return (
                      <button
                        key={src}
                        onClick={() => setActiveSourceFilter(prev => prev === src ? null : src)}
                        className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] transition-colors ${
                          active
                            ? `${meta.bg} ${meta.color} border border-current/30 font-medium`
                            : "bg-muted/60 text-muted-foreground border border-border hover:text-foreground"
                        }`}
                      >
                        <Icon className="w-3 h-3" />
                        {t(SOURCE_LABELS[src] || src)}
                        <span className="opacity-60">{count}</span>
                      </button>
                    )
                  })}
                </div>
                {/* Tag filter chips (mobile) */}
                {allTags.length > 0 && (
                  <div className="flex flex-nowrap gap-1.5 overflow-x-auto">
                    <div className="flex items-center gap-1 mr-1">
                      <Tag className="w-3 h-3 text-muted-foreground/70" />
                      <span className="text-[10px] text-muted-foreground/70">{t("sessions.tags", "Tags")}:</span>
                    </div>
                    {allTags.map(tag => {
                      const active = activeTagFilter === tag.name
                      return (
                        <button
                          key={tag.name}
                          onClick={() => setActiveTagFilter(prev => prev === tag.name ? null : tag.name)}
                          className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] transition-colors ${
                            active
                              ? "bg-indigo-900/30 text-indigo-400 border border-indigo-700/30 font-medium"
                              : "bg-muted/60 text-muted-foreground border border-border hover:text-foreground"
                          }`}
                        >
                          <Tag className="w-3 h-3" />
                          {tag.name}
                          <span className="opacity-60">{tag.count}</span>
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
              {/* Session list */}
              <div className="flex-1 overflow-y-auto">
                {loading ? (
                  <div className="flex items-center justify-center h-40"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
                ) : error ? (
                  <div className="p-4 text-center text-red-400 text-xs">{error}</div>
                ) : filteredSessions.length === 0 ? (
                  <div className="text-center py-12 space-y-2">
                    <p className="text-muted-foreground text-xs">
                      {showFavoritesOnly
                        ? t("sessions.noFavorites", "No favorite sessions yet")
                        : t("sessions.noSessions", "No sessions found")}
                    </p>
                  </div>
                ) : (
                  <div className="py-2">
                    <button
                      className="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-muted/40 transition-colors"
                      onClick={() => setAgentExpanded(v => !v)}
                    >
                      {agentExpanded ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
                      <Bot className="w-5 h-5 text-cyan-400" />
                      <span className="text-xs font-semibold text-foreground">Hermes Agent</span>
                      <span className="ml-auto text-xs text-muted-foreground">{t("sessions.sessionSummary", { count: filteredSessions.length, messages: totalMessages })}</span>
                    </button>
                    {agentExpanded && (
                      <div className="ml-2 border-l border-border">
                        {ALL_SOURCES.map(src => {
                          const items = groups[src]
                          if (!items || items.length === 0) return null
                          const meta = SOURCE_META[src] || { icon: MessageSquare, label: src, color: "text-muted-foreground", bg: "bg-muted/20" }
                          const Icon = meta.icon
                          const expanded = sourceExpanded[src] ?? false
                          return (
                            <div key={src}>
                              <button
                                className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted/30 transition-colors"
                                onClick={() => setSourceExpanded(prev => {
                                  const willExpand = !prev[src]
                                  const next: Record<string, boolean> = {}
                                  if (willExpand) {
                                    for (const key of ALL_SOURCES) next[key] = false
                                    next[src] = true
                                  }
                                  return next
                                })}
                              >
                                {expanded ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground/70" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground/70" />}
                                <Icon className={`w-4 h-4 ${meta.color}`} />
                                <span className="text-xs font-medium text-foreground">{t(SOURCE_LABELS[src] || src)}</span>
                                <span className="ml-auto text-[11px] text-muted-foreground/70">{items.length}</span>
                              </button>
                              {expanded && items.map(session => {
                                const isFav = favorites.has(session.session_id)
                                return (
                                  <div
                                    key={session.session_id}
                                    className={`group flex items-center gap-2 pl-8 pr-3 py-2.5 cursor-pointer transition-colors ${
                                      selectedSession?.session_id === session.session_id
                                        ? "bg-[rgba(124,58,237,0.12)] text-[#7c3aed] border-l-2 border-[#7c3aed]"
                                        : "hover:bg-muted/20 border-l-2 border-transparent"
                                    }`}
                                    onClick={() => fetchSessionDetail(session.session_id)}
                                  >
                                    <button
                                      onClick={(e) => { e.stopPropagation(); toggleFavorite(session.session_id) }}
                                      className={`p-1 lg:p-0.5 rounded transition-colors flex-shrink-0 touch-manipulation ${
                                        isFav ? "text-yellow-400 hover:text-yellow-300" : "text-muted-foreground/40 hover:text-yellow-400"
                                      }`}
                                    >
                                      <Star className={`w-3.5 h-3.5 ${isFav ? "fill-yellow-400" : ""}`} />
                                    </button>
                                    <div className="flex-1 min-w-0">
                                      <div className={`text-xs truncate ${selectedSession?.session_id === session.session_id ? "text-[#7c3aed]" : "text-foreground"}`}>
                                        {session.title || session.session_id.replace(/^20\d{6}_\d{6}_/, "")}
                                      </div>
                                      <div className={`flex items-center gap-2 mt-0.5 text-[10px] ${
                                        selectedSession?.session_id === session.session_id ? "text-purple-400/60" : "text-muted-foreground/70"
                                      }`}>
                                        {session.message_count !== undefined && (
                                          <span className="flex items-center gap-0.5"><MessageSquare className="w-2.5 h-2.5" /> {session.message_count}</span>
                                        )}
                                        {session.created_at && (
                                          <span className="flex items-center gap-0.5"><Clock className="w-2.5 h-2.5" /> {new Date(session.created_at).toLocaleDateString()}</span>
                                        )}
                                      </div>
                                      {/* Tag chips (mobile) */}
                                      {(session.tags && session.tags.length > 0) && (
                                        <div className="flex flex-wrap gap-1 mt-1">
                                          {session.tags.map(tag => (
                                            <span
                                              key={tag}
                                              onClick={(e) => { e.stopPropagation(); setActiveTagFilter(prev => prev === tag ? null : tag) }}
                                              className="inline-flex items-center gap-0.5 px-1.5 py-0 rounded text-[9px] bg-indigo-900/20 text-indigo-400 border border-indigo-800/30 cursor-pointer hover:bg-indigo-900/40 transition-colors"
                                            >
                                              <Tag className="w-2 h-2" />
                                              {tag}
                                              <button
                                                onClick={(e) => { e.stopPropagation(); removeTag(session.session_id, tag) }}
                                                className="ml-0.5 hover:text-red-400 transition-colors"
                                                title={t("sessions.removeTag", "Remove tag")}
                                              >
                                                <X className="w-2 h-2" />
                                              </button>
                                            </span>
                                          ))}
                                        </div>
                                      )}
                                      {/* Tag input (mobile) */}
                                      {taggingSession === session.session_id && (
                                        <div className="mt-1" onClick={e => e.stopPropagation()}>
                                          <form
                                            onSubmit={e => { e.preventDefault(); addTag(session.session_id, tagInput) }}
                                            className="flex items-center gap-1"
                                          >
                                            <input
                                              autoFocus
                                              value={tagInput}
                                              onChange={e => setTagInput(e.target.value)}
                                              onKeyDown={e => { if (e.key === "Escape") { setTaggingSession(null); setTagInput("") } }}
                                              placeholder={t("sessions.tagPlaceholder", "tag name...")}
                                              className="w-20 px-1.5 py-0.5 text-[10px] bg-muted border border-border rounded text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:border-indigo-500"
                                            />
                                            <button type="submit" className="p-0.5 text-indigo-400 hover:text-indigo-300">
                                              <Plus className="w-3 h-3" />
                                            </button>
                                          </form>
                                        </div>
                                      )}
                                    </div>
                                    <div className="flex items-center gap-1">
                                      {/* Add tag button (mobile) */}
                                      <button
                                        onClick={(e) => { e.stopPropagation(); setTaggingSession(prev => prev === session.session_id ? null : session.session_id); setTagInput("") }}
                                        className={`p-1 rounded transition-colors ${
                                          taggingSession === session.session_id
                                            ? "bg-indigo-800/30 text-indigo-400"
                                            : "hover:bg-muted text-muted-foreground/70 hover:text-indigo-400"
                                        }`}
                                        title={t("sessions.addTag", "Add tag")}
                                      >
                                        <Tag className="w-3 h-3" />
                                      </button>
                                      {deleteConfirm === session.session_id ? (
                                        <div className="flex items-center gap-1">
                                          <button onClick={(e) => { e.stopPropagation(); deleteSession(session.session_id) }}
                                            className="px-1.5 py-0.5 bg-red-600 hover:bg-red-700 rounded text-[10px] text-white">
                                            {t("sessions.confirmDelete", "OK")}
                                          </button>
                                          <button onClick={(e) => { e.stopPropagation(); setDeleteConfirm(null) }}
                                            className="px-1.5 py-0.5 bg-muted hover:bg-muted rounded text-[10px] text-foreground">
                                            {t("sessions.cancel", "Cancel")}
                                          </button>
                                        </div>
                                      ) : (
                                        <button onClick={(e) => { e.stopPropagation(); setDeleteConfirm(session.session_id) }}
                                          className="p-1 rounded hover:bg-muted text-muted-foreground/70 hover:text-red-400 transition-colors">
                                          <Trash2 className="w-3 h-3" />
                                        </button>
                                      )}
                                    </div>
                                  </div>
                                )
                              })}
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
        ) : (
        <div
          className="shrink-0 border-r border-border flex flex-col overflow-hidden transition-all duration-300 ease-in-out"
          style={{ width: desktopSidebarOpen ? 320 : 0, borderWidth: desktopSidebarOpen ? 1 : 0 }}
        >
          {/* Search */}
          <form onSubmit={handleSearch} className="px-3 md:px-2 lg:px-4 h-12 flex items-center border-b border-border gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
                placeholder={t("sessions.searchPlaceholder", "Search sessions...")}
                className="w-full pl-10 pr-4 py-2 bg-muted border border-border rounded-lg text-xs lg:text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-blue-500" />
            </div>
          </form>

          {/* Filter Bar */}
          <div className="px-3 md:px-2 lg:px-4 py-2 border-b border-border space-y-2">
            {/* Favorites + clear row */}
            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowFavoritesOnly(v => !v)}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                  showFavoritesOnly
                    ? "bg-yellow-900/30 text-yellow-400 border border-yellow-700/50"
                    : "bg-muted text-muted-foreground border border-border hover:text-foreground hover:border-border"
                }`}
              >
                <Star className={`w-3.5 h-3.5 ${showFavoritesOnly ? "fill-yellow-400" : ""}`} />
                {t("sessions.favorites", "Favorites")}
                {favCount > 0 && <span className="text-[10px] opacity-70">({favCount})</span>}
              </button>

              <div className="flex items-center gap-1 ml-1">
                <Filter className="w-3 h-3 text-muted-foreground/70" />
                <span className="text-[10px] text-muted-foreground/70">{t("sessions.source", "Source")}:</span>
              </div>

              {activeFilterCount > 0 && (
                <button
                  onClick={() => { setShowFavoritesOnly(false); setActiveSourceFilter(null); setActiveTagFilter(null) }}
                  className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                >
                  <X className="w-3 h-3" />
                  {t("sessions.clearFilters", "Clear")}
                </button>
              )}
            </div>

            {/* Source filter chips */}
            <div className="flex flex-nowrap md:flex-wrap gap-1.5 overflow-x-auto">
              {ALL_SOURCES.map(src => {
                const count = sourceCounts[src] || 0
                if (count === 0) return null
                const meta = SOURCE_META[src]
                const Icon = meta.icon
                const active = activeSourceFilter === src
                return (
                  <button
                    key={src}
                    onClick={() => setActiveSourceFilter(prev => prev === src ? null : src)}
                    className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] transition-colors ${
                      active
                        ? `${meta.bg} ${meta.color} border border-current/30 font-medium`
                        : "bg-muted/60 text-muted-foreground border border-border hover:text-foreground"
                    }`}
                  >
                    <Icon className="w-3 h-3" />
                    {t(SOURCE_LABELS[src] || src)}
                    <span className="opacity-60">{count}</span>
                  </button>
                )
              })}
            </div>

            {/* Tag filter chips */}
            {allTags.length > 0 && (
              <div className="flex flex-nowrap md:flex-wrap gap-1.5 overflow-x-auto">
                <div className="flex items-center gap-1 mr-1">
                  <Tag className="w-3 h-3 text-muted-foreground/70" />
                  <span className="text-[10px] text-muted-foreground/70">{t("sessions.tags", "Tags")}:</span>
                </div>
                {allTags.map(tag => {
                  const active = activeTagFilter === tag.name
                  return (
                    <button
                      key={tag.name}
                      onClick={() => setActiveTagFilter(prev => prev === tag.name ? null : tag.name)}
                      className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] transition-colors ${
                        active
                          ? "bg-indigo-900/30 text-indigo-400 border border-indigo-700/30 font-medium"
                          : "bg-muted/60 text-muted-foreground border border-border hover:text-foreground"
                      }`}
                    >
                      <Tag className="w-3 h-3" />
                      {tag.name}
                      <span className="opacity-60">{tag.count}</span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          {/* Tree List */}
          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <div className="flex items-center justify-center h-40"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
            ) : error ? (
              <div className="p-4 text-center text-red-400 text-xs lg:text-sm">{error}</div>
            ) : filteredSessions.length === 0 ? (
              <div className="text-center py-12 space-y-2">
                <p className="text-muted-foreground text-xs lg:text-sm">
                  {showFavoritesOnly
                    ? t("sessions.noFavorites", "No favorite sessions yet")
                    : t("sessions.noSessions", "No sessions found")}
                </p>
                {showFavoritesOnly && (
                  <button
                    onClick={() => setShowFavoritesOnly(false)}
                    className="text-xs text-cyan-500 hover:text-cyan-400 transition-colors"
                  >
                    {t("sessions.showAll", "Show all sessions")}
                  </button>
                )}
              </div>
            ) : (
              <div className="py-2">
                {/* ── Hermes Agent ── */}
                <button
                  className="w-full flex items-center gap-2 px-2 lg:px-4 py-2.5 hover:bg-muted/40 transition-colors"
                  onClick={() => setAgentExpanded(v => !v)}
                >
                  {agentExpanded ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
                  <Bot className="w-5 h-5 text-cyan-400" />
                  <span className="text-xs lg:text-sm font-semibold text-foreground">Hermes Agent</span>
                  <span className="ml-auto text-xs text-muted-foreground">{t("sessions.sessionSummary", { count: filteredSessions.length, messages: totalMessages })}</span>
                </button>

                {agentExpanded && (
                  <div className="ml-2 border-l border-border">
                    {ALL_SOURCES.map(src => {
                      const items = groups[src]
                      if (!items || items.length === 0) return null
                      const meta = SOURCE_META[src] || { icon: MessageSquare, label: src, color: "text-muted-foreground", bg: "bg-muted/20" }
                      const Icon = meta.icon
                      const expanded = sourceExpanded[src] ?? false

                      return (
                        <div key={src}>
                          {/* Source group header */}
                          <button
                            className="w-full flex items-center gap-2 px-2 lg:px-4 py-2 hover:bg-muted/30 transition-colors"
                            onClick={() => setSourceExpanded(prev => {
                              const willExpand = !prev[src]
                              const next: Record<string, boolean> = {}
                              if (willExpand) {
                                for (const key of ALL_SOURCES) next[key] = false
                                next[src] = true
                              }
                              return next
                            })}
                          >
                            {expanded ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground/70" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground/70" />}
                            <Icon className={`w-4 h-4 ${meta.color}`} />
                            <span className="text-xs font-medium text-foreground">{t(SOURCE_LABELS[src] || src)}</span>
                            <span className="ml-auto text-[11px] text-muted-foreground/70">{items.length}</span>
                          </button>

                          {/* Sessions under this source */}
                          {expanded && items.map(session => {
                            const isFav = favorites.has(session.session_id)
                            return (
                              <div
                                key={session.session_id}
                                className={`group flex items-center gap-2 pl-8 lg:pl-14 pr-3 lg:pr-4 py-2.5 cursor-pointer transition-colors ${
                                  selectedSession?.session_id === session.session_id
                                    ? "bg-[rgba(124,58,237,0.12)] text-[#7c3aed] border-l-2 border-[#7c3aed] hover:bg-[rgba(124,58,237,0.18)]"
                                    : "hover:bg-muted/20 border-l-2 border-transparent"
                                }`}
                                onClick={() => fetchSessionDetail(session.session_id)}
                              >
                                {/* Favorite star — always visible on mobile, hover-reveal on desktop */}
                                <button
                                  onClick={(e) => { e.stopPropagation(); toggleFavorite(session.session_id) }}
                                  className={`p-1 lg:p-0.5 rounded transition-colors flex-shrink-0 touch-manipulation ${
                                    isFav
                                      ? "text-yellow-400 hover:text-yellow-300"
                                      : "text-muted-foreground/40 lg:opacity-0 lg:group-hover:opacity-100 hover:text-yellow-400"
                                  }`}
                                  title={isFav ? t("sessions.removeFavorite", "Remove from favorites") : t("sessions.addFavorite", "Add to favorites")}
                                >
                                  <Star className={`w-3.5 h-3.5 ${isFav ? "fill-yellow-400" : ""}`} />
                                </button>

                                <div className="flex-1 min-w-0">
                                  <div className={`text-xs truncate ${
                                    selectedSession?.session_id === session.session_id ? "text-[#7c3aed]" : "text-foreground"
                                  }`}>
                                    {session.title || session.session_id.replace(/^20\d{6}_\d{6}_/, "")}
                                  </div>
                                  <div className={`flex items-center gap-2 mt-0.5 text-[10px] ${
                                    selectedSession?.session_id === session.session_id ? "text-purple-400/60" : "text-muted-foreground/70"
                                  }`}>
                                    {session.message_count !== undefined && (
                                      <span className="flex items-center gap-0.5">
                                        <MessageSquare className="w-2.5 h-2.5" /> {session.message_count}
                                      </span>
                                    )}
                                    {session.created_at && (
                                      <span className="flex items-center gap-0.5">
                                        <Clock className="w-2.5 h-2.5" /> {new Date(session.created_at).toLocaleDateString()}
                                      </span>
                                    )}
                                  </div>
                                  {/* Tag chips */}
                                  {(session.tags && session.tags.length > 0) && (
                                    <div className="flex flex-wrap gap-1 mt-1">
                                      {session.tags.map(tag => (
                                        <span
                                          key={tag}
                                          onClick={(e) => { e.stopPropagation(); setActiveTagFilter(prev => prev === tag ? null : tag) }}
                                          className="inline-flex items-center gap-0.5 px-1.5 py-0 rounded text-[9px] bg-indigo-900/20 text-indigo-400 border border-indigo-800/30 cursor-pointer hover:bg-indigo-900/40 transition-colors"
                                        >
                                          <Tag className="w-2 h-2" />
                                          {tag}
                                          <button
                                            onClick={(e) => { e.stopPropagation(); removeTag(session.session_id, tag) }}
                                            className="ml-0.5 hover:text-red-400 transition-colors"
                                            title={t("sessions.removeTag", "Remove tag")}
                                          >
                                            <X className="w-2 h-2" />
                                          </button>
                                        </span>
                                      ))}
                                    </div>
                                  )}
                                  {/* Tag input */}
                                  {taggingSession === session.session_id && (
                                    <div className="mt-1" onClick={e => e.stopPropagation()}>
                                      <form
                                        onSubmit={e => { e.preventDefault(); addTag(session.session_id, tagInput) }}
                                        className="flex items-center gap-1"
                                      >
                                        <input
                                          autoFocus
                                          value={tagInput}
                                          onChange={e => setTagInput(e.target.value)}
                                          onKeyDown={e => { if (e.key === "Escape") { setTaggingSession(null); setTagInput("") } }}
                                          placeholder={t("sessions.tagPlaceholder", "tag name...")}
                                          className="w-20 px-1.5 py-0.5 text-[10px] bg-muted border border-border rounded text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:border-indigo-500"
                                        />
                                        <button type="submit" className="p-0.5 text-indigo-400 hover:text-indigo-300">
                                          <Plus className="w-3 h-3" />
                                        </button>
                                      </form>
                                    </div>
                                  )}
                                </div>
                                <div className="flex items-center gap-1">
                                  {/* Add tag button */}
                                  <button
                                    onClick={(e) => { e.stopPropagation(); setTaggingSession(prev => prev === session.session_id ? null : session.session_id); setTagInput("") }}
                                    className={`p-1 rounded transition-colors opacity-100 sm:opacity-0 sm:group-hover:opacity-100 ${
                                      taggingSession === session.session_id
                                        ? "bg-indigo-800/30 text-indigo-400"
                                        : "hover:bg-muted text-muted-foreground/70 hover:text-indigo-400"
                                    }`}
                                    title={t("sessions.addTag", "Add tag")}
                                  >
                                    <Tag className="w-3 h-3" />
                                  </button>
                                  {deleteConfirm === session.session_id ? (
                                    <div className="flex items-center gap-1">
                                      <button onClick={(e) => { e.stopPropagation(); deleteSession(session.session_id) }}
                                        className="px-1.5 py-0.5 bg-red-600 hover:bg-red-700 rounded text-[10px] text-white">
                                        {t("sessions.confirmDelete", "OK")}
                                      </button>
                                      <button onClick={(e) => { e.stopPropagation(); setDeleteConfirm(null) }}
                                        className="px-1.5 py-0.5 bg-muted hover:bg-muted rounded text-[10px] text-foreground">
                                        {t("sessions.cancel", "Cancel")}
                                      </button>
                                    </div>
                                  ) : (
                                    <button onClick={(e) => { e.stopPropagation(); setDeleteConfirm(session.session_id) }}
                                      className="p-1 rounded hover:bg-muted text-muted-foreground/70 hover:text-red-400 transition-colors opacity-100 sm:opacity-0 sm:group-hover:opacity-100">
                                      <Trash2 className="w-3 h-3" />
                                    </button>
                                  )}
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        )}

        {/* Session Detail — sidebar-style sliding on mobile, inline on desktop */}
        {isMobile && selectedSession && (
          <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => { setSelectedSession(null); setSidebarOpen(true); }} aria-hidden="true" />
        )}
        {isMobile ? (
          selectedSession && (
            <div
              className="absolute inset-y-0 right-0 z-10 h-full w-[85vw] max-w-lg min-w-0 border-l border-border transition-[right] duration-200 ease-linear flex flex-col overflow-hidden bg-card"
              style={{ right: 0 }}
            >
                <div className="h-12 shrink-0 flex items-center justify-between px-2 border-b border-border">
                  <div className="flex items-center gap-2 min-w-0">
                    <button onClick={() => { setSelectedSession(null); setSidebarOpen(true); }} className="p-1.5 -ml-1 rounded-lg hover:bg-muted touch-manipulation">
                      <ChevronLeft className="w-4 h-4 text-muted-foreground" />
                    </button>
                    <span className="text-xs font-medium text-foreground truncate flex items-center gap-2">
                      <MessageSquare className="w-4 h-4 text-cyan-400 flex-shrink-0" />
                      {selectedSession.title || selectedSession.session_id}
                      <span className="text-xs text-muted-foreground flex-shrink-0">({selectedSession.messages.length})</span>
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 flex-shrink-0">
                    <button onClick={() => router.push(`/chat?session=${selectedSession.session_id}`)}
                      className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-cyan-600/20 text-cyan-400 hover:bg-cyan-600/30 text-xs transition-colors"
                      title={t("sessions.openChat", "Open in Chat")}>
                      <MessageSquare className="w-3 h-3" />
                    </button>
                    <button onClick={() => exportSession("json")}
                      className="p-2 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors touch-manipulation"
                      title="导出为 JSON">
                      <FileJson className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => exportSession("markdown")}
                      className="p-2 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors touch-manipulation"
                      title="导出为 Markdown">
                      <FileText className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
                <div className="flex-1 overflow-y-auto px-2 py-3 space-y-3">
                  {renderMessageList("text-xs")}
                </div>
            </div>
          )
        ) : (
          selectedSession ? (
            <div className="flex flex-1 min-w-0 flex-col overflow-hidden">
              <div className="flex items-center justify-between px-4 h-12 border-b border-border">
                <div className="flex items-center gap-2 min-w-0">
                  <MessageSquare className="w-4 h-4 text-cyan-400 flex-shrink-0" />
                  <span className="text-sm font-medium text-foreground truncate">
                    {selectedSession.title || selectedSession.session_id}
                  </span>
                  <span className="text-xs text-muted-foreground flex-shrink-0">({selectedSession.messages.length} {t("sessions.messages", "messages")})</span>
                </div>
                <div className="flex items-center gap-1 flex-shrink-0">
                  <button onClick={() => router.push(`/chat?session=${selectedSession.session_id}`)}
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-cyan-600/20 text-cyan-400 hover:bg-cyan-600/30 text-xs transition-colors"
                    title={t("sessions.openChat", "Open in Chat")}>
                    <MessageSquare className="w-3 h-3" />
                    {t("sessions.openChat", "Chat")}
                  </button>
                  <button onClick={() => exportSession("json")}
                    className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                    title="导出为 JSON">
                    <FileJson className="w-3.5 h-3.5" />
                  </button>
                  <button onClick={() => exportSession("markdown")}
                    className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                    title="导出为 Markdown">
                    <FileText className="w-3.5 h-3.5" />
                  </button>
                  <button onClick={() => setSelectedSession(null)}
                    className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground">
                    <XCircle className="w-4 h-4" />
                  </button>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
                {renderMessageList("text-sm")}
              </div>
            </div>
          ) : (
            <div className="flex-1 overflow-y-auto p-3 lg:p-6">
              <SessionDashboardCharts sessions={sessions} />
              {sessions.length === 0 && (
                <div className="flex-1 flex items-center justify-center text-muted-foreground py-16">
                  <div className="text-center">
                    <MessageSquare className="w-12 h-12 mx-auto mb-3 opacity-30" />
                    <p className="text-sm">{t("sessions.selectSession", "Select a session to view")}</p>
                  </div>
                </div>
              )}
            </div>
          )
        )}
      </div>
    </div>
  
      </PageLayout>
    )
}
