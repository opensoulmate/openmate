"use client";

import { useAppStore } from "@/stores/app-store";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import {
  TrendingUp, Cpu, Zap,
  HardDrive,
} from "lucide-react";
import { useState, useEffect, useCallback, useMemo } from "react";
import { getApiBaseUrl } from "@/lib/api-client";
import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { PageLayout } from '@/components/page-layout';
import { LeftPanel } from '@/components/left-panel';
import { COLORS, LINE_THEME, PIE_THEME, GAUGE_THEME, COLOR_PALETTE, BASE_CHART } from '@/lib/echarts-theme';

import { EChart } from '@/components/echart';

// ── Interfaces ──────────────────────────────────────────────────

interface UsageSummary {
  total_tokens: number;
  budget_limit: number | null;
  remaining_budget: number | null;
  call_count: number;
  by_model: Record<string, number>;
  by_user: Record<string, number>;
  by_provider: Record<string, number>;
}

interface RecentRecord {
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  user_id: string | null;
  timestamp: number;
}

interface CronJob {
  id: string;
  name: string;
  schedule: string;
  enabled: boolean;
  last_run: string | null;
  next_run: string | null;
}

interface SystemMetrics {
  system: {
    cpu_percent: number;
    memory_percent: number;
    memory_used_mb: number;
    memory_total_mb: number;
    disk_percent: number;
    disk_used_gb: number;
    disk_total_gb: number;
  };
  app: {
    request_qps: number;
    latency_p99_ms: number;
    error_rate: number;
    total_requests: number;
    total_errors: number;
  };
  alerts: {
    total: number;
    active: number;
  };
}

// ── Organ definitions ───────────────────────────────────────────

const organs = [
  { key: "soul", label: "🧠 Soul", endpoint: "/api/health" },
  { key: "cortex", label: "🧩 Cortex", endpoint: "/api/cortex/health" },
  { key: "nerve", label: "⚡ Nerve", endpoint: "/api/nerve/health" },
  { key: "vein", label: "🩸 Vein", endpoint: "/api/vein/health" },
  { key: "soma", label: "🤖 Soma", endpoint: "/api/health" },
  { key: "sense", label: "👁 Sense", endpoint: "/api/sense/health" },
  { key: "will", label: "✨ Will", endpoint: "/api/will/health" },
  { key: "mate", label: "👤 Mate", endpoint: "/api/health" },
  { key: "immune", label: "🛡 Immune", endpoint: "/api/immune/health" },
  { key: "vital", label: "📊 Vital", endpoint: "/api/vital/health" },
  { key: "marrow", label: "🦴 Marrow", endpoint: "/api/marrow/health" },
  { key: "gland", label: "🧪 Gland", endpoint: "/api/gland/health" },
  { key: "gene", label: "🧬 Gene", endpoint: "/api/gene/health" },
  { key: "echo", label: "🔊 Echo", endpoint: "/api/echo/health" },
  { key: "mirror", label: "🪞 Mirror", endpoint: "/api/mirror/health" },
  { key: "link", label: "🔗 Link", endpoint: "/api/link/health" },
  { key: "hippo", label: "🧠 Hippo", endpoint: "/api/hippo/health" },
  { key: "reflex", label: "⚡ Reflex", endpoint: "/api/reflex/health" },
  { key: "heredity", label: "🔗 Heredity", endpoint: "/api/heredity/health" },
  { key: "nest", label: "🏠 Nest", endpoint: "/api/nest/health" },
  { key: "pulse", label: "💓 Pulse", endpoint: "/api/pulse/health" },
  { key: "limb", label: "💪 Limb", endpoint: "/api/limb/health" },
  { key: "voice", label: "🎤 Voice", endpoint: "/api/voice/health" },
  { key: "vision", label: "🎨 Vision", endpoint: "/api/vision/health" },
  { key: "mind", label: "💭 Mind", endpoint: "/api/mind/health" },
  { key: "trajectory", label: "📊 Trajectory", endpoint: "/api/trajectory/health" },
  { key: "mcp", label: "🔌 MCP", endpoint: "/api/mcp/health" },
];

// ── Component ───────────────────────────────────────────────────

