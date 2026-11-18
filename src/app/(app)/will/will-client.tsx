"use client";

// Will 意志 · 业务工作流引擎面板 —— flow_engine 的观察窗（核心 8 组件之一，/will）
// 数据源=acp-proxy :8092 flow_engine 真数据（/flows, /flows/{id}, /flows/runs*）：
// 流程定义 / 运行实例 / 判断分支可视化（decision when/then/else 真分支图）。
// 不新建引擎——flow_engine（plugin/flow_engine.py + flow-console 面板）就是内核。

import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import {
  Sparkles, Play, RefreshCw, GitBranch, Clock, Activity, ChevronRight,
  ChevronDown, Loader2, CheckCircle, XCircle, Pause, Eye, RotateCcw,
  AlertTriangle, Zap,
} from "lucide-react";
import { PageLayout } from "@/components/page-layout";

// ── acp-proxy 直连（flow 引擎宿主 :8092，CORS *；与 plugin-manager 同款取法）──
function acpBase(): string {
  if (typeof window === "undefined") return "http://127.0.0.1:8092";
  return `http://${window.location.hostname}:8092`;
}

interface FlowNode {
  id: string;
  type: string;
  tool?: string;
  when?: string;
  then?: string;
  else_?: string;
  over?: string;
  body?: string[];
  form?: string;
  task?: string;
  role?: string;
  user?: string;
  actions?: Array<{ type: string; message?: string; channel?: string }>;
}

interface FlowDef {
  id: string;
  name: string;
  version: number;
  plugin_id: string;
  triggers: Array<{ type: string; cron?: string; event?: string }>;
  nodes: FlowNode[];
  retain_runs: number;
}

interface RunNode {
  node: string;
  type: string;
  status: string;
  attempts: number;
  retries: number;
  duration_ms: number;
  over_budget: boolean;
  error: string;
  output: string;
}

interface RunSummary {
  run_id: string;
  flow_id: string;
  flow_name: string;
  plugin_id: string;
  status: string;
  trigger: string;
  idempotency_key: string;
  replay_of: string;
  created_at: string;
  finished_at: string;
  duration_ms: number;
  error: string;
  nodes: RunNode[];
  notifications: Array<{ channel?: string; message?: string }>;
}

interface RunDetail extends RunSummary {
  log: Array<{ ts: number; node: string; event: string; detail: string }>;
  outputs: Record<string, unknown>;
  approval: { decision?: string; decided_by?: string; note?: string } | null;
}

type Tab = "flows" | "runs";

const NODE_TYPE_LABEL: Record<string, string> = {
  tool: "will.ntTool", decision: "will.ntDecision", loop: "will.ntLoop",
  approval: "will.ntApproval", action: "will.ntAction", agent: "will.ntAgent",
  human: "will.ntHuman", parallel: "will.ntParallel",
};

function statusColor(status: string): { dot: string; badge: string } {
  switch (status) {
    case "ok":
    case "completed":
      return { dot: "bg-emerald-500", badge: "bg-emerald-500/10 text-emerald-500" };
    case "failed":
      return { dot: "bg-red-500", badge: "bg-red-500/10 text-red-500" };
    case "waiting-approval":
      return { dot: "bg-amber-500", badge: "bg-amber-500/10 text-amber-500" };
    case "running":
      return { dot: "bg-blue-500 animate-pulse", badge: "bg-blue-500/10 text-blue-500" };
    case "skipped":
      return { dot: "bg-muted-foreground/40", badge: "bg-muted text-muted-foreground" };
    default:
      return { dot: "bg-muted-foreground/40", badge: "bg-muted text-muted-foreground" };
  }
}

