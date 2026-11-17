"use client";
import { useState, useEffect, useCallback } from "react";
import { cn } from "@/lib/utils";
import {
  Heart, RefreshCw, Activity, Clock, Timer, ListChecks, Trash2, AlertTriangle,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

/* ❤️Pulse 心跳层观察面板 — 对接 acp-proxy /api/pulse/*（只读观察面）
 * 数据源：agent.pulse.PulseLoop.status()/recent_beats()（进程级心跳宿主）
 * 展示：心跳计数 / 心跳漂移 / 自检清单（五类目）/ 僵尸回收计数 / 心跳流 */

const API_BASE =
  typeof window !== "undefined"
    ? `http://${window.location.hostname}:8092`
    : "http://127.0.0.1:8092";

interface PulseCheckItem {
  name: string;
  category: string;
  runs: number;
  fail_streak: number;
  ok?: boolean;
  detail?: string;
  severity?: string;
  slow?: boolean;
  ts?: number;
}

interface PulseTaskItem {
  task_id: string;
  name: string;
  kind: string;
  status: string;
  steps_done: number;
  deferred: number;
  recoveries: number;
  errors: number;
  last_error: string;
}

interface PulseStatus {
  running: boolean;
  beats: number;
  missed_beats: number;
  beat_errors: number;
  last_beat_error: string;
  last_beat_at: number;
  interval_s: number;
  avg_drift_ms: number;
  max_drift_ms: number;
  checks: PulseCheckItem[];
  tasks: { total: number; by_status: Record<string, number>; items: PulseTaskItem[] };
  reaped_total: number;
  wakes_total: number;
  deferred_total: number;
}

interface BeatReport {
  beat: number;
  ts: number;
  checks: Array<{ check: string; ok: boolean; detail: string; severity: string }>;
  findings: Array<{ check: string; detail: string; severity: string }>;
  tasks_advanced: string[];
  deferred: number;
  overdue: string[];
  zombies_reaped: Array<{ pid: number; reaped: boolean }>;
  zombies_refused: Array<{ pid: number; detail: string }>;
  wakes: Array<{ session_id: string; reason: string }>;
  phase_errors: string[];
  over_budget: boolean;
  elapsed_ms: number;
}

function formatTime(ts: number): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    fractionalSecondDigits: 3,
  });
}

