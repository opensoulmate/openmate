'use client';
import { useState, useEffect } from 'react';
import '@/styles/sidebar.css';
import { AppShell } from '@/components/app-shell';
import { CommandMenu } from '@/components/command-menu';
import { GlobalShortcuts } from '@/components/global-shortcuts';
import { AuthExpiryGuard } from '@/components/auth-expiry-guard';
import { LoginPage } from '@/components/login-page';
import { ToastProvider } from '@/components/toast-provider';
import { isLoggedIn } from '@/lib/api-client';
import { rememberLoginNext } from '@/lib/auth-errors';
import { usePathname } from 'next/navigation';

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const [loggedIn, setLoggedIn] = useState(false);
  const [checking, setChecking] = useState(true);
  const pathname = usePathname();

  useEffect(() => {
    const ok = isLoggedIn();
    // 未登录访问深层页：先记住当前路径，登录成功后回原页（整页登录墙替掉 AppShell）
    if (!ok) rememberLoginNext();
    setLoggedIn(ok);
    setChecking(false);
  }, [pathname]);

  if (checking) return null;

  if (!loggedIn) {
    // 登录成功回跳原路径（深链/401 接管时已存；无则落 /chat，与既有语义一致）
    // LoginPage 自身完成整页回跳（window.location.href 强制刷新重建 WebSocket）
    return <LoginPage />;
  }

  return (
    <ToastProvider>
      <AuthExpiryGuard />
      <AppShell>
        <main className="flex-1 flex flex-col overflow-hidden">{children}</main>
        <CommandMenu />
        <GlobalShortcuts />
      </AppShell>
    </ToastProvider>
  );
}
