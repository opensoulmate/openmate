'use client';
import { useState, useEffect, useCallback } from 'react';
import { getApiBaseUrl } from '@/lib/api-client';
import {
  Stethoscope, RefreshCw, Loader2, CheckCircle, AlertTriangle,
  XCircle, Clock, HardDrive, Shield, Zap, Activity, ChevronDown,
  ChevronRight, Trash2, FileText, Gauge, ArrowRight, Info,
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface OrganCheck {
  id: string;
  label: string;
  status: 'ok' | 'warn' | 'critical' | 'error' | 'timeout';
  response_ms: number;
  error: string;
  details: Record<string, unknown>;
}

interface IntegrationCheck {
  id: string;
  name: string;
  description: string;
  status: 'ok' | 'warn' | 'error';
  error: string;
}

interface StorageItem {
  name: string;
  path: string;
  exists: boolean;
  size_bytes: number;
  size_human: string;
  file_count: number;
}

interface Recommendation {
  priority: 'high' | 'medium' | 'low';
  category: string;
  message: string;
  action: string;
}

interface DiagnosticReport {
  timestamp: number;
  health_score: number;
  grade: string;
  summary: {
    organs: { total: number; ok: number; warn: number; error: number };
    integrations: { total: number; ok: number; error: number };
    storage: { total_bytes: number; total_human: string };
  };
  organs: OrganCheck[];
  integrations: IntegrationCheck[];
  storage_breakdown: StorageItem[];
  recommendations: Recommendation[];
}

interface CleanupResult {
  dry_run: boolean;
  targets_processed: number;
  results: Array<{
    target: string;
    path: string;
    action: string;
    deleted_files?: number;
    freed_bytes?: number;
    freed_human?: string;
    would_delete_files?: number;
    would_free_human?: string;
    error?: string;
  }>;
  total_freed_human: string;
}

type ActiveTab = 'overview' | 'organs' | 'integrations' | 'storage' | 'cleanup';

const GRADE_COLORS: Record<string, string> = {
  'A+': 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30',
  'A': 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30',
  'B': 'text-amber-400 bg-amber-500/10 border-amber-500/30',
  'C': 'text-orange-400 bg-orange-500/10 border-orange-500/30',
  'D': 'text-red-400 bg-red-500/10 border-red-500/30',
};

const STATUS_ICON: Record<string, React.ElementType> = {
  ok: CheckCircle,
  warn: AlertTriangle,
  critical: XCircle,
  error: XCircle,
  timeout: Clock,
};

const STATUS_COLOR: Record<string, string> = {
  ok: 'text-emerald-500',
  warn: 'text-amber-500',
  critical: 'text-orange-500',
  error: 'text-red-500',
  timeout: 'text-red-500',
};

const PRIORITY_STYLES: Record<string, string> = {
  high: 'border-red-500/30 bg-red-500/5 text-red-400',
  medium: 'border-amber-500/30 bg-amber-500/5 text-amber-400',
  low: 'border-blue-500/30 bg-blue-500/5 text-blue-400',
};

export function SystemDoctorClient() {
  const apiBase = getApiBaseUrl();
  const [activeTab, setActiveTab] = useState<ActiveTab>('overview');
  const [report, setReport] = useState<DiagnosticReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastScan, setLastScan] = useState<number>(0);
  const [expandedOrgan, setExpandedOrgan] = useState<string | null>(null);
  const [cleanupResult, setCleanupResult] = useState<CleanupResult | null>(null);
  const [cleanupLoading, setCleanupLoading] = useState(false);
  const [dryRun, setDryRun] = useState(true);

  const runFullReport = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/plugins/system-doctor/report`);
      if (res.ok) {
        const data = await res.json();
        setReport(data);
        setLastScan(Date.now());
      }
    } catch (e) {
      console.error('Failed to run diagnostic report:', e);
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    runFullReport();
  }, [runFullReport]);

  const runCleanup = async () => {
    setCleanupLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/plugins/system-doctor/cleanup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ targets: ['cache', 'temp', 'logs'], dry_run: dryRun }),
      });
      if (res.ok) {
        setCleanupResult(await res.json());
      }
    } catch (e) {
      console.error('Cleanup failed:', e);
    } finally {
      setCleanupLoading(false);
    }
  };

  const tabs: Array<{ id: ActiveTab; label: string; icon: React.ElementType }> = [
    { id: 'overview', label: '概览', icon: Gauge },
    { id: 'organs', label: 'Organs', icon: Activity },
    { id: 'integrations', label: 'Integrations', icon: Zap },
    { id: 'storage', label: '存储', icon: HardDrive },
    { id: 'cleanup', label: 'Cleanup', icon: Trash2 },
  ];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3">
          <Stethoscope size={20} className="text-rose-500" />
          <h1 className="text-lg font-semibold">系统医生</h1>
          {report && (
            <span className={cn(
              'rounded-full border px-2.5 py-0.5 text-xs lg:text-sm font-bold',
              GRADE_COLORS[report.grade] || 'text-muted-foreground/60'
            )}>
              {report.grade}
            </span>
          )}
          {lastScan > 0 && (
            <span className="text-xs text-muted-foreground">
              Last scan: {new Date(lastScan).toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}
            </span>
          )}
        </div>
        <button
          onClick={runFullReport}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-lg bg-rose-500 px-3 py-1.5 text-xs lg:text-sm text-white hover:bg-rose-600 disabled:opacity-50"
        >
          {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          {loading ? 'Scanning...' : 'Run Diagnostics'}
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border px-3 lg:px-6">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-2.5 text-xs lg:text-sm transition-colors rounded-lg',
                activeTab === tab.id
                  ? 'bg-rose-500/10 text-rose-500'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              )}
            >
              <Icon size={14} />
              {tab.label}
            </button>
          );
        })}
      </div>

      <div className="flex-1 overflow-y-auto p-3 lg:p-6 space-y-3 lg:space-y-6">
        {loading && !report && (
          <div className="flex flex-col items-center justify-center py-20">
            <Loader2 size={32} className="animate-spin text-rose-500 mb-3" />
            <p className="text-xs lg:text-sm text-muted-foreground">正在运行全面诊断...</p>
          </div>
        )}

        {/* Overview Tab */}
        {activeTab === 'overview' && report && (
          <>
            {/* Score Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">健康评分</span>
                <p className="text-2xl lg:text-3xl font-bold mt-1">{report.health_score}<span className="text-base text-muted-foreground">/100</span></p>
                <div className="mt-2 h-1.5 rounded-full bg-muted overflow-hidden">
                  <div
                    className={cn(
                      'h-full rounded-full transition-all',
                      report.health_score >= 80 ? 'bg-emerald-500' :
                      report.health_score >= 50 ? 'bg-amber-500' : 'bg-red-500'
                    )}
                    style={{ width: `${report.health_score}%` }}
                  />
                </div>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">器官正常</span>
                <p className="text-2xl lg:text-3xl font-bold mt-1">
                  <span className="text-emerald-500">{report.summary.organs.ok}</span>
                  <span className="text-base text-muted-foreground">/{report.summary.organs.total}</span>
                </p>
                {report.summary.organs.error > 0 && (
                  <p className="text-xs text-red-500 mt-1">{report.summary.organs.error} error(s)</p>
                )}
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">集成</span>
                <p className="text-2xl lg:text-3xl font-bold mt-1">
                  <span className="text-emerald-500">{report.summary.integrations.ok}</span>
                  <span className="text-base text-muted-foreground">/{report.summary.integrations.total}</span>
                </p>
                {report.summary.integrations.error > 0 && (
                  <p className="text-xs text-red-500 mt-1">{report.summary.integrations.error} error(s)</p>
                )}
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">存储占用</span>
                <p className="text-xl lg:text-2xl font-bold mt-1">{report.summary.storage.total_human}</p>
                <p className="text-xs text-muted-foreground mt-1">托管数据</p>
              </div>
            </div>

            {/* Recommendations */}
            {report.recommendations.length > 0 && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <h3 className="text-xs lg:text-sm font-medium flex items-center gap-2 mb-3">
                  <Info size={14} className="text-blue-500" />
                  Recommendations
                </h3>
                <div className="space-y-2">
                  {report.recommendations.map((rec, i) => (
                    <div key={i} className={cn('rounded-lg border p-3', PRIORITY_STYLES[rec.priority])}>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-[10px] font-bold uppercase px-1.5 py-0.5 rounded bg-current/10">
                          {rec.priority}
                        </span>
                        <span className="text-xs font-medium">{rec.category}</span>
                      </div>
                      <p className="text-xs lg:text-sm">{rec.message}</p>
                      <p className="text-xs text-muted-foreground mt-1">→ {rec.action}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 最慢器官 */}
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <h3 className="text-xs lg:text-sm font-medium mb-3">最慢器官</h3>
              <div className="space-y-1">
                {report.organs
                  .filter((o) => o.status !== 'ok')
                  .concat(report.organs.filter((o) => o.status === 'ok').slice(0, 3))
                  .slice(0, 8)
                  .map((organ) => {
                    const Icon = STATUS_ICON[organ.status] || CheckCircle;
                    return (
                      <div key={organ.id} className="flex items-center gap-2 lg:gap-3 rounded-lg px-3 py-2 hover:bg-muted/50">
                        <Icon size={14} className={STATUS_COLOR[organ.status]} />
                        <span className="text-xs lg:text-sm font-medium flex-1">{organ.label}</span>
                        <span className={cn(
                          'text-xs font-mono',
                          organ.response_ms > 5000 ? 'text-red-500' :
                          organ.response_ms > 2000 ? 'text-amber-500' : 'text-emerald-500'
                        )}>
                          {organ.response_ms}ms
                        </span>
                      </div>
                    );
                  })}
              </div>
            </div>
          </>
        )}

        {/* Organs Tab */}
        {activeTab === 'organs' && report && (
          <div className="space-y-1">
            {report.organs.map((organ) => {
              const Icon = STATUS_ICON[organ.status] || CheckCircle;
              const isExpanded = expandedOrgan === organ.id;
              return (
                <div key={organ.id} className="rounded-lg border border-border bg-card overflow-hidden">
                  <div
                    onClick={() => setExpandedOrgan(isExpanded ? null : organ.id)}
                    className="flex items-center gap-2 lg:gap-3 px-4 py-3 cursor-pointer hover:bg-muted/50"
                  >
                    {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    <Icon size={14} className={STATUS_COLOR[organ.status]} />
                    <span className="text-xs lg:text-sm font-medium flex-1">{organ.label}</span>
                    <span className="text-xs text-muted-foreground font-mono">{organ.id}</span>
                    <span className={cn(
                      'text-xs font-mono px-2 py-0.5 rounded',
                      organ.response_ms > 5000 ? 'bg-red-500/10 text-red-500' :
                      organ.response_ms > 2000 ? 'bg-amber-500/10 text-amber-500' :
                      'bg-emerald-500/10 text-emerald-500'
                    )}>
                      {organ.response_ms}ms
                    </span>
                    <span className={cn(
                      'text-xs px-2 py-0.5 rounded',
                      organ.status === 'ok' ? 'bg-emerald-500/10 text-emerald-500' :
                      organ.status === 'warn' ? 'bg-amber-500/10 text-amber-500' :
                      'bg-red-500/10 text-red-500'
                    )}>
                      {organ.status}
                    </span>
                  </div>
                  {isExpanded && (
                    <div className="border-t border-border px-4 py-3 bg-muted/30">
                      {organ.error && (
                        <p className="text-xs text-red-400 mb-2">Error: {organ.error}</p>
                      )}
                      {Object.keys(organ.details).length > 0 && (
                        <pre className="text-xs font-mono text-muted-foreground overflow-x-auto">
                          {JSON.stringify(organ.details, null, 2)}
                        </pre>
                      )}
                      {!organ.error && Object.keys(organ.details).length === 0 && (
                        <p className="text-xs text-muted-foreground">暂无更多详情。</p>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Integrations Tab */}
        {activeTab === 'integrations' && report && (
          <div className="space-y-2">
            {report.integrations.map((integration) => {
              const Icon = STATUS_ICON[integration.status] || CheckCircle;
              return (
                <div key={integration.id} className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <div className="flex items-center gap-2 lg:gap-3">
                    <Icon size={16} className={STATUS_COLOR[integration.status]} />
                    <div className="flex-1">
                      <p className="text-xs lg:text-sm font-medium">{integration.name}</p>
                      <p className="text-xs text-muted-foreground">{integration.description}</p>
                    </div>
                    <span className={cn(
                      'text-xs px-2 py-0.5 rounded',
                      integration.status === 'ok' ? 'bg-emerald-500/10 text-emerald-500' :
                      'bg-red-500/10 text-red-500'
                    )}>
                      {integration.status}
                    </span>
                  </div>
                  {integration.error && (
                    <p className="text-xs text-red-400 mt-2 ml-7">{integration.error}</p>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Storage Tab */}
        {activeTab === 'storage' && report && (
          <>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <h3 className="text-xs lg:text-sm font-medium mb-3 flex items-center gap-2">
                <HardDrive size={14} className="text-blue-500" />
                Storage Breakdown
              </h3>
              <div className="space-y-2">
                {report.storage_breakdown.map((item) => (
                  <div key={item.name} className="flex items-center gap-2 lg:gap-3">
                    <span className="text-xs text-muted-foreground w-40 truncate">{item.name}</span>
                    <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden">
                      <div
                        className="h-full rounded-full bg-blue-500"
                        style={{
                          width: `${Math.min(
                            (item.size_bytes / Math.max(report.summary.storage.total_bytes, 1)) * 100,
                            100
                          )}%`,
                        }}
                      />
                    </div>
                    <span className="text-xs font-mono w-20 text-right">{item.size_human}</span>
                  </div>
                ))}
              </div>
              <div className="mt-3 pt-3 border-t border-border flex justify-between">
                <span className="text-xs text-muted-foreground">托管存储总量</span>
                <span className="text-xs lg:text-sm font-bold">{report.summary.storage.total_human}</span>
              </div>
            </div>
          </>
        )}

        {/* Cleanup Tab */}
        {activeTab === 'cleanup' && (
          <div className="space-y-4">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <h3 className="text-xs lg:text-sm font-medium mb-3 flex items-center gap-2">
                <Trash2 size={14} className="text-orange-500" />
                Storage Cleanup
              </h3>
              <p className="text-xs text-muted-foreground mb-4">
                Clean up temporary files, old logs, and cache data older than 7 days.
              </p>
              <div className="flex items-center gap-2 lg:gap-4 mb-4">
                <label className="flex items-center gap-2 text-xs lg:text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={dryRun}
                    onChange={(e) => setDryRun(e.target.checked)}
                    className="rounded border-border"
                  />
                  Dry run (preview only)
                </label>
                <button
                  onClick={runCleanup}
                  disabled={cleanupLoading}
                  className={cn(
                    'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs lg:text-sm text-white disabled:opacity-50',
                    dryRun
                      ? 'bg-blue-500 hover:bg-blue-600'
                      : 'bg-orange-500 hover:bg-orange-600'
                  )}
                >
                  {cleanupLoading ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Trash2 size={14} />
                  )}
                  {dryRun ? '预览清理' : '执行清理'}
                </button>
              </div>

              {cleanupResult && (
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-xs lg:text-sm">
                    <FileText size={14} className="text-muted-foreground" />
                    <span>
                      {cleanupResult.dry_run ? '预览' : '清理'} 完成——
                      释放 <strong>{cleanupResult.total_freed_human}</strong>
                    </span>
                  </div>
                  {cleanupResult.results.map((r, i) => (
                    <div key={i} className="rounded-lg border border-border bg-muted/50 p-3 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-medium">{r.target}</span>
                        <span className={cn(
                          'px-1.5 py-0.5 rounded',
                          r.action === 'cleaned' ? 'bg-emerald-500/10 text-emerald-500' :
                          r.action === 'dry_run' ? 'bg-blue-500/10 text-blue-500' :
                          r.action === 'error' ? 'bg-red-500/10 text-red-500' :
                          'bg-muted-foreground/10 text-muted-foreground'
                        )}>
                          {r.action}
                        </span>
                      </div>
                      {r.freed_human && <p className="text-muted-foreground mt-1">Freed: {r.freed_human} ({r.deleted_files} files)</p>}
                      {r.would_free_human && <p className="text-muted-foreground mt-1">Would free: {r.would_free_human} ({r.would_delete_files} files)</p>}
                      {r.error && <p className="text-red-400 mt-1">{r.error}</p>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
