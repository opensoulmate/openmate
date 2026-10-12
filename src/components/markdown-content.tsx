'use client';
import { copyToClipboard } from "@/lib/clipboard";
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { Copy, Check, Pencil, Eye, EyeOff, FileText, Download } from 'lucide-react';
import { useState, useCallback, lazy, Suspense, useMemo, memo, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { rehypeSecretMask } from '@/lib/rehype-secret-mask';
import { SecretAwareSpan } from '@/components/secret-pill';
import { scanForSecrets, maskCodeInText } from '@/lib/secret-redaction';
import { MermaidDiagram } from './mermaid-diagram';
import { useTranslation } from 'react-i18next';
import { useAppStore } from '@/stores/app-store';
import { getApiBaseUrl } from '@/lib/api-client';

// Lazy load Monaco editor to avoid SSR issues
const MonacoEditor = lazy(() => import('@monaco-editor/react'));

// Memoized CodeBlock component to prevent unnecessary re-renders
const CodeBlock = memo(function CodeBlock({ 
  code, 
  language, 
  onApply 
}: CodeBlockProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editedCode, setEditedCode] = useState(code);
  // ── P1 流式输出脱敏展示：围栏代码块遮蔽（散文/行走 rehypeSecretMask）──
  const [showSecrets, setShowSecrets] = useState(false);
  const hasSecrets = useMemo(() => scanForSecrets(code, 'high').length > 0, [code]);
  const displayCode = useMemo(
    () => (hasSecrets && !showSecrets ? maskCodeInText(code, 'high') : code),
    [code, hasSecrets, showSecrets]
  );
  const isMountedRef = useRef(true);

  useEffect(() => {
    setEditedCode(code);
  }, [code]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const handleCopy = useCallback(() => {
    if (!isMountedRef.current) return;
    
    // WYSIWYG：未揭示时复制遮蔽文本（剪贴板不落真密钥），揭示后复制原文
    copyToClipboard(isEditing ? editedCode : displayCode);
    setCopied(true);
    
    const timer = setTimeout(() => {
      if (isMountedRef.current) {
        setCopied(false);
      }
    }, 2000);
    
    return () => clearTimeout(timer);
  }, [code, editedCode, isEditing, displayCode]);

  const handleApply = useCallback(() => {
    if (!isMountedRef.current) return;
    
    onApply?.(editedCode);
    setIsEditing(false);
  }, [editedCode, onApply]);

  const handleCancel = useCallback(() => {
    if (!isMountedRef.current) return;
    
    setEditedCode(code);
    setIsEditing(false);
  }, [code]);

  // Memoize syntax highlighter styles
  const syntaxHighlighterStyle = useMemo(() => ({
    margin: 0,
    borderRadius: 0,
  }), []);

  return (
    <div className="relative group my-2 rounded-lg overflow-hidden border border-border/50">
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-[var(--color-secondary)] text-xs text-muted-foreground">
        <span>{language || 'text'}</span>
        <div className="flex items-center gap-1">
          {hasSecrets && (
            <button
              onClick={() => setShowSecrets(v => !v)}
              className="flex items-center gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity hover:text-foreground px-1.5 py-0.5 rounded hover:bg-white/5"
              title={showSecrets
                ? t('markdown.maskSecretsTip', { defaultValue: '重新遮蔽敏感数据' })
                : t('markdown.revealSecretsTip', { defaultValue: '显示敏感数据（默认已遮蔽）' })}
            >
              {showSecrets ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
              <span>
                {showSecrets
                  ? t('markdown.maskSecrets', { defaultValue: '脱敏' })
                  : t('markdown.revealSecrets', { defaultValue: '显示' })}
              </span>
            </button>
          )}
          {!isEditing && (
            <button
              onClick={() => { setShowSecrets(true); setIsEditing(true); }}
              className="flex items-center gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity hover:text-foreground px-1.5 py-0.5 rounded hover:bg-white/5"
            >
              <Pencil className="w-3 h-3" />
              <span>{t('markdown.edit')}</span>
            </button>
          )}
          <button
            onClick={handleCopy}
            className="flex items-center gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity hover:text-foreground px-1.5 py-0.5 rounded hover:bg-white/5"
          >
            {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
            {copied ? t('markdown.copied') : t('markdown.copy')}
          </button>
        </div>
      </div>

      {/* Editor or SyntaxHighlighter */}
      {isEditing ? (
        <div className="border-t border-border/50">
          <Suspense fallback={<div className="p-4 text-muted-foreground">Loading editor...</div>}>
            <MonacoEditor
              height="300px"
              defaultLanguage={language || 'text'}
              value={editedCode}
              onChange={(value) => setEditedCode(value || '')}
              theme="vs-dark"
              options={{
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                fontSize: 14,
                wordWrap: 'on',
                automaticLayout: true,
              }}
            />
          </Suspense>
          <div className="flex justify-end gap-2 px-3 py-2 bg-[var(--color-secondary)] border-t border-border/50">
            <button
              onClick={handleCancel}
              className="px-3 py-1 text-sm bg-background rounded border border-border hover:bg-secondary"
            >
              {t('markdown.cancel')}
            </button>
            <button
              onClick={handleApply}
              className="px-3 py-1 text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90"
            >
              {t('markdown.apply')}
            </button>
          </div>
        </div>
      ) : (
        <SyntaxHighlighter
          language={language || 'text'}
          style={oneDark}
          customStyle={syntaxHighlighterStyle}
        >
          {displayCode}
        </SyntaxHighlighter>
      )}
    </div>
  );
});

interface CodeBlockProps {
  code: string;
  language: string;
  onApply?: (code: string) => void;
}

// Memoized Mermaid component
const MemoizedMermaid = memo(({ code }: { code: string }) => (
  <MermaidDiagram code={code} />
));

// ── MEDIA 标签文件卡片（消息视图；3e 统一布局：顶部路径栏/中部文件信息/底部按钮栏）──
// 历史：renderMediaTags 曾于 e3ffdd19 落地、后被 evo（fb1a5be4 [evo:a]）删除——
// 用户实际报障"发给我呀"（2026-10-12 周期#152）即此回归，恢复后勿再删。
function mimeForFile(fileName: string): string {
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  const map: Record<string, string> = {
    doc: 'application/msword',
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    xls: 'application/vnd.ms-excel',
    xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    ppt: 'application/vnd.ms-powerpoint',
    pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    pdf: 'application/pdf',
    md: 'text/markdown',
    txt: 'text/plain',
    json: 'application/json',
    html: 'text/html',
    htm: 'text/html',
    csv: 'text/csv',
    png: 'image/png',
    jpg: 'image/jpeg',
    jpeg: 'image/jpeg',
    gif: 'image/gif',
    webp: 'image/webp',
    svg: 'image/svg+xml',
    mp3: 'audio/mpeg',
    wav: 'audio/wav',
    mp4: 'video/mp4',
  };
  return map[ext] || 'application/octet-stream';
}

function MediaFileCard({ filePath }: { filePath: string }) {
  const fileName = filePath.split('/').pop() || filePath;
  const mimeType = mimeForFile(fileName);
  const downloadUrl = `http://${window.location.hostname}:8092/api/file?path=${encodeURIComponent(filePath)}`;

  return (
    <div className="my-2 rounded-xl border border-border/60 bg-background/80 overflow-hidden">
      {/* 顶部：路径栏（灰条，3e 统一布局） */}
      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
        <span className="text-[10px] text-muted-foreground/60 truncate block">{filePath}</span>
      </div>
      {/* 中部：文件信息 */}
      <div className="px-3 py-2.5 flex items-center gap-2">
        <FileText className="w-5 h-5 text-muted-foreground shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium truncate">{fileName}</p>
          <p className="text-[10px] text-muted-foreground/60">{mimeType}</p>
        </div>
      </div>
      {/* 底部：功能按钮栏（预览/下载/复制，3e 统一布局） */}
      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
        <button
          onClick={async (e) => {
            e.stopPropagation();
            try {
              const resp = await fetch(downloadUrl);
              if (!resp.ok) return;
              const blob = await resp.blob();
              const url = URL.createObjectURL(new Blob([blob], { type: mimeType }));
              const store = useAppStore.getState();
              store.setPendingFilePreview({ url, name: fileName, mimeType });
              store.setRightPanelOpen(true);
            } catch (err) {
              console.warn('[media-preview] Failed:', err);
            }
          }}
          className="flex items-center gap-1 px-2 py-1 rounded text-xs text-primary hover:bg-primary/10 transition-colors"
        >
          <Eye className="w-3 h-3" />
          预览
        </button>
        <a
          href={downloadUrl}
          download={fileName}
          className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors"
        >
          <Download className="w-3 h-3" />
          下载
        </a>
        <button
          onClick={(e) => { e.stopPropagation(); copyToClipboard(filePath); }}
          className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors"
        >
          <Copy className="w-3 h-3" />
          复制路径
        </button>
      </div>
    </div>
  );
}

type MediaSegment = { type: 'text'; text: string } | { type: 'file'; filePath: string };

function extractMediaSegments(content: string): MediaSegment[] {
  const out: MediaSegment[] = [];
  const re = /MEDIA:(\S+)/g;
  const seen = new Set<string>();
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(content)) !== null) {
    if (m.index > last) out.push({ type: 'text', text: content.slice(last, m.index) });
    const filePath = m[1];
    if (!seen.has(filePath)) {
      seen.add(filePath);
      out.push({ type: 'file', filePath });
    }
    last = m.index + m[0].length;
  }
  if (last < content.length) out.push({ type: 'text', text: content.slice(last) });
  return out;
}

// Main markdown content component
interface MarkdownContentProps {
  content: string;
  onCodeApply?: (code: string) => void;
}

const MarkdownContent = memo(function MarkdownContent({ content, onCodeApply }: MarkdownContentProps) {
  const { t } = useTranslation();

  const components = useMemo(() => ({
    code({ node, inline, className, children, ...props }: any) {
      const match = /language-(\w+)/.exec(className || '');
      const language = match ? match[1] : '';
      const code = String(children).replace(/\n$/, '');

      if (!inline && code) {
        if (language === 'mermaid') {
          return <MemoizedMermaid code={code} />;
        }
        return (
          <CodeBlock
            code={code}
            language={language}
            onApply={onCodeApply}
          />
        );
      }
      return <code className={className} {...props}>{children}</code>;
    },
    // P1 流式输出脱敏展示：密钥段落→可揭示胶囊（其余span透传）
    span: SecretAwareSpan,
  }), [onCodeApply, t]);

  // MEDIA 标签拦截：文本段照常走 markdown 渲染，文件段渲染下载卡片（消息视图）
  const segments = useMemo(() => extractMediaSegments(content), [content]);
  const hasMedia = useMemo(() => segments.some(s => s.type === 'file'), [segments]);

  if (hasMedia) {
    return (
      <div className="markdown-content">
        {segments.map((s, i) =>
          s.type === 'file' ? (
            <MediaFileCard key={`media-${i}`} filePath={s.filePath} />
          ) : s.text.trim() ? (
            <ReactMarkdown
              key={`md-${i}`}
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeSecretMask]}
              components={components}
            >
              {s.text}
            </ReactMarkdown>
          ) : null
        )}
      </div>
    );
  }

  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSecretMask]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

export default MarkdownContent;
