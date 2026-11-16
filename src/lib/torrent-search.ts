/**
 * 种子搜索·前端请求薄层（单一真源=服务端 src/api/download_search.py 适配层）
 *
 * 前端只做两件事：① 调后端 REST（搜索 + 源管理）② 结果行映射到 UI 卡片形状。
 * 源适配（内置 Internet Archive / Academic Torrents、自定义 RSS/JSON 解析、缓存、
 * 逐源并发/超时）全部在服务端实现——前端禁重复实现（单一真源铁律）。
 * 自定义源存服务端（POST/DELETE /search/sources），localStorage 不再是主存储。
 * 移动端（无 Electron/Tauri）天然走同一服务端搜索。
 */

import { getApiBaseUrl } from '@/lib/api-client';

export interface TorrentSearchResult {
  id: string;
  title: string;
  size: number;        // 字节，0=未知
  seeders: number;     // 做种数，-1=未知
  source: string;      // 来源名
  magnet?: string;     // 磁力链（可直接入任务）
  torrentUrl?: string; // .torrent 直链（可直接入任务）
  detailUrl?: string;
}

/** 源信息（服务端 /search/sources 行） */
export interface SearchSourceInfo {
  name: string;
  kind: string;      // 'internet-archive' | 'academic-torrents' | 'json' | 'rss'
  url: string;
  enabled: boolean;
  builtin: boolean;
}

/** 按源分组的搜索批（UI 分组/错误展示形状；source=服务端源名） */
export interface SearchSourceBatch {
  source: { id: string; name: string };
  results: TorrentSearchResult[];
  error?: string;
}

/** 服务端统一结果行（{title,size,seeders,source,url|magnet}） */
interface ServerSearchRow {
  title?: string;
  size?: number;
  seeders?: number;
  source?: string;
  url?: string;
  magnet?: string;
}

/** 服务端结果行 → UI 结果卡形状（url 按 .torrent 直链 / 详情页分流） */
export function toUiResult(row: ServerSearchRow, i: number): TorrentSearchResult {
  const url = row.url || '';
  const seeders = Number(row.seeders);
  return {
    id: `${row.source || 'src'}:${i}`,
    title: String(row.title || ''),
    size: Number(row.size || 0),
    seeders: Number.isFinite(seeders) && seeders > 0 ? seeders : -1,
    source: String(row.source || ''),
    magnet: row.magnet || (url.startsWith('magnet:') ? url : undefined),
    torrentUrl: /\.torrent([?#]|$)/i.test(url) ? url : undefined,
    detailUrl: url.startsWith('http') ? url : undefined,
  };
}

/**
 * 搜索（后端逐源并发 + 短 TTL 缓存 + 逐源错误隔离）→ 按源分组批。
 * 后端 errors[{source,error}] 并入对应批（单源失败不拖垮其它源，禁 fabricate）。
 */
export async function searchTorrents(query: string, limit = 20): Promise<SearchSourceBatch[]> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/download/search?query=${encodeURIComponent(query)}&limit=${limit}`
  );
  if (!res.ok) throw new Error(`search failed: HTTP ${res.status}`);
  const data = await res.json();
  const batches = new Map<string, SearchSourceBatch>();
  const batchFor = (name: string): SearchSourceBatch => {
    if (!batches.has(name)) batches.set(name, { source: { id: name, name }, results: [] });
    return batches.get(name)!;
  };
  ((data?.results || []) as ServerSearchRow[]).forEach((row, i) => {
    batchFor(String(row.source || 'unknown')).results.push(toUiResult(row, i));
  });
  ((data?.errors || []) as { source?: string; error?: string }[]).forEach((e) => {
    batchFor(String(e.source || 'unknown')).error = e.error || 'unknown error';
  });
  return [...batches.values()];
}

/** 源列表（内置 + 自定义，服务端存管） */
export async function listSearchSources(): Promise<SearchSourceInfo[]> {
  const res = await fetch(`${getApiBaseUrl()}/api/download/search/sources`);
  if (!res.ok) throw new Error(`list sources failed: HTTP ${res.status}`);
  const data = await res.json();
  return ((data?.sources || []) as any[]).map((s) => ({
    name: String(s.name || ''),
    kind: String(s.kind || ''),
    url: String(s.url || ''),
    enabled: Boolean(s.enabled),
    builtin: Boolean(s.builtin),
  }));
}

/** 增自定义源（服务端持久化；kind=json|rss，url 可含 {query} 占位符） */
export async function addSearchSource(name: string, kind: 'json' | 'rss', url: string): Promise<void> {
  const res = await fetch(`${getApiBaseUrl()}/api/download/search/sources`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, kind, url }),
  });
  if (!res.ok) throw new Error(`add source failed: HTTP ${res.status}`);
}

/** 删自定义源（服务端持久化） */
export async function removeSearchSource(name: string): Promise<void> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/download/search/sources/${encodeURIComponent(name)}`,
    { method: 'DELETE' }
  );
  if (!res.ok) throw new Error(`remove source failed: HTTP ${res.status}`);
}
