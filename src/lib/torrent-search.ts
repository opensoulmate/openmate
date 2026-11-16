/**
 * 种子搜索·可插拔源适配器（PluggableSearchSource）
 *   内置合法公共源：Internet Archive（advancedsearch JSON API）、Academic Torrents（apiv2 JSON API）
 *   用户自定义源：RSS / JSON（URL 模板含 {query}，JSON 支持字段点路径映射），存 localStorage 可增删
 * 单源失败不拖垮其它源（searchAll 逐源 allSettled）。
 */

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

export interface PluggableSearchSource {
  id: string;
  name: string;
  kind: 'builtin' | 'custom';
  search(query: string, signal?: AbortSignal): Promise<TorrentSearchResult[]>;
}

/* ── 内置源 1：Internet Archive（archive.org advancedsearch JSON API） ── */

class InternetArchiveSource implements PluggableSearchSource {
  readonly id = 'internet-archive';
  readonly name = 'Internet Archive';
  readonly kind = 'builtin' as const;

  async search(query: string, signal?: AbortSignal): Promise<TorrentSearchResult[]> {
    const q = `${query} AND mediatype:data`;
    const url =
      `https://archive.org/advancedsearch.php?q=${encodeURIComponent(q)}` +
      `&fl%5B%5D=identifier&fl%5B%5D=title&fl%5B%5D=item_size&fl%5B%5D=downloads&rows=20&page=1&output=json`;
    const res = await fetch(url, { signal });
    if (!res.ok) throw new Error(`Internet Archive HTTP ${res.status}`);
    const data = await res.json();
    const docs: any[] = data?.response?.docs || [];
    return docs.map((d) => {
      const identifier = String(d.identifier || '');
      return {
        id: `ia:${identifier}`,
        title: String(d.title || identifier),
        size: Number(d.item_size || 0),
        seeders: -1, // IA 不公开做种数（官方保种 + webseed）
        source: this.name,
        torrentUrl: identifier ? `https://archive.org/download/${identifier}/${identifier}_archive.torrent` : undefined,
        detailUrl: identifier ? `https://archive.org/details/${identifier}` : undefined,
      };
    });
  }
}

/* ── 内置源 2：Academic Torrents（academictorrents.com/apiv2/entries?search=） ── */

class AcademicTorrentsSource implements PluggableSearchSource {
  readonly id = 'academic-torrents';
  readonly name = 'Academic Torrents';
  readonly kind = 'builtin' as const;

  async search(query: string, signal?: AbortSignal): Promise<TorrentSearchResult[]> {
    const url = `https://academictorrents.com/apiv2/entries?search=${encodeURIComponent(query)}`;
    const res = await fetch(url, { signal });
    if (!res.ok) throw new Error(`Academic Torrents HTTP ${res.status}`);
    const json = await res.json();
    const entries: any[] = Array.isArray(json) ? json : json?.entries || json?.results || json?.data || [];
    return entries.map((e, i) => {
      const infohash = String(e.infohash || e.info_hash || '').toLowerCase();
      const name = String(e.name || e.title || infohash || `torrent-${i}`);
      return {
        id: `at:${infohash || i}`,
        title: name,
        size: Number(e.size || 0),
        seeders: Number.isFinite(Number(e.mirrors)) ? Number(e.mirrors) : Number.isFinite(Number(e.seeders)) ? Number(e.seeders) : -1,
        source: this.name,
        magnet: infohash ? `magnet:?xt=urn:btih:${infohash}&dn=${encodeURIComponent(name)}` : undefined,
        torrentUrl: infohash ? `https://academictorrents.com/download/${infohash}.torrent` : undefined,
        detailUrl: typeof e.url === 'string' && e.url.startsWith('http') ? e.url : undefined,
      };
    });
  }
}

/* ── 用户自定义源：RSS / JSON ── */

export interface CustomSourceConfig {
  id: string;
  name: string;
  kind: 'json' | 'rss';
  /** URL 模板，含 {query} 占位符 */
  url: string;
  /** JSON 字段点路径映射（可空，自动猜常见字段名） */
  listPath?: string;
  titlePath?: string;
  sizePath?: string;
  seedersPath?: string;
  linkPath?: string;
  magnetPath?: string;
}

const CUSTOM_SOURCES_KEY = 'openmate-search-custom-sources';

export function listCustomSourceConfigs(): CustomSourceConfig[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = JSON.parse(localStorage.getItem(CUSTOM_SOURCES_KEY) || '[]');
    return Array.isArray(raw) ? raw.filter((c: any) => c && c.id && c.url) : [];
  } catch {
    return [];
  }
}

export function saveCustomSourceConfigs(configs: CustomSourceConfig[]): void {
  if (typeof window !== 'undefined') localStorage.setItem(CUSTOM_SOURCES_KEY, JSON.stringify(configs));
}

