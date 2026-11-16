'use client';
import { HardDrive, Server } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { DownloadOrigin, isLocalEngineAvailable } from '@/lib/download-engine';

/**
 * 目标选择（分段控件）：本机（HardDrive）/ 后端（Server）。
 * 无 Tauri 运行时（浏览器/移动端）时本机选项隐藏，只留后端。
 * 选中态遵守 UI 铁律：bg-primary/10 text-primary + rounded，无下划线。
 */
export function EngineTargetSelect({
  value,
  onChange,
  className = '',
}: {
  value: DownloadOrigin;
  onChange: (v: DownloadOrigin) => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const localAvailable = isLocalEngineAvailable();
  const options: Array<{ key: DownloadOrigin; icon: typeof HardDrive; label: string }> = [
    ...(localAvailable ? [{ key: 'local' as const, icon: HardDrive, label: t('download.targetLocal', '本机') }] : []),
    { key: 'remote' as const, icon: Server, label: t('download.targetRemote', '后端') },
  ];
  return (
    <div className={`inline-flex items-center gap-1 p-0.5 rounded-lg bg-muted ${className}`}>
      {options.map((opt) => {
        const selected = value === opt.key;
        return (
          <button
            key={opt.key}
            type="button"
            onClick={() => onChange(opt.key)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              selected ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            <opt.icon className="w-3.5 h-3.5" />
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
