'use client';
import { useEffect, useState } from 'react';
import { Loader2, Check, Plus, Trash2 } from 'lucide-react';
import { getApiBaseUrl } from '@/lib/api-client';
import { useTranslation } from 'react-i18next';

// 账号绑定管理：微信/QQ 登录绑定。无密码账号不许解绑最后一个身份（后端二次校验）。
export function AccountBindings() {
  const [identities, setIdentities] = useState<{ provider: string; nickname?: string }[]>([]);
  const [providers, setProviders] = useState<string[]>([]);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState('');
  const { t } = useTranslation();

  const authHeaders = () => {
    const token = typeof window !== 'undefined' ? localStorage.getItem('openmate-token') : null;
    return { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  };

  const load = async () => {
    try {
      const base = getApiBaseUrl();
      const [i, p] = await Promise.all([
        fetch(`${base}/api/user/identities`, { headers: authHeaders() }).then(r => r.json()),
        fetch(`${base}/api/user/oauth/providers`).then(r => r.json()),
      ]);
      setIdentities(i.identities || []);
      setProviders(p.providers || []);
    } catch { /* 离线时保持空态 */ }
  };

  useEffect(() => { load(); }, []);

  const bind = async (provider: string) => {
    setBusy(provider); setMsg('');
    try {
      const base = getApiBaseUrl();
      const r = await fetch(`${base}/api/user/identities/${provider}/bind-url`, { headers: authHeaders() });
      const d = await r.json();
      if (d.url) window.location.href = d.url;
      else setMsg(d.detail || t('bindings.notConfigured', '该登录方式未配置'));
    } catch {
      setMsg(t('bindings.bindFailed', '发起绑定失败'));
    }
    setBusy('');
  };

  const unbind = async (provider: string) => {
    setBusy(provider); setMsg('');
    try {
      const base = getApiBaseUrl();
      const r = await fetch(`${base}/api/user/identities/${provider}`, { method: 'DELETE', headers: authHeaders() });
      const d = await r.json();
      if (!r.ok) setMsg(d.detail || t('bindings.unbindFailed', '解绑失败'));
      else await load();
    } catch {
      setMsg(t('bindings.unbindFailed', '解绑失败'));
    }
    setBusy('');
  };

  const bound = (p: string) => identities.find(i => i.provider === p);

  return (
    <div>
      <div className="flex flex-col gap-2">
        {['wechat', 'qq'].map(p => {
          const b = bound(p);
          return (
            <div key={p} className="flex items-center gap-3 p-3 rounded-lg bg-muted/50">
              <div className="flex h-9 w-9 items-center justify-center rounded-full text-white text-xs font-medium"
                style={{ background: p === 'wechat' ? '#07C160' : '#12B7F5' }}>
                {p === 'wechat' ? t('bindings.wechatShort', '微') : 'QQ'}
              </div>
              <div className="flex-1">
                <div className="text-xs lg:text-sm font-medium">{p === 'wechat' ? t('bindings.wechat', '微信') : t('bindings.qq', 'QQ')}</div>
                <div className="text-xs text-muted-foreground">
                  {b ? t('bindings.boundTo', '已绑定：{{n}}', { n: b.nickname || b.provider }) : t('bindings.notBound', '未绑定')}
                </div>
              </div>
              {b ? (
                <button onClick={() => unbind(p)} disabled={busy === p}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-md border text-xs text-muted-foreground hover:bg-background hover:text-red-500 disabled:opacity-50">
                  {busy === p ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                  {t('bindings.unbind', '解绑')}
                </button>
              ) : (
                <button onClick={() => bind(p)} disabled={busy === p || !providers.includes(p)}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-md border text-xs text-primary hover:bg-primary/10 disabled:opacity-50">
                  {busy === p ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                  {t('bindings.bind', '绑定')}
                </button>
              )}
            </div>
          );
        })}
      </div>
      {msg && <div className="mt-2 text-xs text-red-500">{msg}</div>}
    </div>
  );
}
