'use client';
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Download, FolderOpen, ArrowDown, CheckCircle, XCircle, Loader2, Plus,
  Magnet, Video, Settings, Zap, Link, Pause, Play, Clock, X,
  FileText, AppWindow, FileArchive, Film, AlertCircle, RefreshCw, ListChecks,
} from 'lucide-react';
import { getApiBaseUrl } from '@/lib/api-client';
import { useTranslation } from 'react-i18next';
import { PageLayout } from '@/components/page-layout';
import { LeftPanel } from '@/components/left-panel';
import { useAppStore } from '@/stores/app-store';

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

function formatDate(ts: number): string {
  if (!ts) return '';
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function detectUrlType(url: string): DownloadTask['type'] {
  if (url.startsWith('magnet:?')) return 'bt';
  if (url.startsWith('thunder://') || url.startsWith('flashget://') || url.startsWith('qqdl://')) return 'thunder';
  if (/youtube\.com|youtu\.be|bilibili\.com|b23\.tv|tiktok\.com|douyin\.com/i.test(url)) return 'video';
  return 'http';
}

/** 文件类型图标块（参考图：彩色圆角方块，PDF 红/安装包 蓝/压缩包 黄/视频 紫/BT 绿） */
function FileTile({ filename, type }: { filename: string; type: DownloadTask['type'] }) {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  let icon = FileText, color = 'text-muted-foreground', bg = 'bg-muted';
  if (ext === 'pdf') { icon = FileText; color = 'text-red-500'; bg = 'bg-red-500/10'; }
  else if (['exe', 'msi', 'dmg', 'pkg', 'deb', 'appimage'].includes(ext)) { icon = AppWindow; color = 'text-blue-500'; bg = 'bg-blue-500/10'; }
  else if (['zip', 'rar', '7z', 'tar', 'gz', 'bz2'].includes(ext)) { icon = FileArchive; color = 'text-amber-500'; bg = 'bg-amber-500/10'; }
  else if (type === 'video') { icon = Film; color = 'text-purple-500'; bg = 'bg-purple-500/10'; }
  else if (type === 'bt') { icon = Magnet; color = 'text-green-500'; bg = 'bg-green-500/10'; }
  return (
    <div className={`w-11 h-11 rounded-xl flex items-center justify-center shrink-0 ${bg}`}>
      {(() => { const I = icon; return <I className={`w-5 h-5 ${color}`} />; })()}
    </div>
  );
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

export function DownloadClient() {
  const { t } = useTranslation();
  const setPageSidebar = useAppStore((s) => s.setPageSidebar);
  const [tasks, setTasks] = useState<DownloadTask[]>([]);
  const [tab, setTab] = useState<'downloading' | 'done'>('downloading');
  const [showSettings, setShowSettings] = useState(false);
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

  /* ── 左侧功能列表（迅雷左列样式）：注册进 openface LeftPanel 槽 ── */
  useEffect(() => {
    setPageSidebar(
      <div className="flex flex-col h-full">
        <div className="p-3">
          <button
            onClick={() => { setAdding(true); setShowSettings(false); }}
            className="w-full px-3 py-2 rounded-lg bg-primary text-primary-foreground flex items-center justify-center gap-2 text-xs lg:text-sm hover:bg-primary/90"
          >
            <Plus className="w-4 h-4" /> {t('download.newDownload', '新建下载')}
          </button>
        </div>
        <nav className="px-2 space-y-0.5">
          {[
            { key: 'all', icon: ListChecks, label: t('download.allTasks', '全部任务'), count: tasks.length, selected: !showSettings, onClick: () => setShowSettings(false) },
            { key: 'settings', icon: Settings, label: t('download.settings', '设置'), count: 0, selected: showSettings, onClick: () => setShowSettings(true) },
          ].map(item => (
            <button
              key={item.key}
              onClick={item.onClick}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs lg:text-sm transition-colors ${
                item.selected ? 'bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              }`}
            >
              <item.icon className="w-4 h-4 shrink-0" />
              <span className="flex-1 text-left truncate">{item.label}</span>
              {item.count > 0 && (
                <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${item.selected ? 'bg-primary/20 text-primary' : 'bg-muted text-muted-foreground'}`}>{item.count}</span>
              )}
            </button>
          ))}
        </nav>
      </div>
    );
    return () => setPageSidebar(null);
  }, [showSettings, tasks.length, setPageSidebar, t]);

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
        setTab('downloading');
        loadTasks();
      }
    } catch {}
    setLoading(false);
  };

  /* ── 暂停/继续/重试/删除：全部打真 API ── */
  const pauseResume = async (task: DownloadTask) => {
    try {
      await fetch(`${getApiBaseUrl()}/api/download/tasks/${encodeURIComponent(task.id)}/${task.status === 'paused' ? 'resume' : 'pause'}`, { method: 'POST' });
      loadTasks();
    } catch {}
  };

  const retryTask = async (task: DownloadTask) => {
    try {
      await fetch(`${getApiBaseUrl()}/api/download/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: task.url, resume: true }),
      });
      setTab('downloading');
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
  const shown = tab === 'downloading' ? activeTasks : doneTasks;

  /* ── 任务行（参考图：图标块｜文件名+meta｜行尾图标按钮） ── */
  const TaskRow = ({ task }: { task: DownloadTask }) => (
    <div className="px-3 lg:px-4 py-3 flex items-center gap-3 hover:bg-muted/40 transition-colors border-b border-border/40 last:border-b-0">
      <FileTile filename={task.filename} type={task.type} />
      <div className="flex-1 min-w-0">
        <div className={`text-xs lg:text-sm font-medium truncate ${task.status === 'error' ? 'text-muted-foreground' : ''}`}>
          {task.filename}
        </div>
        {task.status === 'done' ? (
          /* 参考图 meta 行：共 1 项 › · 时间 · 大小 */
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground mt-1">
            <span>共 1 项 ›</span>
            <span>{formatDate(task.addedAt)}</span>
            <span>{formatBytes(task.totalBytes || task.downloadedBytes)}</span>
          </div>
        ) : task.status === 'error' ? (
          /* 参考图错误行：红色错误信息 */
          <div className="flex items-center gap-1.5 text-[11px] text-red-500 mt-1">
            <AlertCircle className="w-3 h-3 shrink-0" />
            <span className="truncate">{task.error || t('download.failed', '下载失败')}</span>
          </div>
        ) : (
          /* 进行中：进度条 + 速度/大小/ETA */
          <>
            <div className="flex items-center gap-2 mt-1.5">
              <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden max-w-md">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${task.status === 'paused' ? 'bg-yellow-500' : 'bg-primary'}`}
                  style={{ width: `${task.progress || 0}%` }}
                />
              </div>
              <span className="text-[10px] text-muted-foreground shrink-0">{task.progress || 0}%</span>
            </div>
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground mt-1">
              {task.status === 'paused' && <span className="text-yellow-500 flex items-center gap-1"><Pause className="w-3 h-3" />{t('download.paused', '已暂停')}</span>}
              {task.speed > 0 && <span className="flex items-center gap-1 text-primary"><ArrowDown className="w-3 h-3" />{formatSpeed(task.speed)}</span>}
              <span>{formatBytes(task.downloadedBytes)}{task.totalBytes > 0 ? ` / ${formatBytes(task.totalBytes)}` : ''}</span>
              {task.eta > 0 && <span>ETA {formatEta(task.eta)}</span>}
            </div>
          </>
        )}
      </div>
      {/* 行尾图标按钮（参考图：↺/→/×） */}
      <div className="flex items-center gap-1 shrink-0">
        {task.status === 'error' && (
          <button
            onClick={() => retryTask(task)}
            title={t('download.retry', '重试')}
            className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        )}
        {task.status === 'done' && (
          <button
            onClick={() => navigator.clipboard?.writeText(task.dest || task.url)}
            title={t('download.copyPath', '复制文件路径')}
            className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground"
          >
            <FolderOpen className="w-4 h-4" />
          </button>
        )}
        {task.status !== 'done' && task.status !== 'error' && (
          <button
            onClick={() => pauseResume(task)}
            title={task.status === 'paused' ? t('download.resume', '继续') : t('download.pause', '暂停')}
            className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground"
          >
            {task.status === 'paused' ? <Play className="w-4 h-4" /> : <Pause className="w-4 h-4" />}
          </button>
        )}
        <button
          onClick={() => removeTask(task.id)}
          title={t('download.delete', '删除')}
          className="p-1.5 rounded-lg hover:bg-red-500/10 text-muted-foreground hover:text-red-500"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  );

  return (
    <PageLayout title={t('download.title', '下载')}>
      <div className="flex flex-col h-full overflow-hidden">
        {/* ── 标签行（下载中 | 已下载 圆角高亮切换，选中项禁下划线） ── */}
        <div className="flex items-center gap-1.5 px-3 lg:px-4 py-2 border-b border-border shrink-0">
          {([['downloading', t('download.downloading', '下载中'), activeTasks.length], ['done', t('download.done', '已下载'), doneTasks.length]] as const).map(([key, label, count]) => (
            <button
              key={key}
              onClick={() => { setTab(key); setShowSettings(false); }}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-sm lg:text-base font-medium rounded-lg transition-colors ${
                !showSettings && tab === key ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              }`}
            >
              {label}
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${!showSettings && tab === key ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'}`}>{count}</span>
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2 pb-1.5">
            <button
              onClick={() => { setAdding(true); setShowSettings(false); }}
              className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 flex items-center gap-1.5"
            >
              <Plus className="w-3.5 h-3.5" /> {t('download.newDownload', '新建下载')}
            </button>
            <button
              onClick={() => setShowSettings(s => !s)}
              title={t('download.settings', '设置')}
              className={`p-1.5 rounded-lg border transition-colors ${showSettings ? 'bg-primary/10 text-primary border-primary/30' : 'text-muted-foreground hover:bg-muted'}`}
            >
              <Settings className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* 新建下载输入条 */}
        {adding && !showSettings && (
          <div className="p-3 border-b border-border bg-card/60 shrink-0">
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
                {newUrl && <span className="absolute right-2.5 top-1/2 -translate-y-1/2"><span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400">{urlType.toUpperCase()}</span></span>}
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

        {/* ── 内容区 ── */}
        <div className="flex-1 overflow-y-auto">
          {showSettings ? (
            /* 设置 */
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
                  onBlur={() => saveConfig('speed-limit', config.speed_limit.toString())}
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
          ) : shown.length === 0 ? (
            <div className="text-center py-16 text-muted-foreground">
              <Download className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p className="text-sm">
                {tab === 'downloading'
                  ? t('download.noActive', '没有进行中的任务，点右上角「新建下载」开始')
                  : t('download.noDone', '还没有完成的下载')}
              </p>
            </div>
          ) : (
            shown.map(task => <TaskRow key={task.id} task={task} />)
          )}
        </div>
      </div>
    </PageLayout>
  );
}
