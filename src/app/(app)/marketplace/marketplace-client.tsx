"use client";
import { useState, useEffect, useCallback } from "react";
import {
  Store, Package, Bot, RefreshCw, Loader2, CheckCircle2, XCircle,
  AlertCircle, ExternalLink, Search, ToggleLeft, ToggleRight,
  Download, Clock, Zap, Globe, Github, Cloud,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getApiBaseUrl, getToken } from "@/lib/api-client";
import { useTranslation } from "react-i18next";
import { PageLayout } from '@/components/page-layout';

// ─── Types ──────────────────────────────────────────────────────────

interface MarketplaceStats {
  skill_sources: number;
  agent_sources: number;
  total_skills: number;
  total_agents: number;
}

interface SkillSource {
  id: string;
  name: string;
  type: string;
  url: string;
  description: string;
  enabled: boolean;
  builtin: boolean;
  auto_sync: boolean;
  sync_interval: number;
  last_sync: string | null;
  last_sync_error: string | null;
  skill_count: number;
}

interface AgentSource {
  id: string;
  name: string;
  type: string;
  url: string;
  description: string;
  enabled: boolean;
  builtin: boolean;
  auto_update: boolean;
  last_sync: string | null;
  agent_count: number;
}

// ─── API helpers ────────────────────────────────────────────────────

function apiBase() {
  return getApiBaseUrl() || "";
}

