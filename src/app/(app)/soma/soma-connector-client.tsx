"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  Bot, RefreshCw, Plus, Trash2, Activity, Settings,
  CheckCircle, XCircle, Clock, Wifi, WifiOff, Zap,
  Server, Plug, ArrowRight, AlertTriangle, Info,
  Play, Square, RotateCcw, Database, HardDrive,
  Monitor, Layers, History,
} from "lucide-react";
import { useIsMobile } from "@/hooks/use-mobile";
import { PageLayout } from '@/components/page-layout';

interface SomaComponent {
  component_id: string;
  name: string;
  component_type: string;
  version: string;
  capabilities: string[];
  status: string;
  registered_at: string;
  last_heartbeat: string;
  data_push_count: number;
  error_count: number;
  last_error: string | null;
  metadata: Record<string, unknown>;
}

interface PlatformCapabilities {
  platform: string;
  version: string;
  capabilities: Record<string, unknown>;
  api_version: string;
  endpoints: Record<string, string>;
}

interface CollectorStatus {
  id: string;
  name: string;
  type: string;
  status: "running" | "stopped" | "error";
  events_collected: number;
  last_event_at: string | null;
  watch_path?: string;
  error?: string;
}

interface SyncRecord {
  id: string;
  component_id: string;
  data_type: string;
  timestamp: string;
  status: "success" | "failed" | "pending";
  items_count: number;
  error?: string;
}

type TabId = "overview" | "collectors" | "sync";

const TYPE_COLORS: Record<string, string> = {
  collector: "text-blue-500 bg-blue-500/10",
  processor: "text-violet-500 bg-violet-500/10",
  connector: "text-emerald-500 bg-emerald-500/10",
  agent: "text-amber-500 bg-amber-500/10",
  custom: "text-muted-foreground bg-muted-foreground/10",
};

const STATUS_COLORS: Record<string, string> = {
  online: "text-emerald-500",
  offline: "text-red-500",
  busy: "text-amber-500",
  error: "text-red-500",
  maintenance: "text-muted-foreground",
  running: "text-emerald-500",
  stopped: "text-muted-foreground",
};

