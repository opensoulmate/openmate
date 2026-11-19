"use client";

/* 任务三级分流门（task triage gate）— 统一下达入口
 * 用户说一句话 → AI 自动判级（小/中/大）→ 三轨分流：
 *   小 → 开/续 SoulMate 会话直接办（/chat 深链自动送达）
 *   中 → AI 群组发起（自动建组 + 任务发布 + 拆解执行）
 *   大 → 流程化（flow_studio 草稿 → 预览 → 装载跑）
 * 判级建议 + 一键换轨（会话↔群组↔流程，上下文带过去）+ 三轨结果统一回看板
 * 个人/企业双语义：流程化 = 一人公司 / 企业内部流程（模式随设置切换文案与模板偏好）
 * 后端：acp-proxy :8092 /triage/*（plugin/task_triage*.py，纯 Python）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Sparkles, MessageSquare, LayoutDashboard, Send, Repeat,
  CheckCircle2, Loader2, FileText, ArrowRight, X,
} from "lucide-react";
import { SuperAvatar } from "@/components/ui/super-ellipse";

function triageBase(): string {
  if (typeof window === "undefined") return "http://127.0.0.1:8092";
  return `http://${window.location.hostname}:8092`;
}

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("openmate-token");
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
  return headers;
}

async function triageFetch(path: string, options: RequestInit = {}) {
  const res = await fetch(`${triageBase()}${path}`, {
    ...options,
    headers: { ...authHeaders(), ...(options.headers as Record<string, string> || {}) },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const d = await res.json();
      if (d?.detail) detail = String(d.detail);
    } catch { /* keep status text */ }
    throw new Error(detail);
  }
  return res.json();
}

export type TriageTrack = "chat" | "group" | "flow";
export type TriageLevel = "small" | "medium" | "large";

export interface TriageArtifact {
  type: string;
  link?: string;
  session_id?: string;
  group_id?: string;
  task_id?: string;
  flow_id?: string;
  run_id?: string;
  subtasks_count?: number;
  draft_name?: string;
}

export interface TriageRecord {
  id: string;
  prompt: string;
  level: TriageLevel | "";
  confidence: number;
  reasons: string[];
  level_source: string;
  track: TriageTrack | "";
  status: string;
  context: Record<string, unknown>;
  artifact: TriageArtifact;
  detail: { definition?: FlowDraftDef; yaml?: string; name?: string; error?: string };
  switched_from: string;
  switched_to: string;
  tenant_mode: string;
  created_at: number;
  updated_at: number;
  live?: { state: string; label: string };
  link?: string;
}

interface FlowDraftDef {
  id: string;
  name: string;
  nodes: Array<{ id: string; type: string; task?: string; tool?: string }>;
}

interface JudgeResult {
  level: TriageLevel;
  confidence: number;
  reasons: string[];
  source: string;
  track: TriageTrack;
}

const TRACKS: TriageTrack[] = ["chat", "group", "flow"];
const TRACK_ICON: Record<TriageTrack, string> = { chat: "💬", group: "👥", flow: "⚙️" };

const LEVEL_KEY: Record<TriageLevel, string> = {
  small: "triage.levelSmall",
  medium: "triage.levelMedium",
  large: "triage.levelLarge",
};
const LEVEL_CLASS: Record<TriageLevel, string> = {
  small: "bg-emerald-500/15 text-emerald-500",
  medium: "bg-amber-500/15 text-amber-500",
  large: "bg-rose-500/15 text-rose-500",
};
const TRACK_KEY: Record<TriageTrack, string> = {
  chat: "triage.trackChat",
  group: "triage.trackGroup",
  flow: "triage.trackFlow",
};
const TRACK_DESC_KEY: Record<TriageTrack, string> = {
  chat: "triage.trackChatDesc",
  group: "triage.trackGroupDesc",
  flow: "triage.trackFlowDesc",
};

// 实时状态 → 展示（看板「状态」列）
export function liveStateClass(state: string): string {
  if (state === "done") return "bg-emerald-500/15 text-emerald-500";
  if (state === "failed") return "bg-red-500/15 text-red-500";
  if (state === "running") return "bg-sky-500/15 text-sky-500";
  return "bg-muted text-muted-foreground";
}

