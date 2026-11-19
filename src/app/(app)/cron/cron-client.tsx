'use client';
import { useState, useEffect, useMemo, useCallback } from 'react';
import { Clock, Play, Pause, Trash2, RefreshCw, Loader2, ChevronRight, ChevronDown, Plus, X, Zap, History, CheckCircle, XCircle, Timer } from 'lucide-react';
import { getApiBaseUrl } from '@/lib/api-client';
import { cn } from '@/lib/utils';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { PageLayout } from '@/components/page-layout';
import { LeftPanel } from '@/components/left-panel';
import { useAppStore } from '@/stores/app-store';
import { ClockFace } from './clock-face';
import { CronCalendar, filterJobsByDate } from './cron-calendar';
import { WeekSchedule } from './week-schedule';
import { YearView, MonthView } from './calendar-views';

const getApiUrl = () => getApiBaseUrl();

interface CronJob {
  id: string;
  job_id: string;
  name: string;
  schedule: string;
  status: 'active' | 'paused';
  next_run?: string;
  last_run?: string;
  deliver?: string;
  prompt?: string;
  agent?: string;
}

interface HistoryRun {
  run_id: string;
  started_at: string;
  finished_at?: string;
  status: 'success' | 'failed' | 'running';
  duration?: number;
  error?: string;
}

