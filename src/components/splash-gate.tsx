'use client';

// 全平台启动门（web 层统一——安卓 APK/桌面/网页/未来 iOS 全员自带启动动画+断连提示）。
// 产品铁律：所有版本都必须有启动动画；无法连接服务器时必须有明确提示，绝不静默挂死。
// 视觉语言与 desktop/splash.html 同源：深底（#0b0f14）+ 青色（--cyan / --cyan-hi）。
import { useCallback, useEffect, useRef, useState } from 'react';
import { getApiBaseUrl } from '@/lib/api-client';

type Phase = 'boot' | 'fail' | 'done';

const STAGES = ['正在唤醒 OpenMate', '正在连接服务器', '正在同步工作区'];

export default function SplashGate() {
  const [phase, setPhase] = useState<Phase>('boot');
  const [pct, setPct] = useState(8);
  const [stage, setStage] = useState(0);
  const [fading, setFading] = useState(false);
  const [server, setServer] = useState('');
  const abortedRef = useRef(false);

  const check = useCallback(async () => {
    setPhase('boot');
    setFading(false);
    setPct(8);
    setStage(0);
    setServer(typeof window !== 'undefined' ? getApiBaseUrl().replace(/^https?:\/\//, '') : '');
    // 进度动画：随阶段推进（不等真连接也能看到动效）
    const t1 = setTimeout(() => { setPct(38); setStage(1); }, 220);
    const t2 = setTimeout(() => { setPct(68); setStage(2); }, 520);
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 8000);
      const res = await fetch(`${getApiBaseUrl()}/api/health`, { signal: ctrl.signal, cache: 'no-store' });
      clearTimeout(timer);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (abortedRef.current) return;
      clearTimeout(t1); clearTimeout(t2);
      setPct(100);
      setStage(2);
      setFading(true);
      setTimeout(() => setPhase('done'), 360);
    } catch {
      if (abortedRef.current) return;
      clearTimeout(t1); clearTimeout(t2);
      setPhase('fail');
    }
  }, []);

  useEffect(() => {
    abortedRef.current = false;
    check();
    return () => { abortedRef.current = true; };
  }, [check]);

  if (phase === 'done') return null;

  return (
    <div
      className={`fixed inset-0 z-[9999] flex flex-col items-center justify-center transition-opacity duration-300 ${fading ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
      style={{ background: '#0b0f14' }}
    >
      <img
        src="/mark-wide.png"
        alt="OpenMate"
        style={{ height: 'clamp(48px, 11vw, 88px)', width: 'auto', marginBottom: 14 }}
      />
      <div
        className="text-2xl font-semibold tracking-wide"
        style={{ color: 'var(--cyan-hi, #55b9dc)' }}
      >
        OpenMate
      </div>
      <div className="mt-2 text-xs text-white/40">
        {phase === 'fail' ? '连接失败' : STAGES[stage]}
      </div>

      {phase === 'boot' ? (
        <div className="mt-6 w-64 h-1 rounded-full overflow-hidden bg-white/10">
          <div
            className="h-full rounded-full transition-all duration-300"
            style={{ width: `${pct}%`, background: 'linear-gradient(90deg, var(--cyan, #3d9fc4), var(--cyan-hi, #55b9dc))' }}
          />
        </div>
      ) : (
        <div className="mt-6 flex flex-col items-center gap-3">
          <div className="text-sm" style={{ color: '#f87171' }}>
            ⚠ 无法连接服务器
          </div>
          <div className="text-xs text-white/40">
            服务器：{server || '未知'}　请检查网络或服务器状态
          </div>
          <button
            onClick={check}
            className="px-4 py-1.5 rounded text-sm text-white/90 transition-colors hover:bg-white/10"
            style={{ background: 'rgba(85, 185, 220, .18)' }}
          >
            重试
          </button>
        </div>
      )}
    </div>
  );
}
