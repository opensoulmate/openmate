/**
 * 下载引擎接口层（双目标：本机 / 后端）
 *
 *   DownloadEngine（add/pause/resume/remove/list）
 *     ├─ LocalEngineAdapter  → window.__TAURI__ invoke → Rust → aria2 sidecar（本地 JSON-RPC）
 *     │    Tauri 不存在（浏览器/移动端）时 isAvailable()=false，UI 隐藏本机模式，优雅降级。
 *     └─ RemoteEngineAdapter → REST（getApiBaseUrl()）→ 后端 aria2 服务（现有 /api/download/*）
 *
 * 任务模型带 origin: 'local' | 'remote'，列表双源汇总，行内徽标区分。
 * 注：LocalEngineAdapter 的 Rust 命令（download_*）为接口预留，src-tauri 侧后续落地。
 */
import { getApiBaseUrl } from '@/lib/api-client';

export type DownloadOrigin = 'local' | 'remote';
export type DownloadStatus = 'pending' | 'downloading' | 'paused' | 'done' | 'error';
export type DownloadType = 'http' | 'bt' | 'video' | 'thunder';

export interface DownloadTask {
  id: string;
  url: string;
  filename: string;
  dest: string;
  totalBytes: number;
  downloadedBytes: number;
  speed: number;
  eta: number;
  progress: number;
  status: DownloadStatus;
  type: DownloadType;
  error?: string;
  addedAt: number;
  origin: DownloadOrigin;
}

export interface AddDownloadInput {
  url: string;
  filename?: string;
}

export interface DownloadEngine {
  readonly origin: DownloadOrigin;
  /** 引擎是否可用（本机引擎在非 Tauri 环境恒 false） */
  isAvailable(): boolean;
  list(): Promise<DownloadTask[]>;
  add(input: AddDownloadInput): Promise<void>;
  pause(id: string): Promise<void>;
  resume(id: string): Promise<void>;
  remove(id: string): Promise<void>;
}

export function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

