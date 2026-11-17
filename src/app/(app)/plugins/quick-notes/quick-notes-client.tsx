'use client';
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  StickyNote, Plus, Search, Tag, Pin, Trash2, Save, X, Check,
  Loader2, CheckCircle2, XCircle, BookOpen, Hash, RefreshCw,
  ChevronDown, Edit3, Eye, ArrowUpRight,
  PanelLeft,
} from 'lucide-react';
import { getApiBaseUrl, getToken } from '@/lib/api-client';
import { useTranslation } from 'react-i18next';
import { useIsMobile } from '@/hooks/use-mobile';

interface Note {
  id: string;
  title: string;
  content: string;
  tags: string[];
  pinned: boolean;
  created_at: string;
  updated_at: string;
}

const API = () => `${getApiBaseUrl()}/api/plugins/quick-notes`;
const headers = () => ({
  'Content-Type': 'application/json',
  Authorization: `Bearer ${getToken()}`,
});

function timeAgo(ts: string, t: (k: string, o?: Record<string, unknown>) => string): string {
  const diff = Date.now() - new Date(ts).getTime();
  if (diff < 60000) return t('plugins.justNow');
  if (diff < 3600000) return t('plugins.minutesAgo', { n: Math.floor(diff / 60000) });
  if (diff < 86400000) return t('plugins.hoursAgo', { n: Math.floor(diff / 3600000) });
  return t('plugins.daysAgo', { n: Math.floor(diff / 86400000) });
}

