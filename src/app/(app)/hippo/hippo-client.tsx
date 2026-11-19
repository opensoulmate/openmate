"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  Brain, RefreshCw, Plus, Trash2, Play, Search,
  Activity, Clock, Archive, Zap, Settings,
  TrendingDown, BarChart3, Loader2,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface Memory {
  memory_id: string;
  session_id: string;
  content: string;
  importance: number;
  tags: string[];
  retention: number;
  access_count: number;
  archived: boolean;
  created_at: number;
  last_accessed_at: number;
}

interface Session {
  session_id: string;
  user_id: string;
  title: string;
  status: string;
  memory_count: number;
  created_at: number;
  last_active_at: number;
}

interface DecayConfig {
  strategy: string;
  half_life_hours: number;
  archive_threshold: number;
  forget_threshold: number;
}

interface HippoStats {
  memory: {
    total_memories: number;
    active: number;
    archived: number;
    sessions: number;
    total_accesses: number;
    usage_percent: number;
    strategy: string;
    half_life_hours: number;
  };
  sessions: {
    total_sessions: number;
    by_status: Record<string, number>;
    total_memories_tracked: number;
  };
}

function retentionColor(r: number): string {
  if (r >= 0.7) return "text-emerald-500";
  if (r >= 0.3) return "text-amber-500";
  return "text-red-500";
}

function retentionBar(r: number): string {
  if (r >= 0.7) return "bg-emerald-500";
  if (r >= 0.3) return "bg-amber-500";
  return "bg-red-500";
}

