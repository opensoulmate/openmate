"use client";
import { useState, useEffect, useCallback } from "react";
import { getApiBaseUrl } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import { useVisibilityPoll } from "@/hooks/use-visibility-poll";
import {
  Activity, RefreshCw, Loader2, CheckCircle2, XCircle, AlertCircle,
  Clock, Wifi, Cpu, HardDrive, MemoryStick, Network, AlertTriangle,
  TrendingUp, TrendingDown, Server, Database, Zap, Shield, BarChart3,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import i18n from "@/lib/i18n";
import { PageLayout } from '@/components/page-layout';

interface ComponentStatus {
  name: string;
  status: string;
  latency_ms: number;
  message: string;
}

interface HealthReport {
  status: string;
  components: ComponentStatus[];
  ts: number;
}

interface SystemMetrics {
  cpu_percent: number;
  memory_percent: number;
  memory_used_mb: number;
  memory_total_mb: number;
  disk_percent: number;
  disk_used_gb: number;
  disk_total_gb: number;
  net_sent_bytes: number;
  net_recv_bytes: number;
  request_qps: number;
  latency_p99_ms: number;
  error_rate: number;
  requests_total: number;
  errors_total: number;
  knowledge_entries: number;
  agents_online: number;
  search_count: number;
}

interface Alert {
  rule: string;
  severity: string;
  message: string;
  value: number;
  threshold: number;
  resolved: boolean;
  ts: number;
}

interface HistoryEntry {
  ts: number;
  cpu: number;
  mem: number;
  mem_mb: number;
  disk: number;
  qps: number;
  p99: number;
  err_rate: number;
  requests: number;
  errors: number;
  knowledge: number;
}

const STATUS_CONFIG: Record<string, { color: string; bg: string; border: string; icon: typeof CheckCircle2; label: string }> = {
  up: { color: "text-emerald-500", bg: "bg-emerald-500/8", border: "border-emerald-500/20", icon: CheckCircle2, label: "UP" },
  down: { color: "text-red-500", bg: "bg-red-500/8", border: "border-red-500/20", icon: XCircle, label: "DOWN" },
  skipped: { color: "text-yellow-500", bg: "bg-yellow-500/8", border: "border-yellow-500/20", icon: AlertCircle, label: "SKIPPED" },
};

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function formatRelativeTime(ts: number): string {
  const diff = Date.now() - ts * 1000;
  if (diff < 60_000) return i18n.t("vital.justNow");
  if (diff < 3_600_000) return i18n.t("vital.minutesAgo", { count: Math.floor(diff / 60_000) });
  if (diff < 86_400_000) return i18n.t("vital.hoursAgo", { count: Math.floor(diff / 3_600_000) });
  return i18n.t("vital.daysAgo", { count: Math.floor(diff / 86_400_000) });
}

function GaugeBar({ value, max = 100, color = "emerald", label, detail }: {
  value: number; max?: number; color?: string; label: string; detail?: string;
}) {
  const pct = Math.min((value / max) * 100, 100);
  const colorClass = pct > 90 ? "bg-red-500" : pct > 70 ? "bg-yellow-500" : `bg-${color}-500`;
  const textClass = pct > 90 ? "text-red-500" : pct > 70 ? "text-yellow-500" : `text-${color}-500`;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <span className={cn("text-xs font-semibold tabular-nums", textClass)}>
          {detail || `${pct.toFixed(1)}%`}
        </span>
      </div>
      <div className="h-2 rounded-full bg-muted/50 overflow-hidden">
        <div
          className={cn("h-full rounded-full transition-all duration-700 ease-out", colorClass)}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function VitalClient() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();

  // Health state
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [healthLoading, setHealthLoading] = useState(false);

  // Metrics state
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(false);

  // Alerts state
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [alertsLoading, setAlertsLoading] = useState(false);

  // History state
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyMinutes, setHistoryMinutes] = useState(30);

  const [lastFetch, setLastFetch] = useState<Date | null>(null);
  const [activeTab, setActiveTab] = useState<"health" | "metrics" | "alerts" | "history">("metrics");

  const fetchHealth = useCallback(async () => {
    setHealthLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/vital/health`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: HealthReport = await res.json();
      setHealth(data);
      setHealthError(null);
    } catch (e) {
      setHealthError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setHealthLoading(false);
    }
  }, [apiBase]);

  const fetchMetrics = useCallback(async () => {
    setMetricsLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/vital/metrics`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const text = await res.text();
      // Parse Prometheus-style metrics
      const parsed: Record<string, number> = {};
      text.split("\n").forEach(line => {
        const [key, val] = line.split(" ");
        if (key && val) {
          const name = key.replace("vital_", "");
          parsed[name] = parseFloat(val);
        }
      });
      setMetrics(parsed as unknown as SystemMetrics);
    } catch {
      // silent
    } finally {
      setMetricsLoading(false);
    }
  }, [apiBase]);

  const fetchAlerts = useCallback(async () => {
    setAlertsLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/vital/alerts`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setAlerts(data.alerts || []);
    } catch {
      // silent
    } finally {
      setAlertsLoading(false);
    }
  }, [apiBase]);

  const fetchHistory = useCallback(async (minutes?: number) => {
    setHistoryLoading(true);
    try {
      const m = minutes ?? historyMinutes;
      const res = await fetch(`${apiBase}/api/vital/history?minutes=${m}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setHistory(data.data || []);
    } catch {
      // silent
    } finally {
      setHistoryLoading(false);
    }
  }, [apiBase, historyMinutes]);

  const fetchAll = useCallback(async () => {
    await Promise.all([fetchHealth(), fetchMetrics(), fetchAlerts(), fetchHistory()]);
    setLastFetch(new Date());
  }, [fetchHealth, fetchMetrics, fetchAlerts, fetchHistory]);

  useVisibilityPoll(fetchAll, 30_000, []);

  const upCount = health?.components.filter(c => c.status === "up").length || 0;
  const downCount = health?.components.filter(c => c.status === "down").length || 0;
  const totalCount = health?.components.length || 0;
  const avgLatency = health ? Math.round(health.components.reduce((s, c) => s + c.latency_ms, 0) / (totalCount || 1)) : 0;
  const activeAlerts = alerts.filter(a => !a.resolved).length;
  const criticalAlerts = alerts.filter(a => !a.resolved && a.severity === "critical").length;

  const tabs = [
    { key: "metrics" as const, label: t("vital.tabMetrics") || "System Metrics", icon: Activity },
    { key: "health" as const, label: t("vital.tabHealth") || "Health", icon: Server, badge: downCount > 0 ? downCount : undefined },
    { key: "history" as const, label: t("vital.tabHistory") || "History", icon: BarChart3 },
    { key: "alerts" as const, label: t("vital.tabAlerts") || "Alerts", icon: AlertTriangle, badge: activeAlerts > 0 ? activeAlerts : undefined },
  ];

  return (
      <PageLayout title="Vital">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-emerald-500/10">
            <Activity size={18} className="text-emerald-500" />
          </div>
          <div>
            <h1 className="text-base font-semibold">{t("vital.title") || "Vitals · System Monitor"}</h1>
            <p className="text-xs text-muted-foreground">{t("vital.subtitle") || "Real-time monitoring of system health, performance metrics, and alerts"}</p>
          </div>
        </div>
        <div className="flex items-center gap-2 lg:gap-3">
          {lastFetch && (
            <span className="text-[10px] text-muted-foreground">
              {lastFetch.toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}
            </span>
          )}
          <button
            onClick={fetchAll}
            disabled={healthLoading || metricsLoading || alertsLoading}
            className="flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted transition-colors disabled:opacity-50"
          >
            {(healthLoading || metricsLoading || alertsLoading) ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <RefreshCw size={12} />
            )}
            {t("common.refresh") || t("vital.refreshAction")}
          </button>
        </div>
      </div>

      {/* Overview Cards */}
      {metrics && (
        <div className="border-b border-border px-3 lg:px-6 py-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-3">
            <MetricCard
              icon={Cpu}
              label="CPU"
              value={`${(metrics.cpu_percent || 0).toFixed(1)}%`}
              status={(metrics.cpu_percent || 0) > 90 ? "critical" : (metrics.cpu_percent || 0) > 70 ? "warning" : "ok"}
            />
            <MetricCard
              icon={MemoryStick}
              label={t("vital.memoryLabel")}
              value={`${(metrics.memory_percent || 0).toFixed(1)}%`}
              detail={`${((metrics.memory_used_mb || 0) / 1024).toFixed(1)} / ${((metrics.memory_total_mb || 0) / 1024).toFixed(1)} GB`}
              status={(metrics.memory_percent || 0) > 90 ? "critical" : (metrics.memory_percent || 0) > 70 ? "warning" : "ok"}
            />
            <MetricCard
              icon={HardDrive}
              label={t("vital.diskLabel")}
              value={`${(metrics.disk_percent || 0).toFixed(1)}%`}
              detail={`${(metrics.disk_used_gb || 0).toFixed(0)} / ${(metrics.disk_total_gb || 0).toFixed(0)} GB`}
              status={(metrics.disk_percent || 0) > 95 ? "critical" : (metrics.disk_percent || 0) > 85 ? "warning" : "ok"}
            />
            <MetricCard
              icon={Network}
              label={t("vital.networkLabel")}
              value={`${formatBytes(metrics.net_sent_bytes || 0)} ↑`}
              detail={`${formatBytes(metrics.net_recv_bytes || 0)} ↓`}
              status="ok"
            />
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border px-3 lg:px-6">
        {tabs.map(tab => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={cn(
                "flex items-center gap-2 px-4 py-2.5 text-xs font-medium rounded-lg transition-colors",
                activeTab === tab.key
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              )}
            >
              <Icon size={14} />
              {tab.label}
              {tab.badge !== undefined && tab.badge > 0 && (
                <span className="ml-1 rounded-full bg-red-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-red-500">
                  {tab.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-3 lg:p-6">
        {/* Error banner */}
        {healthError && activeTab === "health" && (
          <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 p-3 lg:p-4 text-xs lg:text-sm text-red-500 flex items-center gap-2">
            <XCircle size={16} />
            {t("vital.fetchError") || "Failed to fetch health data"}: {healthError}
          </div>
        )}

        {/* Metrics Tab */}
        {activeTab === "metrics" && metrics && (
          <div className="space-y-3 lg:space-y-6">
            {/* Resource Gauges */}
            <Section title={t("vital.resourceUsage")} icon={Server}>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2 lg:gap-4">
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
                  <GaugeBar value={metrics.cpu_percent || 0} label={t("vital.cpuUsage")} color="blue" />
                  <GaugeBar value={metrics.memory_percent || 0} label={t("vital.memoryUsage")} color="purple" detail={`${((metrics.memory_used_mb || 0) / 1024).toFixed(1)} / ${((metrics.memory_total_mb || 0) / 1024).toFixed(1)} GB`} />
                  <GaugeBar value={metrics.disk_percent || 0} label={t("vital.diskUsage")} color="amber" detail={`${(metrics.disk_used_gb || 0).toFixed(0)} / ${(metrics.disk_total_gb || 0).toFixed(0)} GB`} />
                </div>
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
                  <div className="grid grid-cols-2 gap-2 lg:gap-3">
                    <MiniStat label={t("vital.requestQps")} value={(metrics.request_qps || 0).toFixed(1)} icon={Zap} />
                    <MiniStat label={t("vital.p99Latency")} value={`${(metrics.latency_p99_ms || 0).toFixed(0)}ms`} icon={Clock} />
                    <MiniStat label={t("vital.totalRequests")} value={String(metrics.requests_total || 0)} icon={TrendingUp} />
                    <MiniStat label={t("vital.errorRateLabel")} value={`${((metrics.error_rate || 0) * 100).toFixed(2)}%`} icon={AlertCircle} danger={(metrics.error_rate || 0) > 0.01} />
                  </div>
                </div>
              </div>
            </Section>

            {/* Business Metrics */}
            <Section title={t("vital.businessMetricsTitle")} icon={Database}>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-3">
                <StatCard label={t("vital.knowledgeEntriesLabel")} value={String(metrics.knowledge_entries || 0)} icon={Database} color="blue" />
                <StatCard label={t("vital.onlineAgents")} value={String(metrics.agents_online || 0)} icon={Server} color="emerald" />
                <StatCard label={t("vital.searchCountLabel")} value={String(metrics.search_count || 0)} icon={Activity} color="purple" />
                <StatCard label={t("vital.totalErrors")} value={String(metrics.errors_total || 0)} icon={AlertCircle} color={Number(metrics.errors_total) > 0 ? "red" : "emerald"} />
              </div>
            </Section>

            {/* Network */}
            <Section title={t("vital.networkTraffic")} icon={Network}>
              <div className="grid grid-cols-2 gap-2 lg:gap-3">
                <StatCard label={t("vital.uploadTraffic")} value={formatBytes(metrics.net_sent_bytes || 0)} icon={TrendingUp} color="blue" />
                <StatCard label={t("vital.downloadTrafficLabel")} value={formatBytes(metrics.net_recv_bytes || 0)} icon={TrendingDown} color="emerald" />
              </div>
            </Section>
          </div>
        )}

        {/* Health Tab */}
        {activeTab === "health" && health && (
          <div className="space-y-4">
            {/* Summary */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 lg:gap-3">
              <OverviewCard
                label={t("vital.overallStatus") || "Overall Status"}
                value={health.status.toUpperCase()}
                icon={health.status === "ok" ? CheckCircle2 : XCircle}
                valueClass={health.status === "ok" ? "text-emerald-500" : "text-red-500"}
              />
              <OverviewCard
                label={t("vital.healthyNodes") || "Healthy Nodes"}
                value={`${upCount}/${totalCount}`}
                icon={Wifi}
                valueClass="text-emerald-500"
              />
              <OverviewCard
                label={t("vital.errorNodes") || "Error Nodes"}
                value={String(downCount)}
                icon={XCircle}
                valueClass={downCount > 0 ? "text-red-500" : "text-emerald-500"}
              />
              <OverviewCard
                label={t("vital.avgLatency") || "Avg Latency"}
                value={`${avgLatency}ms`}
                icon={Clock}
                valueClass="text-muted-foreground"
              />
            </div>

            {/* Component list */}
            <div className="space-y-2">
              <h2 className="text-xs lg:text-sm font-medium text-muted-foreground mb-3">
                {t("vital.components") || "Components"} ({totalCount})
              </h2>
              {health.components.map((comp) => {
                const cfg = STATUS_CONFIG[comp.status] || STATUS_CONFIG.down;
                const Icon = cfg.icon;
                return (
                  <div
                    key={comp.name}
                    className={cn(
                      "flex items-center rounded-xl border p-3 lg:p-4 transition-colors",
                      cfg.border, cfg.bg
                    )}
                  >
                    <Icon size={16} className={cn("shrink-0", cfg.color)} />
                    <span className="ml-3 text-xs lg:text-sm font-medium min-w-[140px] capitalize">
                      {comp.name}
                    </span>
                    <span className={cn(
                      "ml-2 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
                      cfg.color, cfg.bg
                    )}>
                      {cfg.label}
                    </span>
                    <span className="ml-auto text-xs text-muted-foreground font-mono tabular-nums">
                      {comp.latency_ms}ms
                    </span>
                    {comp.message && comp.message !== "ok" && (
                      <span className="ml-3 text-xs text-muted-foreground truncate max-w-[200px]">
                        {comp.message}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* History Tab */}
        {activeTab === "history" && (
          <div className="space-y-3 lg:space-y-6">
            {/* Time range selector */}
            <div className="flex items-center gap-2 lg:gap-3">
              <span className="text-xs lg:text-sm text-muted-foreground">{t("vital.timeRange")}</span>
              {[10, 30, 60, 120].map(m => (
                <button
                  key={m}
                  onClick={() => { setHistoryMinutes(m); fetchHistory(m); }}
                  className={cn(
                    "rounded-lg px-3 py-1.5 text-xs font-medium border transition-colors",
                    historyMinutes === m
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border bg-muted/30 text-muted-foreground hover:bg-muted"
                  )}
                >
                  {m < 60 ? t("vital.minutesUnit", { count: m }) : t("vital.hoursUnit", { count: m / 60 })}
                </button>
              ))}
              <button
                onClick={() => fetchHistory()}
                disabled={historyLoading}
                className="ml-auto flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted transition-colors disabled:opacity-50"
              >
                {historyLoading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                {t("vital.refreshAction")}
              </button>
            </div>

            {history.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
                <BarChart3 size={32} className="mb-3 opacity-50" />
                <p className="text-xs lg:text-sm">{t("vital.noHistoryData")}</p>
                <p className="text-xs mt-1">{t("vital.collectingData")}</p>
              </div>
            ) : (
              <>
                {/* CPU + Memory Chart */}
                <div className="rounded-xl border border-border bg-card p-5">
                  <h3 className="text-xs lg:text-sm font-medium mb-4 flex items-center gap-2">
                    <Cpu size={14} className="text-blue-500" />
                    {t("vital.cpuMemoryTitle")}
                  </h3>
                  <MiniChart
                    data={history}
                    series={[
                      { key: "cpu", label: "CPU %", color: "#3b82f6", max: 100 },
                      { key: "mem", label: t("vital.memoryPercent"), color: "#a855f7", max: 100 },
                    ]}
                    height={160}
                  />
                </div>

                {/* Disk Chart */}
                <div className="rounded-xl border border-border bg-card p-5">
                  <h3 className="text-xs lg:text-sm font-medium mb-4 flex items-center gap-2">
                    <HardDrive size={14} className="text-amber-500" />
                    {t("vital.diskUsageTitle")}
                  </h3>
                  <MiniChart
                    data={history}
                    series={[
                      { key: "disk", label: t("vital.diskPercentLabel"), color: "#f59e0b", max: 100 },
                    ]}
                    height={120}
                  />
                </div>

                {/* QPS + Latency Chart */}
                <div className="rounded-xl border border-border bg-card p-5">
                  <h3 className="text-xs lg:text-sm font-medium mb-4 flex items-center gap-2">
                    <Zap size={14} className="text-emerald-500" />
                    {t("vital.qpsLatencyTitle")}
                  </h3>
                  <MiniChart
                    data={history}
                    series={[
                      { key: "qps", label: "QPS", color: "#10b981" },
                      { key: "p99", label: "P99 ms", color: "#f97316" },
                    ]}
                    height={160}
                  />
                </div>

                {/* Summary Stats */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-3">
                  <StatCard
                    label={t("vital.cpuPeakLabel")}
                    value={`${Math.max(...history.map(h => h.cpu)).toFixed(1)}%`}
                    icon={Cpu}
                    color="blue"
                  />
                  <StatCard
                    label={t("vital.memoryPeakLabel")}
                    value={`${Math.max(...history.map(h => h.mem)).toFixed(1)}%`}
                    icon={MemoryStick}
                    color="purple"
                  />
                  <StatCard
                    label={t("vital.qpsPeakLabel")}
                    value={Math.max(...history.map(h => h.qps)).toFixed(2)}
                    icon={Zap}
                    color="emerald"
                  />
                  <StatCard
                    label={t("vital.dataPointsLabel")}
                    value={String(history.length)}
                    icon={BarChart3}
                    color="amber"
                  />
                </div>
              </>
            )}
          </div>
        )}

        {/* Alerts Tab */}
        {activeTab === "alerts" && (
          <div className="space-y-4">
            {alerts.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
                <Shield size={32} className="mb-3 opacity-50" />
                <p className="text-xs lg:text-sm">{t("vital.noAlerts")}</p>
                <p className="text-xs mt-1">{t("vital.allNormalDesc")}</p>
              </div>
            ) : (
              <>
                <div className="flex items-center gap-2 lg:gap-3 mb-2">
                  <span className="text-xs lg:text-sm text-muted-foreground">
                    {t("vital.alertSummary", { total: alerts.length, active: activeAlerts })}
                  </span>
                  {criticalAlerts > 0 && (
                    <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-xs font-semibold text-red-500">
                      {t("vital.criticalCount", { count: criticalAlerts })}
                    </span>
                  )}
                </div>
                {alerts.map((alert, idx) => (
                  <div
                    key={idx}
                    className={cn(
                      "flex items-start gap-2 lg:gap-3 rounded-xl border p-3 lg:p-4 transition-colors",
                      alert.resolved
                        ? "border-border bg-card opacity-60"
                        : alert.severity === "critical"
                          ? "border-red-500/30 bg-red-500/5"
                          : "border-yellow-500/30 bg-yellow-500/5"
                    )}
                  >
                    <div className={cn(
                      "flex items-center justify-center w-8 h-8 rounded-lg shrink-0",
                      alert.resolved
                        ? "bg-muted"
                        : alert.severity === "critical"
                          ? "bg-red-500/10"
                          : "bg-yellow-500/10"
                    )}>
                      {alert.resolved ? (
                        <CheckCircle2 size={16} className="text-muted-foreground" />
                      ) : alert.severity === "critical" ? (
                        <XCircle size={16} className="text-red-500" />
                      ) : (
                        <AlertTriangle size={16} className="text-yellow-500" />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className={cn(
                          "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase",
                          alert.severity === "critical"
                            ? "bg-red-500/15 text-red-500"
                            : "bg-yellow-500/15 text-yellow-500"
                        )}>
                          {alert.severity}
                        </span>
                        <span className="text-xs text-muted-foreground font-mono">{alert.rule}</span>
                        {alert.resolved && (
                          <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] text-emerald-500">
                            {t("vital.recoveredLabel")}
                          </span>
                        )}
                      </div>
                      <p className="text-xs lg:text-sm">{alert.message}</p>
                      <div className="flex items-center gap-2 lg:gap-3 mt-1.5 text-xs text-muted-foreground">
                        <span>{t("vital.currentValue", { value: alert.value })}</span>
                        <span>{t("vital.thresholdValue", { value: alert.threshold })}</span>
                        <span>{formatRelativeTime(alert.ts)}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </>
            )}
          </div>
        )}

        {/* Loading state */}
        {!health && !metrics && !healthError && (
          <div className="flex flex-col items-center justify-center py-20">
            <Loader2 size={28} className="animate-spin text-primary mb-3" />
            <p className="text-xs lg:text-sm text-muted-foreground">{t("vital.checking") || "Checking system status..."}</p>
          </div>
        )}

        {/* Footer */}
        {lastFetch && (
          <div className="flex items-center justify-between text-[10px] text-muted-foreground pt-4 mt-6 border-t border-border">
            <span>
              {t("vital.lastUpdated") || "Last updated"}: {lastFetch.toLocaleString('zh-CN', { fractionalSecondDigits: 3 })}
            </span>
            <span>{t("vital.autoRefresh") || "Auto-refreshes every 30 seconds"}</span>
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}

function Section({ title, icon: Icon, children }: { title: string; icon: React.ElementType; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <Icon size={14} className="text-muted-foreground" />
        <h3 className="text-xs lg:text-sm font-medium">{title}</h3>
      </div>
      {children}
    </div>
  );
}

function MetricCard({ icon: Icon, label, value, detail, status }: {
  icon: React.ElementType; label: string; value: string; detail?: string; status: "ok" | "warning" | "critical";
}) {
  const statusColors = {
    ok: "text-emerald-500 bg-emerald-500/8 border-emerald-500/20",
    warning: "text-yellow-500 bg-yellow-500/8 border-yellow-500/20",
    critical: "text-red-500 bg-red-500/8 border-red-500/20",
  };
  return (
    <div className={cn("rounded-xl border p-3", statusColors[status])}>
      <div className="flex items-center gap-2 mb-1">
        <Icon size={14} className="opacity-70" />
        <span className="text-[11px] uppercase tracking-wider opacity-70">{label}</span>
      </div>
      <div className="text-lg font-semibold">{value}</div>
      {detail && <div className="text-[11px] opacity-60 mt-0.5">{detail}</div>}
    </div>
  );
}

function StatCard({ label, value, icon: Icon, color }: {
  label: string; value: string; icon: React.ElementType; color: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-2">
        <Icon size={14} className="text-muted-foreground" />
        <span className="text-[11px] text-muted-foreground uppercase tracking-wider">{label}</span>
      </div>
      <div className="text-xl font-semibold">{value}</div>
    </div>
  );
}

function MiniStat({ label, value, icon: Icon, danger }: {
  label: string; value: string; icon: React.ElementType; danger?: boolean;
}) {
  return (
    <div className="rounded-lg bg-muted/30 p-3">
      <div className="flex items-center gap-1.5 mb-1">
        <Icon size={12} className="text-muted-foreground" />
        <span className="text-[10px] text-muted-foreground">{label}</span>
      </div>
      <div className={cn("text-xs lg:text-sm font-semibold tabular-nums", danger && "text-red-500")}>{value}</div>
    </div>
  );
}

function OverviewCard({ label, value, icon: Icon, valueClass }: {
  label: string; value: string; icon: React.ElementType; valueClass: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-2">
        <Icon size={14} className="text-muted-foreground" />
        <span className="text-[11px] text-muted-foreground uppercase tracking-wider">{label}</span>
      </div>
      <div className={cn("text-xl font-semibold", valueClass)}>{value}</div>
    </div>
  );
}

// ── SVG Mini Chart ────────────────────────────────────────

interface SeriesConfig {
  key: string;
  label: string;
  color: string;
  max?: number;
}

function MiniChart({ data, series, height = 160 }: {
  data: HistoryEntry[];
  series: SeriesConfig[];
  height?: number;
}) {
  if (data.length < 2) {
    return (
      <div className="flex items-center justify-center text-muted-foreground text-xs" style={{ height }}>
        {i18n.t("vital.needTwoDataPoints")}
      </div>
    );
  }

  const W = 800;
  const H = height;
  const pad = { top: 20, right: 60, bottom: 30, left: 50 };
  const cw = W - pad.left - pad.right;
  const ch = H - pad.top - pad.bottom;

  // Helper to get value from entry by key
  const val = (d: HistoryEntry, key: string): number => {
    const rec = d as unknown as Record<string, number>;
    return rec[key] ?? 0;
  };

  // Calculate global max for each series
  const seriesMax = series.map(s => {
    if (s.max) return s.max;
    const vals = data.map(d => val(d, s.key));
    return Math.max(...vals) * 1.2 || 1;
  });

  // Build path for each series
  const paths = series.map((s, si) => {
    const max = seriesMax[si];
    const points = data.map((d, i) => {
      const x = pad.left + (i / (data.length - 1)) * cw;
      const v = val(d, s.key);
      const y = pad.top + ch - (v / max) * ch;
      return `${x},${y}`;
    });
    return { ...s, path: `M${points.join("L")}`, max };
  });

  // Y-axis ticks (5 ticks)
  const yTicks = [0, 0.25, 0.5, 0.75, 1];

  // X-axis labels (show ~6 evenly spaced)
  const xLabelCount = Math.min(6, data.length);
  const xStep = Math.floor((data.length - 1) / (xLabelCount - 1));
  const xLabels = Array.from({ length: xLabelCount }, (_, i) => {
    const idx = Math.min(i * xStep, data.length - 1);
    const d = new Date(data[idx].ts * 1000);
    return {
      x: pad.left + (idx / (data.length - 1)) * cw,
      label: d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 }),
    };
  });

  return (
    <div className="space-y-3">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height }}>
        {/* Grid lines */}
        {yTicks.map(t => {
          const y = pad.top + ch - t * ch;
          return (
            <g key={t}>
              <line x1={pad.left} y1={y} x2={pad.left + cw} y2={y} stroke="currentColor" strokeOpacity={0.08} />
              <text x={pad.left - 8} y={y + 4} textAnchor="end" fontSize={10} fill="currentColor" opacity={0.4}>
                {Math.round(t * seriesMax[0])}
              </text>
            </g>
          );
        })}

        {/* X-axis labels */}
        {xLabels.map((xl, i) => (
          <text key={i} x={xl.x} y={H - 4} textAnchor="middle" fontSize={10} fill="currentColor" opacity={0.4}>
            {xl.label}
          </text>
        ))}

        {/* Data lines */}
        {paths.map(p => (
          <path
            key={p.key}
            d={p.path}
            fill="none"
            stroke={p.color}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ))}

        {/* Area fill for first series */}
        {paths.length > 0 && (
          <path
            d={`${paths[0].path}L${pad.left + cw},${pad.top + ch}L${pad.left},${pad.top + ch}Z`}
            fill={paths[0].color}
            fillOpacity={0.06}
          />
        )}
      </svg>

      {/* Legend */}
      <div className="flex items-center gap-2 lg:gap-4 justify-center">
        {series.map(s => (
          <div key={s.key} className="flex items-center gap-1.5">
            <div className="w-3 h-0.5 rounded-full" style={{ backgroundColor: s.color }} />
            <span className="text-[11px] text-muted-foreground">{s.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
