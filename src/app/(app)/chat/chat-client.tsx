'use client';
import MarkdownContent from "@/components/markdown-content";
import { SOULMATE_LOGO, SOULMATE_ICON } from "@/lib/agent-brand";
import { SecretScanBadge } from "@/components/secret-scan-badge";
import { MemoryMarkerBadge, type MemoryMarker } from "@/components/memory-marker-badge";
import { MultiFileDiff, type FileChange } from "@/components/multi-file-diff";
import { TaskChoiceMenu, type ChoiceOption, type ChoiceQuestion } from "@/components/task-choice-menu";
import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useAppStore } from '@/stores/app-store';
import { Send, Bot, User, Loader2, Paperclip, X, Wifi, WifiOff, FileText, Image as ImageIcon, Info, ChevronDown, ChevronLeft, ChevronRight, Plus, Bookmark, RotateCcw, Zap, Brain, PanelLeft, Copy, ThumbsUp, ThumbsDown, Share2, RefreshCw, MoreHorizontal, Volume2, MessageSquare, FolderOpen, Video, Music, Link2, CalendarDays, LocateFixed, Mic, Square } from "lucide-react";
import { SkirtTabs } from "@opensoulmate/openface";
import { ContextRing } from "@/components/context-ring";
import { getApiBaseUrl, getToken, getUserId } from '@/lib/api-client';
import { copyToClipboard } from '@/lib/clipboard';
import { Dialog } from '@/components/ui/dialog';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { useIsMobile } from '@/hooks/use-mobile';
import { useSidebar } from '@/components/ui/sidebar';
import { useTranslation } from 'react-i18next';
import { SmartPrompt } from '@/components/smart-prompt';
import { AcpApprovalModal, type AcpApprovalRequest } from '@/components/acp-approval-modal';
import { NetworkWaitBanner } from '@/components/network-wait-banner';
import { buildUserMessage } from './user-message';
import { pickRecorderMimeType, blobToAudioAttachment } from '@/lib/audio-record';

const getApiUrl = () => getApiBaseUrl();
const getWsUrl = () => getApiUrl().replace('http', 'ws');

// Tag a session with its owning agent via OpenSoul API
// This persists server-side, works across devices/browsers
async function tagSessionAgent(sessionId: string, agentId: string): Promise<void> {
  try {
    await fetch(`${getApiUrl()}/api/sessions/${sessionId}/tags`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tag_name: `agent:${agentId}` }),
    });
  } catch (e) {
    console.warn('[session-tag] Failed to tag session:', e);
  }
}

const getAcpProxyUrl = () => {
  // ACP Proxy runs on port 8092, same hostname
  return `http://${window.location.hostname}:8092`;
};
const getAcpWsUrl = () => getAcpProxyUrl().replace('http', 'ws');

interface MessagePart { type: string; text?: string; data?: string; name?: string; mime_type?: string; url?: string; choices?: ChoiceOption[]; questions?: ChoiceQuestion[]; }
interface TokenUsage { input: number; output: number; }

/** 思考过程块 —— 用于存储 Agent 推理链/内心独白的流式文本 */
interface ThinkingBlock {
  id: string;        // 唯一标识（使用时间戳 + 随机后缀）
  text: string;      // 累积的思考文本内容
  isComplete: boolean; // 是否已完成接收（false = 仍在流式接收中）
}

/** 工具调用信息 —— 记录 Agent 调用外部工具的完整生命周期 */
interface ToolCallInfo {
  toolCallId: string;   // 唯一标识（来自 ACP 协议的 toolCallId）
  toolName: string;     // 工具名称（如 web_search、read_file）
  serverName?: string;  // MCP 服务器名称（可选）
  state: 'running' | 'completed' | 'failed'; // 工具调用状态
  args?: string;        // 调用参数（JSON 字符串）
  content?: string;     // 调用结果内容
}

interface Checkpoint { id: string; messageId: string; timestamp: Date; messages: Message[]; label: string; }

/** 安全处理消息列表，避免undefined错误 */
const useSafeMessages = (messages: any[] | undefined) => {
  return useMemo(() => {
    if (!messages || !Array.isArray(messages)) {
      return [];
    }
    return messages;
  }, [messages]);
};

/** 校验附件是否有效 */
const validateAttachments = (files: any[]) => {
  if (!files || !Array.isArray(files)) {
    return false;
  }
  return files.some(file => {
    if (!file) return false;
    // 检查文件大小（如果是文件对象）
    if (file.size !== undefined && file.size === 0) return false;
    // 检查文件数据（如果是附件数据）
    if (file.data && typeof file.data === 'string' && file.data.length === 0) return false;
    return true;
  });
}
type AgentMode = 'plan' | 'act';
interface Message {
  id: string;
  role: 'user' | 'agent';
  parts: MessagePart[];
  timestamp: Date;
  source?: string;
  fileChanges?: FileChange[];
  tokenUsage?: TokenUsage;
  thinking?: ThinkingBlock[]; // 思考过程块列表（新增：Agent 推理链可见性）
  toolCalls?: ToolCallInfo[]; // 工具调用记录列表（新增：工具调用过程可见性）
  memoryMarker?: MemoryMarker | null; // kilocode #9记忆marker（"本回复用了记忆"badge数据源）
}
interface Session { id: string; name?: string; title?: string; platform: string; chat_id?: string; last_message?: string; unread?: number; workspace?: string; last_active?: string; updated_at?: string; created_at?: string; message_count?: number; source?: string; }

// Multi-session data: each session has its own messages and unread state
interface SessionData {
  messages: Message[];
  unreadCount: number;
  lastMessage?: string;
  promptFields?: { task: string; role: string; background: string; constraints: string; format: string };
  loading?: boolean;
}

// Agent definitions with detection
interface SourceGroup {
  source: string;
  label: string;
  icon: string;
  sessions: Session[];
  expanded: boolean;
}

interface AgentInfo {
  id: string;
  name: string;
  icon: string; logo?: string;
  description: string;
  installed: boolean;
  available?: boolean;
  category?: string;
  path?: string;
  sessions: Session[];
  expanded: boolean;
  sourceGroups?: SourceGroup[];
}

// Source metadata for sub-group display (labels are i18n keys)
const SOURCE_META: Record<string, { labelKey: string; icon: string }> = {
  cli:    { labelKey: 'sessions.sourceCli',  icon: '⌨️' },
  weixin: { labelKey: 'sessions.sourceWeixin', icon: '💬' },
  cron:   { labelKey: 'sessions.sourceCron', icon: '⏰' },
  acp:    { labelKey: 'sessions.sourceAcp', icon: '🔗' },
  tui:    { labelKey: 'sessions.sourceTui', icon: '🖥️' },
  tool:   { labelKey: 'sessions.sourceTool', icon: '🔧' },
  subagent: { labelKey: 'sessions.sourceSubagent', icon: '🤖' },
  hermes: { labelKey: 'sessions.sourceAcp', icon: '🔗' },
};

// Known agent icons (fallback for detect API)
const AGENT_ICONS: Record<string, string> = {
  hermes: '🏛️', claude: '🟣', codex: '🟢', gemini: '🔵', mimo: '📱',
  opencode: '⚡', aider: '🤝', copilot: '🐙', cursor: '▶️', windsurf: '🏄',
  cline: '🔧', continue: '🔄', deepseek: '🐋', qwen: '🟠', 'amazon-q': '☁️',
  ollama: '🦙', wechat: '💬', telegram: '✈️', openclaw: '🦞',
};

// Parse file changes from AI response content
function parseFileChanges(content: string): FileChange[] {
  const files: FileChange[] = [];
  // Match patterns like: --- a/file.ts +++ b/file.ts or *** file.ts
  const fileRegex = /(?:---\s+a\/(.+?)\s*\n\+\+\+\s+b\/(.+?)|(\*\*\*\s+.+?))\s*\n([\s\S]*?)(?=---\s+a\/|\*\*\*|$)/g;
  let match;
  while ((match = fileRegex.exec(content)) !== null) {
    const path = match[1] || match[3]?.replace(/^\*\*\*\s+/, '') || 'unknown';
    const diff = match[4] || '';
    
    // Extract language from file extension
    const ext = path.split('.').pop()?.toLowerCase() || '';
    const langMap: Record<string, string> = {
      ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
      py: 'python', rb: 'ruby', go: 'go', rs: 'rust', java: 'java',
      cpp: 'cpp', c: 'c', cs: 'csharp', php: 'php', swift: 'swift',
      kt: 'kotlin', scala: 'scala', sh: 'bash', bash: 'bash',
      html: 'html', css: 'css', scss: 'scss', less: 'less',
      json: 'json', yaml: 'yaml', yml: 'yaml', xml: 'xml',
      md: 'markdown', sql: 'sql', dockerfile: 'dockerfile',
    };
    const language = langMap[ext] || ext;

    // Parse diff content to extract original and modified
    const lines = diff.split('\n');
    let original = '';
    let modified = '';
    let status: FileChange['status'] = 'modified';

    for (const line of lines) {
      if (line.startsWith('-') && !line.startsWith('---')) {
        original += line.substring(1) + '\n';
      } else if (line.startsWith('+') && !line.startsWith('+++')) {
        modified += line.substring(1) + '\n';
      } else if (line.startsWith(' ')) {
        original += line.substring(1) + '\n';
        modified += line.substring(1) + '\n';
      }
    }

    // Determine status
    if (original.trim() === '' && modified.trim() !== '') {
      status = 'added';
    } else if (modified.trim() === '' && original.trim() !== '') {
      status = 'deleted';
    }

    files.push({
      path,
      language,
      original: original.trimEnd(),
      modified: modified.trimEnd(),
      status,
    });
  }

  // Also try to parse code blocks with filename comments
  const codeBlockRegex = /```(\w+)?\s*(?:\/\/\s*(.+?))?\n([\s\S]*?)```/g;
  while ((match = codeBlockRegex.exec(content)) !== null) {
    const language = match[1] || 'text';
    const fileName = match[2]?.trim();
    const code = match[3]?.trim() || '';
    
    if (fileName && code) {
      // Check if this file is already in the list
      const existing = files.find(f => f.path === fileName);
      if (!existing) {
        files.push({
          path: fileName,
          language,
          original: '',
          modified: code,
          status: 'added',
        });
      }
    }
  }

  return files;
}

// Cost calculation (approximate pricing per 1K tokens)
const calculateCost = (usage: TokenUsage): number => {
  const inputCost = (usage.input / 1000) * 0.01;
  const outputCost = (usage.output / 1000) * 0.03;
  return inputCost + outputCost;
};

// Simulate token usage for demo purposes
const simulateTokenUsage = (content: string): TokenUsage => {
  const words = content.split(/\s+/).length;
  const inputTokens = Math.floor(words * 1.3);
  const outputTokens = Math.floor(words * 1.5);
  return { input: inputTokens, output: outputTokens };
};

