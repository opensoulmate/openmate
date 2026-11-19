"use client";
import { useState, useEffect, useCallback } from "react";
import { cn } from "@/lib/utils";
import {
  Zap, RefreshCw, Activity, ShieldAlert, Timer, BarChart3, Radio,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';
import { useTranslation } from 'react-i18next';

/* ⚡Reflex 反射层观察面板 — 对接 acp-proxy /api/reflex/*（只读观察面）
 * 数据源：agent.reflex.ReflexHub.stats()/recent()（/ws_acp 双向事件源桥同源）
 * 展示：事件延迟 / 熔断状态 / 事件流（实时轮询） */

const API_BASE =
  typeof window !== "undefined"
    ? `http://${window.location.hostname}:8092`
    : "http://127.0.0.1:8092";

interface ReflexRuleStat {
  name: string;
  action: string;
  enabled: boolean;
  priority: number;
  calls: number;
  failures: number;
  circuit: string;
  circuit_trips: number;
}

interface ReflexStats {
  total: number;
  by_kind: Record<string, number>;
  by_verdict: Record<string, number>;
  callbacks: number;
  routed: number;
  blocked: number;
  dropped: number;
  dropped_by_kind: Record<string, number>;
  error_storms: number;
  avg_latency_ms: number;
  max_latency_ms: number;
  rules: ReflexRuleStat[];
}

interface ReflexEventRow {
  event_id: string;
  kind: string;
  verdict: string;
  reason?: string;
  dropped?: boolean;
  latency_ms?: number;
  text?: string;
  source?: string;
  ts: number;
  actions?: Array<{ rule: string; action: string; ok: boolean; circuit?: string }>;
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
  return `${ms.toFixed(3)}ms`;
}

const VERDICT_COLORS: Record<string, string> = {
  handled: "text-emerald-500 bg-emerald-500/10",
  routed: "text-blue-500 bg-blue-500/10",
  blocked: "text-red-500 bg-red-500/10",
  dropped: "text-yellow-500 bg-yellow-500/10",
  pass: "text-muted-foreground bg-muted",
  circuit: "text-orange-500 bg-orange-500/10",
};

const CIRCUIT_COLORS: Record<string, string> = {
  closed: "text-emerald-500 bg-emerald-500/10",
  open: "text-red-500 bg-red-500/10",
  half_open: "text-yellow-500 bg-yellow-500/10",
};

export function ReflexClient() {
  const { t } = useTranslation();
  const [stats, setStats] = useState<ReflexStats | null>(null);
  const [events, setEvents] = useState<ReflexEventRow[]>([]);
  const [eventTotal, setEventTotal] = useState(0);

  const fetchAll = useCallback(async () => {
    try {
      const [sRes, eRes] = await Promise.all([
        fetch(`${API_BASE}/api/reflex/stats`),
        fetch(`${API_BASE}/api/reflex/events?limit=50`),
      ]);
      const s = await sRes.json();
      const e = await eRes.json();
      if (s && typeof s.total === "number") setStats(s);
      if (Array.isArray(e.events)) {
        setEvents([...e.events].reverse()); // 最新在前
        setEventTotal(e.total ?? e.events.length);
      }
    } catch {}
  }, []);

  useEffect(() => {
    fetchAll();
    const timer = setInterval(fetchAll, 3000);
    return () => clearInterval(timer);
  }, [fetchAll]);

  const openCircuits = (stats?.rules || []).filter(r => r.circuit === "open").length;

  return (
    <PageLayout title="反射">
      <div className="flex h-full flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
          <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-yellow-500/10">
              <Zap size={20} className="text-yellow-500" />
            </div>
            <div className="min-w-0">
              <h1 className="text-lg lg:text-xl font-semibold truncate">Reflex · 反射层观察面</h1>
              <p className="text-xs lg:text-sm text-muted-foreground truncate">{t('organTag.reflex')}</p>
            </div>
          </div>
          <button onClick={fetchAll}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted transition-colors">
            <RefreshCw size={14} /> 刷新
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4 lg:py-6 space-y-3 lg:space-y-6">
          {/* Stats */}
          {stats && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
              <StatCard icon={Zap} label="事件总数" value={String(stats.total)}
                sub={`handled ${stats.callbacks} · routed ${stats.routed} · blocked ${stats.blocked}`}
                color="text-yellow-500" bg="bg-yellow-500/10" />
              <StatCard icon={Timer} label="事件延迟（平均）" value={formatMs(stats.avg_latency_ms)}
                sub={`最大 ${formatMs(stats.max_latency_ms)}`}
                color="text-blue-500" bg="bg-blue-500/10" />
              <StatCard icon={ShieldAlert} label="熔断状态" value={openCircuits > 0 ? `${openCircuits} 开路` : "全部闭合"}
                sub={`规则 ${stats.rules.length} · 开路次数 ${stats.rules.reduce((a, r) => a + r.circuit_trips, 0)} · 报错风暴 ${stats.error_storms}`}
                color={openCircuits > 0 ? "text-red-500" : "text-emerald-500"}
                bg={openCircuits > 0 ? "bg-red-500/10" : "bg-emerald-500/10"} />
              <StatCard icon={Activity} label="风暴护栏丢弃" value={String(stats.dropped)}
                sub={Object.entries(stats.dropped_by_kind).map(([k, v]) => `${k}:${v}`).join(" ") || "无丢弃"}
                color="text-violet-500" bg="bg-violet-500/10" />
            </div>
          )}

          {/* 熔断状态 · 规则表 */}
          {stats && stats.rules.length > 0 && (
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-3">
              <h3 className="text-xs lg:text-sm font-medium flex items-center gap-2">
                <ShieldAlert size={14} className="text-yellow-500" /> 熔断状态 · 规则
              </h3>
              <div className="rounded-lg border border-border overflow-x-auto">
                <table className="w-full text-xs lg:text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/50">
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">规则</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">动作</th>
                      <th className="px-2 lg:px-4 py-2 text-right text-xs font-medium text-muted-foreground">优先级</th>
                      <th className="px-2 lg:px-4 py-2 text-right text-xs font-medium text-muted-foreground">调用</th>
                      <th className="px-2 lg:px-4 py-2 text-right text-xs font-medium text-muted-foreground">失败</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">熔断</th>
                      <th className="px-2 lg:px-4 py-2 text-right text-xs font-medium text-muted-foreground">开路次数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.rules.map((r) => (
                      <tr key={r.name} className="border-b border-border last:border-0 hover:bg-muted/30">
                        <td className="px-2 lg:px-4 py-2 font-mono text-xs">
                          {r.name}
                          {!r.enabled && <span className="ml-2 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">停用</span>}
                        </td>
                        <td className="px-2 lg:px-4 py-2 text-xs">{r.action}</td>
                        <td className="px-2 lg:px-4 py-2 text-right text-xs">{r.priority}</td>
                        <td className="px-2 lg:px-4 py-2 text-right text-xs">{r.calls}</td>
                        <td className={cn("px-2 lg:px-4 py-2 text-right text-xs", r.failures > 0 ? "text-red-500" : "text-muted-foreground")}>{r.failures}</td>
                        <td className="px-2 lg:px-4 py-2">
                          <span className={cn("rounded-full px-2 py-0.5 text-[10px]", CIRCUIT_COLORS[r.circuit] || "bg-muted")}>
                            {r.circuit === "open" ? "开路" : r.circuit === "half_open" ? "半开" : "闭合"}
                          </span>
                        </td>
                        <td className="px-2 lg:px-4 py-2 text-right text-xs">{r.circuit_trips}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* 事件流 */}
          <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-xs lg:text-sm font-medium flex items-center gap-2">
                <Radio size={14} className="text-yellow-500" /> 事件流（实时）
              </h3>
              <span className="text-xs text-muted-foreground">
                {events.length} 条展示 / 共 {eventTotal} 条
              </span>
            </div>
            {events.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <Radio size={36} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">暂无事件——通过 /ws_acp 发送真实信号即刻上屏</p>
              </div>
            ) : (
              <div className="rounded-lg border border-border overflow-x-auto">
                <table className="w-full text-xs lg:text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/50">
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">时间</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">事件</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">终态</th>
                      <th className="px-2 lg:px-4 py-2 text-right text-xs font-medium text-muted-foreground">延迟</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">来源</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">预览</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.map((e) => (
                      <tr key={e.event_id} className="border-b border-border last:border-0 hover:bg-muted/30">
                        <td className="px-2 lg:px-4 py-2 text-xs font-mono text-muted-foreground">{formatTime(e.ts)}</td>
                        <td className="px-2 lg:px-4 py-2">
                          <span className="rounded-full bg-yellow-500/10 px-2 py-0.5 text-[10px] text-yellow-600 font-mono">{e.kind}</span>
                        </td>
                        <td className="px-2 lg:px-4 py-2">
                          <span className={cn("rounded-full px-2 py-0.5 text-[10px]", VERDICT_COLORS[e.verdict] || "bg-muted")}>
                            {e.verdict}
                          </span>
                        </td>
                        <td className={cn("px-2 lg:px-4 py-2 text-right text-xs font-mono",
                          (e.latency_ms ?? 0) > 5 ? "text-yellow-500" : "text-emerald-500")}>
                          {formatMs(e.latency_ms)}
                        </td>
                        <td className="px-2 lg:px-4 py-2 text-xs text-muted-foreground">{e.source || "-"}</td>
                        <td className="px-2 lg:px-4 py-2 text-xs text-muted-foreground max-w-[280px] truncate"
                          title={e.text || e.reason || ""}>
                          {e.text || e.reason || "-"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* 事件分类计数 */}
          {stats && Object.keys(stats.by_kind).length > 0 && (
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-3">
              <h3 className="text-xs lg:text-sm font-medium flex items-center gap-2">
                <BarChart3 size={14} className="text-yellow-500" /> 按事件类目
              </h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(stats.by_kind).map(([kind, count]) => (
                  <span key={kind} className="rounded-full bg-yellow-500/10 px-3 py-1 text-xs text-yellow-600">
                    {kind}: {count}
                  </span>
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
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <div className={cn("rounded-lg p-1.5", bg)}><Icon size={14} className={color} /></div>
      </div>
      <p className="text-xl lg:text-2xl font-bold">{value}</p>
      <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>
    </div>
  );
}
