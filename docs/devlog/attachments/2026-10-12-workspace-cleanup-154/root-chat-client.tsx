import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';

// ============================================================================
// Types
// ============================================================================

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
  codeModeResult?: CodeModeResult | null;
  isStreaming?: boolean;
  errorMessage?: string;
  showExecutionIndicator?: boolean;
}

interface CodeModeResult {
  ok: boolean;
  error?: string | ErrorInfo | null;
  partial_results?: unknown[];
  results?: unknown[];
  mode?: 'process' | 'worker' | 'thread' | string;
  execution_time_ms?: number;
  killed?: boolean;
  timeout?: boolean;
  worker_crashed?: boolean;
  stack_trace?: string;
  logs?: string[];
  batch_id?: string;
}

interface ErrorInfo {
  message: string;
  type?: string;
  code?: string;
  stack?: string;
  phase?: 'timeout' | 'kill' | 'worker_crash' | 'parse' | 'execution' | 'unknown';
}

interface ExecutionState {
  isLoading: boolean;
  startedAt: number;
  elapsedMs: number;
  messageId: string;
}

interface ChatClientProps {
  endpoint?: string;
  onSend?: (message: string) => Promise<ChatMessage | CodeModeResult | string>;
  initialMessages?: ChatMessage[];
  className?: string;
  executionTimeoutMs?: number;
  indicatorDelayMs?: number;
}

// ============================================================================
// Constants
// ============================================================================

const DEFAULT_INDICATOR_DELAY_MS = 5000;
const DEFAULT_EXECUTION_TIMEOUT_MS = 60000;
const TICK_INTERVAL_MS = 500;

const TIMEOUT_PARTIAL_PRESERVED_MSG = '批级超时已终止，部分结果已保留';
const WORKER_CRASH_MSG = 'Worker进程崩溃，部分结果已保留';
const KILL_MSG = '执行已被强制终止，部分结果已保留';

// ============================================================================
// Utility Functions
// ============================================================================

