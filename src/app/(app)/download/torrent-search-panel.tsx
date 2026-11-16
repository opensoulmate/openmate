'use client';
import { useCallback, useMemo, useState } from 'react';
import { AlertCircle, Download, Loader2, Plus, Search, Trash2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  CustomSourceConfig, PluggableSearchSource, SearchSourceBatch, TorrentSearchResult,
  addCustomSourceConfig, getAllSearchSources, listCustomSourceConfigs, removeCustomSourceConfig, searchAll,
} from '@/lib/torrent-search';
import { DownloadOrigin, engineFor, formatBytes, readStoredTarget, storeTarget } from '@/lib/download-engine';
import { EngineTargetSelect } from './engine-target-select';

/** 来源徽标（结果行内）：内置源低饱和分层色 */
function SourceBadge({ name }: { name: string }) {
  return (
    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-500 shrink-0">
      {name}
    </span>
  );
}

/**
 * 种子搜索面板（下载页内嵌面板，不建新页）：
 * 搜索框 + 结果列表（标题/大小/做种数/来源）+ 一键「下载」弹目标选择。
 * 源可插拔（PluggableSearchSource）：内置 Internet Archive / Academic Torrents + 用户自定义 RSS/JSON。
 */
export function TorrentSearchPanel() {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [sourceFilter, setSourceFilter] = useState<string>('all');
  const [batches, setBatches] = useState<SearchSourceBatch[]>([]);
  const [searching, setSearching] = useState(false);
  const [pending, setPending] = useState<TorrentSearchResult | null>(null);
  const [target, setTarget] = useState<DownloadOrigin>(readStoredTarget);
  const [notice, setNotice] = useState<string | null>(null);
  const sources = useMemo(() => getAllSearchSources(), []);

  const runSearch = useCallback(async () => {
    if (!query.trim()) return;
    setSearching(true);
    setNotice(null);
    const result = await searchAll(query.trim(), sources);
    setBatches(result);
    setSearching(false);
  }, [query, sources]);

  const results = useMemo(
    () =>
      batches
        .flatMap((b) => b.results)
        .filter((r) => sourceFilter === 'all' || r.source === sourceFilter),
    [batches, sourceFilter]
  );

  const downloadResult = async () => {
    if (!pending) return;
    const url = pending.magnet || pending.torrentUrl || pending.detailUrl;
    if (!url) return;
    try {
      await engineFor(target).add({ url, filename: pending.title });
      storeTarget(target);
      setNotice(t('download.searchAdded', '已添加到下载任务：{{title}}', { title: pending.title }));
    } catch {
      setNotice(t('download.searchAddFailed', '添加任务失败，请检查引擎连接'));
    }
    setPending(null);
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* 搜索框行 */}
      <div className="p-3 lg:p-4 border-b border-border shrink-0 space-y-2.5">
        <div className="flex gap-2">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && runSearch()}
              placeholder={t('download.searchPlaceholder', '搜索种子 / 数据集（Internet Archive、Academic Torrents…）')}
              className="w-full pl-9 pr-3 py-2 rounded-lg bg-muted text-xs lg:text-sm outline-none border border-border focus:border-primary"
              autoFocus
            />
          </div>
          <button
            onClick={runSearch}
            disabled={searching || !query.trim()}
            className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-xs lg:text-sm font-medium hover:bg-primary/90 disabled:opacity-50 flex items-center gap-1.5"
          >
            {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            {t('download.search', '搜索')}
          </button>
        </div>
        {/* 源筛选 chip 行 */}
        <div className="flex items-center gap-1.5 flex-wrap">
          {['all', ...sources.map((s) => s.name)].map((name) => (
            <button
              key={name}
              onClick={() => setSourceFilter(name)}
              className={`px-2.5 py-1 rounded-lg text-[11px] transition-colors ${
                sourceFilter === name ? 'bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              }`}
            >
              {name === 'all' ? t('download.allSources', '全部源') : name}
            </button>
          ))}
        </div>
        {notice && (
          <div className="flex items-center gap-1.5 text-[11px] text-primary">
            <AlertCircle className="w-3 h-3 shrink-0" />
            {notice}
          </div>
        )}
      </div>

      {/* 结果列表 */}
      <div className="flex-1 overflow-y-auto">
        {searching ? (
          <div className="text-center py-16 text-muted-foreground">
            <Loader2 className="w-8 h-8 mx-auto mb-3 opacity-40 animate-spin" />
            <p className="text-sm">{t('download.searching', '搜索中…')}</p>
          </div>
        ) : results.length === 0 ? (
          <div className="text-center py-16 text-muted-foreground">
            <Search className="w-12 h-12 mx-auto mb-3 opacity-30" />
            <p className="text-sm">
              {batches.length > 0
                ? t('download.searchNoResults', '没有匹配结果（各源错误见下）')
                : t('download.searchEmpty', '输入关键词搜索公共种子源')}
            </p>
            {batches.filter((b) => b.error).map((b) => (
              <p key={b.source.id} className="text-[11px] text-red-500/80 mt-1">
                {b.source.name}: {b.error}
              </p>
            ))}
          </div>
        ) : (
          results.map((r) => {
            const downloadable = Boolean(r.magnet || r.torrentUrl || r.detailUrl);
            return (
              <div
                key={r.id}
                className="px-3 lg:px-4 py-3 flex items-center gap-3 hover:bg-muted/40 transition-colors border-b border-border/40 last:border-b-0"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs lg:text-sm font-medium truncate">{r.title}</span>
                    <SourceBadge name={r.source} />
                  </div>
                  {/* 结果 meta 行：大小 · 做种数 · 来源 */}
                  <div className="flex items-center gap-3 text-[11px] text-muted-foreground mt-1">
                    <span>{r.size > 0 ? formatBytes(r.size) : '--'}</span>
                    <span>
                      {t('download.seeders', '做种')} {r.seeders >= 0 ? r.seeders : '--'}
                    </span>
                    {r.magnet && (
                      <span className="flex items-center gap-1 text-green-500/80">
                        {t('download.hasMagnet', '磁力')}
                      </span>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => setPending(r)}
                  disabled={!downloadable}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20 disabled:opacity-40 shrink-0"
                >
                  <Download className="w-3.5 h-3.5" />
                  {t('download.download', '下载')}
                </button>
              </div>
            );
          })
        )}
      </div>

      {/* 一键下载 → 目标选择弹层 */}
      {pending && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40" onClick={() => setPending(null)}>
          <div
            className="w-full max-w-sm rounded-2xl border border-border bg-card shadow-xl p-5 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <h3 className="text-sm font-medium truncate">{t('download.chooseTarget', '选择下载目标')}</h3>
                <p className="text-[11px] text-muted-foreground truncate mt-1">{pending.title}</p>
              </div>
              <button
                onClick={() => setPending(null)}
                className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground shrink-0"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <EngineTargetSelect value={target} onChange={setTarget} />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setPending(null)}
                className="px-4 py-2 rounded-lg border text-xs text-muted-foreground hover:bg-muted"
              >
                {t('common.cancel', '取消')}
              </button>
              <button
                onClick={downloadResult}
                className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 flex items-center gap-1.5"
              >
                <Download className="w-3.5 h-3.5" />
                {t('download.download', '下载')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── 自定义搜索源管理（设置区内嵌）：用户自定义 RSS/JSON 源增删 ── */

export function CustomSourceSettings() {
  const { t } = useTranslation();
  const [configs, setConfigs] = useState<CustomSourceConfig[]>(() => listCustomSourceConfigs());
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [kind, setKind] = useState<'json' | 'rss'>('json');
  const [titlePath, setTitlePath] = useState('');
  const [sizePath, setSizePath] = useState('');
  const [seedersPath, setSeedersPath] = useState('');
  const [linkPath, setLinkPath] = useState('');

  const refresh = () => setConfigs(listCustomSourceConfigs());

  const add = () => {
    if (!name.trim() || !url.trim()) return;
    addCustomSourceConfig({
      id: `custom-${Date.now()}`,
      name: name.trim(),
      kind,
      url: url.trim(),
      titlePath: titlePath.trim() || undefined,
      sizePath: sizePath.trim() || undefined,
      seedersPath: seedersPath.trim() || undefined,
      linkPath: linkPath.trim() || undefined,
    });
    setName(''); setUrl(''); setTitlePath(''); setSizePath(''); setSeedersPath(''); setLinkPath('');
    refresh();
  };

  return (
    <div>
      <label className="text-xs lg:text-sm font-medium mb-1.5 block">
        {t('download.customSources', '自定义搜索源（RSS / JSON）')}
      </label>
      {/* 已有自定义源列表 */}
      {configs.length > 0 && (
        <div className="space-y-1.5 mb-2.5">
          {configs.map((c) => (
            <div key={c.id} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-muted/60">
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary uppercase shrink-0">{c.kind}</span>
              <span className="text-xs font-medium truncate shrink-0 max-w-[8rem]">{c.name}</span>
              <span className="text-[11px] text-muted-foreground truncate flex-1">{c.url}</span>
              <button
                onClick={() => { removeCustomSourceConfig(c.id); refresh(); }}
                title={t('download.deleteSource', '删除源')}
                className="p-1 rounded hover:bg-red-500/10 text-muted-foreground hover:text-red-500 shrink-0"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}
      {/* 新增表单 */}
      <div className="rounded-lg border border-border p-3 space-y-2">
        <div className="flex flex-col sm:flex-row gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('download.sourceName', '源名称')}
            className="flex-1 px-3 py-2 rounded-lg bg-muted text-xs outline-none border border-border focus:border-primary"
          />
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as 'json' | 'rss')}
            className="px-3 py-2 rounded-lg bg-muted text-xs outline-none border border-border focus:border-primary"
          >
            <option value="json">JSON</option>
            <option value="rss">RSS</option>
          </select>
        </div>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder={t('download.sourceUrl', 'URL 模板（含 {query} 占位符）')}
          className="w-full px-3 py-2 rounded-lg bg-muted text-xs outline-none border border-border focus:border-primary"
        />
        {kind === 'json' && (
          <div className="grid grid-cols-2 gap-2">
            <input value={titlePath} onChange={(e) => setTitlePath(e.target.value)} placeholder={t('download.titleField', '标题字段（如 data.title）')} className="px-3 py-2 rounded-lg bg-muted text-[11px] outline-none border border-border focus:border-primary" />
            <input value={sizePath} onChange={(e) => setSizePath(e.target.value)} placeholder={t('download.sizeField', '大小字段（字节）')} className="px-3 py-2 rounded-lg bg-muted text-[11px] outline-none border border-border focus:border-primary" />
            <input value={seedersPath} onChange={(e) => setSeedersPath(e.target.value)} placeholder={t('download.seedersField', '做种字段')} className="px-3 py-2 rounded-lg bg-muted text-[11px] outline-none border border-border focus:border-primary" />
            <input value={linkPath} onChange={(e) => setLinkPath(e.target.value)} placeholder={t('download.linkField', '链接字段（磁力/.torrent/详情）')} className="px-3 py-2 rounded-lg bg-muted text-[11px] outline-none border border-border focus:border-primary" />
          </div>
        )}
        <button
          onClick={add}
          disabled={!name.trim() || !url.trim()}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 disabled:opacity-50"
        >
          <Plus className="w-3.5 h-3.5" />
          {t('download.addSource', '添加源')}
        </button>
      </div>
    </div>
  );
}
