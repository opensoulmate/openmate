'use client';
import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { getApiBaseUrl, getToken } from '@/lib/api-client';
import {
  RefreshCw, Newspaper, TrendingUp, TrendingDown, Activity,
  CheckCircle, XCircle, AlertTriangle, Clock, Zap, BarChart3,
  ChevronRight, Loader2, Star, ArrowUpRight, ArrowDownRight,
  Calendar, Hash, Flame, Eye,
} from 'lucide-react';
import Link from 'next/link';

interface DigestData {
  id: string;
  date: string;
  score: number;
  total_events: number;
  active_organs: number;
  total_organs?: number;
  highlights: string[];
  warnings: string[];
  organ_summary: Record<string, { emoji: string; status: string; response_time_ms: number }>;
  timeline_summary: { total_events: number; by_organ: Record<string, number>; by_type: Record<string, number> };
  metrics_summary: { knowledge_entries: number; trajectory_sessions: number; trajectory_events: number; trajectory_tokens: number };
  generated_at: number;
}

interface DigestListItem {
  id: string;
  date: string;
  score: number;
  total_events: number;
  active_organs: number;
  generated_at: number;
  highlights: string[];
  warnings: string[];
}

interface TrendPoint {
  date: string;
  value: number;
}

function scoreColor(score: number): string {
  if (score >= 95) return 'text-emerald-400';
  if (score >= 80) return 'text-green-400';
  if (score >= 60) return 'text-amber-400';
  if (score >= 40) return 'text-orange-400';
  return 'text-red-400';
}

function scoreGlow(score: number): string {
  if (score >= 95) return 'shadow-emerald-500/20';
  if (score >= 80) return 'shadow-green-500/20';
  if (score >= 60) return 'shadow-amber-500/20';
  return 'shadow-red-500/20';
}

function statusIcon(status: string) {
  if (status === 'ok') return <CheckCircle className="w-3.5 h-3.5 text-emerald-400" />;
  if (status === 'error') return <XCircle className="w-3.5 h-3.5 text-red-400" />;
  return <Clock className="w-3.5 h-3.5 text-muted-foreground" />;
}

