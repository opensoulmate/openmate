/**
 * 下载引擎接口层（双目标：本机 / 后端）
 *
 *   DownloadEngine（add/pause/resume/remove/list）
 *     ├─ LocalEngineAdapter  → Electron preload 桥（window.openmateDesktop.downloadEngine.getInfo）
 *     │    → 直连 Python 引擎 REST（127.0.0.1:port + token）。桥只透传端点，下载逻辑
 *     │    全在 Python 引擎（core/download_engine）侧——JS/TS 仅 fetch 转发薄层。
 *     │    Electron 桥不存在（浏览器/移动端）时 isAvailable()=false，UI 隐藏本机模式。
 *     └─ RemoteEngineAdapter → REST（getApiBaseUrl()）→ 后端引擎服务（现有 /api/download/*）
 *
 * 任务模型带 origin: 'local' | 'remote'，列表双源汇总，行内徽标区分。
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

/* ── 本机引擎：Electron preload 桥拿端点（port/token）→ 直连引擎 REST ── */

/** Electron preload 桥（desktop/preload.js openmateDesktop.downloadEngine）——只透传端点 */
export interface DesktopEngineBridge {
  getInfo(): Promise<{ port: number; token: string; pid?: number; origin?: string } | null>;
}

function getDesktopBridge(): DesktopEngineBridge | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as { openmateDesktop?: { downloadEngine?: DesktopEngineBridge } };
  return w.openmateDesktop?.downloadEngine ?? null;
}

/** 本机引擎是否可用（Electron 桥不存在时 UI 隐藏本机模式，只留后端） */
export function isLocalEngineAvailable(): boolean {
  return getDesktopBridge() !== null;
}

/** 引擎任务行（core/download_engine to_dict，camelCase）→ 前端统一结构 */
function fromEngineTask(t: any): DownloadTask {
  const statusStr = String(t?.status || 'pending');
  const status: DownloadStatus =
    statusStr === 'done' ? 'done'
    : statusStr === 'error' ? 'error'
    : statusStr === 'paused' ? 'paused'
    : statusStr === 'pending' ? 'pending'
    : 'downloading';
  return {
    id: String(t?.id || ''),
    url: String(t?.url || ''),
    dest: String(t?.dest || ''),
    filename: String(t?.filename || ''),
    totalBytes: Number(t?.totalBytes || 0),
    downloadedBytes: Number(t?.downloadedBytes || 0),
    speed: status === 'downloading' ? Number(t?.speed || 0) : 0,
    eta: status === 'downloading' ? Number(t?.eta || 0) : 0,
    progress: Number(t?.progress || 0),
    status,
    type: detectUrlType(String(t?.url || '')),
    error: t?.error || undefined,
    addedAt: Number(t?.addedAt || 0) * (Number(t?.addedAt || 0) < 1e12 ? 1000 : 1),
    origin: 'local',
  };
}

export class LocalEngineAdapter implements DownloadEngine {
  readonly origin: DownloadOrigin = 'local';
  private endpoint: { base: string; token: string } | null = null;

  isAvailable(): boolean {
    return isLocalEngineAvailable();
  }

  /** 端点来自 preload 桥（main 进程 spawn 引擎的握手结果），此后纯 fetch 直连引擎 REST */
  private async api<T>(method: string, path: string, body?: unknown): Promise<T> {
    const bridge = getDesktopBridge();
    if (!bridge) throw new Error('local engine unavailable (no Electron preload bridge)');
    if (!this.endpoint) {
      const info = await bridge.getInfo();
      if (!info?.port) throw new Error('local engine unavailable (engine not started)');
      this.endpoint = { base: `http://127.0.0.1:${info.port}`, token: info.token };
    }
    const doFetch = () =>
      fetch(`${this.endpoint!.base}${path}`, {
        method,
        headers: {
          'Content-Type': 'application/json',
          'X-Auth-Token': this.endpoint!.token,
          Authorization: 'Bearer ' + this.endpoint!.token,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    let res = await doFetch();
    if (!res.ok && this.endpoint) {
      // 引擎可能已重启换端口：重取握手信息重试一次
      this.endpoint = null;
      const info = await bridge.getInfo();
      if (!info?.port) throw new Error(`local engine ${method} ${path} failed: ${res.status}`);
      this.endpoint = { base: `http://127.0.0.1:${info.port}`, token: info.token };
      res = await doFetch();
    }
    if (!res.ok) throw new Error(`local engine ${method} ${path} failed: ${res.status}`);
    return (await res.json()) as T;
  }

  async list(): Promise<DownloadTask[]> {
    const data = await this.api<{ tasks: any[] }>('GET', '/api/tasks');
    return (data.tasks || []).map(fromEngineTask);
  }

  async add(input: AddDownloadInput): Promise<void> {
    await this.api('POST', '/api/tasks', {
      url: input.url,
      ...(input.filename ? { filename: input.filename } : {}),
    });
  }

  async pause(id: string): Promise<void> {
    await this.api('POST', `/api/tasks/${encodeURIComponent(id)}/pause`);
  }

  async resume(id: string): Promise<void> {
    await this.api('POST', `/api/tasks/${encodeURIComponent(id)}/resume`);
  }

  async remove(id: string): Promise<void> {
    await this.api('POST', `/api/tasks/${encodeURIComponent(id)}/remove`, { remove_files: false });
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
