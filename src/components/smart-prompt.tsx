'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { cn } from '@/lib/utils';
import {
  Send,
  Sparkles,
  Loader2,
  RotateCcw,
  ChevronUp,
  ChevronDown,
  Mic,
  Square,
  Smile,
  Star,
  Paperclip,
  Check,
  Settings2,
} from 'lucide-react';
import { CodeMirrorEditor } from '@/components/codemirror-editor';

// ── Types ────────────────────────────────────────────────────────

interface SmartPromptFields {
  task: string;
  role: string;
  background: string;
  constraints: string;
  format: string;
}

interface SmartPromptProps {
  /** Callback when user sends the assembled prompt */
  onSend: (assembled: string, fields: SmartPromptFields) => void;
  /** 是否有附件待发送 — 纯附件（无文字）时也允许发送 */
  hasAttachments?: boolean;
  isReadingFiles?: boolean;
  /** Whether the AI is currently generating */
  isLoading?: boolean;
  /** Placeholder for the task field */
  placeholder?: string;
  /** Additional context to pass to the auto-generate API */
  context?: string;
  /** Extra className */
  className?: string;
  /** Footer slot (e.g. action buttons) rendered inside the border */
  footer?: React.ReactNode;
  /** Called when file attach button is clicked */
  onFileClick?: () => void;
  /** 语音输入（17.2）：点击麦克风开始/停止录音 */
  onMicClick?: () => void;
  /** 是否正在录音（按钮变红+计时） */
  isRecording?: boolean;
  /** 已录音秒数 */
  recordingSeconds?: number;
  /** Paste handler for clipboard images */
  onPaste?: (e: React.ClipboardEvent) => void;
  /** Initial task value — auto-expands and triggers generate */
  initialTask?: string;
  /** Full session fields for per-session state sync */
  sessionFields?: { task: string; role: string; background: string; constraints: string; format: string };
  /** Called when any field changes (saves full state to session) */
  onFieldsChange?: (fields: { task: string; role: string; background: string; constraints: string; format: string }) => void;
  /** Expose clear function for parent to call */
  onClearInput?: (clearFn: () => void) => void;
  /** Session ID — drives per-session field sync on switch */
  sessionId?: string;
  /** Increment to trigger full clear (all fields + expanded) */
  clearTrigger?: number;
  /** Increment to force-load sessionFields into SmartPrompt (for edit/external updates) */
  loadFieldsTrigger?: number;
}

// ── Field Definitions ────────────────────────────────────────────

const FIELD_DEFS = [
  { key: 'background' as const, icon: '📋', label: '背景', placeholder: '例如：Node.js后端服务' },
  { key: 'role' as const, icon: '👤', label: '角色', placeholder: '例如：性能优化工程师' },
  { key: 'format' as const, icon: '📐', label: '格式', placeholder: '例如：给出代码示例' },
  { key: 'constraints' as const, icon: '📏', label: '约束', placeholder: '例如：不能改数据库结构' },
];

// ── Prompt Assembly ──────────────────────────────────────────────

function assemblePrompt(fields: SmartPromptFields): string {
  const hasMeta = fields.role.trim() || fields.background.trim() || fields.constraints.trim() || fields.format.trim();

  // 纯task没有其他字段 → 直接返回原文，不做结构化
  if (!hasMeta) {
    return fields.task.trim();
  }

  const parts: string[] = [];

  if (fields.role.trim()) {
    parts.push(`## 角色\n${fields.role.trim()}`);
  }
  if (fields.background.trim()) {
    parts.push(`## 背景\n${fields.background.trim()}`);
  }
  if (fields.task.trim()) {
    parts.push(`## 任务\n${fields.task.trim()}`);
  }
  if (fields.constraints.trim()) {
    parts.push(`## 约束\n${fields.constraints.trim()}`);
  }
  if (fields.format.trim()) {
    parts.push(`## 输出格式\n${fields.format.trim()}`);
  }

  return parts.join('\n\n');
}

// ── Main Component ───────────────────────────────────────────────

