'use client';
import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { getApiBaseUrl, getToken } from '@/lib/api-client';
import { useSidebar } from '@/components/ui/sidebar';
import { useAppStore } from '@/stores/app-store';
import { AIGroupsWorkspace, AIGroupsTaskBoard } from '@/components/ai-groups-workspace';
import {
  Users, Bot, Shield, Zap, User, Loader2,
  MessageSquare,
  ListChecks,
  PanelLeft, Settings, X,
  Plus, Trash2, Edit3, Check, XIcon, ChevronRight,
  StopCircle,
  Trophy, ArrowUp, ArrowDown, ArrowRight,
} from 'lucide-react';
import { useIsMobile } from '@/hooks/use-mobile';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { useAIGroupsStore, type AgentRole, type GroupMessage } from '@/stores/ai-groups-store';
import MarkdownContent from '@/components/markdown-content';
import { MessageBubble, favoriteToKnowledge } from '@/components/message-bubble';
import { SmartPrompt } from '@/components/smart-prompt';

/* ========== 角色配置 ========== */
const ROLE_ICONS: Record<string, any> = { advisor: Shield, executor: Zap, verifier: Bot, human: User };
const ROLE_COLORS: Record<string, string> = { advisor: 'text-yellow-400', executor: 'text-blue-400', verifier: 'text-green-400', human: 'text-purple-400' };
const ROLE_BG_COLORS: Record<string, string> = { advisor: 'bg-yellow-500/20', executor: 'bg-blue-500/20', verifier: 'bg-green-500/20', human: 'bg-purple-500/20' };


/* ========== Agent 头像颜色池 ========== */
const AGENT_AVATAR_COLORS = [
  'bg-rose-500/20 text-rose-400', 'bg-sky-500/20 text-sky-400', 'bg-emerald-500/20 text-emerald-400',
  'bg-amber-500/20 text-amber-400', 'bg-violet-500/20 text-violet-400', 'bg-pink-500/20 text-pink-400',
  'bg-teal-500/20 text-teal-400', 'bg-orange-500/20 text-orange-400',
];

/** 根据索引获取 Agent 头像背景色 */
function getAgentAvatarColor(index: number) {
  return AGENT_AVATAR_COLORS[index % AGENT_AVATAR_COLORS.length];
}

