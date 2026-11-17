"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  Stethoscope, RefreshCw, Play, Zap, CheckCircle,
  XCircle, AlertTriangle, Clock, Loader2, Heart,
  Activity, Wrench, History, ChevronDown, ChevronRight,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface OrganResult {
  organ: string;
  healthy: boolean;
  severity: string;
  symptoms: string[];
  root_cause: string;
  recommended_action: string;
  action_taken: string;
  action_success: boolean;
  response_time_ms: number;
  timestamp: number;
}

interface CycleResult {
  cycle_complete: boolean;
  elapsed_seconds: number;
  total_organs: number;
  healthy: number;
  unhealthy: number;
  healed: number;
  notified: any;
  organs: OrganResult[];
}

export function HealerClient() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"dashboard" | "history" | "stats">("dashboard");
  const [health, setHealth] = useState<any>(null);
  const [results, setResults] = useState<OrganResult[]>([]);
  const [cycleResult, setCycleResult] = useState<CycleResult | null>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [healingOrg, setHealingOrg] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const apiBase = getApiBaseUrl();

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/healer/health`);
      setHealth(await res.json());
    } catch {}
  }, [apiBase]);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/healer/history?limit=100`);
      const data = await res.json();
      setHistory(data.history || []);
    } catch {}
  }, [apiBase]);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/healer/stats`);
      setStats(await res.json());
    } catch {}
  }, [apiBase]);

  useEffect(() => {
    fetchHealth();
  }, [fetchHealth]);

  useEffect(() => {
    if (tab === "history") fetchHistory();
    if (tab === "stats") fetchStats();
  }, [tab, fetchHistory, fetchStats]);

  const handleDiagnoseAll = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/healer/diagnose-all`, { method: "POST" });
      const data = await res.json();
      setResults(data.organs || []);
      fetchHealth();
    } catch {} finally { setLoading(false); }
  };

  const handleHealAll = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/healer/heal-all`, { method: "POST" });
      const data = await res.json();
      setResults(data.organs || []);
      fetchHealth();
    } catch {} finally { setLoading(false); }
  };

  const handleFullCycle = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/healer/cycle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ auto_heal: true, notify: true, audit: true }),
      });
      const data = await res.json();
      setCycleResult(data);
      setResults(data.organs || []);
      fetchHealth();
    } catch {} finally { setLoading(false); }
  };

  const handleHealSingle = async (organ: string) => {
    setHealingOrg(organ);
    try {
      const res = await fetch(`${apiBase}/api/healer/heal/${organ}`, { method: "POST" });
      const data = await res.json();
      // Update the organ in results
      setResults(prev => prev.map(r => r.organ === organ ? { ...r, ...data } : r));
      fetchHealth();
    } catch {} finally { setHealingOrg(null); }
  };

  const toggleExpand = (organ: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(organ)) next.delete(organ);
      else next.add(organ);
      return next;
    });
  };

  const severityColor = (s: string) => {
    switch (s) {
      case "info": return "text-emerald-500";
      case "warning": return "text-amber-500";
      case "critical": return "text-red-500";
      case "recovered": return "text-blue-500";
      default: return "text-muted-foreground";
    }
  };

  const severityBg = (s: string) => {
    switch (s) {
      case "info": return "bg-emerald-500/10";
      case "warning": return "bg-amber-500/10";
      case "critical": return "bg-red-500/10";
      case "recovered": return "bg-blue-500/10";
      default: return "bg-muted";
    }
  };

  const tabs = [
    { id: "dashboard" as const, label: t("healer.dashboardTab"), icon: Stethoscope },
    { id: "history" as const, label: t("healer.historyTab"), icon: History },
    { id: "stats" as const, label: t("healer.statsTab"), icon: Activity },
  ];

  return (
      <PageLayout title="自愈">
        
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3">
          <Stethoscope size={20} className="text-teal-500" />
          <h1 className="text-lg font-semibold">{t("healer.title") || "Healer · Self-Diagnosis"}</h1>
          <span className="rounded-full bg-teal-500/10 px-2 py-0.5 text-xs font-medium text-teal-500">
            {t("healer.autoDiagnoseTitle")}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={handleDiagnoseAll} disabled={loading}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted disabled:opacity-50">
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Stethoscope size={14} />}
            {t("healer.diagnoseAll")}
          </button>
          <button onClick={handleHealAll} disabled={loading}
            className="flex items-center gap-1.5 rounded-lg border border-teal-500/30 px-3 py-1.5 text-xs lg:text-sm text-teal-600 hover:bg-teal-500/10 disabled:opacity-50">
            <Wrench size={14} /> {t("healer.healAll")}
          </button>
          <button onClick={handleFullCycle} disabled={loading}
            className="flex items-center gap-1.5 rounded-lg bg-teal-500 px-3 py-1.5 text-xs lg:text-sm text-white hover:bg-teal-600 disabled:opacity-50">
            <Zap size={14} /> {t("healer.fullCycle")}
          </button>
          <button onClick={() => { fetchHealth(); }}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 lg:p-6 space-y-3 lg:space-y-6">
        {/* Stats Cards */}
        {health && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("healer.monitoredOrgans")}</span>
              <p className="text-xl lg:text-2xl font-bold">{health.monitored_organs || 0}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("healer.totalDiagnoses")}</span>
              <p className="text-xl lg:text-2xl font-bold">{health.stats?.total_diagnoses || 0}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("healer.healSuccessRate")}</span>
              <p className="text-xl lg:text-2xl font-bold text-teal-500">{health.stats?.success_rate || 0}%</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("healer.recentHealthRate")}</span>
              <p className={cn("text-xl lg:text-2xl font-bold", (health.stats?.recent_healthy_rate || 100) >= 90 ? "text-emerald-500" : "text-amber-500")}>
                {health.stats?.recent_healthy_rate || 100}%
              </p>
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-2">
          {tabs.map((tabItem) => (
            <button key={tabItem.id} onClick={() => setTab(tabItem.id)}
              className={cn("flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs lg:text-sm",
                tab === tabItem.id ? "bg-teal-500/10 text-teal-600 font-medium" : "hover:bg-muted text-muted-foreground")}>
              <tabItem.icon size={14} /> {tabItem.label}
            </button>
          ))}
        </div>

        {/* Cycle Result Banner */}
        {cycleResult && (
          <div className="rounded-xl border border-teal-500/30 bg-teal-500/5 p-3 lg:p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Zap size={16} className="text-teal-500" />
                <span className="font-medium text-xs lg:text-sm">{t("healer.cycleComplete")}</span>
              </div>
              <span className="text-xs text-muted-foreground">{cycleResult.elapsed_seconds}s</span>
            </div>
            <div className="mt-2 flex gap-2 lg:gap-4 text-xs lg:text-sm">
              <span>✅ {t("healer.cycleHealthy", { count: cycleResult.healthy })}</span>
              <span className="text-red-500">❌ {t("healer.cycleUnhealthy", { count: cycleResult.unhealthy })}</span>
              <span className="text-teal-500">💊 {t("healer.cycleHealed", { count: cycleResult.healed })}</span>
              {cycleResult.notified?.notified && <span>📨 {t("healer.cycleNotified")}</span>}
            </div>
          </div>
        )}

        {/* Dashboard Tab */}
        {tab === "dashboard" && (
          <div className="space-y-3">
            {results.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Heart size={48} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("healer.clickToStart")}</p>
                <p className="text-xs mt-1">{t("healer.clickToStartDesc")}</p>
              </div>
            ) : results.map((r) => (
              <div key={r.organ} className={cn("rounded-xl border bg-card overflow-hidden",
                !r.healthy && r.severity === "critical" ? "border-red-500/30" :
                !r.healthy ? "border-amber-500/30" :
                r.severity === "recovered" ? "border-blue-500/30" : "border-border")}>
                <div className="flex items-center justify-between p-3 lg:p-4 cursor-pointer"
                  onClick={() => toggleExpand(r.organ)}>
                  <div className="flex items-center gap-2 lg:gap-3">
                    {expanded.has(r.organ) ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    {r.healthy ? (
                      <CheckCircle size={18} className="text-emerald-500" />
                    ) : r.severity === "recovered" ? (
                      <CheckCircle size={18} className="text-blue-500" />
                    ) : (
                      <XCircle size={18} className="text-red-500" />
                    )}
                    <span className="font-medium text-xs lg:text-sm capitalize">{r.organ}</span>
                    <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium",
                      severityBg(r.severity), severityColor(r.severity))}>
                      {r.severity}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 lg:gap-3">
                    <span className="text-xs text-muted-foreground">{r.response_time_ms}ms</span>
                    {!r.healthy && r.severity !== "recovered" && (
                      <button onClick={(e) => { e.stopPropagation(); handleHealSingle(r.organ); }}
                        disabled={healingOrg === r.organ}
                        className="flex items-center gap-1 rounded-lg bg-teal-500 px-2.5 py-1 text-xs text-white hover:bg-teal-600 disabled:opacity-50">
                        {healingOrg === r.organ ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />}
                        {t("healer.healAction")}
                      </button>
                    )}
                  </div>
                </div>

                {expanded.has(r.organ) && (
                  <div className="border-t border-border px-4 pb-4 pt-2 space-y-2">
                    {r.symptoms.length > 0 && (
                      <div>
                        <span className="text-xs font-medium text-muted-foreground">{t("healer.symptomLabel")}</span>
                        <div className="flex flex-wrap gap-1.5 mt-1">
                          {r.symptoms.map((s, i) => (
                            <span key={i} className="rounded bg-muted px-2 py-0.5 text-xs">{s}</span>
                          ))}
                        </div>
                      </div>
                    )}
                    {r.root_cause && (
                      <div>
                        <span className="text-xs font-medium text-muted-foreground">{t("healer.rootCauseLabel")}</span>
                        <p className="text-xs mt-1">{r.root_cause}</p>
                      </div>
                    )}
                    {r.action_taken !== "none" && (
                      <div>
                        <span className="text-xs font-medium text-muted-foreground">{t("healer.healActionLabel")}</span>
                        <p className="text-xs mt-1">
                          {r.action_taken} → {r.action_success ? `✅ ${t("healer.actionSuccess")}` : `❌ ${t("healer.actionFailed")}`}
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* History Tab */}
        {tab === "history" && (
          <div className="space-y-2">
            {history.length === 0 ? (
              <p className="text-center text-muted-foreground text-xs lg:text-sm py-8">{t("healer.noHistory")}</p>
            ) : history.map((h, i) => (
              <div key={i} className="flex items-center justify-between rounded-lg border border-border bg-card p-3">
                <div className="flex items-center gap-2 lg:gap-3">
                  {h.healthy ? (
                    <CheckCircle size={16} className="text-emerald-500" />
                  ) : (
                    <XCircle size={16} className="text-red-500" />
                  )}
                  <span className="text-xs lg:text-sm font-medium capitalize">{h.organ}</span>
                  <span className={cn("text-xs", severityColor(h.severity))}>{h.severity}</span>
                  {h.root_cause && <span className="text-xs text-muted-foreground truncate max-w-xs">{h.root_cause}</span>}
                </div>
                <div className="flex items-center gap-2 lg:gap-3">
                  {h.action_taken !== "none" && (
                    <span className="text-xs text-muted-foreground">{h.action_taken} {h.action_success ? "✅" : "❌"}</span>
                  )}
                  <span className="text-xs text-muted-foreground">{h.response_time_ms}ms</span>
                  <span className="text-xs text-muted-foreground">
                    {new Date(h.timestamp * 1000).toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Stats Tab */}
        {tab === "stats" && stats && (
          <div className="space-y-3 lg:space-y-6">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2 lg:gap-4">
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("healer.totalDiagnosesLabel")}</span>
                <p className="text-xl lg:text-2xl font-bold">{stats.total_diagnoses}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("healer.healedLabel")}</span>
                <p className="text-xl lg:text-2xl font-bold text-emerald-500">{stats.healed}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("healer.failedLabel")}</span>
                <p className="text-xl lg:text-2xl font-bold text-red-500">{stats.failed}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("healer.actionsExecuted")}</span>
                <p className="text-xl lg:text-2xl font-bold">{stats.actions_taken}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("healer.actionsSucceeded")}</span>
                <p className="text-xl lg:text-2xl font-bold text-emerald-500">{stats.actions_succeeded}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("healer.successRateLabel")}</span>
                <p className="text-xl lg:text-2xl font-bold text-teal-500">{stats.success_rate}%</p>
              </div>
            </div>

            {/* Organ failure frequency */}
            {stats.organ_failure_frequency && Object.keys(stats.organ_failure_frequency).length > 0 && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <h3 className="text-xs lg:text-sm font-medium mb-3">{t("healer.organFaultFrequency")}</h3>
                <div className="space-y-2">
                  {Object.entries(stats.organ_failure_frequency).map(([organ, count]) => (
                    <div key={organ} className="flex items-center gap-2 lg:gap-3">
                      <span className="text-xs lg:text-sm font-medium w-24 capitalize">{organ}</span>
                      <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                        <div className="h-full bg-red-500/60 rounded-full"
                          style={{ width: `${Math.min(100, (count as number) / Math.max(...Object.values(stats.organ_failure_frequency).map(Number)) * 100)}%` }} />
                      </div>
                      <span className="text-xs text-muted-foreground w-8 text-right">{count as number}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
