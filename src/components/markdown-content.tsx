'use client';
import { copyToClipboard } from "@/lib/clipboard";
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { Copy, Check, Pencil, Eye, EyeOff } from 'lucide-react';
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
              title={showSecrets ? '重新遮蔽敏感数据' : '显示敏感数据（默认已遮蔽）'}
            >
              {showSecrets ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
              <span>{showSecrets ? '脱敏' : '显示'}</span>
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