function formatMs(ms?: number): string {
  if (ms === undefined || ms === null) return "-";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${ms.toFixed(2)}ms`;
}

const CATEGORY_LABELS: Record<string, string> = {
  backend_service: "后端服务",
  connection_state: "连接状态",
  task_queue: "任务队列",
  work_order: "未完成工单",
  memory_consistency: "记忆一致性",
};

export function PulseClient() {
  const [status, setStatus] = useState<PulseStatus | null>(null);
  const [checks, setChecks] = useState<PulseCheckItem[]>([]);
  const [beats, setBeats] = useState<BeatReport[]>([]);

  const fetchAll = useCallback(async () => {
    try {
      const [sRes, cRes, bRes] = await Promise.all([
        fetch(`${API_BASE}/api/pulse/status`),
        fetch(`${API_BASE}/api/pulse/checks`),
        fetch(`${API_BASE}/api/pulse/beats?limit=20`),
      ]);
      const s = await sRes.json();
      const c = await cRes.json();
      const b = await bRes.json();
      if (s && typeof s.beats === "number") setStatus(s);
      if (Array.isArray(c.checks)) setChecks(c.checks);
      if (Array.isArray(b.beats)) setBeats([...b.beats].reverse()); // 最新在前
    } catch {}
  }, []);

  useEffect(() => {
    fetchAll();
    const timer = setInterval(fetchAll, 5000);
    return () => clearInterval(timer);
  }, [fetchAll]);

  const failedChecks = checks.filter(c => c.ok === false).length;
  const lastBeat = beats[0];

  return (
    <PageLayout title="脉搏">
      <div className="flex h-full flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
          <div className="flex items-center gap-3">
            <Heart size={20} className="text-red-500 animate-pulse" />
            <h1 className="text-lg font-semibold">Pulse · 心跳层观察面</h1>
            <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-500">
              定时心跳 · 周期自检 · 僵尸回收 · 软预算禁硬杀
            </span>
            {status && (
              <span className={cn("rounded-full px-2 py-0.5 text-[10px]",
                status.running ? "text-emerald-500 bg-emerald-500/10" : "text-muted-foreground bg-muted")}>
                {status.running ? "心跳运行中" : "心跳未运行"}
              </span>
            )}
          </div>
          <button onClick={fetchAll}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted transition-colors">
            <RefreshCw size={14} /> 刷新
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 lg:p-6 space-y-3 lg:space-y-6">
          {/* Stats */}
          {status && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
              <StatCard icon={Heart} label="心跳计数" value={String(status.beats)}
                sub={`节拍 ${formatMs(status.interval_s * 1000)} · 最后一拍 ${formatTime(status.last_beat_at)}`}
                color="text-red-500" bg="bg-red-500/10" />
              <StatCard icon={Activity} label="心跳漂移（平均）" value={formatMs(status.avg_drift_ms)}
                sub={`最大 ${formatMs(status.max_drift_ms)} · 漏拍 ${status.missed_beats} · 拍错 ${status.beat_errors}`}
                color="text-blue-500" bg="bg-blue-500/10" />
              <StatCard icon={Trash2} label="僵尸回收计数" value={String(status.reaped_total)}
                sub={`只收尸体 · 唤醒 ${status.wakes_total} · 顺延 ${status.deferred_total}`}
                color="text-violet-500" bg="bg-violet-500/10" />
              <StatCard icon={ListChecks} label="自检清单" value={failedChecks > 0 ? `${failedChecks} 项异常` : "全部通过"}
                sub={`五类目 · 最近一拍 #${lastBeat ? lastBeat.beat : "-"} · 发现 ${lastBeat ? lastBeat.findings.length : 0}`}
                color={failedChecks > 0 ? "text-yellow-500" : "text-emerald-500"}
                bg={failedChecks > 0 ? "bg-yellow-500/10" : "bg-emerald-500/10"} />
            </div>
          )}

          {/* 自检清单（五类目） */}
          <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
            <h3 className="text-xs lg:text-sm font-medium flex items-center gap-2">
              <ListChecks size={14} className="text-red-500" /> 周期自检 · 五类目
            </h3>
            {checks.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
                <ListChecks size={32} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">等待第一拍自检结果…</p>
              </div>
            ) : (
              <div className="space-y-2">
                {checks.map((c) => (
                  <div key={c.name} className="rounded-lg border border-border p-2.5 lg:p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        {c.ok === false
                          ? <AlertTriangle size={14} className="text-yellow-500 shrink-0" />
                          : <ListChecks size={14} className="text-emerald-500 shrink-0" />}
                        <span className="text-xs lg:text-sm font-medium">{c.name}</span>
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground shrink-0">
                          {CATEGORY_LABELS[c.category] || c.category}
                        </span>
                        {c.severity === "critical" && c.ok === false && (
                          <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] text-red-500 shrink-0">critical</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 lg:gap-3 text-[10px] text-muted-foreground shrink-0">
                        {c.slow && <span className="text-yellow-500">slow</span>}
                        <span>运行 {c.runs}</span>
                        {c.fail_streak > 0 && (
                          <span className="text-red-500">连续失败 {c.fail_streak}</span>
                        )}
                        <span className={cn("rounded-full px-2 py-0.5",
                          c.ok === false ? "text-red-500 bg-red-500/10"
                            : c.ok === true ? "text-emerald-500 bg-emerald-500/10"
                              : "text-muted-foreground bg-muted")}>
                          {c.ok === false ? "FAIL" : c.ok === true ? "OK" : "待检"}
                        </span>
                      </div>
                    </div>
                    {c.detail && (
                      <p className="mt-1.5 text-xs text-muted-foreground font-mono break-all">{c.detail}</p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 心跳流 */}
          <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-xs lg:text-sm font-medium flex items-center gap-2">
                <Timer size={14} className="text-red-500" /> 心跳流（每拍六段）
              </h3>
              <span className="text-xs text-muted-foreground">{beats.length} 拍展示 / 共 {status?.beats ?? 0} 拍</span>
            </div>
            {beats.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <Heart size={36} className="mb-3 opacity-30 animate-pulse" />
                <p className="text-xs lg:text-sm">等待心跳…</p>
              </div>
            ) : (
              <div className="rounded-lg border border-border overflow-x-auto">
                <table className="w-full text-xs lg:text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/50">
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">拍号</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">时间</th>
                      <th className="px-2 lg:px-4 py-2 text-right text-xs font-medium text-muted-foreground">耗时</th>
                      <th className="px-2 lg:px-4 py-2 text-right text-xs font-medium text-muted-foreground">自检通过</th>
                      <th className="px-2 lg:px-4 py-2 text-right text-xs font-medium text-muted-foreground">发现</th>
                      <th className="px-2 lg:px-4 py-2 text-right text-xs font-medium text-muted-foreground">工单推进</th>
                      <th className="px-2 lg:px-4 py-2 text-right text-xs font-medium text-muted-foreground">顺延</th>
                      <th className="px-2 lg:px-4 py-2 text-right text-xs font-medium text-muted-foreground">僵尸回收</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">备注</th>
                    </tr>
                  </thead>
                  <tbody>
                    {beats.map((b) => {
                      const okCount = (b.checks || []).filter(c => c.ok).length;
                      const notes: string[] = [];
                      if (b.over_budget) notes.push("软预算超支");
                      if ((b.phase_errors || []).length) notes.push(`段错 ${b.phase_errors.length}`);
                      if ((b.zombies_refused || []).length) notes.push(`拒收 ${b.zombies_refused.length}`);
                      if ((b.wakes || []).length) notes.push(`唤醒 ${b.wakes.length}`);
                      return (
                        <tr key={b.beat} className="border-b border-border last:border-0 hover:bg-muted/30">
                          <td className="px-2 lg:px-4 py-2 font-mono text-xs">#{b.beat}</td>
                          <td className="px-2 lg:px-4 py-2 text-xs font-mono text-muted-foreground">{formatTime(b.ts)}</td>
                          <td className={cn("px-2 lg:px-4 py-2 text-right text-xs font-mono",
                            b.over_budget ? "text-yellow-500" : "text-emerald-500")}>
                            {formatMs(b.elapsed_ms)}
                          </td>
                          <td className="px-2 lg:px-4 py-2 text-right text-xs">
                            {okCount}/{(b.checks || []).length}
                          </td>
                          <td className={cn("px-2 lg:px-4 py-2 text-right text-xs",
                            (b.findings || []).length > 0 ? "text-red-500" : "text-muted-foreground")}>
                            {(b.findings || []).length}
                          </td>
                          <td className="px-2 lg:px-4 py-2 text-right text-xs">{(b.tasks_advanced || []).length}</td>
                          <td className="px-2 lg:px-4 py-2 text-right text-xs">{b.deferred || 0}</td>
                          <td className="px-2 lg:px-4 py-2 text-right text-xs">{(b.zombies_reaped || []).length}</td>
                          <td className="px-2 lg:px-4 py-2 text-xs text-muted-foreground">
                            {notes.join(" · ") || "-"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* 长任务工单账 */}
          {status && status.tasks.total > 0 && (
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
              <h3 className="text-xs lg:text-sm font-medium flex items-center gap-2">
                <Clock size={14} className="text-red-500" /> 长任务工单（软预算禁硬杀）
              </h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(status.tasks.by_status).map(([st, n]) => (
                  <span key={st} className="rounded-full bg-red-500/10 px-3 py-1 text-xs text-red-600">
                    {st}: {n}
                  </span>
                ))}
              </div>
              <div className="space-y-2">
                {status.tasks.items.map((t) => (
                  <div key={t.task_id} className="rounded-lg border border-border p-2.5 lg:p-3 flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <span className="text-xs lg:text-sm font-medium">{t.name || t.task_id}</span>
                      <span className="ml-2 rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">{t.kind}</span>
                      <p className="mt-1 text-xs text-muted-foreground">
                        步进 {t.steps_done} · 顺延 {t.deferred} · 恢复 {t.recoveries} · 错误 {t.errors}
                        {t.last_error && <span className="text-red-500"> · {t.last_error}</span>}
                      </p>
                    </div>
                    <span className={cn("rounded-full px-2 py-0.5 text-[10px] shrink-0",
                      t.status === "active" ? "text-emerald-500 bg-emerald-500/10"
                        : t.status === "needs_attention" ? "text-red-500 bg-red-500/10"
                          : "text-yellow-500 bg-yellow-500/10")}>
                      {t.status}
                    </span>
                  </div>
                ))}
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
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <div className={cn("rounded-lg p-1.5", bg)}><Icon size={14} className={color} /></div>
      </div>
      <p className="text-xl lg:text-2xl font-bold">{value}</p>
      <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>
    </div>
  );
}
