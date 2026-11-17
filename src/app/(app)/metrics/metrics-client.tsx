"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getApiBaseUrl } from "@/lib/api-client";
import { PageLayout } from '@/components/page-layout';

interface MetricLine {
  name: string;
  labels: Record<string, string>;
  value: number;
}

interface ParsedMetrics {
  info: { version: string; python: string; os: string } | null;
  uptime: number;
  memory: number;
  cpuTime: number;
  httpRequests: { path: string; method: string; count: number }[];
  httpErrors: { status: string; count: number }[];
  organHealth: { organ: string; healthy: boolean }[];
  system: {
    cpuCount: number;
    memTotal: number;
    memAvailable: number;
    diskTotal: number;
    diskFree: number;
  };
}

function parsePrometheus(text: string): ParsedMetrics {
  const lines = text.split("\n");
  const result: ParsedMetrics = {
    info: null,
    uptime: 0,
    memory: 0,
    cpuTime: 0,
    httpRequests: [],
    httpErrors: [],
    organHealth: [],
    system: {
      cpuCount: 0,
      memTotal: 0,
      memAvailable: 0,
      diskTotal: 0,
      diskFree: 0,
    },
  };

  const requestMap: Record<string, number> = {};

  for (const line of lines) {
    if (line.startsWith("#") || !line.trim()) continue;

    const match = line.match(/^(\w+)(?:\{([^}]*)\})?\s+(.+)$/);
    if (!match) continue;

    const [, name, labelsStr, valueStr] = match;
    const value = parseFloat(valueStr);
    const labels: Record<string, string> = {};

    if (labelsStr) {
      for (const part of labelsStr.split(",")) {
        const [k, v] = part.split("=");
        if (k && v) labels[k.trim()] = v.replace(/"/g, "").trim();
      }
    }

    switch (name) {
      case "opensoul_info":
        result.info = {
          version: labels.version || "",
          python: labels.python || "",
          os: labels.os || "",
        };
        break;
      case "opensoul_uptime_seconds":
        result.uptime = value;
        break;
      case "opensoul_process_resident_memory_bytes":
        result.memory = value;
        break;
      case "opensoul_process_cpu_seconds_total":
        result.cpuTime = value;
        break;
      case "opensoul_http_requests_total": {
        const key = `${labels.method} ${labels.path}`;
        requestMap[key] = (requestMap[key] || 0) + value;
        break;
      }
      case "opensoul_http_errors_total":
        result.httpErrors.push({ status: labels.status, count: value });
        break;
      case "opensoul_organ_health":
        result.organHealth.push({
          organ: labels.organ,
          healthy: value === 1,
        });
        break;
      case "opensoul_system_cpu_count":
        result.system.cpuCount = value;
        break;
      case "opensoul_system_memory_total_bytes":
        result.system.memTotal = value;
        break;
      case "opensoul_system_memory_available_bytes":
        result.system.memAvailable = value;
        break;
      case "opensoul_system_disk_total_bytes":
        result.system.diskTotal = value;
        break;
      case "opensoul_system_disk_free_bytes":
        result.system.diskFree = value;
        break;
    }
  }

  result.httpRequests = Object.entries(requestMap)
    .map(([key, count]) => {
      const [method, path] = key.split(" ");
      return { method, path, count };
    })
    .sort((a, b) => b.count - a.count)
    .slice(0, 20);

  result.organHealth.sort((a, b) => a.organ.localeCompare(b.organ));

  return result;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function MetricsClient() {
  const { t } = useTranslation();
  const [metrics, setMetrics] = useState<ParsedMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [rawMetrics, setRawMetrics] = useState("");

  const fetchMetrics = useCallback(async () => {
    try {
      const apiBase = getApiBaseUrl();
      const resp = await fetch(`${apiBase}/metrics`);
      const text = await resp.text();
      setRawMetrics(text);
      setMetrics(parsePrometheus(text));
      setLastUpdate(new Date());
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMetrics();
  }, [fetchMetrics]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = setInterval(fetchMetrics, 15000);
    return () => clearInterval(timer);
  }, [autoRefresh, fetchMetrics]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    );
  }

  if (!metrics) {
    return (
      <div className="p-3 lg:p-6 text-center text-muted-foreground">
        {t("metrics.loadFailed", "Failed to load metrics")}
      </div>
    );
  }

  const healthyOrgans = metrics.organHealth.filter((o) => o.healthy).length;
  const totalOrgans = metrics.organHealth.length;
  const memUsedPercent =
    metrics.system.memTotal > 0
      ? ((metrics.system.memTotal - metrics.system.memAvailable) /
          metrics.system.memTotal) *
        100
      : 0;
  const diskUsedPercent =
    metrics.system.diskTotal > 0
      ? ((metrics.system.diskTotal - metrics.system.diskFree) /
          metrics.system.diskTotal) *
        100
      : 0;

  return (
      <PageLayout title="指标">
        
    <div className="px-3 lg:px-6 py-4 lg:py-6 space-y-4 lg:space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl lg:text-2xl font-bold flex items-center gap-2">
            📊 {t("metrics.title", "Prometheus Metrics")}
          </h1>
          <p className="text-xs lg:text-sm text-muted-foreground mt-1">
            {t(
              "metrics.subtitle",
              "OpenTelemetry-compatible metrics for Grafana dashboards"
            )}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastUpdate && (
            <span className="text-xs text-muted-foreground">
              {t("metrics.updated", "Updated")}: {lastUpdate.toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}
            </span>
          )}
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`px-3 py-1.5 text-xs rounded-lg border ${
              autoRefresh
                ? "bg-green-500/10 border-green-500/30 text-green-600"
                : "bg-muted border-border text-muted-foreground"
            }`}
          >
            {autoRefresh
              ? t("metrics.autoRefresh", "Auto-refresh ON")
              : t("metrics.autoRefreshOff", "Auto-refresh OFF")}
          </button>
          <button
            onClick={fetchMetrics}
            className="px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-accent"
          >
            {t("metrics.refresh", "Refresh")}
          </button>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs text-muted-foreground mb-1">
            {t("metrics.uptime", "Uptime")}
          </div>
          <div className="text-xl lg:text-2xl font-bold">{formatUptime(metrics.uptime)}</div>
        </div>
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs text-muted-foreground mb-1">
            {t("metrics.organs", "Organs")}
          </div>
          <div className="text-xl lg:text-2xl font-bold">
            <span className={healthyOrgans === totalOrgans ? "text-green-500" : "text-yellow-500"}>
              {healthyOrgans}
            </span>
            <span className="text-muted-foreground">/{totalOrgans}</span>
          </div>
        </div>
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs text-muted-foreground mb-1">
            {t("metrics.memory", "Memory")}
          </div>
          <div className="text-xl lg:text-2xl font-bold">{formatBytes(metrics.memory)}</div>
          <div className="text-xs text-muted-foreground">
            RSS
          </div>
        </div>
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs text-muted-foreground mb-1">
            {t("metrics.cpuTime", "CPU Time")}
          </div>
          <div className="text-xl lg:text-2xl font-bold">{metrics.cpuTime.toFixed(1)}s</div>
        </div>
      </div>

      {/* System Resources */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2 lg:gap-4">
        {/* CPU */}
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs lg:text-sm font-medium mb-3">
            🖥️ {t("metrics.cpu", "CPU")}
          </div>
          <div className="text-2xl lg:text-3xl font-bold mb-1">{metrics.system.cpuCount}</div>
          <div className="text-xs text-muted-foreground">
            {t("metrics.cores", "cores")}
          </div>
        </div>

        {/* Memory */}
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs lg:text-sm font-medium mb-3">
            🧠 {t("metrics.systemMemory", "System Memory")}
          </div>
          <div className="w-full bg-muted rounded-full h-2.5 mb-2">
            <div
              className={`h-2.5 rounded-full ${
                memUsedPercent > 90
                  ? "bg-red-500"
                  : memUsedPercent > 70
                    ? "bg-yellow-500"
                    : "bg-green-500"
              }`}
              style={{ width: `${Math.min(memUsedPercent, 100)}%` }}
            />
          </div>
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>
              {formatBytes(metrics.system.memTotal - metrics.system.memAvailable)}{" "}
              {t("metrics.used", "used")}
            </span>
            <span>{formatBytes(metrics.system.memTotal)} total</span>
          </div>
        </div>

        {/* Disk */}
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs lg:text-sm font-medium mb-3">
            💾 {t("metrics.disk", "Disk")}
          </div>
          <div className="w-full bg-muted rounded-full h-2.5 mb-2">
            <div
              className={`h-2.5 rounded-full ${
                diskUsedPercent > 90
                  ? "bg-red-500"
                  : diskUsedPercent > 70
                    ? "bg-yellow-500"
                    : "bg-green-500"
              }`}
              style={{ width: `${Math.min(diskUsedPercent, 100)}%` }}
            />
          </div>
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>
              {formatBytes(metrics.system.diskTotal - metrics.system.diskFree)}{" "}
              {t("metrics.used", "used")}
            </span>
            <span>{formatBytes(metrics.system.diskTotal)} total</span>
          </div>
        </div>
      </div>

      {/* Organ Health Grid */}
      <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
        <div className="text-xs lg:text-sm font-medium mb-3">
          🫀 {t("metrics.organHealth", "Organ Health Status")}
        </div>
        <div className="grid grid-cols-4 md:grid-cols-8 gap-2">
          {metrics.organHealth.map((o) => (
            <div
              key={o.organ}
              className={`text-center p-2 rounded-lg text-xs ${
                o.healthy
                  ? "bg-green-500/10 text-green-600 border border-green-500/20"
                  : "bg-red-500/10 text-red-600 border border-red-500/20"
              }`}
            >
              <div className="text-lg">{o.healthy ? "✅" : "❌"}</div>
              <div className="font-medium mt-1">{o.organ}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Top HTTP Endpoints */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 lg:gap-4">
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs lg:text-sm font-medium mb-3">
            🔥 {t("metrics.topEndpoints", "Top HTTP Endpoints")}
          </div>
          <div className="space-y-1 max-h-80 overflow-y-auto">
            {metrics.httpRequests.map((r, i) => (
              <div
                key={i}
                className="flex items-center justify-between text-xs py-1 px-2 rounded hover:bg-accent"
              >
                <div className="flex items-center gap-2 flex-1 min-w-0">
                  <span
                    className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${
                      r.method === "GET"
                        ? "bg-blue-500/10 text-blue-600"
                        : r.method === "POST"
                          ? "bg-green-500/10 text-green-600"
                          : "bg-orange-500/10 text-orange-600"
                    }`}
                  >
                    {r.method}
                  </span>
                  <span className="font-mono truncate">{r.path}</span>
                </div>
                <span className="font-bold ml-2">{r.count}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Errors */}
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs lg:text-sm font-medium mb-3">
            ⚠️ {t("metrics.errors", "HTTP Errors")}
          </div>
          {metrics.httpErrors.length === 0 ? (
            <div className="text-center text-muted-foreground py-8">
              {t("metrics.noErrors", "No errors recorded ✨")}
            </div>
          ) : (
            <div className="space-y-1">
              {metrics.httpErrors.map((e, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between text-xs py-1 px-2 rounded hover:bg-accent"
                >
                  <span
                    className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${
                      e.status.startsWith("4")
                        ? "bg-yellow-500/10 text-yellow-600"
                        : "bg-red-500/10 text-red-600"
                    }`}
                  >
                    {e.status}
                  </span>
                  <span className="font-bold">{e.count}</span>
                </div>
              ))}
            </div>
          )}

          {/* Scrape URL Info */}
          <div className="mt-6 p-3 bg-muted/50 rounded-lg">
            <div className="text-xs font-medium mb-1">
              {t("metrics.scrapeConfig", "Prometheus Scrape Config")}
            </div>
            <code className="text-xs text-muted-foreground block">
              {`scrape_configs:
  - job_name: 'opensoul'
    scrape_interval: 15s
    static_configs:
      - targets: ['localhost:8090']
    metrics_path: /metrics`}
            </code>
          </div>
        </div>
      </div>

      {/* Instance Info */}
      {metrics.info && (
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs lg:text-sm font-medium mb-3">
            ℹ️ {t("metrics.instanceInfo", "Instance Info")}
          </div>
          <div className="flex gap-2 lg:gap-6 text-xs">
            <div>
              <span className="text-muted-foreground">版本：</span>{" "}
              <span className="font-mono">{metrics.info.version}</span>
            </div>
            <div>
              <span className="text-muted-foreground">Python:</span>{" "}
              <span className="font-mono">{metrics.info.python}</span>
            </div>
            <div>
              <span className="text-muted-foreground">操作系统：</span>{" "}
              <span className="font-mono">{metrics.info.os}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  
      </PageLayout>
    );
}
