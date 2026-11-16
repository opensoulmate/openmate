'use client';
import { useMemo, useRef, useState } from 'react';
import { AlertCircle, FileUp, Link, Loader2, Magnet, Video, X, Zap } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  AddDownloadInput, DownloadOrigin, engineFor, readStoredTarget, storeTarget,
} from '@/lib/download-engine';
import { torrentFileToMagnet } from '@/lib/torrent-file';
import { EngineTargetSelect } from './engine-target-select';

interface ParsedLine {
  raw: string;
  valid: boolean;
}

function parseLines(text: string): ParsedLine[] {
  return text
    .split(/[\n\r]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((raw) => ({
      raw,
      valid: /^(https?:\/\/|ftp:\/\/|magnet:\?|thunder:\/\/|flashget:\/\/|qqdl:\/\/)/i.test(raw),
    }));
}

/**
 * 新建下载对话框（弹层）：多行 URL 批量（实时「N 个 URL·M 个有效」计数）、
 * 磁力链、.torrent 文件选择（前端解析出磁力链）、目标选择（本机/后端，记忆上次选择）。
 */
export function NewDownloadDialog({
  open,
  onClose,
  onAdded,
}: {
  open: boolean;
  onClose: () => void;
  onAdded: () => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const [target, setTarget] = useState<DownloadOrigin>(readStoredTarget);
  const [torrent, setTorrent] = useState<{ name: string; magnet: string } | null>(null);
  const [torrentError, setTorrentError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const lines = useMemo(() => parseLines(text), [text]);
  const validCount = lines.filter((l) => l.valid).length;

  if (!open) return null;

  const pickTorrent = async (file: File) => {
    setTorrentError(null);
    setTorrent(null);
    try {
      const buf = await file.arrayBuffer();
      const parsed = await torrentFileToMagnet(buf);
      if (parsed) {
        setTorrent({ name: parsed.name || file.name, magnet: parsed.magnet });
      } else {
        setTorrentError(t('download.torrentParseFailed', '无法解析该 .torrent（需要安全上下文），请改用磁力链'));
      }
    } catch {
      setTorrentError(t('download.torrentParseFailed', '无法解析该 .torrent（需要安全上下文），请改用磁力链'));
    }
  };

  const submit = async () => {
    const inputs: AddDownloadInput[] = lines.filter((l) => l.valid).map((l) => ({ url: l.raw }));
    if (torrent) inputs.push({ url: torrent.magnet, filename: torrent.name });
    if (inputs.length === 0) return;
    setAdding(true);
    setError(null);
    const engine = engineFor(target);
    storeTarget(target);
    let failed = 0;
    for (const input of inputs) {
      try {
        await engine.add(input);
      } catch {
        failed += 1;
      }
    }
    setAdding(false);
    if (failed > 0) {
      setError(t('download.addPartialFailed', '有 {{n}} 个任务添加失败', { n: failed }));
      return;
    }
    setText('');
    setTorrent(null);
    onAdded();
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40" onClick={onClose}>
      <div
        className="w-full max-w-xl rounded-2xl border border-border bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 对话框标题行 */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
          <h3 className="text-sm lg:text-base font-medium">{t('download.newDownload', '新建下载')}</h3>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-3.5">
          {/* 多行 URL 批量输入 + 实时计数 */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs lg:text-sm font-medium">
                {t('download.linksLabel', '下载链接（每行一个，可批量）')}
              </label>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary">
                {t('download.urlCount', '{{n}} 个 URL · {{m}} 个有效', { n: lines.length, m: validCount })}
              </span>
            </div>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={4}
              autoFocus
              placeholder={t('download.urlPlaceholderMulti', 'https://example.com/file.zip\nmagnet:?xt=urn:btih:...\nthunder://...')}
              className="w-full px-3 py-2 rounded-lg bg-muted text-xs lg:text-sm outline-none border border-border focus:border-primary resize-y"
            />
            <div className="flex items-center gap-3 mt-1.5 text-[10px] text-muted-foreground">
              <span className="flex items-center gap-1"><Link className="w-3 h-3" /> HTTP/FTP</span>
              <span className="flex items-center gap-1"><Magnet className="w-3 h-3" /> BT/磁力</span>
              <span className="flex items-center gap-1"><Video className="w-3 h-3" /> YouTube/B站</span>
              <span className="flex items-center gap-1"><Zap className="w-3 h-3" /> thunder://</span>
            </div>
          </div>

          {/* .torrent 文件选择 */}
          <div>
            <label className="text-xs lg:text-sm font-medium mb-1.5 block">
              {t('download.torrentFile', '.torrent 文件')}
            </label>
            <input
              ref={fileRef}
              type="file"
              accept=".torrent,application/x-bittorrent"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) pickTorrent(f);
                e.target.value = '';
              }}
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="flex items-center gap-2 px-3 py-2 rounded-lg border border-dashed border-border text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <FileUp className="w-4 h-4" />
              {torrent ? torrent.name : t('download.chooseTorrent', '选择 .torrent 文件（自动解析为磁力链）')}
            </button>
            {torrent && (
              <div className="flex items-center gap-1.5 text-[11px] text-green-500 mt-1.5">
                <Magnet className="w-3 h-3 shrink-0" />
                <span className="truncate">{torrent.magnet}</span>
                <button
                  type="button"
                  onClick={() => setTorrent(null)}
                  className="p-0.5 rounded hover:bg-muted text-muted-foreground hover:text-foreground shrink-0"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            )}
            {torrentError && (
              <div className="flex items-center gap-1.5 text-[11px] text-red-500 mt-1.5">
                <AlertCircle className="w-3 h-3 shrink-0" />
                {torrentError}
              </div>
            )}
          </div>

          {/* 目标选择（本机/后端，记忆上次选择） */}
          <div>
            <label className="text-xs lg:text-sm font-medium mb-1.5 block">
              {t('download.targetLabel', '下载目标')}
            </label>
            <EngineTargetSelect value={target} onChange={setTarget} />
          </div>

          {error && (
            <div className="flex items-center gap-1.5 text-[11px] text-red-500">
              <AlertCircle className="w-3 h-3 shrink-0" />
              {error}
            </div>
          )}
        </div>

        {/* 对话框底部按钮行 */}
        <div className="flex items-center justify-end gap-2 px-5 py-3.5 border-t border-border">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg border text-xs lg:text-sm text-muted-foreground hover:bg-muted"
          >
            {t('common.cancel', '取消')}
          </button>
          <button
            onClick={submit}
            disabled={adding || (validCount === 0 && !torrent)}
            className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-xs lg:text-sm font-medium hover:bg-primary/90 disabled:opacity-50 flex items-center gap-1.5"
          >
            {adding ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
            {t('download.startDownload', '开始下载')}
          </button>
        </div>
      </div>
    </div>
  );
}
