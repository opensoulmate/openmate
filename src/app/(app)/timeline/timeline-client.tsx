"use client"

import { useEffect, useState, useCallback } from "react"
import { useTranslation } from "react-i18next"
import { cn } from "@/lib/utils"
import { getApiBaseUrl } from "@/lib/api-client"
import {
  Clock, Search, RefreshCw, Filter, Trash2, ChevronDown, ChevronRight,
  Activity, BarChart3, Layers, FolderSync, AlertCircle, CheckCircle, Info,
  XCircle, Zap, Settings, Database,
} from "lucide-react"
import { PageLayout } from '@/components/page-layout';

// ── Types ──────────────────────────────────────────────────────

interface TimelineEvent {
  event_id: string
  organ: string
  emoji: string
  event_type: string
  summary: string
  detail: Record<string, unknown>
  timestamp: number
  collected_at: number
  time_ago: string
}

interface TimelineStats {
  total_events: number
  recent_24h: number
  by_organ: Record<string, number>
  by_type: Record<string, number>
  time_range: { earliest: number | null; latest: number | null }
  hourly_distribution: Array<{ hours_ago: number; count: number }>
}

interface OrganInfo {
  organ: string
  emoji: string
  count: number
  last_event: number
  last_event_ago: string
}

interface TypeInfo {
  event_type: string
  count: number
  last_seen: number
}

// ── Helpers ────────────────────────────────────────────────────

const TYPE_COLORS: Record<string, string> = {
  info: "#3b82f6",
  warning: "#f59e0b",
  error: "#ef4444",
  success: "#22c55e",
  health: "#06b6d4",
  system: "#8b5cf6",
  user: "#ec4899",
  agent: "#a855f7",
  task: "#f97316",
  event: "#6366f1",
}

const TYPE_ICONS: Record<string, React.ElementType> = {
  info: Info,
  warning: AlertCircle,
  error: XCircle,
  success: CheckCircle,
  health: Activity,
  system: Settings,
  agent: Zap,
  task: CheckCircle,
}

function getTypeColor(type: string): string {
  for (const [key, color] of Object.entries(TYPE_COLORS)) {
    if (type.toLowerCase().includes(key)) return color
  }
  return "#6b7280"
}

function getTypeIcon(type: string): React.ElementType {
  for (const [key, icon] of Object.entries(TYPE_ICONS)) {
    if (type.toLowerCase().includes(key)) return icon
  }
  return Activity
}

function formatTimestamp(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleString('zh-CN', {
    month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    fractionalSecondDigits: 3,
  })
}

// ── Main Component ─────────────────────────────────────────────

