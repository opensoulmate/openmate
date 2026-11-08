'use client';
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Download, Trash2, FolderOpen, ArrowDown, CheckCircle, XCircle, Loader2, Plus,
  Magnet, Video, Settings, Zap, Link, Pause, Play, ListChecks, Clock, X,
} from 'lucide-react';
import { getApiBaseUrl } from '@/lib/api-client';
import { useTranslation } from 'react-i18next';
import { PageLayout } from '@/components/page-layout';

interface DownloadTask {
  id: string;
  url: string;
  filename: string;
  dest: string;
  totalBytes: number;
  downloadedBytes: number;
  speed: number;
  eta: number;
  progress: number;
  status: 'pending' | 'downloading' | 'paused' | 'done' | 'error';
  type: 'http' | 'bt' | 'video' | 'thunder';
  error?: string;
  addedAt: number;
}

interface Config {
  threads: number;
  speed_limit: number;
  download_dir: string;
  proxy: string | null;
}

function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatSpeed(bps: number): string {
  if (bps <= 0) return '--';
  return formatBytes(bps) + '/s';
}

function formatEta(s: number): string {
  if (s <= 0) return '--';
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${s % 60}s`;
  return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
}

function detectUrlType(url: string): DownloadTask['type'] {
  if (url.startsWith('magnet:?')) return 'bt';
  if (url.startsWith('thunder://') || url.startsWith('flashget://') || url.startsWith('qqdl://')) return 'thunder';
  if (/youtube\.com|youtu\.be|bilibili\.com|b23\.tv|tiktok\.com|douyin\.com/i.test(url)) return 'video';
  return 'http';
}

function TypeTag({ type }: { type: DownloadTask['type'] }) {
  const styles: Record<string, string> = {
    http: 'bg-blue-500/10 text-blue-400',
    bt: 'bg-green-500/10 text-emerald-400',
    video: 'bg-purple-500/10 text-purple-400',
    thunder: 'bg-orange-500/10 text-orange-400',
  };
  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${styles[type]}`}>{type.toUpperCase()}</span>;
}

/** 后端任务行（native to_dict / plugin_tasks 行）→ 前端统一结构 */
function normalizeTask(row: any): DownloadTask {
  let payload: any = row?.payload;
  if (typeof payload === 'string') {
    try { payload = JSON.parse(payload); } catch { payload = {}; }
  }
  const src = { ...(payload || {}), ...row };
  const url: string = src.url || '';
  const dest: string = src.dest || '';
  const filename = dest.split('/').filter(Boolean).pop() || url.split('/').pop()?.split('?')[0] || 'download';
  const status = String(src.status || 'downloading');
  return {
    id: String(row?.id || row?.task_id || dest || url),
    url, dest, filename,
    totalBytes: Number(src.total_bytes || 0),
    downloadedBytes: Number(src.downloaded_bytes || 0),
    speed: Number(src.speed || 0),
    eta: Number(src.eta || 0),
    progress: Number(src.progress_pct || 0),
    status: status === 'done' ? 'done' : status === 'error' ? 'error' : status === 'paused' ? 'paused' : 'downloading',
    type: detectUrlType(url),
    error: src.error || undefined,
    addedAt: Number(src.added_at || src.started_at || 0) * (Number(src.added_at || 0) < 1e12 ? 1000 : 1),
  };
}

type View = 'all' | 'downloading' | 'done' | 'settings';

