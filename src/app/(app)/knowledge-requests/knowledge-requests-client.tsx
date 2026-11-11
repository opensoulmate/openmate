'use client';
import { useState, useEffect } from 'react';
import { FileText, Loader2, CheckCircle, XCircle, Clock, Send, Eye, Trash2 } from 'lucide-react';
import { api } from '@/lib/api-client';
import { useTranslation } from 'react-i18next';
import { PageLayout } from '@/components/page-layout';

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

export function KnowledgeRequestsClient() {
  const { t } = useTranslation();
  const [requests, setRequests] = useState<KbRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<string>('');
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [reviewNote, setReviewNote] = useState('');
  const [showMyOnly, setShowMyOnly] = useState(false);

  useEffect(() => { loadRequests(); }, [filter, showMyOnly]);

  const loadRequests = async () => {
    setLoading(true);
    setError('');
    try {
      let data;
      if (showMyOnly) {
        data = await api.getMyKbRequests();
      } else {
        data = await api.listKbRequests(filter || undefined);
      }
      setRequests(Array.isArray(data) ? data : data.items || data.results || []);
    } catch (e) { setError(`${t('common.error')}: ${(e as Error).message}`); }
    setLoading(false);
  };

  const handleCreate = async () => {
    if (!newName.trim() || !newDesc.trim()) return;
    setSubmitting(true);
    try {
      await api.createKbRequest({ kb_name: newName, kb_description: newDesc });
      setShowCreate(false);
      setNewName('');
      setNewDesc('');
      loadRequests();
    } catch (e) { setError(`${t('common.error')}: ${(e as Error).message}`); }
    setSubmitting(false);
  };

  const handleReview = async (id: string, status: string) => {
    try {
      await api.reviewKbRequest(id, { status, review_note: reviewNote });
      setReviewId(null);
      setReviewNote('');
      loadRequests();
    } catch (e) { setError(`${t('common.error')}: ${(e as Error).message}`); }
  };

  const statusIcon = (s: string) => {
    if (s === 'approved') return <CheckCircle className="w-4 h-4 text-green-500" />;
    if (s === 'rejected') return <XCircle className="w-4 h-4 text-red-500" />;
    return <Clock className="w-4 h-4 text-yellow-500" />;
  };

  const statusColor = (s: string) => {
    if (s === 'approved') return 'bg-green-500/10 text-green-600 border-green-500/20';
    if (s === 'rejected') return 'bg-red-500/10 text-red-600 border-red-500/20';
    return 'bg-yellow-500/10 text-yellow-600 border-yellow-500/20';
  };

  return (
      <PageLayout title="Knowledge Requests">
        
    <div className="px-3 lg:px-6 py-4 lg:py-6 space-y-4 lg:space-y-6 max-w-6xl mx-auto h-full overflow-y-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl lg:text-2xl font-bold flex items-center gap-2">
            <FileText className="w-6 h-6" /> {t('nav.knowledgeRequests', 'Knowledge Requests')}
          </h1>
          <p className="text-muted-foreground mt-1">{t('knowledgeRequests.description', 'Request new knowledge bases or review pending requests')}</p>
        </div>
        <button onClick={() => setShowCreate(!showCreate)} className="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 flex items-center gap-2">
          <Send className="w-4 h-4" /> {t('knowledgeRequests.newRequest', 'New Request')}
        </button>
      </div>

      {error && <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-md text-red-600 text-xs lg:text-sm">{error}</div>}

      {showCreate && (
        <div className="p-4 border rounded-lg bg-card space-y-4">
          <h3 className="font-medium">{t('knowledgeRequests.createTitle', 'Request New Knowledge Base')}</h3>
          <div className="space-y-3">
            <div>
              <label className="text-xs lg:text-sm font-medium mb-1 block">{t('knowledgeRequests.name', 'Knowledge Base Name')}</label>
              <input value={newName} onChange={(e) => setNewName(e.target.value)} className="w-full px-3 py-2 border rounded-md bg-background" placeholder={t('knowledgeRequests.namePlaceholder', 'e.g. Project Documentation')} />
            </div>
            <div>
              <label className="text-xs lg:text-sm font-medium mb-1 block">{t('knowledgeRequests.description', 'Description')}</label>
              <textarea value={newDesc} onChange={(e) => setNewDesc(e.target.value)} className="w-full px-3 py-2 border rounded-md bg-background h-24 resize-none" placeholder={t('knowledgeRequests.descPlaceholder', 'Describe the purpose and contents of this knowledge base')} />
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={handleCreate} disabled={submitting} className="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2">
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />} {t('common.submit', 'Submit')}
            </button>
            <button onClick={() => setShowCreate(false)} className="px-4 py-2 border rounded-md hover:bg-accent">{t('common.cancel', 'Cancel')}</button>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 lg:gap-4">
        <div className="flex gap-2">
          {['', 'pending', 'approved', 'rejected'].map((s) => (
            <button key={s} onClick={() => setFilter(s)} className={`px-3 py-1.5 rounded-md text-xs lg:text-sm ${filter === s ? 'bg-primary text-primary-foreground' : 'bg-secondary hover:bg-secondary/80'}`}>
              {s || t('common.all', 'All')}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-xs lg:text-sm cursor-pointer">
          <input type="checkbox" checked={showMyOnly} onChange={(e) => setShowMyOnly(e.target.checked)} className="rounded" />
          {t('knowledgeRequests.myOnly', 'My Requests Only')}
        </label>
        <button onClick={loadRequests} className="ml-auto text-xs lg:text-sm text-muted-foreground hover:text-foreground">{t('common.refresh', 'Refresh')}</button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>
      ) : requests.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground">{t('knowledgeRequests.empty', 'No knowledge requests found')}</div>
      ) : (
        <div className="space-y-3">
          {requests.map((req) => (
            <div key={req.id} className="p-4 border rounded-lg bg-card hover:shadow-sm transition-shadow">
              <div className="flex items-start justify-between">
                <div className="space-y-1 flex-1">
                  <div className="flex items-center gap-2">
                    {statusIcon(req.status)}
                    <span className="font-medium">{req.kb_name}</span>
                    <span className={`px-2 py-0.5 rounded-full text-xs border ${statusColor(req.status)}`}>{req.status}</span>
                  </div>
                  <p className="text-xs lg:text-sm text-muted-foreground">{req.kb_description}</p>
                  {req.requester_name && <p className="text-xs text-muted-foreground">{t('knowledgeRequests.requester', 'Requester')}: {req.requester_name}</p>}
                  <p className="text-xs text-muted-foreground">{req.created_at ? new Date(req.created_at).toLocaleString('zh-CN', { fractionalSecondDigits: 3 }) : ''}</p>
                  {req.review_note && <p className="text-xs lg:text-sm mt-2 p-2 bg-muted rounded">{req.review_note}</p>}
                </div>
                {req.status === 'pending' && (
                  <div className="flex gap-2 ml-4">
                    {reviewId === req.id ? (
                      <div className="space-y-2">
                        <input value={reviewNote} onChange={(e) => setReviewNote(e.target.value)} className="px-2 py-1 border rounded text-xs lg:text-sm bg-background w-48" placeholder={t('knowledgeRequests.reviewNote', 'Review note (optional)')} />
                        <div className="flex gap-1">
                          <button onClick={() => handleReview(req.id, 'approved')} className="px-2 py-1 bg-green-500 text-white rounded text-xs hover:bg-green-600">{t('common.approve', 'Approve')}</button>
                          <button onClick={() => handleReview(req.id, 'rejected')} className="px-2 py-1 bg-red-500 text-white rounded text-xs hover:bg-red-600">{t('common.reject', 'Reject')}</button>
                          <button onClick={() => setReviewId(null)} className="px-2 py-1 border rounded text-xs">{t('common.cancel', 'Cancel')}</button>
                        </div>
                      </div>
                    ) : (
                      <button onClick={() => setReviewId(req.id)} className="px-3 py-1.5 border rounded-md text-xs lg:text-sm hover:bg-accent flex items-center gap-1">
                        <Eye className="w-3 h-3" /> {t('common.review', 'Review')}
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  
      </PageLayout>
    );
}
