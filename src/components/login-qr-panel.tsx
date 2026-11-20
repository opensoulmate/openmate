'use client';
// 扫码登录面板（QQ 登录框式）：一次性二维码 + 轮询确认。
// 流程：进入面板即生成一次性 qr_token（短时效）→ 手机扫二维码打开 /qr-confirm 确认页
// （打开即回传「已扫描」）→ 桌面端轮询 status，confirmed 首次领取一次性凭据进应用。
// 过期态：「二维码已过期 点击刷新」，点击重新生成。轮询 3s 一次（服务端限频 1.5s）。

import { useCallback, useEffect, useRef, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { Loader2, RefreshCw, Smartphone } from 'lucide-react';
import { getApiBaseUrl, setToken, setUserId, setUserName } from '@/lib/api-client';
import { consumeLoginNext } from '@/lib/auth-errors';
import { saveLoginAccount } from '@/lib/login-accounts';
import { useTranslation } from 'react-i18next';

type QrState = 'loading' | 'waiting' | 'scanned' | 'expired' | 'error';

export function LoginQrPanel() {
  const [qrToken, setQrToken] = useState('');
  const [confirmUrl, setConfirmUrl] = useState('');
  const [expiresIn, setExpiresIn] = useState(0);
  const [state, setState] = useState<QrState>('loading');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const doneRef = useRef(false);
  const { t } = useTranslation();

  const generate = useCallback(async () => {
    doneRef.current = false;
    setState('loading');
    try {
      const base = getApiBaseUrl();
      const r = await fetch(`${base}/api/user/qr/generate`, { method: 'POST' });
      if (!r.ok) throw new Error(`${r.status}`);
      const d = await r.json();
      setQrToken(d.qr_token);
      setExpiresIn(d.expires_in);
      // 扫码打开的是前端确认页（手机端带登录态确认）
      setConfirmUrl(`${window.location.origin}/qr-confirm?token=${encodeURIComponent(d.qr_token)}`);
      setState('waiting');
    } catch {
      setState('error');
    }
  }, []);

  useEffect(() => {
    generate();
  }, [generate]);

  // 倒计时到点 → 过期态（QQ 式「二维码已过期 点击刷新」）
  useEffect(() => {
    if (state !== 'waiting' && state !== 'scanned') return;
    const tick = setInterval(() => {
      setExpiresIn((v) => {
        if (v <= 1) {
          clearInterval(tick);
          if (!doneRef.current) setState('expired');
          return 0;
        }
        return v - 1;
      });
    }, 1000);
    return () => clearInterval(tick);
  }, [state]);

  // 轮询确认状态（服务端限频 1.5s，这里 3s；429 直接跳过一轮）
  useEffect(() => {
    if (!qrToken || (state !== 'waiting' && state !== 'scanned')) return;
    const poll = async () => {
      if (doneRef.current) return;
      try {
        const base = getApiBaseUrl();
        const r = await fetch(`${base}/api/user/qr/status?token=${encodeURIComponent(qrToken)}`);
        if (r.status === 429) return;
        const d = await r.json();
        if (d.status === 'scanned') setState('scanned');
        else if (d.status === 'expired') {
          if (!doneRef.current) setState('expired');
        } else if (d.status === 'confirmed' && d.access_token) {
          // 一次性凭据领取即进应用（会话作废在服务端已发生）
          doneRef.current = true;
          setUserId(d.user_id);
          setToken(d.access_token);
          setUserName(d.username);
          // 扫码登录只记账号（无密码可记）
          await saveLoginAccount(d.username, null);
          window.location.href = consumeLoginNext();
        }
      } catch {
        /* 轮询失败不打扰用户，下一轮再试 */
      }
    };
    poll();
    pollRef.current = setInterval(poll, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [qrToken, state]);

  return (
    <div className="flex flex-col items-center">
      <div className="relative w-48 h-48 rounded-lg border bg-white p-2 flex items-center justify-center">
        {confirmUrl && state !== 'loading' && state !== 'expired' ? (
          <QRCodeSVG value={confirmUrl} size={176} level="M" />
        ) : (
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        )}
        {(state === 'expired' || state === 'error') && (
          <button
            type="button"
            onClick={generate}
            className="absolute inset-0 rounded-lg bg-background/95 flex flex-col items-center justify-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <RefreshCw className="w-6 h-6" />
            <span>{state === 'expired' ? t('login.qrExpired', '二维码已过期 点击刷新') : t('login.qrError', '二维码加载失败 点击重试')}</span>
          </button>
        )}
      </div>

      <p className="mt-4 text-sm text-foreground flex items-center gap-2">
        <Smartphone className="w-4 h-4 shrink-0 text-primary" />
        {state === 'scanned'
          ? t('login.qrScanned', '已扫描，请在手机上确认登录')
          : t('login.qrScanTip', '使用手机浏览器扫码，在手机上确认登录')}
      </p>
      <p className="mt-1 text-xs text-muted-foreground" suppressHydrationWarning>
        {state === 'waiting' || state === 'scanned' ? t('login.qrExpiresIn', { n: expiresIn, defaultValue: '{{n}} 秒后二维码过期' }) : ''}
      </p>
    </div>
  );
}
