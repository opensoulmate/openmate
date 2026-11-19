"use client";

// Will 意志 · 业务工作流引擎面板 —— flow_engine 的观察窗（核心 8 组件之一，/will）
// 数据源=acp-proxy :8092 flow_engine 真数据（/flows, /flows/{id}, /flows/runs*）：
// 流程定义 / 运行实例 / 判断分支可视化（decision when/then/else 真分支图）。
// 日常使用操作面：①一键操作（触发/带参触发/暂停/克隆/编辑参数/批量）
// ②失败可懂可修（错误归一：人话原因+建议+一键从失败节点续跑/回放）
// ③结果触达（微信通知送达状态可见：跑完/失败/待审批/催办）
// ④运行历史（按流程/时间筛选 + 成败统计/成功率/耗时）。
// 不新建引擎——flow_engine（plugin/flow_engine.py + flow-console 面板）就是内核。

import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import {
  Sparkles, Play, RefreshCw, GitBranch, Clock, Activity, ChevronRight,
  ChevronDown, Loader2, CheckCircle, XCircle, Pause, Eye, RotateCcw,
  AlertTriangle, Zap, Copy, Pencil, ListChecks, Send, BellRing,
  Wrench, Filter, SquareCheck,
} from "lucide-react";
import { PageLayout } from "@/components/page-layout";
import { genRectPath } from "@/components/ui/super-ellipse";
import { FlowStudio, FlowEditDialog } from "./flow-studio";

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

interface FlowTrigger {
  type: string;
  cron?: string;
  event?: string;
}

interface FlowDef {
  id: string;
  name: string;
  version: number;
  plugin_id: string;
  triggers: FlowTrigger[];
  nodes: FlowNode[];
  retain_runs: number;
  paused?: boolean;
}

interface ErrorNorm {
  code: string;
  human: string;
  detail: string;
  suggestion: string;
  retryable: boolean;
  where?: string;
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
  error_norm?: ErrorNorm | null;
  output: string;
  // 双轴（reported done ≠ verified）：黄=自报，绿=验收翻档
  status_axis?: string;
  confidence?: string;
  claim_source?: string;
  executed_by?: string;
  verified?: boolean;
  verified_by?: string;
  verify_evidence?: string;
  settle_rejects?: number;
  fix_rounds?: number;
}

interface NotifyNote {
  id?: string;
  channel?: string;
  kind?: string;
  message?: string;
  ts?: number;
  status?: string;
  message_id?: string;
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
  error_norm?: ErrorNorm | null;
  failed_node?: string;
  verdict?: string;              // verified | done·unverified | 进行态原词
  verdict_reasons?: Array<{ node: string; reason: string }>;
  nodes: RunNode[];
  notifications: NotifyNote[];
}

interface RunDetail extends RunSummary {
  log: Array<{ ts: number; node: string; event: string; detail: string }>;
  outputs: Record<string, unknown>;
  approval: { decision?: string; decided_by?: string; note?: string } | null;
}

interface Stats {
  total: number;
  completed: number;
  failed: number;
  running: number;
  waiting_approval: number;
  success_rate: number | null;
  avg_duration_ms: number | null;
  max_duration_ms: number | null;
}

type Tab = "flows" | "runs" | "studio";
type TimeRange = "all" | "today" | "7d" | "30d";

const NODE_TYPE_LABEL: Record<string, string> = {
  tool: "will.ntTool", decision: "will.ntDecision", loop: "will.ntLoop",
  approval: "will.ntApproval", action: "will.ntAction", agent: "will.ntAgent",
  human: "will.ntHuman", parallel: "will.ntParallel", verify: "will.ntVerify",
};

/** 双轴配色（监督闭环一列定生死）：verified 绿 | reported done 黄 | not run 灰 */
function confidenceColor(confidence?: string): { dot: string; badge: string } {
  if (confidence === "verified") {
    return { dot: "bg-emerald-500", badge: "bg-emerald-500/10 text-emerald-500" };
  }
  if (confidence === "reported done") {
    return { dot: "bg-amber-500", badge: "bg-amber-500/10 text-amber-500" };
  }
  return { dot: "bg-muted-foreground/40", badge: "bg-muted text-muted-foreground" };
}

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

