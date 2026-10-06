'use client';
import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { getApiBaseUrl, getToken } from '@/lib/api-client';
import { useSidebar } from '@/components/ui/sidebar';
import { useAppStore } from '@/stores/app-store';
import { AIGroupsWorkspace, AIGroupsTaskBoard } from '@/components/ai-groups-workspace';
import {
  Users, Send, Bot, Shield, Zap, User, Loader2,
  MessageSquare, AtSign,
  ListChecks,
  Star,
  MessageCircle, Hand, FileText, Lightbulb,
  Target,
  PanelLeft, Settings, X,
  Plus, Trash2, Edit3, Check, XIcon,
  StopCircle,
} from 'lucide-react';
import { useIsMobile } from '@/hooks/use-mobile';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { useAIGroupsStore, type AgentRole, type GroupMessage } from '@/stores/ai-groups-store';

/* ========== 角色配置 ========== */
const ROLE_ICONS: Record<string, any> = { advisor: Shield, executor: Zap, verifier: Bot, human: User };
const ROLE_COLORS: Record<string, string> = { advisor: 'text-yellow-400', executor: 'text-blue-400', verifier: 'text-green-400', human: 'text-purple-400' };
const ROLE_BG_COLORS: Record<string, string> = { advisor: 'bg-yellow-500/20', executor: 'bg-blue-500/20', verifier: 'bg-green-500/20', human: 'bg-purple-500/20' };

/* ========== 意图配置 ========== */
const INTENT_CONFIG: Record<string, { label: string; color: string; bg: string; icon: any }> = {
  claim: { label: '认领', color: 'text-emerald-400', bg: 'bg-emerald-500/20', icon: Hand },
  suggest: { label: '建议', color: 'text-sky-400', bg: 'bg-sky-500/20', icon: Lightbulb },
  refer: { label: '推荐', color: 'text-amber-400', bg: 'bg-amber-500/20', icon: Target },
  comment: { label: '评论', color: 'text-muted-foreground', bg: 'bg-muted/20', icon: MessageCircle },
  result: { label: '结果', color: 'text-violet-400', bg: 'bg-violet-500/20', icon: FileText },
  score: { label: '评分', color: 'text-orange-400', bg: 'bg-orange-500/20', icon: Star },
};

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