function generateId(): string {
  return `msg_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

/**
 * Normalize error into a displayable string message.
 * Never returns undefined — always a meaningful string.
 */
function normalizeErrorMessage(error: unknown): string {
  if (error === null || error === undefined) {
    return '未知错误：执行结果中未提供错误信息';
  }

  if (typeof error === 'string') {
    return error;
  }

  if (error instanceof Error) {
    return error.message || error.name || '未知异常';
  }

  if (typeof error === 'object') {
    const err = error as ErrorInfo;

    // Check for timeout phase
    if (err.phase === 'timeout') {
      return err.message?.includes(TIMEOUT_PARTIAL_PRESERVED_MSG)
        ? err.message
        : `${TIMEOUT_PARTIAL_PRESERVED_MSG}${err.message ? '：' + err.message : ''}`;
    }

    if (err.phase === 'kill') {
      return err.message?.includes(KILL_MSG)
        ? err.message
        : `${KILL_MSG}${err.message ? '：' + err.message : ''}`;
    }

    if (err.phase === 'worker_crash') {
      return err.message?.includes(WORKER_CRASH_MSG)
        ? err.message
        : `${WORKER_CRASH_MSG}${err.message ? '：' + err.message : ''}`;
    }

    if (err.message) {
      return err.message;
    }

    if (err.type) {
      return `[${err.type}] ${err.code || '未知错误'}`;
    }

    return JSON.stringify(error);
  }

  return String(error);
}

/**
 * Parse code_mode execution result into a normalized CodeModeResult.
 * Handles the {ok:false, error, partial_results} structure explicitly.
 */
function parseCodeModeResult(raw: unknown): CodeModeResult | null {
  if (!raw || typeof raw !== 'object') {
    return null;
  }

  const obj = raw as Record<string, unknown>;

  // Must be a code_mode result shape
  if ('ok' in obj && typeof obj.ok === 'boolean') {
    const result: CodeModeResult = {
      ok: obj.ok,
      error: obj.error as CodeModeResult['error'],
      partial_results: (obj.partial_results as unknown[]) ?? [],
      results: (obj.results as unknown[]) ?? [],
      mode: (obj.mode as string) ?? 'process',
      execution_time_ms: (obj.execution_time_ms as number) ?? 0,
      killed: (obj.killed as boolean) ?? false,
      timeout: (obj.timeout as boolean) ?? false,
      worker_crashed: (obj.worker_crashed as boolean) ?? false,
      stack_trace: (obj.stack_trace as string) ?? undefined,
      logs: (obj.logs as string[]) ?? [],
      batch_id: (obj.batch_id as string) ?? undefined,
    };

    // If ok is false but no error provided, synthesize one
    if (!result.ok && !result.error) {
      if (result.timeout) {
        result.error = {
          message: TIMEOUT_PARTIAL_PRESERVED_MSG,
          phase: 'timeout',
        };
      } else if (result.worker_crashed) {
        result.error = {
          message: WORKER_CRASH_MSG,
          phase: 'worker_crash',
        };
      } else if (result.killed) {
        result.error = {
          message: KILL_MSG,
          phase: 'kill',
        };
      } else {
        result.error = {
          message: '执行失败但未提供错误详情',
          phase: 'unknown',
        };
      }
    }

    // Ensure timeout errors always contain the required copy
    if (result.timeout && result.ok === false) {
      const currentMsg = normalizeErrorMessage(result.error);
      if (!currentMsg.includes(TIMEOUT_PARTIAL_PRESERVED_MSG)) {
        result.error = {
          message: `${TIMEOUT_PARTIAL_PRESERVED_MSG}：${currentMsg}`,
          phase: 'timeout',
          ...(typeof result.error === 'object' && result.error !== null ? result.error : {}),
        };
      }
    }

    return result;
  }

  return null;
}

/**
 * Render error message from code_mode result — ensures it's never silently swallowed.
 */
function renderCodeModeError(result: CodeModeResult): string {
  const parts: string[] = [];

  const errorMsg = normalizeErrorMessage(result.error);
  parts.push(`⚠️ 执行错误：${errorMsg}`);

  if (result.timeout) {
    parts.push(`⏱️ ${TIMEOUT_PARTIAL_PRESERVED_MSG}`);
  }

  if (result.worker_crashed) {
    parts.push(`💥 ${WORKER_CRASH_MSG}`);
  }

  if (result.killed && !result.timeout) {
    parts.push(`🛑 ${KILL_MSG}`);
  }

  const partialCount = result.partial_results?.length ?? 0;
  if (partialCount > 0) {
    parts.push(`📊 已保留 ${partialCount} 条部分结果`);
  }

  if (result.stack_trace) {
    parts.push(`\n堆栈信息：\n${result.stack_trace}`);
  }

  if (result.logs && result.logs.length > 0) {
    parts.push(`\n执行日志：\n${result.logs.join('\n')}`);
  }

  return parts.join('\n');
}

/**
 * Render successful code_mode results.
 */
function renderCodeModeSuccess(result: CodeModeResult): string {
  const results = result.results ?? [];
  const logs = result.logs ?? [];

  const parts: string[] = [];

  if (results.length > 0) {
    parts.push(`✅ 执行成功，返回 ${results.length} 条结果`);
    parts.push('```json');
    parts.push(JSON.stringify(results, null, 2));
    parts.push('```');
  } else {
    parts.push('✅ 执行成功，无返回结果');
  }

  if (logs.length > 0) {
    parts.push(`\n执行日志：\n${logs.join('\n')}`);
  }

  return parts.join('\n');
}

// ============================================================================
// Sub-components
// ============================================================================

const ExecutionIndicator: React.FC<{ elapsedMs: number }> = React.memo(({ elapsedMs }) => {
  const seconds = Math.floor(elapsedMs / 1000);
  return (
    <div
      className="code-mode-execution-indicator"
      role="status"
      aria-live="polite"
      aria-label="脚本执行中"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '10px 16px',
        margin: '8px 0',
        borderRadius: '8px',
        backgroundColor: 'rgba(255, 193, 7, 0.1)',
        border: '1px solid rgba(255, 193, 7, 0.3)',
        color: '#b8860b',
        fontSize: '14px',
        fontFamily: 'monospace',
      }}
    >
      <div
        className="spinner"
        aria-hidden="true"
        style={{
          width: '18px',
          height: '18px',
          border: '2px solid rgba(255, 193, 7, 0.3)',
          borderTop: '2px solid #b8860b',
          borderRadius: '50%',
          animation: 'spin 1s linear infinite',
          flexShrink: 0,
        }}
      />
      <span>脚本执行中...</span>
      <span style={{ opacity: 0.7, marginLeft: '4px' }}>{seconds}s</span>
    </div>
  );
});

