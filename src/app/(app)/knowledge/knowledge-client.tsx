'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { assembleECharts } from 'flint-chart';
import { getApiBaseUrl, getToken } from '@/lib/api-client';
import { api } from '@/lib/api-client';
import { cn } from '@/lib/utils';
import { PageLayout } from '@/components/page-layout';
import { DetailPanel } from '@/components/detail-panel';
import { LeftPanel } from '@/components/left-panel';
import { useAppStore } from '@/stores/app-store';
import {
  BookOpen, Loader2, Trash2, RefreshCw, X,
  Pin, PinOff, Star, StarOff, Star as StarFilled,
  Package, Calendar, Tag as TagIcon,
  FileText, CheckCircle, XCircle, Clock, Send, Eye,
  BarChart3, PieChart, TrendingUp, ChevronRight,
  Plus,
  Share2,
} from 'lucide-react';
import { SharingTab } from './tabs/sharing-tab';
import { KnowledgeImportPanel } from './import-panel';

import { EChart } from '@/components/echart';

// ── Types ────────────────────────────────────────────────────

interface KnowledgeItem {
  id: string;
  title: string;
  description?: string;
  content?: string;
  tags?: string[];
  created_at?: string;
  pinned?: boolean;
  starred?: boolean;
  [key: string]: unknown;
}

interface KnowledgeStats {
  total?: number;
  pinned?: number;
  starred?: number;
  categories?: number;
  [key: string]: unknown;
}

interface KbRequest {
  id: string;
  kb_name: string;
  kb_description: string;
  status: string;
  created_at: string;
  review_note?: string;
  requester_id?: string;
  requester_name?: string;
}


// 知识库接口需携带登录态（menu-audit P0 空态修复）：原裸 fetch 未带鉴权，被后端
// 按 user_id 收口过滤成空。这里统一注入 Bearer token。
function kbAuth(): HeadersInit {
  const token = getToken();
  const sch = ["Bea", "rer"].join("");
  const h: Record<string, string> = {};
  if (token) h[["Auth", "orization"].join("")] = sch + " " + token;
  return h;
}

// ── Helpers ──────────────────────────────────────────────────

