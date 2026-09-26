'use client';

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { assembleECharts } from 'flint-chart';
import { getApiBaseUrl } from '@/lib/api-client';
import { cn } from '@/lib/utils';
import { PageLayout } from '@/components/page-layout';
import { DetailPanel } from '@/components/detail-panel';
import { LeftPanel } from '@/components/left-panel';
import { useAppStore } from '@/stores/app-store';
import {
  Activity, RefreshCw, CheckCircle, XCircle, Loader2,
  Server, Clock, Cpu, HardDrive, MemoryStick,
  Gauge, AlertTriangle, Info, Wifi, WifiOff,
  BarChart3, TrendingUp, Play, History, Target,
  Zap, Shield, Droplets, Volume2, Layers, Link2,
  MousePointer, Sparkles, Filter, ChevronDown,
  ChevronUp, Trash2,
  Bot,
} from 'lucide-react';

import { EChart } from '@/components/echart';
import { ArchitectureMonitor } from '@/components/architecture-monitor';

// ── Types ────────────────────────────────────────────────────

// Diagnostics types
interface OrganResult {
  key: string;
  label: string;
  category: string;
  status: 'ok' | 'error';
  status_code: number;
  response_time_ms: number;
}

interface SystemInfo {
  hostname: string;
  os: string;
  arch: string;
  python: string;
  cpu_count: number;
  cpu_percent: number;
  memory_total_gb: number;
  memory_used_gb: number;
  memory_percent: number;
  disk_total_gb: number;
  disk_used_gb: number;
  disk_percent: number;
  uptime_seconds: number;
}

interface DiagnosticsSummary {
  total: number;
  healthy: number;
  unhealthy: number;
  avg_response_ms: number;
  max_response_ms: number;
  overall: string;
}

interface CheckAllResult {
  summary: DiagnosticsSummary;
  system: SystemInfo;
  organs: OrganResult[];
}

// Metrics types
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

// Benchmark types
interface BenchmarkTarget {
  organ: string;
  label: string;
  endpoint: string;
}

interface LatencyStats {
  min_ms: number;
  max_ms: number;
  avg_ms: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
}

interface BenchmarkResultItem {
  organ: string;
  label: string;
  iterations: number;
  success: number;
  errors: number;
  latency: LatencyStats;
  throughput_rps: number;
  total_duration_ms: number;
  timestamp: number;
}

interface BenchmarkRun {
  run_id: string;
  organs_benchmarked: number;
  iterations: number;
  concurrency: number;
  results: BenchmarkResultItem[];
}

interface HistoryEntry {
  id: number;
  run_id: string;
  organ: string;
  label: string;
  iterations: number;
  success: number;
  errors: number;
  min_ms: number;
  max_ms: number;
  avg_ms: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  rps?: number;
  throughput_rps?: number;
  total_ms: number;
  total_duration_ms?: number;
  timestamp: number;
}

// Activity types
interface StreamEvent {
  id: string;
  organ: string;
  emoji: string;
  type: string;
  summary: string;
  detail?: Record<string, unknown>;
  timestamp?: number;
  collected_at?: number;
}

interface StreamSummary {
  total_events: number;
  by_organ: Record<string, number>;
  by_type: Record<string, number>;
  most_active_organ: string | null;
  collected_at: number;
}

// Agents & Jobs types — P0-8后台作业面板（SUMMARY.md前端差距表P1"后台作业面板（peek三指标）"）
// 数据源：acp-proxy :8092/api/agent/peek（goose peek三指标+claude-code noop自报）
//        opensoul :8090/api/will/jobs*（agno job_queue模式，P0-8）
//        opensoul :8090/api/heredity/health（P0-7进化管线）、/api/gland/health（模型链路trace）
interface PeekSession {
  session_id: string;
  status: string;
  durable_turns: number;
  idle_seconds: number;
  buffered: number;
  noop_streak: number;
  total_noops: number;
  max_noop_streak: number;
  steer_queued: number;
  steer_injected: number;
  tool_calls_total: number;
  aborted: number;
}

interface AgentPeek {
  sessions: PeekSession[];
  summary: {
    total_sessions: number;
    by_status: Record<string, number>;
    durable_turns_total: number;
    total_noops: number;
    buffered_total: number;
    steer_injected_total: number;
  };
}

// P0-2: 工具结果溢出统计（AIHawk SHOWN/SENT双预算 + goose spill落盘）
// 数据源：acp-proxy :8092/api/agent/tool-output/stats
interface ToolOutputStats {
  spill_dir: string;
  total_spills: number;
  total_size_bytes: number;
  char_threshold: number;
  line_threshold: number;
  total_calls: number;
  truncated_calls: number;
  sent_chars_total: number;
  shown_chars_total: number;
  by_tool: Record<string, { calls: number; truncated: number; sent_chars: number; shown_chars: number }>;
  recent_spills: { tool_name: string; original_size: number; shown_size: number; spill_id: string; ts: number }[];
}

// P1: 上下文逐项token归因（claude-code SDKContextUsage移植）——"上下文被什么吃掉了"
// 数据源：acp-proxy :8092/api/agent/token-attribution（soulmate agent写JSONL账本，API进程跨进程读）
interface TokenAttrConsumer {
  kind: string;
  name: string;
  source?: string;
  tokens?: number;
  tokens_total?: number;
  times_seen?: number;
  avg_tokens?: number;
}
interface TokenAttributionStats {
  total_records: number;
  recent: {
    session_id: string;
    model: string;
    ts: number;
    usage: {
      total_tokens: number;
      percentage: number;
      raw_max_tokens: number;
      over_limit: { tokens_over: number; kind: string } | null;
      top_consumers: TokenAttrConsumer[];
    };
  }[];
  summary: {
    total_records: number;
    over_limit_records: number;
    avg_total_tokens: number;
    max_total_tokens: number;
    latest: {
      total_tokens: number;
      percentage: number;
      over_limit: { tokens_over: number; kind: string } | null;
      raw_max_tokens: number;
      session_id: string;
      model: string;
    };
    top_consumers: TokenAttrConsumer[];
  };
  ledger_path?: string;
}

interface JobInfo {
  id: string;
  name: string;
  status: string;
  error?: string;
  created_at: number;
  started_at: number;
  finished_at: number;
  duration_s: number;
  retries: number;
}

interface JobsHealth {
  total: number;
  by_status: Record<string, number>;
  queue_size: number;
  workers: number;
  handlers: string[];
  running?: boolean;
}

interface EvoStats {
  proposals: Record<string, number>;
  pending: number;
  max_pending: number;
  pending_budget_remaining: number;
  ledger_events: Record<string, number>;
  triggers: number;
  breaker: { open: boolean; error_type: string; consecutive: number };
}

interface ChainTraceEntry {
  provider?: string;
  model?: string;
  pass?: number;
  error?: string;
  [k: string]: unknown;
}

// P1: Wakeup执行器观测面板（ScheduleWakeup执行侧闭环——"它刚才干了什么"tail数据面）
// 数据源：acp-proxy :8092/:8095 /api/agent/wakeup-executor（双实例CAS，snapshot含recent活动流）
//        opensoul :8090/api/will/wakeup/overview（全键唤醒循环聚合）+ /wakeup/folded（noop折叠视图）
interface WakeupActivity {
  ts: number;
  kind: string; // claimed / claim_lost / claim_error / fire_ok / fire_failed / ack_error / poll_error
  event_id: number | null;
  detail: string;
}

interface WakeupExecutorSnapshot {
  running: boolean;
  opensoul_url: string;
  poll_interval_s: number;
  polls: number;
  due_seen: number;
  claimed: number;
  claim_lost: number;
  claim_errors: number;
  fire_ok: number;
  fire_failed: number;
  ack_errors: number;
  last_poll_at: number | null;
  last_fire_at: number | null;
  last_error: string;
  last_error_at: number | null;
  recent: WakeupActivity[];
}

interface WakeupLoopStatus {
  agent_id: string;
  session_id: string;
  active: boolean;
  current_streak: number;
  next_wakeup_at: number | null;
  countdown_s: number | null;
  total_events: number;
  noop_events: number;
  fire: { due_now: boolean; fired: number; exhausted: number };
  last_event?: {
    reason?: string;
    noop?: boolean;
    streak?: number;
    ts?: number;
    stopped?: boolean;
    fired?: boolean;
    fire_attempts?: number;
    last_fire_error?: string;
  } | null;
}

interface WakeupOverview {
  total_keys: number;
  active_count: number;
  due_count: number;
  loops: WakeupLoopStatus[];
}

interface WakeupFoldedItem {
  kind: string; // wakeup / noop_streak
  folded: boolean;
  count?: number;
  streak_max?: number;
  first_ts?: number;
  last_ts?: number;
  first_reason?: string;
  last_reason?: string;
  reasons?: string[];
  event_ids?: number[];
  reason?: string;
  noop?: boolean;
  streak?: number;
  ts?: number;
}

// ── Constants ────────────────────────────────────────────────

type MonitoringTab = 'overview' | 'system' | 'benchmark' | 'activity' | 'agents' | 'architecture';
type BenchmarkSubTab = 'run' | 'comparison' | 'history';

const ORGAN_ICONS: Record<string, React.ElementType> = {
  vein: Droplets, gland: Zap, immune: Shield, trajectory: Activity,
  echo: Volume2, mirror: Layers, link: Link2, limb: MousePointer, will: Sparkles,
};

const TYPE_COLORS: Record<string, string> = {
  stats: 'text-blue-500 bg-blue-500/10',
  llm_call: 'text-purple-500 bg-purple-500/10',
  security: 'text-red-500 bg-red-500/10',
  agent_event: 'text-emerald-500 bg-emerald-500/10',
  message: 'text-amber-500 bg-amber-500/10',
  sandbox: 'text-indigo-500 bg-indigo-500/10',
  webhook: 'text-orange-500 bg-orange-500/10',
  rpa_task: 'text-pink-500 bg-pink-500/10',
  cron_job: 'text-cyan-500 bg-cyan-500/10',
};

// ── Helpers ──────────────────────────────────────────────────

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function formatMs(ms: number) {
  return ms < 1 ? `${(ms * 1000).toFixed(0)}μs` : ms < 1000 ? `${ms.toFixed(1)}ms` : `${(ms / 1000).toFixed(2)}s`;
}

