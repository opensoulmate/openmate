'use client';
import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { getApiBaseUrl, getToken } from '@/lib/api-client';
import {
  Sparkles, Brain, GitBranch, BarChart3, Network, Loader2,
  History as HistoryIcon, Trash2, FileText, AlertTriangle,
} from 'lucide-react';
import { VisionCanvas } from './vision-canvas';

export type VisionKind = 'mindmap' | 'flowchart' | 'chart' | 'diagram';

export interface GenerationRecord {
  id: string;
  kind: string;
  title: string;
  spec: string;
  created_at: string;
}

interface KnowledgeItem {
  id: string;
  title?: string;
}

const KINDS: { value: VisionKind; icon: typeof Brain; labelKey: string; label: string }[] = [
  { value: 'mindmap', icon: Brain, labelKey: 'vision.kind.mindmap', label: '思维导图' },
  { value: 'flowchart', icon: GitBranch, labelKey: 'vision.kind.flowchart', label: '流程图' },
  { value: 'chart', icon: BarChart3, labelKey: 'vision.kind.chart', label: '数据图' },
  { value: 'diagram', icon: Network, labelKey: 'vision.kind.diagram', label: '示意图' },
];

// 三区工作台：生成（输入）→ 画布（渲染）→ 历史（回看）
export function VisionWorkspace() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();

  const [sourceText, setSourceText] = useState('');
  const [knowledgeId, setKnowledgeId] = useState('');
  const [knowledgeList, setKnowledgeList] = useState<KnowledgeItem[]>([]);
  const [kind, setKind] = useState<VisionKind>('mindmap');
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');
  const [current, setCurrent] = useState<GenerationRecord | null>(null);
  const [history, setHistory] = useState<GenerationRecord[]>([]);

  const authHeaders = useCallback((): Record<string, string> => {
    const token = getToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/vision/generations?limit=50`, { headers: authHeaders() });
      if (res.ok) {
        const data = await res.json();
        setHistory(data.generations || []);
      }
    } catch {
      // 历史加载失败不打扰主流程（画布/生成不受影响）
    }
  }, [apiBase, authHeaders]);

  const fetchKnowledge = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/knowledge/?limit=100`, { headers: authHeaders() });
      if (res.ok) {
        const data = await res.json();
        setKnowledgeList((Array.isArray(data) ? data : []).map((k: { id: string; title?: string }) => ({ id: k.id, title: k.title })));
      }
    } catch {
      // 知识列表加载失败→只能手输文本（降级不阻断）
    }
  }, [apiBase, authHeaders]);

  useEffect(() => {
    fetchHistory();
    fetchKnowledge();
  }, [fetchHistory, fetchKnowledge]);

  const handleGenerate = useCallback(async () => {
    if (!sourceText.trim() && !knowledgeId) return;
    setGenerating(true);
    setError('');
    try {
      const res = await fetch(`${apiBase}/api/vision/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          kind,
          // 选了知识条目→按 knowledge_id 回读正文；否则直用文本框
          source_text: knowledgeId ? '' : sourceText,
          knowledge_id: knowledgeId || null,
        }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        throw new Error(data?.detail || `HTTP ${res.status}`);
      }
      setCurrent(data as GenerationRecord);
      fetchHistory();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }, [sourceText, knowledgeId, kind, apiBase, authHeaders, fetchHistory]);

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await fetch(`${apiBase}/api/vision/generations/${id}`, { method: 'DELETE', headers: authHeaders() });
        setHistory((h) => h.filter((r) => r.id !== id));
        setCurrent((c) => (c && c.id === id ? null : c));
      } catch {
        // 删除失败静默（列表刷新后自愈）
      }
    },
    [apiBase, authHeaders],
  );

  const canGenerate = !generating && (!!knowledgeId || !!sourceText.trim());

  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-3 lg:grid-cols-[320px_minmax(0,1fr)_280px]">
      {/* ── 区 1：生成 ── */}
      <div className="flex min-h-0 flex-col rounded-xl border border-border bg-card overflow-hidden">
        <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
          <Sparkles size={14} className="text-indigo-500" />
          <h2 className="text-xs lg:text-sm font-semibold">{t('vision.generate.title', '生成')}</h2>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
          {/* 类型选择（选中态 bg-primary/10 rounded） */}
          <div className="grid grid-cols-2 gap-1.5">
            {KINDS.map((k) => {
              const Icon = k.icon;
              const selected = kind === k.value;
              return (
                <button
                  key={k.value}
                  onClick={() => setKind(k.value)}
                  className={cn(
                    'flex items-center gap-1.5 rounded px-2 py-2 text-xs transition-colors border',
                    selected
                      ? 'bg-primary/10 rounded border-primary/40 text-foreground font-medium'
                      : 'border-transparent text-muted-foreground hover:bg-muted',
                  )}
                >
                  <Icon size={13} className={selected ? 'text-primary' : ''} />
                  {t(k.labelKey, k.label)}
                </button>
              );
            })}
          </div>

          {/* 选知识 */}
          <div>
            <label className="mb-1 block text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              {t('vision.generate.pickKnowledge', '选择知识')}
            </label>
            <select
              value={knowledgeId}
              onChange={(e) => setKnowledgeId(e.target.value)}
              className="w-full rounded-lg border border-border bg-background px-2 py-2 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary/50"
            >
              <option value="">{t('vision.generate.manualInput', '手动输入文本')}</option>
              {knowledgeList.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.title || k.id}
                </option>
              ))}
            </select>
          </div>

          {/* 文本框 */}
          <div>
            <label className="mb-1 block text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              {t('vision.generate.sourceText', '知识内容')}
            </label>
            <textarea
              value={sourceText}
              onChange={(e) => setSourceText(e.target.value)}
              disabled={!!knowledgeId}
              placeholder={
                knowledgeId
                  ? t('vision.generate.usingKnowledge', '已选知识条目，将读取其正文')
                  : t('vision.generate.placeholder', '粘贴知识文本：标题/要点/步骤/数字序列均可被抽取')
              }
              className="h-40 w-full resize-y rounded-lg border border-border bg-background px-2.5 py-2 text-xs leading-relaxed text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-primary/50 disabled:opacity-50"
            />
          </div>

          {error && (
            <div className="flex items-start gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 p-2.5 text-xs text-red-500">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span className="break-all">{error}</span>
            </div>
          )}

          <button
            onClick={handleGenerate}
            disabled={!canGenerate}
            className={cn(
              'flex w-full items-center justify-center gap-1.5 rounded-lg px-3 py-2.5 text-xs font-medium transition-colors',
              canGenerate
                ? 'bg-primary text-primary-foreground hover:bg-primary/90'
                : 'bg-muted text-muted-foreground opacity-60',
            )}
          >
            {generating ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {generating
              ? t('vision.generate.running', '生成中…')
              : t('vision.generate.button', '生成可视化')}
          </button>
        </div>
      </div>

      {/* ── 区 2：画布 ── */}
      <div className="flex min-h-0 flex-col gap-2">
        <div className="flex items-center gap-2 px-1">
          <FileText size={14} className="text-indigo-500" />
          <h2 className="text-xs lg:text-sm font-semibold truncate">
            {current?.title || t('vision.canvas.title', '画布')}
          </h2>
          {current && (
            <span className="shrink-0 rounded-full bg-indigo-500/10 px-2 py-0.5 text-[10px] font-medium text-indigo-500">
              {current.kind}
            </span>
          )}
        </div>
        <div className="min-h-[320px] flex-1">
          <VisionCanvas spec={current?.spec || ''} kind={current?.kind || kind} title={current?.title} />
        </div>
      </div>

      {/* ── 区 3：历史 ── */}
      <div className="flex min-h-0 flex-col rounded-xl border border-border bg-card overflow-hidden">
        <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
          <HistoryIcon size={14} className="text-indigo-500" />
          <h2 className="text-xs lg:text-sm font-semibold">{t('vision.history.title', '历史')}</h2>
          <span className="rounded-full bg-indigo-500/10 px-1.5 py-0.5 text-[10px] font-medium text-indigo-500">
            {history.length}
          </span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto p-2 space-y-1">
          {history.length === 0 ? (
            <div className="px-2 py-6 text-center text-xs text-muted-foreground">
              {t('vision.history.empty', '暂无生成记录')}
            </div>
          ) : (
            history.map((rec) => {
              const selected = current?.id === rec.id;
              return (
                <div
                  key={rec.id}
                  className={cn(
                    'group flex items-start gap-1.5 rounded px-2 py-2 cursor-pointer transition-colors',
                    selected ? 'bg-primary/10 rounded' : 'hover:bg-muted',
                  )}
                  onClick={() => setCurrent(rec)}
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium">{rec.title || rec.id}</div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                      <span className="rounded bg-muted px-1 py-px">{rec.kind}</span>
                      <span className="tabular-nums">{rec.created_at}</span>
                    </div>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDelete(rec.id);
                    }}
                    className="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:text-red-500 group-hover:opacity-100"
                    title={t('vision.history.delete', '删除')}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