export function TimelineClient() {
  const { t } = useTranslation()
  const api = getApiBaseUrl()

  // Data
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [stats, setStats] = useState<TimelineStats | null>(null)
  const [organs, setOrgans] = useState<OrganInfo[]>([])
  const [types, setTypes] = useState<TypeInfo[]>([])

  // UI state
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [filterOrgan, setFilterOrgan] = useState<string>("")
  const [filterType, setFilterType] = useState<string>("")
  const [activeTab, setActiveTab] = useState<"events" | "stats" | "organs">("events")
  const [expandedEvent, setExpandedEvent] = useState<string | null>(null)
  const [showFilters, setShowFilters] = useState(false)
  const [syncing, setSyncing] = useState(false)

  // ── Fetch Data ─────────────────────────────────────────────

  const fetchEvents = useCallback(async () => {
    try {
      const params = new URLSearchParams()
      if (filterOrgan) params.set("organ", filterOrgan)
      if (filterType) params.set("event_type", filterType)
      if (searchQuery) params.set("search", searchQuery)
      params.set("limit", "200")

      const res = await fetch(`${api}/api/timeline/events?${params}`)
      if (!res.ok) throw new Error(`${res.status}`)
      const data = await res.json()
      setEvents(data.events || [])
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to fetch events")
    }
  }, [api, filterOrgan, filterType, searchQuery])

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${api}/api/timeline/stats`)
      if (res.ok) setStats(await res.json())
    } catch {}
  }, [api])

  const fetchOrgans = useCallback(async () => {
    try {
      const res = await fetch(`${api}/api/timeline/organs`)
      if (res.ok) {
        const data = await res.json()
        setOrgans(data.organs || [])
      }
    } catch {}
  }, [api])

  const fetchTypes = useCallback(async () => {
    try {
      const res = await fetch(`${api}/api/timeline/types`)
      if (res.ok) {
        const data = await res.json()
        setTypes(data.types || [])
      }
    } catch {}
  }, [api])

  const loadAll = useCallback(async () => {
    setLoading(true)
    await Promise.all([fetchEvents(), fetchStats(), fetchOrgans(), fetchTypes()])
    setLoading(false)
  }, [fetchEvents, fetchStats, fetchOrgans, fetchTypes])

  useEffect(() => { loadAll() }, [loadAll])

  // ── Actions ────────────────────────────────────────────────

  const handleSync = async () => {
    setSyncing(true)
    try {
      await fetch(`${api}/api/timeline/sync`, { method: "POST" })
      await loadAll()
    } catch {}
    setSyncing(false)
  }

  const handleDeleteEvent = async (eventId: string) => {
    try {
      await fetch(`${api}/api/timeline/events/${eventId}`, { method: "DELETE" })
      setEvents(prev => prev.filter(e => e.event_id !== eventId))
    } catch {}
  }

  const handleClearOld = async (days: number) => {
    if (!confirm(t("timeline.confirmClearEvents", { days }))) return
    try {
      await fetch(`${api}/api/timeline/clear`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ older_than_days: days }),
      })
      await loadAll()
    } catch {}
  }

  // ── Render ─────────────────────────────────────────────────

  return (
      <PageLayout title="时间线">
        
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 lg:px-6 py-4 border-b border-border">
        <div className="flex items-center gap-2 lg:gap-3">
          <Clock className="w-5 h-5 text-primary" />
          <h1 className="text-lg font-semibold">{t("timeline.title")}</h1>
          {stats && (
            <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
              {t("timeline.eventsSummary", { total: stats.total_events, recent: stats.recent_24h })}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleSync}
            disabled={syncing}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-accent transition-colors",
              syncing && "opacity-50"
            )}
          >
            <FolderSync className={cn("w-3.5 h-3.5", syncing && "animate-spin")} />
            {t("timeline.syncEvents")}
          </button>
          <button
            onClick={() => loadAll()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-accent transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            {t("timeline.refreshAction")}
          </button>
          <button
            onClick={() => handleClearOld(30)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-destructive/30 text-destructive hover:bg-destructive/10 transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5" />
            {t("timeline.cleanOldEvents")}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 px-3 lg:px-6 pt-3">
        {(["events", "stats", "organs"] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={cn(
              "px-3 py-1.5 text-xs rounded-lg transition-colors",
              activeTab === tab
                ? "bg-primary/10 text-primary font-medium"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            {tab === "events" && <><Activity className="w-3.5 h-3.5 inline mr-1" />{t("timeline.eventsTab")}</>}
            {tab === "stats" && <><BarChart3 className="w-3.5 h-3.5 inline mr-1" />{t("timeline.statsTab")}</>}
            {tab === "organs" && <><Layers className="w-3.5 h-3.5 inline mr-1" />{t("timeline.organsTab")}</>}
          </button>
        ))}
      </div>

      {/* Search & Filters */}
      {activeTab === "events" && (
        <div className="px-3 lg:px-6 py-3 border-b border-border space-y-2">
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <input
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                onKeyDown={e => e.key === "Enter" && fetchEvents()}
                placeholder={t("timeline.searchPlaceholder")}
                className="w-full pl-8 pr-3 py-1.5 text-xs rounded-lg border border-border bg-background outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
            <button
              onClick={() => setShowFilters(!showFilters)}
              className={cn(
                "flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-lg border border-border hover:bg-accent",
                showFilters && "bg-accent"
              )}
            >
              <Filter className="w-3.5 h-3.5" />
              {t("timeline.filterAction")}
              {(filterOrgan || filterType) && (
                <span className="w-1.5 h-1.5 rounded-full bg-primary" />
              )}
            </button>
          </div>
          {showFilters && (
            <div className="flex items-center gap-2 lg:gap-3">
              <select
                value={filterOrgan}
                onChange={e => { setFilterOrgan(e.target.value); }}
                className="text-xs rounded-lg border border-border bg-background px-2 py-1.5 outline-none"
              >
                <option value="">{t("timeline.allOrgans")}</option>
                {organs.map(o => (
                  <option key={o.organ} value={o.organ}>{o.emoji} {o.organ} ({o.count})</option>
                ))}
              </select>
              <select
                value={filterType}
                onChange={e => { setFilterType(e.target.value); }}
                className="text-xs rounded-lg border border-border bg-background px-2 py-1.5 outline-none"
              >
                <option value="">{t("timeline.allTypes")}</option>
                {types.map(t => (
                  <option key={t.event_type} value={t.event_type}>{t.event_type} ({t.count})</option>
                ))}
              </select>
              {(filterOrgan || filterType) && (
                <button
                  onClick={() => { setFilterOrgan(""); setFilterType(""); }}
                  className="text-xs text-muted-foreground hover:text-foreground"
                >
                  {t("timeline.clearFilter")}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4">
        {loading ? (
          <div className="flex items-center justify-center py-20 text-muted-foreground">
            <RefreshCw className="w-4 h-4 animate-spin mr-2" /> {t("timeline.loadingAction")}
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-20 text-destructive">
            <AlertCircle className="w-8 h-8 mb-2" />
            <p className="text-xs lg:text-sm">{error}</p>
            <button onClick={loadAll} className="mt-3 text-xs text-primary hover:underline">{t("timeline.retryAction")}</button>
          </div>
        ) : activeTab === "events" ? (
          <EventsList
            events={events}
            expandedEvent={expandedEvent}
            onToggleExpand={id => setExpandedEvent(expandedEvent === id ? null : id)}
            onDelete={handleDeleteEvent}
          />
        ) : activeTab === "stats" ? (
          <StatsPanel stats={stats} types={types} />
        ) : (
          <OrgansPanel organs={organs} onSelectOrgan={o => { setFilterOrgan(o); setActiveTab("events"); }} />
        )}
      </div>
    </div>
  
      </PageLayout>
    )
}

// ── Events List ────────────────────────────────────────────────

function EventsList({
  events, expandedEvent, onToggleExpand, onDelete,
}: {
  events: TimelineEvent[]
  expandedEvent: string | null
  onToggleExpand: (id: string) => void
  onDelete: (id: string) => void
}) {
  const { t } = useTranslation();
  if (events.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
        <Clock className="w-10 h-10 mb-3 opacity-30" />
        <p className="text-xs lg:text-sm">{t("timeline.noEvents")}</p>
        <p className="text-xs mt-1">{t("timeline.syncHint")}</p>
      </div>
    )
  }

  // Group events by date
  const grouped: Record<string, TimelineEvent[]> = {}
  for (const ev of events) {
    const d = new Date(ev.timestamp * 1000)
    const dateKey = d.toLocaleDateString(undefined, { year: "numeric", month: "2-digit", day: "2-digit" })
    if (!grouped[dateKey]) grouped[dateKey] = []
    grouped[dateKey].push(ev)
  }

  return (
    <div className="space-y-3 lg:space-y-6">
      {Object.entries(grouped).map(([date, evts]) => (
        <div key={date}>
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-medium text-muted-foreground bg-muted px-2 py-0.5 rounded-full">{date}</span>
            <span className="text-xs text-muted-foreground">{t("timeline.eventsCount", { count: evts.length })}</span>
          </div>
          <div className="relative pl-6 border-l-2 border-border space-y-1">
            {evts.map(ev => {
              const isExpanded = expandedEvent === ev.event_id
              const color = getTypeColor(ev.event_type)
              const Icon = getTypeIcon(ev.event_type)

              return (
                <div key={ev.event_id} className="relative group">
                  {/* Timeline dot */}
                  <div
                    className="absolute -left-[31px] top-2 w-3 h-3 rounded-full border-2 border-background"
                    style={{ backgroundColor: color }}
                  />

                  <div
                    className={cn(
                      "rounded-lg border border-border hover:border-primary/30 transition-colors cursor-pointer",
                      isExpanded && "border-primary/40 bg-primary/5"
                    )}
                  >
                    <div
                      className="flex items-center gap-2 lg:gap-3 px-3 py-2"
                      onClick={() => onToggleExpand(ev.event_id)}
                    >
                      <span className="text-base">{ev.emoji || "📌"}</span>
                      <Icon className="w-3.5 h-3.5 flex-shrink-0" style={{ color }} />
                      <span className="text-xs font-medium text-foreground flex-1 truncate">{ev.summary}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ backgroundColor: color + "20", color }}>
                        {ev.event_type}
                      </span>
                      <span className="text-[10px] text-muted-foreground">{ev.organ}</span>
                      <span className="text-[10px] text-muted-foreground">{ev.time_ago}</span>
                      <button
                        onClick={e => { e.stopPropagation(); onDelete(ev.event_id); }}
                        className="opacity-100 sm:opacity-0 sm:group-hover:opacity-100 p-0.5 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-all"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                      {isExpanded ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />}
                    </div>

                    {isExpanded && (
                      <div className="px-3 pb-3 border-t border-border pt-2 space-y-2">
                        <div className="flex items-center gap-2 lg:gap-4 text-[10px] text-muted-foreground">
                          <span>ID: {ev.event_id}</span>
                          <span>{t("timeline.timeLabel")} {formatTimestamp(ev.timestamp)}</span>
                          <span>{t("timeline.collectedLabel")} {formatTimestamp(ev.collected_at)}</span>
                        </div>
                        {Object.keys(ev.detail).length > 0 && (
                          <pre className="text-xs bg-muted rounded p-2 overflow-x-auto max-h-48">
                            {JSON.stringify(ev.detail, null, 2)}
                          </pre>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Stats Panel ────────────────────────────────────────────────

function StatsPanel({ stats, types }: { stats: TimelineStats | null; types: TypeInfo[] }) {
  const { t } = useTranslation();
  if (!stats) return <p className="text-xs lg:text-sm text-muted-foreground">{t("timeline.noStats")}</p>

  const maxHourly = Math.max(...stats.hourly_distribution.map(h => h.count), 1)

  return (
    <div className="space-y-3 lg:space-y-6">
      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-3">
        {[
          { label: t("timeline.totalEventsLabel"), value: stats.total_events, icon: Database },
          { label: t("timeline.recent24hLabel"), value: stats.recent_24h, icon: Clock },
          { label: t("timeline.organCountLabel"), value: Object.keys(stats.by_organ).length, icon: Layers },
          { label: t("timeline.eventTypeLabel"), value: Object.keys(stats.by_type).length, icon: Activity },
        ].map(card => (
          <div key={card.label} className="rounded-lg border border-border p-3">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <card.icon className="w-3.5 h-3.5" />
              <span className="text-xs">{card.label}</span>
            </div>
            <p className="text-xl lg:text-2xl font-bold">{card.value}</p>
          </div>
        ))}
      </div>

      {/* Hourly distribution */}
      <div className="rounded-lg border border-border p-3 lg:p-4">
        <h3 className="text-xs lg:text-sm font-medium mb-3">{t("timeline.eventsByHour")}</h3>
        <div className="flex items-end gap-1 h-24">
          {[...stats.hourly_distribution].reverse().map(h => (
            <div key={h.hours_ago} className="flex-1 flex flex-col items-center gap-1">
              <div
                className="w-full bg-primary/20 rounded-t"
                style={{ height: `${(h.count / maxHourly) * 80}px`, minHeight: h.count > 0 ? "4px" : "0" }}
              />
              {h.hours_ago % 6 === 0 && (
                <span className="text-[9px] text-muted-foreground">{h.hours_ago}h</span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* By type */}
      {types.length > 0 && (
        <div className="rounded-lg border border-border p-3 lg:p-4">
          <h3 className="text-xs lg:text-sm font-medium mb-3">{t("timeline.eventsByType")}</h3>
          <div className="space-y-2">
            {types.slice(0, 15).map(typ => {
              const maxCount = types[0]?.count || 1
              return (
                <div key={typ.event_type} className="flex items-center gap-2">
                  <span className="text-xs w-24 truncate text-muted-foreground">{typ.event_type}</span>
                  <div className="flex-1 h-4 bg-muted rounded overflow-hidden">
                    <div
                      className="h-full rounded"
                      style={{
                        width: `${(typ.count / maxCount) * 100}%`,
                        backgroundColor: getTypeColor(typ.event_type),
                        opacity: 0.6,
                      }}
                    />
                  </div>
                  <span className="text-xs font-mono w-10 text-right">{typ.count}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Organs Panel ───────────────────────────────────────────────

function OrgansPanel({ organs, onSelectOrgan }: { organs: OrganInfo[]; onSelectOrgan: (organ: string) => void }) {
  const { t } = useTranslation();
  if (organs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
        <Layers className="w-10 h-10 mb-3 opacity-30" />
        <p className="text-xs lg:text-sm">{t("timeline.noOrganEvents")}</p>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2 lg:gap-3">
      {organs.map(o => (
        <button
          key={o.organ}
          onClick={() => onSelectOrgan(o.organ)}
          className="flex items-center gap-2 lg:gap-3 rounded-lg border border-border p-3 hover:border-primary/30 hover:bg-primary/5 transition-colors text-left"
        >
          <span className="text-2xl">{o.emoji || "🔧"}</span>
          <div className="flex-1 min-w-0">
            <p className="text-xs lg:text-sm font-medium truncate">{o.organ}</p>
            <p className="text-xs text-muted-foreground">{t("timeline.organEventSummary", { count: o.count, ago: o.last_event_ago })}</p>
          </div>
          <ChevronRight className="w-4 h-4 text-muted-foreground" />
        </button>
      ))}
    </div>
  )
}