type TFunc = ReturnType<typeof useTranslation>["t"];

export function liveStateLabel(t: TFunc, state: string): string {
  const key: Record<string, string> = {
    waiting: "triage.statusWaiting",
    running: "triage.statusRunning",
    done: "triage.statusDone",
    failed: "triage.statusFailed",
    draft: "triage.statusDraft",
  };
  return key[state] ? t(key[state], state) : t("triage.statusUnknown", state);
}

/* ── 模式徽章（个人/企业双语义：流程化 = 一人公司 / 内部流程）── */
export function ModeBadge({ mode, onToggle }: { mode: string; onToggle?: () => void }) {
  const { t } = useTranslation();
  const personal = mode !== "enterprise";
  return (
    <button
      onClick={onToggle}
      title={t("triage.modeSwitch", "切换个人/企业模式")}
      className={`flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs transition-colors ${
        personal ? "bg-primary/12 text-primary" : "bg-violet-500/15 text-violet-500"
      }`}
      data-testid="triage-mode-badge"
    >
      <span>{personal ? t("triage.modePersonal", "一人公司") : t("triage.modeEnterprise", "企业内部流程")}</span>
      <span className="text-muted-foreground">
        {personal ? t("triage.modePersonalHint", "流程化=一个人的公司") : t("triage.modeEnterpriseHint", "流程化=企业内部流程")}
      </span>
    </button>
  );
}