export function detectUrlType(url: string): DownloadType {
  if (url.startsWith('magnet:?')) return 'bt';
  if (url.startsWith('thunder://') || url.startsWith('flashget://') || url.startsWith('qqdl://')) return 'thunder';
  if (/\.torrent([?#]|$)/i.test(url)) return 'bt';
  if (/youtube\.com|youtu\.be|bilibili\.com|b23\.tv|tiktok\.com|douyin\.com/i.test(url)) return 'video';
  return 'http';
}

/** 后端任务行（native to_dict / plugin_tasks 行）→ 前端统一结构 */
export function normalizeTask(row: any, origin: DownloadOrigin): DownloadTask {
  let payload: any = row?.payload;
  if (typeof payload === 'string') {
    try { payload = JSON.parse(payload); } catch { payload = {}; }
  }
  const src = { ...(payload || {}), ...row };
  const url: string = src.url || '';
  const dest: string = src.dest || '';
  const filename = dest.split('/').filter(Boolean).pop() || url.split('/').pop()?.split('?')[0] || 'download';
  const statusStr = String(src.status || 'downloading');
  const status: DownloadStatus =
    statusStr === 'done' ? 'done'
    : statusStr === 'error' ? 'error'
    : statusStr === 'paused' ? 'paused'
    : statusStr === 'pending' ? 'pending'
    : 'downloading';
  // 非传输态清空速度 / ETA
  const transferring = status === 'downloading';
  return {
    id: String(row?.id || row?.task_id || dest || url),
    url, dest, filename,
    totalBytes: Number(src.total_bytes || 0),
    downloadedBytes: Number(src.downloaded_bytes || 0),
    speed: transferring ? Number(src.speed || 0) : 0,
    eta: transferring ? Number(src.eta || 0) : 0,
    progress: Number(src.progress_pct || 0),
    status,
    type: detectUrlType(url),
    error: src.error || undefined,
    addedAt: Number(src.added_at || src.started_at || 0) * (Number(src.added_at || 0) < 1e12 ? 1000 : 1),
    origin,
  };
}

/* ── 本机引擎：window.__TAURI__ invoke 包装（aria2 sidecar，接口预留） ── */

type TauriInvoke = <T = unknown>(cmd: string, args?: Record<string, unknown>) => Promise<T>;

function getTauriInvoke(): TauriInvoke | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as {
    __TAURI__?: { core?: { invoke?: TauriInvoke } };
    __TAURI_INTERNALS__?: { invoke?: TauriInvoke };
  };
  return w.__TAURI__?.core?.invoke ?? w.__TAURI_INTERNALS__?.invoke ?? null;
}

/** 本机引擎是否可用（Tauri 不存在时 UI 隐藏本机模式，只留后端） */
export function isLocalEngineAvailable(): boolean {
  return getTauriInvoke() !== null;
}

export class LocalEngineAdapter implements DownloadEngine {
  readonly origin: DownloadOrigin = 'local';

  isAvailable(): boolean {
    return isLocalEngineAvailable();
  }

  private invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
    const inv = getTauriInvoke();
    if (!inv) return Promise.reject(new Error('local engine unavailable (no Tauri runtime)'));
    return inv<T>(cmd, args);
  }

  async list(): Promise<DownloadTask[]> {
    const rows = await this.invoke<any[]>('download_list');
    return (rows || []).map((r) => normalizeTask(r, 'local'));
  }

  add(input: AddDownloadInput): Promise<void> {
    return this.invoke<void>('download_add', { url: input.url, filename: input.filename ?? null });
  }

  pause(id: string): Promise<void> {
    return this.invoke<void>('download_pause', { id });
  }

  resume(id: string): Promise<void> {
    return this.invoke<void>('download_resume', { id });
  }

  remove(id: string): Promise<void> {
    return this.invoke<void>('download_remove', { id });
  }
}

/* ── 后端引擎：现有 REST（getApiBaseUrl() 路径） ── */

export class RemoteEngineAdapter implements DownloadEngine {
  readonly origin: DownloadOrigin = 'remote';

  isAvailable(): boolean {
    return true;
  }

  async list(): Promise<DownloadTask[]> {
    const res = await fetch(`${getApiBaseUrl()}/api/download/tasks`);
    if (!res.ok) return [];
    const data = await res.json();
    return [
      ...((data.tasks || []) as any[]).map((r) => normalizeTask(r, 'remote')),
      ...((data.plugin_tasks || []) as any[]).map((r) => normalizeTask(r, 'remote')),
    ];
  }

  async add(input: AddDownloadInput): Promise<void> {
    const res = await fetch(`${getApiBaseUrl()}/api/download/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: input.url, resume: true }),
    });
    if (!res.ok) throw new Error(`add failed: ${res.status}`);
  }

  async pause(id: string): Promise<void> {
    await fetch(`${getApiBaseUrl()}/api/download/tasks/${encodeURIComponent(id)}/pause`, { method: 'POST' });
  }

  async resume(id: string): Promise<void> {
    await fetch(`${getApiBaseUrl()}/api/download/tasks/${encodeURIComponent(id)}/resume`, { method: 'POST' });
  }

  async remove(id: string): Promise<void> {
    await fetch(`${getApiBaseUrl()}/api/download/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' });
  }
}

export const localEngine: DownloadEngine = new LocalEngineAdapter();
export const remoteEngine: DownloadEngine = new RemoteEngineAdapter();

export function engineFor(origin: DownloadOrigin): DownloadEngine {
  return origin === 'local' ? localEngine : remoteEngine;
}

/** 双源汇总：本机 + 后端任务合并（单侧失败不拖垮另一侧），按添加时间倒序 */
export async function listAllTasks(): Promise<DownloadTask[]> {
  const calls: Promise<DownloadTask[]>[] = [remoteEngine.list()];
  if (isLocalEngineAvailable()) calls.push(localEngine.list());
  const settled = await Promise.allSettled(calls);
  const merged: DownloadTask[] = [];
  for (const s of settled) if (s.status === 'fulfilled') merged.push(...s.value);
  merged.sort((a, b) => b.addedAt - a.addedAt);
  return merged;
}

/** 新建任务目标记忆（上次选择；未选择过时默认后端） */
const TARGET_KEY = 'openmate-download-target';

export function readStoredTarget(): DownloadOrigin {
  if (typeof window === 'undefined') return 'remote';
  const v = localStorage.getItem(TARGET_KEY);
  return v === 'local' && isLocalEngineAvailable() ? 'local' : 'remote';
}

export function storeTarget(target: DownloadOrigin): void {
  if (typeof window !== 'undefined') localStorage.setItem(TARGET_KEY, target);
}
