'use client';
import { useState, useEffect, useCallback } from 'react';
import { LogIn, UserPlus, Loader2, Settings, Check, Wifi, Eye, EyeOff, QrCode, Keyboard } from 'lucide-react';
import { setApiBaseUrl, getApiBaseUrl, setUserId, setToken, setUserName } from '@/lib/api-client';
import { consumeLoginNext, ensureAuthFullPage } from '@/lib/auth-errors';
import {
  listAccounts,
  saveLoginAccount,
  removeLoginAccount,
  getSavedPassword,
  getSavedPasswordDetailed,
  getLastAccount,
  getAutoLoginUser,
  setAutoLogin,
  getAutoToken,
  clearAutoLogin,
  type SavedAccount,
} from '@/lib/login-accounts';
import { LoginAccountField } from '@/components/login-account-field';
import { LoginQrPanel } from '@/components/login-qr-panel';
import { useTranslation } from 'react-i18next';
import { SuperEllipse } from '@/components/ui/super-ellipse';

function api() {
  const base = getApiBaseUrl();
  return {
    login: (username: string, password: string, rememberMe = false) => {
      const body = new URLSearchParams({ username, password });
      if (rememberMe) body.set('remember_me', 'true');
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

export function LoginPage() {
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
  // QQ 登录框三件套 + 扫码
  const [accounts, setAccounts] = useState<SavedAccount[]>([]);
  const [rememberPwd, setRememberPwd] = useState(false);
  const [autoLogin, setAutoLoginOn] = useState(false);
  // 已存密码解密失败的显性降级态：绝不静默空值——提示重输、勾选态保留
  const [savedPwFailed, setSavedPwFailed] = useState(false);
  const [qrMode, setQrMode] = useState(false);
  const [autoChecking, setAutoChecking] = useState(true); // 初始同步 true：SSR/水合一致，effect 判定后即放行表单
  const { t } = useTranslation();

  // 认证 UI 整页铁律：登录界面绝不允许渲染在 iframe/右列/workspace 面板里
  useEffect(() => { ensureAuthFullPage(); }, []);

  useEffect(() => {
    const saved = getApiBaseUrl();
    setServerAddr(saved);
    // Test connection
    testConnection(saved);
    fetch(`${saved}/api/user/oauth/providers`).then(r => r.json()).then(d => setOauthProviders(d.providers || [])).catch(() => {});
  }, []);

  // 账号历史 + 自动登录：预填上次账号（记住密码自动填充），勾了自动登录就静默进站
  useEffect(() => {
    setAccounts(listAccounts());
    const autoUser = getAutoLoginUser();
    setAutoLoginOn(!!autoUser);
    const last = autoUser || getLastAccount();

    let cancelled = false;
    (async () => {
      // 自动登录：长效 token 优先，无效回落加密密码静默登录，全失败回落表单（不锁人）
      if (autoUser) {
        try {
          const tok = await getAutoToken(autoUser);
          if (tok) {
            const base = getApiBaseUrl();
            const r = await fetch(`${base}/api/user/me`, { headers: { Authorization: ['Bearer', tok].join(' ') } });
            if (r.ok) {
              const u = await r.json();
              if (!cancelled) {
                setUserId(u.id);
                setToken(tok);
                setUserName(u.username || autoUser);
                window.location.href = consumeLoginNext();
                return;
              }
            }
          }
          const pw = await getSavedPassword(autoUser);
          if (pw) {
            const res = await api().login(autoUser, pw, true);
            if (!cancelled && res.user_id) {
              setUserId(res.user_id);
              setToken(res.access_token);
              setUserName(res.username || autoUser);
              if (res.remember_token) await setAutoLogin(autoUser, res.remember_token);
              window.location.href = consumeLoginNext();
              return;
            }
          }
        } catch {
          /* 自动登录失败 → 回落登录页，不锁人 */
        }
      }
      if (cancelled) return;
      // 回落/预填：上次账号 + 已记密码自动填充（解密失败=显性降级，绝不静默空值）
      if (last) {
        setUsername(last);
        const r = await getSavedPasswordDetailed(last);
        if (!cancelled) {
          if (r.status === 'ok') {
            setPassword(r.password);
            setRememberPwd(true);
            setSavedPwFailed(false);
          } else if (r.status === 'failed') {
            // 密码存了解不开（存量密钥丢失/跨模式）：提示重输，勾选态保留
            setSavedPwFailed(true);
            setRememberPwd(true);
          }
        }
      }
      setAutoChecking(false);
    })();
    return () => { cancelled = true; };
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

  // 登录成功落盘：账号照记，密码只在勾了「记住密码」才加密存；自动登录收长效 token
  const persistLoginPrefs = async (user: string, pw: string | null, rememberToken?: string | null) => {
    await saveLoginAccount(user, rememberPwd ? pw : null);
    if (autoLogin) await setAutoLogin(user, rememberToken ?? null);
    else if (getAutoLoginUser() === user) clearAutoLogin();
    setAccounts(listAccounts());
  };

  const handleSubmit = async () => {
    // 错误提示语义精确：用户名空≠密码空≠「密码存了解不开」——绝不拿「不能为空」误导
    if (!username.trim()) { setError(t('login.usernameRequired', '请输入用户名')); return; }
    if (!password.trim()) {
      setError(savedPwFailed
        ? t('login.savedPasswordDecryptFailed', '已保存的密码无法解密，请重新输入密码')
        : t('login.passwordRequired', '密码未填入，请输入密码'));
      return;
    }
    setLoading(true); setError('');
    try {
      const a = api();
      if (mode === 'register') {
        const reg = await a.register(username, password, email || `${username}@openmate.local`);
        setUserName(username); setMode('verify'); setResendIn(60);
        setError(reg?.pending_verification ? t('login.pendingResent', '该账号已注册但未验证，验证码已重新发送') : '');
        setLoading(false);
        return;
      } else {
        const res = await a.login(username, password, autoLogin);
        if (res.user_id) { setUserId(res.user_id); setToken(res.access_token); setUserName(res.username || username); }
        else throw new Error(t('login.loginFailed'));
        await persistLoginPrefs(username, password, res.remember_token);
      }
      // 强制页面刷新，确保所有 WebSocket 连接用新 token 重建
      // 登录成功回跳原路径（401 过期接管时已存；无则落 /chat，与既有语义一致）
      window.location.href = consumeLoginNext();
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
      const res = await a.login(username, password, autoLogin);
      if (res.user_id) { setUserId(res.user_id); setToken(res.access_token); setUserName(res.username || username); }
      await persistLoginPrefs(username, password, res.remember_token);
      window.location.href = consumeLoginNext();
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

  // 下拉点选账号：账号+已记密码回填（QQ 式点击即填；解密失败同样显性降级）
  const handleSelectAccount = useCallback(async (acc: SavedAccount) => {
    setUsername(acc.username);
    const r = await getSavedPasswordDetailed(acc.username);
    if (r.status === 'ok') {
      setPassword(r.password);
      setRememberPwd(true);
      setSavedPwFailed(false);
    } else {
      setPassword('');
      // 没记密码=不勾；记了解不开=勾选态保留+提示重输
      setRememberPwd(r.status === 'failed');
      setSavedPwFailed(r.status === 'failed');
    }
    setError('');
  }, []);

  const handleDeleteAccount = useCallback((name: string) => {
    removeLoginAccount(name);
    setAccounts(listAccounts());
    if (!getAutoLoginUser()) setAutoLoginOn(false);
  }, []);

  return (
    // min-h-[var(--app-height,...)]：键盘弹出时登录表单整卡在可视区内重新居中（iOS 不认 resizes-content meta 的兜底）
    <div className="flex items-center justify-center min-h-[var(--app-height,100dvh)] bg-background px-4 py-8">
      <div className="relative p-8 rounded-xl border bg-card w-full max-w-96 shadow-lg">
        {/* QQ 登录框右上角：扫码/账号密码 切换 */}
        <button
          type="button"
          onClick={() => { setQrMode(!qrMode); setError(''); }}
          title={qrMode ? t('login.switchToAccount', '切换到账号密码登录') : t('login.switchToQr', '切换到扫码登录')}
          aria-label={qrMode ? t('login.switchToAccount', '切换到账号密码登录') : t('login.switchToQr', '切换到扫码登录')}
          className="absolute right-3 top-3 w-11 h-11 flex items-center justify-center rounded-lg text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors"
        >
          {qrMode ? <Keyboard className="w-5 h-5" /> : <QrCode className="w-5 h-5" />}
        </button>

        <div className="text-center mb-6">
          <div className="w-16 h-16 shrink-0 overflow-hidden rounded-full bg-primary/10 flex items-center justify-center mx-auto mb-4">
            {mode === 'register' ? <UserPlus size={32} className="text-primary shrink-0" /> : <LogIn size={32} className="text-primary shrink-0" />}
          </div>
          <h1 className="text-2xl font-bold">OpenMate</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {qrMode ? t('login.qrLoginTitle', '扫码登录') : mode === 'register' ? t('login.createAccount') : t('login.loginToContinue')}
          </p>
        </div>

        {qrMode ? (
          <LoginQrPanel />
        ) : mode === 'verify' ? (
          <div>
            <div className="text-sm text-muted-foreground mb-1">{t('login.verifyTip', '验证码已发送至你的邮箱（10 分钟内有效）')}</div>
            <div className="text-xs text-muted-foreground mb-3">
              {resendIn > 0 ? (
                <span>{resendIn} 秒后可重新发送</span>
              ) : (
                <button onClick={handleResend} className="inline-flex items-center min-h-[44px] px-3 rounded-md text-primary hover:bg-primary/10 transition-colors">
                  {t('login.resendCode', '重新发送验证码')}
                </button>
              )}
            </div>
            <input value={verifyCode} onChange={e => setVerifyCode(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleVerify()} placeholder={t('login.verifyCode', '6 位验证码')} className="w-full mb-3 px-4 py-3 rounded-lg border bg-background text-sm tracking-widest" autoFocus />
            <button onClick={handleVerify} disabled={loading} className="w-full min-h-[44px] px-4 py-3 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50 mb-3">
              {loading ? <Loader2 className="w-4 h-4 animate-spin inline" /> : t('login.verifyAndLogin', '验证并登录')}
            </button>
            <button onClick={() => { setMode('login'); setError(''); }} className="w-full min-h-[44px] px-4 py-3 rounded-lg border text-sm text-muted-foreground hover:bg-muted">
              {t('login.backToLogin', '返回登录')}
            </button>
          </div>
        ) : autoChecking ? (
          <div className="flex flex-col items-center gap-3 py-8">
            <Loader2 className="w-6 h-6 animate-spin text-primary" />
            <p className="text-sm text-muted-foreground">{t('login.autoLoggingIn', '自动登录中...')}</p>
          </div>
        ) : (
          <>
        <LoginAccountField
          value={username}
          onChange={setUsername}
          accounts={accounts}
          onSelect={handleSelectAccount}
          onDelete={handleDeleteAccount}
          onSubmit={handleSubmit}
          placeholder={t('login.username')}
          disabled={loading}
          autoFocus
          passwordSavedMark={t('login.passwordSaved', '密码已记住')}
          deleteAccountLabel={t('login.deleteAccount', '删除该账号')}
        />
        <div className="relative w-full mb-3">
          <input type={showPassword ? "text" : "password"} value={password} onChange={e => { setPassword(e.target.value); if (savedPwFailed) setSavedPwFailed(false); if (error) setError(''); }} onKeyDown={e => e.key === 'Enter' && handleSubmit()} placeholder={t('login.password')} className="w-full px-4 py-3 pr-12 rounded-lg border bg-background text-sm" autoComplete="current-password" />
          <button type="button" onClick={() => setShowPassword(!showPassword)} aria-label={t('login.password')} className="absolute right-1 top-1/2 -translate-y-1/2 w-11 h-11 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground transition-colors">
            {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
        {/* 已存密码解不开的显性降级提示（绝不静默空值）：重输即可，勾选态保留 */}
        {savedPwFailed && !password && (
          <p className="text-xs text-amber-500 dark:text-amber-400 mb-3 text-center">
            ⚠ {t('login.savedPasswordDecryptFailed', '已保存的密码无法解密，请重新输入密码')}
          </p>
        )}
        {mode === 'register' && <input value={email} onChange={e => setEmail(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSubmit()} placeholder={t('login.emailOptional')} className="w-full mb-3 px-4 py-3 rounded-lg border bg-background text-sm" />}

        {/* 记住密码 / 自动登录（QQ 式勾选组）：勾自动登录必勾记住密码，取消记住密码连带取消自动登录 */}
        <div className="flex items-center justify-between mb-3 text-sm">
          <label className="flex items-center gap-2 min-h-[44px] cursor-pointer select-none text-muted-foreground hover:text-foreground transition-colors">
            <input
              type="checkbox"
              checked={rememberPwd}
              onChange={(e) => {
                setRememberPwd(e.target.checked);
                if (!e.target.checked) setAutoLoginOn(false);
              }}
              className="w-4 h-4 rounded accent-[var(--color-primary)]"
            />
            {t('login.rememberPassword', '记住密码')}
          </label>
          <label className="flex items-center gap-2 min-h-[44px] cursor-pointer select-none text-muted-foreground hover:text-foreground transition-colors">
            <input
              type="checkbox"
              checked={autoLogin}
              onChange={(e) => {
                setAutoLoginOn(e.target.checked);
                if (e.target.checked) setRememberPwd(true);
              }}
              className="w-4 h-4 rounded accent-[var(--color-primary)]"
            />
            {t('login.autoLogin', '自动登录')}
          </label>
        </div>

        {error && <p className="text-xs text-destructive mb-3 text-center">{error}</p>}

        <button onClick={handleSubmit} disabled={loading} className="w-full min-h-[44px] px-4 py-3 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 flex items-center justify-center gap-2 mb-3">
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : mode === 'register' ? <UserPlus className="w-4 h-4" /> : <LogIn className="w-4 h-4" />}
          {loading ? t('login.pleaseWait') : mode === 'register' ? t('login.register') : t('login.login')}
        </button>

        <button onClick={() => { setMode(mode === 'register' ? 'login' : 'register'); setError(''); }} className="w-full min-h-[44px] px-4 py-3 rounded-lg border text-sm text-muted-foreground hover:bg-muted">
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
                <button onClick={() => handleOAuth('wechat')} className="flex-1 flex items-center justify-center gap-2 px-4 min-h-[44px] py-3 rounded-lg border text-sm hover:bg-muted">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="#07C160"><path d="M8.69 13.03c-.5 0-.9-.4-.9-.9s.4-.9.9-.9.9.4.9.9-.4.9-.9.9zm5.6 0c-.5 0-.9-.4-.9-.9s.4-.9.9-.9.9.4.9.9-.4.9-.9.9zM8.62 3C4.42 3 1 5.9 1 9.48c0 2.02 1.07 3.82 2.75 5.02l-.7 2.1 2.45-1.23c.85.24 1.75.37 2.7.37h.4a5.4 5.4 0 0 1-.15-1.37c0-3.22 3.1-5.84 6.92-5.84h.42C15.85 5.27 12.6 3 8.62 3zm-2.5 3.4a.95.95 0 1 1 0 1.9.95.95 0 0 1 0-1.9zm5.03 0a.95.95 0 1 1 0 1.9.95.95 0 0 1 0-1.9zM16.4 9.5c-3.28 0-5.94 2.24-5.94 5s2.66 5 5.94 5c.72 0 1.42-.1 2.07-.3l2.11.05-.6-1.8C21.27 17.32 22 15.9 22 14.5c0-2.76-2.62-5-5.6-5zm-2 3.1a.8.8 0 1 1 0 1.6.8.8 0 0 1 0-1.6zm4.1 0a.8.8 0 1 1 0 1.6.8.8 0 0 1 0-1.6z"/></svg>
                  {t('login.wechatLogin', '微信登录')}
                </button>
              )}
              {oauthProviders.includes('qq') && (
                <button onClick={() => handleOAuth('qq')} className="flex-1 flex items-center justify-center gap-2 px-4 min-h-[44px] py-3 rounded-lg border text-sm hover:bg-muted">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="#12B7F5"><path d="M12 2C6.9 2 3.2 5.1 3.2 9.2c0 2.3 1.2 4.4 3.1 5.8-.1.9-.5 2.3-1.4 3.4-.2.2 0 .5.3.4 2-.4 3.5-1.2 4.3-1.8.6.1 1.3.2 2 .2 5.1 0 8.5-3.2 8.5-7.2S17.1 2 12 2zm-3.1 7.6c-.5 0-.9-.5-.9-1.1 0-.6.4-1.1.9-1.1s.9.5.9 1.1c0 .6-.4 1.1-.9 1.1zm6.2 0c-.5 0-.9-.5-.9-1.1 0-.6.4-1.1.9-1.1s.9.5.9 1.1c0 .6-.4 1.1-.9 1.1z"/></svg>
                  {t('login.qqLogin', 'QQ登录')}
                </button>
              )}
            </div>
          </div>
        )}
          </>
        )}

        {/* 服务器地址（非登录主流程，归位到卡片底部） */}
        <div className="mt-4 pt-4 border-t border-border/50">
          <button onClick={() => setShowSettings(!showSettings)} className="flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors w-full min-h-[44px] px-3 rounded-[24px] bg-muted/50 hover:bg-muted border border-border/50">
            <Wifi className={`w-3 h-3 shrink-0 ${connStatus === 'ok' ? 'text-green-500' : connStatus === 'fail' ? 'text-red-500' : ''}`} />
            <span suppressHydrationWarning className="truncate flex-1 text-left">{serverAddr}</span>
            <Settings className="w-3 h-3 shrink-0" />
          </button>
          {showSettings && (
            <div className="mt-2 flex gap-2">
              <input value={serverAddr} onChange={e => setServerAddr(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSaveServer()}
                placeholder="http://127.0.0.1:8090" className="flex-1 min-w-0 px-3 py-2 rounded-lg border bg-background text-xs" />
              <button onClick={handleSaveServer} className="shrink-0 text-primary-foreground hover:opacity-90 transition-opacity">
                <SuperEllipse size={36} fill="var(--color-primary)">
                  <Check size={16} className="text-primary-foreground" />
                </SuperEllipse>
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
