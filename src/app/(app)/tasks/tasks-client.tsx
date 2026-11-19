"use client";
import { useState, useEffect, useCallback } from "react";
import {
  Inbox, RefreshCw, Hand, HeartPulse, Send, BellRing, XCircle, CheckCircle2,
  BadgeCheck,
} from "lucide-react";
import { PageLayout } from "@/components/page-layout";
import { TriageRecordsPanel } from "@/components/task-triage-gate";

/* 任务收发台 — 对接 acp-proxy /collab/*（每人任务收件箱）
 * 数据源：plugin.collab_api（工单账=flow_collab.WorkOrderStore）
 * 三桶：在办 open / 交付 delivered / 超时 overdue（+已关闭 closed）
 * 动作：认领（抢占锁）/ 心跳续租 / 交付回流（流程续跑）/ 催办（Pulse 催办同源）
 * 预算看板：三档策略（company/agent/project）状态 ok/warning/hard_stop 直出。
 * 注：/collab/* 在 acp-proxy :8092（非 OpenSoul :8090），须直连 acp 宿主。 */

function acpBase(): string {
  if (typeof window === "undefined") return "http://127.0.0.1:8092";
  return `http://${window.location.hostname}:8092`;
}

export interface TaskView {
  task_id: string;
  kind: string;                 // agent | human | approval
  status: string;
  bucket: string;               // open | delivered | overdue | closed
  title: string;
  instruction: string;
  form: string;
  role: string;
  assignee: string;
  target_agent: string;
  due_at: number;
  delivered_at: number | null;
  delivered_by: string;
  result: unknown;
  claimed_by: string;
  lease_expires_at: number;
  a2a_task_id: string;
  reminders: number;
  over_budget: boolean;
  // 双轴（reported done ≠ verified）：黄=自报交付，绿=验收翻档
  verified?: boolean;
  verified_by?: string;
  verify_evidence?: string;
  claim_source?: string;
  confidence?: string;
  status_axis?: string;
  settle_rejects?: number;
}

export interface BudgetPolicyView {
  id: string;
  scope: string;
  key: string;
  window: string;
  max_cost_usd: number;
  max_tokens: number;
  state: string;                // ok | warning | hard_stop
}

export const BUCKETS: { key: string; label: string }[] = [
  { key: "open", label: "在办" },
  { key: "delivered", label: "交付" },
  { key: "overdue", label: "超时" },
  { key: "closed", label: "已关闭" },
];

export function bucketLabel(bucket: string): string {
  return BUCKETS.find((b) => b.key === bucket)?.label ?? bucket;
}

export function kindLabel(kind: string): string {
  return kind === "agent" ? "Agent 任务" : kind === "approval" ? "审批" : "人工任务";
}

export function dueText(dueAt: number, now: number): string {
  if (!dueAt) return "—";
  const diff = dueAt - now;
  const hours = Math.abs(diff) / 3600;
  const human = hours >= 1 ? `${hours.toFixed(1)} 小时` : `${Math.round(Math.abs(diff) / 60)} 分钟`;
  return diff >= 0 ? `剩 ${human}` : `超时 ${human}`;
}

export function resultText(result: unknown): string {
  if (result == null) return "—";
  if (typeof result === "string") return result;
  try {
    return JSON.stringify(result);
  } catch {
    return String(result);
  }
}