function rangeSince(range: TimeRange): string {
  const now = Date.now() / 1000;
  if (range === "today") {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return String(d.getTime() / 1000);
  }
  if (range === "7d") return String(now - 7 * 86400);
  if (range === "30d") return String(now - 30 * 86400);
  return "";
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
  // 一键操作面
  const [checked, setChecked] = useState<string[]>([]);
  const [editFlow, setEditFlow] = useState<FlowDef | null>(null);
  const [runFlowTarget, setRunFlowTarget] = useState<FlowDef | null>(null);
  // 运行历史筛选 + 统计
  const [filterFlow, setFilterFlow] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterRange, setFilterRange] = useState<TimeRange>("all");
  const [stats, setStats] = useState<Stats | null>(null);

  const showMsg = (type: "ok" | "err", text: string) => {
    setMessage({ type, text });
    setTimeout(() => setMessage(null), 4000);
  };

  const refresh = useCallback(async () => {
    try {
      const since = rangeSince(filterRange);
      const [fRes, rRes, sRes] = await Promise.all([
        fetch(`${base}/flows`),
        fetch(`${base}/flows/runs?limit=100`
          + `&flow_id=${encodeURIComponent(filterFlow)}`
          + `&status=${encodeURIComponent(filterStatus)}`
          + `&since=${encodeURIComponent(since)}`),
        fetch(`${base}/flows/runs/stats?flow_id=${encodeURIComponent(filterFlow)}`
          + `&since=${encodeURIComponent(since)}`),
      ]);
      if (!fRes.ok || !rRes.ok) throw new Error("bad status");
      const fData = await fRes.json();
      const rData = await rRes.json();
      setFlows(fData.flows || []);
      setRuns(rData.runs || []);
      if (sRes.ok) setStats((await sRes.json()) as Stats);
      setError(null);
    } catch {
      setError(t("will.flowsError", "流程引擎不可达（acp-proxy :8092 /flows）"));
    } finally {
      setLoading(false);
    }
  }, [base, t, filterFlow, filterStatus, filterRange]);

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

  const toggleCheck = (flowId: string) => {
    setChecked((prev) => prev.includes(flowId)
      ? prev.filter((id) => id !== flowId) : [...prev, flowId]);
  };

  // ── 一键操作 ──
  const runFlow = async (flowId: string, inputs?: Record<string, unknown>, force?: boolean) => {
    setBusy(`run:${flowId}`);
    try {
      const res = await fetch(`${base}/flows/${flowId}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ inputs: inputs || {}, force: !!force }),
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
      setRunFlowTarget(null);
    }
  };

  const pauseFlow = async (flowId: string, paused: boolean) => {
    setBusy(`pause:${flowId}`);
    try {
      const res = await fetch(`${base}/flows/${flowId}/${paused ? "pause" : "resume"}`,
        { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "pause failed");
      showMsg("ok", paused ? t("will.pausedOk", "已暂停") : t("will.resumedOk", "已恢复"));
      await refresh();
    } catch (e) {
      showMsg("err", String(e));
    } finally {
      setBusy(null);
    }
  };

  const cloneFlow = async (flowId: string) => {
    setBusy(`clone:${flowId}`);
    try {
      const res = await fetch(`${base}/flows/${flowId}/clone`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "clone failed");
      showMsg("ok", t("will.clonedOk", { id: data.id, defaultValue: "已克隆为 {{id}}" }));
      await refresh();
    } catch (e) {
      showMsg("err", String(e));
    } finally {
      setBusy(null);
    }
  };

  const batchOp = async (op: "run" | "pause" | "resume" | "delete") => {
    if (checked.length === 0) return;
    if (op === "delete" && !window.confirm(t("will.confirmBatchDelete",
      { count: checked.length, defaultValue: "确认删除所选 {{count}} 条流程？其实例与幂等键将一并清空" }))) {
      return;
    }
    setBusy(`batch:${op}`);
    try {
      const res = await fetch(`${base}/flows/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ op, flow_ids: checked }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "batch failed");
      showMsg("ok", t("will.batchDone", {
        ok: data.ok, count: data.count,
        defaultValue: "批量完成：{{ok}}/{{count}} 成功",
      }));
      setChecked([]);
      await refresh();
    } catch (e) {
      showMsg("err", String(e));
    } finally {
      setBusy(null);
    }
  };

  // ── 实例操作 ──
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

  const retryRun = async (runId: string) => {
    setBusy(`retry:${runId}`);
    try {
      const res = await fetch(`${base}/flows/runs/${runId}/retry`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "retry failed");
      showMsg("ok", t("will.retryStarted", { id: data.run_id, defaultValue: "已从失败节点续跑 {{id}}" }));
      await refresh();
      await openRun(data.run_id);
    } catch (e) {
      showMsg("err", String(e));
    } finally {
      setBusy(null);
    }
  };

  // 统计（列表口径，统计条用服务端 /flows/runs/stats）
  const totalRuns = runs.length;
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
          {([["flows", t("will.tabFlows", "流程定义")], ["runs", t("will.tabHistory", "运行历史")], ["studio", t("will.tabStudio", "建流工坊")]] as const).map(([id, label]) => (
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
          /* ── 流程定义（一键操作 + 批量） ── */
          <div className="space-y-2">
            {checked.length > 0 && (
              <div className="rounded-xl border border-primary/30 bg-primary/5 p-3 flex items-center gap-2 flex-wrap">
                <ListChecks size={14} className="text-primary" />
                <span className="text-xs text-primary font-medium">
                  {t("will.selectedCount", { count: checked.length, defaultValue: "已选 {{count}} 条" })}
                </span>
                <div className="flex-1" />
                {([
                  ["run", t("will.batchRun", "批量触发"), Play],
                  ["pause", t("will.batchPause", "批量暂停"), Pause],
                  ["resume", t("will.batchResume", "批量恢复"), RotateCcw],
                  ["delete", t("will.batchDelete", "批量删除"), XCircle],
                ] as const).map(([op, label, Icon]) => (
                  <button
                    key={op}
                    onClick={() => batchOp(op as "run" | "pause" | "resume" | "delete")}
                    disabled={busy === `batch:${op}`}
                    className={cn(
                      "flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                      op === "delete"
                        ? "bg-red-500/10 text-red-500 hover:bg-red-500/20"
                        : "bg-muted text-foreground hover:bg-muted/70",
                    )}
                  >
                    {busy === `batch:${op}` ? <Loader2 size={12} className="animate-spin" /> : <Icon size={12} />}
                    {label}
                  </button>
                ))}
                <button
                  onClick={() => setChecked([])}
                  className="rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted"
                >{t("will.clearSelection", "清空选择")}</button>
              </div>
            )}
            {flows.length === 0 && (
              <div className="rounded-xl border border-border bg-card p-8 text-center text-sm text-muted-foreground">
                {t("will.emptyFlows", "暂无流程定义——带 flows: 的插件装载后自动注册")}
              </div>
            )}
            {flows.map((flow) => (
              <div key={flow.id} className="rounded-xl border border-border bg-card">
                <div className="w-full flex items-center gap-3 p-3 lg:p-4">
                  <input
                    type="checkbox"
                    checked={checked.includes(flow.id)}
                    onChange={() => toggleCheck(flow.id)}
                    aria-label={t("will.selectFlow", "选择流程")}
                    className="w-3.5 h-3.5 accent-primary shrink-0 cursor-pointer"
                  />
                  <button
                    onClick={() => toggleFlow(flow.id)}
                    className="flex items-center gap-3 flex-1 min-w-0 text-left hover:bg-muted/40 rounded-lg transition-colors py-1"
                  >
                    {expandedFlow === flow.id ? <ChevronDown size={14} className="text-muted-foreground" /> : <ChevronRight size={14} className="text-muted-foreground" />}
                    <GitBranch size={16} className="text-primary shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium truncate">{flow.name}</span>
                        <code className="text-[10px] text-muted-foreground bg-muted rounded px-1.5 py-0.5">{flow.id}</code>
                        <span className="text-[10px] text-muted-foreground bg-muted rounded px-1.5 py-0.5">{flow.plugin_id}</span>
                        {flow.paused && (
                          <span className="text-[10px] font-medium rounded px-1.5 py-0.5 bg-amber-500/10 text-amber-500">
                            {t("will.pausedBadge", "已暂停")}
                          </span>
                        )}
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
                  </button>
                  {/* 一键操作按钮组 */}
                  <div className="flex items-center gap-1.5 flex-wrap shrink-0">
                    <button
                      onClick={() => runFlow(flow.id)}
                      disabled={busy === `run:${flow.id}` || flow.paused}
                      title={flow.paused ? t("will.pausedHint", "已暂停：恢复后可触发") : t("will.manualRun", "立即触发")}
                      className="flex items-center gap-1 rounded-lg bg-primary/10 text-primary px-2.5 py-1.5 text-xs font-medium hover:bg-primary/20 transition-colors disabled:opacity-40"
                    >
                      {busy === `run:${flow.id}` ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                      {t("will.manualRun", "立即触发")}
                    </button>
                    <button
                      onClick={() => setRunFlowTarget(flow)}
                      title={t("will.runWithParams", "带参触发")}
                      className="flex items-center gap-1 rounded-lg border border-border px-2.5 py-1.5 text-xs hover:bg-muted transition-colors"
                    >
                      <Send size={12} /> {t("will.runWithParams", "带参触发")}
                    </button>
                    <button
                      onClick={() => pauseFlow(flow.id, !flow.paused)}
                      disabled={busy === `pause:${flow.id}`}
                      className="flex items-center gap-1 rounded-lg border border-border px-2.5 py-1.5 text-xs hover:bg-muted transition-colors"
                    >
                      {busy === `pause:${flow.id}` ? <Loader2 size={12} className="animate-spin" />
                        : flow.paused ? <RotateCcw size={12} /> : <Pause size={12} />}
                      {flow.paused ? t("will.resume", "恢复") : t("will.pause", "暂停")}
                    </button>
                    <button
                      onClick={() => cloneFlow(flow.id)}
                      disabled={busy === `clone:${flow.id}`}
                      title={t("will.clone", "克隆流程")}
                      className="flex items-center gap-1 rounded-lg border border-border px-2.5 py-1.5 text-xs hover:bg-muted transition-colors"
                    >
                      {busy === `clone:${flow.id}` ? <Loader2 size={12} className="animate-spin" /> : <Copy size={12} />}
                      {t("will.clone", "克隆")}
                    </button>
                    <button
                      onClick={() => setEditFlow(flow)}
                      title={t("will.editParams", "编辑参数")}
                      className="flex items-center gap-1 rounded-lg border border-border px-2.5 py-1.5 text-xs hover:bg-muted transition-colors"
                    >
                      <Pencil size={12} /> {t("will.editParams", "编辑")}
                    </button>
                  </div>
                </div>

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
        ) : tab === "runs" ? (
          /* ── 运行历史（按流程/时间筛选 + 成败统计） ── */
          <div className="space-y-3">
            {/* 筛选条 */}
            <div className="rounded-xl border border-border bg-card p-3 flex items-center gap-2 flex-wrap">
              <Filter size={13} className="text-muted-foreground" />
              <select
                value={filterFlow}
                onChange={(e) => setFilterFlow(e.target.value)}
                className="rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-primary/30"
              >
                <option value="">{t("will.filterAllFlows", "全部流程")}</option>
                {flows.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
              </select>
              <select
                value={filterStatus}
                onChange={(e) => setFilterStatus(e.target.value)}
                className="rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-primary/30"
              >
                <option value="">{t("will.filterAllStatus", "全部状态")}</option>
                <option value="completed">{t("will.completed", "已完成")}</option>
                <option value="failed">{t("will.failed", "失败")}</option>
                <option value="running">{t("will.running", "运行中")}</option>
                <option value="waiting-approval">{t("will.waiting", "待审批")}</option>
              </select>
              <select
                value={filterRange}
                onChange={(e) => setFilterRange(e.target.value as TimeRange)}
                className="rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-primary/30"
              >
                <option value="all">{t("will.rangeAll", "全部时间")}</option>
                <option value="today">{t("will.rangeToday", "今天")}</option>
                <option value="7d">{t("will.range7d", "近 7 天")}</option>
                <option value="30d">{t("will.range30d", "近 30 天")}</option>
              </select>
            </div>

            {/* 成败统计 + 耗时 */}
            {stats && (
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 lg:gap-3">
                {[
                  { label: t("will.statsTotal", "运行总数"), value: stats.total, icon: Activity },
                  { label: t("will.statsOk", "成功"), value: stats.completed, icon: CheckCircle },
                  { label: t("will.statsFail", "失败"), value: stats.failed, icon: XCircle },
                  {
                    label: t("will.successRate", "成功率"),
                    value: stats.success_rate === null ? "—" : `${stats.success_rate}%`,
                    icon: SquareCheck,
                  },
                ].map((s) => (
                  <div key={s.label} className="rounded-xl border border-border bg-card p-3">
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <s.icon size={12} /> {s.label}
                    </div>
                    <div className="mt-1 text-lg font-semibold tabular-nums">{s.value}</div>
                  </div>
                ))}
                <div className="rounded-xl border border-border bg-card p-3 col-span-2 lg:col-span-4 flex items-center gap-4 text-xs text-muted-foreground flex-wrap">
                  <Clock size={12} />
                  <span>
                    {t("will.avgDuration", "平均耗时")}：
                    <b className="text-foreground tabular-nums">
                      {stats.avg_duration_ms === null ? "—" : `${Math.round(stats.avg_duration_ms)}ms`}
                    </b>
                  </span>
                  <span>
                    {t("will.maxDuration", "最差耗时")}：
                    <b className="text-foreground tabular-nums">
                      {stats.max_duration_ms === null ? "—" : `${Math.round(stats.max_duration_ms)}ms`}
                    </b>
                  </span>
                  <span>
                    {t("will.waiting", "待审批")}：<b className="text-foreground tabular-nums">{stats.waiting_approval}</b>
                  </span>
                </div>
              </div>
            )}

            {runs.length === 0 && (
              <div className="rounded-xl border border-border bg-card p-8 text-center text-sm text-muted-foreground">
                {t("will.emptyRuns", "暂无运行实例——在流程定义里「立即触发」跑一条")}
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
                      {run.status === "failed" && run.error_norm && (
                        <span className="text-[10px] text-red-500 bg-red-500/10 rounded px-1.5 py-0.5 truncate max-w-[220px]">
                          {run.error_norm.human}
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
                  <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium",
                    run.status === "completed"
                      ? confidenceColor(run.verdict === "verified" ? "verified" : "reported done").badge
                      : color.badge)}>
                    {run.status === "waiting-approval" ? t("will.waiting", "待审批")
                      : run.status === "completed"
                        ? (run.verdict === "verified"
                            ? t("will.verified", "已验证")
                            : t("will.doneUnverified", "完成·未验证"))
                      : run.status === "failed" ? t("will.failed", "失败")
                      : t("will.running", "运行中")}
                  </span>
                  {busy === `open:${run.run_id}` ? <Loader2 size={14} className="animate-spin text-muted-foreground" /> : <Eye size={14} className="text-muted-foreground" />}
                </button>
              );
            })}
          </div>
        ) : (
          /* ── 建流工坊（一句话建流/模板库/可视化编辑） ── */
          <FlowStudio
            flows={flows.map((f) => ({ id: f.id, name: f.name, plugin_id: f.plugin_id }))}
            onSaved={() => { setFlowDetails({}); refresh(); }}
          />
        )}

        {/* 实例详情（失败可懂可修 + 通知送达） */}
        {selectedRun && (
          <RunDetailCard
            run={selectedRun}
            busy={busy}
            approvalNote={approvalNote}
            setApprovalNote={setApprovalNote}
            onDecide={decide}
            onReplay={replay}
            onRetry={retryRun}
            onClose={() => setSelectedRun(null)}
          />
        )}

        {/* 带参触发对话框 */}
        {runFlowTarget && (
          <RunParamsDialog
            flow={runFlowTarget}
            busy={busy === `run:${runFlowTarget.id}`}
            onCancel={() => setRunFlowTarget(null)}
            onRun={(inputs, force) => runFlow(runFlowTarget.id, inputs, force)}
          />
        )}

        {/* 统一流程编辑器（表单=主，JSON=高级模式；与建流工坊同一入口） */}
        {editFlow && (
          <FlowEditDialog
            flow={editFlow}
            onClose={() => setEditFlow(null)}
            onSaved={() => {
              setEditFlow(null);
              setFlowDetails({});
              refresh();
            }}
            showMsg={showMsg}
          />
        )}
      </div>
    </PageLayout>
  );
}

// ── 带参触发对话框（编辑运行输入 + 暂停态强制触发）──
function RunParamsDialog({ flow, busy, onCancel, onRun }: {
  flow: FlowDef;
  busy: boolean;
  onCancel: () => void;
  onRun: (inputs: Record<string, unknown>, force: boolean) => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState("{}");
  const [force, setForce] = useState(false);
  const [err, setErr] = useState("");
  const submit = () => {
    try {
      const inputs = JSON.parse(text || "{}");
      if (typeof inputs !== "object" || Array.isArray(inputs)) throw new Error("not object");
      setErr("");
      onRun(inputs, force);
    } catch {
      setErr(t("will.jsonInvalid", "JSON 不合法（须为对象，如 {\"name\": \"团队\"}）"));
    }
  };
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <div className="rounded-xl border border-border bg-card w-full max-w-md p-4 space-y-3">
        <div className="text-sm font-semibold">
          {t("will.runWithParams", "带参触发")} · {flow.name}
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={6}
          spellCheck={false}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-primary/30"
        />
        {err && <div className="text-xs text-red-500">{err}</div>}
        {flow.paused && (
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)}
              className="w-3.5 h-3.5 accent-primary" />
            {t("will.forceRun", "流程已暂停，勾选后强制触发一次")}
          </label>
        )}
        <div className="flex items-center gap-2 justify-end">
          <button onClick={onCancel}
            className="rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
            {t("will.cancel", "取消")}
          </button>
          <button onClick={submit} disabled={busy}
            className="flex items-center gap-1 rounded-lg bg-primary/10 text-primary px-3 py-1.5 text-xs font-medium hover:bg-primary/20 disabled:opacity-50">
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
            {t("will.triggerNow", "立即触发")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 判断分支可视化（decision when/then/else 真分支图；节点=超椭圆设计语言）──
function BranchGraph({ defn }: { defn: FlowDef }) {
  const { t } = useTranslation();
  const nodes = defn.nodes;
  const rowH = 54;
  const height = Math.max(nodes.length * rowH + 20, 120);
  const centerX = 150;
  const nodePath = genRectPath(140, 32);
  const typeColor: Record<string, string> = {
    tool: "var(--color-info)", decision: "var(--color-warning)", loop: "var(--color-warning)",
    approval: "var(--color-success)", action: "var(--color-info)", agent: "var(--color-primary)",
    human: "var(--color-success)", parallel: "var(--color-info)",
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
          const color = typeColor[node.type] || "var(--color-muted-foreground)";
          return (
            <g key={node.id}>
              {/* 顺序连接线（非分支后继） */}
              {i < nodes.length - 1 && (
                <line x1={centerX} y1={y + 18} x2={centerX} y2={y + rowH - 18}
                  stroke="currentColor" strokeWidth={1} opacity={0.18} strokeDasharray="3 3" />
              )}
              <path d={nodePath} transform={`translate(${centerX - 70}, ${y - 16})`}
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
                      side="right" color="var(--color-branch-then)" label={`then → ${node.then}`} />
                  )}
                  {node.else_ && (
                    <BranchEdge nodes={nodes} fromY={y} targetId={node.else_}
                      side="left" color="var(--color-branch-else)" label={`else → ${node.else_}`} />
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

// ── 实例详情（节点状态 + 事件日志 + 失败归因 + 通知送达 + 审批/重试/回放）──
function RunDetailCard({ run, busy, approvalNote, setApprovalNote, onDecide, onReplay, onRetry, onClose }: {
  run: RunDetail;
  busy: string | null;
  approvalNote: string;
  setApprovalNote: (v: string) => void;
  onDecide: (runId: string, d: "approve" | "reject") => void;
  onReplay: (runId: string) => void;
  onRetry: (runId: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const decisions = (run.log || []).filter((e) => e.event === "decision");
  const color = statusColor(run.status);
  const errNorm = run.error_norm;
  const notes = run.notifications || [];
  const kindLabel: Record<string, string> = {
    completed: "will.noteCompleted", failed: "will.noteFailed",
    "waiting-approval": "will.noteWaiting", urge: "will.noteUrge",
  };

  return (
    <div className="rounded-xl border border-primary/30 bg-card">
      <div className="flex items-center gap-3 p-3 lg:p-4 border-b border-border">
        <span className={cn("w-2 h-2 rounded-full", color.dot)} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold">{run.flow_name}</span>
            <code className="text-[10px] text-muted-foreground bg-muted rounded px-1.5 py-0.5">{run.run_id}</code>
            {run.status === "completed" && (
              <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium",
                confidenceColor(run.verdict === "verified" ? "verified" : "reported done").badge)}>
                {run.verdict === "verified"
                  ? t("will.verified", "已验证")
                  : t("will.doneUnverified", "完成·未验证")}
              </span>
            )}
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

      {/* 失败可懂：人话原因 + 建议（错误归一） */}
      {(errNorm || run.error) && (
        <div className="mx-4 mt-3 rounded-xl border border-red-500/25 bg-red-500/5 p-3 space-y-1.5">
          <div className="flex items-center gap-2 text-xs font-semibold text-red-500">
            <Wrench size={13} />
            {errNorm ? errNorm.human : run.error}
            {run.failed_node && (
              <code className="text-[10px] bg-red-500/10 rounded px-1.5 py-0.5">
                {t("will.failedNode", "失败节点")}：{run.failed_node}
              </code>
            )}
            {errNorm && (
              <span className={cn(
                "text-[10px] rounded px-1.5 py-0.5 font-medium",
                errNorm.retryable ? "bg-emerald-500/10 text-emerald-500" : "bg-muted text-muted-foreground",
              )}>
                {errNorm.retryable ? t("will.retryable", "可重试") : t("will.notRetryable", "重试无益")}
              </span>
            )}
          </div>
          {errNorm?.suggestion && (
            <div className="text-xs text-foreground/80">
              💡 {errNorm.suggestion}
            </div>
          )}
          {errNorm?.detail && errNorm.detail !== errNorm.human && (
            <details className="text-[10px] text-muted-foreground">
              <summary className="cursor-pointer select-none">{t("will.rawError", "原始错误（排查）")}</summary>
              <pre className="mt-1 whitespace-pre-wrap break-all font-mono">{errNorm.detail}</pre>
            </details>
          )}
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

      {/* done·unverified 归因（监督日清：为什么还不敢标绿） */}
      {(run.verdict_reasons || []).length > 0 && (
        <div className="px-4 pt-3">
          <div className="text-xs font-semibold text-amber-500 mb-1">
            {t("will.unverifiedReasons", "done·unverified 归因")}
          </div>
          <div className="text-[10px] text-muted-foreground">
            {(run.verdict_reasons || []).map((r) => `${r.node}: ${r.reason}`).join(" · ")}
          </div>
        </div>
      )}

      {/* 节点状态 */}
      <div className="px-4 pt-3">
        <div className="text-xs font-semibold text-muted-foreground mb-2">{t("will.nodeStatus", "节点状态")}</div>
        <div className="space-y-1">
          {run.nodes.map((n) => {
            const c = statusColor(n.status);
            const isFailed = n.node === run.failed_node;
            return (
              <div key={n.node} className={cn(
                "flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs",
                isFailed ? "bg-red-500/10 border border-red-500/20" : "bg-muted/40",
              )}>
                <span className={cn("w-1.5 h-1.5 rounded-full", c.dot)} />
                <code className="font-medium">{n.node}</code>
                <span className="text-muted-foreground">({t(NODE_TYPE_LABEL[n.type] || n.type, n.type)})</span>
                <span className={cn("rounded-full px-1.5 py-0.5 text-[10px]",
                  n.status === "ok" ? confidenceColor(n.confidence).badge : c.badge)}>
                  {n.status === "ok"
                    ? (n.verified
                        ? t("will.verified", "已验证")
                        : t("will.reportedDone", "已交付·未验证"))
                    : n.status}
                </span>
                {n.status === "ok" && n.verified && (
                  <span className="text-[10px] text-emerald-500 truncate max-w-[140px]">✓ {n.verified_by}</span>
                )}
                {n.status === "ok" && !n.verified && (
                  <span className="text-[10px] text-amber-500">{t("will.selfReport", "自报")}{n.settle_rejects ? ` ×${n.settle_rejects}` : ""}</span>
                )}
                {n.retries > 0 && <span className="text-[10px] text-muted-foreground">↻{n.retries}</span>}
                {n.duration_ms > 0 && <span className="text-[10px] text-muted-foreground">{Math.round(n.duration_ms)}ms</span>}
                {isFailed && n.error_norm && (
                  <span className="text-[10px] text-red-500 truncate max-w-[200px]">{n.error_norm.human}</span>
                )}
                {!isFailed && n.output && <span className="text-[10px] text-muted-foreground truncate max-w-[180px]">{n.output}</span>}
              </div>
            );
          })}
        </div>
      </div>

      {/* 结果触达（微信通知送达状态） */}
      {notes.length > 0 && (
        <div className="px-4 pt-3">
          <div className="text-xs font-semibold text-muted-foreground flex items-center gap-1.5 mb-2">
            <BellRing size={12} /> {t("will.notifications", "结果触达")}
          </div>
          <div className="space-y-1">
            {notes.map((n, i) => (
              <div key={n.id || i} className="rounded-lg bg-muted/40 px-3 py-1.5 text-xs flex items-start gap-2">
                <span className={cn(
                  "rounded px-1.5 py-0.5 text-[10px] font-medium shrink-0",
                  (n.status || "").startsWith("delivered") ? "bg-emerald-500/10 text-emerald-500"
                    : (n.status || "").startsWith("error") ? "bg-red-500/10 text-red-500"
                    : (n.status || "").startsWith("deferred") ? "bg-amber-500/10 text-amber-500"
                    : n.status ? "bg-blue-500/10 text-blue-500"
                    : "bg-muted text-muted-foreground",
                )}>
                  {n.kind && kindLabel[n.kind] ? t(kindLabel[n.kind], n.kind) : (n.channel || "in-app")}
                  {n.status ? <>{" · "}
                    {(n.status || "").startsWith("delivered") ? t("will.noteDelivered", "已送达")
                      : (n.status || "").startsWith("error") ? t("will.noteError", "投递失败")
                      : (n.status || "").startsWith("deferred") ? t("will.noteDeferred", "待补投")
                      : t("will.noteSending", "发送中")}
                  </> : null}
                </span>
                <span className="text-muted-foreground whitespace-pre-wrap break-all">{n.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

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

      {/* 审批闸 / 一键重试（从失败节点续跑）/ 回放 */}
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
        {run.status === "failed" && (
          <button
            onClick={() => onRetry(run.run_id)}
            disabled={busy === `retry:${run.run_id}`}
            title={t("will.retryHint", "从失败节点原地续跑（同实例）")}
            className="flex items-center gap-1 rounded-lg bg-primary/10 text-primary px-3 py-1.5 text-xs font-medium hover:bg-primary/20 transition-colors disabled:opacity-50"
          >
            {busy === `retry:${run.run_id}` ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />}
            {t("will.retryFromFailed", "一键重试（从失败节点续跑）")}
          </button>
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
