'use client';
import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Loader2, CheckCircle2, XCircle } from 'lucide-react';
import { setToken, setUserId, setUserName } from '@/lib/api-client';
import { useTranslation } from 'react-i18next';

// 微信/QQ OAuth 授权回跳落点：登录成功带 token 入库后进 /chat；绑定结果显示后回设置页
function OAuthCallback() {
  const params = useSearchParams();
  const [status, setStatus] = useState<'working' | 'ok' | 'error'>('working');
  const [msg, setMsg] = useState('');
  const { t } = useTranslation();

  useEffect(() => {
    const token = params.get('token');
    const userId = params.get('user_id');
    const username = params.get('username');
    const bound = params.get('bound');
    const error = params.get('error');
    if (error) {
      setStatus('error');
      setMsg(error);
      return;
    }
    if (token && userId) {
      setToken(token);
      setUserId(userId);
      if (username) setUserName(username);
      setStatus('ok');
      setMsg(t('oauth.loginSuccess', '登录成功，正在进入…'));
      setTimeout(() => (window.location.href = '/chat'), 800);
      return;
    }
    if (bound) {
      setStatus('ok');
      setMsg(t('oauth.bound', '绑定成功！'));
      setTimeout(() => (window.location.href = '/settings'), 1200);
      return;
    }
    setStatus('error');
    setMsg(t('oauth.missingParams', '授权回跳参数缺失，请重新发起登录'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex items-center justify-center h-screen bg-background">
      <div className="p-8 rounded-xl border bg-card w-96 shadow-lg text-center">
        {status === 'working' && <Loader2 className="w-10 h-10 animate-spin text-primary mx-auto mb-4" />}
        {status === 'ok' && <CheckCircle2 className="w-10 h-10 text-green-500 mx-auto mb-4" />}
        {status === 'error' && <XCircle className="w-10 h-10 text-red-500 mx-auto mb-4" />}
        <p className="text-sm text-muted-foreground">{msg || t('oauth.processing', '正在处理授权…')}</p>
        {status === 'error' && (
          <button onClick={() => (window.location.href = '/')} className="mt-4 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm">
            {t('oauth.backToLogin', '返回登录')}
          </button>
        )}
      </div>
    </div>
  );
}

export default function OAuthCallbackPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center h-screen"><Loader2 className="w-10 h-10 animate-spin text-primary" /></div>}>
      <OAuthCallback />
    </Suspense>
  );
}