export function DownloadClient() {
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<DownloadTask[]>([]);
  const [view, setView] = useState<View>('all');
  const [newUrl, setNewUrl] = useState('');
  const [adding, setAdding] = useState(false);
  const [loading, setLoading] = useState(false);
  const [config, setConfig] = useState<Config>({ threads: 8, speed_limit: 0, download_dir: '~/Downloads', proxy: null });
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /* ── 真源：后端 /tasks（native + plugin 两真源如实合并），2s 轮询 ── */
  const loadTasks = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBaseUrl()}/api/download/tasks`);
      if (!res.ok) return;
      const data = await res.json();
      const merged = [
        ...((data.tasks || []) as any[]).map(normalizeTask),
        ...((data.plugin_tasks || []) as any[]).map(normalizeTask),
      ];
      merged.sort((a, b) => b.addedAt - a.addedAt);
      setTasks(merged);
    } catch {}
  }, []);

  useEffect(() => {
    loadTasks();
    pollRef.current = setInterval(loadTasks, 2000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [loadTasks]);

  const loadConfig = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBaseUrl()}/api/download/config`);
      if (res.ok) setConfig(await res.json());
    } catch {}
  }, []);

  useEffect(() => { loadConfig(); }, [loadConfig]);

  const saveConfig = async (key: string, value: string) => {
    try {
      await fetch(`${getApiBaseUrl()}/api/download/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value }),
      });
      loadConfig();
    } catch {}
  };

  /* ── 新建下载：POST /download（异步真引擎，返回真 task_id） ── */
  const startDownload = async () => {
    if (!newUrl.trim()) return;
    setLoading(true);
    try {
      const res = await fetch(`${getApiBaseUrl()}/api/download/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: newUrl.trim(), resume: true }),
      });
      if (res.ok) {
        setNewUrl('');
        setAdding(false);
        setView('downloading');
        loadTasks();
      }
    } catch {}
    setLoading(false);
  };

  /* ── 暂停/继续/删除：全部打真 API，不再假改本地状态 ── */
  const pauseResume = async (task: DownloadTask) => {
    try {
      await fetch(`${getApiBaseUrl()}/api/download/tasks/${encodeURIComponent(task.id)}/${task.status === 'paused' ? 'resume' : 'pause'}`, { method: 'POST' });
      loadTasks();
    } catch {}
  };

  const removeTask = async (id: string) => {
    try {
      await fetch(`${getApiBaseUrl()}/api/download/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' });
      loadTasks();
    } catch {}
  };

  const activeTasks = tasks.filter(t => t.status !== 'done');
  const doneTasks = tasks.filter(t => t.status === 'done');
  const urlType = detectUrlType(newUrl);

  /* ── 左侧功能列表项（迅雷式） ── */
  const navItems: Array<{ key: View; icon: any; label: string; count?: number }> = [
    { key: 'all', icon: ListChecks, label: t('download.allTasks', '全部任务'), count: tasks.length },
    { key: 'downloading', icon: ArrowDown, label: t('download.downloading', '下载中'), count: activeTasks.length },
    { key: 'done', icon: CheckCircle, label: t('download.done', '已下载'), count: doneTasks.length },
    { key: 'settings', icon: Settings, label: t('download.settings', '设置') },
  ];

  /* ── 任务行（迅雷式：图标｜名称+进度｜速度/大小｜操作） ── */
  const TaskRow = ({ task }: { task: DownloadTask }) => (
    <div className="px-3 py-2.5 flex items-center gap-3 border-b border-border/50 last:border-b-0 hover:bg-muted/40 transition-colors">
      <div className="w-8 h-8 rounded-lg bg-muted flex items-center justify-center shrink-0">
        {task.status === 'downloading' && <Loader2 className="w-4 h-4 text-primary animate-spin" />}
        {task.status === 'paused' && <Pause className="w-4 h-4 text-yellow-500" />}
        {task.status === 'error' && <XCircle className="w-4 h-4 text-red-500" />}
        {task.status === 'done' && <CheckCircle className="w-4 h-4 text-green-500" />}
        {task.status === 'pending' && <Clock className="w-4 h-4 text-muted-foreground" />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs lg:text-sm font-medium truncate">{task.filename}</span>
          <TypeTag type={task.type} />
          {task.status === 'error' && task.error && (
            <span className="text-[10px] text-red-400 truncate max-w-[200px]">{task.error}</span>
          )}
        </div>
        {task.status !== 'done' ? (
          <div className="flex items-center gap-2 mt-1.5">
            <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-500 ${task.status === 'paused' ? 'bg-yellow-500' : task.status === 'error' ? 'bg-red-500' : 'bg-primary'}`}
                style={{ width: `${task.progress || 0}%` }}
              />
            </div>
            <span className="text-[10px] text-muted-foreground w-10 text-right shrink-0">{task.progress || 0}%</span>
          </div>
        ) : (
          <p className="text-[10px] text-muted-foreground truncate mt-1" title={task.dest}>{task.dest || task.url}</p>
        )}
      </div>
      <div className="text-right shrink-0 w-24 hidden sm:block">
        {task.status === 'done' ? (
          <span className="text-xs text-muted-foreground">{formatBytes(task.totalBytes || task.downloadedBytes)}</span>
        ) : (
          <>
            <div className="text-xs text-primary">{formatSpeed(task.speed)}</div>
            <div className="text-[10px] text-muted-foreground">
              {formatBytes(task.downloadedBytes)}{task.totalBytes > 0 ? ` / ${formatBytes(task.totalBytes)}` : ''} · {formatEta(task.eta)}
            </div>
          </>
        )}
      </div>
      <div className="flex items-center gap-1 shrink-0">
        {task.status !== 'done' && task.status !== 'error' && (
          <button
            onClick={() => pauseResume(task)}
            title={task.status === 'paused' ? t('download.resume', '继续') : t('download.pause', '暂停')}
            className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground"
          >
            {task.status === 'paused' ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
          </button>
        )}
        <button
          onClick={() => removeTask(task.id)}
          title={t('download.delete', '删除')}
          className="p-1.5 rounded-lg hover:bg-red-500/10 text-muted-foreground hover:text-red-500"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );

  const EmptyBlock = ({ hint }: { hint: string }) => (
    <div className="text-center py-12 text-muted-foreground">
      <Download className="w-10 h-10 mx-auto mb-2 opacity-30" />
      <p className="text-xs lg:text-sm">{hint}</p>
    </div>
  );

  return (
    <PageLayout title={t('download.title', '下载')}>
      <div className="flex h-full overflow-hidden">
        {/* ── 左侧：下载功能列表 ── */}
        <aside className="w-40 lg:w-48 shrink-0 border-r border-border flex flex-col bg-card/40">
          <div className="p-3">
            <button
              onClick={() => { setAdding(true); setView('all'); }}
              className="w-full px-3 py-2 rounded-lg bg-primary text-primary-foreground flex items-center justify-center gap-2 text-xs lg:text-sm hover:bg-primary/90"
            >
              <Plus className="w-4 h-4" /> {t('download.newDownload', '新建下载')}
            </button>
          </div>
          <nav className="flex-1 px-2 space-y-0.5 overflow-y-auto">
            {navItems.map(item => (
              <button
                key={item.key}
                onClick={() => { setView(item.key); if (item.key !== 'all') setAdding(false); }}
                className={`w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-xs lg:text-sm transition-colors ${
                  view === item.key ? 'bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                }`}
              >
                <item.icon className="w-4 h-4 shrink-0" />
                <span className="flex-1 text-left truncate">{item.label}</span>
                {item.count !== undefined && item.count > 0 && (
                  <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${view === item.key ? 'bg-primary/20 text-primary' : 'bg-muted text-muted-foreground'}`}>
                    {item.count}
                  </span>
                )}
              </button>
            ))}
          </nav>
        </aside>

        {/* ── 右侧：下载详细列表 ── */}
        <div className="flex-1 min-w-0 flex flex-col overflow-y-auto">
          {/* 新建下载输入条 */}
          {adding && (
            <div className="p-3 border-b border-border bg-card/60">
              <div className="flex flex-col sm:flex-row gap-2">
                <div className="flex-1 relative">
                  <input
                    value={newUrl}
                    onChange={e => setNewUrl(e.target.value)}
                    placeholder={t('download.urlPlaceholder', '输入下载链接（HTTP/BT/磁力/视频）')}
                    className="w-full px-3 py-2 pr-20 rounded-lg bg-muted text-xs lg:text-sm outline-none border border-border focus:border-primary"
                    onKeyDown={e => e.key === 'Enter' && startDownload()}
                    autoFocus
                  />
                  {newUrl && <span className="absolute right-2.5 top-1/2 -translate-y-1/2"><TypeTag type={urlType} /></span>}
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={startDownload}
                    disabled={loading || !newUrl.trim()}
                    className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-xs lg:text-sm font-medium hover:bg-primary/90 disabled:opacity-50 flex items-center gap-1.5"
                  >
                    {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
                    {t('download.download', '下载')}
                  </button>
                  <button
                    onClick={() => { setAdding(false); setNewUrl(''); }}
                    className="px-3 py-2 rounded-lg border text-xs lg:text-sm text-muted-foreground hover:bg-muted"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              </div>
              <div className="flex items-center gap-3 mt-2 text-[10px] text-muted-foreground">
                <span className="flex items-center gap-1"><Link className="w-3 h-3" /> HTTP/FTP</span>
                <span className="flex items-center gap-1"><Magnet className="w-3 h-3" /> BT/磁力</span>
                <span className="flex items-center gap-1"><Video className="w-3 h-3" /> YouTube/B站</span>
                <span className="flex items-center gap-1"><Zap className="w-3 h-3" /> thunder://</span>
              </div>
            </div>
          )}

          {view === 'settings' ? (
            /* ── 设置 ── */
            <div className="p-3 lg:p-6 space-y-5 max-w-xl">
              <div>
                <label className="text-xs lg:text-sm font-medium mb-1.5 block">{t('download.threads', '下载线程')} (1-32)</label>
                <div className="flex items-center gap-3">
                  <input
                    type="range" min={1} max={32} value={config.threads}
                    onChange={e => setConfig(c => ({ ...c, threads: parseInt(e.target.value) }))}
                    onMouseUp={() => saveConfig('threads', config.threads.toString())}
                    onTouchEnd={() => saveConfig('threads', config.threads.toString())}
                    className="flex-1 accent-primary"
                  />
                  <span className="text-xs w-8 text-right">{config.threads}</span>
                </div>
              </div>
              <div>
                <label className="text-xs lg:text-sm font-medium mb-1.5 block">{t('download.speedLimit', '速度限制')} (KB/s，0=不限)</label>
                <input
                  type="number" min={0} value={config.speed_limit}
                  onChange={e => setConfig(c => ({ ...c, speed_limit: parseInt(e.target.value) || 0 }))}
                  onBlur={() => saveConfig('speed_limit', config.speed_limit.toString())}
                  className="w-full px-3 py-2 rounded-lg bg-muted text-xs lg:text-sm outline-none border border-border focus:border-primary"
                />
              </div>
              <div>
                <label className="text-xs lg:text-sm font-medium mb-1.5 block">{t('download.downloadDir', '下载目录')}</label>
                <div className="flex items-center gap-2">
                  <input
                    value={config.download_dir}
                    onChange={e => setConfig(c => ({ ...c, download_dir: e.target.value }))}
                    onBlur={() => saveConfig('download-dir', config.download_dir)}
                    className="flex-1 px-3 py-2 rounded-lg bg-muted text-xs lg:text-sm outline-none border border-border focus:border-primary"
                  />
                  <FolderOpen className="w-4 h-4 text-muted-foreground shrink-0" />
                </div>
              </div>
              <div>
                <label className="text-xs lg:text-sm font-medium mb-1.5 block">{t('download.proxy', '代理')}</label>
                <input
                  value={config.proxy || ''}
                  placeholder="socks5://127.0.0.1:1080"
                  onChange={e => setConfig(c => ({ ...c, proxy: e.target.value || null }))}
                  onBlur={() => saveConfig('proxy', config.proxy || '')}
                  className="w-full px-3 py-2 rounded-lg bg-muted text-xs lg:text-sm outline-none border border-border focus:border-primary"
                />
              </div>
            </div>
          ) : (
            <>
              {/* ── 下载中 ── */}
              {view !== 'done' && (
                <section>
                  <div className="px-3 lg:px-4 py-2 flex items-center gap-2 text-xs font-medium text-muted-foreground bg-muted/40 border-b border-border sticky top-0 z-10">
                    <ArrowDown className="w-3.5 h-3.5 text-primary" />
                    {t('download.downloading', '下载中')}
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary/10 text-primary">{activeTasks.length}</span>
                  </div>
                  {activeTasks.length === 0 ? (
                    <EmptyBlock hint={t('download.noActive', '没有进行中的任务，点左上角「新建下载」开始')} />
                  ) : (
                    <div>{activeTasks.map(task => <TaskRow key={task.id} task={task} />)}</div>
                  )}
                </section>
              )}

              {/* ── 已下载 ── */}
              {view !== 'downloading' && (
                <section>
                  <div className="px-3 lg:px-4 py-2 flex items-center gap-2 text-xs font-medium text-muted-foreground bg-muted/40 border-b border-border sticky top-0 z-10">
                    <CheckCircle className="w-3.5 h-3.5 text-green-500" />
                    {t('download.done', '已下载')}
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-500/10 text-green-500">{doneTasks.length}</span>
                  </div>
                  {doneTasks.length === 0 ? (
                    <EmptyBlock hint={t('download.noDone', '还没有完成的下载')} />
                  ) : (
                    <div>{doneTasks.map(task => <TaskRow key={task.id} task={task} />)}</div>
                  )}
                </section>
              )}
            </>
          )}
        </div>
      </div>
    </PageLayout>
  );
}