/** 纯展示工单表（render-test 可 SSR 断言）：动作回调注入，零数据获取。 */
export function TaskTable(props: {
  tasks: TaskView[];
  now: number;
  busyId?: string;
  onClaim?: (t: TaskView) => void;
  onHeartbeat?: (t: TaskView) => void;
  onDeliver?: (t: TaskView, result: string) => void;
  onRemind?: (t: TaskView) => void;
  onVerify?: (t: TaskView, evidence: string) => void;
  deliverId?: string;
  deliverDraft?: string;
  setDeliverId?: (id: string) => void;
  setDeliverDraft?: (text: string) => void;
  verifyId?: string;
  verifyDraft?: string;
  setVerifyId?: (id: string) => void;
  setVerifyDraft?: (text: string) => void;
}) {
  const { tasks, now } = props;
  if (!tasks.length) {
    return (
      <div className="text-sm text-muted-foreground py-8 text-center">
        （本桶暂无工单）
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {tasks.map((t) => (
        <div
          key={t.task_id}
          className="rounded-lg border border-border/60 p-3 flex flex-col gap-2"
          data-testid="task-row"
        >
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-xs text-muted-foreground">{t.task_id}</span>
            <span className="text-sm font-medium">{t.title || t.instruction}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted">
              {kindLabel(t.kind)}
            </span>
            {t.role ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted">@{t.role}</span>
            ) : null}
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted">{t.assignee}</span>
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${t.bucket === "overdue" ? "bg-red-500/15 text-red-500" : "bg-muted"}`}>
              {bucketLabel(t.bucket)} · {dueText(t.due_at, now)}
            </span>
            {t.reminders > 0 ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500">
                催办×{t.reminders}
              </span>
            ) : null}
            {t.over_budget ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/15 text-red-500">
                超预算
              </span>
            ) : null}
          </div>
          <div className="text-xs text-muted-foreground">
            {t.instruction}
            {t.form ? `（交付物：${t.form}）` : ""}
            {t.claimed_by ? ` · 持锁：${t.claimed_by}` : ""}
            {t.a2a_task_id ? ` · a2a:${t.a2a_task_id}` : ""}
          </div>
          {t.bucket === "delivered" || t.status === "delivered" ? (
            <div className="flex flex-col gap-1">
              <div className="text-xs flex items-center gap-2 flex-wrap">
                {t.verified ? (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-500">
                    已验证 Test·verified
                  </span>
                ) : (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500">
                    已交付·未验证 Code·reported done
                  </span>
                )}
                {t.verified ? (
                  <span className="text-xs text-emerald-500">✓ {t.verified_by}</span>
                ) : null}
              </div>
              <div className="text-xs">
                <CheckCircle2 className={`inline w-3 h-3 mr-1 ${t.verified ? "text-emerald-500" : "text-amber-500"}`} />
                交付（{t.delivered_by || "-"}）：{resultText(t.result)}
                {t.verify_evidence ? ` · 验收证据：${t.verify_evidence}` : ""}
              </div>
              {!t.verified && (props.onVerify || props.setVerifyId) ? (
                <div className="flex items-center gap-2 flex-wrap">
                  <button
                    className="text-xs px-2 py-1 rounded border border-border/60 hover:bg-muted"
                    onClick={() => props.setVerifyId?.(t.task_id)}
                    disabled={props.busyId === t.task_id}
                  >
                    <BadgeCheck className="inline w-3 h-3 mr-1" />
                    验收
                  </button>
                  {props.verifyId === t.task_id ? (
                    <div className="flex items-center gap-2 w-full">
                      <textarea
                        className="flex-1 text-xs rounded border border-border/60 bg-background p-2"
                        rows={2}
                        placeholder="内容级验收证据（跑了什么探针/查了什么内容，报成功没用）…"
                        value={props.verifyDraft ?? ""}
                        onChange={(e) => props.setVerifyDraft?.(e.target.value)}
                      />
                      <button
                        className="text-xs px-2 py-1 rounded bg-emerald-600 text-white"
                        onClick={() => props.onVerify?.(t, props.verifyDraft ?? "")}
                      >
                        记为已验证
                      </button>
                      <button
                        className="text-xs px-2 py-1 rounded border border-border/60"
                        onClick={() => props.setVerifyId?.("")}
                      >
                        <XCircle className="inline w-3 h-3" />
                      </button>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="flex items-center gap-2 flex-wrap">
              {props.onClaim ? (
                <button
                  className="text-xs px-2 py-1 rounded border border-border/60 hover:bg-muted"
                  onClick={() => props.onClaim?.(t)}
                  disabled={props.busyId === t.task_id}
                >
                  <Hand className="inline w-3 h-3 mr-1" />
                  认领
                </button>
              ) : null}
              {props.onHeartbeat ? (
                <button
                  className="text-xs px-2 py-1 rounded border border-border/60 hover:bg-muted"
                  onClick={() => props.onHeartbeat?.(t)}
                  disabled={props.busyId === t.task_id}
                >
                  <HeartPulse className="inline w-3 h-3 mr-1" />
                  心跳
                </button>
              ) : null}
              {props.onDeliver ? (
                <button
                  className="text-xs px-2 py-1 rounded border border-border/60 hover:bg-muted"
                  onClick={() => props.setDeliverId?.(t.task_id)}
                  disabled={props.busyId === t.task_id}
                >
                  <Send className="inline w-3 h-3 mr-1" />
                  交付
                </button>
              ) : null}
              {props.onRemind ? (
                <button
                  className="text-xs px-2 py-1 rounded border border-border/60 hover:bg-muted"
                  onClick={() => props.onRemind?.(t)}
                  disabled={props.busyId === t.task_id}
                >
                  <BellRing className="inline w-3 h-3 mr-1" />
                  催办
                </button>
              ) : null}
              {props.deliverId === t.task_id ? (
                <div className="flex items-center gap-2 w-full">
                  <textarea
                    className="flex-1 text-xs rounded border border-border/60 bg-background p-2"
                    rows={2}
                    placeholder="交付物（结论/文件/说明）…"
                    value={props.deliverDraft ?? ""}
                    onChange={(e) => props.setDeliverDraft?.(e.target.value)}
                  />
                  <button
                    className="text-xs px-2 py-1 rounded bg-primary text-primary-foreground"
                    onClick={() => props.onDeliver?.(t, props.deliverDraft ?? "")}
                  >
                    提交交付
                  </button>
                  <button
                    className="text-xs px-2 py-1 rounded border border-border/60"
                    onClick={() => props.setDeliverId?.("")}
                  >
                    <XCircle className="inline w-3 h-3" />
                  </button>
                </div>
              ) : null}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function TasksClient() {
  const [assignee, setAssignee] = useState("");
  const [bucket, setBucket] = useState("open");
  const [unverifiedOnly, setUnverifiedOnly] = useState(false);
  const [tasks, setTasks] = useState<TaskView[]>([]);
  const [policies, setPolicies] = useState<BudgetPolicyView[]>([]);
  const [busyId, setBusyId] = useState("");
  const [deliverId, setDeliverId] = useState("");
  const [deliverDraft, setDeliverDraft] = useState("");
  const [verifyId, setVerifyId] = useState("");
  const [verifyDraft, setVerifyDraft] = useState("");
  const [error, setError] = useState("");
  const now = Date.now() / 1000;

  const reload = useCallback(async () => {
    const base = acpBase();
    try {
      const params = new URLSearchParams({ bucket });
      if (assignee) params.set("assignee", assignee);
      if (unverifiedOnly) params.set("verification", "unverified");
      const [list, stats] = await Promise.all([
        fetch(`${base}/collab/tasks?${params}`).then((r) => r.json()),
        fetch(`${base}/collab/stats`).then((r) => r.json()),
      ]);
      setTasks(list.tasks ?? []);
      setPolicies(stats.budget_policies ?? []);
      setError("");
    } catch {
      setError("收发台不可达（acp-proxy /collab）");
    }
  }, [assignee, bucket, unverifiedOnly]);

  useEffect(() => {
    const saved = typeof window !== "undefined"
      ? localStorage.getItem("openmate-user") || ""
      : "";
    if (saved) setAssignee(saved);
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const post = async (path: string, body: unknown) => {
    setBusyId(path);
    try {
      const base = acpBase();
      await fetch(`${base}/collab/tasks/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setDeliverId("");
      setDeliverDraft("");
      setVerifyId("");
      setVerifyDraft("");
      await reload();
    } finally {
      setBusyId("");
    }
  };

  return (
    <PageLayout title="任务收发台">
      <div className="p-4 flex flex-col gap-4 h-full overflow-auto">
        {/* 分流记录：SoulMate 对话内自动分流（小→会话 / 中→群组 / 大→流程化）的任务在此回看 */}
        <TriageRecordsPanel />
        <div className="flex items-center gap-2 flex-wrap">
          <Inbox className="w-4 h-4" />
          <input
            className="text-xs rounded border border-border/60 bg-background px-2 py-1"
            placeholder="收件人（assignee，如 alice）"
            value={assignee}
            onChange={(e) => setAssignee(e.target.value)}
          />
          {BUCKETS.map((b) => (
            <button
              key={b.key}
              className={`text-xs px-2 py-1 rounded border ${bucket === b.key ? "bg-primary text-primary-foreground" : "border-border/60 hover:bg-muted"}`}
              onClick={() => setBucket(b.key)}
            >
              {b.label}
            </button>
          ))}
          <button
            className={`text-xs px-2 py-1 rounded border ${unverifiedOnly ? "bg-amber-500/20 border-amber-500/50 text-amber-500" : "border-border/60 hover:bg-muted"}`}
            onClick={() => setUnverifiedOnly((v) => !v)}
            title="done·unverified 监督日清清单（已交付未验收=黄）"
          >
            <BadgeCheck className="inline w-3 h-3 mr-1" />
            未验证
          </button>
          <button
            className="text-xs px-2 py-1 rounded border border-border/60 hover:bg-muted"
            onClick={reload}
          >
            <RefreshCw className="inline w-3 h-3 mr-1" />
            刷新
          </button>
          {error ? <span className="text-xs text-red-500">{error}</span> : null}
        </div>

        <TaskTable
          tasks={tasks}
          now={now}
          busyId={busyId}
          deliverId={deliverId}
          deliverDraft={deliverDraft}
          setDeliverId={setDeliverId}
          setDeliverDraft={setDeliverDraft}
          verifyId={verifyId}
          verifyDraft={verifyDraft}
          setVerifyId={setVerifyId}
          setVerifyDraft={setVerifyDraft}
          onClaim={(t) => post(`${t.task_id}/claim`, { claimed_by: assignee || "我" })}
          onHeartbeat={(t) => post(`${t.task_id}/heartbeat`, { claim_token: "" })}
          onDeliver={(t, result) =>
            post(`${t.task_id}/deliver`, {
              result,
              delivered_by: assignee || "我",
            })
          }
          onVerify={(t, evidence) =>
            post(`${t.task_id}/verify`, {
              verified_by: assignee || "我",
              evidence,
              ok: true,
            })
          }
          onRemind={(t) => post(`${t.task_id}/remind`, { note: "收发台催办" })}
        />

        <div className="mt-2">
          <div className="text-xs text-muted-foreground mb-1">预算策略（三档隔离）</div>
          <div className="flex flex-col gap-1">
            {policies.length === 0 ? (
              <div className="text-xs text-muted-foreground">（暂无策略）</div>
            ) : (
              policies.map((p) => (
                <div key={p.id} className="text-xs flex items-center gap-2">
                  <span
                    className={`w-2 h-2 rounded-full ${p.state === "hard_stop" ? "bg-red-500" : p.state === "warning" ? "bg-amber-500" : "bg-emerald-500"}`}
                  />
                  <span className="font-mono">{p.id}</span>
                  <span className="text-muted-foreground">
                    ${p.max_cost_usd} / {p.max_tokens} tokens · {p.window}
                  </span>
                  <span>{p.state}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </PageLayout>
  );
}
