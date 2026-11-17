import { useState, useEffect, useCallback, useRef } from "react";
import type { RendererProps } from "./base";
import { RendererToolbar } from "./renderer-toolbar";
import { Eye, Code, Pencil } from "lucide-react";
import { buildDiffPrompt } from "./diff-utils";
import { decodeWithEncoding, decodeDataUrl } from "./encoding-utils";
import { SelectionAIBar } from "./selection-ai-bar";

type ViewMode = 'preview' | 'code' | 'edit';

export function HtmlRenderer({ fileUrl, fileBuffer, fileName, onSave, onSendToAgent }: RendererProps) {
  const [source, setSource] = useState('');
  const [originalSource, setOriginalSource] = useState('');
  const [previewSrc, setPreviewSrc] = useState<string | undefined>();
  const [mode, setMode] = useState<ViewMode>('preview');
  const [copied, setCopied] = useState(false);
  const [dirty, setDirty] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let text = '';
    if (fileBuffer) {
      text = new TextDecoder().decode(fileBuffer);
    } else if (fileUrl) {
      if (fileUrl.startsWith('data:')) {
        try {
          const commaIdx = fileUrl.indexOf(',');
          if (commaIdx >= 0) text = decodeDataUrl(fileUrl);
        } catch {}
      } else {
        fetch(fileUrl).then(r => r.arrayBuffer()).then(buf => { const t = decodeWithEncoding(buf); setSource(t); setOriginalSource(t); }).catch(() => {});
        return;
      }
    }
    setSource(text);
    setOriginalSource(text);
  }, [fileUrl, fileBuffer]);

  useEffect(() => {
    if (mode === 'edit' && source) {
      const blob = new Blob([source], { type: 'text/html' });
      const url = URL.createObjectURL(blob);
      setPreviewSrc(url);
      return () => URL.revokeObjectURL(url);
    } else if (mode === 'preview' && fileUrl) {
      setPreviewSrc(fileUrl);
    }
  }, [mode, source, fileUrl]);

  const handleCopy = useCallback(async () => {
    try { await navigator.clipboard.writeText(source); } catch {
      const ta = document.createElement('textarea'); ta.value = source;
      document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
    }
    setCopied(true); setTimeout(() => setCopied(false), 2000);
  }, [source]);

  const handlePrint = useCallback(() => {
    const win = window.open('', '_blank');
    if (!win) return;
    win.document.write(source); win.document.close();
    setTimeout(() => { try { win.print(); } catch {} }, 500);
  }, [source]);

  const handleSave = useCallback(() => {
    onSave?.(source, fileName);
    setDirty(false);
  }, [onSave, source, fileName]);

  const handleSendToAI = useCallback(async (instruction: string) => {
    if (!onSendToAgent) return;
    const prompt = buildDiffPrompt(originalSource, source, fileName, instruction, 'HTML');
    const result = await onSendToAgent(source, prompt, fileName);
    if (result) {
      setSource(result);
      setDirty(true);
      setMode('edit');
    }
  }, [onSendToAgent, source, originalSource, fileName, dirty]);

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setSource(e.target.value);
    setDirty(true);
  }, []);

  const ModeBtn = ({ icon: Icon, label, m }: { icon: React.ComponentType<{ className?: string }>; label: string; m: ViewMode }) => (
    <button onClick={() => setMode(m)}
      className={`flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors ${mode === m ? 'bg-primary/20 text-primary' : 'hover:bg-muted/30 text-muted-foreground'}`}>
      <Icon className="w-3.5 h-3.5" /><span className="hidden sm:inline">{label}</span>
    </button>
  );

  return (
    <div className="flex flex-col h-full min-h-0">
      <RendererToolbar fileName={fileName} fileUrl={fileUrl} category="HTML"
        onCopy={handleCopy} copied={copied} onPrint={handlePrint}
        onSave={onSave ? handleSave : undefined} dirty={dirty}
        onSendToAgent={onSendToAgent ? handleSendToAI : undefined}>
        <ModeBtn icon={Eye} label="预览" m="preview" />
        <ModeBtn icon={Code} label="代码" m="code" />
        <ModeBtn icon={Pencil} label="编辑" m="edit" />
      </RendererToolbar>

      {mode === 'preview' && (
        <div className="flex-1 min-h-0">
          {previewSrc ? <iframe src={previewSrc} className="w-full h-full border-0 bg-white" title={fileName} sandbox="allow-scripts allow-same-origin allow-forms allow-popups" />
            : <div className="flex items-center justify-center h-full text-muted-foreground text-sm">加载中...</div>}
        </div>
      )}
      {mode === 'code' && (
        <div className="flex-1 min-h-0 overflow-auto bg-[var(--color-background)]">
          <pre className="p-4 text-xs leading-relaxed font-mono text-green-400 whitespace-pre-wrap break-all">{source || '（空文件）'}</pre>
        </div>
      )}
      {mode === 'edit' && (
        <div className="flex flex-1 min-h-0">
          <div className="flex-1 min-w-0 border-r border-border/30">
            <textarea value={source} onChange={handleChange}
              className="w-full h-full resize-none bg-[var(--color-background)] text-green-400 font-mono text-xs p-4 outline-none leading-relaxed"
              spellCheck={false} placeholder="在此编辑HTML代码..." />
          </div>
          <div className="flex-1 min-w-0">
            {previewSrc ? <iframe src={previewSrc} className="w-full h-full border-0 bg-white" title="实时预览" sandbox="allow-scripts allow-same-origin allow-forms allow-popups" />
              : <div className="flex items-center justify-center h-full text-muted-foreground text-sm">输入代码查看预览</div>}
          </div>
        </div>
      )}
    </div>
  );
}
