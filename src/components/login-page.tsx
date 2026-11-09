'use client';
import { useState, useEffect } from 'react';
import { LogIn, UserPlus, Loader2, Settings, ChevronDown, Check, Wifi, Eye, EyeOff } from 'lucide-react';
import { setApiBaseUrl, getApiBaseUrl, setUserId, setToken, setUserName } from '@/lib/api-client';
import { useTranslation } from 'react-i18next';
import { SuperEllipse } from '@/components/ui/super-ellipse';

function api() {
  const base = getApiBaseUrl();
  return {
    login: (username: string, password: string) => {
      const body = new URLSearchParams({ username, password });
      return fetch(`${base}/api/user/login`, { method: 'POST', body: body.toString(), headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }).then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); });
    },
    register: (username: string, password: string, email: string) =>
      fetch(`${base}/api/user/register`, { method: 'POST', body: JSON.stringify({ username, password, email }), headers: { 'Content-Type': 'application/json' } }).then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); }),
    verifyEmail: (username: string, code: string) =>
      fetch(`${base}/api/user/verify-email`, { method: 'POST', body: JSON.stringify({ username, code }), headers: { 'Content-Type': 'application/json' } }).then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); }),
    resendCode: (username: string) =>
      fetch(`${base}/api/user/resend-code`, { method: 'POST', body: JSON.stringify({ username }), headers: { 'Content-Type': 'application/json' } }).then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); }),
  };
}

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [mode, setMode] = useState<'register' | 'login' | 'verify'>('login');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [serverAddr, setServerAddr] = useState('');
  const [showSettings, setShowSettings] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [verifyCode, setVerifyCode] = useState('');
  const [resendIn, setResendIn] = useState(0);
  const [oauthProviders, setOauthProviders] = useState<string[]>([]);
  const [connStatus, setConnStatus] = useState<'idle' | 'ok' | 'fail'>('idle');
  const { t } = useTranslation();

  useEffect(() => {
    const saved = getApiBaseUrl();
    setServerAddr(saved);
    // Test connection
    testConnection(saved);
    fetch(`${saved}/api/user/oauth/providers`).then(r => r.json()).then(d => setOauthProviders(d.providers || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (resendIn <= 0) return;
    const tick = setTimeout(() => setResendIn(v => v - 1), 1000);
    return () => clearTimeout(tick);
  }, [resendIn]);

  const testConnection = async (url: string) => {
    try {
      const res = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(3000) });
      setConnStatus(res.ok ? 'ok' : 'fail');
    } catch {
      setConnStatus('fail');
    }
  };

  const handleSaveServer = () => {
    const url = serverAddr.trim().replace(/\/+$/, '');
    if (!url) return;
    setApiBaseUrl(url);
    testConnection(url);
    setShowSettings(false);
  };

  const handleSubmit = async () => {
    if (!username.trim() || !password.trim()) { setError(t('login.usernamePasswordRequired')); return; }
    setLoading(true); setError('');
    try {
      const a = api();
      if (mode === 'register') {
        await a.register(username, password, email || `${username}@openmate.local`);
        setUserName(username); setMode('verify'); setResendIn(60); setError('');
        setLoading(false);
        return;
      } else {
        const res = await a.login(username, password);
        if (res.user_id) { setUserId(res.user_id); setToken(res.access_token); setUserName(res.username || username); }
        else throw new Error(t('login.loginFailed'));
      }
      // 强制页面刷新，确保所有 WebSocket 连接用新 token 重建
      window.location.href = '/chat';
    } catch (e) {
      const msg = (e as Error).message;
      if (mode === 'login' && msg.includes('401')) setError(t('login.invalidCredentials'));
      else if (mode === 'login' && msg.includes('403')) { setMode('verify'); setResendIn(60); setError(t('login.verifyFirst', '邮箱未验证，请输入邮箱收到的验证码')); }
      else if (mode === 'register' && msg.includes('400')) setError(t('login.usernameExists'));
      else if (msg.includes('Failed to fetch') || msg.includes('NetworkError')) setError(t('login.connectionError'));
      else setError(msg);
    }
    setLoading(false);
  };

  const handleVerify = async () => {
    if (!verifyCode.trim()) { setError(t('login.codeRequired', '请输入验证码')); return; }
    setLoading(true); setError('');
    try {
      const a = api();
      await a.verifyEmail(username, verifyCode.trim());
      const res = await a.login(username, password);
      if (res.user_id) { setUserId(res.user_id); setToken(res.access_token); setUserName(res.username || username); }
      window.location.href = '/chat';
    } catch {
      setError(t('login.codeInvalid', '验证码错误或已过期'));
    }
    setLoading(false);
  };

  const handleResend = async () => {
    if (resendIn > 0) return;
    try { await api().resendCode(username); setResendIn(60); setError(''); }
    catch { setError(t('login.resendFailed', '重发失败，请稍后再试')); }
  };

  const handleOAuth = async (p: string) => {
    try {
      const base = getApiBaseUrl();
      const r = await fetch(`${base}/api/user/oauth/${p}/start`);
      const d = await r.json();
      if (d.url) window.location.href = d.url;
      else setError(d.detail || t('login.oauthNotConfigured', '该登录方式未配置'));
    } catch {
      setError(t('login.connectionError'));
    }
  };

  return (
    <div className="flex items-center justify-center h-screen bg-background">
      <div className="p-8 rounded-xl border bg-card w-96 shadow-lg">
        <div className="text-center mb-6">
          <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center mx-auto mb-4">
            {mode === 'register' ? <UserPlus className="w-8 h-8 text-primary" /> : <LogIn className="w-8 h-8 text-primary" />}
          </div>
          <h1 className="text-2xl font-bold">OpenMate</h1>
          <p className="text-sm text-muted-foreground mt-1">{mode === 'register' ? t('login.createAccount') : t('login.loginToContinue')}</p>
        </div>

        {/* Server Address */}
        <div className="mb-4">
          <button onClick={() => setShowSettings(!showSettings)} className="flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors w-full px-3 py-2 rounded-[24px] bg-muted/50 hover:bg-muted border border-border/50">
            <Wifi className={`w-3 h-3 ${connStatus === 'ok' ? 'text-green-500' : connStatus === 'fail' ? 'text-red-500' : ''}`} />
            <span suppressHydrationWarning className="truncate flex-1 text-left">{serverAddr}</span>
            <Settings className="w-3 h-3" />
          </button>
          {showSettings && (
            <div className="mt-2 flex gap-2">
              <input value={serverAddr} onChange={e => setServerAddr(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSaveServer()}
                placeholder="http://127.0.0.1:8090" className="flex-1 px-3 py-2 rounded-lg border bg-background text-xs" />
              <button onClick={handleSaveServer} className="text-primary-foreground hover:opacity-90 transition-opacity">
                <SuperEllipse size={36} fill="var(--color-primary)">
                  <Check size={16} className="text-primary-foreground" />
                </SuperEllipse>
              </button>
            </div>
          )}
        </div>

        {mode === 'verify' ? (
          <div>
            <div className="text-sm text-muted-foreground mb-3">{t('login.verifyTip', '验证码已发送至你的邮箱（10 分钟内有效）')}</div>
            <input value={verifyCode} onChange={e => setVerifyCode(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleVerify()} placeholder={t('login.verifyCode', '6 位验证码')} className="w-full mb-3 px-4 py-3 rounded-lg border bg-background text-sm tracking-widest" autoFocus />
            <button onClick={handleVerify} disabled={loading} className="w-full px-4 py-3 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50 mb-3">
              {loading ? <Loader2 className="w-4 h-4 animate-spin inline" /> : t('login.verifyAndLogin', '验证并登录')}
            </button>
            <div className="flex gap-2">
              <button onClick={handleResend} disabled={resendIn > 0} className="flex-1 px-4 py-2 rounded-lg border text-sm text-muted-foreground hover:bg-muted disabled:opacity-50">
                {resendIn > 0 ? t('login.resendIn', '重新发送（{{n}}s）', { n: resendIn }) : t('login.resendCode', '重新发送验证码')}
              </button>
              <button onClick={() => { setMode('login'); setError(''); }} className="flex-1 px-4 py-2 rounded-lg border text-sm text-muted-foreground hover:bg-muted">
                {t('login.backToLogin', '返回登录')}
              </button>
            </div>
          </div>
        ) : (
          <>
        <input value={username} onChange={e => setUsername(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSubmit()} placeholder={t('login.username')} className="w-full mb-3 px-4 py-3 rounded-lg border bg-background text-sm" autoFocus />
        <div className="relative w-full mb-3">
          <input type={showPassword ? "text" : "password"} value={password} onChange={e => setPassword(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSubmit()} placeholder={t('login.password')} className="w-full px-4 py-3 pr-10 rounded-lg border bg-background text-sm" />
          <button type="button" onClick={() => setShowPassword(!showPassword)} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors">
            {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
        {mode === 'register' && <input value={email} onChange={e => setEmail(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSubmit()} placeholder={t('login.emailOptional')} className="w-full mb-3 px-4 py-3 rounded-lg border bg-background text-sm" />}

        {error && <p className="text-xs text-destructive mb-3 text-center">{error}</p>}

        <button onClick={handleSubmit} disabled={loading} className="w-full px-4 py-3 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 flex items-center justify-center gap-2 mb-3">
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : mode === 'register' ? <UserPlus className="w-4 h-4" /> : <LogIn className="w-4 h-4" />}
          {loading ? t('login.pleaseWait') : mode === 'register' ? t('login.register') : t('login.login')}
        </button>

        <button onClick={() => { setMode(mode === 'register' ? 'login' : 'register'); setError(''); }} className="w-full px-4 py-2 rounded-lg border text-sm text-muted-foreground hover:bg-muted">
          {mode === 'register' ? t('login.hasAccountLogin') : t('login.noAccountRegister')}
        </button>

        {oauthProviders.length > 0 && (
          <div className="mt-4">
            <div className="flex items-center gap-2 text-xs text-muted-foreground mb-3">
              <div className="flex-1 h-px bg-border" />
              <span>{t('login.orLoginWith', '或使用以下方式登录')}</span>
              <div className="flex-1 h-px bg-border" />
            </div>
            <div className="flex gap-2">
              {oauthProviders.includes('wechat') && (
                <button onClick={() => handleOAuth('wechat')} className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg border text-sm hover:bg-muted">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="#07C160"><path d="M8.69 13.03c-.5 0-.9-.4-.9-.9s.4-.9.9-.9.9.4.9.9-.4.9-.9.9zm5.6 0c-.5 0-.9-.4-.9-.9s.4-.9.9-.9.9.4.9.9-.4.9-.9.9zM8.62 3C4.42 3 1 5.9 1 9.48c0 2.02 1.07 3.82 2.75 5.02l-.7 2.1 2.45-1.23c.85.24 1.75.37 2.7.37h.4a5.4 5.4 0 0 1-.15-1.37c0-3.22 3.1-5.84 6.92-5.84h.42C15.85 5.27 12.6 3 8.62 3zm-2.5 3.4a.95.95 0 1 1 0 1.9.95.95 0 0 1 0-1.9zm5.03 0a.95.95 0 1 1 0 1.9.95.95 0 0 1 0-1.9zM16.4 9.5c-3.28 0-5.94 2.24-5.94 5s2.66 5 5.94 5c.72 0 1.42-.1 2.07-.3l2.11.05-.6-1.8C21.27 17.32 22 15.9 22 14.5c0-2.76-2.62-5-5.6-5zm-2 3.1a.8.8 0 1 1 0 1.6.8.8 0 0 1 0-1.6zm4.1 0a.8.8 0 1 1 0 1.6.8.8 0 0 1 0-1.6z"/></svg>
                  {t('login.wechatLogin', '微信登录')}
                </button>
              )}
              {oauthProviders.includes('qq') && (
                <button onClick={() => handleOAuth('qq')} className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg border text-sm hover:bg-muted">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="#12B7F5"><path d="M12 2C6.9 2 3.2 5.1 3.2 9.2c0 2.3 1.2 4.4 3.1 5.8-.1.9-.5 2.3-1.4 3.4-.2.2 0 .5.3.4 2-.4 3.5-1.2 4.3-1.8.6.1 1.3.2 2 .2 5.1 0 8.5-3.2 8.5-7.2S17.1 2 12 2zm-3.1 7.6c-.5 0-.9-.5-.9-1.1 0-.6.4-1.1.9-1.1s.9.5.9 1.1c0 .6-.4 1.1-.9 1.1zm6.2 0c-.5 0-.9-.5-.9-1.1 0-.6.4-1.1.9-1.1s.9.5.9 1.1c0 .6-.4 1.1-.9 1.1z"/></svg>
                  {t('login.qqLogin', 'QQ登录')}
                </button>
              )}
            </div>
          </div>
        )}
          </>
        )}
      </div>
    </div>
  );
}