const CodeModeResultBlock: React.FC<{ result: CodeModeResult; isErrorMessage: boolean }> = React.memo(
  ({ result, isErrorMessage }) => {
    const content = isErrorMessage ? renderCodeModeError(result) : renderCodeModeSuccess(result);
    const partialCount = result.partial_results?.length ?? 0;

    return (
      <div
        className={`code-mode-result-block ${isErrorMessage ? 'error' : 'success'}`}
        role={isErrorMessage ? 'alert' : 'status'}
        aria-live={isErrorMessage ? 'assertive' : 'polite'}
        style={{
          padding: '12px 16px',
          borderRadius: '8px',
          margin: '8px 0',
          fontSize: '14px',
          lineHeight: '1.6',
          border: isErrorMessage
            ? '1px solid rgba(220, 53, 69, 0.3)'
            : '1px solid rgba(40, 167, 69, 0.3)',
          backgroundColor: isErrorMessage
            ? 'rgba(220, 53, 69, 0.05)'
            : 'rgba(40, 167, 69, 0.05)',
          color: isErrorMessage ? '#721c24' : '#155724',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          fontFamily: 'monospace',
        }}
      >
        <div style={{ fontWeight: 'bold', marginBottom: '8px' }}>
          {isErrorMessage ? '🔴 Code Mode 执行失败' : '🟢 Code Mode 执行成功'}
          {result.mode && (
            <span style={{ fontWeight: 'normal', marginLeft: '8px', opacity: 0.7 }}>
              [{result.mode} 模式]
            </span>
          )}
          {result.execution_time_ms !== undefined && (
            <span style={{ fontWeight: 'normal', marginLeft: '8px', opacity: 0.7 }}>
              {result.execution_time_ms}ms
            </span>
          )}
        </div>

        <div>{content}</div>

        {isErrorMessage && partialCount > 0 && (
          <div style={{ marginTop: '12px', paddingTop: '8px', borderTop: '1px dashed rgba(114,28,36,0.2)' }}>
            <strong>部分结果 ({partialCount}条)：</strong>
            <pre
              style={{
                marginTop: '6px',
                padding: '8px',
                backgroundColor: 'rgba(0,0,0,0.05)',
                borderRadius: '4px',
                fontSize: '12px',
                maxHeight: '200px',
                overflow: 'auto',
              }}
            >
              {JSON.stringify(result.partial_results, null, 2)}
            </pre>
          </div>
        )}

        {result.batch_id && (
          <div style={{ marginTop: '8px', fontSize: '11px', opacity: 0.5 }}>
            批次ID: {result.batch_id}
          </div>
        )}
      </div>
    );
  }
);

const ErrorMessage: React.FC<{ message: string }> = React.memo(({ message }) => (
  <div
    className="chat-error-message"
    role="alert"
    aria-live="assertive"
    style={{
      padding: '10px 16px',
      borderRadius: '8px',
      margin: '8px 0',
      backgroundColor: 'rgba(220, 53, 69, 0.08)',
      border: '1px solid rgba(220, 53, 69, 0.25)',
      color: '#721c24',
      fontSize: '14px',
      whiteSpace: 'pre-wrap',
      wordBreak: 'break-word',
    }}
  >
    <span style={{ marginRight: '6px' }}>⚠️</span>
    {message}
  </div>
));

