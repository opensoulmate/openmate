'use client';
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Download, FolderOpen, ArrowDown, CheckCircle, XCircle, Loader2, Plus,
  Magnet, Video, Settings, Zap, Link, Pause, Play, Clock, X,
  FileText, AppWindow, FileArchive, Film, AlertCircle, RefreshCw, ListChecks,
  Search, Server, HardDrive,
} from 'lucide-react';
import { getApiBaseUrl } from '@/lib/api-client';
import { useTranslation } from 'react-i18next';
import { PageLayout } from '@/components/page-layout';
import { LeftPanel } from '@/components/left-panel';
import { useAppStore } from '@/stores/app-store';
import {
  DownloadTask, DownloadOrigin, engineFor, isLocalEngineAvailable, listAllTasks,
} from '@/lib/download-engine';
import { NewDownloadDialog } from './new-download-dialog';
import { TorrentSearchPanel, CustomSourceSettings } from './torrent-search-panel';

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

/** 任务行来源徽标（行内：本机=HardDrive 蓝 / 后端=Server 紫，低饱和分层） */
function OriginBadge({ origin }: { origin: DownloadOrigin }) {
  const { t } = useTranslation();
  const isLocal = origin === 'local';
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full shrink-0 ${isLocal ? 'bg-sky-500/10 text-sky-500' : 'bg-violet-500/10 text-violet-500'}`}>
      {isLocal ? <HardDrive className="w-2.5 h-2.5" /> : <Server className="w-2.5 h-2.5" />}
      {isLocal ? t('download.targetLocal', '本机') : t('download.targetRemote', '后端')}
    </span>
  );
}

export function DownloadClient() {
  const { t } = useTranslation();
  const setPageSidebar = useAppStore((s) => s.setPageSidebar);
  const [tasks, setTasks] = useState<DownloadTask[]>([]);
  const [tab, setTab] = useState<'all' | 'active' | 'done' | 'error'>('all');
  const [view, setView] = useState<'tasks' | 'search' | 'settings'>('tasks');
  const [adding, setAdding] = useState(false);
  const [localDir, setLocalDir] = useState('~/Downloads');
  const [config, setConfig] = useState<Config>({ threads: 8, speed_limit: 0, download_dir: '~/Downloads', proxy: null });
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /* ── 双源汇总：本机 + 后端任务（引擎适配层，单侧失败不拖垮另一侧），2s 轮询 ── */
  const loadTasks = useCallback(async () => {
    try {
      setTasks(await listAllTasks());
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
            onClick={() => { setAdding(true); setView('tasks'); }}
            className="w-full px-3 py-2 rounded-lg bg-primary text-primary-foreground flex items-center justify-center gap-2 text-xs lg:text-sm hover:bg-primary/90"
          >
            <Plus className="w-4 h-4" /> {t('download.newDownload', '新建下载')}
          </button>
        </div>
        <nav className="px-2 space-y-0.5">
          {[
            { key: 'tasks', icon: ListChecks, label: t('download.allTasks', '全部任务'), count: tasks.length, selected: view === 'tasks', onClick: () => setView('tasks') },
            { key: 'search', icon: Search, label: t('download.torrentSearch', '种子搜索'), count: 0, selected: view === 'search', onClick: () => setView('search') },
            { key: 'settings', icon: Settings, label: t('download.settings', '设置'), count: 0, selected: view === 'settings', onClick: () => setView('settings') },
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
  }, [view, tasks.length, setPageSidebar, t]);

  const loadConfig = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBaseUrl()}/api/download/config`);
      if (res.ok) setConfig(await res.json());
    } catch {}
  }, []);

  useEffect(() => { loadConfig(); }, [loadConfig]);

  /* ── 本机下载目录（桌面端本地记忆；后端目录走 config） ── */
  useEffect(() => {
    setLocalDir(localStorage.getItem('openmate-download-local-dir') || '~/Downloads');
  }, []);

  const saveLocalDir = (v: string) => {
    setLocalDir(v);
    localStorage.setItem('openmate-download-local-dir', v);
  };

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

  /* ── 暂停/继续/重试/删除：按任务 origin 分发到本机/后端引擎 ── */
  const pauseResume = async (task: DownloadTask) => {
    try {
      const engine = engineFor(task.origin);
      if (task.status === 'paused') await engine.resume(task.id); else await engine.pause(task.id);
      loadTasks();
    } catch {}
  };

  const retryTask = async (task: DownloadTask) => {
    try {
      await engineFor(task.origin).add({ url: task.url });
      setTab('active');
      loadTasks();
    } catch {}
  };

  const removeTask = async (task: DownloadTask) => {
    try {
      await engineFor(task.origin).remove(task.id);
      loadTasks();
    } catch {}
  };

  const activeTasks = tasks.filter(t => t.status === 'downloading' || t.status === 'paused' || t.status === 'pending');
  const doneTasks = tasks.filter(t => t.status === 'done');
  const errorTasks = tasks.filter(t => t.status === 'error');
  const shown = tab === 'all' ? tasks : tab === 'active' ? activeTasks : tab === 'done' ? doneTasks : errorTasks;

  /* ── 任务行（参考图：图标块｜文件名+meta｜行尾图标按钮） ── */
  const TaskRow = ({ task }: { task: DownloadTask }) => (
    <div className="px-3 lg:px-4 py-3 flex items-center gap-3 hover:bg-muted/40 transition-colors border-b border-border/40 last:border-b-0">
      <FileTile filename={task.filename} type={task.type} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`text-xs lg:text-sm font-medium truncate ${task.status === 'error' ? 'text-muted-foreground' : ''}`}>
            {task.filename}
          </span>
          <OriginBadge origin={task.origin} />
        </div>
        {task.status === 'done' ? (
          /* 参考图 meta 行：状态 · 共 1 项 › · 时间 · 大小 */
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground mt-1">
            <span className="text-green-500">{t('download.statusDone', '已完成')}</span>
            <span>共 1 项 ›</span>
            <span>{formatDate(task.addedAt)}</span>
            <span>{formatBytes(task.totalBytes || task.downloadedBytes)}</span>
          </div>
        ) : task.status === 'error' ? (
          /* 参考图错误行：状态文字 + 红色错误信息 */
          <div className="flex items-center gap-1.5 text-[11px] text-red-500 mt-1">
            <AlertCircle className="w-3 h-3 shrink-0" />
            <span>{t('download.statusError', '失败')}</span>
            <span className="truncate">{task.error || t('download.failed', '下载失败')}</span>
          </div>
        ) : (
          /* 进行中：进度条 + 状态文字 +（仅传输态）速度/大小/ETA */
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
              <span className={`flex items-center gap-1 ${task.status === 'paused' ? 'text-yellow-500' : ''}`}>
                {task.status === 'paused' && <Pause className="w-3 h-3" />}
                {task.status === 'pending'
                  ? t('download.statusPending', '等待中')
                  : task.status === 'paused'
                  ? t('download.statusPaused', '已暂停')
                  : t('download.statusDownloading', '下载中')}
              </span>
              {/* 非传输态清空速度 / ETA：仅 downloading 渲染（normalizeTask 已归零） */}
              {task.status === 'downloading' && task.speed > 0 && <span className="flex items-center gap-1 text-primary"><ArrowDown className="w-3 h-3" />{formatSpeed(task.speed)}</span>}
              <span>{formatBytes(task.downloadedBytes)}{task.totalBytes > 0 ? ` / ${formatBytes(task.totalBytes)}` : ''}</span>
              {task.status === 'downloading' && task.eta > 0 && <span>ETA {formatEta(task.eta)}</span>}
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
          onClick={() => removeTask(task)}
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
        {/* ── 标签行（全部 | 进行中 | 已完成 | 错误 四分类圆角高亮切换，选中项禁下划线） ── */}
        <div className="flex items-center gap-1.5 px-3 lg:px-4 py-2 border-b border-border shrink-0">
          {view === 'tasks' && ([['all', t('download.tabAll', '全部'), tasks.length], ['active', t('download.tabActive', '进行中'), activeTasks.length], ['done', t('download.tabDone', '已完成'), doneTasks.length], ['error', t('download.tabError', '错误'), errorTasks.length]] as const).map(([key, label, count]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-sm lg:text-base font-medium rounded-lg transition-colors ${
                tab === key ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              }`}
            >
              {label}
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${tab === key ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'}`}>{count}</span>
            </button>
          ))}
          {view !== 'tasks' && (
            <span className="text-sm lg:text-base font-medium">
              {view === 'search' ? t('download.torrentSearch', '种子搜索') : t('download.settings', '设置')}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2 pb-1.5">
            <button
              onClick={() => setAdding(true)}
              className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 flex items-center gap-1.5"
            >
              <Plus className="w-3.5 h-3.5" /> {t('download.newDownload', '新建下载')}
            </button>
            <button
              onClick={() => setView(view === 'search' ? 'tasks' : 'search')}
              title={t('download.torrentSearch', '种子搜索')}
              className={`p-1.5 rounded-lg border transition-colors ${view === 'search' ? 'bg-primary/10 text-primary border-primary/30' : 'text-muted-foreground hover:bg-muted'}`}
            >
              <Search className="w-4 h-4" />
            </button>
            <button
              onClick={() => setView(view === 'settings' ? 'tasks' : 'settings')}
              title={t('download.settings', '设置')}
              className={`p-1.5 rounded-lg border transition-colors ${view === 'settings' ? 'bg-primary/10 text-primary border-primary/30' : 'text-muted-foreground hover:bg-muted'}`}
            >
              <Settings className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* 新建下载弹层（多行批量 / 磁力 / .torrent / 目标选择） */}
        <NewDownloadDialog open={adding} onClose={() => setAdding(false)} onAdded={loadTasks} />



        {/* ── 内容区 ── */}
        {view === 'search' ? (
          /* 种子搜索面板（下载页内嵌面板，不建新页） */
          <div className="flex-1 min-h-0 overflow-hidden">
            <TorrentSearchPanel />
          </div>
        ) : (
        <div className="flex-1 overflow-y-auto">
          {view === 'settings' ? (
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
                <label className="text-xs lg:text-sm font-medium mb-1.5 block">{t('download.downloadDirLocal', '本机下载目录')}</label>
                <div className="flex items-center gap-2">
                  <input
                    value={localDir}
                    onChange={e => setLocalDir(e.target.value)}
                    onBlur={() => saveLocalDir(localDir)}
                    disabled={!isLocalEngineAvailable()}
                    placeholder="~/Downloads"
                    className="flex-1 px-3 py-2 rounded-lg bg-muted text-xs lg:text-sm outline-none border border-border focus:border-primary disabled:opacity-50"
                  />
                  <FolderOpen className="w-4 h-4 text-muted-foreground shrink-0" />
                </div>
                {!isLocalEngineAvailable() && (
                  <p className="text-[10px] text-muted-foreground mt-1">{t('download.localDirHint', '本机目录仅桌面端（Electron）可设置，浏览器/移动端只走后端')}</p>
                )}
              </div>
              <div>
                <label className="text-xs lg:text-sm font-medium mb-1.5 block">{t('download.downloadDirRemote', '后端下载目录')}</label>
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
              {/* 自定义搜索源增删（RSS/JSON） */}
              <CustomSourceSettings />
            </div>
          ) : shown.length === 0 ? (
            <div className="text-center py-16 text-muted-foreground">
              <Download className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p className="text-sm">
                {tab === 'error'
                  ? t('download.noError', '没有出错的任务')
                  : tab === 'done'
                  ? t('download.noDone', '还没有完成的下载')
                  : tab === 'active'
                  ? t('download.noActive', '没有进行中的任务，点右上角「新建下载」开始')
                  : t('download.noTasks', '暂无任务，点右上角「新建下载」开始')}
              </p>
            </div>
          ) : (
            shown.map(task => <TaskRow key={task.id} task={task} />)
          )}
        </div>
        )}
      </div>
    </PageLayout>
  );
}
