'use client';

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Bot, FileText, Image as ImageIcon, Globe, Download, Trash2,
  ChevronDown, ChevronRight, Clock, User,
  ListChecks, Activity, RotateCcw, Loader2, RefreshCw, X, AlertTriangle,
  Award,
} from 'lucide-react';
import { useAIGroupsStore, type AITask, type AISubTask, type TaskEvent } from '@/stores/ai-groups-store';

const ROLE_ICONS: Record<string, any> = { advisor: Bot, executor: Bot, verifier: Bot, human: User };

interface GroupFile {
  id: string;
  name: string;
  type: 'html' | 'image' | 'document' | 'code' | 'url' | 'other';
  url?: string;
  content?: string;
  agentId?: string;
  agentName?: string;
  taskId?: string;
  createdAt: Date;
}

const FILE_ICONS: Record<string, any> = {
  html: Globe,
  image: ImageIcon,
  document: FileText,
  code: FileText,
  url: Globe,
  other: FileText,
};

export function AIGroupsWorkspace() {
  const { t } = useTranslation();
  const selectedGroup = useAIGroupsStore((s) => s.selectedGroup);
  const messages = useAIGroupsStore((s) => s.messages);
  const [expandedFile, setExpandedFile] = useState<string | null>(null);
  const tasks = useAIGroupsStore((s) => s.tasks);
  // 工作台顶部面板切换：任务卡（默认）/ 文件
  const [panelTab, setPanelTab] = useState<'tasks' | 'files'>('tasks');

  // Extract files/outputs from messages — tasks with results become files
  const groupFiles: GroupFile[] = [];
  if (selectedGroup && messages) {
    messages.forEach((msg) => {
      if (msg.role === 'agent' && msg.content && msg.intent === 'result') {
        // Check if content looks like a file/URL
        const isUrl = /^https?:\/\//i.test(msg.content.trim());
        const isHtml = msg.content.includes('<html') || msg.content.includes('<!DOCTYPE');
        const isCode = msg.content.includes('```');

        let fileType: GroupFile['type'] = 'other';
        if (isUrl) fileType = 'url';
        else if (isHtml) fileType = 'html';
        else if (isCode) fileType = 'code';

        groupFiles.push({
          id: msg.id,
          name: `${msg.agent_name || 'Agent'} - ${new Date(msg.timestamp).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' , fractionalSecondDigits: 3 })}`,
          type: fileType,
          url: isUrl ? msg.content.trim() : undefined,
          content: !isUrl ? msg.content : undefined,
          agentId: msg.agent_id,
          agentName: msg.agent_name,
          taskId: undefined,
          createdAt: new Date(msg.timestamp),
        });
      }
    });
  }

  if (!selectedGroup) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground p-4">
        <Bot className="w-8 h-8 mb-2 opacity-40" />
        <p className="text-xs">{t("aiGroups.selectGroup", "选择群组")}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="p-3 border-b border-border">
        <div className="flex items-center gap-2">
          <FileText className="w-4 h-4 text-primary" />
          <span className="text-xs font-medium">{selectedGroup.name}</span>
          <span className="text-[10px] text-muted-foreground px-1.5 py-0.5 rounded bg-muted">
            {groupFiles.length} {t("aiGroups.files", "个文件")}
          </span>
        </div>
      </div>

      {/* 面板切换：任务卡 / 文件 */}
      <div className="flex items-center gap-1 px-3 py-1.5 border-b border-border">
        <button
          onClick={() => setPanelTab('tasks')}
          className={`flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-full transition-colors ${
            panelTab === 'tasks'
              ? 'bg-primary text-primary-foreground'
              : 'bg-muted hover:bg-muted/80 text-muted-foreground'
          }`}
        >
          <ListChecks className="w-3 h-3" />
          任务 ({tasks.length})
        </button>
        <button
          onClick={() => setPanelTab('files')}
          className={`flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-full transition-colors ${
            panelTab === 'files'
              ? 'bg-primary text-primary-foreground'
              : 'bg-muted hover:bg-muted/80 text-muted-foreground'
          }`}
        >
          <FileText className="w-3 h-3" />
          文件 ({groupFiles.length})
        </button>
      </div>

      {/* 任务卡列表（含子任务重试 + 执行过程时间线） */}
      {panelTab === 'tasks' && <AIGroupsTaskBoard />}

      {/* File list */}
      {panelTab === 'files' && (
      <div className="flex-1 overflow-y-auto">
        {groupFiles.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
            <FileText className="w-8 h-8 mb-2 opacity-40" />
            <p className="text-xs">{t("aiGroups.noFiles", "暂无文件")}</p>
            <p className="text-[10px] mt-1">{t("aiGroups.filesWillAppear", "群组任务产生的文件将显示在这里")}</p>
          </div>
        ) : (
          <div className="space-y-0.5">
            {groupFiles.map((file) => {
              const Icon = FILE_ICONS[file.type] || FileText;
              const isExpanded = expandedFile === file.id;
              return (
                <div key={file.id} className="border-b border-border/30">
                  <div
                    className="flex items-center gap-2 px-3 py-2.5 cursor-pointer hover:bg-muted/50 transition-colors"
                    onClick={() => setExpandedFile(isExpanded ? null : file.id)}
                  >
                    <div className="w-7 h-7 rounded bg-primary/10 flex items-center justify-center shrink-0">
                      <Icon className="w-3.5 h-3.5 text-primary" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium truncate">{file.name}</p>
                      <div className="flex items-center gap-1.5 mt-0.5">
                        <Clock className="w-2.5 h-2.5 text-muted-foreground" />
                        <span className="text-[10px] text-muted-foreground">
                          {file.createdAt.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' , fractionalSecondDigits: 3 })}
                        </span>
                        {file.agentName && (
                          <>
                            <span className="text-[10px] text-muted-foreground">·</span>
                            <span className="text-[10px] text-muted-foreground">{file.agentName}</span>
                          </>
                        )}
                      </div>
                    </div>
                    {file.url && (
                      <a
                        href={file.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="p-1 rounded hover:bg-muted text-muted-foreground"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Download className="w-3 h-3" />
                      </a>
                    )}
                    {isExpanded ? (
                      <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
                    )}
                  </div>
                  {isExpanded && file.content && (
                    <div className="px-3 pb-3">
                      <div className="rounded-lg bg-muted/50 p-2.5 text-xs font-mono whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                        {file.type === 'html' ? (
                          <iframe
                            srcDoc={file.content}
                            className="w-full h-48 border-0 bg-white rounded"
                            title={file.name}
                          />
                        ) : (
                          file.content.slice(0, 2000)
                        )}
                      </div>
                    </div>
                  )}
                  {isExpanded && file.url && (
                    <div className="px-3 pb-3">
                      <iframe
                        src={file.url}
                        className="w-full h-48 border-0 bg-white rounded-lg"
                        title={file.name}
                        sandbox="allow-same-origin allow-scripts"
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
      )}
    </div>
  );
}

/* ====================================================================
 * 任务执行过程可视化 — 任务卡列表（位于工作台「任务」标签页）
 * 任务卡 → 子任务行（规划/执行/验证/审查，各带重试按钮）+ 执行过程时间线
 * ==================================================================== */

/** 子任务/主任务状态徽章配置 */
const TASK_STATUS_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  pending: { label: '待执行', color: 'text-muted-foreground', bg: 'bg-muted' },
  executing: { label: '执行中', color: 'text-sky-400', bg: 'bg-sky-500/20' },
  scored: { label: '已评分', color: 'text-violet-400', bg: 'bg-violet-500/20' },
  completed: { label: '已完成', color: 'text-green-400', bg: 'bg-green-500/20' },
  failed: { label: '失败', color: 'text-red-400', bg: 'bg-red-500/20' },
};

/** 执行过程事件类型徽章配置：started=蓝 output=灰 stderr=橙 finished=绿 failed=红 scored=紫 retry=黄 */
const EVENT_TYPE_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  started: { label: '开始', color: 'text-sky-400', bg: 'bg-sky-500/20' },
  output: { label: '输出', color: 'text-muted-foreground', bg: 'bg-muted' },
  stderr: { label: '错误输出', color: 'text-orange-400', bg: 'bg-orange-500/20' },
  finished: { label: '完成', color: 'text-green-400', bg: 'bg-green-500/20' },
  failed: { label: '失败', color: 'text-red-400', bg: 'bg-red-500/20' },
  scored: { label: '评分', color: 'text-violet-400', bg: 'bg-violet-500/20' },
  retry: { label: '重试', color: 'text-amber-400', bg: 'bg-amber-500/20' },
};

/** 从子任务 goal 前缀提取阶段名（规划/执行/验证/审查） */
function getSubtaskStage(goal: string): string {
  const m = (goal || '').match(/^(规划|执行|验证|审查|评分)[：:]/);
  return m ? m[1] : '子任务';
}

/** 任务卡列表：含子任务级重试按钮 + 执行过程时间线面板 */
export function AIGroupsTaskBoard() {
  const tasks = useAIGroupsStore((s) => s.tasks);
  const tasksLoading = useAIGroupsStore((s) => s.tasksLoading);
  const fetchTasks = useAIGroupsStore((s) => s.fetchTasks);

  return (
    <div className="flex-1 overflow-y-auto">
      {/* 刷新行 */}
      <div className="flex items-center justify-between px-3 py-1.5">
        <span className="text-[10px] text-muted-foreground">任务卡 · 子任务可单独重跑</span>
        <button
          onClick={() => fetchTasks()}
          title="刷新任务列表"
          className="p-1 rounded hover:bg-muted text-muted-foreground transition-colors"
        >
          {tasksLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
        </button>
      </div>
      {tasksLoading && tasks.length === 0 ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
        </div>
      ) : tasks.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
          <ListChecks className="w-8 h-8 mb-2 opacity-40" />
          <p className="text-xs">暂无任务</p>
          <p className="text-[10px] mt-1">发起讨论后任务卡将显示在这里</p>
        </div>
      ) : (
        <div className="space-y-0.5">
          {tasks.map((task) => (
            <TaskCard key={task.id} task={task} />
          ))}
        </div>
      )}
    </div>
  );
}

/** 单个任务卡：卡头可展开子任务行，可打开「执行过程」时间线 */
function TaskCard({ task }: { task: AITask }) {
  const [expanded, setExpanded] = useState(true);
  const timelineTaskId = useAIGroupsStore((s) => s.timelineTaskId);
  const openTaskTimeline = useAIGroupsStore((s) => s.openTaskTimeline);
  const closeTaskTimeline = useAIGroupsStore((s) => s.closeTaskTimeline);

  const statusCfg = TASK_STATUS_CONFIG[task.status] || TASK_STATUS_CONFIG.pending;
  // 时间线面板归属判定：主任务 id 或其任一子任务 id
  const isTimelineOpen =
    timelineTaskId === task.id || (task.subtasks || []).some((st) => st.id === timelineTaskId);
  const toggleTimeline = () => {
    if (isTimelineOpen) closeTaskTimeline();
    else openTaskTimeline(task.id);
  };

  return (
    <div className="border-b border-border/30">
      {/* 卡死黄条：执行中但 >10 分钟无事件，仅提醒不杀任务 */}
      {task.stalled && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 bg-amber-500/15 text-amber-500 text-[10px] border-b border-amber-500/20">
          <AlertTriangle className="w-3 h-3 shrink-0" />
          疑似卡死（10分钟无事件）
        </div>
      )}
      {/* 任务卡头 */}
      <div
        className="flex items-start gap-2 px-3 py-2.5 cursor-pointer hover:bg-muted/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="w-7 h-7 rounded bg-primary/10 flex items-center justify-center shrink-0 mt-0.5">
          <ListChecks className="w-3.5 h-3.5 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium break-words">{task.goal}</p>
          <div className="flex items-center gap-1.5 mt-1 flex-wrap">
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${statusCfg.bg} ${statusCfg.color}`}>
              {statusCfg.label}
            </span>
            {typeof task.quality_score === 'number' && task.quality_score > 0 && (
              <span className="text-[10px] text-muted-foreground">质量 {task.quality_score.toFixed(1)}</span>
            )}
            <span className="text-[10px] text-muted-foreground">
              {(task.subtasks || []).length} 个子任务
            </span>
            {(typeof task.estimated_low_s === 'number' || typeof task.estimated_high_s === 'number') && (
              <span className="text-[10px] text-muted-foreground flex items-center gap-0.5">
                <Clock className="w-2.5 h-2.5" />
                预估 {Math.round(task.estimated_low_s ?? 0)}~{Math.round(task.estimated_high_s ?? 0)}秒
              </span>
            )}
            {task.created_at && (
              <span className="text-[10px] text-muted-foreground">
                {new Date(task.created_at).toLocaleString('zh-CN', { hour12: false })}
              </span>
            )}
          </div>
        </div>
        {/* 执行过程开关 */}
        <button
          onClick={(e) => { e.stopPropagation(); toggleTimeline(); }}
          title="执行过程"
          className={`flex items-center gap-1 px-2 py-1 rounded text-[10px] transition-colors shrink-0 ${
            isTimelineOpen
              ? 'bg-primary/20 text-primary'
              : 'bg-muted hover:bg-muted/80 text-muted-foreground'
          }`}
        >
          <Activity className="w-3 h-3" />
          执行过程
        </button>
        {expanded ? (
          <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0 mt-1" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0 mt-1" />
        )}
      </div>

      {/* 子任务行 + 主任务时间线 */}
      {expanded && (
        <div className="px-3 pb-3 space-y-1">
          {(task.subtasks || []).length === 0 ? (
            <p className="text-[10px] text-muted-foreground px-1">暂无子任务</p>
          ) : (
            (task.subtasks || []).map((st) => <SubtaskRow key={st.id} subtask={st} />)
          )}
          {timelineTaskId === task.id && <TaskExecutionTimeline targetLabel="主任务" />}
          {/* 任务评审（一级界面常驻）——994ad7a7 丢失功能接回 */}
          <TaskReviewPanel task={task} />
        </div>
      )}
    </div>
  );
}

/** 任务评审面板（评分表单+分数明细）——994ad7a7 重构中丢失的功能面，2026-10-23 接回 */
function TaskReviewPanel({ task }: { task: AITask }) {
  const selectedGroup = useAIGroupsStore((s) => s.selectedGroup);
  const activeTaskReview = useAIGroupsStore((s) => s.activeTaskReview);
  const setActiveTaskReview = useAIGroupsStore((s) => s.setActiveTaskReview);
  const scoreValue = useAIGroupsStore((s) => s.scoreValue);
  const setScoreValue = useAIGroupsStore((s) => s.setScoreValue);
  const scoreReason = useAIGroupsStore((s) => s.scoreReason);
  const setScoreReason = useAIGroupsStore((s) => s.setScoreReason);
  const scoreCapability = useAIGroupsStore((s) => s.scoreCapability);
  const setScoreCapability = useAIGroupsStore((s) => s.setScoreCapability);
  const scorerAgentId = useAIGroupsStore((s) => s.scorerAgentId);
  const setScorerAgentId = useAIGroupsStore((s) => s.setScorerAgentId);
  const submitScore = useAIGroupsStore((s) => s.submitScore);
  const [submitting, setSubmitting] = useState(false);

  const review = activeTaskReview && activeTaskReview.task_id === task.id ? activeTaskReview : null;

  const handleOpen = () => {
    setActiveTaskReview({
      task_id: task.id,
      result: '',
      status: 'reviewing',
      round: 1,
      assignments: [],
      scores: [],
      avg_score: typeof task.quality_score === 'number' ? task.quality_score : 0,
      discussion_messages: [],
    });
  };

  const handleSubmit = async () => {
    if (!scorerAgentId) return;
    setSubmitting(true);
    await submitScore(task.id, scorerAgentId, scoreValue, scoreReason, scoreCapability);
    setScoreReason('');
    setScoreCapability('');
    setScorerAgentId('');
    setSubmitting(false);
  };

  return (
    <div className="mt-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5 space-y-2">
      {!review ? (
        <button
          onClick={handleOpen}
          className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 rounded text-[11px] bg-amber-500/15 text-amber-500 hover:bg-amber-500/25 transition-colors"
        >
          <Award className="w-3.5 h-3.5" />
          任务评审
        </button>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <Award className="w-4 h-4 text-amber-400" />
            <span className="text-xs font-medium">任务评审</span>
            {review.avg_score > 0 && (
              <span className="ml-auto text-xs font-bold text-amber-400">
                平均分: {review.avg_score.toFixed(1)}/10
              </span>
            )}
            <button onClick={() => setActiveTaskReview(null)} className="text-muted-foreground hover:text-foreground">
              <X className="w-3 h-3" />
            </button>
          </div>

          {review.scores.length > 0 && (
            <div className="space-y-1.5">
              {review.scores.map((s, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span className="text-muted-foreground w-16 truncate">{s.scorer_name}</span>
                  <div className="flex-1 h-1.5 bg-background rounded-full overflow-hidden">
                    <div className="h-full bg-amber-400 rounded-full" style={{ width: `${s.score * 10}%` }} />
                  </div>
                  <span className="font-medium w-6 text-right">{s.score}</span>
                  <span className="text-muted-foreground flex-1 truncate">{s.reason}</span>
                </div>
              ))}
            </div>
          )}

          {review.status !== 'scored' && (
            <div className="space-y-2 pt-2 border-t border-border">
              <div className="flex items-center gap-2">
                <label className="text-[11px] text-muted-foreground w-14 shrink-0">评分者</label>
                <select
                  value={scorerAgentId}
                  onChange={(e) => setScorerAgentId(e.target.value)}
                  className="flex-1 min-w-0 px-2 py-1 bg-background border border-border rounded text-xs focus:outline-none"
                >
                  <option value="">选择Agent...</option>
                  {(selectedGroup?.agents || []).map((a) => (
                    <option key={a.agent_id} value={a.agent_id}>
                      {a.name} ({a.role})
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex items-center gap-2">
                <label className="text-[11px] text-muted-foreground w-14 shrink-0">分数</label>
                <input
                  type="range"
                  min="1"
                  max="10"
                  value={scoreValue}
                  onChange={(e) => setScoreValue(parseInt(e.target.value))}
                  className="flex-1"
                />
                <span className="text-xs font-bold w-5 text-center">{scoreValue}</span>
              </div>
              <div className="flex items-center gap-2">
                <label className="text-[11px] text-muted-foreground w-14 shrink-0">能力维度</label>
                <input
                  value={scoreCapability}
                  onChange={(e) => setScoreCapability(e.target.value)}
                  placeholder="如：代码、设计、分析..."
                  className="flex-1 min-w-0 px-2 py-1 bg-background border border-border rounded text-xs focus:outline-none"
                />
              </div>
              <div className="flex items-center gap-2">
                <label className="text-[11px] text-muted-foreground w-14 shrink-0">理由</label>
                <input
                  value={scoreReason}
                  onChange={(e) => setScoreReason(e.target.value)}
                  placeholder="评分理由..."
                  className="flex-1 min-w-0 px-2 py-1 bg-background border border-border rounded text-xs focus:outline-none"
                />
              </div>
              <button
                onClick={handleSubmit}
                disabled={!scorerAgentId || !scoreReason.trim() || !scoreCapability.trim() || submitting}
                className="w-full px-3 py-1.5 bg-amber-500 text-white rounded text-xs hover:bg-amber-600 disabled:opacity-50 transition-colors"
              >
                {submitting ? '提交中...' : '提交评分'}
              </button>
            </div>
          )}
          {review.status === 'scored' && (
            <div className="text-center text-xs text-emerald-400 pt-1">✅ 所有评分已完成</div>
          )}
        </>
      )}
    </div>
  );
}

/** 子任务行（规划/执行/验证/审查）：状态徽章 + 执行过程按钮 + 重试按钮 */
function SubtaskRow({ subtask }: { subtask: AISubTask }) {
  const retryingTasks = useAIGroupsStore((s) => s.retryingTasks);
  const retryTask = useAIGroupsStore((s) => s.retryTask);
  const timelineTaskId = useAIGroupsStore((s) => s.timelineTaskId);
  const openTaskTimeline = useAIGroupsStore((s) => s.openTaskTimeline);
  const closeTaskTimeline = useAIGroupsStore((s) => s.closeTaskTimeline);

  const statusCfg = TASK_STATUS_CONFIG[subtask.status] || TASK_STATUS_CONFIG.pending;
  const stage = getSubtaskStage(subtask.goal);
  const retrying = !!retryingTasks[subtask.id];
  const isTimelineOpen = timelineTaskId === subtask.id;
  // 失败态：红色高亮 + 一键重试主按钮
  const isFailed = subtask.status === 'failed';

  return (
    <div className={`rounded-lg border transition-colors ${
      isFailed ? 'border-red-500/60 bg-red-500/10' : 'border-border/50 bg-muted/20'
    }`}>
      <div className="flex items-center gap-2 px-2.5 py-2">
        {/* 阶段标签（规划/执行/验证/审查） */}
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary shrink-0">
          {stage}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-[11px] text-muted-foreground truncate">{subtask.goal}</p>
          <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${statusCfg.bg} ${statusCfg.color}`}>
              {statusCfg.label}
            </span>
            {subtask.assigned_agent_id && (
              <span className="text-[10px] text-muted-foreground">{subtask.assigned_agent_id}</span>
            )}
            {typeof subtask.quality_score === 'number' && subtask.quality_score > 0 && (
              <span className="text-[10px] text-muted-foreground">质量 {subtask.quality_score.toFixed(1)}</span>
            )}
          </div>
        </div>
        {/* 执行过程按钮（该子任务自己的时间线） */}
        <button
          onClick={() => (isTimelineOpen ? closeTaskTimeline() : openTaskTimeline(subtask.id))}
          title="执行过程"
          className={`p-1.5 rounded transition-colors shrink-0 ${
            isTimelineOpen ? 'bg-primary/20 text-primary' : 'hover:bg-muted text-muted-foreground'
          }`}
        >
          <Activity className="w-3 h-3" />
        </button>
        {/* 重试按钮：单独重跑该任务行 */}
        <button
          onClick={() => retryTask(subtask.id, subtask.assigned_agent_id || 'soulmate')}
          disabled={retrying}
          title={isFailed ? '一键重试该失败子任务' : '重试该任务行'}
          className={`flex items-center gap-1 px-2 py-1 rounded text-[10px] disabled:opacity-50 transition-colors shrink-0 ${
            isFailed
              ? 'bg-red-500 text-white hover:bg-red-600 font-medium shadow-sm'
              : 'text-amber-400 bg-amber-500/10 hover:bg-amber-500/20'
          }`}
        >
          {retrying ? <Loader2 className="w-3 h-3 animate-spin" /> : <RotateCcw className="w-3 h-3" />}
          {retrying ? '重试中…' : isFailed ? '一键重试' : '重试'}
        </button>
      </div>
      {/* 子任务的执行过程时间线 */}
      {isTimelineOpen && (
        <div className="px-2.5 pb-2.5">
          <TaskExecutionTimeline targetLabel={`${stage}子任务`} />
        </div>
      )}
    </div>
  );
}

/** 执行过程时间线面板：历史事件 + WebSocket task_progress 实时追加 */
function TaskExecutionTimeline({ targetLabel }: { targetLabel: string }) {
  const timelineTaskId = useAIGroupsStore((s) => s.timelineTaskId);
  const timelineEvents = useAIGroupsStore((s) => s.timelineEvents);
  const timelineLoading = useAIGroupsStore((s) => s.timelineLoading);
  const openTaskTimeline = useAIGroupsStore((s) => s.openTaskTimeline);
  const closeTaskTimeline = useAIGroupsStore((s) => s.closeTaskTimeline);
  const wsConnected = useAIGroupsStore((s) => s.wsConnected);
  const listRef = useRef<HTMLDivElement>(null);

  // 滚动到底部（新事件到达 / 打字机增量时调用）
  const scrollToBottom = () => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  };

  // 新事件到达时自动滚动到底部
  useEffect(() => {
    scrollToBottom();
  }, [timelineEvents]);

  return (
    <div className="rounded-lg border border-border bg-muted/20 overflow-hidden">
      {/* 时间线面板头 */}
      <div className="flex items-center gap-1.5 px-2.5 py-2 border-b border-border/50">
        <Activity className="w-3.5 h-3.5 text-primary" />
        <span className="text-[11px] font-medium">执行过程</span>
        <span className="text-[10px] text-muted-foreground">— {targetLabel}</span>
        {/* 实时连接指示 */}
        {wsConnected && (
          <span className="flex items-center gap-1 text-[10px] text-green-500">
            <span className="relative flex h-1.5 w-1.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-green-500"></span>
            </span>
            实时
          </span>
        )}
        <div className="flex-1" />
        {/* 刷新历史事件 */}
        <button
          onClick={() => timelineTaskId && openTaskTimeline(timelineTaskId)}
          title="刷新"
          className="p-1 rounded hover:bg-muted text-muted-foreground transition-colors"
        >
          <RefreshCw className="w-3 h-3" />
        </button>
        {/* 关闭面板 */}
        <button
          onClick={closeTaskTimeline}
          title="关闭"
          className="p-1 rounded hover:bg-muted text-muted-foreground transition-colors"
        >
          <X className="w-3 h-3" />
        </button>
      </div>
      {/* 事件流 */}
      <div ref={listRef} className="max-h-64 overflow-y-auto">
        {timelineLoading ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
          </div>
        ) : timelineEvents.length === 0 ? (
          <div className="py-6 text-center">
            <p className="text-[10px] text-muted-foreground">暂无执行事件，等待 agent 开始执行…</p>
          </div>
        ) : (
          timelineEvents.map((ev, idx) => (
            <TimelineEventRow
              key={String(ev.id)}
              event={ev}
              isLatest={idx === timelineEvents.length - 1}
              onGrow={scrollToBottom}
            />
          ))
        )}
      </div>
    </div>
  );
}