function statusColor(s: string): string {
  if (s === "active") return "text-emerald-500";
  if (s === "idle") return "text-amber-500";
  if (s === "expired") return "text-red-500";
  return "text-muted-foreground";
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function formatAge(ts: number, t: (key: string, opts?: Record<string, unknown>) => string): string {
  const hours = (Date.now() / 1000 - ts) / 3600;
  const count = hours < 1 ? Math.round(hours * 60) : hours < 24 ? Math.round(hours) : Math.round(hours / 24);
  if (hours < 1) return t("hippo.minutesAgo", { count }) || `${count}m ago`;
  if (hours < 24) return t("hippo.hoursAgo", { count }) || `${count}h ago`;
  return t("hippo.daysAgo", { count }) || `${count}d ago`;
}

export function HippoClient() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"memories" | "sessions" | "decay" | "simulate">("memories");
  const [stats, setStats] = useState<HippoStats | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [sessionList, setSessionList] = useState<Session[]>([]);
  const [decayConfig, setDecayConfig] = useState<DecayConfig | null>(null);
  const [searchSession, setSearchSession] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [loading, setLoading] = useState(false);

  // Create memory form
  const [newContent, setNewContent] = useState("");
  const [newImportance, setNewImportance] = useState(0.5);
  const [newSessionId, setNewSessionId] = useState("default");

  // Simulate form
  const [simAge, setSimAge] = useState(24);
  const [simImportance, setSimImportance] = useState(0.5);
  const [simAccess, setSimAccess] = useState(0);
  const [simResult, setSimResult] = useState<any>(null);

  const apiBase = getApiBaseUrl();

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/hippo/health`);
      setStats(await res.json());
    } catch {}
  }, [apiBase]);

  const fetchMemories = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (searchSession) params.set("session_id", searchSession);
      params.set("include_archived", String(includeArchived));
      params.set("limit", "100");
      const res = await fetch(`${apiBase}/api/hippo/memories?${params}`);
      const data = await res.json();
      setMemories(data.memories || []);
    } catch {}
  }, [apiBase, searchSession, includeArchived]);

  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/hippo/sessions`);
      const data = await res.json();
      setSessionList(data.sessions || []);
    } catch {}
  }, [apiBase]);

  const fetchDecayConfig = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/hippo/decay/config`);
      setDecayConfig(await res.json());
    } catch {}
  }, [apiBase]);

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  useEffect(() => {
    if (tab === "memories") fetchMemories();
    if (tab === "sessions") fetchSessions();
    if (tab === "decay" || tab === "simulate") fetchDecayConfig();
  }, [tab, fetchMemories, fetchSessions, fetchDecayConfig]);

  const handleCreateMemory = async () => {
    if (!newContent.trim()) return;
    setLoading(true);
    try {
      await fetch(`${apiBase}/api/hippo/memories`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: newSessionId,
          content: newContent,
          importance: newImportance,
        }),
      });
      setNewContent("");
      fetchMemories();
      fetchStats();
    } catch {} finally { setLoading(false); }
  };

  const handleDeleteMemory = async (id: string) => {
    try {
      await fetch(`${apiBase}/api/hippo/memories/${id}`, { method: "DELETE" });
      fetchMemories();
      fetchStats();
    } catch {}
  };

  const handleRunDecay = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/hippo/decay/run`, { method: "POST" });
      const data = await res.json();
      alert(t("hippo.decayComplete", { updated: data.updated, archived: data.archived, forgotten: data.forgotten }) || `Decay complete: updated ${data.updated}, archived ${data.archived}, forgotten ${data.forgotten}`);
      fetchMemories();
      fetchStats();
    } catch {} finally { setLoading(false); }
  };

  const handleSimulate = async () => {
    try {
      const params = new URLSearchParams({
        age_hours: String(simAge),
        importance: String(simImportance),
        access_count: String(simAccess),
      });
      const res = await fetch(`${apiBase}/api/hippo/decay/simulate?${params}`);
      setSimResult(await res.json());
    } catch {}
  };

  const handleLifecycleCheck = async () => {
    try {
      const res = await fetch(`${apiBase}/api/hippo/sessions/lifecycle-check`, { method: "POST" });
      const data = await res.json();
      alert(t("hippo.lifecycleResult", { checked: data.checked, idle: data.newly_idle, expired: data.newly_expired }) || `Lifecycle check: ${data.checked} sessions, ${data.newly_idle} newly idle, ${data.newly_expired} expired`);
      fetchSessions();
    } catch {}
  };

  const tabs = [
    { id: "memories" as const, label: t("hippo.memories") || "Memories", icon: Brain },
    { id: "sessions" as const, label: t("hippo.sessions") || "Sessions", icon: Clock },
    { id: "decay" as const, label: t("hippo.decay") || "Decay", icon: TrendingDown },
    { id: "simulate" as const, label: t("hippo.simulate") || "Simulation", icon: BarChart3 },
  ];

  return (
      <PageLayout title="海马体">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-indigo-500/10">
            <Brain size={20} className="text-indigo-500" />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg lg:text-xl font-semibold truncate">{t("hippo.title") || "Hippocampus · Memory Lifecycle"}</h1>
            <p className="text-xs lg:text-sm text-muted-foreground truncate">{t('organTag.hippo')}</p>
          </div>
        </div>
        <button onClick={() => { fetchStats(); tab === "memories" && fetchMemories(); tab === "sessions" && fetchSessions(); }}
          className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted transition-colors">
          <RefreshCw size={14} /> {t("common.refresh") || "Refresh"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4 lg:py-6 space-y-3 lg:space-y-6">
        {/* Stats */}
        {stats && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
            <StatCard icon={Brain} label={t("hippo.activeMemories") || "Active Memories"} value={String(stats.memory.active)}
              sub={`${stats.memory.total_memories} ${t("hippo.total") || "Total Memories"}`} color="text-indigo-500" bg="bg-indigo-500/10" />
            <StatCard icon={Archive} label={t("hippo.archived") || "Archived"} value={String(stats.memory.archived)}
              sub={`${stats.memory.usage_percent}% ${t("hippo.usage") || "Usage"}`} color="text-amber-500" bg="bg-amber-500/10" />
            <StatCard icon={Clock} label={t("hippo.sessions") || "Sessions"} value={String(stats.sessions.total_sessions)}
              sub={`${stats.sessions.by_status?.active || 0} ${t("hippo.active") || "Active"}`} color="text-emerald-500" bg="bg-emerald-500/10" />
            <StatCard icon={Activity} label={t("hippo.totalAccesses") || "Total Accesses"} value={String(stats.memory.total_accesses)}
              sub={`${t("hippo.strategy") || "Strategy"}: ${stats.memory.strategy}`} color="text-violet-500" bg="bg-violet-500/10" />
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-2">
          {tabs.map((tabItem) => (
            <button key={tabItem.id} onClick={() => setTab(tabItem.id)}
              className={cn("flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs lg:text-sm transition-colors",
                tab === tabItem.id ? "bg-indigo-500/10 text-indigo-600 font-medium" : "hover:bg-muted text-muted-foreground")}>
              <tabItem.icon size={14} /> {tabItem.label}
            </button>
          ))}
        </div>

        {/* Memories Tab */}
        {tab === "memories" && (
          <div className="space-y-4">
            {/* Create Memory */}
            <div className="rounded-xl border border-border p-3 lg:p-4 space-y-3">
              <h3 className="text-xs lg:text-sm font-medium">{t("hippo.createMemory") || "Create Memory"}</h3>
              <div className="flex flex-col sm:flex-row gap-2 lg:gap-3">
                <input value={newSessionId} onChange={(e) => setNewSessionId(e.target.value)}
                  placeholder={t("hippo.sessionId") || "Session ID"}
                  className="w-32 rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-indigo-500/20" />
                <input value={newContent} onChange={(e) => setNewContent(e.target.value)}
                  placeholder={t("hippo.contentPlaceholder") || "Memory content..."}
                  className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-indigo-500/20"
                  onKeyDown={(e) => e.key === "Enter" && handleCreateMemory()} />
                <div className="flex items-center gap-2">
                  <label className="text-xs text-muted-foreground">{t("hippo.importance") || "Importance"}</label>
                  <input type="range" min="0" max="1" step="0.1" value={newImportance}
                    onChange={(e) => setNewImportance(parseFloat(e.target.value))}
                    className="w-20" />
                  <span className="text-xs w-6">{newImportance}</span>
                </div>
                <button onClick={handleCreateMemory} disabled={loading}
                  className="rounded-lg bg-indigo-500 px-2 lg:px-4 py-2 text-xs lg:text-sm text-white hover:bg-indigo-600 disabled:opacity-50">
                  {loading ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                </button>
              </div>
            </div>

            {/* Filters */}
            <div className="flex items-center gap-2 lg:gap-3">
              <div className="relative flex-1 max-w-sm">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input value={searchSession} onChange={(e) => setSearchSession(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && fetchMemories()}
                  placeholder={t("hippo.filterSession") || "Filter by Session"}
                  className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-indigo-500/20" />
              </div>
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                <input type="checkbox" checked={includeArchived} onChange={(e) => { setIncludeArchived(e.target.checked); fetchMemories(); }} />
                {t("hippo.includeArchived") || "Include Archived"}
              </label>
              <span className="text-xs text-muted-foreground">{memories.length} {t("hippo.entries") || "Memory Entries"}</span>
            </div>

            {/* Memory List */}
            {memories.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Brain size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("hippo.noMemories") || "No memories yet"}</p>
              </div>
            ) : (
              <div className="space-y-2">
                {memories.map((m) => (
                  <div key={m.memory_id} className={cn("rounded-xl border border-border bg-card p-3 lg:p-4 space-y-2", m.archived && "opacity-60")}>
                    <div className="flex items-start justify-between">
                      <div className="flex-1 min-w-0">
                        <p className="text-xs lg:text-sm">{m.content}</p>
                        <div className="flex items-center gap-2 lg:gap-3 mt-2 text-xs text-muted-foreground">
                          <span className="font-mono">{m.session_id.slice(0, 8)}</span>
                          <span>{formatAge(m.created_at, t)}</span>
                          <span>{t("hippo.accessed") || "Last Accessed"}: {m.access_count}</span>
                          {m.archived && <span className="text-amber-500 font-medium">{t("hippo.archived") || "Archived"}</span>}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0 ml-4">
                        {/* Retention bar */}
                        <div className="flex flex-col items-end gap-1">
                          <span className={cn("text-xs font-medium", retentionColor(m.retention))}>
                            {Math.round(m.retention * 100)}%
                          </span>
                          <div className="w-16 h-1.5 rounded-full bg-muted overflow-hidden">
                            <div className={cn("h-full rounded-full", retentionBar(m.retention))}
                              style={{ width: `${m.retention * 100}%` }} />
                          </div>
                        </div>
                        {/* Importance */}
                        <div className="flex flex-col items-center">
                          <span className="text-[10px] text-muted-foreground">★</span>
                          <span className="text-xs">{m.importance}</span>
                        </div>
                        <button onClick={() => handleDeleteMemory(m.memory_id)}
                          className="rounded-md p-1.5 text-muted-foreground hover:text-red-500 hover:bg-red-500/10 transition-colors">
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                    {m.tags.length > 0 && (
                      <div className="flex gap-1">
                        {m.tags.map((tag) => (
                          <span key={tag} className="inline-block rounded-full bg-indigo-500/10 px-2 py-0.5 text-[10px] text-indigo-500">
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Sessions Tab */}
        {tab === "sessions" && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 lg:gap-3">
              <button onClick={handleLifecycleCheck}
                className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-xs lg:text-sm text-white hover:bg-indigo-600">
                <Zap size={14} /> {t("hippo.lifecycleCheck") || "Lifecycle Check"}
              </button>
              <button onClick={fetchSessions}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs lg:text-sm hover:bg-muted">
                <RefreshCw size={14} />
              </button>
            </div>

            {sessionList.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Clock size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("hippo.noSessions") || "No sessions yet"}</p>
              </div>
            ) : (
              <div className="rounded-xl border border-border overflow-x-auto">
                <table className="w-full text-xs lg:text-sm min-w-[500px]">
                  <thead>
                    <tr className="border-b border-border bg-muted/30">
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">ID</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t("hippo.title") || "Hippocampus · Memory Lifecycle"}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t("hippo.status") || "Status"}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t("hippo.memories") || "Memories"}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t("hippo.lastActive") || "Last Active"}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sessionList.map((s) => (
                      <tr key={s.session_id} className="border-b border-border last:border-0 hover:bg-muted/30">
                        <td className="px-2 lg:px-4 py-2.5 font-mono text-xs">{s.session_id.slice(0, 8)}</td>
                        <td className="px-2 lg:px-4 py-2.5">{s.title}</td>
                        <td className="px-2 lg:px-4 py-2.5">
                          <span className={cn("text-xs font-medium", statusColor(s.status))}>{s.status}</span>
                        </td>
                        <td className="px-2 lg:px-4 py-2.5 text-muted-foreground">{s.memory_count}</td>
                        <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground">{formatTime(s.last_active_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Decay Config Tab */}
        {tab === "decay" && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 lg:gap-3">
              <button onClick={handleRunDecay} disabled={loading}
                className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-2 lg:px-4 py-2 text-xs lg:text-sm text-white hover:bg-indigo-600 disabled:opacity-50">
                {loading ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                {t("hippo.runDecay") || "Run Decay"}
              </button>
            </div>

            {decayConfig && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-4">
                <h3 className="text-xs lg:text-sm font-semibold">{t("hippo.decayConfig") || "Decay Configuration"}</h3>
                <div className="grid grid-cols-2 gap-2 lg:gap-4">
                  <div>
                    <label className="text-xs text-muted-foreground">{t("hippo.strategy") || "Strategy"}</label>
                    <div className="mt-1 text-xs lg:text-sm font-medium">{decayConfig.strategy}</div>
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">{t("hippo.halfLife") || "Half-life (hours)"}</label>
                    <div className="mt-1 text-xs lg:text-sm font-medium">{decayConfig.half_life_hours}h</div>
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">{t("hippo.archiveThreshold") || "Archive Threshold"}</label>
                    <div className="mt-1 text-xs lg:text-sm font-medium">{Math.round(decayConfig.archive_threshold * 100)}%</div>
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">{t("hippo.forgetThreshold") || "Forget Threshold"}</label>
                    <div className="mt-1 text-xs lg:text-sm font-medium">{Math.round(decayConfig.forget_threshold * 100)}%</div>
                  </div>
                </div>

                {/* Decay curve visualization */}
                <div className="mt-4">
                  <h4 className="text-xs text-muted-foreground mb-2">{t("hippo.decayCurve") || "Decay Curve"}</h4>
                  <div className="flex items-end gap-0.5 h-24">
                    {Array.from({ length: 48 }, (_, i) => {
                      const h = i;
                      const r = Math.exp(-h / (decayConfig.half_life_hours / Math.log(2)));
                      return (
                        <div key={i} className="flex-1 flex flex-col items-center">
                          <div className={cn("w-full rounded-t", retentionBar(r))}
                            style={{ height: `${r * 96}px` }} />
                          {i % 12 === 0 && <span className="text-[8px] text-muted-foreground mt-1">{i}h</span>}
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Simulate Tab */}
        {tab === "simulate" && (
          <div className="space-y-4">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-4">
              <h3 className="text-xs lg:text-sm font-semibold">{t("hippo.simulateDecay") || "Simulate Decay"}</h3>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 lg:gap-4">
                <div>
                  <label className="text-xs text-muted-foreground">{t("hippo.ageHours") || "Age (hours)"}</label>
                  <input type="number" value={simAge} onChange={(e) => setSimAge(parseFloat(e.target.value) || 0)}
                    className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">{t("hippo.importance") || "Importance"}</label>
                  <input type="number" min="0" max="1" step="0.1" value={simImportance}
                    onChange={(e) => setSimImportance(parseFloat(e.target.value) || 0)}
                    className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">{t("hippo.accessCount") || "Access Count"}</label>
                  <input type="number" value={simAccess} onChange={(e) => setSimAccess(parseInt(e.target.value) || 0)}
                    className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                </div>
              </div>
              <button onClick={handleSimulate}
                className="rounded-lg bg-indigo-500 px-2 lg:px-4 py-2 text-xs lg:text-sm text-white hover:bg-indigo-600">
                <BarChart3 size={14} className="inline mr-1" />
                {t("hippo.runSimulation") || "Run Simulation"}
              </button>

              {simResult && (
                <div className="mt-4 rounded-lg border border-border p-3 lg:p-4 space-y-2">
                  <div className="flex items-center gap-2 lg:gap-4">
                    <div>
                      <span className="text-xs text-muted-foreground">{t("hippo.retention") || "Retention"}: </span>
                      <span className={cn("text-lg font-bold", retentionColor(simResult.retention))}>
                        {Math.round(simResult.retention * 100)}%
                      </span>
                    </div>
                    <div className="w-32 h-2 rounded-full bg-muted overflow-hidden">
                      <div className={cn("h-full rounded-full", retentionBar(simResult.retention))}
                        style={{ width: `${simResult.retention * 100}%` }} />
                    </div>
                  </div>
                  <div className="flex gap-2 lg:gap-4 text-xs">
                    <span className={simResult.should_archive ? "text-amber-500" : "text-muted-foreground"}>
                      {simResult.should_archive ? (t("hippo.shouldArchive") || "⚠️ Should Archive") : (t("hippo.noArchiveNeeded") || "✓ No Archive Needed")}
                    </span>
                    <span className={simResult.should_forget ? "text-red-500" : "text-muted-foreground"}>
                      {simResult.should_forget ? (t("hippo.shouldForget") || "🗑️ Should Forget") : (t("hippo.noForgetNeeded") || "✓ No Forget Needed")}
                    </span>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}

function StatCard({ icon: Icon, label, value, sub, color, bg }: {
  icon: React.ElementType; label: string; value: string; sub: string; color: string; bg: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <div className={cn("rounded-lg p-1.5", bg)}><Icon size={14} className={color} /></div>
      </div>
      <p className="text-xl lg:text-2xl font-bold">{value}</p>
      <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>
    </div>
  );
}
