"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  Bot, RefreshCw, Activity, Settings, Server, Plug,
  CheckCircle, XCircle, AlertTriangle, Wifi, WifiOff,
  Monitor, Database, Zap, HardDrive, Cpu, BarChart3,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface SystemStatus {
  status: string;
  version?: string;
  uptime?: string | number;
  connectors_count?: number;
  collectors_count?: number;
  [key: string]: unknown;
}

interface Connector {
  id: string;
  name: string;
  type: string;
  status: string;
  enabled?: boolean;
  config?: Record<string, unknown>;
  last_active?: string;
  error_count?: number;
  last_error?: string;
}

interface Collector {
  id: string;
  name: string;
  type: string;
  status: string;
  events_collected?: number;
  last_event_at?: string;
  error?: string;
}

type TabId = "dashboard" | "connectors" | "collectors" | "config";

const STATUS_COLORS: Record<string, string> = {
  online: "text-emerald-500",
  running: "text-emerald-500",
  active: "text-emerald-500",
  offline: "text-red-500",
  stopped: "text-muted-foreground",
  error: "text-red-500",
  degraded: "text-amber-500",
};

function formatTime(iso: string, t: (k: string, o?: Record<string, unknown>) => string): string {
  if (!iso) return "-";
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  if (diff < 60000) return t("somaAdmin.justNow");
  if (diff < 3600000) return t("somaAdmin.minutesAgo", { minutes: Math.floor(diff / 60000) } as any);
  if (diff < 86400000) return t("somaAdmin.hoursAgo", { hours: Math.floor(diff / 3600000) } as any);
  return d.toLocaleDateString(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export default function SomaAdminClient() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabId>("dashboard");
  const apiBase = getApiBaseUrl();
  const somaBase = "http://localhost:8091";

  // Dashboard state
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [dashboardLoading, setDashboardLoading] = useState(false);

  // Connectors state
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [connectorsLoading, setConnectorsLoading] = useState(false);
  const [selectedConnector, setSelectedConnector] = useState<Connector | null>(null);

  // Collectors state
  const [collectors, setCollectors] = useState<Collector[]>([]);
  const [collectorsLoading, setCollectorsLoading] = useState(false);

  // Config state
  const [config, setConfig] = useState<any>(null);
  const [configLoading, setConfigLoading] = useState(false);
  const isMobile = useIsMobile();

  const fetchDashboard = useCallback(async () => {
    setDashboardLoading(true);
    try {
      const res = await fetch(`${somaBase}/api/status`);
      setSystemStatus(await res.json());
    } catch {} finally { setDashboardLoading(false); }
  }, []);

  const fetchConnectors = useCallback(async () => {
    setConnectorsLoading(true);
    try {
      const res = await fetch(`${somaBase}/api/connectors`);
      const data = await res.json();
      setConnectors(Array.isArray(data) ? data : data?.connectors || []);
    } catch {} finally { setConnectorsLoading(false); }
  }, []);

  const fetchCollectors = useCallback(async () => {
    setCollectorsLoading(true);
    try {
      const res = await fetch(`${somaBase}/api/collectors`);
      const data = await res.json();
      setCollectors(Array.isArray(data) ? data : data?.collectors || []);
    } catch {} finally { setCollectorsLoading(false); }
  }, []);

  const fetchConfig = useCallback(async () => {
    setConfigLoading(true);
    try {
      const res = await fetch(`${somaBase}/api/status`);
      setConfig(await res.json());
    } catch {} finally { setConfigLoading(false); }
  }, []);

  const toggleConnector = useCallback(async (name: string) => {
    try {
      const res = await fetch(`${somaBase}/api/connectors/${name}/toggle`, { method: "POST" });
      const data = await res.json();
      // Update local state
      setConnectors(prev => prev.map(c =>
        c.name === name || c.id === name ? { ...c, enabled: data.enabled ?? !c.enabled } : c
      ));
      if (selectedConnector?.name === name || selectedConnector?.id === name) {
        setSelectedConnector(prev => prev ? { ...prev, enabled: data.enabled ?? !prev.enabled } : prev);
      }
    } catch {}
  }, [selectedConnector]);

  useEffect(() => { fetchDashboard(); }, [fetchDashboard]);

  useEffect(() => {
    if (activeTab === "connectors") fetchConnectors();
    if (activeTab === "collectors") fetchCollectors();
    if (activeTab === "config") fetchConfig();
  }, [activeTab, fetchConnectors, fetchCollectors, fetchConfig]);

  const tabs: { id: TabId; label: string; icon: React.ReactNode }[] = [
    { id: "dashboard", label: t("somaAdmin.tabDashboard"), icon: <Monitor size={14} /> },
    { id: "connectors", label: t("somaAdmin.tabConnectors"), icon: <Plug size={14} /> },
    { id: "collectors", label: t("somaAdmin.tabCollectors"), icon: <Database size={14} /> },
    { id: "config", label: t("somaAdmin.tabConfig"), icon: <Settings size={14} /> },
  ];

  return (
      <PageLayout title="Soma 管理">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-3 lg:py-4 gap-2">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <Bot size={18} className="text-cyan-500 shrink-0" />
          <h1 className="text-sm lg:text-lg font-semibold truncate">{t("somaAdmin.title")}</h1>
          <span className="rounded-full bg-cyan-500/10 px-1.5 lg:px-2 py-0.5 text-[10px] lg:text-xs font-medium text-cyan-500 shrink-0">
            {t("somaAdmin.badge")}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={() => { fetchDashboard(); if (activeTab === "connectors") fetchConnectors(); if (activeTab === "collectors") fetchCollectors(); }}
            className="flex items-center gap-1.5 rounded-lg border border-border px-2 lg:px-3 py-1.5 text-xs lg:text-sm hover:bg-muted touch-manipulation">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex items-center gap-1 border-b border-border px-3 lg:px-6 py-2 overflow-x-auto">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs lg:text-sm transition-colors whitespace-nowrap",
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

      <div className="flex-1 overflow-y-auto p-3 lg:p-6 space-y-3 lg:space-y-6">

        {/* ── Dashboard Tab ───────────────────────────────────────── */}
        {activeTab === "dashboard" && (
          <>
            {/* Status Cards */}
            {systemStatus && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("somaAdmin.systemStatus")}</span>
                  <p className={cn("text-xl lg:text-2xl font-bold", STATUS_COLORS[systemStatus.status] || "text-foreground")}>
                    {systemStatus.status}
                  </p>
                </div>
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("somaAdmin.version")}</span>
                  <p className="text-xl lg:text-2xl font-bold">{systemStatus.version || "-"}</p>
                </div>
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("somaAdmin.connectorsCount")}</span>
                  <p className="text-xl lg:text-2xl font-bold text-cyan-500">{systemStatus.connectors_count ?? 0}</p>
                </div>
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("somaAdmin.collectorsCount")}</span>
                  <p className="text-xl lg:text-2xl font-bold text-cyan-500">{systemStatus.collectors_count ?? 0}</p>
                </div>
              </div>
            )}

            {/* System Detail */}
            {systemStatus && (
              <div className="rounded-xl border border-border bg-card p-5 space-y-4">
                <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                  <Server size={14} className="text-cyan-500" />
                  {t("somaAdmin.systemDetail")}
                </h3>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-2 lg:gap-3 text-xs">
                  {Object.entries(systemStatus).map(([key, value]) => (
                    <div key={key}>
                      <span className="text-muted-foreground">{key}:</span>{" "}
                      <span className="font-mono">{typeof value === "object" ? JSON.stringify(value) : String(value)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Uptime */}
            {systemStatus?.uptime && (
              <div className="rounded-xl border border-dashed border-cyan-500/30 bg-cyan-500/5 p-5">
                <h3 className="text-xs lg:text-sm font-semibold text-cyan-600 mb-2">⏱ {t("somaAdmin.uptime")}</h3>
                <p className="text-xl lg:text-2xl font-bold">{systemStatus.uptime}</p>
              </div>
            )}

            {dashboardLoading && (
              <div className="flex items-center justify-center py-16 text-muted-foreground text-xs lg:text-sm">
                {t("somaAdmin.loading")}
              </div>
            )}
          </>
        )}

        {/* ── Connectors Tab ──────────────────────────────────────── */}
        {activeTab === "connectors" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                <Plug size={16} className="text-cyan-500" />
                {t("somaAdmin.connectorsTitle")}
              </h2>
              <button onClick={fetchConnectors}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                <RefreshCw size={12} /> {t("somaAdmin.refresh")}
              </button>
            </div>

            {connectorsLoading ? (
              <div className="flex items-center justify-center py-16 text-muted-foreground text-xs lg:text-sm">{t("somaAdmin.loading")}</div>
            ) : connectors.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Plug size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("somaAdmin.noConnectors")}</p>
                <p className="text-xs mt-1">{t("somaAdmin.noConnectorsHint")}</p>
              </div>
            ) : (
              <div className="flex gap-2 lg:gap-6 relative">
                {/* Connector List — full width on mobile, w-80 on desktop */}
                <div className={`${isMobile ? (selectedConnector ? "hidden" : "w-full") : "w-80"} space-y-3`}>
                  {isMobile && (
                    <div className="flex items-center justify-between pb-2 border-b border-border">
                      <h3 className="text-xs lg:text-sm font-medium">{t("somaAdmin.connectors") || "Connectors"}</h3>
                      <span className="text-xs text-muted-foreground">{connectors.length}</span>
                    </div>
                  )}
                  {connectors.map((conn) => (
                    <div key={conn.id}
                      onClick={() => setSelectedConnector(conn)}
                      className={cn(
                        "rounded-xl border border-border bg-card p-3 lg:p-4 cursor-pointer transition-all hover:shadow-md",
                        selectedConnector?.id === conn.id && "ring-2 ring-cyan-500"
                      )}>
                      <div className="flex items-center justify-between mb-1">
                        <div className="flex items-center gap-2">
                          <div className="rounded-lg bg-cyan-500/10 p-1.5">
                            <Plug size={14} className="text-cyan-500" />
                          </div>
                          <span className="font-medium text-xs lg:text-sm">{conn.name}</span>
                        </div>
                        <span className={cn("text-xs font-medium", STATUS_COLORS[conn.status] || "text-muted-foreground")}>
                          {conn.status}
                        </span>
                      </div>
                      <div className="text-xs text-muted-foreground font-mono">{conn.id}</div>
                      <div className="flex gap-2 lg:gap-3 mt-2 text-xs text-muted-foreground">
                        <span>{conn.type}</span>
                        {conn.enabled !== undefined && (
                          <span>{conn.enabled ? t("somaAdmin.enabled") : t("somaAdmin.disabled")}</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>

                {/* Connector Detail — sidebar sliding on mobile, inline on desktop */}
                {isMobile && !!selectedConnector && (
                  <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setSelectedConnector(null)} aria-hidden="true" />
                )}
                {isMobile ? (
                  !!selectedConnector && (
                    <div
                      className="absolute inset-y-0 right-0 z-10 h-full w-72 min-w-0 border-l border-border transition-[right] duration-200 ease-linear flex flex-col overflow-hidden bg-card"
                      style={{ right: 0 }}
                    >
                      <div className="flex-1 overflow-y-auto p-3 lg:p-4 space-y-4">
                        {selectedConnector && (
                          <>
                            <div>
                              <h3 className="font-semibold">{selectedConnector.name}</h3>
                              <span className="text-xs text-muted-foreground font-mono">{selectedConnector.id}</span>
                            </div>

                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 lg:gap-4 text-xs lg:text-sm">
                              <div><span className="text-muted-foreground">{t("somaAdmin.connType")}:</span> {selectedConnector.type}</div>
                              <div><span className="text-muted-foreground">{t("somaAdmin.connStatus")}:</span>{" "}
                                <span className={cn("font-medium", STATUS_COLORS[selectedConnector.status])}>{selectedConnector.status}</span>
                              </div>
                              {selectedConnector.enabled !== undefined && (
                                <div><span className="text-muted-foreground">{t("somaAdmin.connEnabled")}:</span> {selectedConnector.enabled ? t("somaAdmin.yes") : t("somaAdmin.no")}</div>
                              )}
                              {selectedConnector.last_active && (
                                <div><span className="text-muted-foreground">{t("somaAdmin.connLastActive")}:</span> {formatTime(selectedConnector.last_active, t)}</div>
                              )}
                              {selectedConnector.error_count !== undefined && (
                                <div><span className="text-muted-foreground">{t("somaAdmin.connErrors")}:</span> {selectedConnector.error_count}</div>
                              )}
                            </div>

                            {/* Toggle Button */}
                            {selectedConnector.enabled !== undefined && (
                              <button
                                onClick={() => toggleConnector(selectedConnector.name || selectedConnector.id)}
                                className={cn(
                                  "flex items-center gap-2 rounded-lg px-4 py-2 text-xs lg:text-sm font-medium transition-colors",
                                  selectedConnector.enabled
                                    ? "bg-red-500/10 text-red-500 hover:bg-red-500/20"
                                    : "bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20"
                                )}
                              >
                                {selectedConnector.enabled ? <WifiOff size={14} /> : <Wifi size={14} />}
                                {selectedConnector.enabled ? t("somaAdmin.disableConnector") : t("somaAdmin.enableConnector")}
                              </button>
                            )}

                            {/* Config */}
                            {selectedConnector.config && (
                              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                                <h4 className="text-xs text-muted-foreground mb-2">{t("somaAdmin.connConfig")}</h4>
                                <pre className="text-xs font-mono bg-background rounded p-3 overflow-auto max-h-48">
                                  {JSON.stringify(selectedConnector.config, null, 2)}
                                </pre>
                              </div>
                            )}

                            {/* Error */}
                            {selectedConnector.last_error && (
                              <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3">
                                <div className="flex items-center gap-2 text-xs text-red-500">
                                  <AlertTriangle size={12} />
                                  <span className="font-medium">{t("somaAdmin.recentError")}</span>
                                </div>
                                <p className="text-xs text-red-400 mt-1">{selectedConnector.last_error}</p>
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    </div>
                  )
                ) : (
                  selectedConnector && (
                    <div className="flex-1 space-y-4">
                      <div>
                        <h3 className="font-semibold">{selectedConnector.name}</h3>
                        <span className="text-xs text-muted-foreground font-mono">{selectedConnector.id}</span>
                      </div>

                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 lg:gap-4 text-xs lg:text-sm">
                        <div><span className="text-muted-foreground">{t("somaAdmin.connType")}:</span> {selectedConnector.type}</div>
                        <div><span className="text-muted-foreground">{t("somaAdmin.connStatus")}:</span>{" "}
                          <span className={cn("font-medium", STATUS_COLORS[selectedConnector.status])}>{selectedConnector.status}</span>
                        </div>
                        {selectedConnector.enabled !== undefined && (
                          <div><span className="text-muted-foreground">{t("somaAdmin.connEnabled")}:</span> {selectedConnector.enabled ? t("somaAdmin.yes") : t("somaAdmin.no")}</div>
                        )}
                        {selectedConnector.last_active && (
                          <div><span className="text-muted-foreground">{t("somaAdmin.connLastActive")}:</span> {formatTime(selectedConnector.last_active, t)}</div>
                        )}
                        {selectedConnector.error_count !== undefined && (
                          <div><span className="text-muted-foreground">{t("somaAdmin.connErrors")}:</span> {selectedConnector.error_count}</div>
                        )}
                      </div>

                      {/* Toggle Button */}
                      {selectedConnector.enabled !== undefined && (
                        <button
                          onClick={() => toggleConnector(selectedConnector.name || selectedConnector.id)}
                          className={cn(
                            "flex items-center gap-2 rounded-lg px-4 py-2 text-xs lg:text-sm font-medium transition-colors",
                            selectedConnector.enabled
                              ? "bg-red-500/10 text-red-500 hover:bg-red-500/20"
                              : "bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20"
                          )}
                        >
                          {selectedConnector.enabled ? <WifiOff size={14} /> : <Wifi size={14} />}
                          {selectedConnector.enabled ? t("somaAdmin.disableConnector") : t("somaAdmin.enableConnector")}
                        </button>
                      )}

                      {/* Config */}
                      {selectedConnector.config && (
                        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                          <h4 className="text-xs text-muted-foreground mb-2">{t("somaAdmin.connConfig")}</h4>
                          <pre className="text-xs font-mono bg-background rounded p-3 overflow-auto max-h-48">
                            {JSON.stringify(selectedConnector.config, null, 2)}
                          </pre>
                        </div>
                      )}

                      {/* Error */}
                      {selectedConnector.last_error && (
                        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3">
                          <div className="flex items-center gap-2 text-xs text-red-500">
                            <AlertTriangle size={12} />
                            <span className="font-medium">{t("somaAdmin.recentError")}</span>
                          </div>
                          <p className="text-xs text-red-400 mt-1">{selectedConnector.last_error}</p>
                        </div>
                      )}
                    </div>
                  )
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Collectors Tab ──────────────────────────────────────── */}
        {activeTab === "collectors" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                <Database size={16} className="text-cyan-500" />
                {t("somaAdmin.collectorsTitle")}
              </h2>
              <button onClick={fetchCollectors}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                <RefreshCw size={12} /> {t("somaAdmin.refresh")}
              </button>
            </div>

            {/* Collector Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("somaAdmin.totalCollectors")}</span>
                <p className="text-xl lg:text-2xl font-bold">{collectors.length}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("somaAdmin.runningCollectors")}</span>
                <p className="text-xl lg:text-2xl font-bold text-emerald-500">{collectors.filter(c => c.status === "running" || c.status === "active").length}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("somaAdmin.stoppedCollectors")}</span>
                <p className="text-xl lg:text-2xl font-bold text-muted-foreground">{collectors.filter(c => c.status === "stopped").length}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("somaAdmin.errorCollectors")}</span>
                <p className="text-xl lg:text-2xl font-bold text-red-500">{collectors.filter(c => c.status === "error").length}</p>
              </div>
            </div>

            {/* Collector List */}
            {collectorsLoading ? (
              <div className="flex items-center justify-center py-16 text-muted-foreground text-xs lg:text-sm">{t("somaAdmin.loading")}</div>
            ) : collectors.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Database size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("somaAdmin.noCollectors")}</p>
              </div>
            ) : (
              <div className="space-y-3">
                {collectors.map((col) => (
                  <div key={col.id} className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2 lg:gap-3">
                        <div className={cn("rounded-lg p-2",
                          col.status === "running" || col.status === "active" ? "bg-emerald-500/10" :
                          col.status === "error" ? "bg-red-500/10" : "bg-muted-foreground/10")}>
                          {col.status === "running" || col.status === "active" ? <Activity size={16} className="text-emerald-500" /> :
                           col.status === "error" ? <AlertTriangle size={16} className="text-red-500" /> :
                           <HardDrive size={16} className="text-muted-foreground" />}
                        </div>
                        <div>
                          <p className="font-medium text-xs lg:text-sm">{col.name}</p>
                          <p className="text-xs text-muted-foreground font-mono">{col.id}</p>
                        </div>
                      </div>
                      <span className={cn("text-xs font-medium px-2 py-0.5 rounded-full",
                        col.status === "running" || col.status === "active" ? "bg-emerald-500/10 text-emerald-500" :
                        col.status === "error" ? "bg-red-500/10 text-red-500" :
                        "bg-muted-foreground/10 text-muted-foreground")}>
                        {col.status}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-3 text-xs">
                      <div>
                        <span className="text-muted-foreground">{t("somaAdmin.collectorType")}:</span>{" "}
                        <span className="font-mono">{col.type}</span>
                      </div>
                      {col.events_collected !== undefined && (
                        <div>
                          <span className="text-muted-foreground">{t("somaAdmin.eventsCollected")}:</span>{" "}
                          <span className="font-semibold">{col.events_collected}</span>
                        </div>
                      )}
                      {col.last_event_at && (
                        <div>
                          <span className="text-muted-foreground">{t("somaAdmin.lastEvent")}:</span>{" "}
                          <span>{formatTime(col.last_event_at, t)}</span>
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

        {/* ── Config Tab ──────────────────────────────────────────── */}
        {activeTab === "config" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                <Settings size={16} className="text-cyan-500" />
                {t("somaAdmin.configTitle")}
              </h2>
              <button onClick={fetchConfig}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                <RefreshCw size={12} /> {t("somaAdmin.refresh")}
              </button>
            </div>

            {/* Config Display */}
            {config && (
              <div className="rounded-xl border border-border bg-card p-5 space-y-4">
                <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                  <Server size={14} className="text-cyan-500" />
                  {t("somaAdmin.systemConfig")}
                </h3>
                <pre className="text-xs font-mono bg-background rounded p-3 lg:p-4 overflow-auto max-h-96 border border-border">
                  {JSON.stringify(config, null, 2)}
                </pre>
              </div>
            )}

            {/* Connection Info */}
            <div className="rounded-xl border border-border bg-card p-5 space-y-4">
              <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                <Zap size={14} className="text-cyan-500" />
                {t("somaAdmin.connectionInfo")}
              </h3>
              <div className="grid grid-cols-2 gap-2 lg:gap-3 text-xs">
                <div>
                  <span className="text-muted-foreground">{t("somaAdmin.somaApiUrl")}:</span>{" "}
                  <span className="font-mono">{somaBase}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">{t("somaAdmin.soulApiUrl")}:</span>{" "}
                  <span className="font-mono">{apiBase}</span>
                </div>
              </div>
            </div>

            {configLoading && (
              <div className="flex items-center justify-center py-16 text-muted-foreground text-xs lg:text-sm">{t("somaAdmin.loading")}</div>
            )}
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