function apiHeaders(): HeadersInit {
  const token = getToken();
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function fetchStats(): Promise<MarketplaceStats> {
  const res = await fetch(`${apiBase()}/api/marketplace/stats`, { headers: apiHeaders() });
  if (!res.ok) throw new Error(`Failed to fetch stats: ${res.status}`);
  return res.json();
}

async function fetchSkillSources(): Promise<{ sources: SkillSource[] }> {
  const res = await fetch(`${apiBase()}/api/marketplace/skills/sources`, { headers: apiHeaders() });
  if (!res.ok) throw new Error(`Failed to fetch skill sources: ${res.status}`);
  return res.json();
}

async function fetchAgentSources(): Promise<{ sources: AgentSource[] }> {
  const res = await fetch(`${apiBase()}/api/marketplace/agents/sources`, { headers: apiHeaders() });
  if (!res.ok) throw new Error(`Failed to fetch agent sources: ${res.status}`);
  return res.json();
}

async function syncSkillSource(sourceId: string): Promise<unknown> {
  const res = await fetch(`${apiBase()}/api/marketplace/skills/sources/${sourceId}/sync`, {
    method: "POST",
    headers: apiHeaders(),
  });
  if (!res.ok) throw new Error(`Sync failed: ${res.status}`);
  return res.json();
}

async function syncAllSkills(): Promise<unknown> {
  const res = await fetch(`${apiBase()}/api/marketplace/sync/skills`, {
    method: "POST",
    headers: apiHeaders(),
  });
  if (!res.ok) throw new Error(`Sync failed: ${res.status}`);
  return res.json();
}

async function syncAllAgents(): Promise<unknown> {
  const res = await fetch(`${apiBase()}/api/marketplace/sync/agents`, {
    method: "POST",
    headers: apiHeaders(),
  });
  if (!res.ok) throw new Error(`Sync failed: ${res.status}`);
  return res.json();
}

// ─── Helpers ────────────────────────────────────────────────────────

function sourceIcon(type: string) {
  switch (type) {
    case "github": return Github;
    case "hermes": return Zap;
    case "openclaw": return Cloud;
    case "builtin": return Package;
    case "aliyun": return Cloud;
    case "tencent": return Cloud;
    default: return Globe;
  }
}

function timeAgo(ts: string | null, t: (key: string, opts?: Record<string, unknown>) => string): string {
  if (!ts) return t("marketplace.neverSynced") || "Never synced";
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return t("marketplace.justNow") || "Just now";
  if (mins < 60) return t("marketplace.minutesAgo", { mins }) || `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return t("marketplace.hoursAgo", { hours }) || `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return t("marketplace.daysAgo", { days }) || `${days}d ago`;
}

// ─── Component ──────────────────────────────────────────────────────

export function MarketplaceClient() {
  const { t } = useTranslation();
  const [stats, setStats] = useState<MarketplaceStats | null>(null);
  const [skillSources, setSkillSources] = useState<SkillSource[]>([]);
  const [agentSources, setAgentSources] = useState<AgentSource[]>([]);
  const [activeTab, setActiveTab] = useState<"skills" | "agents">("skills");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState("");

  const loadData = useCallback(async () => {
    try {
      setError(null);
      const [statsData, skillsData, agentsData] = await Promise.all([
        fetchStats(),
        fetchSkillSources(),
        fetchAgentSources(),
      ]);
      setStats(statsData);
      setSkillSources(skillsData.sources || []);
      setAgentSources(agentsData.sources || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : (t("marketplace.loadFailed") || "Load failed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { loadData(); }, [loadData]);

  const handleSyncSource = async (sourceId: string) => {
    setSyncing((prev) => new Set(prev).add(sourceId));
    try {
      const result = (await syncSkillSource(sourceId)) as {
        success?: boolean;
        message?: string;
        error?: { reason?: string; detail?: string };
      };
      // mem0 §1.1失败必须可见：后端sync失败返回HTTP 200+success=false，
      // 此前前端忽略响应体→用户对失败无感知（只有卡片last_sync_error兜底）
      if (result && result.success === false) {
        const detail = result.error?.detail ? `: ${result.error.detail}` : "";
        setError(`${result.message || (t("marketplace.syncFailed") || "Sync failed")}${detail}`);
      }
      await loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : (t("marketplace.syncFailed") || "Sync failed"));
    } finally {
      setSyncing((prev) => {
        const next = new Set(prev);
        next.delete(sourceId);
        return next;
      });
    }
  };

  const handleSyncAll = async () => {
    const key = `all-${activeTab}`;
    setSyncing((prev) => new Set(prev).add(key));
    try {
      const result = ((activeTab === "skills" ? await syncAllSkills() : await syncAllAgents()) || {}) as {
        success?: boolean;
        message?: string;
        results?: Array<{ error?: { detail?: string } }>;
      };
      // 聚合同步结果可见：success=false（含agents not_implemented诚实响应）
      // → 错误条显示聚合消息+第一个失败源的detail
      if (result.success === false) {
        const firstErr = (result.results || []).find((r) => r.error)?.error?.detail;
        setError(`${result.message || (t("marketplace.syncFailed") || "Sync failed")}${firstErr ? `: ${firstErr}` : ""}`);
      }
      await loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : (t("marketplace.syncFailed") || "Sync failed"));
    } finally {
      setSyncing((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  };

  const filterSources = <T extends { name: string; description: string }>(sources: T[]): T[] => {
    if (!searchQuery) return sources;
    const q = searchQuery.toLowerCase();
    return sources.filter((s) => s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q));
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const filteredSkills = filterSources(skillSources);
  const filteredAgents = filterSources(agentSources);

  return (
      <PageLayout title="市场">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="shrink-0 border-b border-border px-3 lg:px-6 py-2 lg:py-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
            <Store className="h-5 w-5 lg:h-6 lg:w-6 text-primary shrink-0" />
            <div className="min-w-0">
              <h1 className="text-base lg:text-xl font-semibold truncate">{t("marketplace.title") || "Marketplace"}</h1>
              <p className="text-[10px] lg:text-sm text-muted-foreground">{t("marketplace.subtitleDesc") || "Discover, install and manage skills & Agents"}</p>
            </div>
          </div>
          <button
            onClick={handleSyncAll}
            disabled={syncing.has(`all-${activeTab}`)}
            className="flex items-center gap-1.5 lg:gap-2 rounded-lg bg-primary px-2.5 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 shrink-0 touch-manipulation"
          >
            {syncing.has(`all-${activeTab}`) ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            <span className="hidden sm:inline">{t("marketplace.syncAll", { type: activeTab === "skills" ? (t("marketplace.skills") || "Skills") : (t("marketplace.agents") || "Agent") }) || `同步全部${activeTab === "skills" ? "技能" : "Agent"}`}</span>
          </button>
        </div>
      </div>

      {/* Stats */}
      {stats && (
        <div className="shrink-0 grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4 border-b border-border px-3 lg:px-6 py-2 lg:py-4">
          <StatCard icon={Package} label={t("marketplace.skillSources") || "Skill Sources"} value={stats.skill_sources} color="text-blue-500" />
          <StatCard icon={Bot} label={t("marketplace.agentSources") || "Agent Sources"} value={stats.agent_sources} color="text-green-500" />
          <StatCard icon={Download} label={t("marketplace.installedSkills") || "Installed Skills"} value={stats.total_skills} color="text-purple-500" />
          <StatCard icon={Zap} label={t("marketplace.installedAgents") || "Installed Agents"} value={stats.total_agents} color="text-orange-500" />
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="shrink-0 flex items-center gap-2 border-b border-border bg-destructive/10 px-3 lg:px-6 py-2 text-xs lg:text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
          <button onClick={() => setError(null)} className="ml-auto text-xs underline">{t("marketplace.close") || "Close"}</button>
        </div>
      )}

      {/* Tabs + Search */}
      <div className="shrink-0 flex items-center gap-2 lg:gap-4 border-b border-border px-3 lg:px-6 py-2 overflow-x-auto">
        <div className="flex gap-1 shrink-0">
          <TabButton active={activeTab === "skills"} onClick={() => setActiveTab("skills")} icon={Package} label={t("marketplace.skillSources") || "Skill Sources"} count={skillSources.length} />
          <TabButton active={activeTab === "agents"} onClick={() => setActiveTab("agents")} icon={Bot} label={t("marketplace.agentSources") || "Agent Sources"} count={agentSources.length} />
        </div>
        <div className="relative ml-auto w-40 lg:w-64 shrink-0">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            type="text"
            placeholder={t("marketplace.searchSources") || "Search..."}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full rounded-lg border border-border bg-background py-1.5 pl-9 pr-3 text-xs lg:text-sm outline-none focus:border-primary"
          />
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-2 lg:py-4">
        {activeTab === "skills" ? (
          <SourceGrid
            sources={filteredSkills}
            syncing={syncing}
            onSync={handleSyncSource}
            type="skills"
          />
        ) : (
          <SourceGrid
            sources={filteredAgents}
            syncing={syncing}
            onSync={handleSyncSource}
            type="agents"
          />
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}

// ─── Sub-components ─────────────────────────────────────────────────

function StatCard({ icon: Icon, label, value, color }: { icon: React.ElementType; label: string; value: number; color: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-card p-3">
      <Icon className={cn("h-5 w-5", color)} />
      <div>
        <div className="text-lg font-semibold">{value}</div>
        <div className="text-xs text-muted-foreground">{label}</div>
      </div>
    </div>
  );
}

function TabButton({ active, onClick, icon: Icon, label, count }: { active: boolean; onClick: () => void; icon: React.ElementType; label: string; count: number }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs lg:text-sm font-medium transition-colors",
        active ? "bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground"
      )}
    >
      <Icon className="h-4 w-4" />
      {label}
      <span className={cn("ml-1 rounded-full px-1.5 py-0.5 text-[10px]", active ? "bg-primary/20" : "bg-muted")}>{count}</span>
    </button>
  );
}

interface SourceItem {
  id: string;
  name: string;
  type: string;
  url: string;
  description: string;
  enabled: boolean;
  builtin: boolean;
  last_sync: string | null;
  last_sync_error?: string | null;
  skill_count?: number;
  agent_count?: number;
  auto_sync?: boolean;
  auto_update?: boolean;
  sync_interval?: number;
}

function SourceGrid({ sources, syncing, onSync, type }: { sources: SourceItem[]; syncing: Set<string>; onSync: (id: string) => void; type: string }) {
  const { t } = useTranslation();

  if (sources.length === 0) {
    const typeName = type === "skills" ? (t("marketplace.skills") || "Skills") : (t("marketplace.agents") || "Agent");
    return (
      <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
        <Store className="mb-4 h-12 w-12" />
        <p className="text-xs lg:text-sm">{t("marketplace.noSources", { type: typeName }) || `No ${typeName} sources`}</p>
      </div>
    );
  }

  return (
    <div className="grid gap-2 lg:gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {sources.map((source) => (
        <SourceCard key={source.id} source={source} syncing={syncing} onSync={onSync} />
      ))}
    </div>
  );
}

function SourceCard({ source, syncing, onSync }: { source: SourceItem; syncing: Set<string>; onSync: (id: string) => void }) {
  const { t } = useTranslation();
  const Icon = sourceIcon(source.type);
  const isSyncing = syncing.has(source.id);
  const count = (source.skill_count ?? source.agent_count ?? 0) as number;

  return (
    <div className={cn(
      "flex flex-col rounded-lg border bg-card p-3 lg:p-4 transition-colors hover:border-primary/30",
      source.enabled ? "border-border" : "border-border/50 opacity-60"
    )}>
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-muted">
            <Icon className="h-4 w-4 text-foreground" />
          </div>
          <div>
            <h3 className="text-xs lg:text-sm font-medium">{source.name}</h3>
            <span className="text-[10px] uppercase text-muted-foreground">{source.type}</span>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {source.enabled ? (
            <ToggleRight className="h-5 w-5 text-green-500" />
          ) : (
            <ToggleLeft className="h-5 w-5 text-muted-foreground" />
          )}
        </div>
      </div>

      <p className="mt-2 flex-1 text-xs text-muted-foreground line-clamp-2">{source.description}</p>

      {/* last_sync_error：同步失败原因直接可见（mem0 §1.1失败必须可见，后端契约已带此字段） */}
      {source.last_sync_error && (
        <div className="mt-2 flex items-start gap-1.5 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1.5 text-[11px] text-destructive">
          <XCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span className="line-clamp-2 break-all" title={source.last_sync_error}>
            {t("marketplace.lastSyncFailed") || "Last sync failed"}: {source.last_sync_error}
          </span>
        </div>
      )}

      <div className="mt-3 flex items-center justify-between text-[11px] text-muted-foreground">
        <div className="flex items-center gap-1">
          <Clock className="h-3 w-3" />
          {timeAgo(source.last_sync, t)}
        </div>
        <span>{t("marketplace.items", { count }) || `${count} 项`}</span>
      </div>

      <div className="mt-3 flex items-center gap-2">
        <button
          onClick={() => onSync(source.id)}
          disabled={isSyncing || !source.enabled}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-border bg-background py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
        >
          {isSyncing ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="h-3 w-3" />
          )}
          {t("marketplace.sync") || "Sync"}
        </button>
        {source.url && (
          <a
            href={source.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-center rounded-md border border-border bg-background p-1.5 hover:bg-muted"
          >
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>

      {source.builtin && (
        <div className="mt-2 flex items-center gap-1 text-[10px] text-muted-foreground">
          <CheckCircle2 className="h-3 w-3 text-green-500" />
          {t("marketplace.builtinSources") || "Built-in Sources"}
        </div>
      )}
    </div>
  );
}