export function addCustomSourceConfig(cfg: CustomSourceConfig): void {
  saveCustomSourceConfigs([...listCustomSourceConfigs(), cfg]);
}

export function removeCustomSourceConfig(id: string): void {
  saveCustomSourceConfigs(listCustomSourceConfigs().filter((c) => c.id !== id));
}

function getPath(obj: any, path?: string): any {
  if (!path) return undefined;
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function firstOf(obj: any, paths: string[]): any {
  for (const p of paths) {
    const v = getPath(obj, p);
    if (v !== undefined && v !== null) return v;
  }
  return undefined;
}

function toSearchResult(raw: any, cfg: CustomSourceConfig, i: number): TorrentSearchResult {
  const title = String(firstOf(raw, [cfg.titlePath || '', 'title', 'name', 'headline']) ?? `result-${i}`);
  const sizeRaw = firstOf(raw, [cfg.sizePath || '', 'size', 'item_size', 'length', 'contentLength']);
  const seedersRaw = firstOf(raw, [cfg.seedersPath || '', 'seeders', 'mirrors', 'seeds']);
  const link = String(firstOf(raw, [cfg.linkPath || '', 'link', 'url', 'guid', 'permalink']) ?? '');
  const magnetRaw = firstOf(raw, [cfg.magnetPath || '', 'magnet']);
  const magnetStr = typeof magnetRaw === 'string' && magnetRaw.startsWith('magnet:') ? magnetRaw : link.startsWith('magnet:') ? link : undefined;
  return {
    id: `${cfg.id}:${i}`,
    title,
    size: Number(sizeRaw) || 0,
    seeders: Number.isFinite(Number(seedersRaw)) ? Number(seedersRaw) : -1,
    source: cfg.name,
    magnet: magnetStr,
    torrentUrl: /\.torrent([?#]|$)/i.test(link) ? link : undefined,
    detailUrl: link.startsWith('http') ? link : undefined,
  };
}

function createCustomSource(cfg: CustomSourceConfig): PluggableSearchSource {
  return {
    id: cfg.id,
    name: cfg.name,
    kind: 'custom',
    async search(query: string, signal?: AbortSignal): Promise<TorrentSearchResult[]> {
      const url = cfg.url.replace(/\{query\}/g, encodeURIComponent(query));
      const res = await fetch(url, { signal });
      if (!res.ok) throw new Error(`${cfg.name} HTTP ${res.status}`);
      if (cfg.kind === 'rss') {
        const xml = await res.text();
        const doc = new DOMParser().parseFromString(xml, 'text/xml');
        const items = Array.from(doc.querySelectorAll('item'));
        return items.map((it, i) => {
          const title = it.querySelector('title')?.textContent?.trim() || `result-${i}`;
          const link = it.querySelector('link')?.textContent?.trim() || '';
          const enclosure = it.querySelector('enclosure');
          const encUrl = enclosure?.getAttribute('url') || '';
          const size = Number(enclosure?.getAttribute('length') || 0);
          return {
            id: `${cfg.id}:${i}`,
            title,
            size,
            seeders: -1,
            source: cfg.name,
            magnet: (link.startsWith('magnet:') ? link : encUrl.startsWith('magnet:') ? encUrl : undefined),
            torrentUrl: /\.torrent([?#]|$)/i.test(encUrl) ? encUrl : undefined,
            detailUrl: link.startsWith('http') ? link : undefined,
          };
        });
      }
      const json = await res.json();
      const list: any[] = Array.isArray(json)
        ? json
        : (getPath(json, cfg.listPath) as any[]) ||
          json?.results || json?.items || json?.docs || json?.torrents || json?.data || json?.entries || [];
      return (Array.isArray(list) ? list : []).map((raw, i) => toSearchResult(raw, cfg, i));
    },
  };
}

/** 全部可用源（内置 + 自定义） */
export function getAllSearchSources(): PluggableSearchSource[] {
  return [
    new InternetArchiveSource(),
    new AcademicTorrentsSource(),
    ...listCustomSourceConfigs().map(createCustomSource),
  ];
}

export interface SearchSourceBatch {
  source: PluggableSearchSource;
  results: TorrentSearchResult[];
  error?: string;
}

/** 并发搜索所有源，逐源隔离错误（15s 超时，单源挂起不拖垮其它源） */
export async function searchAll(query: string, sources: PluggableSearchSource[], signal?: AbortSignal): Promise<SearchSourceBatch[]> {
  return Promise.all(
    sources.map(async (source): Promise<SearchSourceBatch> => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      const onAbort = () => controller.abort();
      signal?.addEventListener('abort', onAbort);
      try {
        return { source, results: await source.search(query, controller.signal) };
      } catch (e: any) {
        const timedOut = controller.signal.aborted && !signal?.aborted;
        return { source, results: [], error: timedOut ? 'timeout (15s)' : e?.message || String(e) };
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener('abort', onAbort);
      }
    })
  );
}