/* ── 统一回看板（三轨产物 + 实时状态 + 深链 + 一键换轨）── */
export function TriageBoard({
  records, busyId, onSwitch, onRefresh,
}: {
  records: TriageRecord[];
  busyId?: string;
  onSwitch?: (rec: TriageRecord, to: TriageTrack) => void;
  onRefresh?: () => void;
}) {
  const { t } = useTranslation();
  if (!records.length) {
    return (
      <div className="text-sm text-muted-foreground py-6 text-center">
        {t("triage.boardEmpty", "（暂无下达记录——用上方输入框一句话下达）")}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2" data-testid="triage-board">
      {records.map((rec) => {
        const live = rec.live || { state: "unknown", label: "" };
        return (
          <div key={rec.id} className="rounded-lg border border-border/60 p-3 flex flex-col gap-2" data-testid="triage-board-row">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-mono text-xs text-muted-foreground">{rec.id}</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted">
                {rec.level ? t(LEVEL_KEY[rec.level as TriageLevel], rec.level) : "—"}
              </span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/12 text-primary">
                {rec.track ? t(TRACK_KEY[rec.track as TriageTrack], rec.track) : "—"}
              </span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${liveStateClass(live.state)}`}>
                {liveStateLabel(t, live.state)}{live.label ? ` · ${live.label}` : ""}
              </span>
              {rec.switched_to ? (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500">
                  {t("triage.switched", "已换轨")}
                </span>
              ) : null}
            </div>
            <div className="text-xs text-muted-foreground line-clamp-2">{rec.prompt}</div>
            <div className="flex items-center gap-2 flex-wrap">
              {rec.link ? (
                <a
                  href={rec.link}
                  className="flex items-center gap-1 text-xs px-2 py-1 rounded-lg bg-primary text-primary-foreground"
                >
                  <ArrowRight className="w-3 h-3" />
                  {t("triage.openArtifact", "打开产物")}
                </a>
              ) : null}
              <span className="text-[10px] text-muted-foreground font-mono">
                {rec.artifact?.session_id || rec.artifact?.group_id || rec.artifact?.flow_id || ""}
                {rec.artifact?.run_id ? ` · run:${rec.artifact.run_id}` : ""}
              </span>
              {onSwitch && rec.status !== "switched"
                ? TRACKS.filter((x) => x !== rec.track).map((to) => (
                    <button
                      key={to}
                      className="flex items-center gap-1 text-xs px-2 py-1 rounded-lg border border-border/60 hover:bg-muted disabled:opacity-50"
                      disabled={busyId === rec.id}
                      onClick={() => onSwitch(rec, to)}
                      title={t("triage.switchHint", "换轨并把上下文带过去")}
                    >
                      <Repeat className="w-3 h-3" />
                      {t("triage.switchTo", "换轨至")} {t(TRACK_KEY[to], to)}
                    </button>
                  ))
                : null}
              {onRefresh ? (
                <button
                  className="text-xs px-2 py-1 rounded-lg border border-border/60 hover:bg-muted"
                  onClick={onRefresh}
                >
                  {t("triage.refresh", "刷新")}
                </button>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── 分流门主体（模态内嵌 / /tasks 顶部内嵌共用）── */
export function TaskTriageGate({ embedded = false, onOpenLink }: {
  embedded?: boolean;
  onOpenLink?: (link: string) => void;
}) {
  const { t } = useTranslation();
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState("personal");
  const [phase, setPhase] = useState<"input" | "judging" | "judged" | "dispatching" | "result">("input");
  const [judge, setJudge] = useState<JudgeResult | null>(null);
  const [pickedTrack, setPickedTrack] = useState<TriageTrack>("chat");
  const [record, setRecord] = useState<TriageRecord | null>(null);
  const [records, setRecords] = useState<TriageRecord[]>([]);
  const [tab, setTab] = useState<"dispatch" | "board">("dispatch");
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [flowRunning, setFlowRunning] = useState(false);
  const [showYaml, setShowYaml] = useState(false);
  const promptRef = useRef("");

  const loadMode = useCallback(() => {
    triageFetch("/triage/mode").then((d) => setMode(d.mode || "personal")).catch(() => {});
  }, []);
  const loadBoard = useCallback(() => {
    triageFetch("/triage/records").then((d) => setRecords(d.records || [])).catch(() => {});
  }, []);
  useEffect(() => { loadMode(); loadBoard(); }, [loadMode, loadBoard]);

  const openLink = useCallback((link: string) => {
    if (onOpenLink) onOpenLink(link);
    else if (typeof window !== "undefined") window.location.href = link;
  }, [onOpenLink]);

  const toggleMode = useCallback(async () => {
    const next = mode === "personal" ? "enterprise" : "personal";
    try {
      await triageFetch("/triage/mode", { method: "PUT", body: JSON.stringify({ mode: next }) });
      setMode(next);
    } catch (e) {
      setError(String(e));
    }
  }, [mode]);

  const analyze = useCallback(async () => {
    if (!prompt.trim() || phase === "judging") return;
    promptRef.current = prompt.trim();
    setPhase("judging");
    setError("");
    try {
      const d = await triageFetch("/triage/analyze", {
        method: "POST", body: JSON.stringify({ prompt: prompt.trim() }),
      });
      const j: JudgeResult = { level: d.level, confidence: d.confidence, reasons: d.reasons || [], source: d.source, track: d.track };
      setJudge(j);
      setPickedTrack(j.track);
      setPhase("judged");
    } catch (e) {
      setError(String(e));
      setPhase("input");
    }
  }, [prompt, phase]);

  const dispatchTo = useCallback(async (track: TriageTrack) => {
    setPhase("dispatching");
    setError("");
    try {
      const d = await triageFetch("/triage/dispatch", {
        method: "POST",
        body: JSON.stringify({
          prompt: promptRef.current,
          track,
          level: judge?.level || "",
          reasons: judge?.reasons || [],
          level_source: judge?.source || "",
        }),
      });
      setRecord(d.record);
      setPhase("result");
      loadBoard();
    } catch (e) {
      setError(String(e));
      setPhase("judged");
    }
  }, [judge, loadBoard]);

  const confirmFlow = useCallback(async () => {
    if (!record) return;
    setFlowRunning(true);
    setError("");
    try {
      const d = await triageFetch(`/triage/records/${record.id}/confirm`, { method: "POST" });
      setRecord(d.record);
      loadBoard();
    } catch (e) {
      setError(String(e));
    } finally {
      setFlowRunning(false);
    }
  }, [record, loadBoard]);

  const switchTrack = useCallback(async (rec: TriageRecord, to: TriageTrack) => {
    setBusyId(rec.id);
    setError("");
    try {
      const d = await triageFetch(`/triage/records/${rec.id}/switch`, {
        method: "POST", body: JSON.stringify({ to_track: to }),
      });
      setRecord(d.record);
      setPhase("result");
      setTab("dispatch");
      loadBoard();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId("");
    }
  }, [loadBoard]);

  const startOver = useCallback(() => {
    setPrompt(""); setJudge(null); setRecord(null); setPhase("input"); setError("");
  }, []);

  const draft = record?.detail?.definition;
  const recordLink = record ? (record.link || record.artifact?.link || "") : "";
  return (
    <div className={`flex flex-col gap-3 ${embedded ? "" : ""}`} data-testid="triage-gate">
      {/* 顶行：标题 + 模式徽章 + 视图切换 */}
      <div className="flex items-center gap-2 flex-wrap">
        <Sparkles className="w-4 h-4 text-primary" />
        <span className="text-sm font-medium">{t("triage.title", "任务下达 · 三级分流门")}</span>
        <ModeBadge mode={mode} onToggle={toggleMode} />
        <div className="flex-1" />
        <div className="flex items-center gap-1">
          {(["dispatch", "board"] as const).map((k) => (
            <button
              key={k}
              className={`text-xs px-2.5 py-1 rounded-lg ${tab === k ? "bg-primary text-primary-foreground" : "border border-border/60 hover:bg-muted"}`}
              onClick={() => { setTab(k); if (k === "board") loadBoard(); }}
            >
              {k === "dispatch"
                ? <><Send className="inline w-3 h-3 mr-1" />{t("triage.tabDispatch", "下达")}</>
                : <><LayoutDashboard className="inline w-3 h-3 mr-1" />{t("triage.boardTitle", "统一看板")}</>}
            </button>
          ))}
        </div>
      </div>

      {tab === "dispatch" ? (
        <>
          {/* 一句话输入 */}
          <div className="flex flex-col gap-2">
            <textarea
              className="w-full text-sm rounded-lg border border-border/60 bg-background p-3 focus:outline-none focus:border-primary/60 resize-y min-h-[64px]"
              placeholder={t("triage.placeholder", "一句话下达任务：小任务进会话、中任务进 AI 群组、大任务进流程化…")}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={phase !== "input"}
              data-testid="triage-input"
            />
            <div className="flex items-center gap-2 flex-wrap">
              <button
                className="flex items-center gap-1.5 text-sm px-3.5 py-1.5 rounded-lg bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
                onClick={phase === "input" ? analyze : startOver}
                disabled={!prompt.trim() && phase === "input"}
                data-testid="triage-analyze"
              >
                {phase === "judging"
                  ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />{t("triage.analyzing", "AI 判级中…")}</>
                  : phase === "input"
                    ? <><Sparkles className="w-3.5 h-3.5" />{t("triage.analyze", "AI 判级")}</>
                    : <>{t("triage.newTask", "下达新任务")}</>}
              </button>
              {phase === "judged" ? (
                <span className="text-xs text-muted-foreground">
                  {t("triage.judgeHint", "判级只是建议——可一键改判到任意轨道")}
                </span>
              ) : null}
            </div>
          </div>

          {/* 判级结果 + 三轨选择 */}
          {judge && (phase === "judged" || phase === "dispatching") ? (
            <div className="rounded-lg border border-border/60 p-3 flex flex-col gap-2" data-testid="triage-judge">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs text-muted-foreground">{t("triage.level", "判级")}</span>
                <span className={`text-xs px-2 py-0.5 rounded ${LEVEL_CLASS[judge.level]}`} data-testid="triage-level">
                  {t(LEVEL_KEY[judge.level], judge.level)}
                </span>
                <span className="text-[10px] text-muted-foreground">
                  {judge.source === "llm" ? t("triage.sourceLlm", "AI 判级") : t("triage.sourceHeuristic", "规则判级")}
                  {judge.confidence ? ` · ${(judge.confidence * 100).toFixed(0)}%` : ""}
                </span>
              </div>
              {judge.reasons.length ? (
                <ul className="text-xs text-muted-foreground list-disc pl-4">
                  {judge.reasons.map((r, i) => <li key={i}>{r}</li>)}
                </ul>
              ) : null}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                {TRACKS.map((tr) => {
                  const suggested = tr === judge.track;
                  const picked = tr === pickedTrack;
                  return (
                    <button
                      key={tr}
                      onClick={() => setPickedTrack(tr)}
                      className={`flex flex-col items-start gap-1 rounded-lg border p-2.5 text-left transition-colors ${
                        picked ? "border-primary/70 bg-primary/12" : "border-border/60 hover:bg-muted"
                      }`}
                      data-testid={`triage-track-${tr}`}
                    >
                      <div className="flex items-center gap-2">
                        <SuperAvatar src={TRACK_ICON[tr]} size={22} bg="bg-muted" />
                        <span className="text-sm">{t(TRACK_KEY[tr], tr)}</span>
                        {suggested ? (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary text-primary-foreground">
                            {t("triage.suggested", "建议")}
                          </span>
                        ) : null}
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {t(TRACK_DESC_KEY[tr], "")}
                      </span>
                    </button>
                  );
                })}
              </div>
              <div className="flex items-center gap-2">
                <button
                  className="flex items-center gap-1.5 text-sm px-3.5 py-1.5 rounded-lg bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
                  onClick={() => dispatchTo(pickedTrack)}
                  disabled={phase === "dispatching"}
                  data-testid="triage-dispatch"
                >
                  {phase === "dispatching"
                    ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />{t("triage.dispatching", "下达中…")}</>
                    : <><ArrowRight className="w-3.5 h-3.5" />{t("triage.dispatchTo", "按所选轨道下达")}</>}
                </button>
                {pickedTrack !== judge.track ? (
                  <span className="text-xs text-amber-500">
                    {t("triage.overrode", "已手动改判（原建议：{{track}}）", {
                      track: t(TRACK_KEY[judge.track], judge.track),
                    })}
                  </span>
                ) : null}
              </div>
            </div>
          ) : null}

          {/* 分流结果（三轨各自产物面） */}
          {record && phase === "result" ? (
            <div className="rounded-lg border border-border/60 p-3 flex flex-col gap-2" data-testid="triage-result">
              <div className="flex items-center gap-2 flex-wrap">
                <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                <span className="text-sm">
                  {record.track === "chat"
                    ? t("triage.dispatchedChat", "已开 SoulMate 会话，任务直达办理")
                    : record.track === "group"
                      ? t("triage.dispatchedGroup", "已建 AI 群组并发布任务（自动拆解执行中）")
                      : t("triage.dispatchedFlow", "已生成流程草稿——预览后装载跑")}
                </span>
                <span className="font-mono text-[10px] text-muted-foreground">{record.id}</span>
              </div>

              {/* 流程草稿预览（大任务轨：所见即所得） */}
              {record.track === "flow" && draft ? (
                <div className="flex flex-col gap-1.5 rounded-lg bg-muted/30 p-2.5">
                  <div className="flex items-center gap-2 text-xs">
                    <FileText className="w-3.5 h-3.5 text-primary" />
                    <span className="font-medium">{t("triage.flowDraft", "流程草稿")}</span>
                    <span>{draft.name}</span>
                    <span className="text-muted-foreground">
                      {t("triage.flowNodes", "{{n}} 个节点", { n: draft.nodes?.length || 0 })}
                    </span>
                    <button
                      className="text-[10px] px-1.5 py-0.5 rounded border border-border/60 hover:bg-muted"
                      onClick={() => setShowYaml((v) => !v)}
                    >
                      YAML
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {(draft.nodes || []).map((n) => (
                      <span key={n.id} className="text-[10px] px-1.5 py-0.5 rounded bg-background border border-border/60">
                        {n.id} · {n.type}{n.task ? ` · ${n.task.slice(0, 18)}` : ""}
                      </span>
                    ))}
                  </div>
                  {showYaml && record.detail?.yaml ? (
                    <pre className="text-[10px] font-mono whitespace-pre-wrap max-h-40 overflow-auto rounded bg-background border border-border/60 p-2">
                      {record.detail.yaml}
                    </pre>
                  ) : null}
                  <div className="flex items-center gap-2">
                    {record.status === "draft_ready" ? (
                      <button
                        className="flex items-center gap-1.5 text-sm px-3.5 py-1.5 rounded-lg bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
                        onClick={confirmFlow}
                        disabled={flowRunning}
                        data-testid="triage-flow-load-run"
                      >
                        {flowRunning
                          ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />{t("triage.flowLoading", "装载跑中…")}</>
                          : <><ArrowRight className="w-3.5 h-3.5" />{t("triage.flowLoadRun", "装载跑")}</>}
                      </button>
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        {t("triage.flowRunningHint", "已装载运行")}
                        {record.artifact?.run_id ? ` · run:${record.artifact.run_id}` : ""}
                      </span>
                    )}
                  </div>
                </div>
              ) : null}

              <div className="flex items-center gap-2 flex-wrap">
                {recordLink ? (
                  <button
                    className="flex items-center gap-1.5 text-sm px-3.5 py-1.5 rounded-lg bg-primary text-primary-foreground hover:opacity-90"
                    onClick={() => openLink(recordLink)}
                    data-testid="triage-go-artifact"
                  >
                    {record.track === "chat"
                      ? t("triage.goChat", "去会话办理")
                      : record.track === "group"
                        ? t("triage.goGroup", "去群组看执行")
                        : t("triage.goFlow", "去流程面板")}
                  </button>
                ) : null}
                <button
                  className="text-xs px-2.5 py-1.5 rounded-lg border border-border/60 hover:bg-muted"
                  onClick={() => { setTab("board"); loadBoard(); }}
                >
                  <LayoutDashboard className="inline w-3 h-3 mr-1" />
                  {t("triage.viewBoard", "回看板")}
                </button>
                <button
                  className="text-xs px-2.5 py-1.5 rounded-lg border border-border/60 hover:bg-muted"
                  onClick={startOver}
                >
                  {t("triage.newTask", "下达新任务")}
                </button>
              </div>
            </div>
          ) : null}

          {error ? <div className="text-xs text-red-500" data-testid="triage-error">{error}</div> : null}
        </>
      ) : (
        <>
          <TriageBoard records={records} busyId={busyId} onSwitch={switchTrack} onRefresh={loadBoard} />
          {error ? <div className="text-xs text-red-500">{error}</div> : null}
        </>
      )}
    </div>
  );
}

/* ── 全局快速下达入口（任一页悬浮球 → 模态；CommandMenu 也发同一事件）── */
export function TaskTriageLauncher() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const handler = () => setOpen(true);
    window.addEventListener("openmate:triage-open", handler);
    return () => window.removeEventListener("openmate:triage-open", handler);
  }, []);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title={t("triage.title", "任务下达 · 三级分流门")}
        data-testid="triage-fab"
        className="fixed bottom-6 right-6 z-[90] flex items-center gap-2 rounded-lg bg-primary text-primary-foreground px-3.5 py-2.5 shadow-lg hover:opacity-90"
      >
        <Sparkles className="w-4 h-4" />
        <span className="text-sm">{t("triage.fab", "下达任务")}</span>
      </button>
      {open ? (
        <div className="fixed inset-0 z-[100] flex items-start justify-center pt-[10vh]">
          <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setOpen(false)} />
          <div className="relative z-50 w-full max-w-2xl overflow-hidden rounded-xl border border-border bg-card shadow-2xl">
            <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
              <div className="flex items-center gap-2">
                <MessageSquare className="w-4 h-4 text-primary" />
                <span className="text-sm font-medium">{t("triage.title", "任务下达 · 三级分流门")}</span>
              </div>
              <button className="p-1 rounded-lg hover:bg-muted" onClick={() => setOpen(false)}>
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-4 max-h-[75vh] overflow-auto">
              <TaskTriageGate onOpenLink={(link) => { setOpen(false); window.location.href = link; }} />
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

/* ── /tasks 顶部内嵌面板（统一下达入口之一）── */
export function TaskTriagePanel() {
  return (
    <div className="rounded-xl border border-border/60 p-4">
      <TaskTriageGate embedded />
    </div>
  );
}

export const TRIAGE_TRACKS = TRACKS;
export const TRIAGE_TRACK_ICON = TRACK_ICON;
export const TRIAGE_LEVEL_KEY = LEVEL_KEY;
export const TRIAGE_TRACK_KEY = TRACK_KEY;
export const TRIAGE_TRACK_DESC_KEY = TRACK_DESC_KEY;
export const TRIAGE_LEVEL_CLASS = LEVEL_CLASS;
export const triageApi = { triageFetch, triageBase };