function MarkdownPreview({ content }: { content: string }) {
  // Simple markdown rendering
  const html = content
    .replace(/^### (.+)$/gm, '<h3 class="text-base font-semibold mt-3 mb-1">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="text-lg font-bold mt-4 mb-2">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="text-xl font-bold mt-4 mb-2">$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`(.+?)`/g, '<code class="bg-muted px-1 py-0.5 rounded text-xs font-mono">$1</code>')
    .replace(/^- (.+)$/gm, '<li class="ml-4 list-disc">$1</li>')
    .replace(/^\d+\. (.+)$/gm, '<li class="ml-4 list-decimal">$1</li>')
    .replace(/^> (.+)$/gm, '<blockquote class="border-l-2 border-primary/30 pl-3 italic text-muted-foreground">$1</blockquote>')
    .replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" class="text-primary underline" target="_blank">$1</a>')
    .replace(/\n\n/g, '</p><p class="mb-2">')
    .replace(/\n/g, '<br/>');

  return (
    <div
      className="prose prose-sm dark:prose-invert max-w-none text-xs lg:text-sm leading-relaxed"
      dangerouslySetInnerHTML={{ __html: `<p class="mb-2">${html}</p>` }}
    />
  );
}

export function QuickNotesClient() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [showSidebar, setShowSidebar] = useState(true);
  const [notes, setNotes] = useState<Note[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterTag, setFilterTag] = useState<string | null>(null);
  const [allTags, setAllTags] = useState<string[]>([]);
  const [editingNote, setEditingNote] = useState<Note | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [preview, setPreview] = useState(false);
  const [stats, setStats] = useState({ total: 0, pinned: 0, created_today: 0 });

  // Form state
  const [formTitle, setFormTitle] = useState('');
  const [formContent, setFormContent] = useState('');
  const [formTags, setFormTags] = useState<string[]>([]);
  const [formPinned, setFormPinned] = useState(false);
  const [tagInput, setTagInput] = useState('');
  const [promoting, setPromoting] = useState(false);

  const autoSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = (msg: string, type: 'success' | 'error' = 'success') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3000);
  };

  const fetchNotes = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (searchQuery) params.set('q', searchQuery);
      if (filterTag) params.set('tag', filterTag);
      params.set('limit', '200');

      const res = await fetch(`${API()}/notes?${params}`, { headers: headers() });
      if (res.ok) {
        const data = await res.json();
        setNotes(data.notes || []);
      }
    } catch (e) {
      console.error('Failed to fetch notes:', e);
    }
    setLoading(false);
  }, [searchQuery, filterTag]);

  const fetchTags = useCallback(async () => {
    try {
      const res = await fetch(`${API()}/tags`, { headers: headers() });
      if (res.ok) {
        const data = await res.json();
        setAllTags(data.tags || []);
      }
    } catch {}
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${API()}/stats`, { headers: headers() });
      if (res.ok) {
        const data = await res.json();
        setStats(data);
      }
    } catch {}
  }, []);

  useEffect(() => {
    fetchNotes();
    fetchTags();
    fetchStats();
  }, [fetchNotes, fetchTags, fetchStats]);

  const openCreate = () => {
    setFormTitle('');
    setFormContent('');
    setFormTags([]);
    setFormPinned(false);
    setIsCreating(true);
    setEditingNote(null);
    setPreview(false);
    if (isMobile) setShowSidebar(false);
  };

  const openEdit = (note: Note) => {
    setFormTitle(note.title);
    setFormContent(note.content);
    setFormTags([...note.tags]);
    setFormPinned(note.pinned);
    setEditingNote(note);
    setIsCreating(false);
    setPreview(false);
    if (isMobile) setShowSidebar(false);
  };

  const closeEditor = () => {
    setEditingNote(null);
    setIsCreating(false);
    if (isMobile) setShowSidebar(true);
  };

  const handleSave = async () => {
    const body = {
      title: formTitle,
      content: formContent,
      tags: formTags,
      pinned: formPinned,
    };

    try {
      let res;
      if (editingNote) {
        res = await fetch(`${API()}/notes/${editingNote.id}`, {
          method: 'PUT',
          headers: headers(),
          body: JSON.stringify(body),
        });
      } else {
        res = await fetch(`${API()}/notes`, {
          method: 'POST',
          headers: headers(),
          body: JSON.stringify(body),
        });
      }

      if (res.ok) {
        showToast(editingNote ? t('plugins.noteUpdated') : t('plugins.noteCreated'));
        closeEditor();
        fetchNotes();
        fetchTags();
        fetchStats();
      } else {
        showToast(t('plugins.saveFailed'), 'error');
      }
    } catch {
      showToast(t('plugins.saveError'), 'error');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      const res = await fetch(`${API()}/notes/${id}`, {
        method: 'DELETE',
        headers: headers(),
      });
      if (res.ok) {
        showToast(t('plugins.noteDeleted'));
        if (editingNote?.id === id) closeEditor();
        fetchNotes();
        fetchTags();
        fetchStats();
      }
    } catch {
      showToast(t('plugins.deleteError'), 'error');
    }
  };

  const handleTogglePin = async (note: Note) => {
    try {
      await fetch(`${API()}/notes/${note.id}`, {
        method: 'PUT',
        headers: headers(),
        body: JSON.stringify({ pinned: !note.pinned }),
      });
      fetchNotes();
    } catch {}
  };

  const handlePromote = async (noteId: string) => {
    setPromoting(true);
    try {
      const res = await fetch(`${API()}/notes/${noteId}/promote`, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (data.status === 'promoted') {
        showToast(t('plugins.promoted'));
      } else {
        showToast(data.error || t('plugins.promoteWarning'), 'error');
      }
    } catch {
      showToast(t('plugins.promoteError'), 'error');
    }
    setPromoting(false);
  };

  const addTag = () => {
    const tag = tagInput.trim().toLowerCase();
    if (tag && !formTags.includes(tag)) {
      setFormTags([...formTags, tag]);
    }
    setTagInput('');
  };

  const removeTag = (tag: string) => {
    setFormTags(formTags.filter(t => t !== tag));
  };

  const pinnedNotes = notes.filter(n => n.pinned);
  const regularNotes = notes.filter(n => !n.pinned);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* Toast */}
      {toast && (
        <div className={`fixed bottom-6 right-6 z-[60] flex items-center gap-2 rounded-lg border px-4 py-2.5 text-xs font-medium shadow-lg ${
          toast.type === 'success'
            ? 'border-green-500/40 bg-green-500/10 text-green-400'
            : 'border-red-500/40 bg-red-500/10 text-red-400'
        }`}>
          {toast.type === 'success' ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
          {toast.msg}
        </div>
      )}

      {/* Note List Panel — sidebar-style sliding on mobile, inline on desktop */}
      {isMobile && showSidebar && (
        <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setShowSidebar(false)} aria-hidden="true" />
      )}
      {isMobile ? (
        showSidebar && (
          <div
            className="absolute inset-y-0 left-0 z-10 h-full w-80 min-w-0 border-r border-border transition-[left] duration-200 ease-linear flex flex-col overflow-hidden bg-card"
            style={{ left: 0 }}
          >
            <NoteListContent
              notes={notes} pinnedNotes={pinnedNotes} regularNotes={regularNotes}
              searchQuery={searchQuery} setSearchQuery={setSearchQuery}
              filterTag={filterTag} setFilterTag={setFilterTag}
              allTags={allTags} stats={stats}
              editingNote={editingNote} openEdit={openEdit} openCreate={openCreate}
              handleTogglePin={handleTogglePin} handleDelete={handleDelete}
              fetchNotes={fetchNotes} fetchTags={fetchTags} fetchStats={fetchStats}
              loading={loading} t={t}
            />
          </div>
        )
      ) : (
      <div className="w-80 border-r border-border flex flex-col shrink-0">
        <NoteListContent
          notes={notes} pinnedNotes={pinnedNotes} regularNotes={regularNotes}
          searchQuery={searchQuery} setSearchQuery={setSearchQuery}
          filterTag={filterTag} setFilterTag={setFilterTag}
          allTags={allTags} stats={stats}
          editingNote={editingNote} openEdit={openEdit} openCreate={openCreate}
          handleTogglePin={handleTogglePin} handleDelete={handleDelete}
          fetchNotes={fetchNotes} fetchTags={fetchTags} fetchStats={fetchStats}
          loading={loading} t={t}
        />
      </div>
      )}

      {/* Editor Panel */}
      {(editingNote || isCreating) ? (
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Editor toolbar */}
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
            <div className="flex items-center gap-2">
              {isMobile && (
                <button onClick={() => { closeEditor(); setShowSidebar(true); }} className="p-1 rounded hover:bg-muted">
                  <PanelLeft size={16} className="text-muted-foreground" />
                </button>
              )}
              <Edit3 size={14} className="text-muted-foreground" />
              <span className="text-xs lg:text-sm font-medium">
                {isCreating ? t('plugins.newNote') : t('plugins.editNote')}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPreview(!preview)}
                className={`px-2.5 py-1 rounded-lg text-xs flex items-center gap-1.5 transition-colors ${
                  preview ? 'bg-primary text-primary-foreground' : 'border hover:bg-muted'
                }`}
              >
                {preview ? <Edit3 size={12} /> : <Eye size={12} />}
                {preview ? t('plugins.editBtn') : t('plugins.previewBtn')}
              </button>
              {editingNote && (
                <button
                  onClick={() => handlePromote(editingNote.id)}
                  disabled={promoting}
                  className="px-2.5 py-1 rounded-lg text-xs border border-green-500/30 text-green-500 hover:bg-green-500/5 flex items-center gap-1.5 disabled:opacity-50"
                >
                  {promoting ? <Loader2 size={12} className="animate-spin" /> : <ArrowUpRight size={12} />}
                  {t('plugins.promoteBtn')}
                </button>
              )}
              <button onClick={handleSave}
                className="px-3 py-1 rounded-lg text-xs bg-primary text-primary-foreground hover:bg-primary/90 flex items-center gap-1.5">
                <Save size={12} />
                {t('plugins.saveBtn')}
              </button>
              <button onClick={closeEditor}
                className="p-1 rounded-lg hover:bg-muted transition-colors">
                <X size={14} className="text-muted-foreground" />
              </button>
            </div>
          </div>

          {/* Editor body */}
          <div className="flex-1 overflow-y-auto p-3 lg:p-4 space-y-4">
            {/* Title */}
            <input
              value={formTitle}
              onChange={e => setFormTitle(e.target.value)}
              placeholder={t('plugins.titlePlaceholder')}
              className="w-full text-xl font-bold bg-transparent outline-none placeholder:text-muted-foreground/50"
            />

            {/* Tags */}
            <div className="flex items-center gap-2 flex-wrap">
              {formTags.map(tag => (
                <span key={tag} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-primary/10 text-primary text-xs">
                  <Hash size={10} />
                  {tag}
                  <button onClick={() => removeTag(tag)} className="hover:text-primary/70">
                    <X size={10} />
                  </button>
                </span>
              ))}
              <div className="flex items-center gap-1">
                <input
                  value={tagInput}
                  onChange={e => setTagInput(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addTag(); } }}
                  placeholder={t('plugins.addTagPlaceholder')}
                  className="w-20 text-xs bg-transparent outline-none placeholder:text-muted-foreground/50"
                />
              </div>
              {/* Pin toggle */}
              <button
                onClick={() => setFormPinned(!formPinned)}
                className={`ml-auto flex items-center gap-1 px-2 py-0.5 rounded-md text-xs transition-colors ${
                  formPinned ? 'bg-amber-500/10 text-amber-500' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                <Pin size={10} />
                {formPinned ? t('plugins.pinnedLabel') : t('plugins.pinLabel')}
              </button>
            </div>

            {/* Content */}
            {preview ? (
              <div className="min-h-[300px] p-3 lg:p-4 rounded-xl border border-border bg-card">
                {formContent ? (
                  <MarkdownPreview content={formContent} />
                ) : (
                  <p className="text-muted-foreground text-xs lg:text-sm italic">{t('plugins.nothingToPreview')}</p>
                )}
              </div>
            ) : (
              <textarea
                value={formContent}
                onChange={e => setFormContent(e.target.value)}
                placeholder={t('plugins.contentPlaceholder')}
                className="w-full min-h-[300px] bg-transparent outline-none resize-none text-xs lg:text-sm leading-relaxed font-mono placeholder:text-muted-foreground/50"
              />
            )}
          </div>
        </div>
      ) : (
        /* Empty state */
        <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground relative">
          {isMobile && (
            <div className="absolute top-2.5 left-3">
              <button onClick={() => setShowSidebar(true)} className="p-1.5 rounded hover:bg-muted">
                <PanelLeft size={18} className="text-muted-foreground" />
              </button>
            </div>
          )}
          <StickyNote size={48} className="mb-4 opacity-20" />
          <p className="text-xs lg:text-sm mb-2">{t('plugins.selectOrCreate')}</p>
          <button onClick={openCreate}
            className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-xs lg:text-sm hover:bg-primary/90 flex items-center gap-2">
            <Plus size={14} />
            {t('plugins.newNoteBtn')}
          </button>
        </div>
      )}
    </div>
  );
}