export function SmartPrompt({
  onSend,
  isLoading = false,
  placeholder = '输入任务直接发送，点 ✨ 自动生成扩展提示词（Enter 发送，Shift+Enter 换行）',
  context,
  className,
  footer,
  onFileClick,
  onMicClick,
  isRecording = false,
  recordingSeconds = 0,
  onPaste,
  hasAttachments,
  isReadingFiles = false,
  initialTask,
  sessionFields,
  onFieldsChange,
  onClearInput,
  sessionId,
  clearTrigger,
  loadFieldsTrigger,
}: SmartPromptProps) {
  const [fields, setFields] = useState<SmartPromptFields>({
    task: '',
    role: '',
    background: '',
    constraints: '',
    format: '',
  });

  // Apply initialTask when set externally (e.g. quick cards)
  useEffect(() => {
    if (initialTask && initialTask !== fields.task) {
      setFields(prev => ({ ...prev, task: initialTask }));
      setExpanded(true);
    }
  }, [initialTask]);

  // Track when updateField is firing so we skip the sessionFields sync (avoid loop)
  const internalUpdateRef = useRef(false);

  // Sync full session fields when switching sessions
  const prevSessionIdRef = useRef(sessionId);
  useEffect(() => {
    const sessionChanged = sessionId !== prevSessionIdRef.current;
    prevSessionIdRef.current = sessionId;

    // Only skip if this was an internal (typing) update AND the session didn't change
    if (internalUpdateRef.current && !sessionChanged) {
      internalUpdateRef.current = false;
      return;
    }
    internalUpdateRef.current = false;

    if (sessionFields) {
      setFields(prev => {
        const differs = Object.keys(sessionFields).some(k => sessionFields[k as keyof typeof sessionFields] !== prev[k as keyof typeof prev]);
        return differs ? sessionFields : prev;
      });
    } else if (sessionChanged) {
      // Switching to a session with no saved fields — clear input
      setFields({ task: '', role: '', background: '', constraints: '', format: '' });
    }
  }, [sessionId, sessionFields]);

  // LoadFieldsTrigger: parent can increment to force-load sessionFields (e.g. after edit)
  const lastLoadRef = useRef(0);
  useEffect(() => {
    if (loadFieldsTrigger && loadFieldsTrigger > lastLoadRef.current) {
      lastLoadRef.current = loadFieldsTrigger;
      if (sessionFields) {
        setFields(sessionFields);
      }
    }
  }, [loadFieldsTrigger]);

  // ClearTrigger: parent can increment to clear all fields
  const lastClearRef = useRef(0);
  useEffect(() => {
    if (clearTrigger && clearTrigger > lastClearRef.current) {
      lastClearRef.current = clearTrigger;
      setFields({ task: '', role: '', background: '', constraints: '', format: '' });
      setGenerated(false);
      if (onFieldsChange) onFieldsChange({ task: '', role: '', background: '', constraints: '', format: '' });
    }
  }, [clearTrigger, onFieldsChange]);

  const [generating, setGenerating] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const generateTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Manual generate: user clicks ✨ button to generate fields
  const autoGenerate = useCallback(
    async (task: string) => {
      if (!task.trim() || task.trim().length < 5) return;
      setGenerating(true);

      try {
        const res = await fetch('/api/smart-prompt/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task: task.trim(), context }),
        });

        if (res.ok) {
          const data = await res.json();
          setFields((prev) => ({
            ...prev,
            role: data.role || prev.role,
            background: data.background || prev.background,
            constraints: data.constraints || prev.constraints,
            format: data.format || prev.format,
          }));
          setGenerated(true);
        }
      } catch {
        // Silent fail — user can still use task-only mode
      } finally {
        setGenerating(false);
      }
    },
    [context],
  );

  // No auto-generate — user sends immediately, or clicks ✨ to generate fields

  const updateField = (key: keyof SmartPromptFields, value: string) => {
    internalUpdateRef.current = true;
    setFields((prev) => {
      const next = { ...prev, [key]: value };
      // Defer parent notification to avoid setState-during-render
      if (onFieldsChange) setTimeout(() => onFieldsChange(next), 0);
      return next;
    });
  };

  const handleClear = () => {
    if (fields.task || generated) {
      const empty = { task: '', role: '', background: '', constraints: '', format: '' };
      setFields(empty);
      setGenerated(false);
      if (onFieldsChange) onFieldsChange(empty);
    }
  };

  // Expose clear function to parent
  useEffect(() => {
    if (onClearInput) onClearInput(handleClear);
  }, [onClearInput, fields.task, generated]);

  const [sendMode, setSendMode] = useState<'enter' | 'ctrl-enter'>(() => {
    if (typeof window !== 'undefined') {
      return (localStorage.getItem('openmate-send-mode') as 'enter' | 'ctrl-enter') || 'enter';
    }
    return 'enter';
  });
  const [showSendMenu, setShowSendMenu] = useState(false);

  // Close send menu on outside click
  useEffect(() => {
    if (!showSendMenu) return;
    const close = () => setShowSendMenu(false);
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [showSendMenu]);

  const handleSend = () => {
    // 附件读取中：阻止发送（大文件base64读取需要时间，防止空附件发送）
    if (isReadingFiles) return;
    // 纯附件发送：文字为空但有待发附件时放行（拦截权交给onSend调用方按attachments判断）
    if (!fields.task.trim() && !hasAttachments) return;
    const assembled = assemblePrompt(fields);
    onSend(assembled, fields);
    // 发送后清空所有字段和草稿
    const empty = { task: '', role: '', background: '', constraints: '', format: '' };
    setFields(empty);
    setGenerated(false);
    if (onFieldsChange) onFieldsChange(empty);
  };

  const sendKeyLabel = sendMode === 'enter' ? 'Enter 发送，Shift+Enter 换行' : 'Ctrl+Enter 发送，Enter 换行';
  const placeholderText = `输入任务，点 ✨ 展开字段（${sendKeyLabel}）`;

  const handleKeyDown = (e: KeyboardEvent, view: any) => {
    if (sendMode === 'enter' && e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      handleSend();
      return true;
    }
    if (sendMode === 'ctrl-enter' && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSend();
      return true;
    }
    return false;
  };

  // Listen for external send trigger (from chat action bar button)
  useEffect(() => {
    const handler = () => handleSend();
    window.addEventListener('smart-prompt-send', handler);
    return () => window.removeEventListener('smart-prompt-send', handler);
  }, [handleSend]);

  return (
    <div
      className={cn(
        'border border-border rounded-xl bg-background transition-all h-full flex flex-col',
        className,
      )}
      onPaste={onPaste}
    >
      {/* Task input — always visible */}
      <div className="p-3 flex-1 min-h-0">
        <div className="relative h-full" onClick={(e) => {
          // 点击空白区域时聚焦 CodeMirror
          const target = e.target as HTMLElement;
          if (!target.closest('.cm-editor')) {
            const cm = target.querySelector('.cm-content') as HTMLElement | null;
            if (cm) cm.focus();
          }
        }}>
          <CodeMirrorEditor
            value={fields.task}
            onChange={(val: string) => updateField('task', val)}
            onKeyDown={handleKeyDown}
            placeholder={placeholderText}
            readOnly={false /* P1插话：任务运行中输入框保持可编辑，消息作为插话排队注入 */}
          />
          {/* Generating indicator */}
          {generating && (
            <div className="absolute right-0 top-0 flex items-center gap-1 text-[10px] text-muted-foreground">
              <Loader2 className="w-3 h-3 animate-spin" />
              <span>分析中...</span>
            </div>
          )}
          {/* File reading indicator */}
          {isReadingFiles && !generating && (
            <div className="absolute right-0 top-0 flex items-center gap-1 text-[10px] text-blue-500">
              <Loader2 className="w-3 h-3 animate-spin" />
              <span>正在读取文件，请稍候...</span>
            </div>
          )}
        </div>
      </div>

      {/* Fields — expandable */}
      {expanded && (
        <div className="px-3 pb-3 grid grid-cols-2 gap-x-3 gap-y-1.5 border-t border-border pt-2">
          {FIELD_DEFS.map((def, idx) => (
            <div key={def.key} className="flex items-center gap-1.5">
              <span className="text-sm shrink-0 w-5 text-center">{def.icon}</span>
              <label className="text-xs text-muted-foreground shrink-0 w-8">
                {def.label}
              </label>
              <input
                type="text"
                value={fields[def.key]}
                onChange={(e) => updateField(def.key, e.target.value)}
                placeholder={def.placeholder}
                className="flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground/50 border-b border-transparent hover:border-border focus:border-primary transition-colors py-0.5 min-w-0"
              />
              {generated && fields[def.key] && (
                <span title="AI生成"><Sparkles className="w-3 h-3 text-amber-500 shrink-0" /></span>
              )}

            </div>
          ))}

        </div>
      )}

      {/* Footer — single row, left: media buttons, right: action buttons */}
      <div className="flex items-center gap-0.5 px-2 py-1.5">
        {/* Left — media & input buttons */}
        <button className="p-1.5 rounded hover:bg-muted/30 text-muted-foreground/40 hover:text-muted-foreground transition-colors" title="表情">
          <Smile className="w-4 h-4" />
        </button>
        <button className="p-1.5 rounded hover:bg-muted/30 text-muted-foreground/40 hover:text-muted-foreground transition-colors" title="收藏">
          <Star className="w-4 h-4" />
        </button>
        <button onClick={onFileClick} className="p-1.5 rounded hover:bg-muted/30 text-muted-foreground/40 hover:text-muted-foreground transition-colors" title="文件">
          <Paperclip className="w-4 h-4" />
        </button>
        <button
          onClick={onMicClick}
          className={`flex items-center gap-1 p-1.5 rounded transition-colors ${isRecording ? 'bg-red-500/15 text-red-500 hover:bg-red-500/25' : 'hover:bg-muted/30 text-muted-foreground/40 hover:text-muted-foreground'}`}
          title={isRecording ? '停止录音' : '语音输入'}
        >
          {isRecording ? <Square className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
          {isRecording && <span className="text-[10px] tabular-nums">{recordingSeconds}s</span>}
        </button>
        {/* ✨ Generate button */}
        {!expanded && (
          <button
            onClick={() => {
              setExpanded(true);
              if (fields.task.trim().length >= 5) autoGenerate(fields.task);
            }}
            className="p-1.5 rounded hover:bg-muted/30 text-muted-foreground/40 hover:text-muted-foreground transition-colors"
            title="展开AI字段"
          >
            <Sparkles className="w-4 h-4" />
          </button>
        )}
        {/* Regenerate button (only when expanded) */}
        {expanded && (
          <button
            onClick={() => {
              if (fields.task.trim().length >= 5) autoGenerate(fields.task);
            }}
            className="p-1.5 rounded bg-amber-500/10 text-amber-500 hover:bg-amber-500/20 transition-colors"
            title="重新生成扩展字段"
            disabled={generating}
          >
            <Sparkles className="w-4 h-4" />
          </button>
        )}
        {/* Collapse button (only when expanded) */}
        {expanded && (
          <button
            onClick={() => setExpanded(false)}
            className="p-1.5 rounded bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
            title="折叠字段"
          >
            <ChevronUp className="w-4 h-4" />
          </button>
        )}

        {/* Spacer */}
        <div className="flex-1" />

        {/* Send key mode dropdown */}
        <div className="relative">
          <button
            onClick={(e) => { e.stopPropagation(); setShowSendMenu(!showSendMenu); }}
            className="p-1 rounded hover:bg-muted/30 text-muted-foreground/40 hover:text-muted-foreground transition-colors"
            title="发送键设置"
          >
            <Settings2 className="w-4 h-4" />
          </button>
          {showSendMenu && (
            <div className="absolute bottom-full right-0 mb-1 bg-popover border border-border rounded-lg shadow-lg py-1 min-w-[180px] z-50">
              <button
                onClick={() => { setSendMode('enter'); localStorage.setItem('openmate-send-mode', 'enter'); setShowSendMenu(false); }}
                className="flex items-center gap-2 w-full px-3 py-1.5 text-sm text-left hover:bg-accent transition-colors"
              >
                <span className="w-4">{sendMode === 'enter' && <Check className="w-3.5 h-3.5" />}</span>
                按 Enter 键发送消息
              </button>
              <button
                onClick={() => { setSendMode('ctrl-enter'); localStorage.setItem('openmate-send-mode', 'ctrl-enter'); setShowSendMenu(false); }}
                className="flex items-center gap-2 w-full px-3 py-1.5 text-sm text-left hover:bg-accent transition-colors"
              >
                <span className="w-4">{sendMode === 'ctrl-enter' && <Check className="w-3.5 h-3.5" />}</span>
                按 Ctrl+Enter 键发送消息
              </button>
            </div>
          )}
        </div>

        {/* Right — action buttons from chat-client */}
        {footer}
      </div>

    </div>
  );
}