/**
 * 打字机效果：逐段揭示文本（仅最新一条事件启用，模拟实时流式输出）
 * @param text 完整文本
 * @param enabled 是否启用打字机动画（最新事件启用，历史事件完整显示）
 * @param onTick 每次揭示增量时回调（用于自动滚动到底部）
 */
function useTypewriter(text: string, enabled: boolean, onTick?: () => void) {
  const [len, setLen] = useState(enabled ? 0 : text.length);
  useEffect(() => {
    if (!enabled || !text) { setLen(text.length); return; }
    setLen(0);
    const total = text.length;
    const step = Math.max(1, Math.ceil(total / 30)); // 约 30 帧揭示完
    let i = 0;
    const timer = window.setInterval(() => {
      i = Math.min(total, i + step);
      setLen(i);
      onTick?.();
      if (i >= total) window.clearInterval(timer);
    }, 30);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, enabled]);
  return len;
}

/** 单条时间线事件行：时间 + 事件类型徽章 + 内容（output/stderr 用小字号灰底 monospace） */
function TimelineEventRow({ event, isLatest, onGrow }: { event: TaskEvent; isLatest?: boolean; onGrow?: () => void }) {
  const cfg = EVENT_TYPE_CONFIG[event.event_type] || EVENT_TYPE_CONFIG.output;
  const isCodeLine = event.event_type === 'output' || event.event_type === 'stderr';
  const time = new Date(event.created_at).toLocaleTimeString('zh-CN', { hour12: false });
  // 最新事件打字机逐段揭示，历史事件直接完整显示
  const shownLen = useTypewriter(event.content || '', !!isLatest, onGrow);
  const shown = (event.content || '').slice(0, shownLen);
  return (
    <div className="px-2.5 py-1.5 border-b border-border/30 last:border-b-0">
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[10px] text-muted-foreground font-mono">{time}</span>
        <span className={`text-[10px] px-1.5 py-0.5 rounded ${cfg.bg} ${cfg.color}`}>{cfg.label}</span>
        {event.agent_id && <span className="text-[10px] text-muted-foreground">{event.agent_id}</span>}
      </div>
      {shown && (isCodeLine ? (
        <pre className="mt-1 text-[10px] font-mono text-muted-foreground bg-muted/60 rounded p-2 whitespace-pre-wrap break-words max-h-40 overflow-y-auto">
          {shown}
        </pre>
      ) : (
        <p className="mt-1 text-[11px] whitespace-pre-wrap break-words">{shown}</p>
      ))}
    </div>
  );
}