// 裙摆边缘钮路径（复刻 SkirtTabs genTabPath 几何：TOP_R=12 圆角 + SKIRT=10 下摆外翻）
// 横向 tab 生成后旋转 -90° 贴到竖直面板边缘：圆角朝外、下摆沿边缘翻出
export default function AIGroupsClient() {
  const { t } = useTranslation();
  const { toggleSidebar, open: sidebarOpen } = useSidebar();
  const toggleRightPanel = useAppStore((s) => s.toggleRightPanel);
  const setRightPanelOpen = useAppStore((s) => s.setRightPanelOpen);
  const rightPanelOpen = useAppStore((s) => s.rightPanelOpen);
  const isMobile = useIsMobile();

  /* ========== 从 Store 读取状态 ========== */
  const groups = useAIGroupsStore((s) => s.groups);
  const selectedGroup = useAIGroupsStore((s) => s.selectedGroup);
  const messages = useAIGroupsStore((s) => s.messages);
  const tasks = useAIGroupsStore((s) => s.tasks);
  const sendingMessage = useAIGroupsStore((s) => s.sendingMessage);
  const setMessages = useAIGroupsStore((s) => s.setMessages);
  const setSendingMessage = useAIGroupsStore((s) => s.setSendingMessage);
  const selectGroup = useAIGroupsStore((s) => s.selectGroup);
  // WS 相关状态
  const wsConnected = useAIGroupsStore((s) => s.wsConnected);
  const wsTypingUsers = useAIGroupsStore((s) => s.wsTypingUsers);
  const sendGroupMessage = useAIGroupsStore((s) => s.sendGroupMessage);
  // Agent 管理状态
  const showAddAgent = useAIGroupsStore((s) => s.showAddAgent);
  const setShowAddAgent = useAIGroupsStore((s) => s.setShowAddAgent);
  const editingAgent = useAIGroupsStore((s) => s.editingAgent);
  const setEditingAgent = useAIGroupsStore((s) => s.setEditingAgent);
  const newAgent = useAIGroupsStore((s) => s.newAgent);
  const setNewAgent = useAIGroupsStore((s) => s.setNewAgent);
  const addAgent = useAIGroupsStore((s) => s.addAgent);
  // 讨论相关状态
  const discussionLoading = useAIGroupsStore((s) => s.discussionLoading);
  const startDiscussion = useAIGroupsStore((s) => s.startDiscussion);
  const cancelDiscussion = useAIGroupsStore((s) => s.cancelDiscussion);

  /* ========== 本地 UI 状态 ========== */
  const [showGroupPanel, setShowGroupPanel] = useState(false);
  const [editDraft, setEditDraft] = useState(''); // 修改消息草稿（注入 SmartPrompt）
  const [sendReady, setSendReady] = useState(false); // 输入框是否有内容（发送钮可用态）
  const [replyMode, setReplyMode] = useState<'plan' | 'act'>('act'); // 接话模式：act=自动自由接话，plan=定点@接话（真后端 reply_mode）
  const [showThinking, setShowThinking] = useState(false); // 显示消息过程（溯源/行动报告）

  // 讨论直达：会话页"讨论"钮带 ?group=&topic= 进来 → 选群、自动发话题、自动发起讨论
  useEffect(() => {
    const run = async () => {
      if (typeof window === 'undefined') return;
      const params = new URLSearchParams(window.location.search);
      const topic = params.get('topic');
      const gid = params.get('group');
      if (!topic && !gid) return;
      window.history.replaceState({}, '', '/ai-groups'); // 清参数防刷新重放
      const s = useAIGroupsStore.getState();
      if (!s.groups || s.groups.length === 0) await s.fetchGroups();
      const list = useAIGroupsStore.getState().groups || [];
      const g = gid ? list.find(x => x.id === gid) : list[0];
      if (!g) return;
      await (s.selectGroup as any)(g);
      if (topic) {
        // 等 WS 连上（selectGroup 内建连接），最多 5s
        for (let i = 0; i < 25; i++) {
          if (useAIGroupsStore.getState().wsConnected) break;
          await new Promise(r => setTimeout(r, 200));
        }
        useAIGroupsStore.getState().sendGroupMessage(topic, 'comment'); // 自动把当前话题发布到群里
        setTimeout(() => useAIGroupsStore.getState().startDiscussion(topic, []), 800); // 自动发起讨论
      }
    };
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // 任务看板常驻面板（Paperclip 看板式）：桌面端消息流右侧常驻，移动端全屏切换
  const [tasksPanelOpen, setTasksPanelOpen] = useState(true);
  // 编辑 Agent 的临时状态
  const [editName, setEditName] = useState('');
  const [editRole, setEditRole] = useState('');
  const [editModel, setEditModel] = useState('');
  // 讨论触发 UI 状态

  const scrollRef = useRef<HTMLDivElement>(null);

  /* ========== 副作用：移动端面板互斥 ========== */
  useEffect(() => {
    if (isMobile && showGroupPanel && (sidebarOpen || rightPanelOpen)) {
      setShowGroupPanel(false);
    }
  }, [sidebarOpen, rightPanelOpen, isMobile, showGroupPanel]);

  /* ========== 副作用：移动端默认收起任务看板（移动端看板为全屏切换） ========== */
  useEffect(() => {
    if (isMobile) setTasksPanelOpen(false);
  }, [isMobile]);

  /* ========== 副作用：消息列表自动滚动到底部 ========== */
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }); }, [messages]);

  /* ========== 副作用：自动选择第一个群组 ========== */
  useEffect(() => {
    if (!selectedGroup && groups.length > 0) {
      selectGroup(groups[0]);
    }
  }, [selectedGroup, groups, selectGroup]);

  /* ========== 工具函数 ========== */
  const getAgentById = (id?: string) => (selectedGroup?.agents || []).find(a => a.agent_id === id);

  /* ========== 发送消息（通过 WS） ========== */
  // 发送文本（SmartPrompt onSend 调用；@目标由上方目标选择器/消息前缀决定）
  const sendText = (raw: string) => {
    const text = raw.trim();
    if (!text || !selectedGroup || !wsConnected) return;
    // 不本地乐观添加——服务端回显 user_message 统一渲染（防重复显示）
    sendGroupMessage(text, 'comment');
  };

  /* ========== 发起讨论 ========== */

  /* ========== 取消讨论 ========== */
  const handleCancelDiscussion = () => {
    // 通过 WS 发送 cancel_discussion 消息
    cancelDiscussion();
  };


  /* ========== Agent 编辑操作 ========== */
  const startEditAgent = (agent: AgentRole) => {
    setEditingAgent(agent.agent_id);
    setEditName(agent.name);
    setEditRole(agent.role);
    setEditModel(agent.model || '');
  };

  const cancelEditAgent = () => {
    setEditingAgent(null);
    setEditName('');
    setEditRole('');
    setEditModel('');
  };

  const saveEditAgent = async () => {
    if (!editingAgent || !selectedGroup) return;
    try {
      const token = getToken();
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;
      await fetch(`${getApiBaseUrl()}/api/ai-groups/${selectedGroup.id}/agents/${editingAgent}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ name: editName, role: editRole, model: editModel }),
      });
      // 重新加载群组数据
      selectGroup(selectedGroup);
      cancelEditAgent();
    } catch (e) {
      console.error('更新 Agent 失败:', e);
    }
  };

  /* ========== Agent 删除操作 ========== */
  const deleteAgent = async (agentId: string) => {
    if (!selectedGroup) return;
    try {
      const token = getToken();
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;
      await fetch(`${getApiBaseUrl()}/api/ai-groups/${selectedGroup.id}/agents/${agentId}`, {
        method: 'DELETE',
        headers,
      });
      // 重新加载群组数据
      selectGroup(selectedGroup);
    } catch (e) {
      console.error('删除 Agent 失败:', e);
    }
  };

  /* ========== 渲染 ========== */
  return (
    <div className="flex flex-1 flex-col min-h-0 relative">
      {/* ===== 聊天头部：群组名称 + WS 连接状态 ===== */}
      <div className="h-12 border-b border-border flex items-center px-2 lg:px-4 justify-between shrink-0">
        <div className="flex items-center gap-2">
          {/* 侧边栏切换按钮 */}
          <button onClick={(e) => { e.stopPropagation(); toggleSidebar(); if (isMobile) { setRightPanelOpen(false); setShowGroupPanel(false); } }}
            className="shrink-0 p-2 hover:bg-muted/50 active:bg-muted transition-colors text-muted-foreground touch-manipulation"
            aria-label="Toggle Sidebar">
            <PanelLeft className="w-4 h-4" />
          </button>
          {/* 群组图标和名称 */}
          <Users className="w-4 h-4 text-primary" />
          <span className="font-medium text-xs lg:text-sm">{selectedGroup?.name || t('aiGroups.selectGroup')}</span>
          {/* Agent 数量徽章 */}
          {selectedGroup && (
            <span className="text-xs text-muted-foreground px-1.5 py-0.5 rounded bg-muted">
              {selectedGroup.agents?.length || 0} Agent
            </span>
          )}
          {/* ===== WS 连接状态指示器 ===== */}
          <div className="flex items-center gap-1.5 ml-2">
            {wsConnected ? (
              <>
                <span className="relative flex h-2.5 w-2.5">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-green-500"></span>
                </span>
                <span className="text-[10px] text-green-500 hidden lg:inline">已连接</span>
              </>
            ) : (
              <>
                <span className="flex h-2.5 w-2.5 rounded-full bg-red-500"></span>
                <span className="text-[10px] text-red-500 hidden lg:inline">未连接</span>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {/* 任务看板开关（桌面端右侧常驻面板 / 移动端全屏任务视图） */}
          {selectedGroup && (
            <button onClick={(e) => { e.stopPropagation(); setTasksPanelOpen(v => !v); if (isMobile) setShowGroupPanel(false); }}
              className={`shrink-0 flex items-center gap-1 px-2 py-1.5 rounded-lg hover:bg-muted/50 active:bg-muted transition-colors touch-manipulation ${tasksPanelOpen ? 'text-primary' : 'text-muted-foreground'}`}
              aria-label="Toggle Task Board"
              title="任务看板">
              <ListChecks className="w-4 h-4" />
              <span className="text-[10px] font-medium">{tasks.length}</span>
            </button>
          )}
          {/* 群组管理按钮 */}
          {selectedGroup && (
            <button onClick={(e) => { e.stopPropagation(); setShowGroupPanel(true); if (isMobile) setRightPanelOpen(false); }}
              className="shrink-0 p-2 hover:bg-muted/50 active:bg-muted transition-colors text-muted-foreground touch-manipulation"
              aria-label="Group Settings">
              <Settings className="w-4 h-4" />
            </button>
          )}
          {/* 工作区面板切换 */}
          <button onClick={(e) => { e.stopPropagation(); toggleRightPanel(); if (isMobile) setShowGroupPanel(false); }}
            className="shrink-0 p-2 hover:bg-muted/50 active:bg-muted transition-colors text-muted-foreground touch-manipulation"
            aria-label="Toggle Workspace">
            <PanelLeft className="w-4 h-4 scale-x-[-1]" />
          </button>
        </div>
      </div>

      {/* ===== 消息列表区域 ===== */}
      {/* ===== 主区域：消息流 + 任务看板常驻（Paperclip 看板式） ===== */}
      <div className="flex flex-1 min-h-0">
      {/* 消息流列：移动端任务看板打开时隐藏 */}
      <div className={`flex flex-1 min-w-0 flex-col ${isMobile && tasksPanelOpen ? 'hidden' : ''}`}>
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 lg:px-6 py-3 lg:py-4 space-y-3 lg:space-y-4">
        {/* 未选择群组时的空状态 */}
        {!selectedGroup && (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
            <MessageSquare className="w-12 h-12 mb-4 opacity-40" />
            <p className="text-xs lg:text-sm">{t("aiGroups.selectOrCreateGroup")}</p>
          </div>
        )}
        {/* 群组已选但无消息时的欢迎状态 */}
        {selectedGroup && messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full max-w-md mx-auto text-center">
            <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center mb-4">
              <Users className="w-8 h-8 text-primary" />
            </div>
            <h3 className="text-lg font-semibold mb-2">{selectedGroup.name}</h3>
            <p className="text-xs lg:text-sm text-muted-foreground mb-4">{selectedGroup.description || t('aiGroups.multiAgentGroup')}</p>
            {/* 显示群组中的 Agent 成员 */}
            <div className="flex flex-wrap gap-2 justify-center">
              {(selectedGroup.agents || []).map((a, i) => {
                const Icon = ROLE_ICONS[a.role] || Bot;
                return (
                  <div key={i} className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg ${ROLE_BG_COLORS[a.role] || 'bg-muted'}`}>
                    <Icon className={`w-3.5 h-3.5 ${ROLE_COLORS[a.role]}`} />
                    <span className="text-xs font-medium">{a.name}</span>
                  </div>
                );
              })}
            </div>
            <p className="text-xs text-muted-foreground mt-6">{t("aiGroups.inputHint")}</p>
          </div>
        )}

        {/* ===== 消息列表渲染 ===== */}
        {messages.map(msg => {
          /* --- 系统消息：居中显示，灰色小字 --- */
          if (msg.role === 'system') {
            return (
              <div key={msg.id} className="flex justify-center">
                <div className="bg-muted/50 rounded-full px-3 py-1">
                  <p className="text-[11px] text-muted-foreground text-center">{msg.content}</p>
                </div>
              </div>
            );
          }

          /* --- 用户/Agent 消息：共用 MessageBubble 组件（与会话同一份样式代码，改动自动同步） --- */
          const isUser = msg.role === 'user';
          return (
            <MessageBubble key={msg.id} id={`msg-${msg.id}`} role={isUser ? 'user' : 'agent'} timestamp={msg.timestamp} name={isUser ? undefined : (msg.agent_name || 'Agent')}
              text={msg.content}
              actions={{
                onRegenerate: () => sendText(`@${msg.agent_name || msg.agent_id} 请重新回答上面的问题`),
                onCheckpoint: () => { favoriteToKnowledge(msg.content); },
                onEdit: () => {
                  setEditDraft(msg.content);
                  useAIGroupsStore.getState().setMessages(prev => prev.filter(m => m.id !== msg.id));
                },
                onDelete: () => {
                  useAIGroupsStore.getState().setMessages(prev => prev.filter(m => m.id !== msg.id));
                },
              }}>
              <MarkdownContent content={msg.content} onCodeApply={(code: string) => {
                navigator.clipboard?.writeText(code).catch(() => {});
              }} />
              {showThinking && msg.metadata && Object.keys(msg.metadata).length > 0 && (
                <div className="mt-1.5 text-[10px] text-muted-foreground/50 font-mono border-t border-border/40 pt-1 space-y-0.5">
                  {msg.metadata.trigger_msg_id && <div>溯源 trigger: {String(msg.metadata.trigger_msg_id)}</div>}
                  {msg.metadata.trace_id && <div>trace: {String(msg.metadata.trace_id)}</div>}
                  {msg.metadata.action_report && <div>过程: {typeof msg.metadata.action_report === 'string' ? msg.metadata.action_report : JSON.stringify(msg.metadata.action_report)}</div>}
                </div>
              )}
            </MessageBubble>
          );
        })}

        {/* 正在发送指示器 */}
        {sendingMessage && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center"><Bot className="w-4 h-4 text-primary" /></div>
            <div className="bg-muted rounded-xl px-4 py-2.5"><Loader2 className="w-4 h-4 animate-spin text-muted-foreground" /></div>
          </div>
        )}

        {/* 讨论进行中指示器 — Agent 们正在思考讨论 */}
        {discussionLoading && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-amber-500/20 flex items-center justify-center">
              <MessageSquare className="w-4 h-4 text-amber-400" />
            </div>
            <div className="bg-muted/70 rounded-xl px-3 lg:px-4 py-2.5 flex items-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin text-amber-400" />
              <span className="text-xs text-muted-foreground">Agent 正在思考讨论中...</span>
              <button onClick={handleCancelDiscussion}
                className="ml-1 px-2 py-0.5 rounded text-[11px] text-red-400 hover:bg-red-500/10 transition-colors"
                title="停止讨论">
                停止讨论
              </button>
            </div>
          </div>
        )}

        {/* 正在输入指示器（来自其他用户） */}
        {wsTypingUsers.length > 0 && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center"><Bot className="w-4 h-4 text-primary" /></div>
            <div className="bg-muted/50 rounded-xl px-3 py-2">
              <p className="text-xs text-muted-foreground">{wsTypingUsers.join(', ')} 正在输入...</p>
            </div>
          </div>
        )}
      </div>

      {/* ===== 输入框区域 ===== */}
      {selectedGroup && (
        <div className="border-t border-border p-3 lg:p-4 shrink-0">
          {/* ===== 输入框：共用会话页 SmartPrompt 组件（用户拍板：输入框必须共用） ===== */}
          <SmartPrompt
            initialTask={editDraft}
            sendDisabled={!wsConnected || (!sendReady && !editDraft.trim())}
            sendLoading={sendingMessage}
            statsText={messages.length > 0 ? `${messages.length}条` : undefined}
            forwardText={editDraft.trim() || (messages.length > 0 ? messages[messages.length - 1].content : '')}
            onFieldsChange={(f) => setSendReady(!!(f.task || '').trim())}
            onSend={(assembled) => { if (!wsConnected) return; sendText(assembled); setEditDraft(''); setSendReady(false); }}
            isLoading={sendingMessage}
            placeholder={!wsConnected ? '请等待 WebSocket 连接...' : t('aiGroups.inputPlaceholder')}
            controls={{
              mode: {
                value: replyMode,
                onToggle: () => {
                  const next = replyMode === 'plan' ? 'act' : 'plan';
                  setReplyMode(next);
                  sendText(`!reply_mode ${next === 'act' ? 'auto' : 'mentions'}`);
                },
              },
              thinking: { show: showThinking, onToggle: () => setShowThinking(v => !v) },
            }}
          />
        </div>
      )}
      </div>
      {/* ===== 任务看板常驻面板：桌面端消息流右侧常驻，移动端全屏 ===== */}
      {tasksPanelOpen && selectedGroup && (
        <aside className={`relative flex flex-col min-h-0 bg-card ${isMobile ? 'flex-1 w-full' : 'w-[340px] shrink-0 border-l border-border'}`}>
          <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border shrink-0">
            <ListChecks className="w-3.5 h-3.5 text-primary" />
            <span className="text-xs font-medium">任务看板</span>
            <span className="text-[10px] text-muted-foreground">{tasks.length} 个任务</span>
            <div className="flex-1" />
          </div>
          <AIGroupsTaskBoard />
        </aside>
      )}
      </div>

      {/* ===== Agent 成员管理面板 ===== */}
      {isMobile ? (
        /* --- 移动端：Sheet 抽屉 --- */
        <Sheet open={showGroupPanel && !!selectedGroup} onOpenChange={setShowGroupPanel}>
          <SheetContent side="right" size="sm" showCloseButton={false}>
            <SheetHeader className="border-b border-border pb-3">
              <div className="flex items-center">
                <SheetTitle className="flex items-center gap-2 text-sm">
                  <Settings className="w-4 h-4" />
                  {t("aiGroups.groupManagement", "群组管理")}
                </SheetTitle>
              </div>
            </SheetHeader>
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {/* Agent 成员列表 */}
              <AgentMemberList
                agents={selectedGroup?.agents || []}
                editingAgent={editingAgent}
                editName={editName}
                editRole={editRole}
                editModel={editModel}
                setEditName={setEditName}
                setEditRole={setEditRole}
                setEditModel={setEditModel}
                onStartEdit={startEditAgent}
                onCancelEdit={cancelEditAgent}
                onSaveEdit={saveEditAgent}
                onDelete={deleteAgent}
              />
              {/* 添加 Agent 区域 */}
              <AddAgentSection
                show={showAddAgent}
                onToggle={() => setShowAddAgent(!showAddAgent)}
                agent={newAgent}
                onChange={setNewAgent}
                onAdd={addAgent}
              />
              {/* 工作区组件 */}
              <AIGroupsWorkspace />
            </div>
          </SheetContent>
        </Sheet>
      ) : (
        /* --- 桌面端：模态弹窗 --- */
        showGroupPanel && selectedGroup && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
            <div className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-lg mx-4 max-h-[80vh] flex flex-col">
              <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
                <h3 className="text-sm font-semibold flex items-center gap-2">
                  <Settings className="w-4 h-4" /> {t("aiGroups.groupManagement", "群组管理")}
                </h3>
                <button onClick={() => setShowGroupPanel(false)} className="p-1 rounded hover:bg-muted">
                  <X className="w-4 h-4 text-muted-foreground" />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto p-4 space-y-4">
                {/* Agent 成员列表 */}
                <AgentMemberList
                  agents={selectedGroup.agents || []}
                  editingAgent={editingAgent}
                  editName={editName}
                  editRole={editRole}
                  editModel={editModel}
                  setEditName={setEditName}
                  setEditRole={setEditRole}
                  setEditModel={setEditModel}
                  onStartEdit={startEditAgent}
                  onCancelEdit={cancelEditAgent}
                  onSaveEdit={saveEditAgent}
                  onDelete={deleteAgent}
                />
                {/* 添加 Agent 区域 */}
                <AddAgentSection
                  show={showAddAgent}
                  onToggle={() => setShowAddAgent(!showAddAgent)}
                  agent={newAgent}
                  onChange={setNewAgent}
                  onAdd={addAgent}
                />
                {/* 工作区组件 */}
                <AIGroupsWorkspace />
              </div>
            </div>
          </div>
        )
      )}
    </div>
  );
}

