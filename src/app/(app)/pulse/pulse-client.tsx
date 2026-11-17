"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  Heart, RefreshCw, Plus, Trash2, Play, Pause,
  Activity, Settings, Clock, Zap, BarChart3,
  Loader2, ChevronRight, Timer,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface PulseSignal {
  signal_id: string;
  name: string;
  signal_type: string;
  interval_ms: number;
  status: string;
  fire_count: number;
  max_fires: number;
  last_fired_at: number | null;
  next_fire_at: number | null;
  drift_correction: number;
  created_at: number;
}

interface TickRecord {
  tick_id: string;
  signal_id: string;
  timestamp: number;
  expected_at: number;
  drift_ms: number;
  latency_ms: number;
}

interface PulseStats {
  uptime_seconds: number;
  total_ticks: number;
  total_signals: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  precision: {
    avg_drift_ms: number;
    max_drift_ms: number;
    sample_size: number;
  };
}

function formatTime(ts: number): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    fractionalSecondDigits: 3 });
}

function formatMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms >= 1) return `${ms.toFixed(1)}ms`;
  return `${(ms * 1000).toFixed(0)}μs`;
}

const STATUS_COLORS: Record<string, string> = {
  active: "text-emerald-500 bg-emerald-500/10",
  paused: "text-yellow-500 bg-yellow-500/10",
  completed: "text-blue-500 bg-blue-500/10",
  cancelled: "text-red-500 bg-red-500/10",
};