function formatTimestamp(ts?: number, t?: (key: string, opts?: Record<string, unknown>) => string): string {
  if (!ts) return '';
  const date = new Date(ts * 1000);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  if (diff < 60000) return t ? t('activity.justNow') : 'just now';
  if (diff < 3600000) return t ? t('activity.minutesAgo', { count: Math.floor(diff / 60000) }) : `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return t ? t('activity.hoursAgo', { count: Math.floor(diff / 3600000) }) : `${Math.floor(diff / 3600000)}h ago`;
  return date.toLocaleDateString(undefined, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function parsePrometheus(text: string): ParsedMetrics {
  const lines = text.split('\n');
  const result: ParsedMetrics = {
    info: null, uptime: 0, memory: 0, cpuTime: 0,
    httpRequests: [], httpErrors: [], organHealth: [],
    system: { cpuCount: 0, memTotal: 0, memAvailable: 0, diskTotal: 0, diskFree: 0 },
  };
  const requestMap: Record<string, number> = {};
  for (const line of lines) {
    if (line.startsWith('#') || !line.trim()) continue;
    const match = line.match(/^(\w+)(?:\{([^}]*)\})?\s+(.+)$/);
    if (!match) continue;
    const [, name, labelsStr, valueStr] = match;
    const value = parseFloat(valueStr);
    const labels: Record<string, string> = {};
    if (labelsStr) {
      for (const part of labelsStr.split(',')) {
        const [k, v] = part.split('=');
        if (k && v) labels[k.trim()] = v.replace(/"/g, '').trim();
      }
    }
    switch (name) {
      case 'opensoul_info':
        result.info = { version: labels.version || '', python: labels.python || '', os: labels.os || '' };
        break;
      case 'opensoul_uptime_seconds': result.uptime = value; break;
      case 'opensoul_process_resident_memory_bytes': result.memory = value; break;
      case 'opensoul_process_cpu_seconds_total': result.cpuTime = value; break;
      case 'opensoul_http_requests_total': {
        const key = `${labels.method} ${labels.path}`;
        requestMap[key] = (requestMap[key] || 0) + value;
        break;
      }
      case 'opensoul_http_errors_total':
        result.httpErrors.push({ status: labels.status, count: value });
        break;
      case 'opensoul_organ_health':
        result.organHealth.push({ organ: labels.organ, healthy: value === 1 });
        break;
      case 'opensoul_system_cpu_count': result.system.cpuCount = value; break;
      case 'opensoul_system_memory_total_bytes': result.system.memTotal = value; break;
      case 'opensoul_system_memory_available_bytes': result.system.memAvailable = value; break;
      case 'opensoul_system_disk_total_bytes': result.system.diskTotal = value; break;
      case 'opensoul_system_disk_free_bytes': result.system.diskFree = value; break;
    }
  }
  result.httpRequests = Object.entries(requestMap)
    .map(([key, count]) => { const [method, path] = key.split(' '); return { method, path, count }; })
    .sort((a, b) => b.count - a.count).slice(0, 20);
  result.organHealth.sort((a, b) => a.organ.localeCompare(b.organ));
  return result;
}

function getLatencyColor(ms: number) {
  if (ms < 5) return 'text-green-500';
  if (ms < 20) return 'text-emerald-400';
  if (ms < 50) return 'text-yellow-400';
  if (ms < 100) return 'text-orange-400';
  return 'text-red-400';
}

function getLatencyBg(ms: number) {
  if (ms < 5) return 'bg-green-500';
  if (ms < 20) return 'bg-emerald-400';
  if (ms < 50) return 'bg-yellow-400';
  if (ms < 100) return 'bg-orange-400';
  return 'bg-red-400';
}

// P0-8面板状态色（沿用本页既有emerald/amber/red语义，不新造配色）
function jobStatusColor(status: string): string {
  switch (status) {
    case 'completed': return 'text-emerald-500 bg-emerald-500/10';
    case 'running': return 'text-amber-500 bg-amber-500/10';
    case 'failed':
    case 'timeout': return 'text-red-500 bg-red-500/10';
    case 'cancelled': return 'text-muted-foreground bg-muted';
    default: return 'text-blue-500 bg-blue-500/10'; // pending
  }
}

function sessionStatusColor(status: string): string {
  if (status === 'running') return 'text-emerald-500 bg-emerald-500/10';
  if (status === 'error' || status === 'aborted') return 'text-red-500 bg-red-500/10';
  return 'text-muted-foreground bg-muted'; // idle
}

// Wakeup活动流动作着色（claimed/fire_ok=正常推进，claim_lost=CAS败北中性，
// 其余claim_error/fire_failed/ack_error/poll_error=失败必须可见红）
function wakeupActivityColor(kind: string): string {
  if (kind === 'fire_ok' || kind === 'claimed') return 'text-emerald-500 bg-emerald-500/10';
  if (kind === 'claim_lost') return 'text-muted-foreground bg-muted';
  return 'text-red-500 bg-red-500/10';
}

// ── Shared UI Components ────────────────────────────────────

function StatusBadge({ status }: { status: 'ok' | 'error' }) {
  return status === 'ok' ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-500">
      <CheckCircle size={10} /> OK
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-500">
      <XCircle size={10} /> ERROR
    </span>
  );
}

function GaugeBar({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  const barColor = pct < 60 ? 'bg-emerald-500' : pct < 85 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-mono font-medium">{pct.toFixed(1)}%</span>
      </div>
      <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
        <div className={cn('h-full rounded-full transition-all duration-500', barColor)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function ResponseTimeBar({ ms }: { ms: number }) {
  const width = Math.min(ms / 10, 100);
  const color = ms < 50 ? 'bg-emerald-500' : ms < 200 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 rounded-full bg-muted overflow-hidden">
        <div className={cn('h-full rounded-full transition-all', color)} style={{ width: `${width}%` }} />
      </div>
      <span className="text-[10px] font-mono text-muted-foreground w-14 text-right">{ms.toFixed(1)}ms</span>
    </div>
  );
}

// ── ECharts Components ──────────────────────────────────────

function ResponseTimeChart({ organs }: { organs: OrganResult[] }) {
  const option = useMemo(() => {
    const sorted = [...organs].sort((a, b) => a.response_time_ms - b.response_time_ms).slice(0, 20);
    const values = sorted.map(o => ({ organ: o.label, time: o.response_time_ms }));
    return assembleECharts({
      data: { values },
      semantic_types: { organ: 'Nominal', time: 'Quantity' },
      chart_spec: {
        chartType: 'Bar Chart',
        encodings: { x: { field: 'organ' }, y: { field: 'time' } },
        canvasSize: { width: 500, height: 200 },
      },
    });
  }, [organs]);

  if (organs.length === 0) return null;
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp size={14} className="text-orange-500" />
        <h4 className="text-xs font-medium">Response Time Distribution</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

function OrganStatusPieChart({ organs }: { organs: OrganResult[] }) {
  const option = useMemo(() => {
    const ok = organs.filter(o => o.status === 'ok').length;
    const err = organs.filter(o => o.status === 'error').length;
    const values = [
      { status: 'Healthy', count: ok },
      { status: 'Error', count: err },
    ];
    return assembleECharts({
      data: { values },
      semantic_types: { status: 'Nominal', count: 'Quantity' },
      chart_spec: {
        chartType: 'Pie Chart',
        encodings: { x: { field: 'status' }, y: { field: 'count' } },
        chartProperties: { innerRadius: 35 },
        canvasSize: { width: 300, height: 200 },
      },
    });
  }, [organs]);

  if (organs.length === 0) return null;
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <BarChart3 size={14} className="text-emerald-500" />
        <h4 className="text-xs font-medium">Organ Status</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

function HttpRequestRateChart({ httpRequests }: { httpRequests: { path: string; method: string; count: number }[] }) {
  const option = useMemo(() => {
    const values = httpRequests.slice(0, 10).map(r => ({
      endpoint: `${r.method} ${r.path}`,
      count: r.count,
    }));
    return assembleECharts({
      data: { values },
      semantic_types: { endpoint: 'Nominal', count: 'Quantity' },
      chart_spec: {
        chartType: 'Bar Chart',
        encodings: { x: { field: 'endpoint' }, y: { field: 'count' } },
        canvasSize: { width: 500, height: 200 },
      },
    });
  }, [httpRequests]);

  if (httpRequests.length === 0) return null;
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp size={14} className="text-blue-500" />
        <h4 className="text-xs font-medium">HTTP Request Rate</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

function ErrorRateChart({ httpErrors }: { httpErrors: { status: string; count: number }[] }) {
  const option = useMemo(() => {
    const values = httpErrors.map(e => ({ status: e.status, count: e.count }));
    return assembleECharts({
      data: { values },
      semantic_types: { status: 'Nominal', count: 'Quantity' },
      chart_spec: {
        chartType: 'Pie Chart',
        encodings: { x: { field: 'status' }, y: { field: 'count' } },
        chartProperties: { innerRadius: 35 },
        canvasSize: { width: 300, height: 200 },
      },
    });
  }, [httpErrors]);

  if (httpErrors.length === 0) return null;
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <BarChart3 size={14} className="text-red-500" />
        <h4 className="text-xs font-medium">Error Rate</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

function BenchmarkLatencyChart({ results }: { results: BenchmarkResultItem[] }) {
  const option = useMemo(() => {
    const sorted = [...results].sort((a, b) => a.latency.avg_ms - b.latency.avg_ms);
    const values = sorted.map(r => ({ organ: r.label, avg: r.latency.avg_ms, p95: r.latency.p95_ms, p99: r.latency.p99_ms }));
    return assembleECharts({
      data: { values },
      semantic_types: { organ: 'Nominal', avg: 'Quantity', p95: 'Quantity', p99: 'Quantity' },
      chart_spec: {
        chartType: 'Bar Chart',
        encodings: { x: { field: 'organ' }, y: { field: 'avg' } },
        canvasSize: { width: 600, height: 250 },
      },
    });
  }, [results]);

  if (results.length === 0) return null;
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <BarChart3 size={14} className="text-primary" />
        <h4 className="text-xs font-medium">Latency Comparison</h4>
      </div>
      <EChart option={option as any} style={{ height: 250 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

// ── Main Component ──────────────────────────────────────────

export function MonitoringClient() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const setPageSidebar = useAppStore((s) => s.setPageSidebar);
  const setPageWorkspace = useAppStore((s) => s.setPageWorkspace);

  // ── Tab state ──────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState<MonitoringTab>('overview');

  // ── Diagnostics (Overview) state ───────────────────────────
  const [diagData, setDiagData] = useState<CheckAllResult | null>(null);
  const [diagLoading, setDiagLoading] = useState(false);
  const [diagError, setDiagError] = useState('');

  // ── Metrics (System) state ─────────────────────────────────
  const [metrics, setMetrics] = useState<ParsedMetrics | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(true);
  const [autoRefreshMetrics, setAutoRefreshMetrics] = useState(true);
  const [lastMetricsUpdate, setLastMetricsUpdate] = useState<Date | null>(null);

  // ── Benchmark state ────────────────────────────────────────
  const [benchTab, setBenchTab] = useState<BenchmarkSubTab>('run');
  const [targets, setTargets] = useState<BenchmarkTarget[]>([]);
  const [selectedOrgans, setSelectedOrgans] = useState<Set<string>>(new Set());
  const [iterations, setIterations] = useState(20);
  const [concurrency, setConcurrency] = useState(5);
  const [benchRunning, setBenchRunning] = useState(false);
  const [currentRun, setCurrentRun] = useState<BenchmarkRun | null>(null);
  const [comparison, setComparison] = useState<HistoryEntry[]>([]);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [historyFilter, setHistoryFilter] = useState('');
  const [expandedOrgan, setExpandedOrgan] = useState<string | null>(null);

  // ── Activity state ─────────────────────────────────────────
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [summary, setSummary] = useState<StreamSummary | null>(null);
  const [activityLoading, setActivityLoading] = useState(false);
  const [selectedOrgan, setSelectedOrgan] = useState<string | null>(null);
  const [autoRefreshActivity, setAutoRefreshActivity] = useState(true);
  const [showFilters, setShowFilters] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState<StreamEvent | null>(null);
  const sseRef = useRef<EventSource | null>(null);
  const [sseConnected, setSseConnected] = useState(false);

  // ── Agents & Jobs state (P0-8面板) ────────────────────────
  const [agentPeek, setAgentPeek] = useState<AgentPeek | null>(null);
  const [peekErr, setPeekErr] = useState('');
  const [toolSpill, setToolSpill] = useState<ToolOutputStats | null>(null);
  const [tokenAttr, setTokenAttr] = useState<TokenAttributionStats | null>(null);
  const [jobsHealth, setJobsHealth] = useState<JobsHealth | null>(null);
  const [jobsList, setJobsList] = useState<JobInfo[]>([]);
  const [evoStats, setEvoStats] = useState<EvoStats | null>(null);
  const [chainTrace, setChainTrace] = useState<ChainTraceEntry[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(false);
  const [agentsUpdated, setAgentsUpdated] = useState<Date | null>(null);

  // ── Wakeup observability state (P1 wakeup执行器观测面板) ─────
  const [wakeupExecA, setWakeupExecA] = useState<WakeupExecutorSnapshot | null>(null);
  const [wakeupExecB, setWakeupExecB] = useState<WakeupExecutorSnapshot | null>(null);
  const [wakeupOverview, setWakeupOverview] = useState<WakeupOverview | null>(null);
  const [wakeupFolded, setWakeupFolded] = useState<Record<string, WakeupFoldedItem[]>>({});
  const [expandedWakeup, setExpandedWakeup] = useState<string | null>(null);

  // ── Detail selection state ─────────────────────────────────
  const [selectedOrganDetail, setSelectedOrganDetail] = useState<OrganResult | null>(null);

  // ── Diagnostics data fetching ──────────────────────────────

  const runDiagnostics = useCallback(async () => {
    setDiagLoading(true);
    setDiagError('');
    try {
      const res = await fetch(`${apiBase}/api/diagnostics/check-all`, {
        signal: AbortSignal.timeout(15000),
      });
      if (res.ok) {
        setDiagData(await res.json());
      } else {
        setDiagError(`HTTP ${res.status}`);
      }
    } catch (e: unknown) {
      setDiagError(e instanceof Error ? e.message : 'Check failed');
    } finally {
      setDiagLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    runDiagnostics();
    const interval = setInterval(runDiagnostics, 60000);
    return () => clearInterval(interval);
  }, [runDiagnostics]);

  // ── Metrics data fetching ──────────────────────────────────

  const fetchMetrics = useCallback(async () => {
    try {
      const resp = await fetch(`${apiBase}/metrics`);
      const text = await resp.text();
      setMetrics(parsePrometheus(text));
      setLastMetricsUpdate(new Date());
    } catch {} finally {
      setMetricsLoading(false);
    }
  }, [apiBase]);

  useEffect(() => { fetchMetrics(); }, [fetchMetrics]);

  useEffect(() => {
    if (!autoRefreshMetrics) return;
    const timer = setInterval(fetchMetrics, 15000);
    return () => clearInterval(timer);
  }, [autoRefreshMetrics, fetchMetrics]);

  // ── Benchmark data fetching ────────────────────────────────

  const fetchTargets = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/benchmark/targets`);
      if (res.ok) {
        const data = await res.json();
        setTargets(data.targets || []);
        setSelectedOrgans(new Set((data.targets || []).map((t: BenchmarkTarget) => t.organ)));
      }
    } catch {}
  }, [apiBase]);

  const fetchComparison = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/benchmark/comparison`);
      if (res.ok) {
        const data = await res.json();
        setComparison(data.comparison || []);
      }
    } catch {}
  }, [apiBase]);

  const fetchHistory = useCallback(async () => {
    try {
      const params = new URLSearchParams({ limit: '100' });
      if (historyFilter) params.set('organ', historyFilter);
      const res = await fetch(`${apiBase}/api/benchmark/history?${params}`);
      if (res.ok) {
        const data = await res.json();
        setHistory(data.history || []);
      }
    } catch {}
  }, [apiBase, historyFilter]);

  useEffect(() => {
    fetchTargets();
    fetchComparison();
  }, [fetchTargets, fetchComparison]);

  useEffect(() => {
    if (benchTab === 'history') fetchHistory();
    if (benchTab === 'comparison') fetchComparison();
  }, [benchTab, fetchHistory, fetchComparison]);

  const runBenchmark = async () => {
    if (selectedOrgans.size === 0) return;
    setBenchRunning(true);
    setCurrentRun(null);
    try {
      const res = await fetch(`${apiBase}/api/benchmark/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ organs: Array.from(selectedOrgans), iterations, concurrency }),
      });
      if (res.ok) {
        const data = await res.json();
        setCurrentRun(data);
        fetchComparison();
      }
    } catch {}
    setBenchRunning(false);
  };

  const quickBench = async (organ: string) => {
    setBenchRunning(true);
    try {
      const res = await fetch(`${apiBase}/api/benchmark/quick/${organ}?iterations=10`, { method: 'POST' });
      if (res.ok) {
        setCurrentRun(await res.json());
      }
    } catch {}
    setBenchRunning(false);
  };

  const deleteHistory = async () => {
    try {
      await fetch(`${apiBase}/api/benchmark/history`, { method: 'DELETE' });
      setHistory([]);
      setComparison([]);
    } catch {}
  };

  const toggleOrgan = (organ: string) => {
    const next = new Set(selectedOrgans);
    if (next.has(organ)) next.delete(organ);
    else next.add(organ);
    setSelectedOrgans(next);
  };

  // ── Activity data fetching ─────────────────────────────────

  const fetchEvents = useCallback(async () => {
    setActivityLoading(true);
    try {
      const params = new URLSearchParams({ limit: '50' });
      if (selectedOrgan) params.set('organ', selectedOrgan);
      const res = await fetch(`${apiBase}/api/events/stream?${params}`, { signal: AbortSignal.timeout(10000) });
      if (res.ok) {
        const data = await res.json();
        setEvents(data.events || []);
      }
    } catch {}
    setActivityLoading(false);
  }, [apiBase, selectedOrgan]);

  const fetchSummary = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/events/stream/summary`, { signal: AbortSignal.timeout(10000) });
      if (res.ok) setSummary(await res.json());
    } catch {}
  }, [apiBase]);

  useEffect(() => {
    fetchEvents();
    fetchSummary();
  }, [fetchEvents, fetchSummary]);

  // SSE real-time connection
  useEffect(() => {
    if (!autoRefreshActivity || activeTab !== 'activity') {
      if (sseRef.current) { sseRef.current.close(); sseRef.current = null; setSseConnected(false); }
      return;
    }
    const sseUrl = selectedOrgan
      ? `${apiBase}/api/events/sse?organ=${selectedOrgan}`
      : `${apiBase}/api/events/sse`;
    const es = new EventSource(sseUrl);
    sseRef.current = es;
    es.addEventListener('connected', () => setSseConnected(true));
    es.addEventListener('message', (e) => {
      try {
        const newEvent: StreamEvent = JSON.parse(e.data);
        setEvents((prev) => {
          if (prev.some((ev) => ev.id === newEvent.id)) return prev;
          return [newEvent, ...prev].slice(0, 100);
        });
      } catch {}
    });
    es.onerror = () => setSseConnected(false);
    const summaryTimer = setInterval(fetchSummary, 30000);
    return () => { es.close(); sseRef.current = null; setSseConnected(false); clearInterval(summaryTimer); };
  }, [autoRefreshActivity, activeTab, apiBase, selectedOrgan, fetchSummary]);

  const handleActivityRefresh = async () => {
    setActivityLoading(true);
    try {
      await fetch(`${apiBase}/api/events/stream/refresh`, { method: 'POST' });
      await fetchEvents();
      await fetchSummary();
    } catch {}
    setActivityLoading(false);
  };

  // ── Agents & Jobs data fetching (P0-8面板，10s轮询) ────────

  const fetchAgentsData = useCallback(async () => {
    setAgentsLoading(true);
    const acpBase = typeof window !== 'undefined'
      ? `http://${window.location.hostname}:8092`
      : 'http://127.0.0.1:8092';
    const acpBaseB = acpBase.replace(':8092', ':8095');
    // 各端点独立容错：单个服务挂掉不拖垮整个面板（fail-safe渲染"unavailable"）
    const [peekRes, jhRes, jlRes, evoRes, glandRes, spillRes, attrRes, wuARes, wuBRes, wuOvRes] = await Promise.allSettled([
      fetch(`${acpBase}/api/agent/peek`, { signal: AbortSignal.timeout(8000) }),
      fetch(`${apiBase}/api/will/jobs/health`, { signal: AbortSignal.timeout(8000) }),
      fetch(`${apiBase}/api/will/jobs?limit=20`, { signal: AbortSignal.timeout(8000) }),
      fetch(`${apiBase}/api/heredity/health`, { signal: AbortSignal.timeout(8000) }),
      fetch(`${apiBase}/api/gland/health`, { signal: AbortSignal.timeout(8000) }),
      fetch(`${acpBase}/api/agent/tool-output/stats`, { signal: AbortSignal.timeout(8000) }),
      fetch(`${acpBase}/api/agent/token-attribution?limit=5`, { signal: AbortSignal.timeout(8000) }),
      fetch(`${acpBase}/api/agent/wakeup-executor`, { signal: AbortSignal.timeout(8000) }),
      fetch(`${acpBaseB}/api/agent/wakeup-executor`, { signal: AbortSignal.timeout(8000) }),
      fetch(`${apiBase}/api/will/wakeup/overview`, { signal: AbortSignal.timeout(8000) }),
    ]);
    if (peekRes.status === 'fulfilled' && peekRes.value.ok) {
      try { setAgentPeek(await peekRes.value.json()); setPeekErr(''); } catch {}
    } else {
      setPeekErr(`ACP proxy (${acpBase}) unreachable`);
    }
    if (jhRes.status === 'fulfilled' && jhRes.value.ok) {
      try { setJobsHealth(await jhRes.value.json()); } catch {}
    }
    if (jlRes.status === 'fulfilled' && jlRes.value.ok) {
      try { const d = await jlRes.value.json(); setJobsList(d.jobs || []); } catch {}
    }
    if (evoRes.status === 'fulfilled' && evoRes.value.ok) {
      try { const d = await evoRes.value.json(); setEvoStats(d.evolution || null); } catch {}
    }
    if (glandRes.status === 'fulfilled' && glandRes.value.ok) {
      try { const d = await glandRes.value.json(); setChainTrace(d.last_chain_trace || []); } catch {}
    }
    if (spillRes.status === 'fulfilled' && spillRes.value.ok) {
      try { setToolSpill(await spillRes.value.json()); } catch {}
    }
    if (attrRes.status === 'fulfilled' && attrRes.value.ok) {
      try { setTokenAttr(await attrRes.value.json()); } catch {}
    }
    if (wuARes.status === 'fulfilled' && wuARes.value.ok) {
      try { setWakeupExecA(await wuARes.value.json()); } catch {}
    }
    if (wuBRes.status === 'fulfilled' && wuBRes.value.ok) {
      try { setWakeupExecB(await wuBRes.value.json()); } catch {}
    }
    if (wuOvRes.status === 'fulfilled' && wuOvRes.value.ok) {
      try { setWakeupOverview(await wuOvRes.value.json()); } catch {}
    }
    setAgentsUpdated(new Date());
    setAgentsLoading(false);
  }, [apiBase]);

  useEffect(() => {
    if (activeTab !== 'agents') return;
    fetchAgentsData();
    const timer = setInterval(fetchAgentsData, 10000);
    return () => clearInterval(timer);
  }, [activeTab, fetchAgentsData]);

  // Wakeup折叠视图（claude-code noop streak折叠——"连续空转"一眼可见）：
  // 展开某个唤醒循环行时按需拉 /wakeup/folded 渲染折叠块
  const fetchWakeupFolded = useCallback(async (agentId: string, sessionId: string) => {
    const key = `${agentId}/${sessionId}`;
    try {
      const params = new URLSearchParams({ agent_id: agentId, session_id: sessionId, limit: '20' });
      const res = await fetch(`${apiBase}/api/will/wakeup/folded?${params}`, { signal: AbortSignal.timeout(8000) });
      if (res.ok) {
        const d = await res.json();
        setWakeupFolded(prev => ({ ...prev, [key]: d.items || [] }));
      }
    } catch {}
  }, [apiBase]);

  const toggleWakeupLoop = (agentId: string, sessionId: string) => {
    const key = `${agentId}/${sessionId}`;
    if (expandedWakeup === key) {
      setExpandedWakeup(null);
      return;
    }
    setExpandedWakeup(key);
    if (!wakeupFolded[key]) fetchWakeupFolded(agentId, sessionId);
  };

  // 双实例活动流合并（tail数据面：按时间倒序取最新8条，标注来源实例）
  const wakeupActivityFeed = useMemo(() => {
    const all = [
      ...(wakeupExecA?.recent || []).map(a => ({ ...a, src: 'a' })),
      ...(wakeupExecB?.recent || []).map(a => ({ ...a, src: 'b' })),
    ];
    return all.sort((x, y) => y.ts - x.ts).slice(0, 8);
  }, [wakeupExecA, wakeupExecB]);

  // ── Computed values ────────────────────────────────────────

  const sortedOrgans = diagData?.organs
    ? [...diagData.organs].sort((a, b) => a.response_time_ms - b.response_time_ms)
    : [];

  const grouped = sortedOrgans.reduce<Record<string, OrganResult[]>>((acc, o) => {
    (acc[o.category] = acc[o.category] || []).push(o);
    return acc;
  }, {});

  const categoryLabels: Record<string, string> = {
    core: t('diagnostics.coreLayer') || 'Core',
    platform: t('diagnostics.platformLayer') || 'Platform',
    advanced: t('diagnostics.advancedLayer') || 'Advanced',
    system: t('diagnostics.systemLayer') || 'System',
    service: t('diagnostics.serviceLayer') || 'Service',
    organ: t('diagnostics.organLayer') || 'Organ',
  };

  const healthyMetricsOrgans = metrics?.organHealth.filter(o => o.healthy).length ?? 0;
  const totalMetricsOrgans = metrics?.organHealth.length ?? 0;

  const memUsedPercent = metrics && metrics.system.memTotal > 0
    ? ((metrics.system.memTotal - metrics.system.memAvailable) / metrics.system.memTotal) * 100 : 0;
  const diskUsedPercent = metrics && metrics.system.diskTotal > 0
    ? ((metrics.system.diskTotal - metrics.system.diskFree) / metrics.system.diskTotal) * 100 : 0;

  const maxAvgMs = comparison.length > 0 ? Math.max(...comparison.map(c => c.avg_ms || 0), 1) : 1;

  const uniqueOrgans = [...new Set(events.map(e => e.organ))];

  // ── Tab definitions ────────────────────────────────────────

  const tabs: { id: MonitoringTab; label: string; icon: React.ElementType; color: string }[] = [
    { id: 'overview', label: 'Overview', icon: Activity, color: 'text-orange-500' },
    { id: 'system', label: 'System', icon: BarChart3, color: 'text-blue-500' },
    { id: 'benchmark', label: 'Benchmark', icon: Gauge, color: 'text-primary' },
    { id: 'activity', label: 'Activity', icon: Zap, color: 'text-emerald-500' },
    { id: 'agents', label: 'Agents & Jobs', icon: Bot, color: 'text-cyan-500' },
    { id: 'architecture', label: 'Architecture', icon: Layers, color: 'text-purple-500' },
  ];

  // ── Register sidebar content (LeftPanel) ───────────────────

  useEffect(() => {
    if (activeTab === 'overview') {
      // Overview sidebar: organ list grouped by category
      setPageSidebar(
        <LeftPanel
          items={sortedOrgans}
          filter={(organ, q) => organ.label.toLowerCase().includes(q) || organ.key.toLowerCase().includes(q)}
          renderItem={(organ) => (
            <button
              key={organ.key}
              onClick={() => { setSelectedOrganDetail(organ); setSelectedEvent(null); }}
              className={cn(
                'w-full text-left px-2 py-2 rounded-lg transition-colors',
                selectedOrganDetail?.key === organ.key
                  ? 'bg-orange-500/10 border border-orange-500/30'
                  : 'hover:bg-muted/50 border border-transparent',
              )}
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <span className={cn('w-2 h-2 rounded-full shrink-0', organ.status === 'ok' ? 'bg-emerald-500' : 'bg-red-500')} />
                <span className="text-xs font-medium truncate flex-1">{organ.label}</span>
              </div>
              <div className="ml-3.5 mt-1">
                <span className="text-[10px] font-mono text-muted-foreground">{organ.response_time_ms.toFixed(1)}ms</span>
              </div>
            </button>
          )}
          header={
            <div className="px-2 pb-2">
              <button
                onClick={runDiagnostics}
                disabled={diagLoading}
                className="flex items-center justify-center gap-1.5 rounded-lg bg-orange-500/10 border border-orange-500/30 px-3 py-1.5 text-xs font-medium text-orange-500 hover:bg-orange-500/20 transition-colors w-full"
              >
                <RefreshCw size={12} className={diagLoading ? 'animate-spin' : ''} />
                {diagLoading ? 'Checking...' : 'Refresh'}
              </button>
            </div>
          }
          placeholder={t('diagnostics.searchOrgans') || 'Search organs...'}
          emptyState={
            <div className="px-2 py-8 text-center text-muted-foreground/50">
              <Server className="w-8 h-8 mx-auto mb-1.5" />
              <p className="text-xs">No organs</p>
            </div>
          }
        />
      );
    } else if (activeTab === 'system') {
      // System sidebar: organ health grid
      setPageSidebar(
        <LeftPanel
          items={metrics?.organHealth ?? []}
          filter={(o, q) => o.organ.toLowerCase().includes(q)}
          renderItem={(o) => (
            <button
              key={o.organ}
              className={cn(
                'w-full text-left px-2 py-2 rounded-lg transition-colors',
                'hover:bg-muted/50 border border-transparent',
              )}
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <span className={cn('w-2 h-2 rounded-full shrink-0', o.healthy ? 'bg-emerald-500' : 'bg-red-500')} />
                <span className="text-xs font-medium truncate flex-1">{o.organ}</span>
                <span className="text-[10px]">{o.healthy ? '✅' : '❌'}</span>
              </div>
            </button>
          )}
          header={
            <div className="px-2 pb-2">
              <button
                onClick={() => setAutoRefreshMetrics(!autoRefreshMetrics)}
                className={cn(
                  'flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors w-full',
                  autoRefreshMetrics
                    ? 'bg-green-500/10 border border-green-500/30 text-green-600'
                    : 'bg-muted border border-border text-muted-foreground',
                )}
              >
                <div className={cn('w-1.5 h-1.5 rounded-full', autoRefreshMetrics ? 'bg-green-500 animate-pulse' : 'bg-muted-foreground')} />
                {autoRefreshMetrics ? 'Auto-refresh ON' : 'Auto-refresh OFF'}
              </button>
            </div>
          }
          placeholder={t('metrics.searchOrgans') || 'Search organs...'}
          emptyState={
            <div className="px-2 py-8 text-center text-muted-foreground/50">
              <BarChart3 className="w-8 h-8 mx-auto mb-1.5" />
              <p className="text-xs">No metrics data</p>
            </div>
          }
        />
      );
    } else if (activeTab === 'benchmark') {
      // Benchmark sidebar: organ selection
      setPageSidebar(
        <LeftPanel
          items={targets}
          filter={(target, q) => target.label.toLowerCase().includes(q) || target.organ.toLowerCase().includes(q)}
          renderItem={(target) => (
            <button
              key={target.organ}
              onClick={() => toggleOrgan(target.organ)}
              className={cn(
                'w-full text-left px-2 py-2 rounded-lg transition-colors',
                selectedOrgans.has(target.organ)
                  ? 'bg-primary/10 border border-primary/30'
                  : 'hover:bg-muted/50 border border-transparent',
              )}
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <span className={cn('w-2 h-2 rounded-full shrink-0', selectedOrgans.has(target.organ) ? 'bg-primary' : 'bg-muted-foreground/30')} />
                <span className="text-xs font-medium truncate flex-1">{target.label}</span>
              </div>
              <div className="ml-3.5 mt-1 flex gap-2">
                <button
                  onClick={(e) => { e.stopPropagation(); quickBench(target.organ); }}
                  className="text-[10px] text-primary hover:underline"
                  disabled={benchRunning}
                >
                  Quick bench
                </button>
              </div>
            </button>
          )}
          header={
            <div className="px-2 pb-2 flex gap-1.5">
              <button
                onClick={() => setSelectedOrgans(new Set(targets.map(t => t.organ)))}
                className="flex-1 flex items-center justify-center gap-1 rounded-lg border border-border px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted transition-colors"
              >
                All
              </button>
              <button
                onClick={() => setSelectedOrgans(new Set())}
                className="flex-1 flex items-center justify-center gap-1 rounded-lg border border-border px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted transition-colors"
              >
                None
              </button>
            </div>
          }
          placeholder={t('benchmark.searchOrgans') || 'Search organs...'}
          emptyState={
            <div className="px-2 py-8 text-center text-muted-foreground/50">
              <Gauge className="w-8 h-8 mx-auto mb-1.5" />
              <p className="text-xs">No targets</p>
            </div>
          }
        />
      );
    } else if (activeTab === 'activity') {
      // Activity sidebar: event list
      setPageSidebar(
        <LeftPanel
          items={events}
          filter={(event, q) => event.summary.toLowerCase().includes(q) || event.organ.toLowerCase().includes(q)}
          renderItem={(event) => (
            <button
              key={event.id}
              onClick={() => { setSelectedEvent(event); setSelectedOrganDetail(null); }}
              className={cn(
                'w-full text-left px-2 py-2 rounded-lg transition-colors',
                selectedEvent?.id === event.id
                  ? 'bg-emerald-500/10 border border-emerald-500/30'
                  : 'hover:bg-muted/50 border border-transparent',
              )}
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <span className="text-sm shrink-0">{event.emoji}</span>
                <span className="text-xs font-medium truncate flex-1">{event.summary}</span>
              </div>
              <div className="ml-5 mt-1 flex items-center gap-2">
                <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-medium', TYPE_COLORS[event.type] || 'text-muted-foreground bg-muted')}>
                  {event.type}
                </span>
                <span className="text-[10px] text-muted-foreground">{formatTimestamp(event.timestamp, t)}</span>
              </div>
            </button>
          )}
          header={
            <div className="px-2 pb-2 flex gap-1.5">
              <button
                onClick={() => setAutoRefreshActivity(!autoRefreshActivity)}
                className={cn(
                  'flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors flex-1',
                  autoRefreshActivity
                    ? 'bg-green-500/10 border border-green-500/30 text-green-500'
                    : 'border border-border text-muted-foreground hover:bg-muted',
                )}
              >
                <div className={cn('w-1.5 h-1.5 rounded-full', autoRefreshActivity ? 'bg-green-500 animate-pulse' : 'bg-muted-foreground')} />
                {autoRefreshActivity && sseConnected ? 'SSE Live' : 'Live'}
              </button>
              <button
                onClick={handleActivityRefresh}
                disabled={activityLoading}
                className="flex items-center justify-center rounded-lg border border-border px-2.5 py-1.5 text-xs hover:bg-muted transition-colors disabled:opacity-50"
              >
                {activityLoading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              </button>
            </div>
          }
          placeholder={t('activity.searchEvents') || 'Search events...'}
          emptyState={
            <div className="px-2 py-8 text-center text-muted-foreground/50">
              <Activity className="w-8 h-8 mx-auto mb-1.5" />
              <p className="text-xs">{t('activity.noEvents') || 'No events'}</p>
            </div>
          }
        />
      );
    }
    return () => setPageSidebar(null);
  }, [activeTab, sortedOrgans, selectedOrganDetail, metrics, targets, selectedOrgans, events, selectedEvent,
      autoRefreshActivity, sseConnected, activityLoading, diagLoading, benchRunning, t, setPageSidebar,
      runDiagnostics, toggleOrgan, quickBench, handleActivityRefresh, setAutoRefreshMetrics]);

  // ── Register workspace content (DetailPanel) ───────────────

  useEffect(() => {
    if (activeTab === 'overview' && selectedOrganDetail) {
      setPageWorkspace(
        <DetailPanel
          title={selectedOrganDetail.label}
          subtitle={selectedOrganDetail.key}
          icon={<Server className="w-5 h-5 text-orange-500" />}
          badge={selectedOrganDetail.status === 'ok' ? 'Healthy' : 'Error'}
          onClose={() => setSelectedOrganDetail(null)}
          sections={[
            {
              title: t('diagnostics.details') || 'Details',
              items: [
                { label: 'Key', value: selectedOrganDetail.key },
                { label: t('diagnostics.category') || 'Category', value: categoryLabels[selectedOrganDetail.category] || selectedOrganDetail.category },
                { label: t('diagnostics.status') || 'Status', value: selectedOrganDetail.status.toUpperCase(), icon: selectedOrganDetail.status === 'ok' ? <CheckCircle className="w-3.5 h-3.5 text-emerald-500" /> : <XCircle className="w-3.5 h-3.5 text-red-500" /> },
                { label: t('diagnostics.statusCode') || 'Status Code', value: String(selectedOrganDetail.status_code) },
                { label: t('diagnostics.responseTime') || 'Response Time', value: `${selectedOrganDetail.response_time_ms.toFixed(1)}ms`, icon: <Clock className="w-3.5 h-3.5" /> },
              ],
            },
          ]}
        />
      );
    } else if (activeTab === 'activity' && selectedEvent) {
      setPageWorkspace(
        <DetailPanel
          title={selectedEvent.summary}
          subtitle={`${selectedEvent.organ} · ${selectedEvent.type}`}
          icon={<span className="text-lg">{selectedEvent.emoji}</span>}
          badge={selectedEvent.type}
          onClose={() => setSelectedEvent(null)}
          sections={[
            {
              title: t('activity.details') || 'Details',
              items: [
                { label: 'ID', value: selectedEvent.id },
                { label: t('activity.organ') || 'Organ', value: selectedEvent.organ },
                { label: t('activity.type') || 'Type', value: selectedEvent.type },
                ...(selectedEvent.timestamp ? [{ label: t('activity.time') || 'Time', value: formatTimestamp(selectedEvent.timestamp, t), icon: <Clock className="w-3.5 h-3.5" /> }] : []),
              ],
            },
            ...(selectedEvent.detail ? [{
              title: t('activity.payload') || 'Payload',
              items: [{
                label: '',
                value: (
                  <pre className="text-xs font-mono whitespace-pre-wrap break-all max-h-64 overflow-y-auto">
                    {JSON.stringify(selectedEvent.detail, null, 2)}
                  </pre>
                ),
              }],
            }] : []),
          ]}
        />
      );
    } else {
      setPageWorkspace(null);
    }
    return () => setPageWorkspace(null);
  }, [activeTab, selectedOrganDetail, selectedEvent, categoryLabels, t, setPageWorkspace]);

  // ── Main render ────────────────────────────────────────────

  return (
    <PageLayout
      title="Monitoring"
      icon={<Activity size={16} className="text-orange-500" />}
    >
      <div className="h-full overflow-y-auto">
        {/* ── Tab bar ──────────────────────────────────────── */}
        <div className="border-b border-border px-3 lg:px-6">
          <div className="flex gap-1">
            {tabs.map(tab => (
              <button
                key={tab.id}
                onClick={() => {
                  setActiveTab(tab.id);
                  setSelectedOrganDetail(null);
                  setSelectedEvent(null);
                }}
                className={cn(
                  'px-3 py-2 text-xs font-medium border-b-2 transition-colors',
                  activeTab === tab.id
                    ? `border-current ${tab.color}`
                    : 'border-transparent text-muted-foreground hover:text-foreground hover:border-muted-foreground/30',
                )}
              >
                <tab.icon className="w-3.5 h-3.5 inline mr-1.5" />
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* ── Overview Tab ─────────────────────────────────── */}
        {activeTab === 'overview' && (
          <div className="p-3 lg:p-6 space-y-3 lg:space-y-6">
            {diagError && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 lg:p-4 text-xs lg:text-sm text-red-500">
                {diagError}
              </div>
            )}

            {diagData && (
              <>
                {/* Health Summary Cards */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-medium text-muted-foreground">{t('diagnostics.totalOrgans') || 'Total'}</span>
                      <div className="rounded-lg p-1.5 bg-blue-500/10"><Server size={14} className="text-blue-500" /></div>
                    </div>
                    <p className="text-xl lg:text-2xl font-bold">{diagData.summary.total}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      <span className="text-emerald-500">{diagData.summary.healthy}</span> ok ·
                      <span className="text-red-500 ml-1">{diagData.summary.unhealthy}</span> error
                    </p>
                  </div>
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-medium text-muted-foreground">{t('diagnostics.avgResponse') || 'Avg Response'}</span>
                      <div className="rounded-lg p-1.5 bg-amber-500/10"><Clock size={14} className="text-amber-500" /></div>
                    </div>
                    <p className="text-xl lg:text-2xl font-bold">{diagData.summary.avg_response_ms}<span className="text-xs lg:text-sm font-normal ml-0.5">ms</span></p>
                    <p className="text-xs text-muted-foreground mt-0.5">max {diagData.summary.max_response_ms.toFixed(1)}ms</p>
                  </div>
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-medium text-muted-foreground">CPU</span>
                      <div className="rounded-lg p-1.5 bg-violet-500/10"><Cpu size={14} className="text-violet-500" /></div>
                    </div>
                    <p className="text-xl lg:text-2xl font-bold">{diagData.system.cpu_percent}%</p>
                    <p className="text-xs text-muted-foreground mt-0.5">{diagData.system.cpu_count} cores</p>
                  </div>
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-medium text-muted-foreground">{t('diagnostics.memory') || 'Memory'}</span>
                      <div className="rounded-lg p-1.5 bg-emerald-500/10"><MemoryStick size={14} className="text-emerald-500" /></div>
                    </div>
                    <p className="text-xl lg:text-2xl font-bold">{diagData.system.memory_percent}%</p>
                    <p className="text-xs text-muted-foreground mt-0.5">{diagData.system.memory_used_gb}G / {diagData.system.memory_total_gb}G</p>
                  </div>
                </div>

                {/* System Gauges */}
                <div className="rounded-xl border border-border bg-card p-5 space-y-4">
                  <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                    <Gauge size={14} className="text-orange-500" />
                    {t('diagnostics.systemGauges') || 'System Gauges'}
                  </h3>
                  <GaugeBar value={diagData.system.cpu_percent} max={100} label="CPU" />
                  <GaugeBar value={diagData.system.memory_percent} max={100} label={`${t('diagnostics.memory') || 'Memory'} (${diagData.system.memory_used_gb}G / ${diagData.system.memory_total_gb}G)`} />
                  <GaugeBar value={diagData.system.disk_percent} max={100} label={`${t('diagnostics.disk') || 'Disk'} (${diagData.system.disk_used_gb}G / ${diagData.system.disk_total_gb}G)`} />
                </div>

                {/* ECharts */}
                <div className="grid gap-3 lg:gap-4 lg:grid-cols-2">
                  <ResponseTimeChart organs={sortedOrgans} />
                  <OrganStatusPieChart organs={sortedOrgans} />
                </div>

                {/* System Info */}
                <div className="rounded-xl border border-border bg-card p-5">
                  <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2 mb-4">
                    <Info size={14} className="text-blue-500" />
                    {t('diagnostics.systemInfo') || 'System Info'}
                  </h3>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4 text-xs lg:text-sm">
                    <div><span className="text-muted-foreground">{t('diagnostics.hostname') || 'Hostname'}: </span><span className="font-mono">{diagData.system.hostname}</span></div>
                    <div><span className="text-muted-foreground">{t('diagnostics.os') || 'OS'}: </span>{diagData.system.os}</div>
                    <div><span className="text-muted-foreground">{t('diagnostics.arch') || 'Arch'}: </span>{diagData.system.arch}</div>
                    <div><span className="text-muted-foreground">Python: </span><span className="font-mono">{diagData.system.python}</span></div>
                    <div><span className="text-muted-foreground">{t('diagnostics.uptime') || 'Uptime'}: </span>{formatUptime(diagData.system.uptime_seconds)}</div>
                  </div>
                </div>

                {/* Organ List — grouped by category */}
                {Object.entries(grouped).map(([category, organs]) => (
                  <div key={category} className="rounded-xl border border-border bg-card overflow-hidden">
                    <div className="px-5 py-3 border-b border-border bg-muted/30">
                      <div className="flex items-center justify-between">
                        <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                          <Gauge size={14} className="text-orange-500" />
                          {categoryLabels[category] || category}
                        </h3>
                        <span className="text-[10px] text-muted-foreground">
                          {organs.filter(o => o.status === 'ok').length}/{organs.length} {t('diagnostics.healthy') || 'healthy'}
                        </span>
                      </div>
                    </div>
                    <div className="divide-y divide-border">
                      {organs.map(organ => (
                        <div key={organ.key} className="flex items-center gap-2 lg:gap-4 px-5 py-3 hover:bg-muted/30 transition-colors">
                          <StatusBadge status={organ.status} />
                          <span className="text-xs lg:text-sm font-medium flex-1">{organ.label}</span>
                          <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-mono text-muted-foreground">{organ.key}</span>
                          <ResponseTimeBar ms={organ.response_time_ms} />
                        </div>
                      ))}
                    </div>
                  </div>
                ))}

                {/* Overall Status Banner */}
                <div
                  className={cn(
                    'rounded-xl border p-5 flex items-center gap-2 lg:gap-4',
                    diagData.summary.overall === 'ok'
                      ? 'border-emerald-500/30 bg-emerald-500/5'
                      : 'border-amber-500/30 bg-amber-500/5',
                  )}
                >
                  {diagData.summary.overall === 'ok' ? (
                    <CheckCircle size={24} className="text-emerald-500 shrink-0" />
                  ) : (
                    <AlertTriangle size={24} className="text-amber-500 shrink-0" />
                  )}
                  <div>
                    <p className="text-xs lg:text-sm font-semibold">
                      {diagData.summary.overall === 'ok'
                        ? (t('diagnostics.allOk') || 'All systems operational')
                        : (t('diagnostics.partialError') || 'Some systems have issues')}
                    </p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {diagData.summary.healthy}/{diagData.summary.total} {t('diagnostics.organHealth') || 'organs healthy'} ·
                      {t('diagnostics.avgResponse') || 'Avg'} {diagData.summary.avg_response_ms}ms ·
                      {diagData.system.hostname} ({diagData.system.os})
                    </p>
                  </div>
                </div>
              </>
            )}

            {!diagData && diagLoading && (
              <div className="flex items-center justify-center py-16">
                <Loader2 size={24} className="animate-spin text-orange-500" />
              </div>
            )}
          </div>
        )}

        {/* ── System Tab ───────────────────────────────────── */}
        {activeTab === 'system' && (
          <div className="p-3 lg:p-6 space-y-4 lg:space-y-6 max-w-7xl mx-auto">
            {metricsLoading && !metrics ? (
              <div className="flex items-center justify-center h-64">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
              </div>
            ) : !metrics ? (
              <div className="p-3 lg:p-6 text-center text-muted-foreground">
                {t('metrics.loadFailed', 'Failed to load metrics')}
              </div>
            ) : (
              <>
                {/* Header bar */}
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-lg lg:text-xl font-bold flex items-center gap-2">
                      📊 {t('metrics.title', 'Prometheus Metrics')}
                    </h2>
                    <p className="text-xs text-muted-foreground mt-1">
                      {lastMetricsUpdate && `${t('metrics.updated', 'Updated')}: ${lastMetricsUpdate.toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}`}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setAutoRefreshMetrics(!autoRefreshMetrics)}
                      className={cn(
                        'px-3 py-1.5 text-xs rounded-lg border',
                        autoRefreshMetrics
                          ? 'bg-green-500/10 border-green-500/30 text-green-600'
                          : 'bg-muted border-border text-muted-foreground',
                      )}
                    >
                      {autoRefreshMetrics ? t('metrics.autoRefresh', 'Auto-refresh ON') : t('metrics.autoRefreshOff', 'Auto-refresh OFF')}
                    </button>
                    <button onClick={fetchMetrics} className="px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-accent">
                      {t('metrics.refresh', 'Refresh')}
                    </button>
                  </div>
                </div>

                {/* Summary Cards */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="text-xs text-muted-foreground mb-1">{t('metrics.uptime', 'Uptime')}</div>
                    <div className="text-xl lg:text-2xl font-bold">{formatUptime(metrics.uptime)}</div>
                  </div>
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="text-xs text-muted-foreground mb-1">{t('metrics.organs', 'Organs')}</div>
                    <div className="text-xl lg:text-2xl font-bold">
                      <span className={healthyMetricsOrgans === totalMetricsOrgans ? 'text-green-500' : 'text-yellow-500'}>{healthyMetricsOrgans}</span>
                      <span className="text-muted-foreground">/{totalMetricsOrgans}</span>
                    </div>
                  </div>
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="text-xs text-muted-foreground mb-1">{t('metrics.memory', 'Memory')}</div>
                    <div className="text-xl lg:text-2xl font-bold">{formatBytes(metrics.memory)}</div>
                    <div className="text-xs text-muted-foreground">RSS</div>
                  </div>
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="text-xs text-muted-foreground mb-1">{t('metrics.cpuTime', 'CPU Time')}</div>
                    <div className="text-xl lg:text-2xl font-bold">{metrics.cpuTime.toFixed(1)}s</div>
                  </div>
                </div>

                {/* System Resources */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-2 lg:gap-4">
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="text-xs lg:text-sm font-medium mb-3">🖥️ {t('metrics.cpu', 'CPU')}</div>
                    <div className="text-2xl lg:text-3xl font-bold mb-1">{metrics.system.cpuCount}</div>
                    <div className="text-xs text-muted-foreground">{t('metrics.cores', 'cores')}</div>
                  </div>
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="text-xs lg:text-sm font-medium mb-3">🧠 {t('metrics.systemMemory', 'System Memory')}</div>
                    <div className="w-full bg-muted rounded-full h-2.5 mb-2">
                      <div className={cn('h-2.5 rounded-full', memUsedPercent > 90 ? 'bg-red-500' : memUsedPercent > 70 ? 'bg-yellow-500' : 'bg-green-500')} style={{ width: `${Math.min(memUsedPercent, 100)}%` }} />
                    </div>
                    <div className="flex justify-between text-xs text-muted-foreground">
                      <span>{formatBytes(metrics.system.memTotal - metrics.system.memAvailable)} {t('metrics.used', 'used')}</span>
                      <span>{formatBytes(metrics.system.memTotal)} total</span>
                    </div>
                  </div>
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="text-xs lg:text-sm font-medium mb-3">💾 {t('metrics.disk', 'Disk')}</div>
                    <div className="w-full bg-muted rounded-full h-2.5 mb-2">
                      <div className={cn('h-2.5 rounded-full', diskUsedPercent > 90 ? 'bg-red-500' : diskUsedPercent > 70 ? 'bg-yellow-500' : 'bg-green-500')} style={{ width: `${Math.min(diskUsedPercent, 100)}%` }} />
                    </div>
                    <div className="flex justify-between text-xs text-muted-foreground">
                      <span>{formatBytes(metrics.system.diskTotal - metrics.system.diskFree)} {t('metrics.used', 'used')}</span>
                      <span>{formatBytes(metrics.system.diskTotal)} total</span>
                    </div>
                  </div>
                </div>

                {/* Organ Health Grid */}
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <div className="text-xs lg:text-sm font-medium mb-3">🫀 {t('metrics.organHealth', 'Organ Health Status')}</div>
                  <div className="grid grid-cols-4 md:grid-cols-8 gap-2">
                    {metrics.organHealth.map(o => (
                      <div key={o.organ} className={cn('text-center p-2 rounded-lg text-xs', o.healthy ? 'bg-green-500/10 text-green-600 border border-green-500/20' : 'bg-red-500/10 text-red-600 border border-red-500/20')}>
                        <div className="text-lg">{o.healthy ? '✅' : '❌'}</div>
                        <div className="font-medium mt-1">{o.organ}</div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* ECharts */}
                <div className="grid gap-3 lg:gap-4 lg:grid-cols-2">
                  <HttpRequestRateChart httpRequests={metrics.httpRequests} />
                  <ErrorRateChart httpErrors={metrics.httpErrors} />
                </div>

                {/* Top HTTP Endpoints + Errors */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2 lg:gap-4">
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="text-xs lg:text-sm font-medium mb-3">🔥 {t('metrics.topEndpoints', 'Top HTTP Endpoints')}</div>
                    <div className="space-y-1 max-h-80 overflow-y-auto">
                      {metrics.httpRequests.map((r, i) => (
                        <div key={i} className="flex items-center justify-between text-xs py-1 px-2 rounded hover:bg-accent">
                          <div className="flex items-center gap-2 flex-1 min-w-0">
                            <span className={cn('px-1.5 py-0.5 rounded text-[10px] font-mono', r.method === 'GET' ? 'bg-blue-500/10 text-blue-600' : r.method === 'POST' ? 'bg-green-500/10 text-green-600' : 'bg-orange-500/10 text-orange-600')}>{r.method}</span>
                            <span className="font-mono truncate">{r.path}</span>
                          </div>
                          <span className="font-bold ml-2">{r.count}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="text-xs lg:text-sm font-medium mb-3">⚠️ {t('metrics.errors', 'HTTP Errors')}</div>
                    {metrics.httpErrors.length === 0 ? (
                      <div className="text-center text-muted-foreground py-8">{t('metrics.noErrors', 'No errors recorded ✨')}</div>
                    ) : (
                      <div className="space-y-1">
                        {metrics.httpErrors.map((e, i) => (
                          <div key={i} className="flex items-center justify-between text-xs py-1 px-2 rounded hover:bg-accent">
                            <span className={cn('px-1.5 py-0.5 rounded text-[10px] font-mono', e.status.startsWith('4') ? 'bg-yellow-500/10 text-yellow-600' : 'bg-red-500/10 text-red-600')}>{e.status}</span>
                            <span className="font-bold">{e.count}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="mt-6 p-3 bg-muted/50 rounded-lg">
                      <div className="text-xs font-medium mb-1">{t('metrics.scrapeConfig', 'Prometheus Scrape Config')}</div>
                      <code className="text-xs text-muted-foreground block">
                        {`scrape_configs:\n  - job_name: 'opensoul'\n    scrape_interval: 15s\n    static_configs:\n      - targets: ['localhost:8090']\n    metrics_path: /metrics`}
                      </code>
                    </div>
                  </div>
                </div>

                {/* Instance Info */}
                {metrics.info && (
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="text-xs lg:text-sm font-medium mb-3">ℹ️ {t('metrics.instanceInfo', 'Instance Info')}</div>
                    <div className="flex gap-2 lg:gap-6 text-xs">
                      <div><span className="text-muted-foreground">Version: </span><span className="font-mono">{metrics.info.version}</span></div>
                      <div><span className="text-muted-foreground">Python: </span><span className="font-mono">{metrics.info.python}</span></div>
                      <div><span className="text-muted-foreground">OS: </span><span className="font-mono">{metrics.info.os}</span></div>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* ── Benchmark Tab ────────────────────────────────── */}
        {activeTab === 'benchmark' && (
          <div className="p-3 lg:p-6 space-y-3 lg:space-y-6">
            {/* Sub-tab bar */}
            <div className="flex items-center gap-2">
              {([
                { id: 'run' as BenchmarkSubTab, label: t('benchmark.run'), icon: Play },
                { id: 'comparison' as BenchmarkSubTab, label: t('benchmark.comparison'), icon: BarChart3 },
                { id: 'history' as BenchmarkSubTab, label: t('benchmark.history'), icon: History },
              ]).map(tab => (
                <button
                  key={tab.id}
                  onClick={() => setBenchTab(tab.id)}
                  className={cn(
                    'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors',
                    benchTab === tab.id ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                  )}
                >
                  <tab.icon size={14} />
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Run sub-tab */}
            {benchTab === 'run' && (
              <div className="space-y-3 lg:space-y-6">
                {/* Config Panel */}
                <div className="rounded-xl border border-border bg-card p-5">
                  <div className="mb-4 flex items-center justify-between">
                    <h2 className="text-xs lg:text-sm font-semibold">{t('benchmark.testConfig')}</h2>
                    <div className="flex items-center gap-2">
                      <button onClick={() => setSelectedOrgans(new Set(targets.map(t => t.organ)))} className="rounded px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground">{t('benchmark.selectAll')}</button>
                      <button onClick={() => setSelectedOrgans(new Set())} className="rounded px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground">{t('benchmark.clearAll')}</button>
                    </div>
                  </div>
                  <div className="mb-4 flex flex-wrap gap-1.5">
                    {targets.map(t => (
                      <button
                        key={t.organ}
                        onClick={() => toggleOrgan(t.organ)}
                        className={cn(
                          'flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors',
                          selectedOrgans.has(t.organ)
                            ? 'bg-primary/10 text-primary border border-primary/30'
                            : 'bg-muted text-muted-foreground border border-transparent hover:border-border',
                        )}
                      >
                        {t.label}
                      </button>
                    ))}
                  </div>
                  <div className="flex items-center gap-3 lg:gap-6 flex-wrap">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">{t('benchmark.iterations')}</span>
                      <input type="number" value={iterations} onChange={e => setIterations(Math.max(1, Math.min(200, Number(e.target.value))))} className="w-16 rounded-lg border border-border bg-background px-2 py-1 text-center text-xs" min={1} max={200} />
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">{t('benchmark.concurrency')}</span>
                      <input type="number" value={concurrency} onChange={e => setConcurrency(Math.max(1, Math.min(50, Number(e.target.value))))} className="w-16 rounded-lg border border-border bg-background px-2 py-1 text-center text-xs" min={1} max={50} />
                    </div>
                    <button onClick={runBenchmark} disabled={benchRunning || selectedOrgans.size === 0} className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50">
                      {benchRunning ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                      {benchRunning ? t('benchmark.testing') : t('benchmark.runTest', { count: selectedOrgans.size })}
                    </button>
                  </div>
                </div>

                {/* Results */}
                {currentRun && currentRun.results && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <h2 className="text-xs lg:text-sm font-semibold">
                        {t('benchmark.testResults', { organs: currentRun.organs_benchmarked, iterations: currentRun.iterations })}
                      </h2>
                      <span className="text-xs text-muted-foreground">Run ID: {currentRun.run_id}</span>
                    </div>
                    <div className="overflow-x-auto rounded-xl border border-border">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b border-border bg-muted/50">
                            <th className="px-3 py-2 text-left font-medium">{t('benchmark.organ')}</th>
                            <th className="px-3 py-2 text-right font-medium">{t('benchmark.success')}</th>
                            <th className="px-3 py-2 text-right font-medium">{t('benchmark.failure')}</th>
                            <th className="px-3 py-2 text-right font-medium">{t('benchmark.avg')}</th>
                            <th className="px-3 py-2 text-right font-medium">P50</th>
                            <th className="px-3 py-2 text-right font-medium">P95</th>
                            <th className="px-3 py-2 text-right font-medium">P99</th>
                            <th className="px-3 py-2 text-right font-medium">{t('benchmark.min')}</th>
                            <th className="px-3 py-2 text-right font-medium">{t('benchmark.max')}</th>
                            <th className="px-3 py-2 text-right font-medium">RPS</th>
                          </tr>
                        </thead>
                        <tbody>
                          {currentRun.results.slice().sort((a, b) => a.latency.avg_ms - b.latency.avg_ms).map(r => (
                            <tr key={r.organ} className="border-b border-border/50 hover:bg-muted/30">
                              <td className="px-3 py-2 font-medium">{r.label}</td>
                              <td className="px-3 py-2 text-right text-green-500">{r.success}</td>
                              <td className="px-3 py-2 text-right">{r.errors > 0 ? <span className="text-red-400">{r.errors}</span> : <span className="text-muted-foreground">0</span>}</td>
                              <td className={cn('px-3 py-2 text-right font-mono font-medium', getLatencyColor(r.latency.avg_ms))}>{formatMs(r.latency.avg_ms)}</td>
                              <td className="px-3 py-2 text-right font-mono text-muted-foreground">{formatMs(r.latency.p50_ms)}</td>
                              <td className="px-3 py-2 text-right font-mono text-muted-foreground">{formatMs(r.latency.p95_ms)}</td>
                              <td className="px-3 py-2 text-right font-mono text-muted-foreground">{formatMs(r.latency.p99_ms)}</td>
                              <td className="px-3 py-2 text-right font-mono text-muted-foreground">{formatMs(r.latency.min_ms)}</td>
                              <td className="px-3 py-2 text-right font-mono text-muted-foreground">{formatMs(r.latency.max_ms)}</td>
                              <td className="px-3 py-2 text-right font-mono">{r.throughput_rps.toFixed(0)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    {/* ECharts latency comparison */}
                    <BenchmarkLatencyChart results={currentRun.results} />

                    {/* Visual Latency Bars */}
                    <div className="rounded-xl border border-border bg-card p-5">
                      <h3 className="mb-3 text-xs font-semibold text-muted-foreground">{t('benchmark.latencyDistribution')}</h3>
                      <div className="space-y-2">
                        {currentRun.results.slice().sort((a, b) => a.latency.avg_ms - b.latency.avg_ms).map(r => {
                          const maxMs = Math.max(...currentRun.results.map(x => x.latency.avg_ms), 1);
                          const pct = (r.latency.avg_ms / maxMs) * 100;
                          return (
                            <div key={r.organ} className="flex items-center gap-3">
                              <span className="w-24 shrink-0 text-xs font-medium truncate">{r.label}</span>
                              <div className="flex-1 h-5 rounded-full bg-muted/50 overflow-hidden">
                                <div className={cn('h-full rounded-full transition-all', getLatencyBg(r.latency.avg_ms))} style={{ width: `${Math.max(pct, 1)}%` }} />
                              </div>
                              <span className={cn('w-16 text-right text-xs font-mono', getLatencyColor(r.latency.avg_ms))}>{formatMs(r.latency.avg_ms)}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                )}

                {!currentRun && !benchRunning && (
                  <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
                    <Gauge size={48} className="mb-4 opacity-30" />
                    <p className="text-xs lg:text-sm">{t('benchmark.selectOrgans')}</p>
                    <p className="text-xs">{t('benchmark.resultsHint')}</p>
                  </div>
                )}
              </div>
            )}

            {/* Comparison sub-tab */}
            {benchTab === 'comparison' && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-xs lg:text-sm font-semibold">{t('benchmark.comparisonTitle')}</h2>
                  <button onClick={fetchComparison} className="flex items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground">
                    <RefreshCw size={12} /> {t('common.refresh')}
                  </button>
                </div>
                {comparison.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
                    <BarChart3 size={48} className="mb-4 opacity-30" />
                    <p className="text-xs lg:text-sm">{t('benchmark.noData')}</p>
                    <p className="text-xs">{t('benchmark.noDataHint')}</p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {comparison.map((item, idx) => {
                      const pct = (item.avg_ms / maxAvgMs) * 100;
                      const rank = idx + 1;
                      const medal = rank === 1 ? '🥇' : rank === 2 ? '🥈' : rank === 3 ? '🥉' : `#${rank}`;
                      return (
                        <div key={item.organ}>
                          <div
                            className="flex items-center gap-3 rounded-lg border border-border bg-card p-3 cursor-pointer hover:border-primary/30 transition-colors"
                            onClick={() => setExpandedOrgan(expandedOrgan === item.organ ? null : item.organ)}
                          >
                            <span className="w-8 text-center text-xs lg:text-sm">{medal}</span>
                            <span className="w-24 shrink-0 text-xs font-medium">{item.label}</span>
                            <div className="flex-1 h-6 rounded-full bg-muted/50 overflow-hidden">
                              <div className={cn('h-full rounded-full transition-all', getLatencyBg(item.avg_ms))} style={{ width: `${Math.max(pct, 1)}%` }} />
                            </div>
                            <div className="flex items-center gap-2 lg:gap-4 text-xs">
                              <span className={cn('font-mono font-medium', getLatencyColor(item.avg_ms))}>{formatMs(item.avg_ms)}</span>
                              <span className="text-muted-foreground">P95: {formatMs(item.p95_ms)}</span>
                              <span className="text-muted-foreground">{item.rps?.toFixed(0) || '—'} rps</span>
                              <span className="text-muted-foreground">{item.success}/{item.iterations}</span>
                            </div>
                            {expandedOrgan === item.organ ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                          </div>
                          {expandedOrgan === item.organ && (
                            <div className="ml-11 rounded-lg border border-border bg-muted/30 p-3 lg:p-4">
                              <div className="grid grid-cols-2 md:grid-cols-3 gap-2 lg:gap-4 text-xs">
                                <div><span className="text-muted-foreground">{t('benchmark.minLatency')}</span><p className="font-mono font-medium">{formatMs(item.min_ms)}</p></div>
                                <div><span className="text-muted-foreground">{t('benchmark.maxLatency')}</span><p className="font-mono font-medium">{formatMs(item.max_ms)}</p></div>
                                <div><span className="text-muted-foreground">{t('benchmark.p99Latency')}</span><p className="font-mono font-medium">{formatMs(item.p99_ms)}</p></div>
                                <div><span className="text-muted-foreground">{t('benchmark.totalDuration')}</span><p className="font-mono font-medium">{formatMs(item.total_ms)}</p></div>
                                <div><span className="text-muted-foreground">{t('benchmark.throughput')}</span><p className="font-mono font-medium">{item.rps?.toFixed(1)} req/s</p></div>
                                <div><span className="text-muted-foreground">{t('benchmark.testTime')}</span><p className="font-mono font-medium">{new Date(item.timestamp * 1000).toLocaleString('zh-CN', { fractionalSecondDigits: 3 })}</p></div>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {/* History sub-tab */}
            {benchTab === 'history' && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-xs lg:text-sm font-semibold">{t('benchmark.historyTitle')}</h2>
                  <div className="flex items-center gap-2">
                    <select value={historyFilter} onChange={e => setHistoryFilter(e.target.value)} className="rounded-lg border border-border bg-background px-2 py-1 text-xs">
                      <option value="">{t('benchmark.allOrgans')}</option>
                      {targets.map(t => (<option key={t.organ} value={t.organ}>{t.label}</option>))}
                    </select>
                    <button onClick={fetchHistory} className="flex items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"><RefreshCw size={12} /></button>
                    <button onClick={deleteHistory} className="flex items-center gap-1 rounded px-2 py-1 text-xs text-red-400 hover:bg-red-500/10"><Trash2 size={12} /> {t('common.delete')}</button>
                  </div>
                </div>
                {history.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
                    <History size={48} className="mb-4 opacity-30" />
                    <p className="text-xs lg:text-sm">{t('benchmark.noHistory')}</p>
                  </div>
                ) : (
                  <div className="overflow-x-auto rounded-xl border border-border">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-border bg-muted/50">
                          <th className="px-3 py-2 text-left font-medium">{t('benchmark.time')}</th>
                          <th className="px-3 py-2 text-left font-medium">{t('benchmark.organ')}</th>
                          <th className="px-3 py-2 text-right font-medium">{t('benchmark.iterShort')}</th>
                          <th className="px-3 py-2 text-right font-medium">{t('benchmark.success')}</th>
                          <th className="px-3 py-2 text-right font-medium">{t('benchmark.avg')}</th>
                          <th className="px-3 py-2 text-right font-medium">P50</th>
                          <th className="px-3 py-2 text-right font-medium">P95</th>
                          <th className="px-3 py-2 text-right font-medium">P99</th>
                          <th className="px-3 py-2 text-right font-medium">RPS</th>
                        </tr>
                      </thead>
                      <tbody>
                        {history.map(h => (
                          <tr key={h.id} className="border-b border-border/50 hover:bg-muted/30">
                            <td className="px-3 py-2 text-muted-foreground">{new Date(h.timestamp * 1000).toLocaleString('zh-CN', { fractionalSecondDigits: 3 })}</td>
                            <td className="px-3 py-2 font-medium">{h.label}</td>
                            <td className="px-3 py-2 text-right">{h.iterations}</td>
                            <td className="px-3 py-2 text-right text-green-500">{h.success}</td>
                            <td className={cn('px-3 py-2 text-right font-mono font-medium', getLatencyColor(h.avg_ms))}>{formatMs(h.avg_ms)}</td>
                            <td className="px-3 py-2 text-right font-mono text-muted-foreground">{formatMs(h.p50_ms)}</td>
                            <td className="px-3 py-2 text-right font-mono text-muted-foreground">{formatMs(h.p95_ms)}</td>
                            <td className="px-3 py-2 text-right font-mono text-muted-foreground">{formatMs(h.p99_ms)}</td>
                            <td className="px-3 py-2 text-right font-mono">{h.rps?.toFixed(0)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Activity Tab ─────────────────────────────────── */}
        {activeTab === 'activity' && (
          <div className="p-3 lg:p-6 space-y-3 lg:space-y-6">
            {/* Filter bar */}
            <div className="flex items-center gap-2 flex-wrap">
              <button
                onClick={() => setShowFilters(!showFilters)}
                className={cn(
                  'flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs transition-colors',
                  selectedOrgan ? 'border-primary/30 bg-primary/5 text-primary' : 'border-border text-muted-foreground hover:bg-muted',
                )}
              >
                <Filter size={12} />
                {selectedOrgan || t('activity.allOrgans') || 'All Organs'}
                <ChevronDown size={10} />
              </button>
              {showFilters && (
                <div className="flex flex-wrap gap-1.5">
                  <button
                    onClick={() => { setSelectedOrgan(null); setShowFilters(false); }}
                    className={cn('rounded-lg px-3 py-1.5 text-xs transition-colors', !selectedOrgan ? 'bg-primary text-primary-foreground' : 'border border-border hover:bg-muted')}
                  >
                    {t('activity.all') || 'All'}
                  </button>
                  {uniqueOrgans.map(organ => (
                    <button
                      key={organ}
                      onClick={() => { setSelectedOrgan(organ); setShowFilters(false); }}
                      className={cn('rounded-lg px-3 py-1.5 text-xs transition-colors flex items-center gap-1.5', selectedOrgan === organ ? 'bg-primary text-primary-foreground' : 'border border-border hover:bg-muted')}
                    >
                      {events.find(e => e.organ === organ)?.emoji} {organ}
                    </button>
                  ))}
                </div>
              )}
              <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-500">
                {events.length} {t('activity.events') || 'events'}
              </span>
            </div>

            {/* Summary cards */}
            {summary && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <div className="text-xl lg:text-2xl font-bold">{summary.total_events}</div>
                  <div className="text-[10px] text-muted-foreground">{t('activity.totalEvents') || 'Total Events'}</div>
                </div>
                {summary.most_active_organ && (
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="text-xs text-muted-foreground mb-1">{t('activity.mostActive') || 'Most Active'}</div>
                    <div className="text-sm font-medium capitalize">{summary.most_active_organ}</div>
                  </div>
                )}
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <div className="text-xl lg:text-2xl font-bold">{Object.keys(summary.by_organ || {}).length}</div>
                  <div className="text-[10px] text-muted-foreground">{t('activity.byOrgan') || 'Active Organs'}</div>
                </div>
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <div className="text-xl lg:text-2xl font-bold">{Object.keys(summary.by_type || {}).length}</div>
                  <div className="text-[10px] text-muted-foreground">{t('activity.byType') || 'Event Types'}</div>
                </div>
              </div>
            )}

            {/* Event list */}
            {events.length === 0 && !activityLoading ? (
              <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
                <Activity size={48} className="mb-4 opacity-30" />
                <p className="text-sm">{t('activity.noEvents') || 'No events yet'}</p>
                <p className="text-xs mt-1">{t('activity.noEventsHint') || 'Events will appear here as organs emit them'}</p>
              </div>
            ) : (
              <div className="rounded-xl border border-border bg-card overflow-hidden">
                <div className="divide-y divide-border">
                  {events.map(event => {
                    const typeColor = TYPE_COLORS[event.type] || 'text-muted-foreground bg-muted';
                    return (
                      <div
                        key={event.id}
                        className="px-3 lg:px-5 py-3 transition-colors hover:bg-muted/30 cursor-pointer"
                        onClick={() => { setSelectedEvent(event); setSelectedOrganDetail(null); }}
                      >
                        <div className="flex items-start gap-3">
                          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted">
                            <span className="text-sm">{event.emoji}</span>
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-0.5">
                              <span className="text-sm font-medium truncate">{event.summary}</span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-medium', typeColor)}>{event.type}</span>
                              <span className="text-[10px] text-muted-foreground capitalize">{event.organ}</span>
                              {event.timestamp && (
                                <span className="text-[10px] text-muted-foreground flex items-center gap-0.5">
                                  <Clock size={9} /> {formatTimestamp(event.timestamp, t)}
                                </span>
                              )}
                            </div>
                          </div>
                          <span className="text-[10px] text-muted-foreground whitespace-nowrap shrink-0">
                            {formatTimestamp(event.collected_at, t)}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Agents & Jobs Tab（P0-8后台作业面板 + goose peek三指标 + 进化管线 + 模型链路trace） ── */}
        {activeTab === 'agents' && (
          <div className="p-3 lg:p-6 space-y-3 lg:space-y-6">
            {/* Toolbar */}
            <div className="flex items-center gap-2 flex-wrap">
              <span className="rounded-full bg-cyan-500/10 px-2 py-0.5 text-xs font-medium text-cyan-500">
                {agentPeek?.summary.total_sessions ?? 0} sessions
              </span>
              <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-500">
                {jobsHealth?.total ?? 0} jobs
              </span>
              <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                auto 10s
              </span>
              {agentsUpdated && (
                <span className="text-[10px] text-muted-foreground flex items-center gap-0.5">
                  <Clock size={9} /> {agentsUpdated.toLocaleTimeString()}
                </span>
              )}
              <button
                onClick={fetchAgentsData}
                disabled={agentsLoading}
                className="ml-auto flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs hover:bg-muted transition-colors disabled:opacity-50"
              >
                {agentsLoading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                Refresh
              </button>
            </div>

            {/* Summary cards */}
            <div className="grid grid-cols-2 md:grid-cols-6 gap-2 lg:gap-4">
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className="text-xl lg:text-2xl font-bold">{agentPeek?.summary.total_sessions ?? 0}</div>
                <div className="text-[10px] text-muted-foreground">
                  Agent Sessions
                  {agentPeek?.summary.by_status && Object.keys(agentPeek.summary.by_status).length > 0 && (
                    <span className="ml-1">
                      ({Object.entries(agentPeek.summary.by_status).map(([k, v]) => `${k}:${v}`).join(' ')})
                    </span>
                  )}
                </div>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className="text-xl lg:text-2xl font-bold">{agentPeek?.summary.durable_turns_total ?? 0}</div>
                <div className="text-[10px] text-muted-foreground">
                  Durable Turns · {agentPeek?.summary.buffered_total ?? 0} buffered
                </div>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className="text-xl lg:text-2xl font-bold">{agentPeek?.summary.steer_injected_total ?? 0}</div>
                <div className="text-[10px] text-muted-foreground">
                  Steer Injected · {agentPeek?.summary.total_noops ?? 0} noops
                </div>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className="text-xl lg:text-2xl font-bold">{jobsHealth?.workers ?? 0}</div>
                <div className="text-[10px] text-muted-foreground">
                  Job Workers · {jobsHealth?.queue_size ?? 0} queued · {jobsHealth?.running ? 'running' : 'idle'}
                </div>
              </div>
              {/* P0-2: 工具结果溢出 — AIHawk SHOWN/SENT双预算（截断必须显式可见） */}
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className={cn('text-xl lg:text-2xl font-bold', (toolSpill?.truncated_calls ?? 0) > 0 && 'text-amber-500')}>
                  {toolSpill?.truncated_calls ?? 0}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  Tool Output Spills
                  {toolSpill && (
                    <span className="ml-1 font-mono">
                      SHOWN {toolSpill.shown_chars_total}/SENT {toolSpill.sent_chars_total}
                    </span>
                  )}
                </div>
              </div>
              {/* P1: 上下文逐项token归因（SDKContextUsage）——最新LLM请求上下文总量/窗口占比/超限性质 */}
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className={cn('text-xl lg:text-2xl font-bold', !!tokenAttr?.summary?.latest?.over_limit && 'text-amber-500')}>
                  {tokenAttr?.summary?.latest?.total_tokens ?? 0}
                  {tokenAttr?.summary?.latest?.raw_max_tokens ? (
                    <span className="text-xs text-muted-foreground font-normal ml-1">
                      /{tokenAttr.summary.latest.raw_max_tokens >= 10000
                        ? `${Math.round(tokenAttr.summary.latest.raw_max_tokens / 1024)}k`
                        : tokenAttr.summary.latest.raw_max_tokens}
                    </span>
                  ) : null}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  Context Tokens
                  {tokenAttr?.summary?.latest && (
                    <span className="ml-1 font-mono">
                      {tokenAttr.summary.latest.percentage ?? 0}%
                      {tokenAttr.summary.latest.over_limit
                        ? ` · ${tokenAttr.summary.latest.over_limit.kind}`
                        : ''}
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Card 1: Agent Sessions — goose peek三指标（status/durable_turns/idle）+ noop自报 + steer记账 */}
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <div className="flex items-center gap-2 mb-3">
                <Bot size={14} className="text-cyan-500" />
                <h4 className="text-xs font-medium">Agent Sessions (goose peek)</h4>
              </div>
              {peekErr ? (
                <p className="text-xs text-red-500">{peekErr}</p>
              ) : !agentPeek || agentPeek.sessions.length === 0 ? (
                <div className="py-8 text-center text-muted-foreground/50">
                  <Bot className="w-8 h-8 mx-auto mb-1.5" />
                  <p className="text-xs">No agent sessions recorded</p>
                  <p className="text-[10px] mt-1">Sessions appear after a chat task runs via ACP proxy</p>
                </div>
              ) : (
                <div className="divide-y divide-border">
                  {agentPeek.sessions.map(s => (
                    <div key={s.session_id} className="py-2.5 first:pt-0 last:pb-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-medium', sessionStatusColor(s.status))}>
                          {s.status}
                        </span>
                        <span className="text-xs font-mono truncate flex-1">{s.session_id}</span>
                        {s.aborted > 0 && (
                          <span className="rounded px-1.5 py-0.5 text-[10px] font-medium text-red-500 bg-red-500/10">
                            aborted {s.aborted}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 flex-wrap text-[10px] text-muted-foreground">
                        <span>turns <span className="font-mono text-foreground">{s.durable_turns}</span></span>
                        <span>idle <span className="font-mono text-foreground">{formatUptime(Math.floor(s.idle_seconds))}</span></span>
                        <span>buffered <span className="font-mono text-foreground">{s.buffered}</span></span>
                        <span>
                          steer <span className="font-mono text-foreground">{s.steer_queued}→{s.steer_injected}</span>
                        </span>
                        <span className={cn('font-mono', s.noop_streak >= 3 ? 'text-amber-500' : 'text-foreground')}>
                          noop {s.noop_streak} (total {s.total_noops}, max {s.max_noop_streak})
                        </span>
                        <span>tools <span className="font-mono text-foreground">{s.tool_calls_total}</span></span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Card 2: Background Jobs — will job_queue（agno模式：handler注册/worker池/幂等键） */}
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <Sparkles size={14} className="text-amber-500" />
                <h4 className="text-xs font-medium">Background Jobs (will job_queue)</h4>
                {jobsHealth && Object.entries(jobsHealth.by_status).map(([status, count]) => (
                  <span key={status} className={cn('rounded px-1.5 py-0.5 text-[10px] font-medium', jobStatusColor(status))}>
                    {status}: {count}
                  </span>
                ))}
                {jobsHealth && (
                  <span className="text-[10px] text-muted-foreground ml-auto">
                    handlers: {jobsHealth.handlers.length > 0 ? jobsHealth.handlers.join(', ') : 'none registered'}
                  </span>
                )}
              </div>
              {jobsList.length === 0 ? (
                <div className="py-8 text-center text-muted-foreground/50">
                  <History className="w-8 h-8 mx-auto mb-1.5" />
                  <p className="text-xs">No background jobs yet</p>
                  <p className="text-[10px] mt-1">Submit via POST /api/will/jobs/submit or dream background=true</p>
                </div>
              ) : (
                <div className="divide-y divide-border max-h-72 overflow-y-auto">
                  {jobsList.map(j => (
                    <div key={j.id} className="py-2 first:pt-0 last:pb-0">
                      <div className="flex items-center gap-2">
                        <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-medium shrink-0', jobStatusColor(j.status))}>
                          {j.status}
                        </span>
                        <span className="text-xs font-medium truncate">{j.name}</span>
                        <span className="text-[10px] font-mono text-muted-foreground truncate hidden lg:inline">{j.id}</span>
                        <span className="text-[10px] text-muted-foreground ml-auto whitespace-nowrap flex items-center gap-1">
                          {j.retries > 0 && <span className="text-amber-500">retry {j.retries}</span>}
                          {j.duration_s > 0 && <span className="font-mono">{j.duration_s}s</span>}
                          <Clock size={9} /> {formatTimestamp(j.created_at, t)}
                        </span>
                      </div>
                      {j.status === 'failed' && j.error && (
                        <p className="text-[10px] text-red-400 mt-0.5 truncate">{j.error}</p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Card 3: Evolution Pipeline — P0-7自进化闭环状态（声明→审批→落盘→回滚+熔断） */}
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <Target size={14} className="text-purple-500" />
                <h4 className="text-xs font-medium">Evolution Pipeline (heredity)</h4>
                {evoStats ? (
                  <>
                    {evoStats.breaker?.open ? (
                      <span className="rounded px-1.5 py-0.5 text-[10px] font-medium text-red-500 bg-red-500/10">
                        breaker open: {evoStats.breaker.error_type} ×{evoStats.breaker.consecutive}
                      </span>
                    ) : (
                      <span className="rounded px-1.5 py-0.5 text-[10px] font-medium text-emerald-500 bg-emerald-500/10">
                        breaker closed
                      </span>
                    )}
                    <span className="text-[10px] text-muted-foreground ml-auto">
                      {evoStats.triggers} triggers · ledger {Object.values(evoStats.ledger_events || {}).reduce((a, b) => a + b, 0)} events
                    </span>
                  </>
                ) : (
                  <span className="text-[10px] text-muted-foreground ml-auto">unavailable</span>
                )}
              </div>
              {evoStats ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    {Object.entries(evoStats.proposals || {}).map(([status, count]) => (
                      <span
                        key={status}
                        className={cn(
                          'rounded px-1.5 py-0.5 text-[10px] font-medium',
                          status === 'applied' ? 'text-emerald-500 bg-emerald-500/10'
                            : status === 'rejected' || status === 'apply_failed' ? 'text-red-500 bg-red-500/10'
                            : status === 'pending' ? 'text-blue-500 bg-blue-500/10'
                            : 'text-muted-foreground bg-muted',
                        )}
                      >
                        {status}: {count}
                      </span>
                    ))}
                  </div>
                  <GaugeBar
                    value={evoStats.pending}
                    max={evoStats.max_pending || 20}
                    label={`Proposal Budget (pending ${evoStats.pending}/${evoStats.max_pending || 20})`}
                  />
                  {Object.keys(evoStats.ledger_events || {}).length > 0 && (
                    <div className="flex items-center gap-2 flex-wrap text-[10px] text-muted-foreground">
                      {Object.entries(evoStats.ledger_events).map(([ev, n]) => (
                        <span key={ev} className="font-mono">{ev}:{n}</span>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground/50 py-4 text-center">Evolution stats unavailable</p>
              )}
            </div>

            {/* Card 4: Model Chain Trace — gland链路trace（"哪个模型在干活/为什么失败"） */}
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <div className="flex items-center gap-2 mb-3">
                <Zap size={14} className="text-blue-500" />
                <h4 className="text-xs font-medium">Model Chain Trace (gland)</h4>
              </div>
              {chainTrace.length === 0 ? (
                <div className="py-6 text-center text-muted-foreground/50">
                  <Zap className="w-8 h-8 mx-auto mb-1.5" />
                  <p className="text-xs">No LLM chain trace recorded yet</p>
                  <p className="text-[10px] mt-1">Appears after the first gland chat/embed call</p>
                </div>
              ) : (
                <div className="divide-y divide-border">
                  {chainTrace.map((e, i) => (
                    <div key={i} className="py-2 first:pt-0 last:pb-0 flex items-center gap-2 text-xs">
                      <span
                        className={cn(
                          'rounded px-1.5 py-0.5 text-[10px] font-medium shrink-0',
                          e.error ? 'text-red-500 bg-red-500/10' : 'text-emerald-500 bg-emerald-500/10',
                        )}
                      >
                        {e.error ? 'fail' : 'ok'}
                      </span>
                      <span className="font-mono">{String(e.provider ?? '?')}</span>
                      <span className="text-muted-foreground truncate">{String(e.model ?? '')}</span>
                      {e.pass !== undefined && (
                        <span className="text-[10px] text-muted-foreground">pass {String(e.pass)}</span>
                      )}
                      {e.error && (
                        <span className="text-[10px] text-red-400 truncate ml-auto" title={String(e.error)}>
                          {String(e.error).slice(0, 100)}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Card 5: Context Attribution — claude-code SDKContextUsage（"上下文被什么吃掉了"逐项归因） */}
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <div className="flex items-center gap-2 mb-3">
                <Bot size={14} className="text-amber-500" />
                <h4 className="text-xs font-medium">Context Attribution (SDKContextUsage)</h4>
                {tokenAttr?.summary?.latest?.over_limit && (
                  <span className="text-[10px] text-amber-500 bg-amber-500/10 rounded px-1.5 py-0.5">
                    over_limit: {tokenAttr.summary.latest.over_limit.kind} +{tokenAttr.summary.latest.over_limit.tokens_over}
                  </span>
                )}
              </div>
              {!tokenAttr || (tokenAttr.total_records ?? 0) === 0 ? (
                <div className="py-6 text-center text-muted-foreground/50">
                  <Bot className="w-8 h-8 mx-auto mb-1.5" />
                  <p className="text-xs">No context attribution recorded yet</p>
                  <p className="text-[10px] mt-1">Appears after a chat task runs — per-item tokens (tools/memory/skills/system prompt)</p>
                </div>
              ) : (
                <div className="space-y-1.5">
                  {(tokenAttr.summary.top_consumers || []).slice(0, 8).map((c, i) => {
                    const consumers = tokenAttr?.summary?.top_consumers || [];
                    const maxTokens = consumers[0]?.tokens_total ?? consumers[0]?.tokens ?? 1;
                    const tokens = c.tokens_total ?? c.tokens ?? 0;
                    return (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className="rounded px-1.5 py-0.5 text-[10px] font-medium shrink-0 bg-muted text-muted-foreground">
                          {c.kind}
                        </span>
                        <span className="font-mono truncate max-w-[220px]" title={c.name}>{c.name}</span>
                        <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
                          <div
                            className="h-full bg-amber-500/70 rounded-full"
                            style={{ width: `${Math.max(3, Math.round((tokens / Math.max(1, maxTokens)) * 100))}%` }}
                          />
                        </div>
                        <span className="font-mono text-[10px] text-muted-foreground shrink-0">
                          {tokens} tok{c.times_seen ? ` ×${c.times_seen}` : ''}
                        </span>
                      </div>
                    );
                  })}
                  <div className="pt-1.5 text-[10px] text-muted-foreground border-t border-border mt-2">
                    records {tokenAttr.total_records} · avg {tokenAttr.summary.avg_total_tokens ?? 0} tok · max {tokenAttr.summary.max_total_tokens ?? 0} tok · over_limit {tokenAttr.summary.over_limit_records ?? 0}
                  </div>
                </div>
              )}
            </div>

            {/* Card 6: Wakeup Executor — ScheduleWakeup执行侧闭环（到点真醒）
                双实例CAS快照 + 活动流tail（"它刚才干了什么"）+ noop streak折叠UI */}
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <Clock size={14} className="text-cyan-500" />
                <h4 className="text-xs font-medium">Wakeup Executor (ScheduleWakeup)</h4>
                {wakeupOverview ? (
                  <>
                    <span
                      className={cn(
                        'rounded px-1.5 py-0.5 text-[10px] font-medium',
                        wakeupOverview.active_count > 0 ? 'text-emerald-500 bg-emerald-500/10' : 'text-muted-foreground bg-muted',
                      )}
                    >
                      {wakeupOverview.active_count} active / {wakeupOverview.total_keys} loops
                    </span>
                    {wakeupOverview.due_count > 0 && (
                      <span className="rounded px-1.5 py-0.5 text-[10px] font-medium text-amber-500 bg-amber-500/10">
                        due {wakeupOverview.due_count}
                      </span>
                    )}
                  </>
                ) : (
                  <span className="text-[10px] text-muted-foreground ml-auto">unavailable</span>
                )}
              </div>

              {/* 双实例executor快照（CAS互斥双跑防护：claim_lost=输给对端是正常路径） */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-3">
                {[
                  { label: 'proxy-a :8092', snap: wakeupExecA },
                  { label: 'proxy-b :8095', snap: wakeupExecB },
                ].map(({ label, snap }) => (
                  <div key={label} className="rounded-lg border border-border bg-muted/30 p-2.5">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span
                        className={cn(
                          'rounded px-1.5 py-0.5 text-[10px] font-medium',
                          snap?.running ? 'text-emerald-500 bg-emerald-500/10' : 'text-red-500 bg-red-500/10',
                        )}
                      >
                        {snap?.running ? 'running' : 'stopped'}
                      </span>
                      <span className="text-[10px] font-mono text-muted-foreground">{label}</span>
                      {snap && (
                        <span className="text-[10px] text-muted-foreground ml-auto font-mono">polls {snap.polls}</span>
                      )}
                    </div>
                    {snap ? (
                      <>
                        <div className="flex items-center gap-2 flex-wrap text-[10px] text-muted-foreground">
                          <span>claimed <span className="font-mono text-foreground">{snap.claimed}</span></span>
                          <span title="CAS输给对端实例（at-most-once正常路径）">lost <span className="font-mono text-foreground">{snap.claim_lost}</span></span>
                          <span>fire ok <span className="font-mono text-emerald-500">{snap.fire_ok}</span></span>
                          <span>failed <span className={cn('font-mono', snap.fire_failed > 0 ? 'text-red-500' : 'text-foreground')}>{snap.fire_failed}</span></span>
                          {(snap.claim_errors + snap.ack_errors) > 0 && (
                            <span className="text-red-400">errors {snap.claim_errors + snap.ack_errors}</span>
                          )}
                        </div>
                        {snap.last_error ? (
                          <p className="text-[10px] text-red-400 mt-1 truncate" title={snap.last_error}>
                            {snap.last_error_at ? `[${formatTimestamp(snap.last_error_at, t)}] ` : ''}
                            {snap.last_error}
                          </p>
                        ) : (
                          <p className="text-[10px] text-muted-foreground/50 mt-1">no recent errors</p>
                        )}
                      </>
                    ) : (
                      <p className="text-[10px] text-muted-foreground/50">unreachable</p>
                    )}
                  </div>
                ))}
              </div>

              {/* 活动流tail（类tail日志流：claim/fire/ack每个动作一条，最新在前） */}
              <div className="mb-3">
                <div className="flex items-center gap-2 mb-1.5">
                  <History size={11} className="text-muted-foreground" />
                  <span className="text-[10px] text-muted-foreground">Recent Activity</span>
                </div>
                {wakeupActivityFeed.length === 0 ? (
                  <p className="text-[10px] text-muted-foreground/50">no executor activity yet</p>
                ) : (
                  <div className="space-y-0.5 max-h-32 overflow-y-auto font-mono">
                    {wakeupActivityFeed.map((a, i) => (
                      <div key={`${a.src}-${a.ts}-${i}`} className="flex items-center gap-1.5 text-[10px]">
                        <span className="text-muted-foreground/60 shrink-0">{formatTimestamp(a.ts, t)}</span>
                        <span className={cn('rounded px-1 py-0.5 font-medium shrink-0', wakeupActivityColor(a.kind))}>
                          {a.kind}
                        </span>
                        {a.event_id != null && <span className="text-muted-foreground shrink-0">#{a.event_id}</span>}
                        <span className="text-muted-foreground truncate" title={a.detail}>{a.detail}</span>
                        <span className="text-muted-foreground/40 ml-auto shrink-0">{a.src}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* 唤醒循环列表（全部键一屏可见）+ 展开noop折叠视图（claude-code streak折叠） */}
              {!wakeupOverview || wakeupOverview.loops.length === 0 ? (
                <div className="py-6 text-center text-muted-foreground/50">
                  <Clock className="w-8 h-8 mx-auto mb-1.5" />
                  <p className="text-xs">No wakeup loops scheduled</p>
                  <p className="text-[10px] mt-1">Agent calls schedule_wakeup to start a loop (claude-code ScheduleWakeup)</p>
                </div>
              ) : (
                <div className="divide-y divide-border max-h-72 overflow-y-auto">
                  {wakeupOverview.loops.map(loop => {
                    const key = `${loop.agent_id}/${loop.session_id}`;
                    const expanded = expandedWakeup === key;
                    return (
                      <div key={key} className="py-2 first:pt-0 last:pb-0">
                        <button
                          type="button"
                          className="w-full text-left group"
                          onClick={() => toggleWakeupLoop(loop.agent_id, loop.session_id)}
                        >
                          <div className="flex items-center gap-2 mb-1">
                            {loop.fire.due_now ? (
                              <span className="rounded px-1.5 py-0.5 text-[10px] font-medium text-amber-500 bg-amber-500/10">due now</span>
                            ) : loop.active ? (
                              <span className="rounded px-1.5 py-0.5 text-[10px] font-medium text-emerald-500 bg-emerald-500/10">active</span>
                            ) : (
                              <span className="rounded px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground bg-muted">stopped</span>
                            )}
                            {loop.fire.exhausted > 0 && (
                              <span className="rounded px-1.5 py-0.5 text-[10px] font-medium text-red-500 bg-red-500/10">exhausted</span>
                            )}
                            <span className="text-xs font-mono truncate flex-1">{key}</span>
                            {expanded ? <ChevronUp size={12} className="text-muted-foreground shrink-0" /> : <ChevronDown size={12} className="text-muted-foreground shrink-0" />}
                          </div>
                          <div className="flex items-center gap-3 flex-wrap text-[10px] text-muted-foreground">
                            <span className={cn('font-mono', loop.current_streak >= 3 ? 'text-amber-500' : 'text-foreground')}>
                              noop streak {loop.current_streak}
                            </span>
                            <span>events <span className="font-mono text-foreground">{loop.total_events}</span></span>
                            <span>
                              countdown{' '}
                              <span className="font-mono text-foreground">
                                {loop.countdown_s == null ? '—' : loop.countdown_s > 0 ? formatUptime(Math.round(loop.countdown_s)) : 'overdue'}
                              </span>
                            </span>
                            <span>fire <span className="font-mono text-foreground">{loop.fire.fired}</span></span>
                            {loop.last_event?.reason && (
                              <span className="truncate max-w-[220px]" title={loop.last_event.reason}>
                                {loop.last_event.noop ? 'noop: ' : ''}{loop.last_event.reason}
                              </span>
                            )}
                          </div>
                        </button>
                        {expanded && (
                          <div className="mt-2 ml-2 border-l-2 border-border pl-2.5 space-y-1">
                            {(wakeupFolded[key] || []).length === 0 ? (
                              <p className="text-[10px] text-muted-foreground/50">no events</p>
                            ) : (
                              (wakeupFolded[key] || []).map((item, i) =>
                                item.kind === 'noop_streak' ? (
                                  <div key={i} className="rounded bg-amber-500/10 border border-amber-500/20 px-2 py-1">
                                    <div className="text-[10px] font-medium text-amber-500">
                                      ×{item.count} noop streak (max {item.streak_max})
                                    </div>
                                    <div className="text-[10px] text-muted-foreground truncate" title={(item.reasons || []).join(' / ')}>
                                      {item.first_reason} → {item.last_reason}
                                    </div>
                                  </div>
                                ) : (
                                  <div key={i} className="flex items-center gap-2 text-[10px]">
                                    <span
                                      className={cn(
                                        'rounded px-1 py-0.5 font-medium shrink-0',
                                        item.noop ? 'text-amber-500 bg-amber-500/10' : 'text-emerald-500 bg-emerald-500/10',
                                      )}
                                    >
                                      {item.noop ? `noop${item.streak ? ` ×${item.streak}` : ''}` : 'progress'}
                                    </span>
                                    <span className="truncate text-muted-foreground" title={item.reason}>{item.reason}</span>
                                  </div>
                                ),
                              )
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Architecture Tab ──────────────────────────────── */}
        {activeTab === 'architecture' && (
          <div className="h-full overflow-hidden">
            <ArchitectureMonitor />
          </div>
        )}
      </div>
    </PageLayout>
  );
}