function NoteListContent({
  notes, pinnedNotes, regularNotes,
  searchQuery, setSearchQuery,
  filterTag, setFilterTag,
  allTags, stats,
  editingNote, openEdit, openCreate,
  handleTogglePin, handleDelete,
  fetchNotes, fetchTags, fetchStats,
  loading, t,
}: {
  notes: Note[]; pinnedNotes: Note[]; regularNotes: Note[];
  searchQuery: string; setSearchQuery: (q: string) => void;
  filterTag: string | null; setFilterTag: (tag: string | null) => void;
  allTags: string[]; stats: { total: number; pinned: number; created_today: number };
  editingNote: Note | null; openEdit: (note: Note) => void; openCreate: () => void;
  handleTogglePin: (note: Note) => void; handleDelete: (id: string) => void;
  fetchNotes: () => void; fetchTags: () => void; fetchStats: () => void;
  loading: boolean; t: (k: string, o?: Record<string, unknown>) => string;
}) {
  return (
    <>
      {/* Header */}
      <div className="p-4 border-b border-border">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <StickyNote size={18} className="text-primary" />
            <h2 className="font-semibold text-xs lg:text-sm">{t('plugins.quickNotesTitle')}</h2>
          </div>
          <div className="flex gap-1.5">
            <button onClick={() => { fetchNotes(); fetchTags(); fetchStats(); }}
              className="p-1.5 rounded-lg hover:bg-muted transition-colors" title="刷新">
              <RefreshCw size={14} className="text-muted-foreground" />
            </button>
            <button onClick={openCreate}
              className="p-1.5 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
              <Plus size={14} />
            </button>
          </div>
        </div>

        {/* Search */}
        <div className="relative mb-3">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder={t('plugins.searchNotes')}
            className="w-full pl-8 pr-3 py-1.5 rounded-lg border border-border bg-muted text-xs outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>

        {/* Stats */}
        <div className="flex gap-3 text-[10px] text-muted-foreground">
          <span>{t('plugins.notesCount', { count: stats.total })}</span>
          <span>{t('plugins.pinnedCount', { count: stats.pinned })}</span>
          <span>{t('plugins.todayCount', { count: stats.created_today })}</span>
        </div>
      </div>

      {/* Tags */}
      {allTags.length > 0 && (
        <div className="px-4 py-2 border-b border-border flex flex-wrap gap-1">
          <button
            onClick={() => setFilterTag(null)}
            className={`px-2 py-0.5 rounded-md text-[10px] transition-colors ${
              !filterTag ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {t('plugins.all')}
          </button>
          {allTags.map(tag => (
            <button
              key={tag}
              onClick={() => setFilterTag(filterTag === tag ? null : tag)}
              className={`px-2 py-0.5 rounded-md text-[10px] transition-colors flex items-center gap-1 ${
                filterTag === tag
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted text-muted-foreground hover:text-foreground'
              }`}
            >
              <Hash size={8} />
              {tag}
            </button>
          ))}
        </div>
      )}

      {/* Notes list */}
      <div className="flex-1 overflow-y-auto">
        {pinnedNotes.length > 0 && (
          <div className="px-3 pt-2">
            <p className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1 px-1 flex items-center gap-1">
              <Pin size={8} /> {t('plugins.pinnedSection')}
            </p>
            {pinnedNotes.map(note => (
              <NoteCard
                key={note.id}
                note={note}
                active={editingNote?.id === note.id}
                onClick={() => openEdit(note)}
                onTogglePin={() => handleTogglePin(note)}
                onDelete={() => handleDelete(note.id)}
                t={t}
              />
            ))}
          </div>
        )}

        <div className="px-3 pt-2 pb-4">
          {pinnedNotes.length > 0 && regularNotes.length > 0 && (
            <p className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1 px-1">
              {t('plugins.recentSection')}
            </p>
          )}
          {regularNotes.length === 0 && pinnedNotes.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <StickyNote size={32} className="mb-2 opacity-30" />
              <p className="text-xs">{t('plugins.noNotesYet')}</p>
              <button onClick={openCreate} className="text-xs text-primary mt-2 hover:underline">
                {t('plugins.createFirstNote')}
              </button>
            </div>
          ) : (
            regularNotes.map(note => (
              <NoteCard
                key={note.id}
                note={note}
                active={editingNote?.id === note.id}
                onClick={() => openEdit(note)}
                onTogglePin={() => handleTogglePin(note)}
                onDelete={() => handleDelete(note.id)}
                t={t}
              />
            ))
          )}
        </div>
      </div>
    </>
  );
}

function NoteCard({
  note, active, onClick, onTogglePin, onDelete, t,
}: {
  note: Note;
  active: boolean;
  onClick: () => void;
  onTogglePin: () => void;
  onDelete: () => void;
  t: (k: string, o?: Record<string, unknown>) => string;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);

  return (
    <button
      onClick={onClick}
      className={`w-full text-left p-2.5 rounded-lg mb-1 transition-all group ${
        active
          ? 'bg-primary/10 border border-primary/30'
          : 'hover:bg-muted border border-transparent'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            {note.pinned && <Pin size={10} className="text-amber-500 shrink-0" />}
            <p className="text-xs font-medium truncate">
              {note.title || t('plugins.untitled')}
            </p>
          </div>
          <p className="text-[10px] text-muted-foreground truncate mt-0.5">
            {note.content.slice(0, 60) || t('plugins.emptyNote')}
          </p>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-[9px] text-muted-foreground/60">{timeAgo(note.updated_at, t)}</span>
            {note.tags.slice(0, 2).map(tag => (
              <span key={tag} className="text-[9px] text-primary/60">#{tag}</span>
            ))}
            {note.tags.length > 2 && (
              <span className="text-[9px] text-muted-foreground/40">+{note.tags.length - 2}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-0.5 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity shrink-0">
          <button
            onClick={e => { e.stopPropagation(); onTogglePin(); }}
            className="p-1 rounded hover:bg-background transition-colors"
            title={note.pinned ? t('plugins.unpinLabel') : t('plugins.pinLabel')}
          >
            <Pin size={10} className={note.pinned ? 'text-amber-500' : 'text-muted-foreground'} />
          </button>
          {confirmDelete ? (
            <button
              onClick={e => { e.stopPropagation(); onDelete(); }}
              className="p-1 rounded hover:bg-red-500/10 transition-colors"
              title={t('plugins.confirmDelete')}
            >
              <Check size={10} className="text-red-500" />
            </button>
          ) : (
            <button
              onClick={e => { e.stopPropagation(); setConfirmDelete(true); setTimeout(() => setConfirmDelete(false), 2000); }}
              className="p-1 rounded hover:bg-red-500/10 transition-colors"
              title={t('plugins.deleteLabel')}
            >
              <Trash2 size={10} className="text-muted-foreground" />
            </button>
          )}
        </div>
      </div>
    </button>
  );
}