const MessageBubble: React.FC<{
  message: ChatMessage;
  showIndicator: boolean;
  elapsedMs: number;
}> = React.memo(({ message, showIndicator, elapsedMs }) => {
  const isUser = message.role === 'user';

  return (
    <div
      className={`chat-message ${message.role}`}
      data-message-id={message.id}
      style={{
        display: 'flex',
        justifyContent: isUser ? 'flex-end' : 'flex-start',
        marginBottom: '12px',
      }}
    >
      <div
        style={{
          maxWidth: '80%',
          padding: '12px 16px',
          borderRadius: isUser ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
          backgroundColor: isUser ? '#007bff' : '#f0f0f0',
          color: isUser ? '#fff' : '#333',
          fontSize: '14px',
          lineHeight: '1.6',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          boxShadow: '0 1px 2px rgba(0,0,0,0.08)',
        }}
      >
        {message.content}

        {message.errorMessage && <ErrorMessage message={message.errorMessage} />}

        {message.codeModeResult && (
          <CodeModeResultBlock
            result={message.codeModeResult}
            isErrorMessage={!message.codeModeResult.ok}
          />
        )}

        {showIndicator && <ExecutionIndicator elapsedMs={elapsedMs} />}

        <div
          style={{
            marginTop: '6px',
            fontSize: '11px',
            opacity: 0.5,
            textAlign: isUser ? 'right' : 'left',
          }}
        >
          {new Date(message.timestamp).toLocaleTimeString()}
        </div>
      </div>
    </div>
  );
});

// ============================================================================
// Main Component
// ============================================================================

