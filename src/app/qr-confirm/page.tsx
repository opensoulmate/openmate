'use client';
// 扫码登录确认页（手机端打开）：QQ/微信式「确认登录」页。
// 认证走现有登录态：已登录直接确认；未登录先登录（LoginPage）再回跳本确认页
// （rememberLoginNext 回跳语义与深链一致）。确认必须带 Bearer 登录态，
// 服务端取身份——未确认绝不下发桌面端登录态。

import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { CheckCircle2, Loader2, LogIn, MonitorSmartphone, ShieldCheck, UserX } from 'lucide-react';
import { getApiBaseUrl, getToken, getUserId, getUserName, clearUser } from '@/lib/api-client';
import { ensureAuthFullPage, rememberLoginNext } from '@/lib/auth-errors';
import { LoginPage } from '@/components/login-page';
import { useTranslation } from 'react-i18next';

type Phase = 'confirming' | 'done' | 'failed';

function QrConfirmView() {
  const searchParams = useSearchParams();
  const qrToken = searchParams.get('token') || '';
  const [loggedIn, setLoggedIn] = useState<boolean | null>(null);
  const [phase, setPhase] = useState<Phase>('confirming');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const { t } = useTranslation();

  // 认证 UI 整页铁律：确认页/登录页都必须整页渲染
  useEffect(() => { ensureAuthFullPage(); }, []);

  useEffect(() => {
    const ok = !!getToken() && !!getUserId();
    // 未登录 → 记住回跳（登录后回本确认页），渲染共享 LoginPage
    if (!ok) rememberLoginNext();
    setLoggedIn(ok);
    if (ok && qrToken) {
      // 打开即回传「已扫描」（桌面端提示请在手机上确认）
      fetch(`${getApiBaseUrl()}/api/user/qr/scanned`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ qr_token: qrToken }),
      }).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleConfirm = async () => {
    setBusy(true);
    setError('');
    try {
      const r = await fetch(`${getApiBaseUrl()}/api/user/qr/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: ['Bearer', getToken() ?? ''].join(' ') },
        body: JSON.stringify({ qr_token: qrToken }),
      });
      if (r.ok) {
        setPhase('done');
      } else {
        const d = await r.json().catch(() => ({}));
        const code = d?.detail?.code || '';
        setPhase('failed');
        setError(
          code === 'expired' || code === 'unknown'
            ? t('login.qrConfirmExpired', '二维码已失效，请在电脑上点击刷新二维码')
            : code === 'already'
              ? t('login.qrConfirmUsed', '该二维码已被确认，请在电脑上查看登录结果')
              : t('login.qrConfirmFailed', '确认失败，请重试或在电脑上刷新二维码'),
        );
      }
    } catch {
      setPhase('failed');
      setError(t('login.connectionError'));
    }
    setBusy(false);
  };

  const handleSwitchAccount = () => {
    // 切换账号：清会话 → 保留本确认页回跳 → 重载出登录页
    clearUser();
    rememberLoginNext();
    window.location.reload();
  };

  if (!qrToken) {
    return (
      <ConfirmShell>
        <p className="text-sm text-muted-foreground text-center">{t('login.qrConfirmMissing', '缺少二维码参数，请重新扫码')}</p>
      </ConfirmShell>
    );
  }

  if (loggedIn === null) return null;
  if (!loggedIn) return <LoginPage />;

  return (
    <ConfirmShell>
      {phase === 'done' ? (
        <div className="flex flex-col items-center gap-3 py-4">
          <CheckCircle2 className="w-14 h-14 text-green-500" />
          <p className="text-base font-medium">{t('login.qrConfirmDone', '登录成功，请回到电脑查看')}</p>
          <p className="text-xs text-muted-foreground">{t('login.qrConfirmDoneTip', '本次二维码已作废，可关闭本页')}</p>
        </div>
      ) : phase === 'failed' ? (
        <div className="flex flex-col items-center gap-3 py-4">
          <UserX className="w-14 h-14 text-destructive" />
          <p className="text-sm text-muted-foreground text-center">{error}</p>
        </div>
      ) : (
        <>
          <div className="flex flex-col items-center gap-2 py-2">
            <MonitorSmartphone className="w-12 h-12 text-primary" />
            <p className="text-base font-medium">{t('login.qrConfirmTitle', '确认登录 OpenMate')}</p>
            <p className="text-xs text-muted-foreground text-center">{t('login.qrConfirmTip', '电脑端正在请求登录你的账号，请确认是本人操作')}</p>
          </div>
          <div className="flex items-center gap-3 rounded-lg border bg-muted/40 px-4 py-3 mb-2">
            <span className="w-10 h-10 shrink-0 rounded-full bg-primary/10 text-primary flex items-center justify-center text-base font-medium">
              {(getUserName() || '?').slice(0, 1).toUpperCase()}
            </span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium truncate">{getUserName()}</p>
              <p className="text-xs text-muted-foreground">{t('login.qrConfirmCurrent', '当前登录账号')}</p>
            </div>
            <ShieldCheck className="w-4 h-4 shrink-0 text-green-500" />
          </div>
          <button
            onClick={handleConfirm}
            disabled={busy}
            className="w-full min-h-[44px] px-4 py-3 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 flex items-center justify-center gap-2 mb-2"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogIn className="w-4 h-4" />}
            {busy ? t('login.pleaseWait') : t('login.qrConfirmButton', '确认登录')}
          </button>
          <button
            onClick={handleSwitchAccount}
            className="w-full min-h-[44px] px-4 py-3 rounded-lg border text-sm text-muted-foreground hover:bg-muted"
          >
            {t('login.qrConfirmSwitch', '不是本人？切换账号')}
          </button>
        </>
      )}
    </ConfirmShell>
  );
}

function ConfirmShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-center min-h-dvh bg-background px-4 py-8">
      <div className="p-8 rounded-xl border bg-card w-full max-w-96 shadow-lg">{children}</div>
    </div>
  );
}

export default function QrConfirmPage() {
  return (
    <Suspense fallback={null}>
      <QrConfirmView />
    </Suspense>
  );
}