export function PulseClient() {
  const { t } = useTranslation();
  const TYPE_LABELS: Record<string, string> = {
    tick: t('pulse.t16785'),
    interval: t('pulse.t55782'),
    cron: t("pulse.cron"),
    once: t('pulse.t10485'),
  };
  const [tab, setTab] = useState<"signals" | "ticks" | "config">("signals");
  const [stats, setStats] = useState<PulseStats | null>(null);
  const [signals, setSignals] = useState<PulseSignal[]>([]);
  const [ticks, setTicks] = useState<TickRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedSignal, setSelectedSignal] = useState<string | null>(null);

  // Create form
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState("interval");
  const [newInterval, setNewInterval] = useState("1000");
  const [newMaxFires, setNewMaxFires] = useState("0");

  const apiBase = getApiBaseUrl();

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/pulse/health`);
      setStats(await res.json());
    } catch {}
  }, [apiBase]);

  const fetchSignals = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/pulse/signals`);
      const data = await res.json();
      setSignals(data.signals || []);
    } catch {}
  }, [apiBase]);

  const fetchTicks = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (selectedSignal) params.set("signal_id", selectedSignal);
      params.set("limit", "100");
      const res = await fetch(`${apiBase}/api/pulse/ticks?${params}`);
      const data = await res.json();
      setTicks(data.ticks || []);
    } catch {}
  }, [apiBase, selectedSignal]);

  useEffect(() => { fetchStats(); }, [fetchStats]);
  useEffect(() => { if (tab === "signals") fetchSignals(); }, [tab, fetchSignals]);
  useEffect(() => { if (tab === "ticks") fetchTicks(); }, [tab, fetchTicks]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setLoading(true);
    try {
      await fetch(`${apiBase}/api/pulse/signals`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newName, signal_type: newType,
          interval_ms: parseFloat(newInterval), max_fires: parseInt(newMaxFires) || 0,
        }),
      });
      setNewName(""); setNewInterval("1000"); setNewMaxFires("0");
      fetchSignals(); fetchStats();
    } catch {} finally { setLoading(false); }
  };

  const handleTick = async (id: string) => {
    try {
      await fetch(`${apiBase}/api/pulse/signals/${id}/tick`, { method: "POST" });
      fetchSignals(); fetchStats(); if (tab === "ticks") fetchTicks();
    } catch {}
  };

  const handlePause = async (id: string) => {
    try {
      await fetch(`${apiBase}/api/pulse/signals/${id}/pause`, { method: "POST" });
      fetchSignals();
    } catch {}
  };

  const handleResume = async (id: string) => {
    try {
      await fetch(`${apiBase}/api/pulse/signals/${id}/resume`, { method: "POST" });
      fetchSignals();
    } catch {}
  };

  const handleDelete = async (id: string) => {
    try {
      await fetch(`${apiBase}/api/pulse/signals/${id}`, { method: "DELETE" });
      fetchSignals(); fetchStats();
    } catch {}
  };

  const tabs = [
    { id: "signals" as const, label: t("pulse.signals") || t('pulse.text4'), icon: Heart },
    { id: "ticks" as const, label: t("pulse.ticks") || t('pulse.text5'), icon: Timer },
    { id: "config" as const, label: t("pulse.stats") || t('pulse.precisionStats'), icon: BarChart3 },
  ];

  return (
      <PageLayout title="脉搏">
        
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-3">
          <Heart size={20} className="text-red-500 animate-pulse" />
          <h1 className="text-lg font-semibold">{t("pulse.title") || t('pulse.text7')}</h1>
          <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-500">
            {t("pulse.subtitle") || t('pulse.text8')}
          </span>
        </div>
        <button onClick={() => { fetchStats(); tab === "signals" && fetchSignals(); tab === "ticks" && fetchTicks(); }}
          className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted transition-colors">
          <RefreshCw size={14} /> {t("common.refresh") || t('pulse.refresh')}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 lg:p-6 space-y-3 lg:space-y-6">
        {/* Stats */}
        {stats && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
            <StatCard icon={Heart} label={t("pulse.signals") || t('pulse.text9')} value={String(stats.total_signals)}
              sub={`${stats.by_status.active || 0} ${t("pulse.active") || t('pulse.active')}`} color="text-red-500" bg="bg-red-500/10" />
            <StatCard icon={Zap} label={t("pulse.totalTicks") || t('pulse.text11')} value={String(stats.total_ticks)}
              sub={t("pulse.fired") || t('pulse.fired')} color="text-yellow-500" bg="bg-yellow-500/10" />
            <StatCard icon={Activity} label={t("pulse.avgDrift") || t('pulse.avgDrift')} value={formatMs(stats.precision.avg_drift_ms)}
              sub={`${stats.precision.sample_size} ${t("pulse.samples") || t('pulse.text14')}`} color="text-blue-500" bg="bg-blue-500/10" />
            <StatCard icon={Clock} label={t("pulse.uptime") || t('pulse.uptime')} value={`${Math.round(stats.uptime_seconds)}s`}
              sub={t("pulse.engineUptime") || t('pulse.text15')} color="text-emerald-500" bg="bg-emerald-500/10" />
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-2">
          {tabs.map((tabItem) => (
            <button key={tabItem.id} onClick={() => setTab(tabItem.id)}
              className={cn("flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs lg:text-sm transition-colors",
                tab === tabItem.id ? "bg-red-500/10 text-red-600 font-medium" : "hover:bg-muted text-muted-foreground")}>
              <tabItem.icon size={14} /> {tabItem.label}
            </button>
          ))}
        </div>

        {/* Signals Tab */}
        {tab === "signals" && (
          <div className="space-y-4">
            {/* Create */}
            <div className="rounded-xl border border-border p-3 lg:p-4 space-y-3">
              <h3 className="text-xs lg:text-sm font-medium">{t("pulse.createSignal") || t('pulse.create')}</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
                <input value={newName} onChange={(e) => setNewName(e.target.value)}
                  placeholder={t("pulse.signalName") || "Signal Name"}
                  className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-red-500/20" />
                <select value={newType} onChange={(e) => setNewType(e.target.value)}
                  className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none">
                  <option value="interval">{t("pulse.interval") || "Interval (ms)"}</option>
                  <option value="tick">{t("pulse.tick") || "Tick"}</option>
                  <option value="once">{t("pulse.once") || "Once"}</option>
                </select>
                <input value={newInterval} onChange={(e) => setNewInterval(e.target.value)}
                  placeholder={t("pulse.intervalPlaceholder") || "e.g. 5m, 1h"}
                  className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-red-500/20" />
                <input value={newMaxFires} onChange={(e) => setNewMaxFires(e.target.value)}
                  placeholder={t("pulse.maxCountPlaceholder") || "Max count"}
                  className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-red-500/20" />
                <button onClick={handleCreate} disabled={loading}
                  className="flex items-center justify-center gap-1.5 rounded-lg bg-red-500 px-2 lg:px-4 py-2 text-xs lg:text-sm text-white hover:bg-red-600 disabled:opacity-50">
                  {loading ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                  {t("common.create") || t('pulse.text18')}
                </button>
              </div>
            </div>

            {/* Signal List */}
            {signals.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Heart size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("pulse.noSignals") || t('pulse.empty')}</p>
              </div>
            ) : (
              <div className="space-y-2">
                {signals.map((s) => (
                  <div key={s.signal_id}
                    className={cn("rounded-xl border p-3 lg:p-4 transition-colors",
                      selectedSignal === s.signal_id ? "border-red-500/50 bg-red-500/5" : "border-border bg-card hover:border-red-500/30")}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <Heart size={16} className={cn(s.status === "active" ? "text-red-500 animate-pulse" : "text-muted-foreground")} />
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="text-xs lg:text-sm font-medium">{s.name}</span>
                            <span className={cn("rounded-full px-2 py-0.5 text-[10px]", STATUS_COLORS[s.status] || "bg-muted")}>
                              {s.status}
                            </span>
                            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                              {TYPE_LABELS[s.signal_type] || s.signal_type}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 lg:gap-4 mt-1 text-xs text-muted-foreground">
                            <span>{t("pulse.interval") || t('pulse.text19')}: {formatMs(s.interval_ms)}</span>
                            <span>{t("pulse.fired") || t('pulse.fired')}: {s.fire_count}{s.max_fires > 0 ? `/${s.max_fires}` : ""}</span>
                            <span>{t("pulse.drift") || t('pulse.text20')}: {formatMs(s.drift_correction)}</span>
                            {s.last_fired_at && <span>{formatTime(s.last_fired_at)}</span>}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        <button onClick={() => handleTick(s.signal_id)}
                          className="rounded-md p-1.5 text-muted-foreground hover:text-red-500 hover:bg-red-500/10 transition-colors"
                          title={t('pulse.t41384')}>
                          <Zap size={14} />
                        </button>
                        {s.status === "active" ? (
                          <button onClick={() => handlePause(s.signal_id)}
                            className="rounded-md p-1.5 text-muted-foreground hover:text-yellow-500 hover:bg-yellow-500/10 transition-colors"
                            title={t('pulse.pause')}>
                            <Pause size={14} />
                          </button>
                        ) : s.status === "paused" ? (
                          <button onClick={() => handleResume(s.signal_id)}
                            className="rounded-md p-1.5 text-muted-foreground hover:text-emerald-500 hover:bg-emerald-500/10 transition-colors"
                            title={t('pulse.resume')}>
                            <Play size={14} />
                          </button>
                        ) : null}
                        <button onClick={() => handleDelete(s.signal_id)}
                          className="rounded-md p-1.5 text-muted-foreground hover:text-red-500 hover:bg-red-500/10 transition-colors"
                          title={t('pulse.delete')}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Ticks Tab */}
        {tab === "ticks" && (
          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <select value={selectedSignal || ""} onChange={(e) => setSelectedSignal(e.target.value || null)}
                className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none">
                <option value="">{t("pulse.allSignals") || t('pulse.allSignals')}</option>
                {signals.map((s) => (
                  <option key={s.signal_id} value={s.signal_id}>{s.name}</option>
                ))}
              </select>
              <span className="text-xs text-muted-foreground">{ticks.length} {t("pulse.tickRecords") || t('pulse.text23')}</span>
            </div>

            {ticks.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Timer size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("pulse.noTicks") || t('pulse.noTicks')}</p>
              </div>
            ) : (
              <div className="rounded-xl border border-border overflow-x-auto">
                <table className="w-full text-xs lg:text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/50">
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">{t("pulse.signal") || "Signal"}</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">{t("pulse.time") || "Interval"}</th>
                      <th className="px-2 lg:px-4 py-2 text-right text-xs font-medium text-muted-foreground">{t("pulse.drift") || "Drift"}</th>
                      <th className="px-2 lg:px-4 py-2 text-right text-xs font-medium text-muted-foreground">{t("pulse.latency") || "Latency"}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ticks.map((t) => (
                      <tr key={t.tick_id} className="border-b border-border last:border-0 hover:bg-muted/30">
                        <td className="px-2 lg:px-4 py-2 font-mono text-xs">{t.signal_id.slice(-8)}</td>
                        <td className="px-2 lg:px-4 py-2 text-xs">{formatTime(t.timestamp)}</td>
                        <td className={cn("px-2 lg:px-4 py-2 text-right text-xs font-mono",
                          Math.abs(t.drift_ms) > 10 ? "text-red-500" : Math.abs(t.drift_ms) > 5 ? "text-yellow-500" : "text-emerald-500")}>
                          {t.drift_ms > 0 ? "+" : ""}{t.drift_ms.toFixed(3)}ms
                        </td>
                        <td className="px-2 lg:px-4 py-2 text-right text-xs font-mono text-muted-foreground">
                          {t.latency_ms.toFixed(3)}ms
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Stats Tab */}
        {tab === "config" && stats && (
          <div className="space-y-4">
            <div className="rounded-xl border border-border bg-card p-5 space-y-4">
              <h3 className="text-xs lg:text-sm font-semibold">{t("pulse.precisionStats") || t('pulse.precisionStats')}</h3>
              <div className="grid grid-cols-2 lg:grid-cols-3 gap-2 lg:gap-6">
                <div>
                  <label className="text-xs text-muted-foreground">{t("pulse.avgDrift") || t('pulse.avgDrift')}</label>
                  <div className="mt-1 text-xl lg:text-2xl font-bold">{formatMs(stats.precision.avg_drift_ms)}</div>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">{t("pulse.maxDrift") || t('pulse.maxDrift')}</label>
                  <div className="mt-1 text-xl lg:text-2xl font-bold">{formatMs(stats.precision.max_drift_ms)}</div>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">{t("pulse.samples") || t('pulse.samples')}</label>
                  <div className="mt-1 text-xl lg:text-2xl font-bold">{stats.precision.sample_size}</div>
                </div>
              </div>
            </div>

            {/* Type breakdown */}
            <div className="rounded-xl border border-border bg-card p-5 space-y-3">
              <h3 className="text-xs lg:text-sm font-semibold">{t("pulse.byType") || t('pulse.byType')}</h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(stats.by_type).map(([type, count]) => (
                  <span key={type} className="rounded-full bg-red-500/10 px-3 py-1 text-xs text-red-600">
                    {TYPE_LABELS[type] || type}: {count}
                  </span>
                ))}
              </div>
            </div>

            {/* Status breakdown */}
            <div className="rounded-xl border border-border bg-card p-5 space-y-3">
              <h3 className="text-xs lg:text-sm font-semibold">{t("pulse.byStatus") || t('pulse.byStatus')}</h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(stats.by_status).map(([status, count]) => (
                  <span key={status} className={cn("rounded-full px-3 py-1 text-xs", STATUS_COLORS[status] || "bg-muted")}>
                    {status}: {count}
                  </span>
                ))}
              </div>
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