export const ChatClient: React.FC<ChatClientProps> = ({
  endpoint = '/api/chat',
  onSend,
  initialMessages = [],
  className = '',
  executionTimeoutMs = DEFAULT_EXECUTION_TIMEOUT_MS,
  indicatorDelayMs = DEFAULT_INDICATOR_DELAY_MS,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [inputValue, setInputValue] = useState('');
  const [execution, setExecution] = useState<ExecutionState | null>(null);
  const [inputDisabled, setInputDisabled] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const executionStartRef = useRef<number>(0);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Timer for tracking execution elapsed time
  useEffect(() => {
    if (execution && execution.isLoading) {
      timerRef.current = setInterval(() => {
        setExecution((prev) => {
          if (!prev || !prev.isLoading) return prev;
          return {
            ...prev,
            elapsedMs: Date.now() - executionStartRef.current,
          };
        });
      }, TICK_INTERVAL_MS);

      return () => {
        if (timerRef.current) {
          clearInterval(timerRef.current);
          timerRef.current = null;
        }
      };
    }
    return undefined;
  }, [execution?.isLoading]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
      }
    };
  }, []);

  const appendMessage = useCallback((msg: ChatMessage) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const updateMessage = useCallback((messageId: string, updates: Partial<ChatMessage>) => {
    setMessages((prev) =>
      prev.map((m) => (m.id === messageId ? { ...m, ...updates } : m))
    );
  }, []);

  const stopExecutionTracking = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    setExecution(null);
    setInputDisabled(false);
  }, []);

  /**
   * Process a code_mode result and update the assistant message accordingly.
   * Errors are displayed in the chat flow — NEVER silently swallowed.
   */
  const processCodeModeResult = useCallback(
    (assistantMessageId: string, result: CodeModeResult) => {
      stopExecutionTracking();

      const codeModeResult = parseCodeModeResult(result) ??```tsx
        null;

      if (!codeModeResult) {
        // Result was not a code_mode shape — display as plain text
        updateMessage(assistantMessageId, {
          content: typeof result === 'string' ? result : JSON.stringify(result, null, 2),
          codeModeResult: null,
          errorMessage: undefined,
          showExecutionIndicator: false,
        });
        return;
      }

      if (!codeModeResult.ok) {
        // ═══════════════════════════════════════════════════════════
        // ERROR PATH — explicitly surface all error forms
        // ═══════════════════════════════════════════════════════════
        const errorDisplay = renderCodeModeError(codeModeResult);

        // Build user-facing message with required copy for timeout
        let displayContent: string;
        if (codeModeResult.timeout) {
          displayContent = `${TIMEOUT_PARTIAL_PRESERVED_MSG}\n\n${errorDisplay}`;
        } else if (codeModeResult.worker_crashed) {
          displayContent = `${WORKER_CRASH_MSG}\n\n${errorDisplay}`;
        } else if (codeModeResult.killed) {
          displayContent = `${KILL_MSG}\n\n${errorDisplay}`;
        } else {
          displayContent = errorDisplay;
        }

        updateMessage(assistantMessageId, {
          content: displayContent,
          codeModeResult: codeModeResult,
          errorMessage: normalizeErrorMessage(codeModeResult.error),
          showExecutionIndicator: false,
        });
      } else {
        // ═══════════════════════════════════════════════════════════
        // SUCCESS PATH
        // ═══════════════════════════════════════════════════════════
        updateMessage(assistantMessageId, {
          content: renderCodeModeSuccess(codeModeResult),
          codeModeResult: codeModeResult,
          errorMessage: undefined,
          showExecutionIndicator: false,
        });
      }
    },
    [stopExecutionTracking, updateMessage]
  );

  /**
   * Handle sending a message and processing the response.
   */
  const handleSend = useCallback(async () => {
    const trimmed = inputValue.trim();
    if (!trimmed || inputDisabled) return;

    const userMessage: ChatMessage = {
      id: generateId(),
      role: 'user',
      content: trimmed,
      timestamp: Date.now(),
    };

    const assistantMessageId = generateId();
    const assistantMessage: ChatMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      codeModeResult: null,
      showExecutionIndicator: false,
      isStreaming: true,
    };

    appendMessage(userMessage);
    appendMessage(assistantMessage);
    setInputValue('');
    setInputDisabled(true);

    // Start execution tracking
    executionStartRef.current = Date.now();
    setExecution({
      isLoading: true,
      startedAt: Date.now(),
      elapsedMs: 0,
      messageId: assistantMessageId,
    });

    // Schedule the execution indicator to appear after delay
    const indicatorTimer = setTimeout(() => {
      setExecution((prev) => {
        if (prev && prev.isLoading) {
          updateMessage(assistantMessageId, {
            showExecutionIndicator: true,
          });
        }
        return prev;
      });
    }, indicatorDelayMs);

    try {
      let response: ChatMessage | CodeModeResult | string;

      if (onSend) {
        response = await onSend(trimmed);
      } else {
        // Default fetch-based implementation
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), executionTimeoutMs);

        try {
          const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: trimmed }),
            signal: controller.signal,
          });

          clearTimeout(timeoutId);

          if (!res.ok) {
            throw new Error(`HTTP ${res.status}: ${res.statusText}`);
          }

          const contentType = res.headers.get('content-type');
          if (contentType?.includes('application/json')) {
            response = await res.json();
          } else {
            response = await res.text();
          }
        } catch (fetchError) {
          clearTimeout(timeoutId);
          if (fetchError instanceof DOMException && fetchError.name === 'AbortError') {
            // Construct a timeout-style code_mode result
            const timeoutResult: CodeModeResult = {
              ok: false,
              error: {
                message: TIMEOUT_PARTIAL_PRESERVED_MSG,
                phase: 'timeout',
              },
              timeout: true,
              partial_results: [],
              mode: 'process',
            };
            clearTimeout(indicatorTimer);
            processCodeModeResult(assistantMessageId, timeoutResult);
            return;
          }
          throw fetchError;
        }
      }

      clearTimeout(indicatorTimer);

      // Process response based on its shape
      const codeMode = parseCodeModeResult(response);
      if (codeMode) {
        processCodeModeResult(assistantMessageId, codeMode);
      } else if (typeof response === 'string') {
        stopExecutionTracking();
        updateMessage(assistantMessageId, {
          content: response,
          showExecutionIndicator: false,
          isStreaming: false,
        });
      } else if (response && typeof response === 'object' && 'content' in (response as ChatMessage)) {
        const msg = response as ChatMessage;
        // Check if the message contains embedded code_mode result
        const embeddedResult = parseCodeModeResult(msg.codeModeResult);
        if (embeddedResult) {
          processCodeModeResult(assistantMessageId, embeddedResult);
        } else {
          stopExecutionTracking();
          updateMessage(assistantMessageId, {
            content: msg.content,
            showExecutionIndicator: false,
            isStreaming: false,
            codeModeResult: msg.codeModeResult ?? null,
            errorMessage: msg.errorMessage,
          });
        }
      } else {
        stopExecutionTracking();
        updateMessage(assistantMessageId, {
          content: JSON.stringify(response, null, 2),
          showExecutionIndicator: false,
          isStreaming: false,
        });
      }
    } catch (err) {
      clearTimeout(indicatorTimer);
      stopExecutionTracking();

      const errMsg = normalizeErrorMessage(err);
      updateMessage(assistantMessageId, {
        content: '',
        errorMessage: `执行请求失败：${errMsg}`,
        showExecutionIndicator: false,
        isStreaming: false,
      });
    }
  }, [
    inputValue,
    inputDisabled,
    appendMessage,
    updateMessage,
    onSend,
    endpoint,
    executionTimeoutMs,
    indicatorDelayMs,
    processCodeModeResult,
    stopExecutionTracking,
  ]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const showIndicator = useMemo(
    () => (messageId: string) => {
      if (!execution || !execution.isLoading) return false;
      if (execution.messageId !== messageId) return false;
      return execution.elapsedMs >= indicatorDelayMs;
    },
    [execution, indicatorDelayMs]
  );

  const currentElapsedMs = execution?.elapsedMs ?? 0;

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div
      className={`chat-client-container ${className}`}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        maxWidth: '860px',
        margin: '0 auto',
        backgroundColor: '#fff',
        borderRadius: '12px',
        overflow: 'hidden',
        boxShadow: '0 2px 12px rgba(0,0,0,0.08)',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '16px 20px',
          borderBottom: '1px solid #eee',
          backgroundColor: '#fafafa',
          fontWeight: 600,
          fontSize: '16px',
          color: '#333',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}
      >
        <span>💬</span>
        <span>Chat Client</span>
        {execution?.isLoading && (
          <span
            style={{
              marginLeft: 'auto',
              fontSize: '12px',
              color: '#b8860b',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
            }}
          >
            <span
              style={{
                width: '8px',
                height: '8px',
                borderRadius: '50%',
                backgroundColor: '#ffc107',
                display: 'inline-block',
                animation: 'pulse 1.5s ease-in-out infinite',
              }}
            />
            执行中
          </span>
        )}
      </div>

      {/* Messages area */}
      <div
        className="chat-messages"
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '20px',
          backgroundColor: '#fafafa',
        }}
      >
        {messages.length === 0 && (
          <div
            style={{
              textAlign: 'center',
              color: '#999',
              marginTop: '40px',
              fontSize: '14px',
            }}
          >
            暂无消息，请输入内容开始对话
          </div>
        )}

        {messages.map((msg) => (
          <MessageBubble
            key={msg.id}
            message={msg}
            showIndicator={showIndicator(msg.id)}
            elapsedMs={currentElapsedMs}
          />
        ))}

        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div
        style={{
          display: 'flex',
          padding: '16px 20px',
          borderTop: '1px solid #eee',
          backgroundColor: '#fff',
          gap: '12px',
          alignItems: 'flex-end',
        }}
      >
        <textarea
          ref={inputRef}
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={inputDisabled}
          placeholder={inputDisabled ? '执行中，请稍候...' : '输入消息... (Enter 发送, Shift+Enter 换行)'}
          rows={2}
          style={{
            flex: 1,
            padding: '12px 16px',
            borderRadius: '12px',
            border: '1.5px solid #ddd',
            fontSize: '14px',
            lineHeight: '1.5',
            resize: 'none',
            outline: 'none',
            fontFamily: 'inherit',
            backgroundColor: inputDisabled ? '#f5f5f5' : '#fff',
            color: inputDisabled ? '#999' : '#333',
            transition: 'border-color 0.2s',
          }}
          onFocus={(e) => (e.target.style.borderColor = '#007bff')}
          onBlur={(e) => (e.target.style.borderColor = '#ddd')}
        />
        <button
          onClick={handleSend}
          disabled={inputDisabled || !inputValue.trim()}
          style={{
            padding: '12px 24px',
            borderRadius: '12px',
            border: 'none',
            backgroundColor: inputDisabled || !inputValue.trim() ? '#ccc' : '#007bff',
            color: '#fff',
            fontSize: '14px',
            fontWeight: 600,
            cursor: inputDisabled || !inputValue.trim() ? 'not-allowed' : 'pointer',
            transition: 'background-color 0.2s',
            whiteSpace: 'nowrap',
          }}
        >
          发送
        </button>
      </div>

      {/* Global styles injected for animations */}
      <style>{`
        @keyframes spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        .chat-messages::-webkit-scrollbar {
          width: 6px;
        }
        .chat-messages::-webkit-scrollbar-track {
          background: transparent;
        }
        .chat-messages::-webkit-scrollbar-thumb {
          background: #ddd;
          border-radius: 3px;
        }
        .chat-messages::-webkit-scrollbar-thumb:hover {
          background: #bbb;
        }
      `}</style>
    </div>
  );
};

export default ChatClient;