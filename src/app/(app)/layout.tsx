'use client';
import { useState, useEffect } from 'react';
import '@/styles/sidebar.css';
import { AppShell } from '@/components/app-shell';
import { CommandMenu } from '@/components/command-menu';
import { TaskTriageLauncher } from '@/components/task-triage-gate';
import { GlobalShortcuts } from '@/components/global-shortcuts';
import { AuthExpiryGuard } from '@/components/auth-expiry-guard';
import { LoginPage } from '@/components/login-page';
import { ToastProvider } from '@/components/toast-provider';
import { isLoggedIn } from '@/lib/api-client';
import { consumeLoginNext } from '@/lib/auth-errors';

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const [loggedIn, setLoggedIn] = useState(false);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    setLoggedIn(isLoggedIn());
    setChecking(false);
  }, []);

  if (checking) return null;

  if (!loggedIn) {
    // 登录成功回跳原路径（401 接管时已存；无则落 /chat，与既有语义一致）
    return <LoginPage onLogin={() => { window.location.href = consumeLoginNext(); }} />;
  }

  return (
    <ToastProvider>
      <AuthExpiryGuard />
      <AppShell>
        <main className="flex-1 flex flex-col overflow-hidden">{children}</main>
        <CommandMenu />
        <TaskTriageLauncher />
        <GlobalShortcuts />
      </AppShell>
    </ToastProvider>
  );
}