function MiniSparkline({ data, color = '#10b981' }: { data: number[]; color?: string }) {
  if (data.length < 2) return null;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const w = 120;
  const h = 32;
  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / range) * (h - 4) - 2;
    return `${x},${y}`;
  }).join(' ');
  return (
    <svg width={w} height={h} className="opacity-70">
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function DailyDigestClient() {
  const { t } = useTranslation();
  const [digest, setDigest] = useState<DigestData | null>(null);
  const [history, setHistory] = useState<DigestListItem[]>([]);
  const [trends, setTrends] = useState<TrendPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [tab, setTab] = useState<'today' | 'history' | 'trends'>('today');
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const apiBase = getApiBaseUrl();
  const headers = { Authorization: `Bearer ${getToken()}` };

  const fetchToday = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/plugins/daily-digest/today`, { headers });
      if (res.ok) {
        const data = await res.json();
        setDigest(data);
      }
    } catch {}
  }, [apiBase]);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/plugins/daily-digest/digests?limit=60`, { headers });
      if (res.ok) {
        const data = await res.json();
        setHistory(data.digests || []);
      }
    } catch {}
  }, [apiBase]);

  const fetchTrends = useCallback(async (metric = 'health_score', days = 30) => {
    try {
      const res = await fetch(`${apiBase}/api/plugins/daily-digest/trends?metric=${metric}&days=${days}`, { headers });
      if (res.ok) {
        const data = await res.json();
        setTrends(data.data || []);
      }
    } catch {}
  }, [apiBase]);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchToday(), fetchHistory(), fetchTrends()]).finally(() => setLoading(false));
  }, []);

  const generateDigest = async () => {
    setGenerating(true);
    try {
      const res = await fetch(`${apiBase}/api/plugins/daily-digest/generate?force=true`, { method: 'POST', headers });
      if (res.ok) {
        const data = await res.json();
        setDigest(data);
        fetchHistory();
      }
    } catch {}
    setGenerating(false);
  };

  const viewDigest = async (date: string) => {
    setSelectedDate(date);
    try {
      const res = await fetch(`${apiBase}/api/plugins/daily-digest/digests/${date}`, { headers });
      if (res.ok) {
        const data = await res.json();
        setDigest(data);
        setTab('today');
      }
    } catch {}
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const score = digest?.score || 0;
  const totalOrgans = digest?.active_organs || Object.keys(digest?.organ_summary || {}).length;
  const healthyOrgans = Object.values(digest?.organ_summary || {}).filter(o => o.status === 'ok').length;
  const avgResponse = Object.values(digest?.organ_summary || {}).reduce((s, o) => s + o.response_time_ms, 0) / Math.max(Object.keys(digest?.organ_summary || {}).length, 1);

  return (
    <div className="space-y-3 lg:space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl bg-gradient-to-br from-violet-500/20 to-fuchsia-500/20 border border-violet-500/20">
            <Newspaper className="w-6 h-6 text-violet-400" />
          </div>
          <div>
            <h1 className="text-xl lg:text-2xl font-bold">每日摘要</h1>
            <p className="text-xs lg:text-sm text-muted-foreground">{t("plugins.digestSubtitle") || "Cross-organ data aggregation · System health trends · Daily insights"}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={generateDigest}
            disabled={generating}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-violet-600 to-fuchsia-600 text-white text-xs lg:text-sm font-medium hover:from-violet-500 hover:to-fuchsia-500 transition-all disabled:opacity-50"
          >
            {generating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
            {generating ? t('plugins.generating') : t('plugins.generateToday')}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 bg-muted/30 rounded-lg w-fit">
        {[
          { key: 'today', label: t('plugins.todayDigest'), icon: Newspaper },
          { key: 'history', label: t('plugins.history'), icon: Calendar },
          { key: 'trends', label: t('plugins.trendAnalysis'), icon: TrendingUp },
        ].map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key as any)}
            className={cn(
              'flex items-center gap-1.5 px-4 py-2 rounded-md text-xs lg:text-sm font-medium transition-all',
              tab === key ? 'bg-background shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <Icon className="w-4 h-4" />
            {label}
          </button>
        ))}
      </div>

      {/* Today Tab */}
      {tab === 'today' && digest && (
        <div className="space-y-3 lg:space-y-6">
          {/* Score & Quick Stats */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-2 lg:gap-4">
            {/* 健康评分 */}
            <div className={cn(
              'relative p-3 lg:p-6 rounded-2xl border border-border bg-gradient-to-br from-card to-card/50 shadow-lg',
              scoreGlow(score)
            )}>
              <div className="text-xs text-muted-foreground uppercase tracking-wider mb-2">健康评分</div>
              <div className={cn('text-5xl font-black tabular-nums', scoreColor(score))}>
                {score.toFixed(0)}
              </div>
              <div className="text-xs text-muted-foreground mt-1">/ 100</div>
              <div className="mt-3 w-full h-2 bg-muted/30 rounded-full overflow-hidden">
                <div
                  className={cn('h-full rounded-full transition-all duration-1000', score >= 80 ? 'bg-emerald-500' : score >= 60 ? 'bg-amber-500' : 'bg-red-500')}
                  style={{ width: `${Math.min(score, 100)}%` }}
                />
              </div>
            </div>

            {/* Active Organs */}
            <div className="p-3 lg:p-6 rounded-2xl border border-border bg-card">
              <div className="flex items-center gap-2 text-xs text-muted-foreground uppercase tracking-wider mb-2">
                <Activity className="w-3.5 h-3.5" />
                {t('plugins.active')}
              </div>
              <div className="text-2xl lg:text-3xl font-bold text-foreground">
                <span className="text-emerald-400">{healthyOrgans}</span>
                <span className="text-muted-foreground text-lg">/{Object.keys(digest.organ_summary).length}</span>
              </div>
              <div className="text-xs text-muted-foreground mt-1">{t('plugins.online')}</div>
            </div>

            {/* Events */}
            <div className="p-3 lg:p-6 rounded-2xl border border-border bg-card">
              <div className="flex items-center gap-2 text-xs text-muted-foreground uppercase tracking-wider mb-2">
                <Flame className="w-3.5 h-3.5" />
                24h {t('plugins.events24h')}
              </div>
              <div className="text-2xl lg:text-3xl font-bold text-foreground">{digest.total_events}</div>
              <div className="text-xs text-muted-foreground mt-1">{t("plugins.crossOrganEvents") || "Cross-Organ Events"}</div>
            </div>

            {/* Avg Response */}
            <div className="p-3 lg:p-6 rounded-2xl border border-border bg-card">
              <div className="flex items-center gap-2 text-xs text-muted-foreground uppercase tracking-wider mb-2">
                <Clock className="w-3.5 h-3.5" />
                {t("plugins.avgResponse") || "Avg Response"}
              </div>
              <div className={cn('text-2xl lg:text-3xl font-bold', avgResponse > 300 ? 'text-amber-400' : 'text-emerald-400')}>
                {avgResponse.toFixed(0)}<span className="text-lg text-muted-foreground">ms</span>
              </div>
              <div className="text-xs text-muted-foreground mt-1">{t("plugins.crossOrganAvg") || "Cross-Organ Average"}</div>
            </div>
          </div>

          {/* Highlights & Warnings */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 lg:gap-4">
            {digest.highlights.length > 0 && (
              <div className="p-5 rounded-2xl border border-emerald-500/20 bg-emerald-500/5">
                <div className="flex items-center gap-2 text-xs lg:text-sm font-semibold text-emerald-400 mb-3">
                  <Star className="w-4 h-4" />
                  {t("plugins.highlights") || "Highlights"}
                </div>
                <div className="space-y-2">
                  {digest.highlights.map((h, i) => (
                    <div key={i} className="text-xs lg:text-sm text-foreground/80">{h}</div>
                  ))}
                </div>
              </div>
            )}
            {digest.warnings.length > 0 && (
              <div className="p-5 rounded-2xl border border-amber-500/20 bg-amber-500/5">
                <div className="flex items-center gap-2 text-xs lg:text-sm font-semibold text-amber-400 mb-3">
                  <AlertTriangle className="w-4 h-4" />
                  {t("plugins.alerts") || "Alerts"}
                </div>
                <div className="space-y-2">
                  {digest.warnings.map((w, i) => (
                    <div key={i} className="text-xs lg:text-sm text-foreground/80">{w}</div>
                  ))}
                </div>
              </div>
            )}
            {digest.highlights.length === 0 && digest.warnings.length === 0 && (
              <div className="col-span-2 p-8 rounded-2xl border border-border bg-card text-center text-muted-foreground">
                {t("plugins.noHighlightsOrAlerts") || "No highlights or alerts — generate today's summary to see analysis"}
              </div>
            )}
          </div>

          {/* Organ Grid */}
          <div>
            <h3 className="text-xs lg:text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">{t("plugins.organStatus") || "Organ Status"}</h3>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 lg:grid-cols-6 gap-2">
              {Object.entries(digest.organ_summary).sort(([, a], [, b]) => (b.response_time_ms || 0) - (a.response_time_ms || 0)).map(([name, info]) => (
                <div
                  key={name}
                  className={cn(
                    'p-3 rounded-xl border transition-all hover:scale-105 cursor-default',
                    info.status === 'ok' ? 'border-emerald-500/20 bg-emerald-500/5' : 'border-red-500/20 bg-red-500/5'
                  )}
                >
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="text-base">{info.emoji}</span>
                    {statusIcon(info.status)}
                  </div>
                  <div className="text-xs font-medium capitalize truncate">{name}</div>
                  <div className="text-[10px] text-muted-foreground tabular-nums">{info.response_time_ms.toFixed(0)}ms</div>
                </div>
              ))}
            </div>
          </div>

          {/* Timeline Breakdown */}
          {digest.timeline_summary.by_organ && Object.keys(digest.timeline_summary.by_organ).length > 0 && (
            <div>
              <h3 className="text-xs lg:text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">{t("plugins.activityDistribution") || "Activity Distribution"}</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2 lg:gap-4">
                {/* By Organ */}
                <div className="p-5 rounded-2xl border border-border bg-card">
                  <div className="text-xs text-muted-foreground mb-3">{t("plugins.byOrgan") || "By Organ"}</div>
                  <div className="space-y-2">
                    {Object.entries(digest.timeline_summary.by_organ)
                      .sort(([, a], [, b]) => b - a)
                      .slice(0, 10)
                      .map(([organ, count]) => {
                        const maxCount = Math.max(...Object.values(digest.timeline_summary.by_organ));
                        return (
                          <div key={organ} className="flex items-center gap-2">
                            <span className="text-xs w-20 truncate capitalize text-muted-foreground">{organ}</span>
                            <div className="flex-1 h-4 bg-muted/20 rounded-full overflow-hidden">
                              <div
                                className="h-full bg-gradient-to-r from-violet-500 to-fuchsia-500 rounded-full"
                                style={{ width: `${(count / maxCount) * 100}%` }}
                              />
                            </div>
                            <span className="text-xs tabular-nums text-muted-foreground w-8 text-right">{count}</span>
                          </div>
                        );
                      })}
                  </div>
                </div>

                {/* By Type */}
                <div className="p-5 rounded-2xl border border-border bg-card">
                  <div className="text-xs text-muted-foreground mb-3">{t("plugins.byType") || "By Type"}</div>
                  <div className="space-y-2">
                    {Object.entries(digest.timeline_summary.by_type)
                      .sort(([, a], [, b]) => b - a)
                      .slice(0, 10)
                      .map(([type, count]) => {
                        const maxCount = Math.max(...Object.values(digest.timeline_summary.by_type));
                        return (
                          <div key={type} className="flex items-center gap-2">
                            <span className="text-xs w-32 truncate text-muted-foreground">{type}</span>
                            <div className="flex-1 h-4 bg-muted/20 rounded-full overflow-hidden">
                              <div
                                className="h-full bg-gradient-to-r from-cyan-500 to-blue-500 rounded-full"
                                style={{ width: `${(count / maxCount) * 100}%` }}
                              />
                            </div>
                            <span className="text-xs tabular-nums text-muted-foreground w-8 text-right">{count}</span>
                          </div>
                        );
                      })}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Metrics */}
          {digest.metrics_summary && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
              <div className="p-4 rounded-xl border border-border bg-card">
                <div className="text-xs text-muted-foreground mb-1">{t("plugins.knowledgeEntries") || "📚 Knowledge Entries"}</div>
                <div className="text-lg lg:text-xl font-bold">{digest.metrics_summary.knowledge_entries || 0}</div>
              </div>
              <div className="p-4 rounded-xl border border-border bg-card">
                <div className="text-xs text-muted-foreground mb-1">{t("plugins.trajectorySessions") || "📊 Trajectory Sessions"}</div>
                <div className="text-lg lg:text-xl font-bold">{digest.metrics_summary.trajectory_sessions || 0}</div>
              </div>
              <div className="p-4 rounded-xl border border-border bg-card">
                <div className="text-xs text-muted-foreground mb-1">{t("plugins.trajectoryEvents") || "📝 Trajectory Events"}</div>
                <div className="text-lg lg:text-xl font-bold">{digest.metrics_summary.trajectory_events || 0}</div>
              </div>
              <div className="p-4 rounded-xl border border-border bg-card">
                <div className="text-xs text-muted-foreground mb-1">{t("plugins.tokenUsage") || "🔤 Token Usage"}</div>
                <div className="text-lg lg:text-xl font-bold">{(digest.metrics_summary.trajectory_tokens || 0).toLocaleString()}</div>
              </div>
            </div>
          )}

          {/* Generated timestamp */}
          <div className="text-xs text-muted-foreground text-center">
            {t("plugins.generatedAt") || "Generated at"} {new Date(digest.generated_at * 1000).toLocaleString('zh-CN')}
            {selectedDate && selectedDate !== new Date().toISOString().slice(0, 10) && (
              <span className="ml-2 text-violet-400">· {t("plugins.viewHistory") || "View History"}: {selectedDate}</span>
            )}
          </div>
        </div>
      )}

      {/* History Tab */}
      {tab === 'history' && (
        <div className="space-y-4">
          {history.length === 0 ? (
            <div className="p-12 rounded-2xl border border-border bg-card text-center">
              <Calendar className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
              <p className="text-muted-foreground">{t("plugins.noHistory") || "No history"}</p>
              <p className="text-xs lg:text-sm text-muted-foreground mt-1">{t("plugins.generateHint") || "Click \"Generate Today's Digest\" to start"}</p>
            </div>
          ) : (
            <div className="space-y-2">
              {history.map((item) => (
                <button
                  key={item.id}
                  onClick={() => viewDigest(item.date)}
                  className="w-full flex items-center gap-2 lg:gap-4 p-3 lg:p-4 rounded-xl border border-border bg-card hover:bg-muted/30 transition-all text-left group"
                >
                  {/* Score badge */}
                  <div className={cn(
                    'w-14 h-14 rounded-xl flex items-center justify-center text-lg lg:text-xl font-black tabular-nums',
                    item.score >= 80 ? 'bg-emerald-500/10 text-emerald-400' : item.score >= 60 ? 'bg-amber-500/10 text-amber-400' : 'bg-red-500/10 text-red-400'
                  )}>
                    {item.score.toFixed(0)}
                  </div>
                  {/* Date & Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{item.date}</span>
                      <span className="text-xs text-muted-foreground">
                        {new Date(item.generated_at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' , second: '2-digit', fractionalSecondDigits: 3 })}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                      <span>{t("plugins.organsOnlineCount", { count: item.active_organs }) || `${item.active_organs} organs online`}</span>
                      <span>{t("plugins.eventsCount", { count: item.total_events }) || `${item.total_events} events`}</span>
                      {item.warnings.length > 0 && (
                        <span className="text-amber-400">{t("plugins.warningsCount", { count: item.warnings.length }) || `${item.warnings.length} warnings`}</span>
                      )}
                    </div>
                    {item.highlights.length > 0 && (
                      <div className="text-xs text-muted-foreground mt-1 truncate">{item.highlights[0]}</div>
                    )}
                  </div>
                  <ChevronRight className="w-4 h-4 text-muted-foreground group-hover:text-foreground transition-colors" />
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Trends Tab */}
      {tab === 'trends' && (
        <div className="space-y-3 lg:space-y-6">
          <div className="flex gap-2">
            {[
              { key: 'health_score', label: t('plugins.healthScore') },
              { key: 'total_events', label: t('plugins.eventCount') },
              { key: 'active_organs', label: t('plugins.activeOrgans') },
              { key: 'avg_response_ms', label: t('plugins.responseTime') },
            ].map(({ key, label }) => (
              <button
                key={key}
                onClick={() => fetchTrends(key)}
                className="px-3 py-1.5 rounded-lg text-xs font-medium bg-muted/30 hover:bg-muted/50 transition-colors"
              >
                {label}
              </button>
            ))}
          </div>

          {trends.length === 0 ? (
            <div className="p-12 rounded-2xl border border-border bg-card text-center">
              <BarChart3 className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
              <p className="text-muted-foreground">{t("plugins.noTrendData") || "No trend data"}</p>
              <p className="text-xs lg:text-sm text-muted-foreground mt-1">{t("plugins.trendHint") || "Generate summaries for multiple dates to view trends"}</p>
            </div>
          ) : (
            <div className="p-3 lg:p-6 rounded-2xl border border-border bg-card">
              <div className="flex items-center gap-3 mb-4">
                <h3 className="text-xs lg:text-sm font-semibold">{t("plugins.trendChart") || "Trend Chart"}</h3>
                <span className="text-xs text-muted-foreground">{t("plugins.dataPoints", { count: trends.length }) || `${trends.length} data points`}</span>
              </div>
              {/* Simple bar chart */}
              <div className="flex items-end gap-1 h-40">
                {trends.map((point, i) => {
                  const max = Math.max(...trends.map(t => t.value));
                  const h = max > 0 ? (point.value / max) * 100 : 0;
                  return (
                    <div
                      key={i}
                      className="flex-1 group relative"
                    >
                      <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 hidden group-hover:block z-10">
                        <div className="px-2 py-1 rounded bg-popover border text-xs whitespace-nowrap">
                          {point.date}: {point.value.toFixed(1)}
                        </div>
                      </div>
                      <div
                        className="w-full bg-gradient-to-t from-violet-600 to-fuchsia-400 rounded-t transition-all hover:opacity-80"
                        style={{ height: `${Math.max(h, 2)}%` }}
                      />
                    </div>
                  );
                })}
              </div>
              <div className="flex justify-between mt-2 text-[10px] text-muted-foreground">
                <span>{trends[0]?.date}</span>
                <span>{trends[trends.length - 1]?.date}</span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
