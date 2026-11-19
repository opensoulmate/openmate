"use client";

/* 任务三级分流·共享件（2026-11-19 归位：显式入口 → 对话内隐式分流）
 * 用户裁定：不再设"下达任务"独立入口——用户只在对话里跟 SoulMate 说事，
 * SoulMate 沟通中自己判级（小=直接干 / 中=自动转 AI 群组拆解 / 大=自动转流程化），
 * 升级时透明告知（"这事适合协作，我转到群组了"）。判级/分流引擎在
 * acp-proxy :8092 /triage/*（plugin/task_triage*.py）。
 *
 * 本文件保留分流能力的共享面（归位不砍杀，迁移优先于删除）：
 *   - triageApi：/triage/* HTTP 面（判级/分流/换轨/看板/双语义模式）
 *   - ModeBadge：个人/企业双语义（一人公司 / 企业内部流程，/will 同源展示）
 *   - TriageBoard + TriageRecordsPanel：三轨产物统一回看（对话内自动分流的任务可查、可换轨）
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { LayoutDashboard, ArrowRight, Repeat } from "lucide-react";

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
  name?: string;
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
  detail: { definition?: FlowDraftDef; yaml?: string; name?: string; error?: string; confirm_error?: string };
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
        {t("triage.boardEmpty", "（暂无分流记录——在对话里跟 SoulMate 说事，需要协作时会自动转进来）")}
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

/* ── 分流记录面板（三轨产物统一回看）：SoulMate 对话内自动分流的任务在这里可查，
 * 带一键换轨（会话↔群组↔流程，上下文带过去）。无下达输入框——分流是对话内隐式的。── */
export function TriageRecordsPanel() {
  const { t } = useTranslation();
  const [records, setRecords] = useState<TriageRecord[]>([]);
  const [mode, setMode] = useState("personal");
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    triageFetch("/triage/records").then((d) => setRecords(d.records || [])).catch((e) => setError(String(e)));
    triageFetch("/triage/mode").then((d) => setMode(d.mode || "personal")).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const toggleMode = useCallback(async () => {
    const next = mode === "enterprise" ? "personal" : "enterprise";
    try {
      await triageFetch("/triage/mode", { method: "PUT", body: JSON.stringify({ mode: next }) });
      setMode(next);
    } catch (e) {
      setError(String(e));
    }
  }, [mode]);

  const switchTrack = useCallback(async (rec: TriageRecord, to: TriageTrack) => {
    setBusyId(rec.id);
    setError("");
    try {
      await triageFetch(`/triage/records/${rec.id}/switch`, {
        method: "POST", body: JSON.stringify({ to_track: to }),
      });
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId("");
    }
  }, [load]);

  return (
    <div className="rounded-xl border border-border/60 p-4 flex flex-col gap-3" data-testid="triage-records-panel">
      <div className="flex items-center gap-2 flex-wrap">
        <LayoutDashboard className="w-4 h-4 text-primary" />
        <span className="text-sm font-medium">{t("triage.boardTitle", "分流记录")}</span>
        <ModeBadge mode={mode} onToggle={toggleMode} />
        <div className="flex-1" />
        <button
          className="text-xs px-2 py-1 rounded-lg border border-border/60 hover:bg-muted"
          onClick={load}
        >
          {t("triage.refresh", "刷新")}
        </button>
      </div>
      <TriageBoard records={records} busyId={busyId} onSwitch={switchTrack} />
      {error ? <div className="text-xs text-red-500">{error}</div> : null}
    </div>
  );
}

export const TRIAGE_TRACKS = TRACKS;
export const TRIAGE_TRACK_ICON = TRACK_ICON;
export const TRIAGE_LEVEL_KEY = LEVEL_KEY;
export const TRIAGE_TRACK_KEY = TRACK_KEY;
export const TRIAGE_LEVEL_CLASS = LEVEL_CLASS;
export const triageApi = { triageFetch, triageBase };