export default function AIGroupsPage() {
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
  const selectedTarget = useAIGroupsStore((s) => s.selectedTarget);
  const setSelectedTarget = useAIGroupsStore((s) => s.setSelectedTarget);
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
  // 任务看板常驻面板（Paperclip 看板式）：桌面端消息流右侧常驻，移动端全屏切换
  const [tasksPanelOpen, setTasksPanelOpen] = useState(true);
  const [input, setInput] = useState('');
  const [showMention, setShowMention] = useState(false);
  const [mentionFilter, setMentionFilter] = useState('');
  const [mentionIndex, setMentionIndex] = useState(0);
  // 编辑 Agent 的临时状态
  const [editName, setEditName] = useState('');
  const [editRole, setEditRole] = useState('');
  const [editModel, setEditModel] = useState('');
  // 讨论触发 UI 状态
  const [showDiscussionForm, setShowDiscussionForm] = useState(false);   // 是否展开讨论表单
  const [discussionGoal, setDiscussionGoal] = useState('');              // 讨论任务描述
  const [discussionConstraints, setDiscussionConstraints] = useState(''); // 约束条件（逗号分隔）

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

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
  const handleSend = () => {
    const text = input.trim();
    if (!text || !selectedGroup || !wsConnected) return;

    // 解析 @mention 目标
    let target = selectedTarget;
    const mentionMatch = text.match(/^@(\S+)\s/);
    if (mentionMatch) {
      const mention = mentionMatch[1];
      if (mention === 'all') target = 'all';
      else {
        const agent = (selectedGroup.agents || []).find(
          a => a.name.toLowerCase().includes(mention.toLowerCase()) || a.agent_id === mention
        );
        if (agent) target = agent.agent_id;
      }
    }

    // 本地添加用户消息到列表
    const userMsg: GroupMessage = {
      id: `msg-${Date.now()}`, role: 'user', content: text,
      timestamp: new Date(), target,
    };
    setMessages(prev => [...prev, userMsg]);

    // 通过 WS 发送给后端
    sendGroupMessage(text, 'comment');

    // 清空输入框
    setInput('');
    setSelectedTarget('all');
  };

  /* ========== 发起讨论 ========== */
  const handleStartDiscussion = () => {
    const goal = discussionGoal.trim();
    if (!goal || !wsConnected) return;
    // 解析约束条件：按逗号分隔，去除空白
    const constraints = discussionConstraints
      .split(',')
      .map(s => s.trim())
      .filter(Boolean);
    // 通过 WS 发送 start_discussion 消息
    startDiscussion(goal, constraints);
    // 关闭表单并清空输入
    setShowDiscussionForm(false);
    setDiscussionGoal('');
    setDiscussionConstraints('');
  };

  /* ========== 取消讨论 ========== */
  const handleCancelDiscussion = () => {
    // 通过 WS 发送 cancel_discussion 消息
    cancelDiscussion();
  };

  /* ========== 输入框 @mention 逻辑 ========== */
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setInput(val);
    const cursorPos = e.target.selectionStart;
    const textBeforeCursor = val.slice(0, cursorPos);
    const atMatch = textBeforeCursor.match(/@(\w*)$/);
    if (atMatch) {
      setShowMention(true);
      setMentionFilter(atMatch[1].toLowerCase());
      setMentionIndex(0);
    } else {
      setShowMention(false);
    }
  };

  const filteredAgentsForMention = (selectedGroup?.agents || []).filter(a =>
    a.name.toLowerCase().includes(mentionFilter) || a.agent_id.toLowerCase().includes(mentionFilter)
  );

  const insertMention = (agent: AgentRole | { name: string; agent_id: string }) => {
    const cursorPos = inputRef.current?.selectionStart || input.length;
    const textBeforeCursor = input.slice(0, cursorPos);
    const atIndex = textBeforeCursor.lastIndexOf('@');
    const before = input.slice(0, atIndex);
    const after = input.slice(cursorPos);
    setInput(`${before}@${agent.name} ${after}`);
    setSelectedTarget(agent.agent_id);
    setShowMention(false);
    inputRef.current?.focus();
  };

  /* ========== 键盘事件：Enter 发送 / Shift+Enter 换行 / @mention 导航 ========== */
  const handleKeyDown = (e: React.KeyboardEvent) => {
    // @mention 下拉导航
    if (showMention) {
      const items = [{ name: 'all', agent_id: 'all' }, ...filteredAgentsForMention];
      if (e.key === 'ArrowDown') { e.preventDefault(); setMentionIndex(i => (i + 1) % items.length); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setMentionIndex(i => (i - 1 + items.length) % items.length); }
      else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); insertMention(items[mentionIndex]); }
      else if (e.key === 'Escape') { setShowMention(false); }
      return;
    }
    // Enter 发送，Shift+Enter 换行
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
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
          {/* @目标指示器 */}
          {selectedTarget !== 'all' && selectedGroup && (
            <span className="text-xs text-primary px-1.5 py-0.5 rounded bg-primary/10">
              @{getAgentById(selectedTarget)?.name || selectedTarget}
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
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 lg:px-6 py-4 space-y-4">
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
          const agent = msg.role === 'agent' ? getAgentById(msg.agent_id) : null;
          const agentIndex = (selectedGroup?.agents || []).findIndex(a => a.agent_id === msg.agent_id);

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

          /* --- 用户消息：靠右 + 背景色 --- */
          if (msg.role === 'user') {
            return (
              <div key={msg.id} className="flex gap-3 justify-end">
                <div className="max-w-[85%] lg:max-w-[70%]">
                  <div className="rounded-xl px-2 lg:px-4 py-2.5 text-xs lg:text-sm bg-primary text-primary-foreground rounded-tr-sm">
                    <p className="whitespace-pre-wrap break-words">{msg.content}</p>
                    <div className="text-[10px] mt-1.5 opacity-60">
                      {msg.timestamp.toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}
                      {msg.target && msg.target !== 'all' && (
                        <span className="ml-1.5">@{getAgentById(msg.target)?.name || msg.target}</span>
                      )}
                    </div>
                  </div>
                </div>
                {/* 用户头像 */}
                <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center shrink-0">
                  <User className="w-4 h-4 text-primary-foreground" />
                </div>
              </div>
            );
          }

          /* --- Agent 消息：靠左 + 角色头像 --- */
          return (
            <div key={msg.id} className="flex gap-3">
              {/* Agent 头像（角色颜色） */}
              <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${getAgentAvatarColor(agentIndex >= 0 ? agentIndex : 0)}`}>
                {(() => { const Icon = ROLE_ICONS[msg.agent_role || 'executor'] || Bot; return <Icon className="w-4 h-4" />; })()}
              </div>
              <div className="max-w-[85%] lg:max-w-[70%]">
                {/* Agent 名称 + 角色标签 + 意图标签 */}
                <div className="flex items-center gap-1.5 mb-1 flex-wrap">
                  <span className="text-xs font-medium">{msg.agent_name || 'Agent'}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${ROLE_BG_COLORS[msg.agent_role || 'executor']} ${ROLE_COLORS[msg.agent_role || 'executor']}`}>
                    {msg.agent_role || 'executor'}
                  </span>
                  {msg.intent && INTENT_CONFIG[msg.intent] && (() => {
                    const ic = INTENT_CONFIG[msg.intent];
                    const IntentIcon = ic.icon;
                    return (
                      <span className={`text-[10px] px-1.5 py-0.5 rounded flex items-center gap-0.5 ${ic.bg} ${ic.color}`}>
                        <IntentIcon className="w-2.5 h-2.5" />
                        {ic.label}
                      </span>
                    );
                  })()}
                  {msg.target && msg.target !== 'all' && (
                    <span className="text-[10px] text-muted-foreground">{t("aiGroups.replyTo")} @{msg.target}</span>
                  )}
                </div>
                {/* Agent 消息气泡 */}
                <div className="rounded-xl px-2 lg:px-4 py-2.5 text-xs lg:text-sm bg-muted rounded-tl-sm">
                  <p className="whitespace-pre-wrap break-words">{msg.content}</p>
                  <div className="text-[10px] mt-1.5 opacity-60">
                    {msg.timestamp.toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}
                  </div>
                </div>
              </div>
            </div>
          );
        })}

        {/* 正在发送指示器 */}
        {sendingMessage && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center"><Bot className="w-4 h-4 text-primary" /></div>
            <div className="bg-muted rounded-xl px-2 lg:px-4 py-2.5"><Loader2 className="w-4 h-4 animate-spin text-muted-foreground" /></div>
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
          {/* @目标选择器 */}
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            <span className="text-[11px] text-muted-foreground">{t("aiGroups.sendTo")}:</span>
            <button onClick={() => setSelectedTarget('all')}
              className={`text-[11px] px-2 py-0.5 rounded-full transition-colors ${selectedTarget === 'all' ? 'bg-primary text-primary-foreground' : 'bg-muted hover:bg-muted/80 text-muted-foreground'}`}>
              @{t("aiGroups.allMembers")}
            </button>
            {(selectedGroup.agents || []).map((a, i) => {
              const Icon = ROLE_ICONS[a.role] || Bot;
              return (
                <button key={a.agent_id} onClick={() => setSelectedTarget(a.agent_id)}
                  className={`flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full transition-colors ${selectedTarget === a.agent_id ? 'bg-primary text-primary-foreground' : 'bg-muted hover:bg-muted/80 text-muted-foreground'}`}>
                  <Icon className="w-3 h-3" /> {a.name}
                </button>
              );
            })}
          </div>

          {/* ===== 讨论触发表单（展开/收起） ===== */}
          {showDiscussionForm && (
            <div className="mb-3 p-3 rounded-lg border border-amber-500/30 bg-amber-500/5 space-y-2">
              {/* 任务描述输入框 */}
              <textarea
                value={discussionGoal}
                onChange={e => setDiscussionGoal(e.target.value)}
                placeholder="描述讨论任务，例如：分析这个方案的可行性并提出改进建议"
                rows={2}
                className="w-full resize-none rounded-lg border border-input bg-background px-3 py-2 text-xs lg:text-sm focus:outline-none focus:ring-2 focus:ring-amber-500/30"
              />
              {/* 约束条件输入框（可选，逗号分隔） */}
              <input
                value={discussionConstraints}
                onChange={e => setDiscussionConstraints(e.target.value)}
                placeholder="约束条件（可选，逗号分隔），例如：预算不超过10万, 3天内完成"
                className="w-full rounded-lg border border-input bg-background px-3 py-2 text-xs lg:text-sm focus:outline-none focus:ring-2 focus:ring-amber-500/30"
              />
              {/* 操作按钮 */}
              <div className="flex gap-2 justify-end">
                {/* 取消按钮 — 关闭表单 */}
                <button
                  onClick={() => { setShowDiscussionForm(false); setDiscussionGoal(''); setDiscussionConstraints(''); }}
                  className="px-3 py-1.5 rounded-lg text-xs text-muted-foreground hover:bg-muted transition-colors"
                >
                  取消
                </button>
                {/* 开始讨论按钮 — 通过 WS 发送 start_discussion */}
                <button
                  onClick={handleStartDiscussion}
                  disabled={!discussionGoal.trim() || !wsConnected}
                  className="px-3 py-1.5 rounded-lg text-xs bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50 transition-colors font-medium"
                >
                  开始讨论
                </button>
              </div>
            </div>
          )}

          {/* 输入框 + 发送按钮 */}
          <div className="flex gap-2 items-end relative">
            <div className="flex-1 relative">
              <textarea ref={inputRef} value={input} onChange={handleInputChange} onKeyDown={handleKeyDown}
                placeholder={
                  !wsConnected
                    ? '请等待 WebSocket 连接...'
                    : selectedTarget === 'all'
                      ? t('aiGroups.inputPlaceholder')
                      : `@${getAgentById(selectedTarget)?.name || 'Agent'} ...`
                }
                rows={1}
                disabled={!wsConnected}
                className="w-full resize-none rounded-lg border border-input bg-background px-3 py-2 text-xs lg:text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-50" />

              {/* @mention 下拉菜单 */}
              {showMention && (
                <div className="absolute bottom-full left-0 mb-1 w-56 bg-popover border border-border rounded-lg shadow-lg overflow-hidden z-50">
                  <div className="p-1">
                    <button onClick={() => insertMention({ name: 'all', agent_id: 'all' })}
                      className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded text-xs lg:text-sm hover:bg-muted transition-colors ${mentionIndex === 0 ? 'bg-muted' : ''}`}>
                      <AtSign className="w-3.5 h-3.5 text-muted-foreground" />
                      <span>{t("aiGroups.everyone")}</span>
                    </button>
                    {filteredAgentsForMention.map((a, i) => {
                      const Icon = ROLE_ICONS[a.role] || Bot;
                      return (
                        <button key={a.agent_id} onClick={() => insertMention(a)}
                          className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded text-xs lg:text-sm hover:bg-muted transition-colors ${mentionIndex === i + 1 ? 'bg-muted' : ''}`}>
                          <Icon className={`w-3.5 h-3.5 ${ROLE_COLORS[a.role]}`} />
                          <span className="flex-1 text-left">{a.name}</span>
                          <span className="text-[10px] text-muted-foreground">{a.role}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>

            {/* 发送按钮 */}
            <button onClick={handleSend} disabled={sendingMessage || !input.trim() || !wsConnected}
              className="px-3 lg:px-4 py-2.5 lg:py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 touch-manipulation">
              <Send className="w-4 h-4" />
            </button>
            {/* 讨论按钮 — 讨论中显示取消，否则显示发起讨论 */}
            {discussionLoading ? (
              <button onClick={handleCancelDiscussion}
                className="px-3 lg:px-4 py-2.5 lg:py-2 rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors touch-manipulation"
                title="取消讨论">
                <StopCircle className="w-4 h-4" />
              </button>
            ) : (
              <button onClick={() => setShowDiscussionForm(!showDiscussionForm)}
                className={`px-3 lg:px-4 py-2.5 lg:py-2 rounded-lg transition-colors touch-manipulation ${showDiscussionForm ? 'bg-amber-500/20 text-amber-400' : 'bg-muted hover:bg-muted/80 text-muted-foreground'}`}
                title="发起讨论">
                <MessageSquare className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>
      )}
      </div>
      {/* ===== 任务看板常驻面板：桌面端消息流右侧常驻，移动端全屏 ===== */}
      {tasksPanelOpen && selectedGroup && (
        <aside className={`flex flex-col min-h-0 bg-card ${isMobile ? 'flex-1 w-full' : 'w-[340px] shrink-0 border-l border-border'}`}>
          <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border shrink-0">
            <ListChecks className="w-3.5 h-3.5 text-primary" />
            <span className="text-xs font-medium">任务看板</span>
            <span className="text-[10px] text-muted-foreground">{tasks.length} 个任务</span>
            <div className="flex-1" />
            <button onClick={() => setTasksPanelOpen(false)} title="收起任务看板"
              className="p-1 rounded hover:bg-muted text-muted-foreground transition-colors">
              <XIcon className="w-3.5 h-3.5" />
            </button>
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
          <input value={agent.name} onChange={e => onChange({ ...agent, name: e.target.value })}
            placeholder="Agent 名称"
            className="w-full px-2 py-1.5 bg-background border border-border rounded text-xs focus:outline-none focus:ring-1 focus:ring-primary" />
          <div className="flex gap-2">
            <select value={agent.role} onChange={e => onChange({ ...agent, role: e.target.value })}
              className="flex-1 px-2 py-1.5 bg-background border border-border rounded text-xs focus:outline-none focus:ring-1 focus:ring-primary">
              <option value="advisor">advisor (顾问)</option>
              <option value="executor">executor (执行者)</option>
              <option value="verifier">verifier (验证者)</option>
              <option value="human">human (人工)</option>
            </select>
            <input value={agent.model} onChange={e => onChange({ ...agent, model: e.target.value })}
              placeholder="模型名称"
              className="flex-1 px-2 py-1.5 bg-background border border-border rounded text-xs focus:outline-none focus:ring-1 focus:ring-primary" />
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
