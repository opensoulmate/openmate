'use client';
/**
 * 网络等待交互卡 — kilocode SessionNetwork 人工拒绝窗口
 * （前端"等待网络恢复…[拒绝]"卡片，SUMMARY P0-9 HITL专项，dev-reports多轮挂账）
 *
 * 语义（kilocode session/network.ts）：断网park等待期间用户可见等待状态并可
 * 人工拒绝；网络恢复后的宽限期（RESUME_MS）= 人工拒绝窗口——窗口内拒绝 > 自动续跑。
 *
 * 数据面：GET :8092/api/network/status（3s轮询，隐藏页跳过；跨进程合并快照——
 * park发生在agent子进程）；拒绝：POST :8092/api/network/reject {wait_id} →
 * 跨进程拒绝标志文件 → park所在进程下一轮询消费即中止（error=human_rejected），
 * 走既有"[LLM错误: …]"可见失败契约。
 *
 * 受控/自轮询两用：props.state 传入=受控模式（渲染测试/外部状态源）；
 * 不传=自轮询模式（chat-client生产挂载）。
 */

import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { WifiOff, Loader2, XCircle, Wifi } from 'lucide-react';

export interface NetworkWaitInfo {
  id: string;
  message: string;
  created: number; // epoch秒（network_state.NetworkWait.created=time.time()）
  restored?: number | null; // 非空=已进入恢复宽限期（人工拒绝窗口）
  turn_id?: string | null;
  merged_calls?: number;
  max_wait_s?: number;
  resume_grace_s?: number;
}

function apiBase(): string {
  // 与chat-client getApiUrl()同款：ACP Proxy跑在同hostname的8092
  return `http://${window.location.hostname}:8092`;
}

export function NetworkWaitBanner({
  state,
  onReject,
}: {
  state?: NetworkWaitInfo | null;
  onReject?: (id: string) => void;
}) {
  const { t } = useTranslation();
  const controlled = state !== undefined;
  const [live, setLive] = useState<NetworkWaitInfo | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  const wait = controlled ? state : live;

  // 自轮询模式：3s查一次park状态（受控模式/SSR不轮询）
  useEffect(() => {
    if (controlled || typeof window === 'undefined') return;
    let stopped = false;
    const tick = async () => {
      if (typeof document !== 'undefined' && document.hidden) return;
      try {
        const r = await fetch(`${apiBase()}/api/network/status`);
        const data = await r.json();
        if (stopped) return;
        const waits = ((data && data.waits) || []).filter(
          (w: { state?: string }) => w && w.state === 'blocked'
        );
        const w = waits[0] || null;
        setLive(
          w
            ? {
                id: w.id,
                message: w.message,
                created: w.created,
                restored: w.restored ?? null,
                turn_id: w.turn_id ?? null,
                merged_calls: w.merged_calls ?? 0,
                max_wait_s: data?.stats?.max_wait_s,
                resume_grace_s: data?.stats?.resume_grace_s,
              }
            : null
        );
        if (!w) setRejecting(false);
      } catch {
        // 状态端点不可达=不打扰聊天（fail-safe静默）
      }
    };
    tick();
    const iv = setInterval(tick, 3000);
    return () => {
      stopped = true;
      clearInterval(iv);
    };
  }, [controlled]);

  // 秒表：卡片存活期间"已等待"每秒增长
  useEffect(() => {
    if (!wait) return;
    setNowSec(Math.floor(Date.now() / 1000));
    const iv = setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(iv);
  }, [wait?.id]);

  const handleReject = useCallback(async () => {
    if (!wait || rejecting) return;
    setRejecting(true);
    if (controlled) {
      onReject?.(wait.id);
      return;
    }
    try {
      await fetch(`${apiBase()}/api/network/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ wait_id: wait.id }),
      });
    } catch {
      setRejecting(false); // 请求失败允许重试
    }
  }, [wait, rejecting, controlled, onReject]);

  if (!wait) return null;

  const elapsed = Math.max(0, nowSec - Math.floor(wait.created || 0));
  const inGrace = wait.restored != null && wait.restored > 0;
  const maxWait = Math.round(wait.max_wait_s ?? 30);
  const merged = wait.merged_calls ?? 0;

  return (
    <div className="fixed bottom-24 left-1/2 -translate-x-1/2 z-40 w-[min(92vw,420px)]">
      <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 shadow-lg backdrop-blur px-4 py-3 space-y-2">
        {/* 标题行：图标必须JSX（lucide forwardRef对象不可调用） */}
        <div className="flex items-center gap-2">
          {inGrace ? (
            <Wifi className="w-4 h-4 text-amber-600 dark:text-amber-400" />
          ) : (
            <WifiOff className="w-4 h-4 text-amber-600 dark:text-amber-400" />
          )}
          <span className="text-sm font-semibold text-amber-700 dark:text-amber-300">
            {inGrace
              ? t('networkWait.restored', '网络已恢复，{{seconds}}s 内自动续跑（可拒绝）', {
                  seconds: Math.round(wait.resume_grace_s ?? 10),
                })
              : t('networkWait.title', '等待网络恢复')}
          </span>
          <span className="ml-auto text-xs text-muted-foreground font-mono">
            {t('networkWait.elapsed', '已等待 {{seconds}}s（上限 {{max}}s）', {
              seconds: elapsed,
              max: maxWait,
            })}
          </span>
        </div>

        {/* 详情行：断网原因 + 合并等待数 */}
        <div className="text-xs text-muted-foreground">
          {inGrace
            ? t('networkWait.waiting', '网络中断，正在等待恢复…')
            : wait.message || t('networkWait.waiting', '网络中断，正在等待恢复…')}
          {merged > 0 && (
            <span className="ml-1">
              · {t('networkWait.merged', '{{count}} 个并发调用合并等待', { count: merged })}
            </span>
          )}
        </div>

        {/* 操作行 */}
        <div className="flex items-center gap-2">
          {rejecting ? (
            <span className="flex items-center gap-1.5 text-xs text-red-600 dark:text-red-400">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              {t('networkWait.rejecting', '已拒绝，正在停止…')}
            </span>
          ) : (
            <button
              onClick={handleReject}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-card hover:bg-destructive/10 hover:text-destructive hover:border-destructive/30 transition-all"
            >
              <XCircle className="w-3.5 h-3.5" />
              {t('networkWait.reject', '拒绝并停止')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