/* ====================================================================
 * Agent 成员列表组件
 * 展示所有 Agent 成员，支持编辑和删除
 * ==================================================================== */
function AgentMemberList({
  agents,
  editingAgent,
  editName, editRole, editModel,
  setEditName, setEditRole, setEditModel,
  onStartEdit, onCancelEdit, onSaveEdit, onDelete,
}: {
  agents: AgentRole[];
  editingAgent: string | null;
  editName: string; editRole: string; editModel: string;
  setEditName: (v: string) => void;
  setEditRole: (v: string) => void;
  setEditModel: (v: string) => void;
  onStartEdit: (a: AgentRole) => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        Agent 成员 ({agents.length})
      </h4>
      <div className="space-y-2">
        {agents.map((a, i) => {
          const Icon = ROLE_ICONS[a.role] || Bot;
          const isEditing = editingAgent === a.agent_id;

          return (
            <div key={a.agent_id}
              className={`flex items-center gap-3 p-3 rounded-lg border transition-colors ${
                isEditing ? 'border-primary/50 bg-primary/5' : 'border-border hover:bg-muted/50'
              }`}>
              {/* Agent 头像 */}
              <div className={`w-9 h-9 rounded-full flex items-center justify-center shrink-0 ${getAgentAvatarColor(i)}`}>
                <Icon className="w-4 h-4" />
              </div>

              {/* Agent 信息（普通模式 vs 编辑模式） */}
              {isEditing ? (
                /* --- 编辑模式 --- */
                <div className="flex-1 space-y-2">
                  <input value={editName} onChange={e => setEditName(e.target.value)}
                    placeholder="名称"
                    className="w-full px-2 py-1 bg-background border border-border rounded text-xs focus:outline-none focus:ring-1 focus:ring-primary" />
                  <div className="flex gap-2">
                    <select value={editRole} onChange={e => setEditRole(e.target.value)}
                      className="flex-1 px-2 py-1 bg-background border border-border rounded text-xs focus:outline-none focus:ring-1 focus:ring-primary">
                      <option value="advisor">advisor</option>
                      <option value="executor">executor</option>
                      <option value="verifier">verifier</option>
                      <option value="human">human</option>
                    </select>
                    <input value={editModel} onChange={e => setEditModel(e.target.value)}
                      placeholder="模型"
                      className="flex-1 px-2 py-1 bg-background border border-border rounded text-xs focus:outline-none focus:ring-1 focus:ring-primary" />
                  </div>
                </div>
              ) : (
                /* --- 普通模式 --- */
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm font-medium truncate">{a.name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${ROLE_BG_COLORS[a.role]} ${ROLE_COLORS[a.role]}`}>
                      {a.role}
                    </span>
                  </div>
                  {a.model && (
                    <span className="text-[10px] text-muted-foreground">{a.model}</span>
                  )}
                </div>
              )}

              {/* 操作按钮 */}
              <div className="flex items-center gap-1 shrink-0">
                {isEditing ? (
                  /* --- 编辑模式按钮 --- */
                  <>
                    <button onClick={onSaveEdit}
                      className="p-1.5 rounded hover:bg-green-500/20 text-green-500 transition-colors"
                      title="保存">
                      <Check className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={onCancelEdit}
                      className="p-1.5 rounded hover:bg-muted text-muted-foreground transition-colors"
                      title="取消">
                      <XIcon className="w-3.5 h-3.5" />
                    </button>
                  </>
                ) : (
                  /* --- 普通模式按钮 --- */
                  <>
                    <button onClick={() => onStartEdit(a)}
                      className="p-1.5 rounded hover:bg-muted text-muted-foreground transition-colors"
                      title="编辑">
                      <Edit3 className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => onDelete(a.agent_id)}
                      className="p-1.5 rounded hover:bg-red-500/20 text-muted-foreground hover:text-red-500 transition-colors"
                      title="删除">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {/* 能力档案·综合排名（一级界面常驻）——994ad7a7 丢失功能接回 */}
      <AgentLeaderboard />
    </div>
  );
}

/* ====================================================================
 * 能力档案 + 综合排名（一级界面常驻）——994ad7a7 重构中丢失的功能面，2026-10-23 接回
 * ==================================================================== */
function AgentLeaderboard() {
  const selectedGroup = useAIGroupsStore((s) => s.selectedGroup);
  const agentProfiles = useAIGroupsStore((s) => s.agentProfiles);
  const loadingProfile = useAIGroupsStore((s) => s.loadingProfile);
  const fetchAgentProfile = useAIGroupsStore((s) => s.fetchAgentProfile);
  const agents = selectedGroup?.agents || [];
  if (agents.length === 0) return null;

  return (
    <div className="mt-3 pt-3 border-t border-border space-y-2">
      <div className="flex items-center gap-1.5">
        <Trophy className="w-3.5 h-3.5 text-amber-400" />
        <span className="text-xs font-medium">能力档案 · 综合排名</span>
        <button
          onClick={() => agents.forEach((a) => { if (!agentProfiles[a.agent_id]) fetchAgentProfile(a.agent_id); })}
          className="ml-auto text-[10px] px-2 py-0.5 rounded bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
        >
          {agents.some((a) => loadingProfile === a.agent_id) ? '加载中...' : '刷新档案'}
        </button>
      </div>
      {agents.map((a) => {
        const profile = agentProfiles[a.agent_id];
        return (
          <div key={a.agent_id} className="rounded-lg border border-border/50 bg-muted/20 p-2 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium truncate">{a.name}</span>
              {profile ? (
                <div className="flex items-center gap-1">
                  <Trophy className="w-3 h-3 text-amber-400" />
                  <span className="text-[11px] font-bold text-amber-400">{profile.overall_rank.toFixed(1)}</span>
                </div>
              ) : (
                <button
                  onClick={() => fetchAgentProfile(a.agent_id)}
                  className="text-[10px] px-2 py-0.5 rounded bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
                >
                  {loadingProfile === a.agent_id ? '加载中...' : '查看能力档案'}
                </button>
              )}
            </div>
            {profile &&
              profile.capabilities.map((cap, ci) => (
                <div key={ci} className="space-y-0.5">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] text-muted-foreground">{cap.capability}</span>
                    <div className="flex items-center gap-1">
                      <span className="text-[10px] font-medium">{cap.avg_score.toFixed(1)}</span>
                      {cap.trend === 'up' ? (
                        <ArrowUp className="w-3 h-3 text-emerald-400" />
                      ) : cap.trend === 'down' ? (
                        <ArrowDown className="w-3 h-3 text-red-400" />
                      ) : (
                        <ArrowRight className="w-3 h-3 text-muted-foreground" />
                      )}
                    </div>
                  </div>
                  <div className="w-full h-1.5 bg-muted rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-500 bg-primary"
                      style={{ width: `${Math.min(cap.avg_score * 10, 100)}%` }}
                    />
                  </div>
                </div>
              ))}
          </div>
        );
      })}
    </div>
  );
}

/* ====================================================================
 * 添加 Agent 组件
 * 展开/收起的添加 Agent 表单
 * ==================================================================== */
function AddAgentSection({
  show, onToggle, agent, onChange, onAdd,
  }: {
  show: boolean;
  onToggle: () => void;
  agent: { name: string; role: string; model: string };
  onChange: (a: { name: string; role: string; model: string }) => void;
  onAdd: () => void;
  }) {
   const availableAgents = useAIGroupsStore((s) => s.availableAgents);
   const fetchAvailableAgents = useAIGroupsStore((s) => s.fetchAvailableAgents);
   const selectedGroup = useAIGroupsStore((s) => s.selectedGroup);
   useEffect(() => { if (show) fetchAvailableAgents(); }, [show, fetchAvailableAgents]);
   const choices = availableAgents.filter((a) => !(selectedGroup?.agents || []).some((m) => m.name === a.name));
   return (
    <div className="space-y-2">
      {/* 展开/收起按钮 */}
      <button onClick={onToggle}
        className="w-full flex items-center justify-center gap-2 p-2 rounded-lg border border-dashed border-border hover:border-primary/50 hover:bg-primary/5 transition-colors text-xs text-muted-foreground hover:text-primary">
        <Plus className="w-4 h-4" />
        {show ? '收起' : '添加新 Agent'}
      </button>

      {/* 添加表单 */}
      {show && (
        <div className="p-3 rounded-lg border border-border bg-muted/30 space-y-2">
          <p className="text-[11px] text-muted-foreground">成员=系统接入的 AI Agent，选择入组</p>
          <select value={agent.name}
            onChange={e => {
              const picked = availableAgents.find((a) => (a.agent_id || a.name) === e.target.value);
              onChange({ ...agent, name: picked?.name || e.target.value, model: picked?.model || '' });
            }}
            className="w-full px-2 py-1.5 bg-background border border-border rounded text-xs focus:outline-none focus:ring-1 focus:ring-primary">
            <option value="">选择接入的 Agent…</option>
            {choices.map((a) => (
              <option key={a.agent_id || a.name} value={a.agent_id || a.name}>{a.name}</option>
            ))}
          </select>
          <div className="flex gap-2">
            <select value={agent.role} onChange={e => onChange({ ...agent, role: e.target.value })}
              className="flex-1 px-2 py-1.5 bg-background border border-border rounded text-xs focus:outline-none focus:ring-1 focus:ring-primary">
              <option value="advisor">advisor (顾问)</option>
              <option value="executor">executor (执行者)</option>
              <option value="verifier">verifier (验证者)</option>
              <option value="human">human (人工)</option>
            </select>
          </div>
          <button onClick={onAdd} disabled={!agent.name.trim()}
            className="w-full px-3 py-1.5 bg-primary text-primary-foreground rounded text-xs font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors">
            添加
          </button>
        </div>
      )}
    </div>
  );
}
