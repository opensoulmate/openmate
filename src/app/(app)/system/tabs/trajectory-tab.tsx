"use client"

import { useEffect, useState, useCallback, useRef } from "react"
import { cn } from "@/lib/utils"
import { getApiBaseUrl } from "@/lib/api-client"
import { useTranslation } from "react-i18next"
import { useIsMobile } from "@/hooks/use-mobile"
import { PanelLeft } from "lucide-react"
import {
  Activity, Search, GitBranch, Play, RefreshCw, Zap,
  BarChart3, Layers, Bot, Wrench, ArrowLeft,
} from "lucide-react"
import { SpanTreeView } from "../../trajectory/span-tree-view"
// 展示格式化+SpanNode类型=span-tree单一真源（本地副本已删，防双实现漂移）
import { formatDuration, formatTokens, type SpanNode, type TreeFilter } from "../../trajectory/span-tree"
import {
  buildSearchQueryString, filterFlatEvents, groupEventsBySession, treeFilterFromSearch,
  type CrossSearchQueryInput, type ServerSessionMatch,
} from "../../trajectory/cross-search"
import { SessionGroupHeader, FlatFilterBar } from "../../trajectory/cross-search-view"
// 共享展示组件/类型单一真源（2026-10-05 共享化去重：本文件与trajectory-client
// 此前逐字镜像SearchBar/EventCard等~515行，现两消费者import同一模块防漂移）
import {
  StatsBar, SearchBar, SessionCard, EventCard, ReplayControls, getEventColor, getEventIcon,
  type TrajectoryStats, type TrajectorySession, type TrajectoryEvent, type EventType,
} from "../../trajectory/trajectory-shared"

// ── Main Component ─────────────────────────────────────────────

export function TrajectoryTab() {
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

  // Span树状态（2026-10-02 P1，Warp orchestration树——/system tab第二消费者
  // 接树，与trajectory-client同款）：treeMode默认开=观察面优先，列表一键可回；
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
                  {/* Daily chart */}
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
        {/* Left: Session List */}
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
                {/* span树/列表切换（Warp orchestration树）——
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
  )
}
