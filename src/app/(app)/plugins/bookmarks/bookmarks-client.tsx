"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  Bookmark, Plus, Search, Star, StarOff, Trash2, Edit3,
  ExternalLink, Folder, Tag, BarChart3, Grid3X3, List,
  Loader2, Check, X, ChevronRight, Heart, Clock, MousePointerClick,
  ArrowUpRight, BookmarkCheck, FolderPlus, Hash,
  PanelLeft,
} from "lucide-react";
import { useIsMobile } from "@/hooks/use-mobile";

interface BookmarkItem {
  id: string;
  url: string;
  title: string;
  description: string;
  favicon: string;
  collection: string;
  tags: string[];
  is_favorite: boolean;
  click_count: number;
  created_at: number;
  updated_at: number;
}

interface Collection {
  id: string;
  name: string;
  icon: string;
  description: string;
  bookmark_count: number;
}

interface TagInfo {
  name: string;
  count: number;
}

interface Stats {
  total_bookmarks: number;
  favorites: number;
  collections: number;
  top_domains: { domain: string; count: number }[];
  recently_added: { id: string; title: string; url: string }[];
  most_clicked: { id: string; title: string; url: string; clicks: number }[];
}

type Tab = "bookmarks" | "stats";
type ViewMode = "grid" | "list";