export function WillClient() {
  const { t } = useTranslation();
  const base = acpBase();
  const [tab, setTab] = useState<Tab>("flows");
  const [flows, setFlows] = useState<FlowDef[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedFlow, setExpandedFlow] = useState<string | null>(null);
  const [flowDetails, setFlowDetails] = useState<Record<string, FlowDef>>({});
  const [selectedRun, setSelectedRun] = useState<RunDetail | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [approvalNote, setApprovalNote] = useState("");

  const showMsg = (type: "ok" | "err", text: string) => {
    setMessage({ type, text });
    setTimeout(() => setMessage(null), 4000);
  };

  const refresh = useCallback(async () => {
    try {
      const [fRes, rRes] = await Promise.all([
        fetch(`${base}/flows`),
        fetch(`${base}/flows/runs?limit=50`),
      ]);
      if (!fRes.ok || !rRes.ok) throw new Error("bad status");
      const fData = await fRes.json();
      const rData = await rRes.json();
      setFlows(fData.flows || []);
      setRuns(rData.runs || []);
      setError(null);
    } catch {
      setError(t("will.flowsError", "流程引擎不可达（acp-proxy :8092 /flows）"));
    } finally {
      setLoading(false);
    }
  }, [base, t]);

  useEffect(() => { refresh(); }, [refresh]);

  const loadFlowDetail = useCallback(async (flowId: string) => {
    if (flowDetails[flowId]) return;
    try {
      const res = await fetch(`${base}/flows/${flowId}`);
      if (!res.ok) return;
      const data = (await res.json()) as FlowDef;
      setFlowDetails((prev) => ({ ...prev, [flowId]: data }));
    } catch { /* 观察窗静默降级：列表已够用 */ }
  }, [base, flowDetails]);

  const toggleFlow = (flowId: string) => {
    const next = expandedFlow === flowId ? null : flowId;
    setExpandedFlow(next);
    if (next) loadFlowDetail(next);
  };

  const runFlow = async (flowId: string) => {
    setBusy(`run:${flowId}`);
    try {
      const res = await fetch(`${base}/flows/${flowId}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "run failed");
      showMsg("ok", t("will.runStarted", { id: data.run_id, defaultValue: "已触发 {{id}}" }));
      setTab("runs");
      await refresh();
      const detail = await fetch(`${base}/flows/runs/${data.run_id}`).then((r) => r.json());
      setSelectedRun(detail as RunDetail);
    } catch (e) {
      showMsg("err", t("will.runFailed", { msg: String(e), defaultValue: "触发失败：{{msg}}" }));
    } finally {
      setBusy(null);
    }
  };

  const openRun = async (runId: string) => {
    setBusy(`open:${runId}`);
    try {
      const detail = await fetch(`${base}/flows/runs/${runId}`).then((r) => r.json());
      setSelectedRun(detail as RunDetail);
    } catch {
      showMsg("err", t("will.networkError", "网络错误"));
    } finally {
      setBusy(null);
    }
  };

  const decide = async (runId: string, decision: "approve" | "reject") => {
    setBusy(`decide:${runId}`);
    try {
      const res = await fetch(`${base}/flows/runs/${runId}/approval`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, decided_by: "用户", note: approvalNote }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "approval failed");
      showMsg("ok", decision === "approve" ? t("will.approved", "已批准，流程继续") : t("will.rejected", "已驳回"));
      setApprovalNote("");
      await refresh();
      await openRun(runId);
    } catch (e) {
      showMsg("err", String(e));
    } finally {
      setBusy(null);
    }
  };

  const replay = async (runId: string) => {
    setBusy(`replay:${runId}`);
    try {
      const res = await fetch(`${base}/flows/runs/${runId}/replay`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "replay failed");
      showMsg("ok", t("will.replayStarted", { id: data.run_id, defaultValue: "回放已启动 {{id}}" }));
      await refresh();
      await openRun(data.run_id);
    } catch (e) {
      showMsg("err", String(e));
    } finally {
      setBusy(null);
    }
  };

  // 统计
  const totalRuns = runs.length;
  const okRuns = runs.filter((r) => r.status === "completed").length;
  const runningRuns = runs.filter((r) => r.status === "running").length;
  const waitingRuns = runs.filter((r) => r.status === "waiting-approval").length;

  return (
    <PageLayout title={t("will.title", "意志 · 工作流引擎")}>
      <div className="h-full overflow-y-auto px-3 lg:px-6 py-4 lg:py-6 space-y-4">
        {/* 头部 */}
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3">
            <Sparkles size={20} className="text-primary" />
            <div>
              <h1 className="text-base lg:text-lg font-semibold">{t("will.title", "意志 · 工作流引擎")}</h1>
              <p className="text-xs text-muted-foreground">{t("will.subtitle", "复杂工作流编排、条件触发、多分支定时业务流程")}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {message && (
              <span className={cn(
                "rounded-lg px-3 py-1.5 text-xs",
                message.type === "ok" ? "bg-emerald-500/10 text-emerald-500" : "bg-red-500/10 text-red-500",
              )}>{message.text}</span>
            )}
            <button
              onClick={() => { setLoading(true); refresh(); }}
              className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted transition-colors"
            >
              <RefreshCw size={12} /> {t("will.refresh", "刷新")}
            </button>
          </div>
        </div>

        {/* 统计条 */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 lg:gap-3">
          {[
            { label: t("will.totalFlows", "流程定义"), value: flows.length, icon: GitBranch },
            { label: t("will.totalRuns", "运行实例"), value: totalRuns, icon: Activity },
            { label: t("will.running", "运行中"), value: runningRuns, icon: Zap },
            { label: t("will.waiting", "待审批"), value: waitingRuns, icon: Clock },
          ].map((s) => (
            <div key={s.label} className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <s.icon size={12} /> {s.label}
              </div>
              <div className="mt-1 text-xl lg:text-2xl font-semibold tabular-nums">{s.value}</div>
            </div>
          ))}
        </div>

        {/* Tab 切换 */}
        <div className="flex items-center gap-1 rounded-lg bg-muted p-1 w-fit">
          {([["flows", t("will.tabFlows", "流程定义")], ["runs", t("will.tabRuns", "运行实例")]] as const).map(([id, label]) => (
            <button
              key={id}
              onClick={() => setTab(id as Tab)}
              className={cn(
                "rounded-md px-4 py-1.5 text-xs font-medium transition-colors",
                tab === id ? "bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground",
              )}
            >{label}</button>
          ))}
        </div>

        {error && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-4 text-xs text-red-500 flex items-center gap-2">
            <AlertTriangle size={14} /> {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-16 text-muted-foreground">
            <Loader2 size={16} className="animate-spin mr-2" /> {t("will.loading", "加载中…")}
          </div>
        ) : tab === "flows" ? (
          /* ── 流程定义 ── */
          <div className="space-y-2">
            {flows.length === 0 && (
              <div className="rounded-xl border border-border bg-card p-8 text-center text-sm text-muted-foreground">
                {t("will.emptyFlows", "暂无流程定义——带 flows: 的插件装载后自动注册")}
              </div>
            )}
            {flows.map((flow) => (
              <div key={flow.id} className="rounded-xl border border-border bg-card">
                <button
                  onClick={() => toggleFlow(flow.id)}
                  className="w-full flex items-center gap-3 p-3 lg:p-4 text-left hover:bg-muted/40 transition-colors"
                >
                  {expandedFlow === flow.id ? <ChevronDown size={14} className="text-muted-foreground" /> : <ChevronRight size={14} className="text-muted-foreground" />}
                  <GitBranch size={16} className="text-primary shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium truncate">{flow.name}</span>
                      <code className="text-[10px] text-muted-foreground bg-muted rounded px-1.5 py-0.5">{flow.id}</code>
                      <span className="text-[10px] text-muted-foreground bg-muted rounded px-1.5 py-0.5">{flow.plugin_id}</span>
                    </div>
                    <div className="mt-1 flex items-center gap-2 flex-wrap text-[10px] text-muted-foreground">
                      {flow.triggers.map((tr, i) => (
                        <span key={i} className="rounded bg-muted px-1.5 py-0.5">
                          {tr.type === "cron" ? `cron ${tr.cron}` : tr.type === "event" ? `${t("will.triggerEvent", "事件触发")} ${tr.event}` : t("will.triggerManual", "手动触发")}
                        </span>
                      ))}
                      <span>{t("will.nodesCount", "节点")} {flow.nodes.length}</span>
                      <span>v{flow.version}</span>
                    </div>
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); runFlow(flow.id); }}
                    disabled={busy === `run:${flow.id}`}
                    className="flex items-center gap-1 rounded-lg bg-primary/10 text-primary px-3 py-1.5 text-xs font-medium hover:bg-primary/20 transition-colors disabled:opacity-50"
                  >
                    {busy === `run:${flow.id}` ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                    {t("will.manualRun", "手动触发")}
                  </button>
                </button>

                {/* 判断分支可视化 */}
                {expandedFlow === flow.id && (
                  <div className="border-t border-border p-3 lg:p-4">
                    {flowDetails[flow.id] ? (
                      <BranchGraph defn={flowDetails[flow.id]} />
                    ) : (
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Loader2 size={12} className="animate-spin" /> {t("will.loading", "加载中…")}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          /* ── 运行实例 ── */
          <div className="space-y-2">
            {runs.length === 0 && (
              <div className="rounded-xl border border-border bg-card p-8 text-center text-sm text-muted-foreground">
                {t("will.emptyRuns", "暂无运行实例——在流程定义里「手动触发」跑一条")}
              </div>
            )}
            {runs.map((run) => {
              const color = statusColor(run.status);
              return (
                <button
                  key={run.run_id}
                  onClick={() => openRun(run.run_id)}
                  className={cn(
                    "w-full rounded-xl border border-border bg-card p-3 lg:p-4 flex items-center gap-3 text-left hover:bg-muted/40 transition-colors",
                    selectedRun?.run_id === run.run_id && "border-primary/40",
                  )}
                >
                  <span className={cn("w-2 h-2 rounded-full shrink-0", color.dot)} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium">{run.flow_name}</span>
                      <code className="text-[10px] text-muted-foreground bg-muted rounded px-1.5 py-0.5 truncate max-w-[160px]">{run.run_id}</code>
                      {run.replay_of && (
                        <span className="text-[10px] text-blue-500 bg-blue-500/10 rounded px-1.5 py-0.5">
                          {t("will.replayBadge", "回放")} ← {run.replay_of.slice(0, 16)}…
                        </span>
                      )}
                    </div>
                    <div className="mt-1 text-[10px] text-muted-foreground flex items-center gap-2 flex-wrap">
                      <span>{run.created_at}</span>
                      <span>·</span>
                      <span>{Math.round(run.duration_ms)}ms</span>
                      <span>·</span>
                      <span>{run.trigger}</span>
                    </div>
                  </div>
                  <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium", color.badge)}>
                    {run.status === "waiting-approval" ? t("will.waiting", "待审批")
                      : run.status === "completed" ? t("will.completed", "已完成")
                      : run.status === "failed" ? t("will.failed", "失败")
                      : t("will.running", "运行中")}
                  </span>
                  {busy === `open:${run.run_id}` ? <Loader2 size={14} className="animate-spin text-muted-foreground" /> : <Eye size={14} className="text-muted-foreground" />}
                </button>
              );
            })}
          </div>
        )}

        {/* 实例详情 */}
        {selectedRun && (
          <RunDetailCard
            run={selectedRun}
            busy={busy}
            approvalNote={approvalNote}
            setApprovalNote={setApprovalNote}
            onDecide={decide}
            onReplay={replay}
            onClose={() => setSelectedRun(null)}
          />
        )}
      </div>
    </PageLayout>
  );
}

// ── 判断分支可视化（decision when/then/else 真分支图）──
function BranchGraph({ defn }: { defn: FlowDef }) {
  const { t } = useTranslation();
  const nodes = defn.nodes;
  const rowH = 54;
  const height = Math.max(nodes.length * rowH + 20, 120);
  const centerX = 150;
  const typeColor: Record<string, string> = {
    tool: "#3b82f6", decision: "#f59e0b", loop: "#8b5cf6", approval: "#a855f7",
    action: "#10b981", agent: "#06b6d4", human: "#ec4899", parallel: "#6366f1",
  };

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 340 ${height}`} className="w-full min-w-[320px]" style={{ maxHeight: 360 }}>
        <defs>
          <marker id="will-arrow" markerWidth={6} markerHeight={6} refX={5} refY={3} orient="auto">
            <path d="M 0 0 L 6 3 L 0 6 z" fill="currentColor" opacity={0.6} />
          </marker>
        </defs>
        {nodes.map((node, i) => {
          const y = 14 + i * rowH;
          const color = typeColor[node.type] || "#64748b";
          return (
            <g key={node.id}>
              {/* 顺序连接线（非分支后继） */}
              {i < nodes.length - 1 && (
                <line x1={centerX} y1={y + 18} x2={centerX} y2={y + rowH - 18}
                  stroke="currentColor" strokeWidth={1} opacity={0.18} strokeDasharray="3 3" />
              )}
              <rect x={centerX - 70} y={y - 16} width={140} height={32} rx={8}
                fill={color} fillOpacity={0.1} stroke={color} strokeWidth={1.2} />
              <text x={centerX - 58} y={y + 1} fontSize={10} fill="currentColor" fontWeight={600}>{node.id}</text>
              <text x={centerX - 58} y={y + 11} fontSize={8} fill="currentColor" opacity={0.7}>
                {t(NODE_TYPE_LABEL[node.type] || node.type, node.type)}
                {node.type === "tool" && node.tool ? ` · ${node.tool}` : ""}
              </text>
              {/* decision 分支弧线 */}
              {node.type === "decision" && (
                <>
                  {node.then && (
                    <BranchEdge nodes={nodes} fromY={y} targetId={node.then}
                      side="right" color="#10b981" label={`then → ${node.then}`} />
                  )}
                  {node.else_ && (
                    <BranchEdge nodes={nodes} fromY={y} targetId={node.else_}
                      side="left" color="#ef4444" label={`else → ${node.else_}`} />
                  )}
                  {node.when && (
                    <text x={centerX + 78} y={y + 3} fontSize={8} fill="currentColor" opacity={0.75}>
                      {t("will.when", "条件")}: {node.when.length > 42 ? node.when.slice(0, 42) + "…" : node.when}
                    </text>
                  )}
                </>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function BranchEdge({ nodes, fromY, targetId, side, color, label }: {
  nodes: FlowNode[]; fromY: number; targetId: string;
  side: "left" | "right"; color: string; label: string;
}) {
  const rowH = 54;
  const centerX = 150;
  const idx = nodes.findIndex((n) => n.id === targetId);
  if (idx < 0) return null;
  const toY = 14 + idx * rowH;
  const laneX = side === "right" ? 315 : 25;
  const startX = side === "right" ? centerX + 70 : centerX - 70;
  return (
    <g>
      <path
        d={`M ${startX} ${fromY} C ${laneX} ${fromY}, ${laneX} ${toY}, ${startX} ${toY}`}
        fill="none" stroke={color} strokeWidth={1.2} opacity={0.75} strokeDasharray="4 3"
        markerEnd="url(#will-arrow)"
      />
      <text x={laneX} y={(fromY + toY) / 2 + 3} fontSize={7.5} fill={color}
        textAnchor={side === "right" ? "end" : "start"} fontWeight={600}>
        {label}
      </text>
    </g>
  );
}

// ── 实例详情（节点状态 + 事件日志 + 审批/回放）──
function RunDetailCard({ run, busy, approvalNote, setApprovalNote, onDecide, onReplay, onClose }: {
  run: RunDetail;
  busy: string | null;
  approvalNote: string;
  setApprovalNote: (v: string) => void;
  onDecide: (runId: string, d: "approve" | "reject") => void;
  onReplay: (runId: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const decisions = (run.log || []).filter((e) => e.event === "decision");
  const color = statusColor(run.status);

  return (
    <div className="rounded-xl border border-primary/30 bg-card">
      <div className="flex items-center gap-3 p-3 lg:p-4 border-b border-border">
        <span className={cn("w-2 h-2 rounded-full", color.dot)} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold">{run.flow_name}</span>
            <code className="text-[10px] text-muted-foreground bg-muted rounded px-1.5 py-0.5">{run.run_id}</code>
          </div>
          <div className="text-[10px] text-muted-foreground mt-0.5">
            {t("will.startedAt", "开始时间")} {run.created_at} · {Math.round(run.duration_ms)}ms · {run.trigger}
            {run.finished_at ? ` · ${t("will.completedAt", "完成时间")} ${run.finished_at}` : ""}
          </div>
        </div>
        <button onClick={onClose} className="rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-muted">
          {t("will.close", "关闭")}
        </button>
      </div>

      {run.error && (
        <div className="px-4 pt-3 text-xs text-red-500 flex items-center gap-2">
          <XCircle size={12} /> {run.error}
        </div>
      )}

      {/* 判断分支走向（decision 真分支） */}
      {decisions.length > 0 && (
        <div className="px-4 pt-3">
          <div className="text-xs font-semibold text-muted-foreground flex items-center gap-1.5 mb-2">
            <GitBranch size={12} /> {t("will.branchTaken", "判断分支走向")}
          </div>
          <div className="space-y-1">
            {decisions.map((d, i) => (
              <div key={i} className="rounded-lg bg-amber-500/10 border border-amber-500/20 px-3 py-2 text-xs font-mono text-amber-600 dark:text-amber-400">
                [{d.node}] {d.detail}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 节点状态 */}
      <div className="px-4 pt-3">
        <div className="text-xs font-semibold text-muted-foreground mb-2">{t("will.nodeStatus", "节点状态")}</div>
        <div className="space-y-1">
          {run.nodes.map((n) => {
            const c = statusColor(n.status);
            return (
              <div key={n.node} className="flex items-center gap-2 rounded-lg bg-muted/40 px-3 py-1.5 text-xs">
                <span className={cn("w-1.5 h-1.5 rounded-full", c.dot)} />
                <code className="font-medium">{n.node}</code>
                <span className="text-muted-foreground">({t(NODE_TYPE_LABEL[n.type] || n.type, n.type)})</span>
                <span className={cn("rounded-full px-1.5 py-0.5 text-[10px]", c.badge)}>{n.status}</span>
                {n.retries > 0 && <span className="text-[10px] text-muted-foreground">↻{n.retries}</span>}
                {n.duration_ms > 0 && <span className="text-[10px] text-muted-foreground">{Math.round(n.duration_ms)}ms</span>}
                {n.output && <span className="text-[10px] text-muted-foreground truncate max-w-[180px]">{n.output}</span>}
              </div>
            );
          })}
        </div>
      </div>

      {/* 事件日志 */}
      <div className="px-4 pt-3">
        <div className="text-xs font-semibold text-muted-foreground mb-2">{t("will.eventLog", "事件日志")}</div>
        <div className="max-h-48 overflow-y-auto space-y-0.5 rounded-lg bg-muted/40 p-2">
          {(run.log || []).map((e, i) => (
            <div key={i} className="text-[10px] font-mono text-muted-foreground flex gap-2">
              <span className="shrink-0">{new Date(e.ts * 1000).toLocaleTimeString("zh-CN", { fractionalSecondDigits: 3 })}</span>
              <span className="shrink-0 text-foreground/70">[{e.node}]</span>
              <span className="shrink-0 text-primary/80">{e.event}</span>
              <span className="truncate">{e.detail}</span>
            </div>
          ))}
        </div>
      </div>

      {/* 审批闸 / 回放 */}
      <div className="p-4 flex items-center gap-2 flex-wrap">
        {run.status === "waiting-approval" && (
          <>
            <input
              value={approvalNote}
              onChange={(e) => setApprovalNote(e.target.value)}
              placeholder={t("will.note", "审批说明（可选）")}
              className="flex-1 min-w-[160px] rounded-lg border border-border bg-background px-3 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
            <button
              onClick={() => onDecide(run.run_id, "approve")}
              disabled={busy === `decide:${run.run_id}`}
              className="flex items-center gap-1 rounded-lg bg-emerald-500/10 text-emerald-600 px-3 py-1.5 text-xs font-medium hover:bg-emerald-500/20 transition-colors"
            >
              {busy === `decide:${run.run_id}` ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle size={12} />}
              {t("will.approve", "批准")}
            </button>
            <button
              onClick={() => onDecide(run.run_id, "reject")}
              disabled={busy === `decide:${run.run_id}`}
              className="flex items-center gap-1 rounded-lg bg-red-500/10 text-red-500 px-3 py-1.5 text-xs font-medium hover:bg-red-500/20 transition-colors"
            >
              <XCircle size={12} /> {t("will.reject", "驳回")}
            </button>
          </>
        )}
        <button
          onClick={() => onReplay(run.run_id)}
          disabled={busy === `replay:${run.run_id}`}
          className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted transition-colors"
        >
          {busy === `replay:${run.run_id}` ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
          {t("will.replay", "回放")}
        </button>
        {run.approval?.decision && (
          <span className="text-[10px] text-muted-foreground">
            {t("will.decidedBy", "审批")}：{run.approval.decided_by}（{run.approval.decision}）{run.approval.note ? ` · ${run.approval.note}` : ""}
          </span>
        )}
      </div>
    </div>
  );
}