export function DashboardClient() {
  const apiBase = getApiBaseUrl();
  const { t } = useTranslation();
  const setPageSidebar = useAppStore((s) => s.setPageSidebar);
  const router = useRouter();
  const pathname = usePathname();

  // Data state
  const [organHealth, setOrganHealth] = useState<Record<string, "ok" | "error" | "loading">>({});
  const [usageSummary, setUsageSummary] = useState<UsageSummary | null>(null);
  const [recentRecords, setRecentRecords] = useState<RecentRecord[]>([]);
  const [cronJobs, setCronJobs] = useState<CronJob[]>([]);
  const [sysMetrics, setSysMetrics] = useState<SystemMetrics | null>(null);

  // Zustand store reads
  const agentNodes = useAppStore((s) => s.agentNodes);
  const skills = useAppStore((s) => s.skills);
  const conversations = useAppStore((s) => s.conversations);
  const groups = useAppStore((s) => s.groups);
  const teams = useAppStore((s) => s.teams);
  const knowledgeItems = useAppStore((s) => s.knowledgeItems);
  const workspaces = useAppStore((s) => s.workspaces);

  // ── Data fetching ────────────────────────────────────────────

  const checkOrganHealth = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/health/all`, { signal: AbortSignal.timeout(8000) });
      if (res.ok) {
        const data = await res.json();
        setOrganHealth(data.organs || {});
      } else {
        const fallback: Record<string, "error"> = {};
        organs.forEach((o) => (fallback[o.key] = "error"));
        setOrganHealth(fallback);
      }
    } catch {
      const fallback: Record<string, "error"> = {};
      organs.forEach((o) => (fallback[o.key] = "error"));
      setOrganHealth(fallback);
    }
  }, [apiBase]);

  const fetchUsage = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/gland/usage`, { signal: AbortSignal.timeout(5000) });
      if (res.ok) setUsageSummary(await res.json());
    } catch (e) { console.error("Failed to fetch usage", e); }
  }, [apiBase]);

  const fetchRecentUsage = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/gland/usage/recent?limit=20`, { signal: AbortSignal.timeout(5000) });
      if (res.ok) {
        const data = await res.json();
        setRecentRecords(data.records || []);
      }
    } catch (e) { console.error("Failed to fetch recent usage", e); }
  }, [apiBase]);

  const fetchCronJobs = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/cron/jobs`, { signal: AbortSignal.timeout(5000) });
      if (res.ok) {
        const data = await res.json();
        const jobs = data.jobs || data || [];
        setCronJobs(Array.isArray(jobs) ? jobs : []);
      }
    } catch (e) { console.error("Failed to fetch cron jobs", e); }
  }, [apiBase]);

  const fetchSysMetrics = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/vital/stats`, { signal: AbortSignal.timeout(5000) });
      if (res.ok) setSysMetrics(await res.json());
    } catch {}
  }, [apiBase]);

  useEffect(() => {
    checkOrganHealth();
    fetchUsage();
    fetchRecentUsage();
    fetchCronJobs();
    fetchSysMetrics();
    const metricsInterval = setInterval(fetchSysMetrics, 10000);
    return () => clearInterval(metricsInterval);
  }, [checkOrganHealth, fetchUsage, fetchRecentUsage, fetchCronJobs, fetchSysMetrics]);

  // ── Organ list (sorted: errors first) ────────────────────────

  const sortedOrgans = useMemo(() => {
    return [...organs].sort((a, b) => {
      const sa = organHealth[a.key] || "loading";
      const sb = organHealth[b.key] || "loading";
      if (sa === "error" && sb !== "error") return -1;
      if (sa !== "error" && sb === "error") return 1;
      return 0;
    });
  }, [organHealth]);

  // ── Sidebar: organ list via LeftPanel ────────────────────────

  useEffect(() => {
    setPageSidebar(
      <LeftPanel
        items={sortedOrgans}
        placeholder={t("dashboard.searchOrgans", "Search organs...")}
        filter={(organ, query) => organ.label.toLowerCase().includes(query)}
        renderItem={(organ) => {
          const status = organHealth[organ.key] || "loading";
          // 导航语义（菜单点击劫持根修 2026-11-19）：器官项=菜单项，点击必须路由跳转
          // （router.push 到 /{key} 整页渲染），绝不走「选中→openface workspace 详情面板」
          // 旧语义（那正是用户暴怒报障的「点击菜单在 openface 的 workspace 里面打开」）。
          return (
            <button
              key={organ.key}
              onClick={() => router.push(`/${organ.key}`)}
              className={cn(
                "w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors hover:bg-muted/50",
                pathname === `/${organ.key}` && "bg-muted/70",
              )}
            >
              <span
                className={cn(
                  "w-2 h-2 rounded-full shrink-0",
                  status === "ok" && "bg-emerald-500",
                  status === "error" && "bg-red-500",
                  status === "loading" && "bg-muted-foreground/30 animate-pulse",
                )}
              />
              <span className="truncate">{organ.label}</span>
            </button>
          );
        }}
        header={
          <div className="px-3 pb-2 flex items-center gap-3 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              {Object.values(organHealth).filter((s) => s === "ok").length}
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-red-500" />
              {Object.values(organHealth).filter((s) => s === "error").length}
            </span>
            <button
              onClick={checkOrganHealth}
              className="ml-auto text-muted-foreground hover:text-foreground transition-colors"
            >
              ↻
            </button>
          </div>
        }
      />,
    );
    return () => setPageSidebar(null);
  }, [sortedOrgans, organHealth, setPageSidebar, checkOrganHealth, t, router, pathname]);

  // ── Computed values ──────────────────────────────────────────

  const totalTokens = usageSummary?.total_tokens ?? 0;
  const callCount = usageSummary?.call_count ?? 0;
  const estimatedCost = totalTokens * 0.00001;

  const modelBreakdown = Object.entries(usageSummary?.by_model || {})
    .map(([model, tokens]) => ({ model, tokens, cost: tokens * 0.00001 }))
    .sort((a, b) => b.tokens - a.tokens);

  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todayTs = todayStart.getTime() / 1000;
  const todayTokens = recentRecords
    .filter((r) => r.timestamp >= todayTs)
    .reduce((sum, r) => sum + r.total_tokens, 0);

  // ── ECharts options ──────────────────────────────────────────

  // 1. Token Usage Trend — 渐变面积折线图
  const tokenTrendOption = useMemo(() => {
    const hourly: Record<string, number> = {};
    recentRecords.forEach((r) => {
      const d = new Date(r.timestamp * 1000);
      const key = `${String(d.getHours()).padStart(2, "0")}:00`;
      hourly[key] = (hourly[key] || 0) + r.total_tokens;
    });
    const hours = Object.keys(hourly).sort();
    return {
      ...LINE_THEME,
      tooltip: { ...LINE_THEME.tooltip, trigger: "axis" as const },
      grid: { top: 20, right: 16, bottom: 28, left: 50, containLabel: true },
      xAxis: { ...LINE_THEME.xAxis, type: "category" as const, data: hours },
      yAxis: { ...LINE_THEME.yAxis, type: "value" as const },
      series: [{
        type: "line",
        data: hours.map((h) => hourly[h]),
        smooth: 0.4,
        symbol: 'circle',
        symbolSize: 6,
        showSymbol: hours.length <= 12,
        lineStyle: { width: 2.5, color: COLORS.primary },
        itemStyle: { color: COLORS.primary, borderColor: '#0f172a', borderWidth: 2 },
        areaStyle: {
          color: {
            type: 'linear' as const, x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(59, 130, 246, 0.3)' },
              { offset: 1, color: 'rgba(59, 130, 246, 0.02)' },
            ],
          },
        },
      }],
    };
  }, [recentRecords]);

  // 2. Model Breakdown — 渐变环形图
  const modelPieOption = useMemo(() => ({
    ...PIE_THEME,
    tooltip: {
      ...PIE_THEME.tooltip,
      trigger: "item" as const,
      formatter: "{b}: {c} tokens ({d}%)",
    },
    legend: {
      ...PIE_THEME.legend,
      show: modelBreakdown.length <= 6,
    },
    series: [{
      type: "pie",
      radius: ["40%", "70%"],
      center: ["40%", "50%"],
      avoidLabelOverlap: true,
      padAngle: 2,
      data: modelBreakdown.map((m, i) => ({
        name: m.model,
        value: m.tokens,
        itemStyle: {
          color: COLOR_PALETTE[i % COLOR_PALETTE.length],
          borderRadius: 6,
        },
      })),
      label: {
        show: modelBreakdown.length <= 4,
        fontSize: 10,
        color: '#94a3b8',
        formatter: '{b}\n{d}%',
      },
      emphasis: {
        scaleSize: 8,
        label: { fontSize: 12, fontWeight: 'bold' as const },
        itemStyle: { shadowBlur: 20, shadowColor: 'rgba(0,0,0,0.4)' },
      },
      labelLine: { lineStyle: { color: '#475569' } },
    }],
  }), [modelBreakdown]);

  // 3. Cost Trend — 渐变面积图（橙色系）
  const costTrendOption = useMemo(() => {
    const daily: Record<string, number> = {};
    recentRecords.forEach((r) => {
      const d = new Date(r.timestamp * 1000);
      const key = `${d.getMonth() + 1}/${d.getDate()}`;
      daily[key] = (daily[key] || 0) + r.total_tokens * 0.00001;
    });
    const days = Object.keys(daily).sort();
    return {
      ...LINE_THEME,
      tooltip: {
        ...LINE_THEME.tooltip,
        trigger: "axis" as const,
        formatter: (params: any) => `${params[0]?.axisValue}<br/>$${params[0]?.value?.toFixed(4)}`,
      },
      grid: { top: 20, right: 16, bottom: 28, left: 50, containLabel: true },
      xAxis: { ...LINE_THEME.xAxis, type: "category" as const, data: days },
      yAxis: {
        ...LINE_THEME.yAxis,
        type: "value" as const,
        axisLabel: { ...LINE_THEME.yAxis.axisLabel, formatter: "${value}" },
      },
      series: [{
        type: "line",
        data: days.map((d) => +daily[d].toFixed(4)),
        smooth: 0.4,
        symbol: 'circle',
        symbolSize: 6,
        showSymbol: days.length <= 12,
        lineStyle: { width: 2.5, color: COLORS.warning },
        itemStyle: { color: COLORS.warning, borderColor: '#0f172a', borderWidth: 2 },
        areaStyle: {
          color: {
            type: 'linear' as const, x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(245, 158, 11, 0.3)' },
              { offset: 1, color: 'rgba(245, 158, 11, 0.02)' },
            ],
          },
        },
      }],
    };
  }, [recentRecords]);

  // 4. System Health — 半圆仪表盘（参考远山风格）
  const sysGaugeOption = useMemo(() => {
    if (!sysMetrics) return {};
    return {
      ...BASE_CHART,
      series: [
        {
          type: "gauge",
          center: ["25%", "55%"],
          radius: "75%",
          startAngle: 200,
          endAngle: -20,
          min: 0,
          max: 100,
          pointer: {
            show: true,
            length: '55%',
            width: 4,
            itemStyle: { color: '#e2e8f0' },
          },
          axisLine: {
            lineStyle: {
              width: 10,
              color: [
                [0.6, '#10b981'],
                [0.8, '#f59e0b'],
                [1, '#ef4444'],
              ],
            },
          },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false },
          title: {
            fontSize: 11,
            color: '#94a3b8',
            offsetCenter: [0, '70%'],
          },
          detail: {
            fontSize: 18,
            fontWeight: 'bold' as const,
            color: '#e2e8f0',
            formatter: '{value}%',
            offsetCenter: [0, '35%'],
          },
          data: [{ value: +sysMetrics.system.cpu_percent.toFixed(1), name: "CPU" }],
        },
        {
          type: "gauge",
          center: ["75%", "55%"],
          radius: "75%",
          startAngle: 200,
          endAngle: -20,
          min: 0,
          max: 100,
          pointer: {
            show: true,
            length: '55%',
            width: 4,
            itemStyle: { color: '#e2e8f0' },
          },
          axisLine: {
            lineStyle: {
              width: 10,
              color: [
                [0.6, '#10b981'],
                [0.8, '#f59e0b'],
                [1, '#ef4444'],
              ],
            },
          },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false },
          title: {
            fontSize: 11,
            color: '#94a3b8',
            offsetCenter: [0, '70%'],
          },
          detail: {
            fontSize: 18,
            fontWeight: 'bold' as const,
            color: '#e2e8f0',
            formatter: '{value}%',
            offsetCenter: [0, '35%'],
          },
          data: [{ value: +sysMetrics.system.memory_percent.toFixed(1), name: "MEM" }],
        },
      ],
    };
  }, [sysMetrics]);

  // ── Render ───────────────────────────────────────────────────

  return (
    <PageLayout title="仪表盘" showSidebarToggle showWorkspaceToggle>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-6xl px-3 lg:px-6 py-4 lg:py-6 space-y-6">

          {/* ── System Metric Cards (4 in a row) ─────────────── */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 lg:gap-3">
            {/* CPU */}
            <div className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-center gap-2 mb-1">
                <Cpu className="w-3.5 h-3.5 text-blue-500" />
                <span className="text-[10px] text-muted-foreground">CPU</span>
              </div>
              <p className={cn("text-lg font-bold", (sysMetrics?.system.cpu_percent ?? 0) > 80 ? "text-red-500" : (sysMetrics?.system.cpu_percent ?? 0) > 50 ? "text-amber-500" : "text-emerald-500")}>
                {sysMetrics ? `${sysMetrics.system.cpu_percent.toFixed(1)}%` : "—"}
              </p>
            </div>
            {/* Memory */}
            <div className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-center gap-2 mb-1">
                <HardDrive className="w-3.5 h-3.5 text-violet-500" />
                <span className="text-[10px] text-muted-foreground">{t("dashboard.memory", "Memory")}</span>
              </div>
              <p className={cn("text-lg font-bold", (sysMetrics?.system.memory_percent ?? 0) > 85 ? "text-red-500" : (sysMetrics?.system.memory_percent ?? 0) > 60 ? "text-amber-500" : "text-emerald-500")}>
                {sysMetrics ? `${sysMetrics.system.memory_percent.toFixed(1)}%` : "—"}
              </p>
              {sysMetrics && (
                <p className="text-[10px] text-muted-foreground">{(sysMetrics.system.memory_used_mb / 1024).toFixed(1)}/{(sysMetrics.system.memory_total_mb / 1024).toFixed(1)} GB</p>
              )}
            </div>
            {/* Disk */}
            <div className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-center gap-2 mb-1">
                <HardDrive className="w-3.5 h-3.5 text-amber-500" />
                <span className="text-[10px] text-muted-foreground">{t("dashboard.disk", "Disk")}</span>
              </div>
              <p className={cn("text-lg font-bold", (sysMetrics?.system.disk_percent ?? 0) > 90 ? "text-red-500" : (sysMetrics?.system.disk_percent ?? 0) > 75 ? "text-amber-500" : "text-emerald-500")}>
                {sysMetrics ? `${sysMetrics.system.disk_percent.toFixed(1)}%` : "—"}
              </p>
              {sysMetrics && (
                <p className="text-[10px] text-muted-foreground">{sysMetrics.system.disk_used_gb.toFixed(0)}/{sysMetrics.system.disk_total_gb.toFixed(0)} GB</p>
              )}
            </div>
            {/* QPS */}
            <div className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-center gap-2 mb-1">
                <Zap className="w-3.5 h-3.5 text-blue-500" />
                <span className="text-[10px] text-muted-foreground">QPS</span>
              </div>
              <p className="text-lg font-bold">{sysMetrics ? sysMetrics.app.request_qps.toFixed(1) : "—"}</p>
              {sysMetrics && (
                <p className="text-[10px] text-muted-foreground">P99 {sysMetrics.app.latency_p99_ms.toFixed(0)}ms</p>
              )}
            </div>
          </div>

          {/* ── Charts row 1: Token trend + Model pie ─────────── */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-3">
            <div className="rounded-lg border border-border bg-card p-3">
              <h4 className="text-xs font-medium text-muted-foreground mb-2">{t("dashboard.tokenUsageTrend", "Token Usage Trend")}</h4>
              <div className="h-48">
                <EChart option={tokenTrendOption} style={{ height: "100%", width: "100%" }} opts={{ renderer: "svg" }} />
              </div>
            </div>
            <div className="rounded-lg border border-border bg-card p-3">
              <h4 className="text-xs font-medium text-muted-foreground mb-2">{t("dashboard.modelBreakdown", "Model Breakdown")}</h4>
              <div className="h-48">
                <EChart option={modelPieOption} style={{ height: "100%", width: "100%" }} opts={{ renderer: "svg" }} />
              </div>
            </div>
          </div>

          {/* ── Recent Usage Table ────────────────────────────── */}
          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2 border-b border-border">
              <h4 className="text-xs font-medium text-muted-foreground">{t("dashboard.recentActivity", "Recent Usage")}</h4>
              <button onClick={() => { fetchRecentUsage(); fetchCronJobs(); }} className="text-[10px] text-muted-foreground hover:text-foreground transition-colors">
                ↻ {t("common.refresh", "Refresh")}
              </button>
            </div>
            {recentRecords.length > 0 ? (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border bg-muted/30">
                        <th className="px-3 py-1.5 text-left font-medium text-muted-foreground text-[10px]">{t("dashboard.time", "Time")}</th>
                        <th className="px-3 py-1.5 text-left font-medium text-muted-foreground text-[10px]">{t("dashboard.model", "Model")}</th>
                        <th className="px-3 py-1.5 text-left font-medium text-muted-foreground text-[10px]">{t("dashboard.provider", "Provider")}</th>
                        <th className="px-3 py-1.5 text-right font-medium text-muted-foreground text-[10px]">{t("dashboard.tokens", "Tokens")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentRecords.slice(0, 10).map((r, i) => (
                        <tr key={i} className="border-b border-border last:border-0 hover:bg-muted/30 transition-colors">
                          <td className="px-3 py-1.5 text-[10px] text-muted-foreground">
                            {new Date(r.timestamp * 1000).toLocaleString('zh-CN', { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                          </td>
                          <td className="px-3 py-1.5 text-[10px] font-medium">{r.model}</td>
                          <td className="px-3 py-1.5 text-[10px] text-muted-foreground">{r.provider}</td>
                          <td className="px-3 py-1.5 text-[10px] text-right">
                            <span className="text-blue-500">{r.prompt_tokens.toLocaleString()}</span>
                            <span className="text-muted-foreground mx-1">→</span>
                            <span className="text-emerald-500">{r.completion_tokens.toLocaleString()}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="px-3 py-1.5 text-[10px] text-muted-foreground bg-muted/20 border-t border-border">
                  {recentRecords.length} {t("dashboard.records", "records")} · {callCount} {t("dashboard.totalCalls", "total calls")}
                </div>
              </>
            ) : (
              <div className="p-6 text-center">
                <TrendingUp size={24} className="mx-auto mb-2 text-muted-foreground/50" />
                <p className="text-xs text-muted-foreground">{t("dashboard.noActivity", "No recent activity")}</p>
              </div>
            )}
          </div>

          {/* ── Charts row 2: Cost trend + System health gauges ─ */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-3">
            <div className="rounded-lg border border-border bg-card p-3">
              <h4 className="text-xs font-medium text-muted-foreground mb-2">{t("dashboard.costTrend", "Cost Trend")}</h4>
              <div className="h-48">
                <EChart option={costTrendOption} style={{ height: "100%", width: "100%" }} opts={{ renderer: "svg" }} />
              </div>
            </div>
            <div className="rounded-lg border border-border bg-card p-3">
              <h4 className="text-xs font-medium text-muted-foreground mb-2">{t("dashboard.systemHealth", "System Health")}</h4>
              <div className="h-48">
                {sysMetrics && (
                  <EChart option={sysGaugeOption} style={{ height: "100%", width: "100%" }} opts={{ renderer: "svg" }} />
                )}
              </div>
            </div>
          </div>

        </div>
      </div>
    </PageLayout>
  );
}