export function BookmarksClient() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const pluginBase = `${apiBase}/api/plugins/bookmarks`;
  const isMobile = useIsMobile();
  const [showSidebar, setShowSidebar] = useState(true);

  const [tab, setTab] = useState<Tab>("bookmarks");
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [bookmarks, setBookmarks] = useState<BookmarkItem[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [tags, setTags] = useState<TagInfo[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(false);

  // Filters
  const [searchQuery, setSearchQuery] = useState("");
  const [activeCollection, setActiveCollection] = useState<string | null>(null);
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [showFavoritesOnly, setShowFavoritesOnly] = useState(false);

  // Add/Edit modal
  const [showModal, setShowModal] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formUrl, setFormUrl] = useState("");
  const [formTitle, setFormTitle] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [formCollection, setFormCollection] = useState("Uncategorized");
  const [formTags, setFormTags] = useState("");
  const [formSaving, setFormSaving] = useState(false);

  // New collection modal
  const [showColModal, setShowColModal] = useState(false);
  const [newColName, setNewColName] = useState("");
  const [newColIcon, setNewColIcon] = useState("📁");

  // Toast
  const [toast, setToast] = useState("");

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2500);
  };

  // ── Data Loading ────────────────────────────────────────

  const loadBookmarks = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: "200", sort: "created_at", order: "desc" });
      if (activeCollection) params.set("collection", activeCollection);
      if (activeTag) params.set("tag", activeTag);
      if (showFavoritesOnly) params.set("favorite", "true");
      if (searchQuery) params.set("search", searchQuery);

      const res = await fetch(`${pluginBase}/bookmarks?${params}`);
      const data = await res.json();
      setBookmarks(data.bookmarks || []);
    } catch { /* ignore */ }
    setLoading(false);
  }, [pluginBase, activeCollection, activeTag, showFavoritesOnly, searchQuery]);

  const loadCollections = useCallback(async () => {
    try {
      const res = await fetch(`${pluginBase}/collections`);
      const data = await res.json();
      setCollections(data.collections || []);
    } catch { /* ignore */ }
  }, [pluginBase]);

  const loadTags = useCallback(async () => {
    try {
      const res = await fetch(`${pluginBase}/tags`);
      const data = await res.json();
      setTags(data.tags || []);
    } catch { /* ignore */ }
  }, [pluginBase]);

  const loadStats = useCallback(async () => {
    try {
      const res = await fetch(`${pluginBase}/stats`);
      const data = await res.json();
      setStats(data);
    } catch { /* ignore */ }
  }, [pluginBase]);

  useEffect(() => { loadBookmarks(); }, [loadBookmarks]);
  useEffect(() => { loadCollections(); loadTags(); }, [loadCollections, loadTags]);
  useEffect(() => { if (tab === "stats") loadStats(); }, [tab, loadStats]);

  // ── Actions ─────────────────────────────────────────────

  const openAdd = () => {
    setEditingId(null);
    setFormUrl(""); setFormTitle(""); setFormDesc("");
    setFormCollection(activeCollection || "Uncategorized"); setFormTags("");
    setShowModal(true);
  };

  const openEdit = (bm: BookmarkItem) => {
    setEditingId(bm.id);
    setFormUrl(bm.url); setFormTitle(bm.title); setFormDesc(bm.description);
    setFormCollection(bm.collection); setFormTags(bm.tags.join(", "));
    setShowModal(true);
  };

  const saveBookmark = async () => {
    if (!formUrl.trim()) return;
    setFormSaving(true);
    try {
      const tagsList = formTags.split(",").map(t => t.trim()).filter(Boolean);
      if (editingId) {
        await fetch(`${pluginBase}/bookmarks/${editingId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: formUrl, title: formTitle, description: formDesc, collection: formCollection, tags: tagsList }),
        });
        showToast(t("bookmarks.updated"));
      } else {
        await fetch(`${pluginBase}/bookmarks`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: formUrl, title: formTitle, description: formDesc, collection: formCollection, tags: tagsList }),
        });
        showToast(t("bookmarks.added"));
      }
      setShowModal(false);
      loadBookmarks(); loadCollections(); loadTags();
    } catch { showToast(t("bookmarks.saveError")); }
    setFormSaving(false);
  };

  const deleteBookmark = async (id: string) => {
    if (!confirm(t("bookmarks.deleteConfirm"))) return;
    await fetch(`${pluginBase}/bookmarks/${id}`, { method: "DELETE" });
    showToast(t("bookmarks.deleted"));
    loadBookmarks(); loadCollections(); loadTags();
  };

  const toggleFavorite = async (id: string) => {
    await fetch(`${pluginBase}/bookmarks/${id}/favorite`, { method: "POST" });
    loadBookmarks();
  };

  const createCollection = async () => {
    if (!newColName.trim()) return;
    await fetch(`${pluginBase}/collections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newColName, icon: newColIcon }),
    });
    setShowColModal(false); setNewColName(""); setNewColIcon("📁");
    loadCollections();
    showToast(t("bookmarks.collectionCreated"));
  };

  const promoteBookmark = async (id: string) => {
    const res = await fetch(`${pluginBase}/bookmarks/${id}/promote`, { method: "POST" });
    if (res.ok) showToast(t("bookmarks.promoted"));
  };

  // ── Helpers ─────────────────────────────────────────────

  const getDomain = (url: string) => {
    try { return new URL(url).hostname; } catch { return url; }
  };

  const getFaviconUrl = (url: string) => {
    try {
      const domain = new URL(url).hostname;
      return `https://www.google.com/s2/favicons?domain=${domain}&sz=32`;
    } catch { return ""; }
  };

  const timeAgo = (ts: number) => {
    const diff = Date.now() / 1000 - ts;
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
    return new Date(ts * 1000).toLocaleDateString();
  };

  // ── Render ──────────────────────────────────────────────

  const allCollections = [{ id: "__all__", name: "All", icon: "📋", bookmark_count: bookmarks.length, description: "" }, ...collections];

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar — sidebar-style sliding on mobile, inline on desktop */}
      {isMobile && showSidebar && (
        <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setShowSidebar(false)} aria-hidden="true" />
      )}
      {isMobile ? (
        showSidebar && (
          <div
            className="absolute inset-y-0 left-0 z-10 h-full w-64 min-w-0 border-r border-border transition-[left] duration-200 ease-linear flex flex-col overflow-hidden bg-card"
            style={{ left: 0 }}
          >
            <BookmarkSidebar
              allCollections={allCollections} collections={collections} tags={tags}
              activeCollection={activeCollection} setActiveCollection={setActiveCollection}
              activeTag={activeTag} setActiveTag={setActiveTag}
              showFavoritesOnly={showFavoritesOnly} setShowFavoritesOnly={setShowFavoritesOnly}
              openAdd={openAdd} setShowColModal={setShowColModal}
              bookmarks={bookmarks} t={t}
            />
          </div>
        )
      ) : (
      <aside className="w-64 border-r border-border bg-background/50 flex flex-col shrink-0">
        <BookmarkSidebar
          allCollections={allCollections} collections={collections} tags={tags}
          activeCollection={activeCollection} setActiveCollection={setActiveCollection}
          activeTag={activeTag} setActiveTag={setActiveTag}
          showFavoritesOnly={showFavoritesOnly} setShowFavoritesOnly={setShowFavoritesOnly}
          openAdd={openAdd} setShowColModal={setShowColModal}
          bookmarks={bookmarks} t={t}
        />
      </aside>
      )}

      {/* Main Content */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <header className="flex items-center gap-2 lg:gap-3 px-2 lg:px-4 md:px-3 lg:px-6 py-3 border-b border-border bg-background/30">
          {isMobile && (
            <button onClick={() => setShowSidebar(true)} className="p-1.5 rounded hover:bg-muted shrink-0">
              <PanelLeft className="w-4 h-4 text-muted-foreground" />
            </button>
          )}
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="搜索书签..."
              className="w-full pl-9 pr-4 py-2 rounded-lg bg-muted/50 border border-border text-xs lg:text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-violet-500 transition"
            />
          </div>
          <div className="flex items-center gap-1 bg-muted/50 rounded-lg p-0.5">
            <button onClick={() => setTab("bookmarks")} className={`px-3 py-1.5 rounded-md text-xs font-medium transition ${tab === "bookmarks" ? "bg-violet-600/20 text-violet-300" : "text-muted-foreground hover:text-foreground"}`}>
              <Bookmark className="w-3.5 h-3.5 inline mr-1" />Bookmarks
            </button>
            <button onClick={() => setTab("stats")} className={`px-3 py-1.5 rounded-md text-xs font-medium transition ${tab === "stats" ? "bg-violet-600/20 text-violet-300" : "text-muted-foreground hover:text-foreground"}`}>
              <BarChart3 className="w-3.5 h-3.5 inline mr-1" />Stats
            </button>
          </div>
          <div className="flex items-center gap-1 bg-muted/50 rounded-lg p-0.5">
            <button onClick={() => setViewMode("grid")} className={`p-1.5 rounded-md transition ${viewMode === "grid" ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground"}`}>
              <Grid3X3 className="w-3.5 h-3.5" />
            </button>
            <button onClick={() => setViewMode("list")} className={`p-1.5 rounded-md transition ${viewMode === "list" ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground"}`}>
              <List className="w-3.5 h-3.5" />
            </button>
          </div>
        </header>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-3 lg:p-6">
          {tab === "bookmarks" ? (
            loading ? (
              <div className="flex items-center justify-center h-40"><Loader2 className="w-6 h-6 animate-spin text-violet-400" /></div>
            ) : bookmarks.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-60 text-muted-foreground">
                <Bookmark className="w-12 h-12 mb-3 text-muted-foreground/40" />
                <p className="text-xs lg:text-sm">暂无书签</p>
                <button onClick={openAdd} className="mt-3 text-violet-400 text-xs lg:text-sm hover:underline">添加你的第一个书签</button>
              </div>
            ) : viewMode === "grid" ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2 lg:gap-4">
                {bookmarks.map(bm => (
                  <div key={bm.id} className="group bg-muted/50 border border-border rounded-xl p-3 lg:p-4 hover:border-border transition">
                    <div className="flex items-start gap-2 lg:gap-3 mb-3">
                      <img src={getFaviconUrl(bm.url)} alt="" className="w-5 h-5 rounded mt-0.5" onError={e => { (e.target as HTMLImageElement).style.display = "none"; }} />
                      <div className="flex-1 min-w-0">
                        <a href={bm.url} target="_blank" rel="noopener" className="text-xs lg:text-sm font-medium text-foreground hover:text-violet-300 line-clamp-2 leading-snug">
                          {bm.title}
                        </a>
                        <p className="text-xs text-muted-foreground mt-0.5 truncate">{getDomain(bm.url)}</p>
                      </div>
                      <button onClick={() => toggleFavorite(bm.id)} className="shrink-0">
                        {bm.is_favorite ? <Star className="w-4 h-4 text-yellow-400 fill-yellow-400" /> : <Star className="w-4 h-4 text-muted-foreground/70 group-hover:text-muted-foreground" />}
                      </button>
                    </div>
                    {bm.description && <p className="text-xs text-muted-foreground line-clamp-2 mb-3">{bm.description}</p>}
                    <div className="flex items-center gap-1 mb-3 flex-wrap">
                      {bm.tags.slice(0, 3).map(tag => (
                        <span key={tag} className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-muted text-muted-foreground text-[10px]">
                          <Hash className="w-2 h-2" />{tag}
                        </span>
                      ))}
                      {bm.tags.length > 3 && <span className="text-[10px] text-muted-foreground/70">+{bm.tags.length - 3}</span>}
                    </div>
                    <div className="flex items-center justify-between text-[10px] text-muted-foreground/70">
                      <span className="flex items-center gap-1"><Clock className="w-2.5 h-2.5" />{timeAgo(bm.created_at)}</span>
                      <div className="flex items-center gap-0.5 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition">
                        <button onClick={() => openEdit(bm)} className="p-1 hover:text-foreground"><Edit3 className="w-3 h-3" /></button>
                        <a href={bm.url} target="_blank" rel="noopener" className="p-1 hover:text-foreground"><ExternalLink className="w-3 h-3" /></a>
                        <button onClick={() => promoteBookmark(bm.id)} className="p-1 hover:text-foreground" title="提升为知识"><BookmarkCheck className="w-3 h-3" /></button>
                        <button onClick={() => deleteBookmark(bm.id)} className="p-1 hover:text-red-400"><Trash2 className="w-3 h-3" /></button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="space-y-1">
                {bookmarks.map(bm => (
                  <div key={bm.id} className="group flex items-center gap-2 lg:gap-3 px-2 lg:px-4 py-2.5 rounded-lg hover:bg-muted/50 transition">
                    <img src={getFaviconUrl(bm.url)} alt="" className="w-4 h-4 rounded shrink-0" onError={e => { (e.target as HTMLImageElement).style.display = "none"; }} />
                    <button onClick={() => toggleFavorite(bm.id)} className="shrink-0">
                      {bm.is_favorite ? <Star className="w-3.5 h-3.5 text-yellow-400 fill-yellow-400" /> : <Star className="w-3.5 h-3.5 text-muted-foreground/40 group-hover:text-muted-foreground" />}
                    </button>
                    <a href={bm.url} target="_blank" rel="noopener" className="text-xs lg:text-sm text-foreground hover:text-violet-300 truncate flex-1 min-w-0">{bm.title}</a>
                    <span className="text-xs text-muted-foreground/70 truncate max-w-[120px]">{getDomain(bm.url)}</span>
                    <div className="flex items-center gap-1">
                      {bm.tags.slice(0, 2).map(tag => (
                        <span key={tag} className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground text-[10px]">{tag}</span>
                      ))}
                    </div>
                    <span className="text-[10px] text-muted-foreground/70 w-16 text-right">{timeAgo(bm.created_at)}</span>
                    <div className="flex items-center gap-0.5 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition">
                      <button onClick={() => openEdit(bm)} className="p-1 hover:text-foreground text-muted-foreground/70"><Edit3 className="w-3 h-3" /></button>
                      <button onClick={() => deleteBookmark(bm.id)} className="p-1 hover:text-red-400 text-muted-foreground/70"><Trash2 className="w-3 h-3" /></button>
                    </div>
                  </div>
                ))}
              </div>
            )
          ) : (
            /* Stats Tab */
            stats ? (
              <div className="space-y-3 lg:space-y-6 max-w-4xl">
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 lg:gap-4">
                  {[
                    { label: "Total Bookmarks", value: stats.total_bookmarks, icon: Bookmark, color: "violet" },
                    { label: "Favorites", value: stats.favorites, icon: Star, color: "yellow" },
                    { label: "Collections", value: stats.collections, icon: Folder, color: "blue" },
                  ].map(s => (
                    <div key={s.label} className="bg-muted/50 border border-border rounded-xl p-3 lg:p-4">
                      <div className="flex items-center gap-2 mb-2">
                        <s.icon className={`w-4 h-4 text-${s.color}-400`} />
                        <span className="text-xs text-muted-foreground">{s.label}</span>
                      </div>
                      <p className="text-xl lg:text-2xl font-bold text-foreground">{s.value}</p>
                    </div>
                  ))}
                </div>

                {stats.top_domains.length > 0 && (
                  <div className="bg-muted/50 border border-border rounded-xl p-3 lg:p-4">
                    <h3 className="text-xs lg:text-sm font-medium text-foreground mb-3">热门域名</h3>
                    <div className="space-y-2">
                      {stats.top_domains.map(d => (
                        <div key={d.domain} className="flex items-center gap-2 lg:gap-3">
                          <span className="text-xs text-muted-foreground w-40 truncate">{d.domain}</span>
                          <div className="flex-1 bg-muted rounded-full h-2">
                            <div className="bg-violet-500 rounded-full h-2 transition-all" style={{ width: `${Math.min(100, (d.count / (stats.top_domains[0]?.count || 1)) * 100)}%` }} />
                          </div>
                          <span className="text-xs text-muted-foreground w-8 text-right">{d.count}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {stats.most_clicked.length > 0 && (
                  <div className="bg-muted/50 border border-border rounded-xl p-3 lg:p-4">
                    <h3 className="text-xs lg:text-sm font-medium text-foreground mb-3">最多点击</h3>
                    <div className="space-y-2">
                      {stats.most_clicked.map((bm, i) => (
                        <div key={bm.id} className="flex items-center gap-2 lg:gap-3">
                          <span className="text-xs text-muted-foreground/70 w-4">#{i + 1}</span>
                          <a href={bm.url} target="_blank" rel="noopener" className="text-xs lg:text-sm text-foreground hover:text-violet-300 truncate flex-1">{bm.title}</a>
                          <span className="flex items-center gap-1 text-xs text-muted-foreground"><MousePointerClick className="w-3 h-3" />{bm.clicks}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex items-center justify-center h-40"><Loader2 className="w-6 h-6 animate-spin text-violet-400" /></div>
            )
          )}
        </div>
      </main>

      {/* Add/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowModal(false)}>
          <div className="bg-card border border-border rounded-2xl w-full max-w-lg p-3 lg:p-6 shadow-2xl" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-foreground">{editingId ? "编辑书签" : "添加书签"}</h3>
              <button onClick={() => setShowModal(false)} className="text-muted-foreground hover:text-foreground"><X className="w-5 h-5" /></button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">URL *</label>
                <input value={formUrl} onChange={e => setFormUrl(e.target.value)} placeholder="https://example.com" className="w-full px-3 py-2 rounded-lg bg-muted border border-border text-xs lg:text-sm text-foreground focus:outline-none focus:border-violet-500" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">标题</label>
                <input value={formTitle} onChange={e => setFormTitle(e.target.value)} placeholder="从 URL 自动生成" className="w-full px-3 py-2 rounded-lg bg-muted border border-border text-xs lg:text-sm text-foreground focus:outline-none focus:border-violet-500" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">描述</label>
                <textarea value={formDesc} onChange={e => setFormDesc(e.target.value)} rows={2} placeholder="简要描述..." className="w-full px-3 py-2 rounded-lg bg-muted border border-border text-xs lg:text-sm text-foreground focus:outline-none focus:border-violet-500 resize-none" />
              </div>
              <div className="grid grid-cols-2 gap-2 lg:gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">收藏夹</label>
                  <select value={formCollection} onChange={e => setFormCollection(e.target.value)} className="w-full px-3 py-2 rounded-lg bg-muted border border-border text-xs lg:text-sm text-foreground focus:outline-none focus:border-violet-500">
                    {collections.map(c => <option key={c.id} value={c.name}>{c.icon} {c.name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">标签（逗号分隔）</label>
                  <input value={formTags} onChange={e => setFormTags(e.target.value)} placeholder="标签1, 标签2" className="w-full px-3 py-2 rounded-lg bg-muted border border-border text-xs lg:text-sm text-foreground focus:outline-none focus:border-violet-500" />
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setShowModal(false)} className="px-2 lg:px-4 py-2 rounded-lg text-xs lg:text-sm text-muted-foreground hover:text-foreground transition">取消</button>
              <button onClick={saveBookmark} disabled={formSaving || !formUrl.trim()} className="flex items-center gap-2 px-2 lg:px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-xs lg:text-sm font-medium transition disabled:opacity-50">
                {formSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                {editingId ? "保存修改" : "添加书签"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 新建收藏夹 Modal */}
      {showColModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowColModal(false)}>
          <div className="bg-card border border-border rounded-2xl w-full max-w-sm p-3 lg:p-6 shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-foreground mb-4">新建收藏夹</h3>
            <div className="flex gap-2 mb-3">
              <select value={newColIcon} onChange={e => setNewColIcon(e.target.value)} className="px-2 py-2 rounded-lg bg-muted border border-border text-xs lg:text-sm">
                {["📁", "📖", "🔧", "💡", "🎯", "📊", "🎨", "🔗", "⚡", "🧪", "🌟", "📝"].map(i => <option key={i} value={i}>{i}</option>)}
              </select>
              <input value={newColName} onChange={e => setNewColName(e.target.value)} placeholder="收藏夹名称" className="flex-1 px-3 py-2 rounded-lg bg-muted border border-border text-xs lg:text-sm text-foreground focus:outline-none focus:border-violet-500" />
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowColModal(false)} className="px-2 lg:px-4 py-2 rounded-lg text-xs lg:text-sm text-muted-foreground hover:text-foreground">取消</button>
              <button onClick={createCollection} disabled={!newColName.trim()} className="px-2 lg:px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-xs lg:text-sm font-medium disabled:opacity-50">创建</button>
            </div>
          </div>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 flex items-center gap-2 px-2 lg:px-4 py-2.5 rounded-xl bg-muted border border-border shadow-xl text-xs lg:text-sm text-foreground animate-in fade-in slide-in-from-bottom-2">
          <Check className="w-4 h-4 text-emerald-400" />{toast}
        </div>
      )}
    </div>
  );
}

function BookmarkSidebar({
  allCollections, collections, tags,
  activeCollection, setActiveCollection,
  activeTag, setActiveTag,
  showFavoritesOnly, setShowFavoritesOnly,
  openAdd, setShowColModal,
  bookmarks, t,
}: {
  allCollections: { id: string; name: string; icon: string; bookmark_count: number; description: string }[];
  collections: Collection[];
  tags: TagInfo[];
  activeCollection: string | null;
  setActiveCollection: (c: string | null) => void;
  activeTag: string | null;
  setActiveTag: (t: string | null) => void;
  showFavoritesOnly: boolean;
  setShowFavoritesOnly: (v: boolean) => void;
  openAdd: () => void;
  setShowColModal: (v: boolean) => void;
  bookmarks: BookmarkItem[];
  t: (k: string, o?: Record<string, unknown>) => string;
}) {
  return (
    <>
      <div className="p-4 border-b border-border">
        <div className="flex items-center gap-2 mb-3">
          <Bookmark className="w-5 h-5 text-violet-400" />
          <h2 className="text-xs lg:text-sm font-semibold text-foreground">书签</h2>
        </div>
        <button onClick={openAdd} className="w-full flex items-center gap-2 px-3 py-2 rounded-lg bg-violet-600/20 hover:bg-violet-600/30 text-violet-300 text-xs lg:text-sm transition">
          <Plus className="w-4 h-4" /> Add Bookmark
        </button>
      </div>

      {/* Collections */}
      <div className="flex-1 overflow-y-auto p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">收藏夹</span>
          <button onClick={() => setShowColModal(true)} className="text-muted-foreground hover:text-violet-400 transition">
            <FolderPlus className="w-3.5 h-3.5" />
          </button>
        </div>
        {allCollections.map(col => (
          <button
            key={col.id}
            onClick={() => { setActiveCollection(col.id === "__all__" ? null : col.name); setActiveTag(null); }}
            className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-xs lg:text-sm transition mb-0.5 ${
              (col.id === "__all__" && !activeCollection) || activeCollection === col.name
                ? "bg-violet-600/20 text-violet-300"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
            }`}
          >
            <span>{col.icon}</span>
            <span className="flex-1 text-left truncate">{col.name}</span>
            <span className="text-xs text-muted-foreground/70">{col.bookmark_count}</span>
          </button>
        ))}

        {/* Tags */}
        {tags.length > 0 && (
          <>
            <div className="flex items-center gap-1 mt-4 mb-2">
              <Tag className="w-3 h-3 text-muted-foreground" />
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">标签</span>
            </div>
            <div className="flex flex-wrap gap-1">
              {tags.slice(0, 20).map(tag => (
                <button
                  key={tag.name}
                  onClick={() => { setActiveTag(activeTag === tag.name ? null : tag.name); setActiveCollection(null); }}
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs transition ${
                    activeTag === tag.name
                      ? "bg-violet-600/30 text-violet-300"
                      : "bg-muted text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <Hash className="w-2.5 h-2.5" />{tag.name}
                  <span className="text-muted-foreground/70">{tag.count}</span>
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Favorites toggle */}
      <div className="p-3 border-t border-border">
        <button
          onClick={() => { setShowFavoritesOnly(!showFavoritesOnly); setActiveCollection(null); setActiveTag(null); }}
          className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-xs lg:text-sm transition ${
            showFavoritesOnly ? "bg-yellow-600/20 text-yellow-300" : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
          }`}
        >
          <Star className="w-4 h-4" /> Favorites
        </button>
      </div>
    </>
  );
}