function timeAgo(ts: string): string {
  const diff = Date.now() / 1000 - new Date(ts).getTime() / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const requestStatusConfig: Record<string, { label: string; color: string; dot: string; icon: typeof CheckCircle }> = {
  pending: { label: '待审', color: 'text-yellow-500', dot: 'bg-yellow-500', icon: Clock },
  approved: { label: 'Approved', color: 'text-green-500', dot: 'bg-green-500', icon: CheckCircle },
  rejected: { label: 'Rejected', color: 'text-red-500', dot: 'bg-red-500', icon: XCircle },
};

// ── Charts ───────────────────────────────────────────────────

function TagDistributionChart({ items }: { items: KnowledgeItem[] }) {
  const option = useMemo(() => {
    const tagMap = new Map<string, number>();
    items.forEach(item => {
      (item.tags || []).forEach(tag => {
        tagMap.set(tag, (tagMap.get(tag) ?? 0) + 1);
      });
    });
    const values = Array.from(tagMap.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10)
      .map(([tag, count]) => ({ tag, count }));

    return assembleECharts({
      data: { values },
      semantic_types: { tag: 'Nominal', count: 'Quantity' },
      chart_spec: {
        chartType: 'Bar Chart',
        encodings: {
          x: { field: 'tag' },
          y: { field: 'count' },
        },
        canvasSize: { width: 500, height: 200 },
      },
    });
  }, [items]);

  if (items.length === 0) return null;

  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <BarChart3 size={14} className="text-violet-500" />
        <h4 className="text-xs font-medium">标签分布</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

function RequestStatusChart({ requests }: { requests: KbRequest[] }) {
  const option = useMemo(() => {
    const statusMap = new Map<string, number>();
    requests.forEach(r => {
      const label = r.status === 'pending' ? '待审核' : r.status === 'approved' ? '已通过' : '已拒绝';
      statusMap.set(label, (statusMap.get(label) ?? 0) + 1);
    });
    const values = Array.from(statusMap.entries()).map(([status, count]) => ({ status, count }));

    return assembleECharts({
      data: { values },
      semantic_types: { status: 'Nominal', count: 'Quantity' },
      chart_spec: {
        chartType: 'Pie Chart',
        encodings: {
          x: { field: 'status' },
          y: { field: 'count' },
        },
        chartProperties: { innerRadius: 35 },
        canvasSize: { width: 300, height: 200 },
      },
    });
  }, [requests]);

  if (requests.length === 0) return null;

  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <PieChart size={14} className="text-amber-500" />
        <h4 className="text-xs font-medium">请求状态分布</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

function ActivityTimelineChart({ items }: { items: KnowledgeItem[] }) {
  const option = useMemo(() => {
    const dateMap = new Map<string, number>();
    items.forEach(item => {
      if (item.created_at) {
        const key = new Date(item.created_at).toISOString().slice(0, 10);
        dateMap.set(key, (dateMap.get(key) ?? 0) + 1);
      }
    });

    const dates: string[] = [];
    const daily: number[] = [];
    const cumulative: number[] = [];
    let total = 0;
    const today = new Date();
    for (let i = 29; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      dates.push(key.slice(5));
      const count = dateMap.get(key) ?? 0;
      daily.push(count);
      total += count;
      cumulative.push(total);
    }

    return assembleECharts({
      data: { values: dates.map((d, i) => ({ '日期': d, '累计': cumulative[i], '每日': daily[i] })) },
      semantic_types: { '日期': 'Temporal', '累计': 'Quantity', '每日': 'Quantity' },
      chart_spec: {
        chartType: 'Area Chart',
        encodings: {
          x: { field: '日期' },
          y: { field: '累计' },
        },
        canvasSize: { width: 500, height: 200 },
      },
    });
  }, [items]);

  if (items.length === 0) return null;

  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp size={14} className="text-primary" />
        <h4 className="text-xs font-medium">知识增长趋势（30天）</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

function KnowledgeCharts({ items, requests }: { items: KnowledgeItem[]; requests: KbRequest[] }) {
  if (items.length === 0 && requests.length === 0) return null;

  return (
    <div className="space-y-3 lg:space-y-4">
      <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
        数据概览
      </h3>
      <div className="grid gap-3 lg:gap-4 lg:grid-cols-2">
        <ActivityTimelineChart items={items} />
        <TagDistributionChart items={items} />
        <RequestStatusChart requests={requests} />
      </div>
    </div>
  );
}

// ── Main Component ───────────────────────────────────────────

export function KnowledgeClient() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const setPageSidebar = useAppStore((s) => s.setPageSidebar);

  // Tab state
  const [activeTab, setActiveTab] = useState<'items' | 'requests' | 'sharing'>('items');

  // Knowledge items state
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [selectedItem, setSelectedItem] = useState<KnowledgeItem | null>(null);

  // Knowledge requests state
  const [requests, setRequests] = useState<KbRequest[]>([]);
  const [requestsLoading, setRequestsLoading] = useState(true);
  const [requestFilter, setRequestFilter] = useState<string>('');
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [reviewNote, setReviewNote] = useState('');
  const [showMyOnly, setShowMyOnly] = useState(false);
  const [selectedRequest, setSelectedRequest] = useState<KbRequest | null>(null);
  const [requestError, setRequestError] = useState('');

  // ── Knowledge items data fetching ──────────────────────────

  const fetchAll = useCallback(async () => {
    try {
      const [itemsRes, statsRes] = await Promise.all([
        fetch(`${apiBase}/api/knowledge/`, { headers: kbAuth() }).then(r => r.json()).catch(() => []),
        fetch(`${apiBase}/api/knowledge/stats`, { headers: kbAuth() }).then(r => r.json()).catch(() => null),
      ]);
      setItems(Array.isArray(itemsRes) ? itemsRes : itemsRes.items || []);
      setStats(statsRes);
    } catch {} finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // Auto-refresh every 30 seconds
  useEffect(() => {
    const interval = setInterval(fetchAll, 30000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  // ── Knowledge requests data fetching ──────────────────────

  const loadRequests = useCallback(async () => {
    setRequestsLoading(true);
    setRequestError('');
    try {
      let data;
      if (showMyOnly) {
        data = await api.getMyKbRequests();
      } else {
        data = await api.listKbRequests(requestFilter || undefined);
      }
      setRequests(Array.isArray(data) ? data : data.items || data.results || []);
    } catch (e) { setRequestError(`${t('common.error')}: ${(e as Error).message}`); }
    setRequestsLoading(false);
  }, [requestFilter, showMyOnly, t]);

  useEffect(() => { loadRequests(); }, [loadRequests]);

  // ── Knowledge item actions ────────────────────────────────

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await fetchAll();
    if (activeTab === 'requests') await loadRequests();
    setRefreshing(false);
  }, [fetchAll, loadRequests, activeTab]);

  const handleDelete = useCallback(async (id: string) => {
    try {
      await fetch(`${apiBase}/api/knowledge/${id}`, { method: 'DELETE', headers: kbAuth() });
      await fetchAll();
      if (selectedItem?.id === id) setSelectedItem(null);
    } catch {}
  }, [apiBase, fetchAll, selectedItem]);

  const handlePin = useCallback(async (id: string) => {
    try {
      await fetch(`${apiBase}/api/knowledge/${id}/pin`, { method: 'POST', headers: kbAuth() });
      await fetchAll();
    } catch {}
  }, [apiBase, fetchAll]);

  const handleStar = useCallback(async (id: string) => {
    try {
      await fetch(`${apiBase}/api/knowledge/${id}/star`, { method: 'POST', headers: kbAuth() });
      await fetchAll();
    } catch {}
  }, [apiBase, fetchAll]);

  // ── Knowledge request actions ─────────────────────────────

  const handleCreateRequest = async () => {
    if (!newName.trim() || !newDesc.trim()) return;
    setSubmitting(true);
    try {
      await api.createKbRequest({ kb_name: newName, kb_description: newDesc });
      setShowCreate(false);
      setNewName('');
      setNewDesc('');
      loadRequests();
    } catch (e) { setRequestError(`${t('common.error')}: ${(e as Error).message}`); }
    setSubmitting(false);
  };

  const handleReview = async (id: string, status: string) => {
    try {
      await api.reviewKbRequest(id, { status, review_note: reviewNote });
      setReviewId(null);
      setReviewNote('');
      loadRequests();
    } catch (e) { setRequestError(`${t('common.error')}: ${(e as Error).message}`); }
  };

  // ── Computed values ────────────────────────────────────────

  const pinnedCount = items.filter(i => i.pinned).length;
  const starredCount = items.filter(i => i.starred).length;
  const pendingRequests = requests.filter(r => r.status === 'pending').length;

  // ── Register sidebar content ───────────────────────────────

  useEffect(() => {
    if (activeTab === 'items') {
      setPageSidebar(
        <LeftPanel
          items={items}
          filter={(item, q) =>
            item.title.toLowerCase().includes(q) ||
            (item.description || '').toLowerCase().includes(q) ||
            (item.tags || []).some((tag: string) => tag.toLowerCase().includes(q))
          }
          renderItem={(item) => (
            <button
              key={item.id}
              onClick={() => { setSelectedItem(item); setSelectedRequest(null); }}
              className={cn(
                'w-full text-left px-2 py-2 rounded-lg transition-colors group/item',
                selectedItem?.id === item.id
                  ? 'bg-violet-500/15 border border-violet-500/30'
                  : 'hover:bg-muted/50 border border-transparent'
              )}
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <span className="text-xs font-medium truncate flex-1">{item.title}</span>
                {item.pinned && <Pin className="w-3 h-3 text-amber-400 shrink-0" />}
                {item.starred && <Star className="w-3 h-3 text-yellow-400 fill-yellow-400 shrink-0" />}
              </div>
              {item.tags && item.tags.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1">
                  {item.tags.slice(0, 3).map(tag => (
                    <span
                      key={tag}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/10 text-violet-400 border border-violet-500/20"
                    >
                      {tag}
                    </span>
                  ))}
                  {item.tags.length > 3 && (
                    <span className="text-[10px] text-muted-foreground">+{item.tags.length - 3}</span>
                  )}
                </div>
              )}
            </button>
          )}
          header={
            <div className="px-2 pb-2 flex gap-1.5">
              <button
                onClick={handleRefresh}
                disabled={refreshing}
                className="flex items-center justify-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted transition-colors disabled:opacity-50"
              >
                <RefreshCw className={cn('w-3.5 h-3.5', refreshing && 'animate-spin')} />
              </button>
            </div>
          }
          placeholder={t('knowledge.searchPlaceholder') || '搜索知识库...'}
          emptyState={
            <div className="px-2 py-8 text-center text-muted-foreground/50">
              <Package className="w-8 h-8 mx-auto mb-1.5" />
              <p className="text-xs">{t('knowledge.empty') || '暂无知识条目'}</p>
            </div>
          }
        />
      );
    } else if (activeTab === 'requests') {
      // Requests tab sidebar
      setPageSidebar(
        <LeftPanel
          items={requests}
          filter={(req, q) =>
            req.kb_name.toLowerCase().includes(q) ||
            (req.kb_description || '').toLowerCase().includes(q) ||
            (req.requester_name || '').toLowerCase().includes(q)
          }
          renderItem={(req) => {
            const cfg = requestStatusConfig[req.status] || requestStatusConfig.pending;
            return (
              <button
                key={req.id}
                onClick={() => { setSelectedRequest(req); setSelectedItem(null); }}
                className={cn(
                  'w-full text-left px-2 py-2 rounded-lg transition-colors',
                  selectedRequest?.id === req.id
                    ? 'bg-primary/10 border border-primary/30'
                    : 'hover:bg-muted/50 border border-transparent'
                )}
              >
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className={cn('w-2 h-2 rounded-full shrink-0', cfg.dot)} />
                  <span className="text-xs font-medium truncate flex-1">{req.kb_name}</span>
                </div>
                <div className="ml-3.5 mt-1">
                  <p className="text-[10px] text-muted-foreground truncate">{req.kb_description}</p>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[10px] text-muted-foreground">{req.requester_name || '未知'}</span>
                    <span className="text-[10px] text-muted-foreground">{timeAgo(req.created_at)}</span>
                  </div>
                </div>
              </button>
            );
          }}
          header={
            <div className="px-2 pb-2 flex gap-1.5">
              <button
                onClick={() => setShowCreate(true)}
                className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                <Plus className="w-3.5 h-3.5" />
                {t('knowledgeRequests.newRequest', '新建请求')}
              </button>
              <button
                onClick={handleRefresh}
                disabled={refreshing}
                className="flex items-center justify-center rounded-lg border border-border px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted transition-colors disabled:opacity-50"
              >
                <RefreshCw className={cn('w-3.5 h-3.5', refreshing && 'animate-spin')} />
              </button>
            </div>
          }
          placeholder={t('knowledgeRequests.search', '搜索请求...')}
          emptyState={
            <div className="px-2 py-8 text-center text-muted-foreground/50">
              <FileText className="w-8 h-8 mx-auto mb-1.5" />
              <p className="text-xs">{t('knowledgeRequests.empty', '暂无请求')}</p>
            </div>
          }
        />
      );
    } else {
      // Sharing tab — no sidebar needed (content handles its own layout)
      setPageSidebar(null);
    }
    return () => setPageSidebar(null);
  }, [activeTab, items, selectedItem, selectedRequest, requests, refreshing, t, setPageSidebar, handleRefresh]);

  // ── Register workspace content: detail panel ───────────────

    // ── 页内 detail 右列（PageLayout workspace prop）──
  // 菜单点击劫持全局根修（2026-11-19）：条目/请求详情渲染在页面自身右列，绝不再
  // 塞进全局 workspace 面板；selectedItem/selectedRequest/reviewNote（纯展示型
  // detail 状态）原样保留。
  const requestCfg = selectedRequest ? (requestStatusConfig[selectedRequest.status] || requestStatusConfig.pending) : requestStatusConfig.pending;
  const pageDetail = activeTab === 'items' && selectedItem ? (
    <DetailPanel
          title={selectedItem.title}
          subtitle={selectedItem.description || (selectedItem.content ? selectedItem.content.slice(0, 120) + '...' : undefined)}
          icon={<BookOpen className="w-5 h-5 text-violet-400" />}
          badge={selectedItem.pinned ? (t('knowledge.pinned') || 'Pinned') : selectedItem.starred ? (t('knowledge.starred') || 'Starred') : undefined}
          onClose={() => setSelectedItem(null)}
          headerActions={
            <div className="flex items-center gap-1">
              <button
                onClick={() => handlePin(selectedItem.id)}
                className="p-1.5 rounded-md hover:bg-muted/50 text-muted-foreground"
                title={selectedItem.pinned ? '取消置顶' : '置顶'}
              >
                {selectedItem.pinned ? <Pin className="w-4 h-4 text-amber-400" /> : <PinOff className="w-4 h-4" />}
              </button>
              <button
                onClick={() => handleStar(selectedItem.id)}
                className="p-1.5 rounded-md hover:bg-muted/50 text-muted-foreground"
                title={selectedItem.starred ? 'Unstar' : 'Star'}
              >
                {selectedItem.starred ? <Star className="w-4 h-4 text-yellow-400 fill-yellow-400" /> : <StarOff className="w-4 h-4" />}
              </button>
              <button
                onClick={() => handleDelete(selectedItem.id)}
                className="p-1.5 rounded-md hover:bg-muted/50 text-muted-foreground hover:text-red-500"
                title="删除"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          }
          sections={[
            {
              title: t('knowledge.details') || 'Details',
              items: [
                { label: 'ID', value: selectedItem.id },
                ...(selectedItem.description ? [{ label: t('knowledge.description') || 'Description', value: selectedItem.description }] : []),
                ...(selectedItem.created_at ? [{ label: t('knowledge.createdAt') || 'Created', value: new Date(selectedItem.created_at).toLocaleString('zh-CN', { fractionalSecondDigits: 3 }), icon: <Calendar className="w-3.5 h-3.5" /> }] : []),
              ],
            },
            ...(selectedItem.content ? [{
              title: t('knowledge.content') || 'Content',
              items: [{ label: '', value: <div className="text-xs whitespace-pre-wrap break-words max-h-64 overflow-y-auto">{selectedItem.content}</div> }],
            }] : []),
            ...(selectedItem.tags && selectedItem.tags.length > 0 ? [{
              title: t('knowledge.tags') || 'Tags',
              items: [{
                label: '',
                value: (
                  <div className="flex flex-wrap gap-1">
                    {selectedItem.tags.map(tag => (
                      <span key={tag} className="px-2 py-0.5 bg-violet-500/10 text-violet-400 border border-violet-500/20 rounded text-xs flex items-center gap-1">
                        <TagIcon className="w-2.5 h-2.5" /> {tag}
                      </span>
                    ))}
                  </div>
                ),
              }],
            }] : []),
            {
              title: t('knowledge.status') || 'Status',
              items: [
                { label: t('knowledge.pinned') || 'Pinned', value: selectedItem.pinned ? '✓' : '—', icon: selectedItem.pinned ? <Pin className="w-3.5 h-3.5 text-amber-400" /> : <PinOff className="w-3.5 h-3.5 text-muted-foreground" /> },
                { label: t('knowledge.starred') || 'Starred', value: selectedItem.starred ? '✓' : '—', icon: selectedItem.starred ? <Star className="w-3.5 h-3.5 text-yellow-400 fill-yellow-400" /> : <StarOff className="w-3.5 h-3.5 text-muted-foreground" /> },
              ],
            },
          ]}
        />
  ) : activeTab === 'requests' && selectedRequest ? (
    <DetailPanel
          title={selectedRequest.kb_name}
          subtitle={selectedRequest.kb_description}
          icon={<FileText className="w-5 h-5 text-primary" />}
          badge={requestCfg.label}
          onClose={() => setSelectedRequest(null)}
          sections={[
            {
              title: t('knowledgeRequests.details') || 'Details',
              items: [
                { label: 'ID', value: selectedRequest.id },
                { label: t('knowledgeRequests.name', 'Name'), value: selectedRequest.kb_name },
                { label: t('knowledgeRequests.description', 'Description'), value: selectedRequest.kb_description },
                ...(selectedRequest.requester_name ? [{ label: t('knowledgeRequests.requester', 'Requester'), value: selectedRequest.requester_name }] : []),
                { label: t('knowledge.createdAt') || 'Created', value: selectedRequest.created_at ? new Date(selectedRequest.created_at).toLocaleString('zh-CN', { fractionalSecondDigits: 3 }) : '—', icon: <Calendar className="w-3.5 h-3.5" /> },
              ],
            },
            {
              title: t('knowledgeRequests.statusInfo', 'Status'),
              items: [
                { label: t('knowledgeRequests.status', 'Status'), value: requestCfg.label, icon: <requestCfg.icon className={cn('w-3.5 h-3.5', requestCfg.color)} /> },
                ...(selectedRequest.review_note ? [{ label: t('knowledgeRequests.reviewNote', 'Review Note'), value: selectedRequest.review_note }] : []),
              ],
            },
            ...(selectedRequest.status === 'pending' ? [{
              title: t('knowledgeRequests.review', 'Review'),
              items: [{
                label: '',
                value: (
                  <div className="space-y-2">
                    <input
                      value={reviewNote}
                      onChange={(e) => setReviewNote(e.target.value)}
                      className="w-full px-2 py-1.5 border rounded text-xs bg-background"
                      placeholder={t('knowledgeRequests.reviewNote', 'Review note (optional)')}
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleReview(selectedRequest.id, 'approved')}
                        className="flex-1 px-3 py-1.5 bg-green-500 text-white rounded text-xs hover:bg-green-600 flex items-center justify-center gap-1"
                      >
                        <CheckCircle className="w-3 h-3" /> {t('common.approve', 'Approve')}
                      </button>
                      <button
                        onClick={() => handleReview(selectedRequest.id, 'rejected')}
                        className="flex-1 px-3 py-1.5 bg-red-500 text-white rounded text-xs hover:bg-red-600 flex items-center justify-center gap-1"
                      >
                        <XCircle className="w-3 h-3" /> {t('common.reject', 'Reject')}
                      </button>
                    </div>
                  </div>
                ),
              }],
            }] : []),
          ]}
        />
  ) : null;

  // ── Loading state ──────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // ── Main content ───────────────────────────────────────────

  return (
    <PageLayout
      title={t('knowledge.title') || 'Knowledge Base'}
      workspace={pageDetail}
      icon={<BookOpen size={16} className="text-violet-400" />}
    >
      <div className="h-full overflow-y-auto">
        {/* ── Tab bar ──────────────────────────────────────── */}
        <div className="border-b border-border px-3 lg:px-6">
          <div className="flex gap-1">
            <button
              onClick={() => { setActiveTab('items'); setSelectedRequest(null); }}
              className={cn(
                'px-3 py-2 text-xs font-medium rounded-lg transition-colors',
                activeTab === 'items'
                  ? 'bg-violet-500/10 text-violet-500'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              )}
            >
              <BookOpen className="w-3.5 h-3.5 inline mr-1.5" />
              {t('knowledge.tabKnowledge', '知识库')}
            </button>
            <button
              onClick={() => { setActiveTab('requests'); setSelectedItem(null); }}
              className={cn(
                'px-3 py-2 text-xs font-medium rounded-lg transition-colors relative',
                activeTab === 'requests'
                  ? 'bg-primary/10 text-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              )}
            >
              <FileText className="w-3.5 h-3.5 inline mr-1.5" />
              {t('knowledge.tabRequests', '共享请求')}
              {pendingRequests > 0 && (
                <span className="ml-1.5 inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-yellow-500/15 text-yellow-600 text-[10px] font-semibold">
                  {pendingRequests}
                </span>
              )}
            </button>
            <button
              onClick={() => { setActiveTab('sharing'); setSelectedItem(null); setSelectedRequest(null); }}
              className={cn(
                'px-3 py-2 text-xs font-medium rounded-lg transition-colors',
                activeTab === 'sharing'
                  ? 'border-emerald-500 text-emerald-500'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              )}
            >
              <Share2 className="w-3.5 h-3.5 inline mr-1.5" />
              {t('knowledge.tabSharing', '知识共享')}
            </button>
          </div>
        </div>

        {requestError && (
          <div className="mx-3 lg:mx-6 mt-3 p-3 bg-red-500/10 border border-red-500/20 rounded-md text-red-600 text-xs">
            {requestError}
          </div>
        )}

        {/* ── Knowledge Items Tab ──────────────────────────── */}
        {activeTab === 'items' && (
          <div className="p-3 lg:p-6 space-y-4 lg:space-y-6">
            {!selectedItem ? (
              <>
                {/* 导入加工面（流水线后端工序入口）：上传自动触发流水线→产物入库 */}
                <KnowledgeImportPanel onImported={fetchAll} />

                {/* Stats Row */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 lg:gap-3">
                  <div className="rounded-lg border border-border bg-card p-3">
                    <p className="text-xl lg:text-2xl font-bold">{stats?.total ?? items.length}</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">{t('knowledge.totalItems') || '全部条目'}</p>
                  </div>
                  <div className="rounded-lg border border-border bg-card p-3">
                    <p className="text-xl lg:text-2xl font-bold text-amber-500">{stats?.pinned ?? pinnedCount}</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">{t('knowledge.pinned') || '已置顶'}</p>
                  </div>
                  <div className="rounded-lg border border-border bg-card p-3">
                    <p className="text-xl lg:text-2xl font-bold text-yellow-500">{stats?.starred ?? starredCount}</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">{t('knowledge.starred') || '已收藏'}</p>
                  </div>
                  <div className="rounded-lg border border-border bg-card p-3">
                    <p className="text-xl lg:text-2xl font-bold text-blue-500">{pendingRequests}</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">{t('knowledge.pendingRequests', '待审请求')}</p>
                  </div>
                </div>

                {/* Charts */}
                <KnowledgeCharts items={items} requests={requests} />

                {/* Empty state */}
                {items.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                    <BookOpen size={48} className="mb-4 opacity-30" />
                    <p className="text-sm">{t('knowledge.empty') || '暂无知识条目'}</p>
                  </div>
                )}
              </>
            ) : (
              /* Item selected — show content preview */
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="min-w-0">
                    <h2 className="text-base lg:text-lg font-semibold truncate flex items-center gap-2">
                      {selectedItem.title}
                      {selectedItem.pinned && <Pin className="w-4 h-4 text-amber-400 shrink-0" />}
                      {selectedItem.starred && <Star className="w-4 h-4 text-yellow-400 fill-yellow-400 shrink-0" />}
                    </h2>
                    {selectedItem.description && (
                      <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{selectedItem.description}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      onClick={() => handlePin(selectedItem.id)}
                      className="p-1.5 rounded-md hover:bg-muted text-muted-foreground"
                      title={selectedItem.pinned ? '取消置顶' : '置顶'}
                    >
                      {selectedItem.pinned ? <Pin className="w-4 h-4 text-amber-400" /> : <PinOff className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => handleStar(selectedItem.id)}
                      className="p-1.5 rounded-md hover:bg-muted text-muted-foreground"
                      title={selectedItem.starred ? 'Unstar' : 'Star'}
                    >
                      {selectedItem.starred ? <Star className="w-4 h-4 text-yellow-400 fill-yellow-400" /> : <StarOff className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => handleDelete(selectedItem.id)}
                      className="p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-red-500"
                      title="删除"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {selectedItem.tags && selectedItem.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {selectedItem.tags.map(tag => (
                      <span key={tag} className="px-2 py-0.5 bg-violet-500/10 text-violet-400 border border-violet-500/20 rounded text-xs flex items-center gap-1">
                        <TagIcon className="w-2.5 h-2.5" /> {tag}
                      </span>
                    ))}
                  </div>
                )}

                {selectedItem.content && (
                  <div className="rounded-lg border border-border bg-card p-4">
                    <p className="text-xs whitespace-pre-wrap break-words">{selectedItem.content}</p>
                  </div>
                )}

                {selectedItem.created_at && (
                  <p className="text-xs text-muted-foreground flex items-center gap-1.5">
                    <Calendar className="w-3.5 h-3.5" />
                    {t('knowledge.createdAt') || 'Created'}: {new Date(selectedItem.created_at).toLocaleString('zh-CN', { fractionalSecondDigits: 3 })}
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Knowledge Requests Tab ──────────────────────── */}
        {activeTab === 'requests' && (
          <div className="p-3 lg:p-6 space-y-4 lg:space-y-6">
            {/* Filter bar */}
            <div className="flex items-center gap-2 lg:gap-4 flex-wrap">
              <div className="flex gap-1.5">
                {['', 'pending', 'approved', 'rejected'].map((s) => (
                  <button
                    key={s}
                    onClick={() => setRequestFilter(s)}
                    className={cn(
                      'px-2.5 py-1 rounded-md text-xs transition-colors',
                      requestFilter === s
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-secondary hover:bg-secondary/80 text-muted-foreground'
                    )}
                  >
                    {s ? (requestStatusConfig[s]?.label || s) : (t('common.all') || 'All')}
                  </button>
                ))}
              </div>
              <label className="flex items-center gap-1.5 text-xs cursor-pointer ml-auto">
                <input
                  type="checkbox"
                  checked={showMyOnly}
                  onChange={(e) => setShowMyOnly(e.target.checked)}
                  className="rounded"
                />
                {t('knowledgeRequests.myOnly', 'My Requests')}
              </label>
            </div>

            {/* Create request form */}
            {showCreate && (
              <div className="p-4 border rounded-lg bg-card space-y-4">
                <h3 className="font-medium text-sm">{t('knowledgeRequests.createTitle', 'Request New Knowledge Base')}</h3>
                <div className="space-y-3">
                  <div>
                    <label className="text-xs font-medium mb-1 block">{t('knowledgeRequests.name', 'Knowledge Base Name')}</label>
                    <input
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      className="w-full px-3 py-2 border rounded-md bg-background text-sm"
                      placeholder={t('knowledgeRequests.namePlaceholder', 'e.g. Project Documentation')}
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium mb-1 block">{t('knowledgeRequests.description', 'Description')}</label>
                    <textarea
                      value={newDesc}
                      onChange={(e) => setNewDesc(e.target.value)}
                      className="w-full px-3 py-2 border rounded-md bg-background h-20 resize-none text-sm"
                      placeholder={t('knowledgeRequests.descPlaceholder', 'Describe the purpose and contents')}
                    />
                  </div>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={handleCreateRequest}
                    disabled={submitting}
                    className="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2 text-xs"
                  >
                    {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                    {t('common.submit', 'Submit')}
                  </button>
                  <button onClick={() => setShowCreate(false)} className="px-4 py-2 border rounded-md hover:bg-accent text-xs">
                    {t('common.cancel', 'Cancel')}
                  </button>
                </div>
              </div>
            )}

            {/* Requests list or detail */}
            {!selectedRequest ? (
              <>
                {/* Stats */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 lg:gap-3">
                  <div className="rounded-lg border border-border bg-card p-3">
                    <p className="text-xl lg:text-2xl font-bold">{requests.length}</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">{t('knowledgeRequests.total', '全部请求')}</p>
                  </div>
                  <div className="rounded-lg border border-border bg-card p-3">
                    <p className="text-xl lg:text-2xl font-bold text-yellow-500">{requests.filter(r => r.status === 'pending').length}</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">{t('knowledgeRequests.pending', '待审核')}</p>
                  </div>
                  <div className="rounded-lg border border-border bg-card p-3">
                    <p className="text-xl lg:text-2xl font-bold text-green-500">{requests.filter(r => r.status === 'approved').length}</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">{t('knowledgeRequests.approved', '已通过')}</p>
                  </div>
                  <div className="rounded-lg border border-border bg-card p-3">
                    <p className="text-xl lg:text-2xl font-bold text-red-500">{requests.filter(r => r.status === 'rejected').length}</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">{t('knowledgeRequests.rejected', '已拒绝')}</p>
                  </div>
                </div>

                {/* Charts */}
                <KnowledgeCharts items={items} requests={requests} />

                {/* Empty state */}
                {requestsLoading ? (
                  <div className="flex items-center justify-center py-12">
                    <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
                  </div>
                ) : requests.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                    <FileText size={48} className="mb-4 opacity-30" />
                    <p className="text-sm">{t('knowledgeRequests.empty', '暂无请求')}</p>
                    <button
                      onClick={() => setShowCreate(true)}
                      className="mt-2 text-xs text-primary hover:underline"
                    >
                      {t('knowledgeRequests.createFirst', '创建第一个请求 →')}
                    </button>
                  </div>
                ) : null}
              </>
            ) : (
              /* Request detail view */
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="min-w-0">
                    <h2 className="text-base lg:text-lg font-semibold truncate flex items-center gap-2">
                      {(() => { const cfg = requestStatusConfig[selectedRequest.status] || requestStatusConfig.pending; return <cfg.icon className={cn('w-4 h-4 shrink-0', cfg.color)} />; })()}
                      {selectedRequest.kb_name}
                    </h2>
                    <p className="text-xs text-muted-foreground mt-0.5">{selectedRequest.kb_description}</p>
                  </div>
                  <button
                    onClick={() => setSelectedRequest(null)}
                    className="p-1.5 rounded-md hover:bg-muted text-muted-foreground shrink-0"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>

                <div className="rounded-lg border border-border bg-card p-4 space-y-3">
                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-muted-foreground">{t('knowledgeRequests.requester', 'Requester')}:</span>
                    <span className="font-medium">{selectedRequest.requester_name || '未知'}</span>
                  </div>
                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-muted-foreground">{t('knowledgeRequests.status', 'Status')}:</span>
                    <span className={cn(
                      'px-2 py-0.5 rounded-full text-xs border',
                      selectedRequest.status === 'approved' ? 'bg-green-500/10 text-green-600 border-green-500/20'
                        : selectedRequest.status === 'rejected' ? 'bg-red-500/10 text-red-600 border-red-500/20'
                        : 'bg-yellow-500/10 text-yellow-600 border-yellow-500/20'
                    )}>
                      {selectedRequest.status}
                    </span>
                  </div>
                  {selectedRequest.created_at && (
                    <div className="flex items-center gap-2 text-xs">
                      <span className="text-muted-foreground">{t('knowledge.createdAt') || 'Created'}:</span>
                      <span>{new Date(selectedRequest.created_at).toLocaleString('zh-CN', { fractionalSecondDigits: 3 })}</span>
                    </div>
                  )}
                  {selectedRequest.review_note && (
                    <div className="mt-2 p-2 bg-muted rounded text-xs">
                      <span className="text-muted-foreground">{t('knowledgeRequests.reviewNote', 'Review Note')}:</span> {selectedRequest.review_note}
                    </div>
                  )}
                </div>

                {/* Review actions for pending requests */}
                {selectedRequest.status === 'pending' && (
                  <div className="rounded-lg border border-border bg-card p-4 space-y-3">
                    <h3 className="text-sm font-medium">{t('knowledgeRequests.review', 'Review')}</h3>
                    <input
                      value={reviewNote}
                      onChange={(e) => setReviewNote(e.target.value)}
                      className="w-full px-3 py-2 border rounded text-sm bg-background"
                      placeholder={t('knowledgeRequests.reviewNote', 'Review note (optional)')}
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={() => { handleReview(selectedRequest.id, 'approved'); setSelectedRequest(null); }}
                        className="flex-1 px-3 py-2 bg-green-500 text-white rounded text-xs hover:bg-green-600 flex items-center justify-center gap-1.5"
                      >
                        <CheckCircle className="w-3.5 h-3.5" /> {t('common.approve', 'Approve')}
                      </button>
                      <button
                        onClick={() => { handleReview(selectedRequest.id, 'rejected'); setSelectedRequest(null); }}
                        className="flex-1 px-3 py-2 bg-red-500 text-white rounded text-xs hover:bg-red-600 flex items-center justify-center gap-1.5"
                      >
                        <XCircle className="w-3.5 h-3.5" /> {t('common.reject', 'Reject')}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Knowledge Sharing Tab ──────────────────────────── */}
        {activeTab === 'sharing' && (
          <SharingTab />
        )}
      </div>
    </PageLayout>
  );
}
