'use client';

import { useMemo } from 'react';
import { useAppStore, type SessionSpending } from '@/stores/app-store';
import { DollarSign, TrendingUp, MessageSquare, Zap, Trash2 } from 'lucide-react';
import { PageLayout } from '@/components/page-layout';

export default function SpendingPage() {
  const sessionSpending = useAppStore((s) => s.sessionSpending);
  // 从稳定的 sessionSpending 引用派生排序列表；不能在选择器里调 getAllSpending()——
  // 它每次返回新数组，zustand v5 下会让 useSyncExternalStore 无限重渲染（Maximum update depth exceeded）
  const allSpending = useMemo(
    () => Object.values(sessionSpending).sort((a, b) => b.lastUpdated - a.lastUpdated),
    [sessionSpending]
  );

  const totalCost = allSpending.reduce((sum, s) => sum + s.totalCost, 0);
  const totalMessages = allSpending.reduce((sum, s) => sum + s.messageCount, 0);
  const totalInput = allSpending.reduce((sum, s) => sum + s.totalInputTokens, 0);
  const totalOutput = allSpending.reduce((sum, s) => sum + s.totalOutputTokens, 0);

  const handleClear = () => {
    if (confirm('确定清空所有花销记录？')) {
      localStorage.removeItem('openmate-session-spending');
      useAppStore.setState({ sessionSpending: {} });
    }
  };

  return (
    <PageLayout
      title="💰 花销统计"
      headerActions={
        <button onClick={handleClear} className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-muted hover:bg-muted/80 text-muted-foreground transition-colors">
          <Trash2 className="w-3.5 h-3.5" />
          清空记录
        </button>
      }
    >
      <div className="px-3 lg:px-6 py-4 lg:py-6 h-full overflow-y-auto space-y-6">

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <SummaryCard icon={<DollarSign className="w-5 h-5 text-yellow-500" />} label="总花费" value={`$${totalCost.toFixed(4)}`} />
        <SummaryCard icon={<MessageSquare className="w-5 h-5 text-blue-500" />} label="AI回复数" value={totalMessages.toLocaleString()} />
        <SummaryCard icon={<TrendingUp className="w-5 h-5 text-green-500" />} label="输入Token" value={formatTokens(totalInput)} />
        <SummaryCard icon={<Zap className="w-5 h-5 text-purple-500" />} label="输出Token" value={formatTokens(totalOutput)} />
      </div>

      {/* Per-session table */}
      <div className="rounded-xl border border-border overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-muted/50 text-left">
              <th className="px-4 py-2.5 font-medium text-muted-foreground">会话</th>
              <th className="px-4 py-2.5 font-medium text-muted-foreground text-right">回复数</th>
              <th className="px-4 py-2.5 font-medium text-muted-foreground text-right">输入Token</th>
              <th className="px-4 py-2.5 font-medium text-muted-foreground text-right">输出Token</th>
              <th className="px-4 py-2.5 font-medium text-muted-foreground text-right">花费</th>
              <th className="px-4 py-2.5 font-medium text-muted-foreground text-right">最后更新</th>
            </tr>
          </thead>
          <tbody>
            {allSpending.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-muted-foreground text-sm">
                  暂无花销记录，开始聊天后自动统计
                </td>
              </tr>
            ) : (
              allSpending.map((s) => (
                <tr key={s.sessionId} className="border-t border-border/50 hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-2.5 font-mono text-xs truncate max-w-48">{s.sessionName || s.sessionId}</td>
                  <td className="px-4 py-2.5 text-right">{s.messageCount}</td>
                  <td className="px-4 py-2.5 text-right text-green-400">{s.totalInputTokens.toLocaleString()}</td>
                  <td className="px-4 py-2.5 text-right text-purple-400">{s.totalOutputTokens.toLocaleString()}</td>
                  <td className="px-4 py-2.5 text-right text-yellow-500 font-medium">${s.totalCost.toFixed(4)}</td>
                  <td className="px-4 py-2.5 text-right text-muted-foreground text-xs">{new Date(s.lastUpdated).toLocaleString('zh-CN', { fractionalSecondDigits: 3 })}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      </div>
    </PageLayout>
  );
}

function SummaryCard({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center gap-3 p-4 rounded-xl bg-muted/30 border border-border/50">
      {icon}
      <div>
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="text-lg font-bold">{value}</div>
      </div>
    </div>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}
