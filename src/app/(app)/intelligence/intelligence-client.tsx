"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  RefreshCw, Brain, Activity, AlertTriangle, TrendingUp,
  CheckCircle, XCircle, Loader2, Zap, Shield, Clock,
  BarChart3, ChevronDown, ChevronUp, Lightbulb,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface SystemSummary {
  health_score: number;
  components: { total: number; healthy: number; unhealthy: number; unknown: number };
  performance: { avg_response_ms: number; tracked_metrics: number };
  insights: { total: number; recent_hour: number; by_severity: Record<string, number> };
}

interface ComponentDetail {
  name: string;
  health: string;
  response_time_ms: number;
  request_count: number;
  error_count: number;
  last_check: number;
  custom: Record<string, any>;
}

interface Insight {
  id: string;
  type: string;
  severity: string;
  component: string;
  title: string;
  description: string;
  recommendation: string;
  timestamp: number;
}

interface Recommendation {
  component: string;
  type: string;
  priority: string;
  title: string;
  description: string;
  suggestion: string;
}

function formatMs(ms: number): string {
  return ms < 1000 ? `${ms.toFixed(0)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function severityColor(s: string): string {
  switch (s) {
    case "critical": return "text-red-500 bg-red-500/10 border-red-500/30";
    case "high": return "text-orange-500 bg-orange-500/10 border-orange-500/30";
    case "medium": return "text-yellow-500 bg-yellow-500/10 border-yellow-500/30";
    case "low": return "text-blue-500 bg-blue-500/10 border-blue-500/30";
    default: return "text-muted-foreground bg-muted/50";
  }
}

function healthIcon(health: string) {
  if (health === "ok") return <CheckCircle className="w-4 h-4 text-green-500" />;
  if (health === "error") return <XCircle className="w-4 h-4 text-red-500" />;
  return <Clock className="w-4 h-4 text-muted-foreground" />;
}

export function IntelligenceClient() {
  const { t } = useTranslation();
  const [summary, setSummary] = useState<SystemSummary | null>(null);
  const [components, setComponents] = useState<ComponentDetail[]>([]);
  const [insights, setInsights] = useState<Insight[]>([]);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [expandedComponent, setExpandedComponent] = useState<string | null>(null);
  const [tab, setTab] = useState<"overview" | "components" | "insights" | "recommendations">("overview");
  const apiBase = getApiBaseUrl();

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [sumRes, compRes, insRes, recRes] = await Promise.all([
        fetch(`${apiBase}/api/intelligence/summary`),
        fetch(`${apiBase}/api/intelligence/components`),
        fetch(`${apiBase}/api/intelligence/insights`),
        fetch(`${apiBase}/api/intelligence/recommendations`),
      ]);
      if (sumRes.ok) setSummary(await sumRes.json());
      if (compRes.ok) {
        const d = await compRes.json();
        setComponents(d.components || []);
      }
      if (insRes.ok) {
        const d = await insRes.json();
        setInsights(d.insights || []);
      }
      if (recRes.ok) {
        const d = await recRes.json();
        setRecommendations(d.recommendations || []);
      }
    } catch {}
    setLoading(false);
  }, [apiBase]);

  const collectMetrics = useCallback(async () => {
    setCollecting(true);
    try {
      await fetch(`${apiBase}/api/intelligence/collect`, { method: "POST" });
      await fetchAll();
    } catch {}
    setCollecting(false);
  }, [apiBase, fetchAll]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  return (
      <PageLayout title="智能分析">
        
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-3 lg:p-4 border-b border-border gap-2">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <Brain className="w-5 h-5 lg:w-6 lg:h-6 text-primary shrink-0" />
          <h1 className="text-sm lg:text-lg font-semibold truncate">{t("intelligence.title") || "System Intelligence"}</h1>
          {summary && (
            <span className={cn(
              "px-1.5 lg:px-2 py-0.5 rounded-full text-[10px] lg:text-xs font-medium shrink-0",
              summary.health_score >= 90 ? "bg-green-500/10 text-green-500" :
              summary.health_score >= 70 ? "bg-yellow-500/10 text-yellow-500" :
              "bg-red-500/10 text-red-500"
            )}>
              Score: {summary.health_score}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5 lg:gap-2 shrink-0">
          <button
            onClick={collectMetrics}
            disabled={collecting}
            className="flex items-center gap-1 lg:gap-1.5 px-2 lg:px-3 py-1.5 text-xs lg:text-sm bg-primary/10 text-primary rounded-md hover:bg-primary/20 transition-colors disabled:opacity-50"
          >
            {collecting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
            <span className="hidden sm:inline">{t("intelligence.collect") || "Collect"}</span>
          </button>
          <button
            onClick={fetchAll}
            disabled={loading}
            className="flex items-center gap-1 lg:gap-1.5 px-2 lg:px-3 py-1.5 text-xs lg:text-sm bg-muted rounded-md hover:bg-muted/80 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} />
            <span className="hidden sm:inline">{t("common.refresh") || "Refresh"}</span>
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-border overflow-x-auto">
        {(["overview", "components", "insights", "recommendations"] as const).map((t_) => (
          <button
            key={t_}
            onClick={() => setTab(t_)}
            className={cn(
              "px-3 lg:px-4 py-2 text-xs lg:text-sm font-medium rounded-lg transition-colors whitespace-nowrap",
              tab === t_ ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            {t(`intelligence.${t_}`) || t_.charAt(0).toUpperCase() + t_.slice(1)}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-3 lg:p-4">
        {/* Overview Tab */}
        {tab === "overview" && summary && (
          <div className="space-y-4">
            {/* Health Score */}
            <div className="flex items-center justify-center py-3 lg:py-6">
              <div className="relative w-40 h-40">
                <svg className="w-full h-full -rotate-90" viewBox="0 0 120 120">
                  <circle cx="60" cy="60" r="50" fill="none" stroke="currentColor" strokeWidth="8" className="text-muted/30" />
                  <circle
                    cx="60" cy="60" r="50" fill="none" strokeWidth="8"
                    strokeDasharray={`${(summary.health_score / 100) * 314} 314`}
                    strokeLinecap="round"
                    className={cn(
                      summary.health_score >= 90 ? "text-green-500" :
                      summary.health_score >= 70 ? "text-yellow-500" : "text-red-500"
                    )}
                    stroke="currentColor"
                  />
                </svg>
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <span className="text-2xl lg:text-3xl font-bold">{summary.health_score}</span>
                  <span className="text-xs text-muted-foreground">{t("intelligence.healthScore") || "Health Score"}</span>
                </div>
              </div>
            </div>

            {/* Stats Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="bg-card border border-border rounded-lg p-3">
                <div className="text-xs text-muted-foreground mb-1">{t("intelligence.totalComponents") || "Components"}</div>
                <div className="text-xl lg:text-2xl font-bold">{summary.components.healthy}<span className="text-xs lg:text-sm text-muted-foreground">/{summary.components.total}</span></div>
                {summary.components.unhealthy > 0 && (
                  <div className="text-xs text-red-500 mt-1">{summary.components.unhealthy} {t("intelligence.unhealthy") || "unhealthy"}</div>
                )}
              </div>
              <div className="bg-card border border-border rounded-lg p-3">
                <div className="text-xs text-muted-foreground mb-1">{t("intelligence.avgResponse") || "Avg Response"}</div>
                <div className="text-xl lg:text-2xl font-bold">{formatMs(summary.performance.avg_response_ms)}</div>
              </div>
              <div className="bg-card border border-border rounded-lg p-3">
                <div className="text-xs text-muted-foreground mb-1">{t("intelligence.recentInsights") || "Insights (1h)"}</div>
                <div className="text-xl lg:text-2xl font-bold">{summary.insights.recent_hour}</div>
                {Object.entries(summary.insights.by_severity).map(([k, v]) => (
                  <span key={k} className={cn("text-xs mr-1", severityColor(k))}>{k}: {v}</span>
                ))}
              </div>
              <div className="bg-card border border-border rounded-lg p-3">
                <div className="text-xs text-muted-foreground mb-1">{t("intelligence.trackedMetrics") || "Tracked Metrics"}</div>
                <div className="text-xl lg:text-2xl font-bold">{summary.performance.tracked_metrics}</div>
              </div>
            </div>
          </div>
        )}

        {/* Components Tab */}
        {tab === "components" && (
          <div className="space-y-2">
            {components.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                <Activity className="w-8 h-8 mx-auto mb-2 opacity-50" />
                <p>{t("intelligence.noComponents") || "No metrics collected yet."} {t("intelligence.collectFirst") || 'Click "Collect Metrics" to gather data.'}</p>
              </div>
            ) : (
              components
                .sort((a, b) => b.response_time_ms - a.response_time_ms)
                .map((c) => (
                  <div key={c.name} className="bg-card border border-border rounded-lg">
                    <button
                      onClick={() => setExpandedComponent(expandedComponent === c.name ? null : c.name)}
                      className="w-full flex items-center justify-between p-3 hover:bg-muted/30 transition-colors"
                    >
                      <div className="flex items-center gap-2">
                        {healthIcon(c.health)}
                        <span className="font-medium text-xs lg:text-sm">{c.name}</span>
                        <span className={cn(
                          "text-xs px-1.5 py-0.5 rounded",
                          c.health === "ok" ? "bg-green-500/10 text-green-500" : "bg-red-500/10 text-red-500"
                        )}>{c.health}</span>
                      </div>
                      <div className="flex items-center gap-3 text-xs text-muted-foreground">
                        <span>{formatMs(c.response_time_ms)}</span>
                        {expandedComponent === c.name ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                      </div>
                    </button>
                    {expandedComponent === c.name && (
                      <div className="px-3 pb-3 border-t border-border pt-2">
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          <div><span className="text-muted-foreground">{t("intelligence.requestCount") || "Requests"}:</span> {c.request_count}</div>
                          <div><span className="text-muted-foreground">{t("intelligence.errorCount") || "Errors"}:</span> {c.error_count}</div>
                          <div><span className="text-muted-foreground">{t("intelligence.lastCheck") || "Last Check"}:</span> {new Date(c.last_check * 1000).toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}</div>
                        </div>
                        {Object.keys(c.custom).length > 0 && (
                          <div className="mt-2 p-2 bg-muted/30 rounded text-xs">
                            <div className="text-muted-foreground mb-1">自定义数据：</div>
                            <pre className="whitespace-pre-wrap break-all">{JSON.stringify(c.custom, null, 2).slice(0, 500)}</pre>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))
            )}
          </div>
        )}

        {/* Insights Tab */}
        {tab === "insights" && (
          <div className="space-y-2">
            {insights.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                <CheckCircle className="w-8 h-8 mx-auto mb-2 opacity-50 text-green-500" />
                <p>{t("intelligence.noInsights") || "No insights detected. System is running smoothly!"}</p>
              </div>
            ) : (
              insights.map((i) => (
                <div key={i.id} className={cn("border rounded-lg p-3", severityColor(i.severity))}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      {i.severity === "critical" ? <AlertTriangle className="w-4 h-4" /> : <TrendingUp className="w-4 h-4" />}
                      <span className="font-medium text-xs lg:text-sm">{i.title}</span>
                    </div>
                    <span className="text-xs opacity-70">{i.component}</span>
                  </div>
                  <p className="text-xs mb-1">{i.description}</p>
                  <p className="text-xs italic opacity-80">💡 {i.recommendation}</p>
                </div>
              ))
            )}
          </div>
        )}

        {/* Recommendations Tab */}
        {tab === "recommendations" && (
          <div className="space-y-2">
            {recommendations.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                <Lightbulb className="w-8 h-8 mx-auto mb-2 opacity-50 text-yellow-500" />
                <p>{t("intelligence.noRecommendations") || "No optimization recommendations. System is well-configured!"}</p>
              </div>
            ) : (
              recommendations.map((r, idx) => (
                <div key={idx} className="bg-card border border-border rounded-lg p-3">
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-medium text-xs lg:text-sm">{r.title}</span>
                    <span className={cn("text-xs px-1.5 py-0.5 rounded", 
                      r.priority === "high" ? "bg-red-500/10 text-red-500" :
                      r.priority === "medium" ? "bg-yellow-500/10 text-yellow-500" :
                      "bg-blue-500/10 text-blue-500"
                    )}>{r.priority}</span>
                  </div>
                  <p className="text-xs text-muted-foreground mb-1">{r.description}</p>
                  <p className="text-xs text-primary">💡 {r.suggestion}</p>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