function formatTime(iso: string, t: (k: string, o?: Record<string, unknown>) => string): string {
  if (!iso) return "-";
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  if (diff < 60000) return t("soma.justNow");
  if (diff < 3600000) return t("soma.minutesAgo", { minutes: Math.floor(diff / 60000) });
  if (diff < 86400000) return t("soma.hoursAgo", { hours: Math.floor(diff / 3600000) });
  return d.toLocaleDateString(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function SomaConnectorClient() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabId>("overview");
  const [components, setComponents] = useState<SomaComponent[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [capabilities, setCapabilities] = useState<PlatformCapabilities | null>(null);
  const [selected, setSelected] = useState<SomaComponent | null>(null);
  const [showRegister, setShowRegister] = useState(false);
  const [loading, setLoading] = useState(false);
  const [showCapabilities, setShowCapabilities] = useState(false);
  const isMobile = useIsMobile();
  const apiBase = getApiBaseUrl();

  // Collectors state
  const [collectors, setCollectors] = useState<CollectorStatus[]>([]);
  const [collectorLoading, setCollectorLoading] = useState(false);

  // Sync state
  const [syncHistory, setSyncHistory] = useState<SyncRecord[]>([]);
  const [syncLoading, setSyncLoading] = useState(false);

  // Register form state
  const [regId, setRegId] = useState("");
  const [regName, setRegName] = useState("");
  const [regType, setRegType] = useState("collector");
  const [regVersion, setRegVersion] = useState("0.1.0");
  const [regCaps, setRegCaps] = useState("");

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/soma/health`);
      setHealth(await res.json());
    } catch {}
  }, [apiBase]);

  const fetchComponents = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/soma/components`);
      const data = await res.json();
      setComponents(data.components || []);
    } catch {}
  }, [apiBase]);

  const fetchCapabilities = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/soma/capabilities`);
      setCapabilities(await res.json());
    } catch {}
  }, [apiBase]);

  const fetchCollectors = useCallback(async () => {
    setCollectorLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/soma/components?type=collector`);
      const data = await res.json();
      const comps: SomaComponent[] = data.components || [];
      setCollectors(comps.map((c: SomaComponent) => ({
        id: c.component_id,
        name: c.name,
        type: c.metadata?.collector_type as string || c.component_type,
        status: c.status === "online" ? "running" : c.status === "error" ? "error" : "stopped",
        events_collected: c.data_push_count,
        last_event_at: c.last_heartbeat,
        watch_path: c.metadata?.watch_path as string,
        error: c.last_error || undefined,
      })));
    } catch {} finally { setCollectorLoading(false); }
  }, [apiBase]);

  const fetchSyncHistory = useCallback(async () => {
    setSyncLoading(true);
    try {
      // Build sync history from all components' push data
      const res = await fetch(`${apiBase}/api/soma/components`);
      const data = await res.json();
      const comps: SomaComponent[] = data.components || [];
      const records: SyncRecord[] = comps.map((c: SomaComponent, i: number) => ({
        id: `sync-${c.component_id}-${i}`,
        component_id: c.component_id,
        data_type: c.capabilities?.[0] || "unknown",
        timestamp: c.last_heartbeat,
        status: c.last_error ? "failed" as const : (c.data_push_count > 0 ? "success" as const : "pending" as const),
        items_count: c.data_push_count,
        error: c.last_error || undefined,
      }));
      setSyncHistory(records);
    } catch {} finally { setSyncLoading(false); }
  }, [apiBase]);

  useEffect(() => {
    fetchHealth();
    fetchComponents();
    fetchCapabilities();
  }, [fetchHealth, fetchComponents, fetchCapabilities]);

  useEffect(() => {
    if (activeTab === "collectors") fetchCollectors();
    if (activeTab === "sync") fetchSyncHistory();
  }, [activeTab, fetchCollectors, fetchSyncHistory]);

  const handleRegister = async () => {
    if (!regId.trim() || !regName.trim()) return;
    setLoading(true);
    try {
      const caps = regCaps.split(",").map(c => c.trim()).filter(Boolean);
      const res = await fetch(`${apiBase}/api/soma/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          component_id: regId,
          name: regName,
          component_type: regType,
          version: regVersion,
          capabilities: caps,
        }),
      });
      if (res.ok) {
        setShowRegister(false);
        setRegId(""); setRegName(""); setRegCaps("");
        fetchComponents();
        fetchHealth();
      }
    } catch {} finally { setLoading(false); }
  };

  const handleUnregister = async (id: string) => {
    if (!confirm(t("soma.confirmUnregister"))) return;
    try {
      await fetch(`${apiBase}/api/soma/components/${id}`, { method: "DELETE" });
      setSelected(null);
      fetchComponents();
      fetchHealth();
    } catch {}
  };

  const handleHeartbeat = async (id: string) => {
    try {
      await fetch(`${apiBase}/api/soma/heartbeat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ component_id: id, status: "ok" }),
      });
      fetchComponents();
    } catch {}
  };

  const handleCollectorAction = async (id: string, action: "start" | "stop" | "restart") => {
    try {
      const statusMap = { start: "online", stop: "maintenance", restart: "online" };
      await fetch(`${apiBase}/api/soma/components/${id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: statusMap[action], message: action }),
      });
      fetchCollectors();
    } catch {}
  };

  const tabs: { id: TabId; label: string; icon: React.ReactNode }[] = [
    { id: "overview", label: t("soma.tabOverview"), icon: <Monitor size={14} /> },
    { id: "collectors", label: t("soma.tabCollectors"), icon: <Database size={14} /> },
    { id: "sync", label: t("soma.tabSync"), icon: <History size={14} /> },
  ];

  return (
      <PageLayout title="Soma">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-cyan-500/10">
            <Bot size={20} className="text-cyan-500" />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg lg:text-xl font-semibold truncate">{t("soma.title")}</h1>
            <p className="text-xs lg:text-sm text-muted-foreground truncate">{t('organTag.soma')}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowCapabilities(!showCapabilities)}
            className={cn("flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs lg:text-sm transition-colors",
              showCapabilities ? "bg-cyan-500 text-white" : "border border-border hover:bg-muted")}>
            <Info size={14} /> {t("soma.platformCapabilities")}
          </button>
          <button onClick={() => setShowRegister(true)}
            className="flex items-center gap-1.5 rounded-lg bg-cyan-500 px-3 py-1.5 text-xs lg:text-sm text-white hover:bg-cyan-600">
            <Plus size={14} /> {t("soma.registerComponent")}
          </button>
          <button onClick={() => { fetchHealth(); fetchComponents(); }}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex items-center gap-1 border-b border-border px-3 lg:px-6 py-2">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs lg:text-sm transition-colors",
              activeTab === tab.id
                ? "bg-cyan-500/10 text-cyan-600 font-medium"
                : "text-muted-foreground hover:bg-muted"
            )}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4 lg:py-6 space-y-3 lg:space-y-6">

        {/* ── Overview Tab ───────────────────────────────────────── */}
        {activeTab === "overview" && (
          <>
            {/* Stats */}
            {health && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("soma.registeredComponents")}</span>
                  <p className="text-xl lg:text-2xl font-bold">{health.registry?.total || 0}</p>
                </div>
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("soma.online")}</span>
                  <p className="text-xl lg:text-2xl font-bold text-emerald-500">{health.registry?.online || 0}</p>
                </div>
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("soma.offline")}</span>
                  <p className="text-xl lg:text-2xl font-bold text-red-500">{health.registry?.offline || 0}</p>
                </div>
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("soma.typeDistribution")}</span>
                  <p className="text-xs font-mono mt-1">
                    {Object.entries(health.registry?.by_type || {}).map(([k, v]) => `${k}:${v}`).join(" · ") || t("soma.none")}
                  </p>
                </div>
              </div>
            )}

            {/* Platform Capabilities */}
            {showCapabilities && capabilities && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-4">
                <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                  <Info size={14} className="text-cyan-500" />
                  {t("soma.platformDiscovery")} — {capabilities.platform} v{capabilities.version}
                </h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-3">
                  {Object.entries(capabilities.capabilities).map(([key, value]) => (
                    <div key={key} className="flex items-center gap-2 text-xs">
                      {value === true ? (
                        <CheckCircle size={12} className="text-emerald-500" />
                      ) : (
                        <span className="font-mono text-muted-foreground">{String(value)}</span>
                      )}
                      <span>{key}</span>
                    </div>
                  ))}
                </div>
                <div className="border-t border-border pt-3">
                  <h4 className="text-xs text-muted-foreground mb-2">{t("soma.apiEndpoints")}</h4>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {Object.entries(capabilities.endpoints).map(([name, path]) => (
                      <div key={name} className="flex items-center gap-2 text-xs">
                        <span className="text-cyan-500 font-medium">{name}</span>
                        <span className="font-mono text-muted-foreground">{path}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* Quick Start Guide */}
            <div className="rounded-xl border border-dashed border-cyan-500/30 bg-cyan-500/5 p-5">
              <h3 className="text-xs lg:text-sm font-semibold text-cyan-600 mb-2">🚀 {t("soma.quickStartGuide")}</h3>
              <div className="text-xs text-muted-foreground space-y-2">
                <p>{t("soma.quickStartDesc")}</p>
                <div className="font-mono bg-background rounded p-3 text-[11px] space-y-1">
                  <p><span className="text-cyan-500">1.</span> {t("soma.step1")}</p>
                  <p><span className="text-cyan-500">2.</span> {t("soma.step2")}</p>
                  <p><span className="text-cyan-500">3.</span> {t("soma.step3")}</p>
                  <p className="pt-1 text-muted-foreground"><span className="text-cyan-500">{t("soma.step4Label")}</span> {t("soma.step4")}</p>
                </div>
              </div>
            </div>

            {/* Component List */}
            <div className={`flex gap-2 lg:gap-6 ${isMobile ? 'flex-col' : ''} relative`}>
              <div className={`${isMobile ? 'w-full' : 'w-80'} space-y-3`}>
                {components.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                    <Plug size={40} className="mb-3 opacity-30" />
                    <p className="text-xs lg:text-sm">{t("soma.noComponents")}</p>
                    <p className="text-xs mt-1">{t("soma.noComponentsHint")}</p>
                  </div>
                ) : components.map((c) => (
                  <div key={c.component_id}
                    onClick={() => setSelected(c)}
                    className={cn(
                      "rounded-xl border border-border bg-card p-3 lg:p-4 cursor-pointer transition-all hover:shadow-md",
                      selected?.component_id === c.component_id && "ring-2 ring-cyan-500"
                    )}>
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        <div className={cn("rounded-lg p-1.5", TYPE_COLORS[c.component_type] || TYPE_COLORS.custom)}>
                          <Bot size={14} />
                        </div>
                        <span className="font-medium text-xs lg:text-sm">{c.name}</span>
                      </div>
                      <span className={cn("text-xs font-medium", STATUS_COLORS[c.status] || "text-muted-foreground")}>
                        {c.status === "online" ? t("soma.statusOnline") : c.status === "offline" ? t("soma.statusOffline") : c.status}
                      </span>
                    </div>
                    <div className="text-xs text-muted-foreground font-mono">{c.component_id}</div>
                    <div className="flex gap-2 lg:gap-3 mt-2 text-xs text-muted-foreground">
                      <span>v{c.version}</span>
                      <span>{t("soma.push")}: {c.data_push_count}</span>
                      {c.error_count > 0 && <span className="text-red-500">{t("soma.error")}: {c.error_count}</span>}
                    </div>
                  </div>
                ))}
              </div>

              {/* Detail Panel — sidebar sliding on mobile, inline on desktop */}
              {selected && isMobile && (
                <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setSelected(null)} aria-hidden="true" />
              )}
              {selected && isMobile ? (
                <div
                  className="absolute inset-y-0 right-0 z-10 h-full w-72 min-w-0 border-l border-border transition-[right] duration-200 ease-linear flex flex-col overflow-hidden bg-card"
                  style={{ right: 0 }}
                >
                  <div className="flex-1 overflow-y-auto p-4 space-y-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="font-semibold">{selected.name}</h3>
                        <span className="text-xs text-muted-foreground font-mono">{selected.component_id}</span>
                      </div>
                      <div className="flex gap-2">
                        <button onClick={() => handleHeartbeat(selected.component_id)}
                          className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                          <Zap size={12} /> {t("soma.sendHeartbeat")}
                        </button>
                        <button onClick={() => handleUnregister(selected.component_id)}
                          className="flex items-center gap-1 rounded-lg border border-red-500/30 px-3 py-1.5 text-xs text-red-500 hover:bg-red-500/10">
                          <Trash2 size={12} /> {t("soma.unregister")}
                        </button>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-2 lg:gap-4 text-xs lg:text-sm">
                      <div><span className="text-muted-foreground">{t("soma.type")}:</span> {selected.component_type}</div>
                      <div><span className="text-muted-foreground">{t("soma.version")}:</span> v{selected.version}</div>
                      <div><span className="text-muted-foreground">{t("soma.registeredAt")}:</span> {formatTime(selected.registered_at, t)}</div>
                      <div><span className="text-muted-foreground">{t("soma.lastHeartbeat")}:</span> {formatTime(selected.last_heartbeat, t)}</div>
                      <div><span className="text-muted-foreground">{t("soma.dataPush")}:</span> {selected.data_push_count} {t("soma.times")}</div>
                      <div><span className="text-muted-foreground">{t("soma.errorCount")}:</span> {selected.error_count}</div>
                    </div>

                    {selected.capabilities.length > 0 && (
                      <div>
                        <h4 className="text-xs text-muted-foreground mb-2">{t("soma.capabilityTags")}</h4>
                        <div className="flex flex-wrap gap-2">
                          {selected.capabilities.map((cap) => (
                            <span key={cap} className="rounded-full bg-cyan-500/10 px-3 py-1 text-xs text-cyan-600">
                              {cap}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {selected.last_error && (
                      <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3">
                        <div className="flex items-center gap-2 text-xs text-red-500">
                          <AlertTriangle size={12} />
                          <span className="font-medium">{t("soma.recentError")}</span>
                        </div>
                        <p className="text-xs text-red-400 mt-1">{selected.last_error}</p>
                      </div>
                    )}
                  </div>
                </div>
              ) : selected ? (
                <div className="flex-1 space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="font-semibold">{selected.name}</h3>
                      <span className="text-xs text-muted-foreground font-mono">{selected.component_id}</span>
                    </div>
                    <div className="flex gap-2">
                      <button onClick={() => handleHeartbeat(selected.component_id)}
                        className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                        <Zap size={12} /> {t("soma.sendHeartbeat")}
                      </button>
                      <button onClick={() => handleUnregister(selected.component_id)}
                        className="flex items-center gap-1 rounded-lg border border-red-500/30 px-3 py-1.5 text-xs text-red-500 hover:bg-red-500/10">
                        <Trash2 size={12} /> {t("soma.unregister")}
                      </button>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-2 lg:gap-4 text-xs lg:text-sm">
                    <div><span className="text-muted-foreground">{t("soma.type")}:</span> {selected.component_type}</div>
                    <div><span className="text-muted-foreground">{t("soma.version")}:</span> v{selected.version}</div>
                    <div><span className="text-muted-foreground">{t("soma.registeredAt")}:</span> {formatTime(selected.registered_at, t)}</div>
                    <div><span className="text-muted-foreground">{t("soma.lastHeartbeat")}:</span> {formatTime(selected.last_heartbeat, t)}</div>
                    <div><span className="text-muted-foreground">{t("soma.dataPush")}:</span> {selected.data_push_count} {t("soma.times")}</div>
                    <div><span className="text-muted-foreground">{t("soma.errorCount")}:</span> {selected.error_count}</div>
                  </div>

                  {/* Capabilities */}
                  {selected.capabilities.length > 0 && (
                    <div>
                      <h4 className="text-xs text-muted-foreground mb-2">{t("soma.capabilityTags")}</h4>
                      <div className="flex flex-wrap gap-2">
                        {selected.capabilities.map((cap) => (
                          <span key={cap} className="rounded-full bg-cyan-500/10 px-3 py-1 text-xs text-cyan-600">
                            {cap}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Error Info */}
                  {selected.last_error && (
                    <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3">
                      <div className="flex items-center gap-2 text-xs text-red-500">
                        <AlertTriangle size={12} />
                        <span className="font-medium">{t("soma.recentError")}</span>
                      </div>
                      <p className="text-xs text-red-400 mt-1">{selected.last_error}</p>
                    </div>
                  )}
                </div>
              ) : null}
            </div>
          </>
        )}

        {/* ── Collectors Tab ─────────────────────────────────────── */}
        {activeTab === "collectors" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                <Database size={16} className="text-cyan-500" />
                {t("soma.collectorsTitle")}
              </h2>
              <button onClick={fetchCollectors}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                <RefreshCw size={12} /> {t("soma.refresh")}
              </button>
            </div>

            {collectorLoading ? (
              <div className="flex items-center justify-center py-16 text-muted-foreground text-xs lg:text-sm">{t("soma.loading")}</div>
            ) : collectors.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Database size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("soma.noCollectors")}</p>
                <p className="text-xs mt-1">{t("soma.noCollectorsHint")}</p>
              </div>
            ) : (
              <div className="space-y-3">
                {collectors.map((col) => (
                  <div key={col.id}
                    className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2 lg:gap-3">
                        <div className={cn("rounded-lg p-2",
                          col.status === "running" ? "bg-emerald-500/10" :
                          col.status === "error" ? "bg-red-500/10" : "bg-muted-foreground/10")}>
                          {col.status === "running" ? <Activity size={16} className="text-emerald-500" /> :
                           col.status === "error" ? <AlertTriangle size={16} className="text-red-500" /> :
                           <Square size={16} className="text-muted-foreground" />}
                        </div>
                        <div>
                          <p className="font-medium text-xs lg:text-sm">{col.name}</p>
                          <p className="text-xs text-muted-foreground font-mono">{col.id}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className={cn("text-xs font-medium px-2 py-0.5 rounded-full",
                          col.status === "running" ? "bg-emerald-500/10 text-emerald-500" :
                          col.status === "error" ? "bg-red-500/10 text-red-500" :
                          "bg-muted-foreground/10 text-muted-foreground")}>
                          {col.status === "running" ? t("soma.collectorRunning") :
                           col.status === "error" ? t("soma.collectorError") :
                           t("soma.collectorStopped")}
                        </span>
                        <div className="flex gap-1">
                          {col.status !== "running" && (
                            <button onClick={() => handleCollectorAction(col.id, "start")}
                              className="rounded-lg border border-emerald-500/30 px-2 py-1 text-xs text-emerald-500 hover:bg-emerald-500/10"
                              title={t("soma.startCollector")}>
                              <Play size={12} />
                            </button>
                          )}
                          {col.status === "running" && (
                            <button onClick={() => handleCollectorAction(col.id, "stop")}
                              className="rounded-lg border border-red-500/30 px-2 py-1 text-xs text-red-500 hover:bg-red-500/10"
                              title={t("soma.stopCollector")}>
                              <Square size={12} />
                            </button>
                          )}
                          <button onClick={() => handleCollectorAction(col.id, "restart")}
                            className="rounded-lg border border-border px-2 py-1 text-xs hover:bg-muted"
                            title={t("soma.restartCollector")}>
                            <RotateCcw size={12} />
                          </button>
                        </div>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-3 text-xs">
                      <div>
                        <span className="text-muted-foreground">{t("soma.collectorType")}:</span>{" "}
                        <span className="font-mono">{col.type}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">{t("soma.eventsCollected")}:</span>{" "}
                        <span className="font-semibold">{col.events_collected}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">{t("soma.lastEvent")}:</span>{" "}
                        <span>{col.last_event_at ? formatTime(col.last_event_at, t) : "-"}</span>
                      </div>
                      {col.watch_path && (
                        <div>
                          <span className="text-muted-foreground">{t("soma.watchPath")}:</span>{" "}
                          <span className="font-mono text-[11px]">{col.watch_path}</span>
                        </div>
                      )}
                    </div>
                    {col.error && (
                      <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/5 p-2">
                        <p className="text-xs text-red-400">{col.error}</p>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── Sync Tab ───────────────────────────────────────────── */}
        {activeTab === "sync" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                <History size={16} className="text-cyan-500" />
                {t("soma.syncTitle")}
              </h2>
              <button onClick={fetchSyncHistory}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                <RefreshCw size={12} /> {t("soma.refresh")}
              </button>
            </div>

            {/* Sync Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("soma.totalSyncs")}</span>
                <p className="text-xl lg:text-2xl font-bold">{syncHistory.length}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("soma.syncSuccess")}</span>
                <p className="text-xl lg:text-2xl font-bold text-emerald-500">{syncHistory.filter(s => s.status === "success").length}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("soma.syncFailed")}</span>
                <p className="text-xl lg:text-2xl font-bold text-red-500">{syncHistory.filter(s => s.status === "failed").length}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("soma.totalItems")}</span>
                <p className="text-xl lg:text-2xl font-bold">{syncHistory.reduce((sum, s) => sum + s.items_count, 0)}</p>
              </div>
            </div>

            {/* Sync History Table */}
            {syncLoading ? (
              <div className="flex items-center justify-center py-16 text-muted-foreground text-xs lg:text-sm">{t("soma.loading")}</div>
            ) : syncHistory.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <History size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("soma.noSyncHistory")}</p>
                <p className="text-xs mt-1">{t("soma.noSyncHistoryHint")}</p>
              </div>
            ) : (
              <div className="rounded-xl border border-border overflow-x-auto">
                <table className="w-full text-xs lg:text-sm min-w-[500px]">
                  <thead>
                    <tr className="border-b border-border bg-muted/50">
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">{t("soma.syncComponent")}</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">{t("soma.syncDataType")}</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">{t("soma.syncStatus")}</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">{t("soma.syncItems")}</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">{t("soma.syncTime")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {syncHistory.map((record) => (
                      <tr key={record.id} className="border-b border-border last:border-0 hover:bg-muted/30">
                        <td className="px-2 lg:px-4 py-2">
                          <span className="font-mono text-xs">{record.component_id}</span>
                        </td>
                        <td className="px-2 lg:px-4 py-2">
                          <span className="rounded-full bg-cyan-500/10 px-2 py-0.5 text-xs text-cyan-600">{record.data_type}</span>
                        </td>
                        <td className="px-2 lg:px-4 py-2">
                          <span className={cn("flex items-center gap-1 text-xs",
                            record.status === "success" ? "text-emerald-500" :
                            record.status === "failed" ? "text-red-500" : "text-amber-500")}>
                            {record.status === "success" ? <CheckCircle size={12} /> :
                             record.status === "failed" ? <XCircle size={12} /> :
                             <Clock size={12} />}
                            {record.status === "success" ? t("soma.syncStatusSuccess") :
                             record.status === "failed" ? t("soma.syncStatusFailed") :
                             t("soma.syncStatusPending")}
                          </span>
                        </td>
                        <td className="px-2 lg:px-4 py-2 font-mono text-xs">{record.items_count}</td>
                        <td className="px-2 lg:px-4 py-2 text-xs text-muted-foreground">
                          {formatTime(record.timestamp, t)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Failed items detail */}
            {syncHistory.some(s => s.status === "failed") && (
              <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-3 lg:p-4 space-y-2">
                <h3 className="text-xs font-semibold text-red-500 flex items-center gap-2">
                  <AlertTriangle size={12} /> {t("soma.syncErrors")}
                </h3>
                {syncHistory.filter(s => s.error).map((s) => (
                  <div key={s.id} className="text-xs text-red-400">
                    <span className="font-mono">{s.component_id}</span>: {s.error}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Register Modal */}
        {showRegister && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
            <div className="w-full max-w-md rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-4">
              <h3 className="font-semibold">{t("soma.registerExternal")}</h3>
              <input value={regId} onChange={(e) => setRegId(e.target.value)}
                placeholder={t("soma.componentIdPlaceholder")} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
              <input value={regName} onChange={(e) => setRegName(e.target.value)}
                placeholder={t("soma.componentNamePlaceholder")} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
              <select value={regType} onChange={(e) => setRegType(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm">
                <option value="collector">{t("soma.collector")}</option>
                <option value="processor">{t("soma.processor")}</option>
                <option value="connector">{t("soma.connector")}</option>
                <option value="agent">Agent</option>
                <option value="custom">{t("soma.custom")}</option>
              </select>
              <input value={regVersion} onChange={(e) => setRegVersion(e.target.value)}
                placeholder={t("soma.versionPlaceholder")} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
              <input value={regCaps} onChange={(e) => setRegCaps(e.target.value)}
                placeholder={t("soma.capsPlaceholder")} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
              <div className="flex justify-end gap-2">
                <button onClick={() => setShowRegister(false)}
                  className="rounded-lg border border-border px-2 lg:px-4 py-2 text-xs lg:text-sm hover:bg-muted">{t("soma.cancel")}</button>
                <button onClick={handleRegister} disabled={loading || !regId.trim() || !regName.trim()}
                  className="rounded-lg bg-cyan-500 px-2 lg:px-4 py-2 text-xs lg:text-sm text-white hover:bg-cyan-600 disabled:opacity-50">
                  {loading ? t("soma.registering") : t("soma.register")}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
