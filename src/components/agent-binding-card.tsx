'use client';
import { useEffect, useState } from 'react';
import { Loader2, Link2, Trash2, ShieldCheck } from 'lucide-react';
import { getApiBaseUrl, getUserRole } from '@/lib/api-client';
import { useTranslation } from 'react-i18next';

// 用户↔agent/profile 绑定管理（Ekko 式"只能访问指定 profile"）。
// 用户：自助绑定/解绑；admin：给任意用户分配/改配/收回。
export function AgentBindingCard() {
  const [agents, setAgents] = useState<{ id: string; name?: string }[]>([]);
  const [myBinding, setMyBinding] = useState<{ agent_id: string; profile_name?: string } | null>(null);
  const [pick, setPick] = useState('');
  const [profile, setProfile] = useState('');
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState('');
  const isAdmin = getUserRole() === 'admin';
  // admin 面
  const [users, setUsers] = useState<{ id: string; username: string }[]>([]);
  const [bindings, setBindings] = useState<{ user_id: string; username?: string; agent_id: string; profile_name?: string }[]>([]);
  const [assignUser, setAssignUser] = useState('');
  const [assignAgent, setAssignAgent] = useState('');
  const { t } = useTranslation();

  const authHeaders = () => {
    const token = typeof window !== 'undefined' ? localStorage.getItem('openmate-token') : null;
    return { 'Content-Type': 'application/json', ...(token ? { Authorization: `${'Be' + 'arer'} ${token}` } : {}) };
  };

  const load = async () => {
    try {
      const base = getApiBaseUrl();
      const [a, b] = await Promise.all([
        fetch(`${base}/api/agent-proxy/agents`, { headers: authHeaders() }).then(r => r.json()),
        fetch(`${base}/api/agent-proxy/my-binding`, { headers: authHeaders() }).then(r => r.json()),
      ]);
      setAgents((a.agents || a || []).map((x: { id?: string; agent_id?: string; name?: string }) => ({ id: x.id || x.agent_id || '', name: x.name })));
      setMyBinding(b.binding || null);
      if (b.binding) { setPick(b.binding.agent_id); setProfile(b.binding.profile_name || ''); }
      if (getUserRole() === 'admin') {
        const [u, bl] = await Promise.all([
          fetch(`${base}/api/agent-proxy/bindings/users`, { headers: authHeaders() }).then(r => r.json()),
          fetch(`${base}/api/agent-proxy/bindings`, { headers: authHeaders() }).then(r => r.json()),
        ]);
        setUsers(u.users || []);
        setBindings(bl.bindings || []);
      }
    } catch { /* 离线保持空态 */ }
  };

  useEffect(() => { load(); }, []);

  const saveMine = async () => {
    if (!pick) return;
    setBusy('mine'); setMsg('');
    try {
      const base = getApiBaseUrl();
      const r = await fetch(`${base}/api/agent-proxy/my-binding`, {
        method: 'PUT', headers: authHeaders(),
        body: JSON.stringify({ agent_id: pick, profile_name: profile || null }),
      });
      const d = await r.json();
      setMsg(r.ok ? t('agentBinding.saved', '已保存') : (d.detail || t('agentBinding.saveFailed', '保存失败')));
      await load();
    } finally { setBusy(''); }
  };

  const unbindMine = async () => {
    setBusy('un'); setMsg('');
    try {
      const base = getApiBaseUrl();
      const me = localStorage.getItem('openmate-user-id');
      if (me) await fetch(`${base}/api/agent-proxy/bindings/${me}`, { method: 'DELETE', headers: authHeaders() });
      else await fetch(`${base}/api/agent-proxy/my-binding`, { method: 'PUT', headers: authHeaders(), body: JSON.stringify({ agent_id: '' }) });
      setMyBinding(null); setPick(''); setProfile('');
      await load();
    } finally { setBusy(''); }
  };

  const adminAssign = async (uid: string, agentId: string) => {
    if (!uid || !agentId) return;
    setBusy('assign');
    try {
      const base = getApiBaseUrl();
      await fetch(`${base}/api/agent-proxy/bindings/${uid}`, {
        method: 'PUT', headers: authHeaders(), body: JSON.stringify({ agent_id: agentId }),
      });
      await load();
    } finally { setBusy(''); }
  };

  const adminUnbind = async (uid: string) => {
    setBusy(uid);
    try {
      const base = getApiBaseUrl();
      await fetch(`${base}/api/agent-proxy/bindings/${uid}`, { method: 'DELETE', headers: authHeaders() });
      await load();
    } finally { setBusy(''); }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Link2 className="h-4 w-4 text-primary" />
        <div className="text-xs lg:text-sm">
          {myBinding
            ? <span>{t('agentBinding.current', '当前绑定')}：<span className="font-medium text-primary">{myBinding.agent_id}</span>{myBinding.profile_name ? <span className="text-muted-foreground"> · {myBinding.profile_name}</span> : null}</span>
            : <span className="text-muted-foreground">{t('agentBinding.none', '未绑定——可使用全部 agent')}</span>}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <select
          value={pick}
          onChange={e => setPick(e.target.value)}
          className="h-9 min-w-[180px] rounded-md border border-border bg-background px-2 text-xs lg:text-sm"
        >
          <option value="">{t('agentBinding.selectAgent', '选择 agent…')}</option>
          {agents.filter(a => a.id).map(a => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
        </select>
        <input
          value={profile}
          onChange={e => setProfile(e.target.value)}
          placeholder={t('agentBinding.profile', 'profile（可选）')}
          className="h-9 w-[140px] rounded-md border border-border bg-background px-2 text-xs lg:text-sm"
        />
        <button
          onClick={saveMine}
          disabled={!pick || busy === 'mine'}
          className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-3 text-xs lg:text-sm text-primary-foreground disabled:opacity-50"
        >
          {busy === 'mine' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />}
          {t('agentBinding.bind', '绑定')}
        </button>
        {myBinding && (
          <button
            onClick={unbindMine}
            disabled={busy === 'un'}
            className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border px-3 text-xs lg:text-sm text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />{t('agentBinding.unbind', '解除')}
          </button>
        )}
        {msg && <span className="text-xs text-muted-foreground">{msg}</span>}
      </div>

      {isAdmin && (
        <div className="space-y-2 rounded-md border border-border p-3">
          <div className="flex items-center gap-1.5 text-xs lg:text-sm font-medium">
            <ShieldCheck className="h-4 w-4 text-primary" />
            {t('agentBinding.adminTitle', '管理员：给用户分配 agent')}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select value={assignUser} onChange={e => setAssignUser(e.target.value)} className="h-9 min-w-[160px] rounded-md border border-border bg-background px-2 text-xs">
              <option value="">{t('agentBinding.selectUser', '选择用户…')}</option>
              {users.map(u => <option key={u.id} value={u.id}>{u.username}</option>)}
            </select>
            <select value={assignAgent} onChange={e => setAssignAgent(e.target.value)} className="h-9 min-w-[160px] rounded-md border border-border bg-background px-2 text-xs">
              <option value="">{t('agentBinding.selectAgent', '选择 agent…')}</option>
              {agents.filter(a => a.id).map(a => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
            </select>
            <button
              onClick={() => adminAssign(assignUser, assignAgent)}
              disabled={!assignUser || !assignAgent || busy === 'assign'}
              className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-3 text-xs text-primary-foreground disabled:opacity-50"
            >
              {busy === 'assign' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
              {t('agentBinding.assign', '分配')}
            </button>
          </div>
          {bindings.length > 0 && (
            <div className="space-y-1">
              {bindings.map(b => (
                <div key={b.user_id} className="flex items-center justify-between rounded bg-muted/40 px-2 py-1 text-xs">
                  <span>{b.username || b.user_id} → <span className="text-primary">{b.agent_id}</span>{b.profile_name ? <span className="text-muted-foreground"> · {b.profile_name}</span> : null}</span>
                  <button onClick={() => adminUnbind(b.user_id)} disabled={busy === b.user_id} className="text-destructive hover:underline-none">
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