// ── useAcpWebSocket hook（ACP JSON-RPC 2.0 协议，多会话版）────────────────
function useAcpWebSocket(params: {
  selectedAgent: AgentInfo | null;
  selectedSession: Session | null;
  selectedSessionRef: React.MutableRefObject<Session | null>;
  // eslint-disable-next-line @typescript-eslint/no-unsafe-function-type
  t: Function;
  updateSessionMessages: (sessionId: string, updater: (prev: Message[]) => Message[]) => void;
  incrementUnread: (sessionId: string, lastMsg: string) => void;
  setLoading: React.Dispatch<React.SetStateAction<boolean>>;
  setSelectedSession: React.Dispatch<React.SetStateAction<Session | null>>;
  activeAgentIdFromStore: string | null;
  migrateSessionId: (oldId: string, newId: string) => void;
  getSessionMessages: (sessionId: string) => Message[];
}) {
  const { selectedAgent, selectedSession, selectedSessionRef, t, updateSessionMessages, incrementUnread, setLoading, setSelectedSession, activeAgentIdFromStore, migrateSessionId, getSessionMessages } = params;

  // Multi-session: Map<sessionId, WebSocket> for concurrent connections
  const wsMapRef = useRef<Map<string, WebSocket>>(new Map());

  // Per-session ACP state
  const sessionStateMapRef = useRef<Map<string, {
    acpSessionId: string | null;
    rpcId: number;
    pendingRequests: Map<number, { resolve: (result: unknown) => void; reject: (err: Error) => void }>;
    acpReady: Promise<void> | null;
    resolveAcpReady: (() => void) | null;
    connectedToken: string | null;
    reconnectTimer: ReturnType<typeof setTimeout> | null;
    unmounted: boolean;
    retryDelay: number;
    ws: WebSocket | null;
    promptRpcId: number | null; // 记录 session/prompt 的 RPC id
    steerRpcIds: Set<number>; // P1插话：运行中消息的RPC id集合（其响应≠任务完成）
  }>>(new Map());
  const [wsConnected, setWsConnected] = useState(false);
  const streamingSessionIdRef = useRef<string | null>(null);
  // ACP审批弹窗状态
  const [approvalRequest, setApprovalRequest] = useState<AcpApprovalRequest | null>(null);
  // P0-3: ACP v1.0标准 session/request_permission 的JSON-RPC请求上下文（agent→client请求-响应）
  const approvalRpcRef = useRef<{
    id: string | number;
    ws: WebSocket;
    allowOptionId: string;
  } | null>(null);

  // Get or create per-session state
  const getSessionState = useCallback((sessionId: string) => {
    let state = sessionStateMapRef.current.get(sessionId);
    if (!state) {
      state = {
        acpSessionId: null,
        rpcId: 0,
        pendingRequests: new Map(),
        acpReady: null,
        resolveAcpReady: null,
        connectedToken: null,
        reconnectTimer: null,
        unmounted: false,
        retryDelay: 1000,
        ws: null as WebSocket | null,
        promptRpcId: null, // 记录 session/prompt 的 RPC id
        steerRpcIds: new Set<number>(), // P1插话：运行中消息的RPC id（响应只代表已排队）
      };
      sessionStateMapRef.current.set(sessionId, state);
    }
    return state;
  }, []);

  // 发送用户消息到指定会话的 ACP 连接（支持多模态：文本+图片+文件）
  // opts.steer=true：任务运行中的插话（Khoj interrupt_queue/goose Steer），RPC响应不触发完成
  const sendAcpPrompt = useCallback(async (sessionId: string, text: string, attachments?: MessagePart[], opts?: { steer?: boolean }) => {
    const state = getSessionState(sessionId);
    // 等待 ACP 握手完成
    if (state.acpReady) await state.acpReady;
    const sid = state.acpSessionId;
    const ws = wsMapRef.current.get(sessionId) || state.ws;
    if (!sid || !ws || ws.readyState !== WebSocket.OPEN) return;
    const id = ++state.rpcId;
    const messageId = `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    // 记录 prompt RPC id，响应回来时知道是 prompt 完成
    if (opts?.steer) {
      state.steerRpcIds.add(id);
    } else {
      state.promptRpcId = id;
    }
    state.pendingRequests.set(id, { resolve: () => {}, reject: () => {} });
    // 构建多模态 prompt 内容（文本 + 图片 + 文件）
    const promptParts: Array<Record<string, unknown>> = [{ type: 'text', text }];
    if (attachments && attachments.length > 0) {
      for (const att of attachments) {
        if (att.type === 'image' && att.data) {
          promptParts.push({ type: 'image', data: att.data, mimeType: att.mime_type || 'image/png' });
        }
        if (att.type === 'audio' && att.data) {
          // ACP AudioContentBlock — 语音输入（17.2）：ws_acp 纯透传，后端 attach_audio_parts 直发 MiMo input_audio
          promptParts.push({ type: 'audio', data: att.data, mimeType: att.mime_type || 'audio/wav' });
        }
        if (att.type === 'file' && att.data) {
          // ACP v1.0标准 EmbeddedResourceContentBlock — type:'file'不是合法schema，SDK解析会报ValidationError
          promptParts.push({ type: 'resource', resource: { uri: `file:///${att.name || 'file'}`, mimeType: att.mime_type || 'application/octet-stream', blob: att.data } });
        }
      }
    }
    ws.send(JSON.stringify({
      jsonrpc: '2.0',
      id,
      method: 'session/prompt',
      params: { sessionId: sid, messageId, prompt: promptParts },
    }));
    // 不再用 setTimeout 删除 pending——等响应回来再处理
  }, [getSessionState]);

  // ── Common message handlers (shared by session.event and session/update) ──
  // Handle incremental agent message chunk: append to streaming message or create new one
  const handleAgentChunk = useCallback((targetSessionId: string, text: string) => {
    // 检测 choice 类型的结构化消息
    try {
      const parsed = JSON.parse(text);
      if (parsed.type === 'choice' && (parsed.choices || parsed.questions)) {
        updateSessionMessages(targetSessionId, prev => {
          return [...prev, {
            id: Date.now().toString(),
            role: 'agent',
            parts: [{ type: 'choice', text: parsed.text, choices: parsed.choices, questions: parsed.questions }],
            timestamp: new Date(),
          }];
        });
        return;
      }
    } catch { /* not JSON, normal text */ }

    updateSessionMessages(targetSessionId, prev => {
      const last = prev[prev.length - 1];
      if (last?.role === 'agent' && last?.source === 'streaming') {
        // Append to existing streaming message
        return [...prev.slice(0, -1), { ...last, parts: [{ type: 'text', text: (last.parts[0]?.text || '') + text }] }];
      }
      // Create new streaming message
      return [...prev, { id: Date.now().toString(), role: 'agent', parts: [{ type: 'text', text }], timestamp: new Date(), source: 'streaming' }];
    });
  }, [updateSessionMessages]);

  // ── Agent 思考过程处理 (agent_thought_chunk / agent_thought) ──
  // 累积思考文本到当前流式消息的 thinking 字段；如果消息不存在则先创建
  const handleAgentThoughtChunk = useCallback((targetSessionId: string, text: string) => {
    updateSessionMessages(targetSessionId, prev => {
      const last = prev[prev.length - 1];
      if (last?.role === 'agent' && last?.source === 'streaming') {
        // 找到最后一个未完成的 thinking block，累积文本
        const thinking = [...(last.thinking || [])];
        const lastBlock = thinking[thinking.length - 1];
        if (lastBlock && !lastBlock.isComplete) {
          // 追加到现有的未完成思考块
          lastBlock.text += text;
        } else {
          // 创建新的思考块（时间戳 + 随机后缀保证唯一性）
          thinking.push({ id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, text, isComplete: false });
        }
        return [...prev.slice(0, -1), { ...last, thinking }];
      }
      // 如果还没有流式消息，先创建一个（思考块先于回复块到达时）
      const newThinking: ThinkingBlock = { id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, text, isComplete: false };
      return [...prev, { id: Date.now().toString(), role: 'agent', parts: [{ type: 'text', text: '' }], timestamp: new Date(), source: 'streaming', thinking: [newThinking] }];
    });
  }, [updateSessionMessages]);

  // 标记当前所有未完成的 thinking block 为已完成
  const handleAgentThoughtComplete = useCallback((targetSessionId: string) => {
    updateSessionMessages(targetSessionId, prev => {
      const last = prev[prev.length - 1];
      if (last?.role === 'agent' && last.thinking?.some(t => !t.isComplete)) {
        // 将所有未完成的思考块标记为完成
        const thinking = last.thinking.map(t => t.isComplete ? t : { ...t, isComplete: true });
        return [...prev.slice(0, -1), { ...last, thinking }];
      }
      return prev;
    });
  }, [updateSessionMessages]);

  // ── 工具调用处理 (tool_call / tool_call_update) ──
  // 创建新的工具调用记录，或更新已有记录的状态
  const handleToolCall = useCallback((targetSessionId: string, toolData: { toolCallId?: string; name?: string; arguments?: string; serverName?: string }) => {
    updateSessionMessages(targetSessionId, prev => {
      const last = prev[prev.length - 1];
      if (last?.role === 'agent' && last?.source === 'streaming') {
        const toolCalls = [...(last.toolCalls || [])];
        const callId = toolData.toolCallId || `tc-${Date.now()}`;
        // 检查是否已存在相同 ID 或同名同参数的工具调用（避免重复）
        const existing = toolCalls.find(tc => tc.toolCallId === callId || (tc.toolName === (toolData.name || 'unknown') && tc.args === toolData.arguments));
        if (!existing) {
          toolCalls.push({
            toolCallId: callId,
            toolName: toolData.name || 'unknown',
            serverName: toolData.serverName,
            state: 'running',
            args: toolData.arguments,
          });
        }
        return [...prev.slice(0, -1), { ...last, toolCalls }];
      }
      return prev;
    });
  }, [updateSessionMessages]);

  // 更新工具调用进度/结果（tool_call_update）
  const handleToolCallUpdate = useCallback((targetSessionId: string, update: { toolCallId?: string; state?: string; content?: string }) => {
    updateSessionMessages(targetSessionId, prev => {
      const last = prev[prev.length - 1];
      if (last?.role === 'agent' && last.toolCalls?.length) {
        const toolCalls = last.toolCalls.map(tc => {
          if (tc.toolCallId === update.toolCallId) {
            // 映射 ACP 状态到内部状态
            const stateMap: Record<string, ToolCallInfo['state']> = {
              running: 'running', completed: 'completed', done: 'completed',
              failed: 'failed', error: 'failed',
            };
            return {
              ...tc,
              state: stateMap[update.state || ''] || tc.state,
              content: update.content || tc.content,
            };
          }
          return tc;
        });
        return [...prev.slice(0, -1), { ...last, toolCalls }];
      }
      return prev;
    });
  }, [updateSessionMessages]);

  // Finalize a completed session: clear streaming flag, mark thinking complete, parse file changes, record spending
  const handleSessionComplete = useCallback((completedSessionId: string) => {
    setLoading(false);
    streamingSessionIdRef.current = null;
    updateSessionMessages(completedSessionId, prev => {
      const last = prev[prev.length - 1];
      if (last?.role === 'agent' && last?.source === 'streaming') {
        const content = last.parts[0]?.text || '';
        const fileChanges = parseFileChanges(content);
        const tokenUsage = simulateTokenUsage(content);
        if (tokenUsage) {
          const sid = completedSessionId || 'default';
          setTimeout(() => {
            useAppStore.getState().addSessionSpending(sid, {
              input: tokenUsage.input,
              output: tokenUsage.output,
              cost: calculateCost(tokenUsage),
            });
          }, 0);
        }
        // 会话完成时：标记所有思考块为已完成，清除流式标记
        const thinking = last.thinking?.map(t => t.isComplete ? t : { ...t, isComplete: true });
        // 工具调用：将仍在 running 的标记为 completed（兜底）
        const toolCalls = last.toolCalls?.map(tc => tc.state === 'running' ? { ...tc, state: 'completed' as const } : tc);
        return [...prev.slice(0, -1), { ...last, source: undefined, fileChanges, tokenUsage, thinking, toolCalls }];
      }
      return prev;
    });
  }, [updateSessionMessages, setLoading]);

  // Migrate a temp session to a real session ID (update store + sidebar + tag agent)
  const handleTempSessionMigration = useCallback((newSessionId: string, currentSelectedAgentId: string) => {
    const curId = selectedSessionRef.current?.id;
    const isTemp = !curId || curId.startsWith('temp-');
    if (isTemp) {
      // temp→real 迁移完成，清除新建流程标记
      useAppStore.setState({ _isNewSessionFlow: false });
      tagSessionAgent(newSessionId, currentSelectedAgentId);
      // 直接从sessionDataMap读消息（同步），提取自动命名
      const msgs = getSessionMessages(newSessionId);
      const firstUserMsg = msgs.find((m: { role: string }) => m.role === 'user');
      const autoName = firstUserMsg?.parts?.find((p: { type: string; text?: string }) => p.type === 'text')?.text?.slice(0, 20) || '';
      const displayName = autoName || '新会话';
      // 更新selectedSession和store
      const updated = { id: newSessionId, name: displayName, platform: 'hermes' } as Session;
      setSelectedSession(updated);
      selectedSessionRef.current = updated;
      useAppStore.getState().setActiveSession(newSessionId, null, { sessionName: displayName });
      // PATCH到后端，完成后再刷新sidebar
      if (autoName) {
        fetch(`${getApiBaseUrl()}/api/sessions/${newSessionId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: autoName }),
        }).then(() => useAppStore.getState().refreshSidebar()).catch(() => useAppStore.getState().refreshSidebar());
      } else {
        useAppStore.getState().refreshSidebar();
      }
    } else {
      useAppStore.getState().refreshSidebar();
    }
  }, [setSelectedSession, getSessionMessages]);

  // Connect a single session with its own WebSocket
  const connectSession = useCallback((sessionId: string, agentId: string, sessionName?: string) => {
    // 调试日志：追踪谁在调用 connectSession
    console.log(`[ACP] connectSession called: sessionId=${sessionId}, agentId=${agentId}`);
    // 防止无效 sessionId 创建无用 WebSocket 连接
    if (!sessionId || sessionId === 'unknown') {
      console.warn(`[ACP] connectSession 拒绝: 无效 sessionId="${sessionId}"`);
      return;
    }
    const existingWs = wsMapRef.current.get(sessionId);
    console.debug(`[ACP-DEBUG] connectSession(${sessionId}) existing=${existingWs ? existingWs.readyState : 'none'}`);
    if (existingWs && existingWs.readyState === WebSocket.OPEN) return; // 已连接
    if (existingWs) { existingWs.close(); wsMapRef.current.delete(sessionId); } // 清理已关闭的WS

    const state = getSessionState(sessionId);
    state.unmounted = false;

    const isTokenExpired = (t: string): boolean => {
      try {
        const payload = JSON.parse(atob(t.split('.')[1]));
        return payload.exp ? payload.exp * 1000 < Date.now() : false;
      } catch { return true; }
    };

    const sendRpcRequest = (ws: WebSocket, method: string, params: Record<string, unknown> = {}): Promise<unknown> => {
      return new Promise((resolve, reject) => {
        if (ws.readyState !== WebSocket.OPEN) {
          reject(new Error('WebSocket 未连接'));
          return;
        }
        const id = ++state.rpcId;
        state.pendingRequests.set(id, { resolve, reject });
        ws.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }));
        setTimeout(() => {
          if (state.pendingRequests.has(id)) {
            state.pendingRequests.delete(id);
            reject(new Error(`RPC 请求 ${method} 超时`));
          }
        }, 30000);
      });
    };

    const performHandshake = async (ws: WebSocket) => {
      try {
        await sendRpcRequest(ws, 'initialize', { protocolVersion: 1 });
        // 优先使用已有的ACP session ID（重连时避免创建重复会话）；temp键也带上，服务端按键幂等去重
        const existingAcpSid = state.acpSessionId || (sessionId.startsWith('temp-') ? undefined : sessionId);
        const result = await sendRpcRequest(ws, 'session/new', { cwd: '/', mcpServers: [], agent_id: agentId, _meta: { session_id: existingAcpSid || sessionId } }) as { session_id?: string; sessionId?: string };
        const acpSid = result?.session_id || result?.sessionId;
        if (acpSid) {
          state.acpSessionId = acpSid;
          // Migrate temp session ID to real session ID
          if (sessionId.startsWith('temp-')) {
            // 迁移 ACP 状态和 WebSocket 到真实 sessionId
            const acpState = sessionStateMapRef.current.get(sessionId);
            if (acpState) {
              sessionStateMapRef.current.set(acpSid, acpState);
              // 不删除旧state — onclose重连闭包引用原始sessionId，删了会导致重连时acpSessionId=null
            }
            const wsEntry = wsMapRef.current.get(sessionId);
            if (wsEntry) {
              wsMapRef.current.set(acpSid, wsEntry);
              // 不删除旧wsMap条目 — 同上，保持重连能力
            }
            migrateSessionId(sessionId, acpSid);
            // Update selectedSession with real ID, preserve session name
            const updated = { id: acpSid, name: sessionName || selectedSessionRef.current?.name || '', platform: 'hermes' } as Session;
            setSelectedSession(updated);
            selectedSessionRef.current = updated;
            try {
              const apiBase = getApiBaseUrl();
              await fetch(`${apiBase}/api/sessions`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
                body: JSON.stringify({ id: acpSid, name: sessionName || `${agentId} 会话`, agent_id: agentId, tags: [`agent:${agentId}`] }),
              });
            } catch (saveErr) {
              console.warn('[ACP] 保存session到OpenSoul失败:', saveErr);
            }
            // 注册行已落库，此刻再刷新侧边栏（此前 refreshSidebar 发生在注册前，
            // 拉列表时行还不存在 → 新会话不刷新页面永远不显示）
            useAppStore.getState().refreshSidebar();
          }
        }
        state.resolveAcpReady?.();
      } catch (e) {
        console.error('[ACP] 握手失败:', e);
        state.resolveAcpReady?.();
      }
    };

    const connect = () => {
      if (state.unmounted) return;
      console.debug(`[ACP-DEBUG] effect-connect(${sessionId}) mapEntry=${wsMapRef.current.get(sessionId) ? wsMapRef.current.get(sessionId)!.readyState : 'none'}`);
      const currentToken = getToken();
      if (!currentToken) {
        console.warn('[ACP] 无token，跳转登录页');
        window.location.href = '/login';
        return;
      }
      if (isTokenExpired(currentToken)) {
        console.warn('[ACP] Token已过期，跳转登录页');
        localStorage.removeItem('openmate-token');
        window.location.href = '/login';
        return;
      }

      const wsUrl = `${getAcpWsUrl()}/ws/acp?token=${currentToken}&sessionId=${sessionId}`;
      const ws = new WebSocket(wsUrl);
      wsMapRef.current.set(sessionId, ws);
      state.ws = ws;
      state.connectedToken = currentToken;

      ws.onopen = () => {
        setWsConnected(true);
        state.retryDelay = 1000;
        state.acpReady = new Promise<void>(resolve => { state.resolveAcpReady = resolve; });
        performHandshake(ws);
      };

      ws.onclose = (event) => {
        // 保留 state.acpSessionId 供重连时复用，避免创建重复会话
        state.resolveAcpReady?.();
        state.acpReady = null;
        for (const [id, { reject }] of state.pendingRequests) {
          reject(new Error('WebSocket连接已断开'));
        }
        state.pendingRequests.clear();

        // 服务端主动关闭(1000)且无token才跳登录；有token就重连
        if (event.code === 1000 && !state.unmounted) {
          const storedToken = localStorage.getItem('openmate-token');
          if (!storedToken) {
            console.warn('[ACP] 连接被服务端关闭，无token，跳转登录');
            window.location.href = '/login';
            return;
          }
          // 有token，可能是服务端重启，直接重连
          console.warn('[ACP] 连接被服务端关闭，有token，尝试重连');
        }
        wsMapRef.current.delete(sessionId);
        // WebSocket断开时重置loading状态，否则后续消息被 if(loading) return 拦截
        setLoading(false);
        if (!state.unmounted) {
          state.reconnectTimer = setTimeout(connect, state.retryDelay);
        }
        state.retryDelay = Math.min(state.retryDelay * 2, 30000);
        // Update overall connected state
        setWsConnected(wsMapRef.current.size > 0);
      };

      ws.onerror = () => { setWsConnected(false); };

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);

          // Token invalid detection
          if (data.id === null && data.error) {
            const errMsg = (data.error as { message?: string })?.message || '';
            if (errMsg.includes('token') || errMsg.includes('Token') || errMsg.includes('expired') || errMsg.includes('Invalid')) {
              console.warn('[ACP] Token无效，跳转登录页');
              localStorage.removeItem('openmate-token');
              state.unmounted = true;
              window.location.href = '/login';
              return;
            }
          }

          // RPC responses
          if (data.id != null && (data.result !== undefined || data.error !== undefined)) {
            const pending = state.pendingRequests.get(data.id);
            if (pending) {
              state.pendingRequests.delete(data.id);
              if (data.error) {
                pending.reject(new Error(data.error.message || 'RPC error'));
                console.error('[ACP] RPC 错误:', data.error);
                const errMsg = (data.error as { message?: string })?.message || '';
                if (errMsg.includes('token') || errMsg.includes('Token') || errMsg.includes('expired')) {
                  console.warn('[ACP] Token无效，跳转登录页');
                  localStorage.removeItem('openmate-token');
                  state.unmounted = true;
                  window.location.href = '/login';
                  return;
                }
              } else {
                pending.resolve(data.result);
              }
            }
            // P1插话：运行中插话的RPC响应只代表"已排队"，原任务仍在执行，不能触发完成
            if (data.id != null && state.steerRpcIds.has(data.id as number)) {
              state.steerRpcIds.delete(data.id as number);
              return;
            }
            // session/prompt 的 RPC 响应 = agent 处理完成（SoulMate 不发 last:true）
            if (data.id === state.promptRpcId) {
              state.promptRpcId = null;
              // 用 acpSessionId（om-xxx），因为 sessionDataMap 已迁移到这个 key
              handleSessionComplete(state.acpSessionId || sessionId);
            }
            return;
          }

          // ACP v1.0 session.event
          if (data.method === 'session.event') {
            const p = data.params || {};
            const eventType = p.event_type as string;
            const currentAgentId = activeAgentIdFromStore || 'soulmate';

            if (eventType === 'agent.message') {
              // Use common handler for incremental chunks
              const delta = (p.payload?.chunk || p.payload?.content_delta) as string | undefined;
              const effectiveSessionId = state?.acpSessionId || useAppStore.getState().activeSessionId || sessionId;
              if (delta) handleAgentChunk(effectiveSessionId, delta);
            }
            else if (eventType === 'session.completed') {
              const completedSessionId = p.session_id || sessionId;
              // Migrate temp session if needed, then finalize
              handleTempSessionMigration(completedSessionId, currentAgentId);
              handleSessionComplete(completedSessionId);
            }
            else if (eventType === 'session.error') {
              setLoading(false);
              streamingSessionIdRef.current = null;
              const errorMsg = p.payload?.msg || p.payload?.error || '未知错误';
              const effectiveSessionIdErr = state?.acpSessionId || useAppStore.getState().activeSessionId || sessionId;
              updateSessionMessages(effectiveSessionIdErr, prev => [...prev, { id: Date.now().toString(), role: 'agent', parts: [{ type: 'text', text: `${t("chat.error")}: ${errorMsg}` }], timestamp: new Date() }]);
            }
            else if (eventType === 'human.approval.required') {
              const payload = p.payload || {};
              console.log('[ACP] 审批请求:', payload);
              setApprovalRequest({
                request_id: payload.request_id || '',
                tool_name: payload.tool_name || 'unknown',
                risk_level: payload.risk_level || 'medium',
                description: payload.description || '',
              });
            }
            else if (eventType === 'agent.tool_call') {
              console.log('[ACP] tool call:', p.payload);
            }
          }

          // P0-3: ACP v1.0标准工具审批 — agent→client的 session/request_permission 请求
          // （opensoul免疫权限引擎 ask决策 → SoulMateAgent经acp库发起 → 弹窗答复JSON-RPC result）
          if (data.method === 'session/request_permission' && data.id != null) {
            const permParams = data.params || {};
            const permToolCall = permParams.toolCall || permParams.tool_call || {};
            const permDesc = Array.isArray(permToolCall.content)
              ? permToolCall.content.map((c: any) => c?.content?.text || '').join('\n').trim()
              : '';
            const permRisk = /risk=(high|critical)/.test(permDesc)
              ? 'high'
              : /risk=medium/.test(permDesc) ? 'medium' : 'low';
            const allowOpt = (permParams.options || []).find((o: any) => o.kind?.startsWith('allow'));
            approvalRpcRef.current = {
              id: data.id,
              ws,
              allowOptionId: allowOpt?.optionId || 'allow_once',
            };
            setApprovalRequest({
              request_id: `perm:${data.id}`,
              tool_name: permToolCall.title || 'terminal',
              risk_level: permRisk,
              description: permDesc || '代理请求执行工具，需要你的确认',
            });
            console.log('[ACP] session/request_permission:', data.id, permToolCall.title);
          }

          // ACP v1.0 session/update
          if (data.method === 'session/update') {
            const p = data.params || {};
            const updateSessionId = p.sessionId as string || sessionId;
            const update = p.update || {};
            const sessionUpdate = update.sessionUpdate as string;
            const currentAgentId2 = activeAgentIdFromStore || 'soulmate';

            if (sessionUpdate === 'agent_message_chunk') {
              const text = update.content?.text as string | undefined;
              if (text) {
                // Use common handler for incremental chunks
                handleAgentChunk(updateSessionId, text);
                // Track unread for non-active sessions
                incrementUnread(updateSessionId, text);
              }
              // last 标志必须在 if(text) 外面检查，否则无 text 的最后一包会被跳过
              if (update.last === true) {
                // Migrate temp session if needed, then finalize
                handleTempSessionMigration(updateSessionId, currentAgentId2);
                handleSessionComplete(updateSessionId);
              }
            }
            else if (sessionUpdate === 'agent_message') {
              setLoading(false);
              streamingSessionIdRef.current = null;
              const text = update.content?.text as string | undefined;
              if (text) {
                updateSessionMessages(updateSessionId, prev => {
                  const fileChanges = parseFileChanges(text);
                  const tokenUsage = simulateTokenUsage(text);
                  return [...prev, { id: Date.now().toString(), role: 'agent', parts: [{ type: 'text', text }], timestamp: new Date(), fileChanges, tokenUsage }];
                });
                incrementUnread(updateSessionId, text);
              }
            }
            // ── 系统生成的用户角色行 (user_message，角色保真) ──
            // 唤醒触发行等role='user'落库行经wakeup_relay按角色推送（21:40轮遗留#4
            // 销账）：与loadHistory的role='user'→用户气泡映射一致，同一行live视图与
            // 历史视图角色不翻转。消息构造单一真源buildUserMessage（user-message.ts
            // 纯函数，composeAskAnswers同款惯例）。不setLoading(false)：触发行先到、
            // 回合输出（agent_message）后到，agent仍在工作中。
            else if (sessionUpdate === 'user_message') {
              const text = update.content?.text as string | undefined;
              if (text) {
                updateSessionMessages(updateSessionId, prev => {
                  return [...prev, buildUserMessage(text)];
                });
              }
            }
            // ── 思考过程流式块 (agent_thought_chunk) ──
            // 累积思考文本到当前流式消息的 thinking 字段
            else if (sessionUpdate === 'agent_thought_chunk') {
              const text = update.content?.text as string | undefined;
              if (text) {
                handleAgentThoughtChunk(updateSessionId, text);
              }
            }
            // ── 思考过程完整消息 (agent_thought) ──
            // 一次性接收完整思考内容，创建已完成的 thinking block
            else if (sessionUpdate === 'agent_thought') {
              const text = update.content?.text as string | undefined;
              if (text) {
                handleAgentThoughtChunk(updateSessionId, text);
                handleAgentThoughtComplete(updateSessionId);
              }
            }
            // ── 工具调用开始 (tool_call) ──
            // text 字段是 JSON 字符串，包含 name/arguments/toolCallId 等
            else if (sessionUpdate === 'tool_call') {
              const text = update.content?.text as string | undefined;
              if (text) {
                try {
                  const toolData = JSON.parse(text);
                  handleToolCall(updateSessionId, toolData);
                } catch {
                  // JSON 解析失败时用原始文本作为工具名（兜底）
                  console.warn('[ACP] tool_call JSON 解析失败，使用原始文本:', text);
                  handleToolCall(updateSessionId, { name: text });
                }
              }
            }
            // ── 工具调用进度/结果 (tool_call_update) ──
            // text 字段是 JSON 字符串，包含 toolCallId/state/content 等
            else if (sessionUpdate === 'tool_call_update') {
              const text = update.content?.text as string | undefined;
              if (text) {
                try {
                  const updateData = JSON.parse(text);
                  handleToolCallUpdate(updateSessionId, updateData);
                } catch {
                  console.warn('[ACP] tool_call_update JSON 解析失败:', text);
                }
              }
            }
          }
        } catch {}
      };
    };

    connect();

    // WebSocket心跳：每30秒发一次ping防止NAT/路由器断开空闲连接
    const heartbeatTimer = setInterval(() => {
      const ws = wsMapRef.current.get(sessionId);
      if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ jsonrpc: '2.0', method: 'ping' })); } catch { /* ignore */ }
      }
    }, 30000);

    // Listen for token changes
    const onStorage = (e: StorageEvent) => {
      if (e.key === 'openmate-token' && e.newValue !== state.connectedToken) {
        console.log('[ACP] 检测到 token 变化，断开旧连接并重连');
        const oldWs = wsMapRef.current.get(sessionId);
        if (oldWs) oldWs.close();
        if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
        setTimeout(connect, 100);
      }
    };
    window.addEventListener('storage', onStorage);

    // Store cleanup reference
    (connect as unknown as { _cleanup: () => void })._cleanup = () => {
      state.unmounted = true;
      window.removeEventListener('storage', onStorage);
      if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
      clearInterval(heartbeatTimer);
      const oldWs = wsMapRef.current.get(sessionId);
      if (oldWs) oldWs.close();
      wsMapRef.current.delete(sessionId);
    };
  }, [getSessionState, updateSessionMessages, incrementUnread]);

  // Disconnect a single session
  const disconnectSession = useCallback((sessionId: string) => {
    const ws = wsMapRef.current.get(sessionId);
    if (ws) {
      ws.close();
      wsMapRef.current.delete(sessionId);
    }
    const state = sessionStateMapRef.current.get(sessionId);
    if (state) {
      state.unmounted = true;
      if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
    }
    sessionStateMapRef.current.delete(sessionId);
  }, []);

  // Cleanup all sessions on unmount
  useEffect(() => {
    return () => {
      for (const [sid] of wsMapRef.current) {
        disconnectSession(sid);
      }
    };
  }, [disconnectSession]);

  // 发送ACP审批决议
  const sendApproval = useCallback((requestId: string, action: 'approve' | 'reject', comment: string) => {
    // P0-3: ACP v1.0标准 request_permission — 以JSON-RPC响应直接答复agent
    // result: {"outcome":{"outcome":"selected","optionId":"allow_once"}} / {"outcome":{"outcome":"cancelled"}}
    if (requestId.startsWith('perm:') && approvalRpcRef.current) {
      const ctx = approvalRpcRef.current;
      approvalRpcRef.current = null;
      const approved = action === 'approve';
      ctx.ws.send(JSON.stringify({
        jsonrpc: '2.0',
        id: ctx.id,
        result: {
          outcome: approved
            ? { outcome: 'selected', optionId: ctx.allowOptionId }
            : { outcome: 'cancelled' },
        },
      }));
      console.log(`[ACP] request_permission #${String(ctx.id)} → ${action}`, comment || '');
      setApprovalRequest(null);
      return;
    }
    // 旧版 human.approval.required 流程：Use the active session's WebSocket for approval — read from store (single source of truth)
    const currentActiveSessionId = useAppStore.getState().activeSessionId;
    if (!currentActiveSessionId) {
      console.error('[ACP] 无法发送审批决议：无活跃会话');
      return;
    }
    const ws = wsMapRef.current.get(currentActiveSessionId);
    const state = getSessionState(currentActiveSessionId);
    const sid = state.acpSessionId;
    if (!ws || ws.readyState !== WebSocket.OPEN || !sid) {
      console.error('[ACP] 无法发送审批决议：WebSocket未连接或无会话');
      return;
    }
    const id = ++state.rpcId;
    state.pendingRequests.set(id, { resolve: () => {}, reject: () => {} });
    ws.send(JSON.stringify({
      jsonrpc: '2.0',
      id,
      method: 'session.approval',
      params: {
        session_id: sid,
        request_id: requestId,
        action,
        comment,
      },
    }));
    console.log(`[ACP] 审批决议已发送: ${action} request_id=${requestId}`);
    setApprovalRequest(null);
    setTimeout(() => { state.pendingRequests.delete(id); }, 5000);
  }, [getSessionState]);

  return { wsMapRef, wsConnected, streamingSessionIdRef, sendAcpPrompt, connectSession, disconnectSession, approvalRequest, sendApproval };
}

// ═══════════════════════════════════════════════════════════════
// 思考过程折叠组件 (ThinkingBlock)
// 可折叠的思考过程区块，默认收起；流式接收时自动展开，完成后延迟自动折叠
// ═══════════════════════════════════════════════════════════════
function ThinkingBlockComponent({ blocks, isStreaming }: { blocks: ThinkingBlock[]; isStreaming: boolean }) {
  // 展开/收起状态 —— 默认收起，用户不想看时无干扰
  const [expanded, setExpanded] = useState(false);
  // 记录上一次 isStreaming 状态，用于检测 流式→完成 的转变
  const wasStreamingRef = useRef(isStreaming);

  // 流式接收时自动展开，让用户实时看到思考过程
  useEffect(() => {
    if (isStreaming) {
      setExpanded(true);
    }
  }, [isStreaming]);

  // 流式完成（从 true 变为 false）时，延迟 500ms 自动收起
  useEffect(() => {
    if (wasStreamingRef.current && !isStreaming) {
      const timer = setTimeout(() => setExpanded(false), 500);
      return () => clearTimeout(timer);
    }
    wasStreamingRef.current = isStreaming;
  }, [isStreaming]);

  // 合并所有思考块的文本内容
  const content = blocks.map(b => b.text).join('');

  // 如果没有任何思考内容，不渲染（优雅降级）
  if (!content.trim()) return null;

  return (
    <div className="mb-2 rounded-lg border-l-2 border-[var(--color-thinking-border)] bg-[var(--color-thinking-bg)] overflow-hidden transition-all duration-200 ease-in-out">
      {/* 可点击的标题栏：切换展开/收起 */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-1.5 text-xs text-[var(--color-thinking-title)] hover:bg-[var(--color-thinking-hover)] transition-colors cursor-pointer select-none"
      >
        <span className="flex items-center gap-1.5">
          <Brain className="w-3.5 h-3.5 text-[var(--color-thinking-border)]" />
          <span className="font-medium">
            {isStreaming ? '正在思考...' : '思考过程'}
          </span>
          {isStreaming && (
            /* 流式接收中的打字光标动画 — 使用主题边框色 */
            <span className="inline-block w-1.5 h-3.5 bg-[var(--color-thinking-border)] animate-pulse ml-0.5" />
          )}
        </span>
        {/* 展开/收起指示器 — 使用主题前景色 */}
        <span className="text-[10px] text-[var(--color-thinking-text)] opacity-60">{expanded ? '▼' : '▶'}</span>
      </button>
      {/* 展开时显示思考内容 */}
      {expanded && (
        <div className="px-3 pb-2 pt-0.5">
          <MarkdownContent content={content} onCodeApply={() => {}} />
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// 工具调用信息组件 (ToolCallBlock)
// 显示工具名称 + 状态标签（⏳运行中 / ✅完成 / ❌失败）
// 可选：点击展开查看参数和结果
// ═══════════════════════════════════════════════════════════════
function ToolCallBlockComponent({ toolCalls }: { toolCalls: ToolCallInfo[] }) {
  if (!toolCalls.length) return null;

  return (
    <div className="mb-2 space-y-1">
      {toolCalls.map(tc => (
        <ToolCallItem key={tc.toolCallId} tc={tc} />
      ))}
    </div>
  );
}

/** 单个工具调用条目 —— 支持点击展开详情 */
function ToolCallItem({ tc }: { tc: ToolCallInfo }) {
  const [showDetail, setShowDetail] = useState(false);

  // 状态 → 图标 + 颜色映射（使用主题 CSS 变量）
  const statusConfig = {
    running:   { icon: '⏳', label: '运行中',  color: 'text-[var(--color-tool-status-running)]' },
    completed: { icon: '✅', label: '完成',    color: 'text-[var(--color-tool-status-done)]' },
    failed:    { icon: '❌', label: '失败',    color: 'text-[var(--color-tool-status-fail)]' },
  };
  const cfg = statusConfig[tc.state] || statusConfig.running;

  // 是否有详情可展开（参数、结果、或仍在运行中）
  const hasDetail = !!(tc.args || tc.content || tc.state === 'running');

  return (
    <div className="rounded-lg bg-[var(--color-tool-bg)] border-l-2 border-[var(--color-tool-border)] overflow-hidden">
      {/* 工具调用摘要行 */}
      <button
        onClick={() => hasDetail && setShowDetail(!showDetail)}
        className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors ${hasDetail ? 'cursor-pointer hover:bg-[var(--color-thinking-hover)]' : 'cursor-default'}`}
      >
        <span>🔧</span>
        {/* 工具名称 — 使用主题函数名色 */}
        <span className="font-mono font-medium text-[var(--color-tool-name)] truncate">
          {tc.toolName}
        </span>
        {tc.serverName && (
          <span className="text-[10px] text-[var(--color-tool-status-done)] truncate">
            ({tc.serverName})
          </span>
        )}
        {/* 状态标签 */}
        <span className={`ml-auto flex items-center gap-1 ${cfg.color}`}>
          <span>{cfg.icon}</span>
          <span>{cfg.label}</span>
          {/* 运行中的旋转动画 */}
          {tc.state === 'running' && (
            <Loader2 className="w-3 h-3 animate-spin" />
          )}
        </span>
      </button>
      {/* 展开的详情区域：调用参数和返回结果 */}
      {showDetail && hasDetail && (
        <div className="px-3 pb-2 pt-0.5 border-t border-[var(--color-border)]">
          {tc.args && (
            <div className="mb-1">
              <span className="text-[10px] text-[var(--color-tool-status-running)] uppercase tracking-wide">参数</span>
              <pre className="mt-0.5 text-[11px] text-[var(--color-thinking-text)] bg-[var(--color-thinking-bg)] rounded p-1.5 overflow-x-auto whitespace-pre-wrap break-all max-h-32 overflow-y-auto">
                {tc.args}
              </pre>
            </div>
          )}
          {tc.content && (
            <div>
              <span className="text-[10px] text-[var(--color-tool-status-running)] uppercase tracking-wide">结果</span>
              <pre className="mt-0.5 text-[11px] text-[var(--color-thinking-text)] bg-[var(--color-thinking-bg)] rounded p-1.5 overflow-x-auto whitespace-pre-wrap break-all max-h-48 overflow-y-auto">
                {tc.content}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ChatClient() {
  const [sessionDataMap, setSessionDataMap] = useState<Map<string, SessionData>>(new Map());

  const [input, setInput] = useState('');
  const [clearTrigger, setClearTrigger] = useState(0);
  const [loadFieldsTrigger, setLoadFieldsTrigger] = useState(0);
  const pendingSessionIdRef = useRef<string | null>(null);
  // per-session loading: derived from sessionDataMap for current session
  const effectiveSessionIdForLoading = useAppStore((s) => s.activeSessionId) || pendingSessionIdRef.current;
  const loading = effectiveSessionIdForLoading ? (sessionDataMap.get(effectiveSessionIdForLoading)?.loading || false) : false;
  const setLoading = useCallback((val: boolean | ((prev: boolean) => boolean)) => {
    const sid = useAppStore.getState().activeSessionId || pendingSessionIdRef.current;
    if (!sid) return;
    setSessionDataMap(prev => {
      const next = new Map(prev);
      const data = next.get(sid) || { messages: [], unreadCount: 0 };
      const newLoading = typeof val === 'function' ? val(data.loading || false) : val;
      next.set(sid, { ...data, loading: newLoading });
      return next;
    });
  }, []);
  const [showScrollDown, setShowScrollDown] = useState(false);
  const agents = useAppStore((s) => s.sidebarAgents) as AgentInfo[];
  const setSidebarAgents = useAppStore((s) => s.setSidebarAgents);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<AgentInfo | null>(null);
  const [attachments, setAttachments] = useState<MessagePart[]>([]);
  const [readingCount, setReadingCount] = useState(0); // 附件读取中计数（大文件base64异步读取，防止读取完成前发送空附件）
  const [smartPromptTask, setSmartPromptTask] = useState<string>('');
  const [agentMode, setAgentMode] = useState<AgentMode>('act');
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [showCheckpoints, setShowCheckpoints] = useState(false);
  // 思考过程/工具调用显示开关，默认显示，状态持久化到 localStorage
  const [showThinking, setShowThinking] = useState(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('chat-show-thinking') !== 'false';
    }
    return true;
  });
  useEffect(() => {
    localStorage.setItem('chat-show-thinking', String(showThinking));
  }, [showThinking]);
  const [deleteConfirm, setDeleteConfirm] = useState<{ id: string; name: string } | null>(null);
  const [editingTitle, setEditingTitle] = useState(false);
  const [editTitleValue, setEditTitleValue] = useState('');
  const titleInputRef = useRef<HTMLInputElement>(null);
  const [chatView, setChatView] = useState<string>('messages');
  const [smartTaskText, setSmartTaskText] = useState<string>(''); // SmartPrompt实时文字（发送按钮禁用判断：附件与文字都为空时禁用）
  const [calMonth, setCalMonth] = useState<Date>(() => new Date()); // 日期日历视图当前查看月份
  const [showViewTabs, setShowViewTabs] = useState(false);
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const setSessionDetails = useAppStore((s) => s.setSessionDetails);
  const { toggleSidebar, open: sidebarOpen } = useSidebar();
  const toggleRightPanel = useAppStore((s) => s.toggleRightPanel);
  const setRightPanelOpen = useAppStore((s) => s.setRightPanelOpen);
  const rightPanelOpen = useAppStore((s) => s.rightPanelOpen);
  // Agent metadata from store (set by sidebar when selecting a session)
  const storeAgentIcon = useAppStore((s) => s.activeAgentIcon);
  const storeAgentName = useAppStore((s) => s.activeAgentName);
  const storeSessionName = useAppStore((s) => s.activeSessionName);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const selectedSessionRef = useRef<Session | null>(null);
  const activeAgentIdFromStore = useAppStore((s) => s.activeAgentId);

  // Multi-session helpers
  const updateSessionMessages = useCallback((sessionId: string, updater: (prev: Message[]) => Message[]) => {
    setSessionDataMap(prev => {
      const data = prev.get(sessionId) || { messages: [], unreadCount: 0 };
      const newMessages = updater(data.messages);
      return new Map(prev).set(sessionId, { ...data, messages: newMessages });
    });
  }, []);

  const incrementUnread = useCallback((sessionId: string, lastMsg: string) => {
    const currentActiveSessionId = useAppStore.getState().activeSessionId;
    if (sessionId === currentActiveSessionId) return;
    setSessionDataMap(prev => {
      const data = prev.get(sessionId) || { messages: [], unreadCount: 0 };
      return new Map(prev).set(sessionId, {
        ...data,
        unreadCount: data.unreadCount + 1,
        lastMessage: lastMsg,
      });
    });
  }, []);

  const clearUnread = useCallback((sessionId: string) => {
    setSessionDataMap(prev => {
      const data = prev.get(sessionId);
      if (!data || data.unreadCount === 0) return prev;
      return new Map(prev).set(sessionId, { ...data, unreadCount: 0 });
    });
  }, []);

  const updateCurrentSessionMessages = useCallback((updater: (prev: Message[]) => Message[]) => {
    const sessionId = useAppStore.getState().activeSessionId || selectedSessionRef.current?.id;
    if (!sessionId) return; // No active session, skip update
    updateSessionMessages(sessionId, updater);
  }, [updateSessionMessages]);

  const clearCurrentSessionMessages = useCallback(() => {
    const sessionId = selectedSessionRef.current?.id || useAppStore.getState().activeSessionId;
    if (!sessionId) return; // No active session, skip clear
    updateSessionMessages(sessionId, () => []);
  }, [updateSessionMessages]);

  // Migrate session data from a temp ID to a real session ID
  const migrateSessionId = useCallback((oldId: string, newId: string) => {
    // Update store's activeSessionId if it matches the old temp ID
    const currentActive = useAppStore.getState().activeSessionId;
    if (currentActive === oldId) {
      // 迁移时保留当前 agentId，不要重置为 null
      const currentAgentId = useAppStore.getState().activeAgentId;
      // 保留现有 metadata（特别是 sessionName），不要传空对象
      useAppStore.getState().setActiveSession(newId, currentAgentId);
    }
    // 更新 sidebarAgents 列表中的 session ID（temp → real）
    // 不再直接修改 sidebarAgents，由 refreshSidebar → fetchSessions 统一管理
    setSessionDataMap(prev => {
      const data = prev.get(oldId);
      if (!data) return prev;
      const next = new Map(prev);
      next.delete(oldId);
      next.set(newId, data);
      return next;
    });
    // Sync refs so they point to the migrated ID
    if (pendingSessionIdRef.current === oldId) pendingSessionIdRef.current = newId;
    if (selectedSessionRef.current?.id === oldId) selectedSessionRef.current = { ...selectedSessionRef.current, id: newId };
    // Note: sessionStateMapRef and wsMapRef migration happens in hook's performHandshake
    // 刷新由注册流程落库后的 refreshSidebar 统一触发（此处不重复，防一次操作多次整表重拉）
  }, []);

  // Derived messages for active session (read from store — single source of truth)
  const activeSessionIdFromStore = useAppStore((s) => s.activeSessionId);
  // 优先用 store 值，store 未更新时用 ref 兜底（onSend 里同步设置）
  const effectiveSessionId = activeSessionIdFromStore || pendingSessionIdRef.current;
  const messages = effectiveSessionId ? (sessionDataMap.get(effectiveSessionId)?.messages || []) : [];
  // Collect all file attachments from messages (for file view)
  const fileAttachments = useMemo(() => {
    const files: { name: string; filePath?: string; mimeType?: string; data?: string; messageId: string; role: 'user' | 'agent'; timestamp: Date }[] = [];
    for (const msg of messages) {
      // Extract file/image parts
      for (const p of msg.parts) {
        if (p.type === 'file' && p.name) {
          files.push({ name: p.name, mimeType: p.mime_type, data: p.data, messageId: msg.id, role: msg.role, timestamp: msg.timestamp });
        }
        if (p.type === 'image' && p.data) {
          files.push({ name: p.name || 'image.png', mimeType: p.mime_type || 'image/png', data: p.data, messageId: msg.id, role: msg.role, timestamp: msg.timestamp });
        }
        if (p.type === 'audio' && p.data) {
          files.push({ name: p.name || 'voice', mimeType: p.mime_type || 'audio/wav', data: p.data, messageId: msg.id, role: msg.role, timestamp: msg.timestamp });
        }
      }
      // Also scan text parts for MEDIA: tags
      for (const p of msg.parts) {
        if (p.type === 'text' && p.text) {
          const mediaMatches = p.text.matchAll(/MEDIA:(\/[^\s\n]+)/g);
          for (const m of mediaMatches) {
            const filePath = m[1];
            const fileName = filePath.split('/').pop() || filePath;
            const ext = fileName.split('.').pop()?.toLowerCase() || '';
            const mimeType = ext === 'html' || ext === 'htm' ? 'text/html' : ext === 'md' ? 'text/markdown' : ext === 'json' ? 'application/json' : 'text/plain';
            if (!files.some(f => f.filePath === filePath)) {
              files.push({ name: fileName, filePath, mimeType, data: undefined, messageId: msg.id, role: msg.role, timestamp: msg.timestamp });
            }
          }
        }
      }
    }
    return files;
  }, [messages]);

  // 分类视图数据：图片、视频、音频
  const imageAttachments = useMemo(() => fileAttachments.filter(f =>
    f.mimeType?.startsWith('image/') || /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i.test(f.name)
  ), [fileAttachments]);

  const videoAttachments = useMemo(() => fileAttachments.filter(f =>
    f.mimeType?.startsWith('video/') || /\.(mp4|avi|mov|wmv|flv|mkv|webm)$/i.test(f.name)
  ), [fileAttachments]);

  const audioAttachments = useMemo(() => fileAttachments.filter(f =>
    f.mimeType?.startsWith('audio/') || /\.(mp3|wav|ogg|flac|aac|m4a|wma)$/i.test(f.name)
  ), [fileAttachments]);

  // 文档类（用户规格）：pdf,docx,xlsx,pptx,md,txt — 代码类/其他全部归"其他"兜底
  const docAttachments = useMemo(() => fileAttachments.filter(f => {
    if (imageAttachments.includes(f) || videoAttachments.includes(f) || audioAttachments.includes(f)) return false;
    return /pdf|msword|excel|powerpoint|officedocument|opendocument|text\/plain|markdown/.test(f.mimeType || '')
      || /\.(pdf|docx?|xlsx?|pptx?|md|txt)$/i.test(f.name);
  }), [fileAttachments, imageAttachments, videoAttachments, audioAttachments]);

  // 其他类：排除图片/视频/音频/文档后的剩余（压缩包、二进制、未知类型）
  const otherAttachments = useMemo(() => fileAttachments.filter(f =>
    !imageAttachments.includes(f) && !videoAttachments.includes(f) && !audioAttachments.includes(f) && !docAttachments.includes(f)
  ), [fileAttachments, imageAttachments, videoAttachments, audioAttachments, docAttachments]);

  // 链接提取：从消息文本中提取URL
  const linkItems = useMemo(() => {
    const links: { url: string; title?: string; messageId: string; role: 'user' | 'agent'; timestamp: Date }[] = [];
    const urlRegex = /https?:\/\/[^\s<>"')\]]+/g;
    for (const msg of messages) {
      for (const p of msg.parts) {
        if (p.type === 'text' && p.text) {
          const matches = p.text.matchAll(urlRegex);
          for (const m of matches) {
            const url = m[0].replace(/[.,;:!?]+$/, '');
            if (!links.some(l => l.url === url)) {
              // 尝试从URL提取域名作为标题
              let title = '';
              try { title = new URL(url).hostname; } catch { title = url.slice(0, 50); }
              links.push({ url, title, messageId: msg.id, role: msg.role, timestamp: msg.timestamp });
            }
          }
        }
      }
    }
    return links;
  }, [messages]);

  // 日期分组：按日期分组消息
  const dateGroups = useMemo(() => {
    const groups: { date: string; label: string; count: number; messages: typeof messages }[] = [];
    const map = new Map<string, typeof messages>();
    for (const msg of messages) {
      const d = msg.timestamp.toISOString().slice(0, 10);
      if (!map.has(d)) map.set(d, []);
      map.get(d)!.push(msg);
    }
    for (const [date, msgs] of map) {
      const d = new Date(date);
      const now = new Date();
      const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000);
      let label = date;
      if (diffDays === 0) label = '今天';
      else if (diffDays === 1) label = '昨天';
      else if (diffDays < 7) label = `${diffDays}天前`;
      else label = d.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' });
      groups.push({ date, label, count: msgs.length, messages: msgs });
    }
    return groups.sort((a, b) => b.date.localeCompare(a.date));
  }, [messages]);

  // 跳转上下文：切到会话视图+滚动定位到目标消息（各视图功能按钮+日期日历共用）
  const jumpToMessage = useCallback((messageId: string) => {
    setChatView('messages');
    setTimeout(() => {
      const el = document.getElementById(`msg-${messageId}`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.classList.add('ring-2', 'ring-primary');
        setTimeout(() => el.classList.remove('ring-2', 'ring-primary'), 2000);
      }
    }, 150);
  }, []);

  // 通用附件操作按钮（跳转/预览/下载/复制）— 文档/其他/图片/视频/音频视图共用
  const renderAttachmentActions = (f: { name: string; messageId?: string; data?: string; filePath?: string; mimeType?: string }) => (
    <>
      {f.messageId && <button onClick={(e) => { e.stopPropagation(); jumpToMessage(f.messageId!); }} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors" title="跳转到会话中此消息">
        <LocateFixed className="w-3 h-3" />
        跳转
      </button>}
      <button onClick={async (e) => {
        e.stopPropagation();
        const store = useAppStore.getState();
        let dataUrl: string | undefined;
        if (f.data) {
          dataUrl = `data:${f.mimeType || 'application/octet-stream'};base64,${f.data}`;
        } else if (f.filePath) {
          try {
            const apiBase = getApiUrl();
            const resp = await fetch(`${apiBase}/api/file?path=${encodeURIComponent(f.filePath)}`);
            if (resp.ok) {
              const data = await resp.json();
              const b64 = btoa(unescape(encodeURIComponent(data.content || '')));
              dataUrl = `data:${f.mimeType || 'text/plain'};base64,${b64}`;
            }
          } catch (err) { console.warn('[file-preview] Failed:', err); }
        }
        if (dataUrl) {
          store.setPendingFilePreview({ url: dataUrl, name: f.name, mimeType: f.mimeType });
          store.setRightPanelOpen(true);
        }
      }} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-primary hover:bg-primary/10 transition-colors">
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></svg>
        预览
      </button>
      {(f.data || f.filePath) && <button onClick={async (e) => {
        e.stopPropagation();
        if (f.data) {
          const byteChars = atob(f.data);
          const bytes = new Uint8Array(byteChars.length);
          for (let j = 0; j < byteChars.length; j++) bytes[j] = byteChars.charCodeAt(j);
          const blob = new Blob([bytes], { type: f.mimeType || 'application/octet-stream' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a'); a.href = url; a.download = f.name; a.click(); URL.revokeObjectURL(url);
        } else if (f.filePath) {
          try {
            const apiBase = getApiUrl();
            const resp = await fetch(`${apiBase}/api/file?path=${encodeURIComponent(f.filePath)}`);
            if (resp.ok) {
              const content = (await resp.json()).content || '';
              const blob = new Blob([content], { type: f.mimeType || 'text/plain' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a'); a.href = url; a.download = f.name; a.click(); URL.revokeObjectURL(url);
            }
          } catch (err) { console.warn('[file-download] Failed:', err); }
        }
      }} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors">
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
        下载
      </button>}
      {f.data && <button onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(atob(f.data!)); }} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors">
        <Copy className="w-3 h-3" />
        复制
      </button>}
    </>
  );


  // Total unread count across all sessions
  const totalUnread = useMemo(() => {
    let count = 0;
    for (const [, data] of sessionDataMap) {
      count += data.unreadCount;
    }
    return count;
  }, [sessionDataMap]);
  const getSessionMessages = useCallback((sessionId: string): Message[] => {
    return sessionDataMap.get(sessionId)?.messages || [];
  }, [sessionDataMap]);

  const { wsMapRef, wsConnected, streamingSessionIdRef, sendAcpPrompt, connectSession, disconnectSession, approvalRequest, sendApproval } = useAcpWebSocket({
    selectedAgent, selectedSession, selectedSessionRef,
    t, updateSessionMessages, incrementUnread, setLoading, setSelectedSession, activeAgentIdFromStore, migrateSessionId, getSessionMessages,
  });

  // Auto-resize textarea on input
  const autoResizeTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  }, []);

  useEffect(() => { autoResizeTextarea(); }, [input, autoResizeTextarea]);

  // Close checkpoints when sidebar or right panel opens on mobile (handles external triggers like bottom nav)
  useEffect(() => {
    if (isMobile && showCheckpoints && (sidebarOpen || rightPanelOpen)) {
      setShowCheckpoints(false);
    }
  }, [sidebarOpen, rightPanelOpen, isMobile, showCheckpoints]);

  // Checkpoint management
  const saveCheckpoint = useCallback((messageId: string) => {
    const checkpoint: Checkpoint = {
      id: Date.now().toString(),
      messageId,
      timestamp: new Date(),
      messages: [...messages],
      label: `${t("chat.checkpoints")} ${checkpoints.length + 1}`,
    };
    setCheckpoints(prev => [...prev, checkpoint]);
  }, [messages, checkpoints]);

  const rollbackToCheckpoint = useCallback((checkpointId: string) => {
    const checkpoint = checkpoints.find(cp => cp.id === checkpointId);
    if (checkpoint) {
      updateCurrentSessionMessages(() => checkpoint.messages);
      setShowCheckpoints(false);
    }
  }, [checkpoints]);

  // ── Session cumulative stats ────────────────────────────────
  const sessionStats = useMemo(() => {
    let totalIn = 0, totalOut = 0, totalCost = 0, msgCount = 0;
    for (const msg of messages) {
      if (msg.role === 'agent' && msg.tokenUsage) {
        totalIn += msg.tokenUsage.input;
        totalOut += msg.tokenUsage.output;
        totalCost += calculateCost(msg.tokenUsage);
        msgCount++;
      }
    }
    return { totalIn, totalOut, totalCost, msgCount };
  }, [messages]);

  // ── Message action handlers ──────────────────────────────────

  // Get plain text from message parts
  const getMessageText = useCallback((msg: Message): string => {
    return msg.parts.filter(p => p.type === 'text').map(p => p.text).join('\n');
  }, []);

  // Copy message text to clipboard
  const handleCopy = useCallback((msg: Message) => {
    copyToClipboard(getMessageText(msg));
  }, [getMessageText]);

  // Read aloud using Web Speech API
  const handleReadAloud = useCallback((msg: Message) => {
    const text = getMessageText(msg);
    if (!text) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'zh-CN';
    utterance.rate = 1.0;
    window.speechSynthesis.speak(utterance);
  }, [getMessageText]);

  // Like/dislike — send feedback to backend
  const handleFeedback = useCallback(async (msg: Message, type: 'like' | 'dislike') => {
    try {
      await fetch(`${getApiUrl()}/api/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({
          message_id: msg.id,
          session_id: selectedSession?.id,
          type,
          content: getMessageText(msg).slice(0, 200),
          user_id: getUserId(),
        }),
      });
    } catch (e) { console.error('Feedback error:', e); }
  }, [selectedSession, getMessageText]);

  // Share via Web Share API or copy
  const handleShare = useCallback(async (msg: Message) => {
    const text = getMessageText(msg);
    if (navigator.share) {
      try { await navigator.share({ text }); } catch { /* user cancelled */ }
    } else {
      copyToClipboard(text);
    }
  }, [getMessageText]);

  // Regenerate — re-send the last user message before this agent message
  const handleRegenerate = useCallback((agentMsgId: string) => {
    const agentIdx = messages.findIndex(m => m.id === agentMsgId);
    if (agentIdx < 0) return;
    // Find the user message before this agent message
    let userMsg: Message | null = null;
    for (let i = agentIdx - 1; i >= 0; i--) {
      if (messages[i].role === 'user') { userMsg = messages[i]; break; }
    }
    if (!userMsg) return;
    // Remove the agent message
    updateCurrentSessionMessages(prev => prev.filter(m => m.id !== agentMsgId));
    // 通过 ACP 重新发送
    const text = getMessageText(userMsg);
    const regenSessionId = selectedSession?.id || activeSessionIdFromStore;
    if (!regenSessionId) return; // No session to regenerate into
    setLoading(true);
    streamingSessionIdRef.current = regenSessionId;
    sendAcpPrompt(regenSessionId, text);
  }, [messages, selectedSession, selectedAgent, getMessageText, sendAcpPrompt]);

  // Edit user message — put text back into input
  const handleEditMessage = useCallback((msg: Message) => {
    const text = getMessageText(msg);
    // Remove the message from list
    updateCurrentSessionMessages(prev => prev.filter(m => m.id !== msg.id));
    // Set the message text as draft in SmartPrompt
    const sid = useAppStore.getState().activeSessionId;
    if (sid) {
      setSessionDataMap(prev => {
        const next = new Map(prev);
        const data = next.get(sid) || { messages: [], unreadCount: 0 };
        next.set(sid, { ...data, promptFields: { task: text, role: '', background: '', constraints: '', format: '' } });
        return next;
      });
      setLoadFieldsTrigger(n => n + 1);
    }
  }, [getMessageText]);

  // Favorite message — save to store
  const handleFavorite = useCallback(async (msg: Message) => {
    try {
      await fetch(`${getApiUrl()}/api/knowledge/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({
          content: getMessageText(msg),
          content_type: 'feedback',
          title: `收藏消息 ${new Date().toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' , fractionalSecondDigits: 3 })}`,
          user_id: getUserId(),
        }),
      });
    } catch (e) { console.error('Favorite error:', e); }
  }, [getMessageText]);

  // Delete message
  const handleDeleteMessage = useCallback((msgId: string) => {
    updateCurrentSessionMessages(prev => prev.filter(m => m.id !== msgId));
  }, []);

  // Load history for a session
  // Delete session
  const deleteSession = useCallback(async (sessionId: string) => {
    try {
      const res = await fetch(`${getApiBaseUrl()}/api/sessions/${sessionId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      if (res.ok) {
        // Remove from local state (both flat sessions and sourceGroups)
        setSidebarAgents((prev: AgentInfo[]) => prev.map(a => ({
          ...a,
          sessions: a.sessions.filter(s => s.id !== sessionId),
          sourceGroups: a.sourceGroups?.map(g => ({
            ...g,
            sessions: g.sessions.filter(s => s.id !== sessionId),
          })).filter(g => g.sessions.length > 0),
        })));
        if (selectedSession?.id === sessionId) {
          setSelectedSession(null);
          selectedSessionRef.current = null;
          clearCurrentSessionMessages();
        }
      } else {
        const errData = await res.json().catch(() => ({}));
        console.error("Delete failed:", res.status, errData);
      }
    } catch (e) { console.error("Delete failed:", e); }
    setDeleteConfirm(null);
  }, [selectedSession]);

  const loadHistory = useCallback(async (sessionId: string) => {
    // 新建的 temp session 不需要加载历史，也不要清空（消息已经在 onSend 里写入了）
    if (sessionId.startsWith('temp-')) return;
    // 如果本地已有消息（流式写入的），先显示本地数据
    const localMsgs = sessionDataMap.get(sessionId);
    if (localMsgs && localMsgs.messages.length > 0) {
      updateCurrentSessionMessages(() => localMsgs.messages);
      // 不return — 继续从DB加载，用DB数据补全可能缺失的AI回复
    }
    // 所有会话（包括 soulmate）都从 DB 加载历史
    try {
      const r = await fetch(`${getApiUrl()}/api/sessions/${sessionId}/messages`, { headers: { Authorization: `Bearer ${getToken()}` } });
      if (r.ok) {
        const d = await r.json();
        const msgs: Message[] = (d.messages || [])
          .filter((m: Record<string, unknown>) => {
            const role = m.role as string;
            const content = m.content as string;
            if (role === 'user' && content) return true;
            if (role === 'assistant' && content && content.trim()) return true;
            return false;
          })
          .map((m: Record<string, unknown>) => {
            const content = (m.content as string) || '';
            const isAgent = m.role !== 'user';
            const attachmentParts = ((m.attachments as Array<Record<string, unknown>>) || [])
              .filter((a): a is Record<string, unknown> & { data: string } => typeof a.data === 'string' && a.data.length > 0)
              .map(a => ({
                type: (a.mime_type as string)?.startsWith('image/') ? 'image' : (a.mime_type as string)?.startsWith('audio/') ? 'audio' : 'file',
                data: a.data as string,
                name: a.name as string,
                mime_type: a.mime_type as string,
              }));
            return {
              id: (m.id || Date.now()).toString(),
              role: isAgent ? 'agent' : 'user',
              parts: [{ type: 'text', text: content }, ...attachmentParts],
              timestamp: new Date((m.timestamp as string) || Date.now()),
              source: m.source as string,
              fileChanges: isAgent ? parseFileChanges(content) : undefined,
              memoryMarker: (m.memory_marker as MemoryMarker | null) || null,
            };
          });
        // DB数据可能比本地更完整（包含AI回复），但如果本地有更多消息（流式中），取更长的
        const localCount = sessionDataMap.get(sessionId)?.messages.length || 0;
        if (msgs.length >= localCount || localCount === 0) {
          updateCurrentSessionMessages(() => msgs);
        }
      }
    } catch (e) { console.error('[loadHistory] error:', e); }
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    setShowScrollDown(false);
  }, [messages]);

  // Listen for session selection from global sidebar (via store)
  // activeSessionIdFromStore is already declared above near line 770

  useEffect(() => {
    console.log(`[SESSION_EFFECT] activeSessionIdFromStore=${activeSessionIdFromStore}, selectedSession?.id=${selectedSession?.id}, activeAgentIdFromStore=${activeAgentIdFromStore}`);
    // 清除跨页面刷新残留的 temp session（temp ID 不应持久化到下次刷新）
    if (activeSessionIdFromStore && activeSessionIdFromStore.startsWith('temp-')) {
      const store = useAppStore.getState();
      if (store._isNewSessionFlow) {
        // 当前新建流程中的 temp，允许保留，不清除标记（标记在session替换后清除）
      } else {
        // 页面刷新导致的残留 temp，清除
        store.setActiveSession(null, null);
        return;
      }
    }
    if (!activeSessionIdFromStore) {
      // SoulMate new session: clear session but keep WS alive (don't return early)
      const isSoulMate = !activeAgentIdFromStore || activeAgentIdFromStore === 'soulmate';
      // 先清除旧session的messages，再清ref
      if (!isSoulMate) {
        clearCurrentSessionMessages();
      }
      setSelectedSession(null);
      selectedSessionRef.current = null;
      if (!isSoulMate) {
        return;
      }
      // SoulMate: don't clear messages, let WS useEffect handle connection
    }
    if (selectedSession?.id === activeSessionIdFromStore) return;
    // Try to find session in local agents list
    for (const agent of agents) {
      const session = agent.sessions.find(s => s.id === activeSessionIdFromStore);
      if (session) {
        selectSession(session, agent);
        return;
      }
    }
    // Fallback: create minimal objects from store data (only when we have a valid sessionId)
    // 不要在 activeSessionId 为空时创建连接——这会导致 agent_id="unknown" 的无效 WebSocket
    if (storeAgentName && activeSessionIdFromStore) {
      const minimalSession: Session = { id: activeSessionIdFromStore, name: storeSessionName || '', platform: 'hermes' } as Session;
      const minimalAgent: AgentInfo = {
        id: activeAgentIdFromStore || 'soulmate', // SoulMate 的 activeAgentId 为 null，fallback 到 'soulmate'
        name: storeAgentName,
        icon: storeAgentIcon || SOULMATE_ICON,
        logo: (!activeAgentIdFromStore || activeAgentIdFromStore === 'soulmate') ? SOULMATE_LOGO : undefined,
        description: '',
        installed: true,
        sessions: [],
        expanded: false,
      };
      selectSession(minimalSession, minimalAgent);
    }
  }, [activeSessionIdFromStore, agents]);

  // Auto-select session from URL param ?session=SESSION_ID (fallback)
  // Expose sendAcpPrompt to window for TaskChoiceMenu
  useEffect(() => {
    (window as any).__openmate_send__ = (text: string) => {
      const sid = useAppStore.getState().activeSessionId;
      if (sid) sendAcpPrompt(sid, text);
    };
    return () => { delete (window as any).__openmate_send__; };
  }, [sendAcpPrompt]);

  useEffect(() => {
    const sid = new URLSearchParams(window.location.search).get('session');
    if (!sid) return;
    if (selectedSession?.id === sid) return;
    for (const agent of agents) {
      const session = agent.sessions.find(s => s.id === sid);
      if (session) {
        selectSession(session, agent);
        return;
      }
    }
  }, [agents]);

  // When store agentId changes, switch selectedAgent and clear session/messages
  // 但如果用户点击了具体会话（activeSessionId 有值），不要清空
  useEffect(() => {
    if (!agents.length) return;
    const agent = activeAgentIdFromStore
      ? agents.find(a => a.id === activeAgentIdFromStore)
      : agents.find(a => a.id === 'soulmate') || agents[0];
    if (agent && selectedAgent?.id !== agent.id) {
      setSelectedAgent(agent);
      if (!useAppStore.getState().activeSessionId) {
        setSelectedSession(null);
        clearCurrentSessionMessages();
      }
    }
  }, [activeAgentIdFromStore, agents]);

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    Array.from(files).forEach(file => {
      setReadingCount(c => c + 1);
      const reader = new FileReader();
      reader.onload = () => {
        const base64 = (reader.result as string).split(',')[1];
        const isImage = file.type.startsWith('image/');
        const isAudio = file.type.startsWith('audio/');
        setAttachments(prev => [...prev, { type: isImage ? 'image' : isAudio ? 'audio' : 'file', data: base64, name: file.name, mime_type: file.type }]);
        setReadingCount(c => Math.max(0, c - 1));
      };
      reader.onerror = () => {
        setReadingCount(c => Math.max(0, c - 1));
      };
      reader.readAsDataURL(file);
    });
    e.target.value = '';
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of Array.from(items)) {
      if (item.type.startsWith('image/')) {
        e.preventDefault();
        const blob = item.getAsFile();
        if (!blob) continue;
        const reader = new FileReader();
        reader.onload = () => {
          const base64 = (reader.result as string).split(',')[1];
          setAttachments(prev => [...prev, { type: 'image', data: base64, name: 'clipboard.png', mime_type: 'image/png' }]);
        };
        reader.readAsDataURL(blob);
      }
    }
  };

  // ── 语音输入（17.2）：MediaRecorder 录音 → 统一转 WAV → 音频附件（sendAcpPrompt 以 audio ACP 块直发）──
  const [isRecording, setIsRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [audioError, setAudioError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recordChunksRef = useRef<Blob[]>([]);
  const recordTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopRecording = useCallback(() => {
    if (recordTimerRef.current) { clearInterval(recordTimerRef.current); recordTimerRef.current = null; }
    setIsRecording(false);
    setRecordingSeconds(0);
    const rec = recorderRef.current;
    recorderRef.current = null;
    if (rec && rec.state !== 'inactive') rec.stop();
  }, []);

  const startRecording = useCallback(async () => {
    setAudioError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = pickRecorderMimeType();
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recordChunksRef.current = [];
      rec.ondataavailable = (e) => { if (e.data && e.data.size > 0) recordChunksRef.current.push(e.data); };
      rec.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(recordChunksRef.current, { type: rec.mimeType || 'audio/webm' });
        recordChunksRef.current = [];
        if (!blob.size) { setAudioError('录音为空，请重试'); return; }
        try {
          const att = await blobToAudioAttachment(blob);
          if (!att.converted) setAudioError(`音频未能统一转码，已按原格式 ${att.mime_type} 附着（如后端不支持会显式报因）`);
          setAttachments(prev => [...prev, att]);
        } catch (e) {
          setAudioError(`音频处理失败：${(e as Error)?.message || e}`);
        }
      };
      rec.onerror = () => { setAudioError('录音出错，请重试'); };
      rec.start();
      recorderRef.current = rec;
      setIsRecording(true);
      setRecordingSeconds(0);
      recordTimerRef.current = setInterval(() => setRecordingSeconds(s => s + 1), 1000);
    } catch (e) {
      const name = (e as Error)?.name || '';
      setAudioError(
        name === 'NotAllowedError' ? '麦克风权限被拒绝，无法录音'
        : name === 'NotFoundError' ? '未检测到麦克风设备'
        : `无法开始录音：${(e as Error)?.message || e}`
      );
    }
  }, []);

  const toggleRecording = useCallback(() => {
    if (isRecording) stopRecording();
    else void startRecording();
  }, [isRecording, startRecording, stopRecording]);

  // 组件卸载收尾录音（释放麦克风，不静默留后台录音）
  useEffect(() => () => {
    if (recordTimerRef.current) clearInterval(recordTimerRef.current);
    const rec = recorderRef.current;
    if (rec && rec.state !== 'inactive') rec.stop();
  }, []);

  // 公共 WS 等待函数：连接建立后自动发送消息
  // opts.steer=true：任务运行中发送=插话（排队注入，不抢占当前任务）
  const waitForConnection = useCallback((sessionId: string, messageText: string, attachments?: MessagePart[], opts?: { steer?: boolean }) => {
    let spWaited = 0; // 已等待毫秒数
    const spWaitConnect = setInterval(() => {
      spWaited += 500; // 每500ms检查一次
      // WS 可能被迁移到新 sessionId（temp→real），两个都检查
      const ws2 = wsMapRef.current.get(sessionId) || wsMapRef.current.get(useAppStore.getState().activeSessionId || '');
      console.debug(`[ACP-DEBUG] waitForConnection(${sessionId}) poll#${spWaited / 500} active=${useAppStore.getState().activeSessionId} found=${ws2 ? ws2.readyState : 'none'}`);
      if (ws2?.readyState === WebSocket.OPEN) {
        clearInterval(spWaitConnect);
        // 用迁移后的 sessionId 发送
        const activeSid = useAppStore.getState().activeSessionId;
        const sendId = activeSid && wsMapRef.current.has(activeSid) ? activeSid : sessionId;
        sendAcpPrompt(sendId, messageText, attachments, opts);
      } else if (spWaited >= 15000) {
        // 超时15秒，放弃并提示用户
        clearInterval(spWaitConnect);
        setLoading(false);
        updateSessionMessages(sessionId, prev => [...prev, { id: Date.now().toString(), role: 'agent', parts: [{ type: 'text', text: t('chat.connectionLost') }], timestamp: new Date() }]);
      }
    }, 500);
  }, [sendAcpPrompt, updateSessionMessages, t]);

  const selectSession = (session: Session, agent: AgentInfo) => {
    console.log(`[selectSession] session.id=${session.id}, agent.id=${agent.id}`);
    // Update store (single source of truth for activeSessionId)
    useAppStore.getState().setActiveSession(session.id, agent.id === 'soulmate' ? null : agent.id, {
      agentName: agent.name,
      sessionName: session.name || session.title || '',
    });
    setSelectedSession(session);
    selectedSessionRef.current = session;
    setSelectedAgent(agent);
    setDeleteConfirm(null);
    setEditingTitle(false);
    setShowCheckpoints(false); // close checkpoints when switching sessions
    loadHistory(session.id);
    // Ensure ACP WebSocket is connected for this session
    connectSession(session.id, agent.id);
    // Update store with agent metadata for cross-component access
    setSessionDetails({
      agentIcon: agent.icon,
      agentName: agent.name,
      agentDescription: agent.description || '',
      sessionName: session.name || session.title || '',
      lastActive: session.last_active || session.updated_at || '',
      imageCount: 0,
      fileCount: 0,
    });
  };

  const startEditTitle = () => {
    if (!selectedSession?.id) return; // can't rename unsaved sessions
    setEditTitleValue(selectedSession.name || selectedSession.title || '');
    setEditingTitle(true);
    setTimeout(() => titleInputRef.current?.select(), 50);
  };

  const saveTitle = async () => {
    if (!selectedSession?.id || !editTitleValue.trim()) {
      setEditingTitle(false);
      return;
    }
    const newTitle = editTitleValue.trim();
    setEditingTitle(false);
    try {
      const res = await fetch(`${getApiBaseUrl()}/api/sessions/${selectedSession.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: newTitle }),
      });
      if (res.ok) {
        setSelectedSession(prev => prev ? { ...prev, name: newTitle, title: newTitle } : prev);
        useAppStore.getState().refreshSidebar(); // refresh sidebar list
      }
    } catch (e) { console.error('Rename failed:', e); }
  };

  // Collect attachments from conversation
  const allAttachments = messages.flatMap(m => m.parts.filter(p => p.type === 'image' || p.type === 'file'));
  const imageCount = allAttachments.filter(p => p.type === 'image').length;
  const fileCount = allAttachments.filter(p => p.type === 'file').length;

  // Sync image/file counts to store for workspace details tab
  useEffect(() => {
    const current = useAppStore.getState().sessionDetails;
    if (current) {
      setSessionDetails({ ...current, imageCount, fileCount });
    }
  }, [imageCount, fileCount]);

  // Shared checkpoint list content (used by both mobile sidebar-style panel and desktop overlay)
  const renderCheckpointList = () => (
    <div className="flex-1 overflow-y-auto p-3 space-y-2">
      {checkpoints.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
          <Bookmark className="w-8 h-8 mb-2 opacity-40" />
          <p className="text-xs">{t("chat.noCheckpoints", "No checkpoints saved")}</p>
          <p className="text-[10px] text-muted-foreground mt-1">{t("chat.checkpointHint", "Click the bookmark icon on any agent message to save a checkpoint")}</p>
        </div>
      ) : (
        checkpoints.map(cp => (
          <div key={cp.id} className="flex items-center justify-between p-3 rounded-lg border border-border bg-card hover:bg-muted/50 transition-colors">
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium truncate">{cp.label}</p>
              <p className="text-[10px] text-muted-foreground">{cp.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' , fractionalSecondDigits: 3 })} · {cp.messages.length} msgs</p>
            </div>
            <button
              onClick={() => rollbackToCheckpoint(cp.id)}
              className="shrink-0 ml-2 px-2.5 py-1.5 rounded-md text-xs text-primary hover:bg-primary/10 transition-colors"
            >
              {t("chat.rollback", "Rollback")}
            </button>
          </div>
        ))
      )}
    </div>
  );

  return (
    <div className="flex flex-1 min-h-0 relative">
      {/* Chat Window */}
      <div className="flex flex-1 flex-col min-w-0">
        {/* Chat header */}
        <div className="flex flex-col shrink-0">
          <div className="h-12 flex items-center gap-1.5 lg:gap-2 min-w-0 px-3 lg:px-4 border-b border-border">
            <button onClick={(e) => { e.stopPropagation(); toggleSidebar(); if (isMobile) { setRightPanelOpen(false); setShowCheckpoints(false); } }} className="shrink-0 p-2 hover:bg-muted/50 active:bg-muted transition-colors text-muted-foreground touch-manipulation" aria-label="Toggle Sidebar">
              <PanelLeft className="w-4 h-4" />
            </button>
            {(selectedAgent || storeAgentIcon) && (
              (selectedAgent?.logo || (!selectedAgent && SOULMATE_LOGO))
                ? <img src={selectedAgent?.logo || SOULMATE_LOGO} alt={selectedAgent?.name || 'SoulMate'} className="w-5 h-5 rounded object-contain shrink-0" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
                : <span className="text-sm shrink-0">{selectedAgent?.icon || storeAgentIcon}</span>
            )}
            <div className="min-w-0 flex-1">
              {editingTitle ? (
                <input
                  ref={titleInputRef}
                  value={editTitleValue}
                  onChange={e => setEditTitleValue(e.target.value)}
                  onBlur={saveTitle}
                  onKeyDown={e => { if (e.key === 'Enter') saveTitle(); if (e.key === 'Escape') setEditingTitle(false); }}
                  className="font-medium text-sm bg-transparent border-b border-primary outline-none px-0.5 min-w-0 max-w-[140px] lg:max-w-[200px]"
                />
              ) : (
                <span
                  className={`font-medium text-sm truncate block ${selectedSession?.id ? 'cursor-pointer hover:text-primary' : ''}`}
                  onClick={startEditTitle}
                  title={selectedSession?.id ? 'Click to rename' : undefined}
                >
                  {(() => { const fallbackTitle = messages.find(m => m.role === 'user')?.parts?.find((p: any) => p.type === 'text')?.text?.slice(0, 30) || ''; return storeSessionName || selectedSession?.name || selectedSession?.title || fallbackTitle || (selectedAgent || storeAgentName ? `${selectedAgent?.name || storeAgentName} ${t('chat.newSession')}` : t('chat.newChat')); })()}
                </span>
              )}
            </div>
            {(selectedAgent || storeAgentName) && <span className="text-[10px] lg:text-xs text-muted-foreground px-1 lg:px-1.5 py-0.5 rounded bg-muted shrink-0 truncate max-w-[80px] lg:max-w-none">{selectedAgent?.name || storeAgentName}</span>}
            <button onClick={(e) => { e.stopPropagation(); toggleRightPanel(); setShowCheckpoints(false); if (isMobile && sidebarOpen) toggleSidebar(); }} className="shrink-0 p-2 hover:bg-muted/50 active:bg-muted transition-colors text-muted-foreground touch-manipulation" aria-label="Toggle Workspace">
              <PanelLeft className="w-4 h-4 scale-x-[-1]" />
            </button>
          </div>
          {/* Collapse handle — always visible on title row border */}
          <div className="relative">
            <button
              onClick={() => setShowViewTabs(!showViewTabs)}
              className="absolute left-1/2 -translate-x-1/2 -top-[9px] z-10 p-0.5 rounded-full bg-background border border-border hover:bg-muted/50 active:bg-muted transition-colors text-muted-foreground touch-manipulation"
              title={showViewTabs ? '收起视图切换' : '展开视图切换'}
            >
              <ChevronDown className={`w-3.5 h-3.5 transition-transform duration-200 ${showViewTabs ? 'rotate-180' : 'rotate-0'}`} />
            </button>
          </div>
          {/* Chat view toggle — 裙摆式分段视图（可折叠） */}
          {showViewTabs && <>
          <SkirtTabs
            tabs={[
              { id: 'messages', title: t('chat.viewSessions', '会话') },
              { id: 'images', title: t('chat.viewImages', '图片') },
              { id: 'videos', title: t('chat.viewVideos', '视频') },
              { id: 'audio', title: t('chat.viewAudio', '音频') },
              { id: 'docs', title: t('chat.viewDocs', '文档') },
              { id: 'others', title: t('chat.viewOthers', '其他') },
              { id: 'links', title: t('chat.viewLinks', '链接') },
              { id: 'dates', title: t('chat.viewDates', '日期') },
            ]}
            activeTabId={chatView}
            onTabChange={(id: string) => setChatView(id as any)}
            renderTabContent={(tab: any, isActive: boolean) => (
              <>
                {tab.id === 'messages' && <MessageSquare className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'images' && <ImageIcon className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'videos' && <Video className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'audio' && <Music className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'docs' && <FileText className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'others' && <Paperclip className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'links' && <Link2 className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'dates' && <CalendarDays className="w-3.5 h-3.5 shrink-0" />}
                <span className="hidden lg:inline truncate text-xs">{tab.title}</span>
                {tab.id === 'docs' && docAttachments.length > 0 && (
                  <span className={`shrink-0 text-[10px] px-1.5 py-0 rounded-full font-medium min-w-[18px] text-center ${isActive ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>{docAttachments.length}</span>
                )}
                {tab.id === 'others' && otherAttachments.length > 0 && (
                  <span className={`shrink-0 text-[10px] px-1.5 py-0 rounded-full font-medium min-w-[18px] text-center ${isActive ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>{otherAttachments.length}</span>
                )}
                {tab.id === 'images' && imageAttachments.length > 0 && (
                  <span className={`shrink-0 text-[10px] px-1.5 py-0 rounded-full font-medium min-w-[18px] text-center ${isActive ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>{imageAttachments.length}</span>
                )}
                {tab.id === 'videos' && videoAttachments.length > 0 && (
                  <span className={`shrink-0 text-[10px] px-1.5 py-0 rounded-full font-medium min-w-[18px] text-center ${isActive ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>{videoAttachments.length}</span>
                )}
                {tab.id === 'audio' && audioAttachments.length > 0 && (
                  <span className={`shrink-0 text-[10px] px-1.5 py-0 rounded-full font-medium min-w-[18px] text-center ${isActive ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>{audioAttachments.length}</span>
                )}
                {tab.id === 'links' && linkItems.length > 0 && (
                  <span className={`shrink-0 text-[10px] px-1.5 py-0 rounded-full font-medium min-w-[18px] text-center ${isActive ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>{linkItems.length}</span>
                )}
              </>
            )}
          />
          </>}
        </div>



        {/* Messages */}
        <div ref={scrollRef} onScroll={(e) => {
          const el = e.currentTarget;
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          setShowScrollDown(!atBottom);
        }} className="flex-1 overflow-y-auto px-3 lg:px-6 py-3 lg:py-4 space-y-3 lg:space-y-4 chat-scrollbar relative">
                    {/* Docs view — 文档（pdf/docx/xlsx/pptx/md/txt，三段式卡片） */}
          {chatView === 'docs' && (
            <div className="space-y-2">
              {docAttachments.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
                  <FileText className="w-8 h-8 mb-2 opacity-40" />
                  <p className="text-sm">暂无文档</p>
                </div>
              ) : (
                docAttachments.map((f, i) => (

                  <div key={i} className={`flex gap-2 lg:gap-3 ${f.role === 'user' ? 'justify-end' : ''}`}>
                    {f.role === 'agent' && (
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Bot className="w-4 h-4 text-primary" />
                      </div>
                    )}
                    <div className="max-w-[85%] lg:max-w-[70%] rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                        <span className="text-[10px] text-muted-foreground/60">
                          {f.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                        </span>
                      </div>
                      <div className="px-3 py-2.5 flex items-center gap-2">
                        <FileText className="w-5 h-5 text-muted-foreground shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium truncate">{f.name}</p>
                          {f.mimeType && <p className="text-[10px] text-muted-foreground/60">{f.mimeType}</p>}
                        </div>
                      </div>
                      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                        {renderAttachmentActions(f)}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Others view — 其他文件（zip/7z/代码/二进制/未知兜底，三段式卡片） */}
          {chatView === 'others' && (
            <div className="space-y-2">
              {otherAttachments.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
                  <Paperclip className="w-8 h-8 mb-2 opacity-40" />
                  <p className="text-sm">暂无其他文件</p>
                </div>
              ) : (
                otherAttachments.map((f, i) => (

                  <div key={i} className={`flex gap-2 lg:gap-3 ${f.role === 'user' ? 'justify-end' : ''}`}>
                    {f.role === 'agent' && (
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Bot className="w-4 h-4 text-primary" />
                      </div>
                    )}
                    <div className="max-w-[85%] lg:max-w-[70%] rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                        <span className="text-[10px] text-muted-foreground/60">
                          {f.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                        </span>
                      </div>
                      <div className="px-3 py-2.5 flex items-center gap-2">
                        <Paperclip className="w-5 h-5 text-muted-foreground shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium truncate">{f.name}</p>
                          {f.mimeType && <p className="text-[10px] text-muted-foreground/60">{f.mimeType}</p>}
                        </div>
                      </div>
                      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                        {renderAttachmentActions(f)}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Images view — 图片（三段式卡片：时间在上+内容+功能按钮在下） */}
          {chatView === 'images' && (
            <div className="space-y-2">
              {imageAttachments.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
                  <ImageIcon className="w-8 h-8 mb-2 opacity-40" />
                  <p className="text-sm">暂无图片</p>
                </div>
              ) : (
                imageAttachments.map((f, i) => (
                  <div key={i} className={`flex gap-2 lg:gap-3 ${f.role === 'user' ? 'justify-end' : ''}`}>
                    {f.role === 'agent' && (
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Bot className="w-4 h-4 text-primary" />
                      </div>
                    )}
                    <div className="max-w-[85%] lg:max-w-[70%] rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                      {/* 时间在上 */}
                      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                        <span className="text-[10px] text-muted-foreground/60">
                          {f.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                        </span>
                      </div>
                      {/* 图片内容（点击预览） */}
                      <div className="relative cursor-pointer group" onClick={async () => {
                        const store = useAppStore.getState();
                        let dataUrl: string | undefined;
                        if (f.data) {
                          dataUrl = `data:${f.mimeType || 'image/png'};base64,${f.data}`;
                        } else if (f.filePath) {
                          try {
                            const apiBase = getApiUrl();
                            const resp = await fetch(`${apiBase}/api/file?path=${encodeURIComponent(f.filePath)}`);
                            if (resp.ok) {
                              const data = await resp.json();
                              const b64 = btoa(unescape(encodeURIComponent(data.content || '')));
                              dataUrl = `data:${f.mimeType || 'image/png'};base64,${b64}`;
                            }
                          } catch {}
                        }
                        if (dataUrl) {
                          store.setPendingFilePreview({ url: dataUrl, name: f.name, mimeType: f.mimeType });
                          store.setRightPanelOpen(true);
                        }
                      }}>
                        {f.data ? (
                          <img src={`data:${f.mimeType || 'image/png'};base64,${f.data}`} alt={f.name} className="w-full max-h-80 object-contain bg-black/5" />
                        ) : (
                          <div className="w-full h-40 flex items-center justify-center">
                            <ImageIcon className="w-8 h-8 opacity-30" />
                          </div>
                        )}
                        <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/60 to-transparent p-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                          <p className="text-[10px] text-white truncate">{f.name}</p>
                        </div>
                      </div>
                      {/* 功能按钮在下 */}
                      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                        {renderAttachmentActions(f)}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Videos view — 视频（三段式卡片：时间在上+播放器+功能按钮在下） */}
          {chatView === 'videos' && (
            <div className="space-y-2">
              {videoAttachments.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
                  <Video className="w-8 h-8 mb-2 opacity-40" />
                  <p className="text-sm">暂无视频</p>
                </div>
              ) : (
                videoAttachments.map((f, i) => (
                  <div key={i} className={`flex gap-2 lg:gap-3 ${f.role === 'user' ? 'justify-end' : ''}`}>
                    {f.role === 'agent' && (
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Bot className="w-4 h-4 text-primary" />
                      </div>
                    )}
                    <div className="max-w-[85%] lg:max-w-[70%] rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                        <span className="text-[10px] text-muted-foreground/60">
                          {f.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                        </span>
                      </div>
                      <div className="px-3 py-2.5 flex items-center gap-2">
                        <Video className="w-5 h-5 text-muted-foreground shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium truncate">{f.name}</p>
                          {f.mimeType && <p className="text-[10px] text-muted-foreground/60">{f.mimeType}</p>}
                        </div>
                      </div>
                      {f.data && (
                        <video controls className="w-full max-h-64 bg-black" preload="metadata">
                          <source src={`data:${f.mimeType || 'video/mp4'};base64,${f.data}`} />
                        </video>
                      )}
                      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                        {renderAttachmentActions(f)}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Audio view — 音频（三段式卡片：时间在上+播放器+功能按钮在下） */}
          {chatView === 'audio' && (
            <div className="space-y-2">
              {audioAttachments.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
                  <Music className="w-8 h-8 mb-2 opacity-40" />
                  <p className="text-sm">暂无音频</p>
                </div>
              ) : (
                audioAttachments.map((f, i) => (
                  <div key={i} className={`flex gap-2 lg:gap-3 ${f.role === 'user' ? 'justify-end' : ''}`}>
                    {f.role === 'agent' && (
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Bot className="w-4 h-4 text-primary" />
                      </div>
                    )}
                    <div className="max-w-[85%] lg:max-w-[70%] rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                        <span className="text-[10px] text-muted-foreground/60">
                          {f.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                        </span>
                      </div>
                      <div className="px-3 py-2.5 flex items-center gap-2">
                        <Music className="w-5 h-5 text-muted-foreground shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium truncate">{f.name}</p>
                          {f.mimeType && <p className="text-[10px] text-muted-foreground/60">{f.mimeType}</p>}
                        </div>
                      </div>
                      {f.data && (
                        <div className="px-3 pb-2">
                          <audio controls className="w-full" preload="metadata">
                            <source src={`data:${f.mimeType || 'audio/mpeg'};base64,${f.data}`} />
                          </audio>
                        </div>
                      )}
                      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                        {renderAttachmentActions(f)}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Links view — 链接（三段式卡片：时间在上+链接信息+功能按钮在下） */}
          {chatView === 'links' && (
            <div className="space-y-2">
              {linkItems.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
                  <Link2 className="w-8 h-8 mb-2 opacity-40" />
                  <p className="text-sm">暂无链接</p>
                </div>
              ) : (
                linkItems.map((link, i) => (
                  <div key={i} className={`flex gap-2 lg:gap-3 ${link.role === 'user' ? 'justify-end' : ''}`}>
                    {link.role === 'agent' && (
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Bot className="w-4 h-4 text-primary" />
                      </div>
                    )}
                    <div className="max-w-[85%] lg:max-w-[70%] rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                        <span className="text-[10px] text-muted-foreground/60">
                          {link.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                        </span>
                      </div>
                      <div className="px-3 py-2.5 flex items-center gap-2">
                        <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                          <Link2 className="w-4 h-4 text-primary" />
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium truncate">{link.title || link.url}</p>
                          <p className="text-[10px] text-muted-foreground/60 truncate">{link.url}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                        <button onClick={() => jumpToMessage(link.messageId)} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors" title="跳转到会话中此消息">
                          <LocateFixed className="w-3 h-3" />
                          跳转
                        </button>
                        <a href={link.url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 px-2 py-1 rounded text-xs text-primary hover:bg-primary/10 transition-colors">
                          <Link2 className="w-3 h-3" />
                          打开链接
                        </a>
                        <button onClick={() => navigator.clipboard.writeText(link.url)} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors">
                          <Copy className="w-3 h-3" />
                          复制链接
                        </button>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Dates view — 日期日历（微信式：有会话消息的日期突出显示，点击跳转当天第一条消息） */}
          {chatView === 'dates' && (() => {
            const dateMap = new Map(dateGroups.map(g => [g.date, g]));
            const y = calMonth.getFullYear();
            const m = calMonth.getMonth();
            const startOffset = (new Date(y, m, 1).getDay() + 6) % 7; // 周一起始
            const daysInMonth = new Date(y, m + 1, 0).getDate();
            const today = new Date().toISOString().slice(0, 10);
            const cells: ({ d: number; dateStr: string; group?: typeof dateGroups[number] } | null)[] = [];
            for (let i = 0; i < startOffset; i++) cells.push(null);
            for (let d = 1; d <= daysInMonth; d++) {
              const dateStr = `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
              cells.push({ d, dateStr, group: dateMap.get(dateStr) });
            }
            return (
              <div className="max-w-sm mx-auto rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                {/* 月份导航 */}
                <div className="flex items-center justify-between px-4 py-3 border-b border-border/30 bg-muted/20">
                  <button onClick={() => setCalMonth(new Date(y, m - 1, 1))} className="p-1.5 rounded hover:bg-muted/50 text-muted-foreground transition-colors" title="上个月">
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <span className="text-sm font-medium">{y}年{m + 1}月</span>
                  <button onClick={() => setCalMonth(new Date(y, m + 1, 1))} className="p-1.5 rounded hover:bg-muted/50 text-muted-foreground transition-colors" title="下个月">
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
                {/* 星期表头 */}
                <div className="grid grid-cols-7 px-2 pt-2 pb-1">
                  {['一', '二', '三', '四', '五', '六', '日'].map(w => (
                    <div key={w} className="text-center text-[10px] text-muted-foreground/60 py-1">{w}</div>
                  ))}
                </div>
                {/* 日期网格 */}
                <div className="grid grid-cols-7 gap-1 p-2">
                  {cells.map((cell, i) => {
                    if (!cell) return <div key={`pad-${i}`} />;
                    const has = !!cell.group;
                    const isToday = cell.dateStr === today;
                    return (
                      <button
                        key={cell.dateStr}
                        disabled={!has}
                        onClick={() => { const first = cell.group?.messages[0]; if (first) jumpToMessage(first.id); }}
                        className={`relative aspect-square rounded-lg flex flex-col items-center justify-center text-xs transition-colors ${has ? 'bg-primary/15 text-primary font-semibold hover:bg-primary/25 cursor-pointer' : 'text-muted-foreground/40'} ${isToday ? 'ring-1 ring-primary' : ''}`}
                        title={has ? `${cell.dateStr}：${cell.group!.count}条消息，点击跳转` : cell.dateStr}
                      >
                        {cell.d}
                        {has && <span className="absolute bottom-1 w-1 h-1 rounded-full bg-primary" />}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })()}

{/* Messages view */}
          {chatView === 'messages' && (<>
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full max-w-2xl mx-auto">
              <h2 className="text-xl lg:text-2xl font-semibold mb-4 lg:mb-8">{t("chat.welcomeMessage")}</h2>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 lg:gap-3 w-full">
                {[
                  { icon: '📝', label: t('chat.quickReqDoc'), desc: t('chat.quickReqDocDesc') },
                  { icon: '🎨', label: t('chat.quickPrototype'), desc: t('chat.quickPrototypeDesc') },
                  { icon: '📊', label: t('chat.quickDataAnalysis'), desc: t('chat.quickDataAnalysisDesc') },
                  { icon: '💻', label: t('chat.quickCoding'), desc: t('chat.quickCodingDesc') },
                  { icon: '📋', label: t('chat.quickProjectPlan'), desc: t('chat.quickProjectPlanDesc') },
                  { icon: '🧪', label: t('chat.quickTestCase'), desc: t('chat.quickTestCaseDesc') },
                  { icon: '📄', label: t('chat.quickSolution'), desc: t('chat.quickSolutionDesc') },
                  { icon: '🔍', label: t('chat.quickKnowledgeSearch'), desc: t('chat.quickKnowledgeSearchDesc') },
                ].map(item => (
                  <button key={item.label} onClick={() => setSmartPromptTask(item.desc)}
                    className="flex flex-col items-center gap-1.5 lg:gap-2 p-3 lg:p-4 rounded-xl border bg-card hover:bg-muted/80 hover:border-primary/30 active:bg-muted transition-all group touch-manipulation">
                    <span className="text-xl lg:text-2xl">{item.icon}</span>
                    <span className="text-xs lg:text-sm font-medium">{item.label}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map(msg => (
            <div key={msg.id} id={`msg-${msg.id}`} className={`flex gap-2 lg:gap-3 rounded-xl transition-all duration-300 ${msg.role === 'user' ? 'justify-end' : ''}`}>
              {msg.role === 'agent' && (
                <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                  <Bot className="w-4 h-4 text-primary" />
                </div>
              )}
              <div
                className={`max-w-[85%] lg:max-w-[70%] rounded-xl px-3 lg:px-4 py-2 lg:py-2.5 text-sm ${msg.role === 'user' ? 'text-foreground' : 'bg-card text-card-foreground border border-border'}`}
                style={msg.role === 'user' ? { backgroundColor: 'color-mix(in srgb, var(--color-thinking-border) 15%, transparent)', borderColor: 'color-mix(in srgb, var(--color-thinking-border) 25%, transparent)', borderWidth: '1px', borderStyle: 'solid' } : undefined}
              >
                {/* ── 时间戳（消息内容上方）── */}
                <div className="text-[10px] text-muted-foreground/40 mb-1">
                  {msg.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 })}
                </div>
                {/* ── 思考过程区块（仅 agent 消息，有 thinking 数据时渲染）── */}
                {showThinking && msg.role === 'agent' && msg.thinking && msg.thinking.length > 0 && (
                  <ThinkingBlockComponent
                    blocks={msg.thinking}
                    isStreaming={msg.source === 'streaming' && msg.thinking.some(t => !t.isComplete)}
                  />
                )}
                {/* ── 工具调用区块（仅 agent 消息，有 toolCalls 数据时渲染）── */}
                {showThinking && msg.role === 'agent' && msg.toolCalls && msg.toolCalls.length > 0 && (
                  <ToolCallBlockComponent toolCalls={msg.toolCalls} />
                )}
                {/* ── 记忆marker badge（kilocode #9："本回复用了记忆"，消息级可审计）── */}
                {msg.role === 'agent' && <MemoryMarkerBadge marker={msg.memoryMarker} />}
                {msg.parts.map((p, i) => (
                  <div key={i}>
                    {p.type === 'text' && <MarkdownContent content={p.text || ''} onCodeApply={(code: string) => {
                      // Handle code apply - 复制到剪贴板，HTTP环境下用fallback
                      copyToClipboard(code);
                    }} />}
                    {p.type === 'text' && msg.role === 'agent' && <SecretScanBadge text={p.text || ''} minRisk="high" />}
                    {p.type === 'image' && p.data && <img src={`data:${p.mime_type || 'image/png'};base64,${p.data}`} alt={p.name || 'image'} className="max-w-xs w-auto max-h-64 rounded-lg mt-1 object-contain" />}
                    {p.type === 'audio' && (
                      <div className="mt-2 rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                        {/* 日期在上（与文件视图音频卡片同布局，3e 统一布局铁律） */}
                        <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                          <span className="text-[10px] text-muted-foreground/60">
                            {msg.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                          </span>
                        </div>
                        {/* 音频信息 */}
                        <div className="px-3 py-2.5 flex items-center gap-2">
                          <Music className="w-5 h-5 text-muted-foreground shrink-0" />
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-medium truncate">{p.name || 'voice'}</p>
                            {p.mime_type && <p className="text-[10px] text-muted-foreground/60">{p.mime_type}</p>}
                          </div>
                        </div>
                        {/* 播放器 */}
                        {p.data && (
                          <div className="px-3 pb-2">
                            <audio controls className="w-full" preload="metadata">
                              <source src={`data:${p.mime_type || 'audio/wav'};base64,${p.data}`} />
                            </audio>
                          </div>
                        )}
                        {/* 功能按钮在下 */}
                        <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                          {renderAttachmentActions({ name: p.name || 'voice', data: p.data, mimeType: p.mime_type })}
                        </div>
                      </div>
                    )}
                    {p.type === 'choice' && (p.choices || p.questions) && (
                      <TaskChoiceMenu
                        question={p.text || "请选择："}
                        options={p.choices || []}
                        questions={p.questions}
                        onSelect={(id, label) => {
                          const send = (window as any).__openmate_send__;
                          if (send) send(label);
                        }}
                        onCustomSubmit={(text) => {
                          const send = (window as any).__openmate_send__;
                          if (send) send(text);
                        }}
                        onSubmitAnswers={(text) => {
                          // P1多问题/多选：一次提交全部作答（格式=每行『问题：选项』）
                          const send = (window as any).__openmate_send__;
                          if (send) send(text);
                        }}
                        disabled={false}
                      />
                    )}
                    {p.type === 'file' && <div className="mt-2 rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                      {/* 日期在上 */}
                      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                        <span className="text-[10px] text-muted-foreground/60">
                          {msg.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                        </span>
                      </div>
                      {/* 文件信息 */}
                      <div className="px-3 py-2.5 flex items-center gap-2">
                        <FileText className="w-5 h-5 text-muted-foreground shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium truncate">{p.name || 'file'}</p>
                          {p.mime_type && <p className="text-[10px] text-muted-foreground/60">{p.mime_type}</p>}
                        </div>
                      </div>
                      {/* 功能按钮在下 */}
                      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                        <button onClick={(e) => {
                          e.stopPropagation();
                          const store = useAppStore.getState();
                          const dataUrl = p.data ? `data:${p.mime_type || 'application/octet-stream'};base64,${p.data}` : undefined;
                          if (dataUrl) {
                            store.setPendingFilePreview({ url: dataUrl, name: p.name || 'File Preview', mimeType: p.mime_type });
                            store.setRightPanelOpen(true);
                          }
                        }} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-primary hover:bg-primary/10 transition-colors">
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></svg>
                          预览
                        </button>
                        {p.data && <button onClick={(e) => {
                          e.stopPropagation();
                          const byteChars = atob(p.data!);
                          const bytes = new Uint8Array(byteChars.length);
                          for (let i = 0; i < byteChars.length; i++) bytes[i] = byteChars.charCodeAt(i);
                          const blob = new Blob([bytes], { type: p.mime_type || 'application/octet-stream' });
                          const url = URL.createObjectURL(blob);
                          const a = document.createElement('a'); a.href = url; a.download = p.name || 'file'; a.click(); URL.revokeObjectURL(url);
                        }} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors">
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                          下载
                        </button>}
                        <button onClick={(e) => {
                          e.stopPropagation();
                          if (p.data) { navigator.clipboard.writeText(atob(p.data!)); }
                        }} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors">
                          <Copy className="w-3 h-3" />
                          复制
                        </button>
                      </div>
                    </div>}
                  </div>
                ))}
                {/* Multi-file diff view for agent messages with file changes */}
                {msg.role === 'agent' && msg.fileChanges && msg.fileChanges.length > 0 && (
                  <MultiFileDiff
                    files={msg.fileChanges}
                    onAccept={(path, content) => {
                      // Handle accept - copy to clipboard
                      copyToClipboard(content);
                    }}
                    onReject={(path) => {
                      console.log('Rejected:', path);
                    }}
                    onAcceptAll={() => {
                      console.log('Accepted all files');
                    }}
                    onRejectAll={() => {
                      console.log('Rejected all files');
                    }}
                  />
                )}
                {/* AI message action bar */}
                {msg.role === 'agent' && (
                  <div className="flex items-center gap-0.5 mt-1.5 -mb-1">
                    <button onClick={() => handleCopy(msg)} className="p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-muted-foreground transition-colors" title="复制">
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => handleReadAloud(msg)} className="p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-muted-foreground transition-colors" title="朗读">
                      <Volume2 className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => handleFeedback(msg, 'like')} className="p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-green-500 transition-colors" title="喜欢">
                      <ThumbsUp className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => handleFeedback(msg, 'dislike')} className="p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-red-500 transition-colors" title="不喜欢">
                      <ThumbsDown className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => handleShare(msg)} className="p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-muted-foreground transition-colors" title="转发">
                      <Share2 className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => handleRegenerate(msg.id)} className="p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-muted-foreground transition-colors" title="重新生成">
                      <RefreshCw className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => saveCheckpoint(msg.id)} className="p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-muted-foreground transition-colors" title="保存检查点">
                      <Bookmark className="w-3.5 h-3.5" />
                    </button>
                    <button className="p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-muted-foreground transition-colors" title="更多">
                      <MoreHorizontal className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}
                {/* User message action bar */}
                {msg.role === 'user' && (
                  <div className="flex items-center gap-0.5 mt-1.5 -mb-1 justify-end">
                    <button onClick={() => handleCopy(msg)} className="p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-muted-foreground transition-colors" title="复制">
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => handleShare(msg)} className="p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-muted-foreground transition-colors" title="分享">
                      <Share2 className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => handleEditMessage(msg)} className="p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-muted-foreground transition-colors" title="修改">
                      <RotateCcw className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => handleFavorite(msg)} className="p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-yellow-500 transition-colors" title="收藏">
                      <Bookmark className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => handleDeleteMessage(msg.id)} className="p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-red-500 transition-colors" title="删除">
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}
              </div>
              {msg.role === 'user' && (
                <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center shrink-0">
                  <User className="w-4 h-4 text-primary-foreground" />
                </div>
              )}
            </div>
          ))}
          {loading && (
            <div className="flex gap-2 lg:gap-3">
              <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center"><Bot className="w-4 h-4 text-primary" /></div>
              <div className="bg-muted rounded-xl px-4 py-2.5"><Loader2 className="w-4 h-4 animate-spin text-muted-foreground" /></div>
            </div>
          )}
          {/* Scroll-to-bottom FAB */}
          {showScrollDown && (
            <button
              onClick={() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })}
              className="sticky bottom-2 left-1/2 -translate-x-1/2 flex items-center justify-center w-8 h-8 rounded-full bg-background border border-border shadow-md hover:bg-muted active:scale-95 transition-all z-10"
              aria-label="Scroll to bottom"
            >
              <ChevronDown className="w-4 h-4 text-muted-foreground" />
            </button>
          )}
          </>)} {/* End Messages view */}

        </div>
        <div className="px-3 lg:px-4 pt-3 pb-6 shrink-0">
          {readingCount > 0 && (
            <div className="flex items-center gap-1.5 mb-2 px-2 py-1.5 bg-blue-500/10 text-blue-500 rounded text-xs">
              <Loader2 className="w-3 h-3 animate-spin" />
              <span>正在读取文件（{readingCount}个），读取完成前无法发送...</span>
            </div>
          )}
          {isRecording && (
            <div className="flex items-center gap-1.5 mb-2 px-2 py-1.5 bg-red-500/10 text-red-500 rounded text-xs">
              <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
              <span>正在录音… {recordingSeconds}s（再点麦克风停止，停止后作为语音附件随消息发送）</span>
            </div>
          )}
          {audioError && (
            <div className="flex items-center gap-1.5 mb-2 px-2 py-1.5 bg-amber-500/10 text-amber-600 rounded text-xs">
              <span className="flex-1">{audioError}</span>
              <button onClick={() => setAudioError(null)} title="关闭"><X className="w-3 h-3" /></button>
            </div>
          )}
          {attachments.length > 0 && (
            <div className="flex gap-2 mb-2 flex-wrap">
              {attachments.map((a, i) => (
                <div key={i} className="flex items-center gap-1 px-2 py-1 bg-muted rounded text-xs">
                  {a.type === 'image' ? <ImageIcon className="w-3 h-3" /> : a.type === 'audio' ? <Music className="w-3 h-3" /> : <FileText className="w-3 h-3" />}
                  <span className="truncate max-w-20">{a.name}</span>
                  <button onClick={() => setAttachments(prev => prev.filter((_, j) => j !== i))}><X className="w-3 h-3" /></button>
                </div>
              ))}
            </div>
          )}
          <div className="space-y-2 min-h-[80px]">
            <input ref={fileRef} type="file" multiple className="hidden" onChange={handleFile} />
            <SmartPrompt
              hasAttachments={attachments.length > 0}
              isReadingFiles={readingCount > 0}
              initialTask={smartPromptTask}
              sessionFields={effectiveSessionId ? sessionDataMap.get(effectiveSessionId)?.promptFields : undefined}
              sessionId={effectiveSessionId || undefined}
              clearTrigger={clearTrigger}
              loadFieldsTrigger={loadFieldsTrigger}
              onFieldsChange={(newFields) => {
                setSmartTaskText(newFields.task || '');
                const sid = useAppStore.getState().activeSessionId;
                if (!sid) return;
                setSessionDataMap(prev => {
                  const next = new Map(prev);
                  const data = next.get(sid) || { messages: [], unreadCount: 0 };
                  next.set(sid, { ...data, promptFields: newFields });
                  return next;
                });
              }}
              onSend={(assembled) => {
                // P1插话（Khoj interrupt_queue/goose Steer）：任务运行中发送=插话排队注入，不拒绝
                const isSteer = loading;
                if (readingCount > 0) return; // 附件读取中，阻止发送防止空附件
                if (!assembled.trim() && attachments.length === 0) return;
                const text = assembled.trim();
                const userMsg: Message = { id: Date.now().toString(), role: 'user', parts: [{ type: 'text', text }, ...attachments], timestamp: new Date() };
                let currentSessionId = activeSessionIdFromStore;
                // 没有活跃 session 时，自动创建新会话并跳转
                if (!currentSessionId) {
                  // 直接从 store 取 agentId，不依赖 ref（ref 可能还没更新）
                  const storeAgentId = useAppStore.getState().activeAgentId;
                  const spAgentId = storeAgentId || 'soulmate';
                  const agent = agents.find((a: AgentInfo) => a.id === spAgentId);
                  const newId = `temp-${Date.now()}`;
                  const newSession = { id: newId, name: text.slice(0, 30) || '新会话', platform: 'hermes', agentId: spAgentId, createdAt: new Date().toISOString() } as Session;
                  // 更新 store：setActiveSession 是唯一的 activeSessionId 来源
                  useAppStore.getState().setActiveSession(newId, spAgentId === 'soulmate' ? null : spAgentId, { agentName: agent?.name || spAgentId, sessionName: text.slice(0, 30) || '新会话', _isNewSessionFlow: true });
                  currentSessionId = newId;
                  // 同步设置 selectedSession，让 header 标题立即更新
                  setSelectedSession(newSession);
                  selectedSessionRef.current = newSession;
                  // 同步更新 ref，让 messages 计算在当前渲染就能拿到新 sessionId
                  pendingSessionIdRef.current = newId;
                }
                updateSessionMessages(currentSessionId, prev => [...prev, userMsg]);
                // 立即更新标题为消息前20字（不等session迁移）
                const curName = selectedSessionRef.current?.name || '';
                const curId = selectedSessionRef.current?.id || '';
                const isDefaultName = curName.includes('新会话') || curName.includes('New Session') || curName.includes('新しいセッション');
                console.log('[title-fix] curId:', curId, 'curName:', curName, 'isDefault:', isDefaultName);
                if (selectedSessionRef.current && (curId.startsWith('temp-') || isDefaultName)) {
                  const autoTitle = text.slice(0, 20);
                  const updatedSession = { ...selectedSessionRef.current, name: autoTitle };
                  selectedSessionRef.current = updatedSession;
                  setSelectedSession(updatedSession);
                  // 只更新标题，绝不触碰 agentId（曾在此传 null 清掉 agent 身份，
                  // 导致点击任何 agent 新建的会话都被顶替成 soulmate——用户判死）
                  useAppStore.getState().setActiveSession(currentSessionId, useAppStore.getState().activeAgentId, { sessionName: autoTitle });
                }
                setAttachments([]); setLoading(true);
                // 计划模式添加前缀
                const messageText = agentMode === 'plan' ? `[PLAN MODE] ${text}` : text;
                streamingSessionIdRef.current = selectedSession?.id || null;
                // 始终走 connectSession + waitForConnection，避免僵尸WebSocket（readyState=OPEN但对端已死）
                // agent 身份来源：会话自带 > store > 空白入口默认 soulmate（默认只许在这一处）
                const spAgentId2 = useAppStore.getState().activeAgentId || (selectedSessionRef.current as { agentId?: string } | null)?.agentId || 'soulmate';
                connectSession(currentSessionId, spAgentId2, text.slice(0, 30));
                waitForConnection(currentSessionId, messageText, attachments, { steer: isSteer });
              }}
              isLoading={loading}
              placeholder={t("chat.inputPlaceholder", "输入任务，点 ✨ 展开字段（Enter 发送，Shift+Enter 换行）")}
              onFileClick={() => fileRef.current?.click()}
              onMicClick={toggleRecording}
              isRecording={isRecording}
              recordingSeconds={recordingSeconds}
              onPaste={handlePaste}
              footer={<>
                  <button
                    onClick={() => setAgentMode(prev => prev === 'plan' ? 'act' : 'plan')}
                    title={agentMode === 'plan' ? '切换到执行模式' : '切换到计划模式'}
                    className={`flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                      agentMode === 'plan' ? 'text-blue-400 hover:bg-blue-500/10' : 'text-green-400 hover:bg-green-500/10'
                    }`}
                  >
                    {agentMode === 'plan' ? <Brain className="w-4 h-4" /> : <Zap className="w-4 h-4" />}
                    <span className="hidden lg:inline">{agentMode === 'plan' ? 'Plan' : 'Act'}</span>
                  </button>
                  <button
                    onClick={() => setShowThinking(prev => !prev)}
                    className={`p-1.5 rounded transition-colors ${showThinking ? 'bg-muted/50 text-[var(--color-thinking-border)] hover:bg-muted/70' : 'text-muted-foreground/40 hover:bg-muted/30 hover:text-muted-foreground'}`}
                    title={showThinking ? '隐藏思考过程' : '显示思考过程'}
                  >
                    <Brain className="w-4 h-4" />
                  </button>
                  <button onClick={() => setClearTrigger(n => n + 1)} className="p-1.5 rounded hover:bg-muted/30 text-muted-foreground/40 hover:text-muted-foreground transition-colors" title="清空输入">
                    <RotateCcw className="w-4 h-4" />
                  </button>
                  <button onClick={() => { const next = !showCheckpoints; setShowCheckpoints(next); if (next) { setRightPanelOpen(false); if (isMobile && sidebarOpen) toggleSidebar(); } }} className="p-1.5 rounded hover:bg-muted/30 text-muted-foreground/40 hover:text-muted-foreground transition-colors relative" title="历史">
                    <Bookmark className="w-4 h-4" />
                    {checkpoints.length > 0 && <span className="absolute -top-1 -right-1 bg-primary text-primary-foreground text-[9px] rounded-full w-3.5 h-3.5 flex items-center justify-center">{checkpoints.length}</span>}
                  </button>
                  <ContextRing />
                  {/* Session cumulative stats */}
                  {sessionStats.msgCount > 0 && (
                    <span className="text-[9px] text-muted-foreground/30 whitespace-nowrap">
                      {sessionStats.msgCount}条 · ${sessionStats.totalCost.toFixed(3)}
                    </span>
                  )}
                  <button
                    onClick={() => { window.dispatchEvent(new CustomEvent('smart-prompt-send')); }}
                    disabled={attachments.length === 0 && !smartTaskText.trim() /* 附件与文字都为空时禁用，任一有内容可发送 */}
                    className="flex items-center justify-center w-9 h-9 rounded-full bg-primary text-primary-foreground hover:bg-primary/90 active:bg-primary/80 disabled:opacity-50 transition-colors"
                    title={loading ? "发送插话（任务间隙注入）" : "发送"}
                  >
                    {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                  </button>
              </>}
            />
          </div>
        </div>
      </div>
      {/* Checkpoints Panel — Sheet on mobile, inline overlay on desktop */}
      {isMobile ? (
        <Sheet open={showCheckpoints} onOpenChange={setShowCheckpoints}>
          <SheetContent side="right" size="sm" showCloseButton={false}>
            <SheetHeader className="border-b border-border pb-3">
              <div className="flex items-center justify-between">
                <SheetTitle className="flex items-center gap-2 text-sm">
                  <RotateCcw className="w-4 h-4" />
                  {t("chat.checkpoints", "Checkpoints")}
                </SheetTitle>
                <button onClick={() => setShowCheckpoints(false)} className="p-1 rounded hover:bg-muted">
                  <X className="w-4 h-4" />
                </button>
              </div>
            </SheetHeader>
            {renderCheckpointList()}
          </SheetContent>
        </Sheet>
      ) : (
        showCheckpoints && (
          <div className="absolute right-0 top-12 bottom-0 w-72 border-l border-border bg-card z-20 flex flex-col shadow-lg">
            <div className="h-12 shrink-0 flex items-center justify-between px-3 border-b border-border">
              <span className="text-sm font-semibold flex items-center gap-2">
                <RotateCcw className="w-4 h-4" />
                {t("chat.checkpoints", "Checkpoints")}
              </span>
              <button onClick={() => setShowCheckpoints(false)} className="p-1 rounded hover:bg-muted">
                <X className="w-4 h-4" />
              </button>
            </div>
            {renderCheckpointList()}
          </div>
        )
      )}

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={!!deleteConfirm}
        onClose={() => setDeleteConfirm(null)}
        title={t("chat.deleteSessionTitle", "删除会话")}
        description={t("chat.deleteSessionDesc", `确定要删除「${deleteConfirm?.name}」吗？`, { name: deleteConfirm?.name })}
        className="max-w-xs"
        footer={
          <>
            <button onClick={() => setDeleteConfirm(null)} className="px-4 py-2 rounded-lg text-sm bg-muted hover:bg-muted/80 text-foreground transition-colors">
              {t("chat.cancelDelete", "取消")}
            </button>
            <button onClick={() => { if (deleteConfirm) { deleteSession(deleteConfirm.id); setDeleteConfirm(null); } }} className="px-4 py-2 rounded-lg text-sm bg-red-600 hover:bg-red-700 text-white font-medium transition-colors">
              {t("chat.confirmDelete", "确认删除")}
            </button>
          </>
        }
      />

      {/* 网络等待交互卡 — kilocode人工拒绝窗口：断网park期间可见+可拒绝 */}
      <NetworkWaitBanner />

      {/* ACP审批弹窗 — human.approval.required事件触发 */}
      <AcpApprovalModal
        request={approvalRequest}
        onApprove={(requestId, comment) => sendApproval(requestId, 'approve', comment)}
        onReject={(requestId, comment) => sendApproval(requestId, 'reject', comment)}
        onClose={() => {/* 弹窗内部已通过sendApproval清理状态 */}}
      />
    </div>
  );
}