async function apiRequest(path: string, method = 'GET', body?: unknown) {
  const token = localStorage.getItem('openmate-token');
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${getApiUrl()}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

function formatDuration(ms?: number): string {
  if (!ms) return '-';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

function formatTime(iso?: string): string {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('zh-CN', { fractionalSecondDigits: 3 });
}

export function CronClient() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [expandedHistory, setExpandedHistory] = useState<Set<string>>(new Set());
  const [historyData, setHistoryData] = useState<Record<string, HistoryRun[]>>({});
  const [historyLoading, setHistoryLoading] = useState<Set<string>>(new Set());
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createSchedule, setCreateSchedule] = useState('30m');
  const [createPrompt, setCreatePrompt] = useState('');
  const [createDeliver, setCreateDeliver] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<Date | null>(null);
  const [viewMode, setViewMode] = useState<'year' | 'month' | 'week' | 'day'>('week');
  const { t } = useTranslation();
  const router = useRouter();
  const setPageSidebar = useAppStore((s) => s.setPageSidebar);

  const loadJobs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiRequest('/api/cron/list');
      setJobs(data.jobs || []);
    } catch (e) { console.error('Failed to load cron jobs:', e); }
    setLoading(false);
  }, []);

  useEffect(() => { loadJobs(); }, [loadJobs]);

  const handleAction = async (jobId: string, action: 'pause' | 'resume' | 'run' | 'delete') => {
    setActionLoading(jobId + action);
    try {
      if (action === 'delete') {
        await apiRequest(`/api/cron/${jobId}`, 'DELETE');
        if (selectedJobId === jobId) setSelectedJobId(null);
      } else {
        await apiRequest(`/api/cron/${jobId}/${action}`, 'POST');
      }
      await loadJobs();
    } catch (e) { console.error(`Failed to ${action} job:`, e); }
    setActionLoading(null);
  };

  const toggleHistory = async (jobId: string) => {
    setExpandedHistory(prev => {
      const next = new Set(prev);
      if (next.has(jobId)) {
        next.delete(jobId);
      } else {
        next.add(jobId);
      }
      return next;
    });
    // Fetch history if not already loaded
    if (!historyData[jobId]) {
      setHistoryLoading(prev => new Set(prev).add(jobId));
      try {
        const data = await apiRequest(`/api/cron/${jobId}/history`);
        setHistoryData(prev => ({ ...prev, [jobId]: data.runs || data.history || [] }));
      } catch (e) { console.error('Failed to load history:', e); }
      setHistoryLoading(prev => {
        const next = new Set(prev);
        next.delete(jobId);
        return next;
      });
    }
  };

  const handleCreate = async () => {
    if (!createSchedule.trim()) return;
    setCreating(true);
    setCreateError('');
    try {
      await apiRequest('/api/cron/create', 'POST', {
        schedule: createSchedule.trim(),
        prompt: createPrompt.trim(),
        name: createName.trim(),
        deliver: createDeliver.trim(),
      });
      setShowCreate(false);
      setCreateName(''); setCreateSchedule('30m'); setCreatePrompt(''); setCreateDeliver('');
      await loadJobs();
    } catch (e: unknown) {
      setCreateError(e instanceof Error ? e.message : 'Failed to create job');
    }
    setCreating(false);
  };

  const activeCount = useMemo(() => jobs.filter(j => j.status === 'active').length, [jobs]);
  const pausedCount = useMemo(() => jobs.filter(j => j.status === 'paused').length, [jobs]);

  // Group jobs by status for sidebar
  const groupedJobs = useMemo(() => {
    const active = jobs.filter(j => j.status === 'active');
    const paused = jobs.filter(j => j.status === 'paused');
    return { active, paused };
  }, [jobs]);

  // Register sidebar content
  useEffect(() => {
    setPageSidebar(
      <LeftPanel
        items={jobs}
        filter={(job, q) => {
          const jobId = job.job_id || job.id;
          const name = job.name || jobId;
          return name.toLowerCase().includes(q) || job.schedule.toLowerCase().includes(q);
        }}
        renderItem={(job) => {
          const jobId = job.job_id || job.id;
          const isActive = job.status === 'active';
          return (
            <div
              key={jobId}
              className={cn(
                'w-full px-2 py-2 rounded-lg transition-colors',
                selectedJobId === jobId
                  ? 'bg-primary/10 border border-primary/30'
                  : 'hover:bg-muted/50 border border-transparent',
              )}
            >
              <div className="flex items-center gap-1.5 min-w-0" onClick={() => router.push(`/cron/${jobId}`)}>
                <span className={cn('w-2 h-2 rounded-full shrink-0', isActive ? 'bg-green-500' : 'bg-amber-500')} />
                <span className="text-xs font-medium truncate flex-1 cursor-pointer">{job.name || jobId}</span>
                {/* Action buttons */}
                <button
                  onClick={(e) => { e.stopPropagation(); handleAction(jobId, isActive ? 'pause' : 'resume'); }}
                  className={cn('p-1 rounded hover:bg-muted shrink-0', isActive ? 'text-amber-500' : 'text-green-500')}
                  title={isActive ? '暂停' : '继续'}
                >
                  {isActive ? <Pause className="w-3 h-3" /> : <Play className="w-3 h-3" />}
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); handleAction(jobId, 'run'); }}
                  className="p-1 rounded hover:bg-muted text-primary shrink-0"
                  title="立即运行"
                >
                  <Zap className="w-3 h-3" />
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); handleAction(jobId, 'delete'); }}
                  className="p-1 rounded hover:bg-muted text-destructive shrink-0"
                  title="删除"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
              <div className="ml-3.5 mt-0.5">
                <span className="text-[10px] text-muted-foreground font-mono">{job.schedule}</span>
              </div>
            </div>
          );
        }}
        header={
          <div className="px-2 pb-2">
            <button onClick={() => setShowCreate(true)}
              className="w-full flex items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors">
              <Plus className="w-3.5 h-3.5" />
              {t('cron.newJob', '新建任务')}
            </button>
          </div>
        }
        placeholder={t('cron.searchPlaceholder', 'Search jobs...')}
        emptyState={
          <div className="px-2 py-8 text-center text-muted-foreground/50">
            <Clock className="w-8 h-8 mx-auto mb-1.5" />
            <p className="text-xs">{t('cron.noJobs', 'No cron jobs found')}</p>
          </div>
        }
      />
    );
    return () => setPageSidebar(null);
  }, [jobs, selectedJobId, t, setPageSidebar]);

  // 任务详情=整页 /cron/[id]（入口点击 router.push 路由，2026-11-19 根修）；
  // 页内纯展示型选中态 selectedJobId 保留给周视图高亮，不进 workspace 面板。

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
      <PageLayout title="定时任务">
        
    <div className="h-full flex flex-col overflow-hidden">
      {/* View tabs + Legend */}
      <div className="flex items-center justify-between px-3 lg:px-6 pt-3 pb-2 shrink-0">
        <div className="flex items-center gap-0.5 bg-muted/50 rounded-lg p-0.5">
          {(['year', 'month', 'week', 'day'] as const).map(mode => (
            <button
              key={mode}
              onClick={() => setViewMode(mode)}
              className={cn(
                'px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
                viewMode === mode
                  ? 'bg-background text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {mode === 'year' ? '年' : mode === 'month' ? '月' : mode === 'week' ? '周' : '日'}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-green-500" />
            <span className="text-[10px] text-muted-foreground">启用</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-amber-500" />
            <span className="text-[10px] text-muted-foreground">已暂停</span>
          </div>
        </div>
      </div>

      {/* View content */}
      {jobs.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground">
          <Clock className="w-12 h-12 mb-3 opacity-30" />
          <p className="text-sm">{t('cron.noJobs', '暂无定时任务')}</p>
        </div>
      ) : viewMode === 'week' ? (
        <div className="flex-1 min-h-0">
          <WeekSchedule jobs={jobs} selectedJobId={selectedJobId} onSelectJob={setSelectedJobId} />
        </div>
      ) : viewMode === 'month' ? (
        <div className="flex-1 min-h-0 overflow-y-auto px-3 lg:px-6">
          <MonthView jobs={jobs} selectedJobId={selectedJobId} onSelectJob={setSelectedJobId} />
        </div>
      ) : viewMode === 'year' ? (
        <div className="flex-1 min-h-0 overflow-y-auto px-3 lg:px-6">
          <YearView jobs={jobs} />
        </div>
      ) : (
        <div className="flex-1 min-h-0 w-full flex flex-col">
          <ClockFace jobs={jobs} selectedJobId={selectedJobId} onSelectJob={setSelectedJobId} />
        </div>
      )}

      {/* Create Dialog */}
      {showCreate && (
        <div className="mt-4 p-3 lg:p-4 rounded-xl border bg-card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium flex items-center gap-2">
              <Zap className="w-4 h-4 text-primary" /> {t('cron.createNew', 'Create New Job')}
            </h3>
            <button onClick={() => { setShowCreate(false); setCreateError(''); }} className="p-1 rounded hover:bg-muted">
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t('cron.taskNameLabel', 'Job Name')}</label>
              <input value={createName} onChange={e => setCreateName(e.target.value)}
                placeholder={t('cron.taskNamePlaceholder', 'My scheduled task')}
                className="w-full px-3 py-2 rounded-lg border bg-background text-sm outline-none focus:border-primary" />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t('cron.scheduleRule', 'Schedule')} <span className="text-destructive">*</span></label>
              <input value={createSchedule} onChange={e => setCreateSchedule(e.target.value)}
                placeholder="30m / every 2h / 0 9 * * *"
                className="w-full px-3 py-2 rounded-lg border bg-background text-sm outline-none focus:border-primary" />
            </div>
          </div>
          <div className="mb-3">
            <label className="text-xs text-muted-foreground mb-1 block">{t('cron.executePrompt', 'Prompt')}</label>
            <textarea value={createPrompt} onChange={e => setCreatePrompt(e.target.value)} rows={2}
              placeholder={t('cron.promptPlaceholder', 'What should the agent do?')}
              className="w-full px-3 py-2 rounded-lg border bg-background text-sm outline-none focus:border-primary resize-none" />
          </div>
          {createError && <p className="text-xs text-destructive mb-2">{createError}</p>}
          <div className="flex gap-2">
            <button onClick={handleCreate} disabled={creating || !createSchedule.trim()}
              className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm hover:bg-primary/90 disabled:opacity-50 flex items-center gap-1">
              {creating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />}
              {t('cron.createBtn', 'Create')}
            </button>
            <button onClick={() => { setShowCreate(false); setCreateError(''); }}
              className="px-4 py-2 rounded-lg border text-sm hover:bg-muted">
              {t('cron.cancel', 'Cancel')}
            </button>
          </div>
        </div>
      )}
    </div>
  
      </PageLayout>
    );
}
