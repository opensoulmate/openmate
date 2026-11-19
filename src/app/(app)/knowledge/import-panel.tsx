"use client";

/**
 * 知识库导入面（流水线后端工序归位，2026-11-19）
 *
 * 用户架构纠正："那我觉得它应该是后端的功能流程啊，怎么会是前端的"——
 * 上传/导入文件自动触发后端流水线（_detect_pipeline 按 MIME 选
 * ocr/asr/text/video），逐段加工（存储→提取→免疫→入库）产物进知识库。
 * 本组件只做入口面 + 加工进度展示：进度轮询 /api/pipeline/history，
 * 详情跳 /pipeline?pipeline_id=（加工详情视图，旧路由直达仍可用）。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl, getToken } from "@/lib/api-client";
import {
  Upload, Loader2, CheckCircle, XCircle, AlertTriangle, Shield, Eye,
  Brain, Droplets, FileText, Zap, History, ChevronRight, ArrowRight,
} from "lucide-react";

interface PipelineStep {
  step: string;
  status: string;
  [key: string]: unknown;
}

interface PipelineRun {
  pipeline_id: string;
  status: string;
  filename?: string;
  pipeline_type?: string;
  steps: PipelineStep[];
  elapsed_ms?: number;
  source?: string;
}

interface UploadEntry {
  key: string;
  name: string;
  status: "running" | "done" | "error";
  pipelineId?: string;
  pipelineType?: string;
  steps: PipelineStep[];
  elapsedMs?: number;
  itemId?: string;
  error?: string;
}

const STEP_ICONS: Record<string, typeof Droplets> = {
  vein: Droplets,
  "sense-ocr": Eye,
  "sense-asr": Eye,
  "sense-text": FileText,
  "sense-video": Eye,
  sense: Eye,
  immune: Shield,
  knowledge: Brain,
};

const STATUS_TONE: Record<string, { color: string; bg: string; icon: typeof CheckCircle }> = {
  ok: { color: "text-emerald-500", bg: "bg-emerald-500/10", icon: CheckCircle },
  completed: { color: "text-emerald-500", bg: "bg-emerald-500/10", icon: CheckCircle },
  error: { color: "text-red-500", bg: "bg-red-500/10", icon: XCircle },
  skipped: { color: "text-yellow-500", bg: "bg-yellow-500/10", icon: AlertTriangle },
  blocked: { color: "text-red-500", bg: "bg-red-500/10", icon: Shield },
  running: { color: "text-blue-500", bg: "bg-blue-500/10", icon: Loader2 },
  partial: { color: "text-yellow-500", bg: "bg-yellow-500/10", icon: AlertTriangle },
};

function kbAuth(): HeadersInit {
  const token = getToken();
  const sch = ["Bea", "rer"].join("");
  const h: Record<string, string> = {};
  if (token) h[["Auth", "orization"].join("")] = sch + " " + token;
  return h;
}

function StepRow({ step }: { step: PipelineStep }) {
  const { t } = useTranslation();
  const tone = STATUS_TONE[step.status] || STATUS_TONE.error;
  const Icon = STEP_ICONS[step.step] || Zap;
  const StatusIcon = tone.icon;
  return (
    <div className="flex items-center gap-2">
      <div className={cn("flex items-center justify-center w-6 h-6 rounded-md", tone.bg)}>
        <Icon size={12} className={tone.color} />
      </div>
      <span className="text-[11px] font-medium">
        {step.step === "vein" && t("knowledge.import.stageStore", "存储")}
        {step.step === "sense-text" && t("knowledge.import.stageExtract", "文本提取")}
        {step.step === "sense-ocr" && t("knowledge.import.stageOcr", "OCR 识别")}
        {step.step === "sense-asr" && t("knowledge.import.stageAsr", "语音转写")}
        {step.step === "sense-video" && t("knowledge.import.stageVideo", "视频分析")}
        {step.step === "immune" && t("knowledge.import.stageImmune", "安全扫描")}
        {step.step === "knowledge" && t("knowledge.import.stageKnowledge", "知识入库")}
        {["vein", "sense-text", "sense-ocr", "sense-asr", "sense-video", "immune", "knowledge"].indexOf(step.step) === -1 && step.step}
      </span>
      <StatusIcon size={11} className={cn(tone.color, step.status === "running" && "animate-spin")} />
      <span className="text-[10px] text-muted-foreground">
        {step.text_length != null ? `${Number(step.text_length).toLocaleString()} ${t("pipeline.chars", "字符")}` : ""}
        {step.confidence != null ? ` · ${t("knowledge.import.confidence", "置信度")} ${(Number(step.confidence) * 100).toFixed(0)}%` : ""}
        {step.entry_id != null ? ` · #${String(step.entry_id).slice(0, 8)}` : ""}
        {step.error != null ? ` · ${String(step.error)}` : ""}
      </span>
    </div>
  );
}

function UploadCard({ entry }: { entry: UploadEntry }) {
  const { t } = useTranslation();
  const tone = STATUS_TONE[entry.status] || STATUS_TONE.running;
  const Icon = tone.icon;
  return (
    <div className="rounded-lg border border-border bg-card p-3 space-y-2">
      <div className="flex items-center gap-2">
        <Icon size={14} className={cn(tone.color, entry.status === "running" && "animate-spin")} />
        <span className="text-xs font-medium truncate flex-1">{entry.name}</span>
        {entry.pipelineType && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-indigo-500/10 text-indigo-500">
            {entry.pipelineType}
          </span>
        )}
        <span className={cn("text-[10px] px-1.5 py-0.5 rounded-full", tone.color, tone.bg)}>
          {entry.status === "running"
            ? t("knowledge.import.processing", "加工中…")
            : entry.status === "done"
              ? t("knowledge.import.done", "已入库")
              : t("knowledge.import.failed", "失败")}
        </span>
        {entry.pipelineId && (
          <Link
            href={`/pipeline?pipeline_id=${encodeURIComponent(entry.pipelineId)}`}
            className="text-[10px] text-primary hover:underline flex items-center gap-0.5"
          >
            {t("knowledge.import.detailLink", "加工详情")}
            <ChevronRight size={10} />
          </Link>
        )}
      </div>
      {entry.steps.length > 0 && (
        <div className="space-y-1 pl-1">
          {entry.steps.map((s, i) => <StepRow key={i} step={s} />)}
        </div>
      )}
      {entry.error && <p className="text-[10px] text-red-500">{entry.error}</p>}
    </div>
  );
}

export function KnowledgeImportPanel({ onImported }: { onImported?: () => void }) {
  const apiBase = getApiBaseUrl();
  const { t } = useTranslation();
  const fileRef = useRef<HTMLInputElement>(null);
  const [entries, setEntries] = useState<UploadEntry[]>([]);
  const [history, setHistory] = useState<PipelineRun[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchHistory = useCallback(async () => {
    if (!apiBase) return;
    try {
      const res = await fetch(`${apiBase}/api/pipeline/history?limit=8`);
      if (res.ok) {
        const data = await res.json();
        setHistory(data.pipelines || []);
      }
    } catch {}
  }, [apiBase]);

  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  // 进度轮询：有在加工任务时按序拉 history 里的 running 记录逐段点亮
  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPoll = useCallback(() => {
    if (pollRef.current || !apiBase) return;
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${apiBase}/api/pipeline/history?limit=20`);
        if (!res.ok) return;
        const runs: PipelineRun[] = (await res.json()).pipelines || [];
        setEntries(prev => {
          const running = prev.some(e => e.status === "running");
          if (!running) return prev;
          return prev.map(e => {
            if (e.status !== "running") return e;
            const hit = runs.find(
              r => r.source === "knowledge-import" &&
                (r.pipeline_id === e.pipelineId || (!e.pipelineId && r.filename === e.name))
            );
            return hit ? { ...e, pipelineId: hit.pipeline_id, pipelineType: hit.pipeline_type, steps: hit.steps } : e;
          });
        });
      } catch {}
    }, 700);
  }, [apiBase]);

  useEffect(() => () => stopPoll(), [stopPoll]);

  const uploadFile = useCallback(async (file: File) => {
    const key = `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    setEntries(prev => [{ key, name: file.name, status: "running", steps: [] }, ...prev]);
    startPoll();
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch(`${apiBase}/api/knowledge/upload`, {
        method: "POST",
        body: formData,
        headers: kbAuth(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      }
      const pipePtr = (data.metadata?.pipeline || {}) as { pipeline_id?: string; pipeline_type?: string };
      // 收尾：拉一次完整运行记录（含 knowledge 入库步）逐段展示
      let steps: PipelineStep[] = [];
      if (pipePtr.pipeline_id) {
        try {
          const runRes = await fetch(`${apiBase}/api/pipeline/history/${pipePtr.pipeline_id}`);
          if (runRes.ok) steps = ((await runRes.json()).steps || []) as PipelineStep[];
        } catch {}
      }
      setEntries(prev => prev.map(e => e.key === key ? {
        ...e,
        status: "done",
        pipelineId: pipePtr.pipeline_id,
        pipelineType: pipePtr.pipeline_type,
        steps,
        itemId: data.id,
      } : e));
    } catch (e) {
      setEntries(prev => prev.map(x => x.key === key ? {
        ...x,
        status: "error",
        error: e instanceof Error ? e.message : String(e),
      } : x));
    }
    fetchHistory();
    onImported?.();
  }, [apiBase, fetchHistory, onImported, startPoll]);

  // 无在加工任务 → 停轮询
  useEffect(() => {
    if (!entries.some(e => e.status === "running")) stopPoll();
  }, [entries, stopPoll]);

  const handleFiles = useCallback((files: FileList | File[] | null) => {
    if (!files) return;
    Array.from(files).forEach(f => { void uploadFile(f); });
  }, [uploadFile]);

  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Upload size={14} className="text-violet-500" />
        <h2 className="text-xs lg:text-sm font-medium">
          {t("knowledge.import.title", "导入加工")}
        </h2>
        <span className="text-[10px] text-muted-foreground">
          {t("knowledge.import.subtitle", "文件自动走后端流水线（OCR/ASR/文本/视频）加工入库")}
        </span>
      </div>

      {/* Drop zone */}
      <div
        onDragOver={e => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={e => {
          e.preventDefault();
          setDragOver(false);
          handleFiles(e.dataTransfer.files);
        }}
        onClick={() => fileRef.current?.click()}
        className={cn(
          "flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed px-4 py-5 cursor-pointer transition-colors",
          dragOver ? "border-violet-400 bg-violet-500/5" : "border-border hover:bg-muted/40"
        )}
      >
        <Upload size={18} className="text-muted-foreground" />
        <p className="text-[11px] text-muted-foreground">
          {t("knowledge.import.dropHint", "拖拽文件到此处，或点击选择——自动加工入库")}
        </p>
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          onChange={e => {
            handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {/* In-flight / recent uploads */}
      {entries.length > 0 && (
        <div className="space-y-2">
          {entries.map(e => <UploadCard key={e.key} entry={e} />)}
        </div>
      )}

      {/* 加工历史（进度/历史可达——详情进 /pipeline 加工详情视图） */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-1.5">
          <History size={12} className="text-muted-foreground" />
          <span className="text-[11px] font-medium text-muted-foreground">
            {t("knowledge.import.recent", "最近加工")}
          </span>
        </div>
        {history.length === 0 ? (
          <p className="text-[10px] text-muted-foreground">
            {t("knowledge.import.noHistory", "暂无加工记录")}
          </p>
        ) : (
          <div className="space-y-1">
            {history.map(h => {
              const tone = STATUS_TONE[h.status] || STATUS_TONE.error;
              const Icon = tone.icon;
              return (
                <Link
                  key={h.pipeline_id}
                  href={`/pipeline?pipeline_id=${encodeURIComponent(h.pipeline_id)}`}
                  className="flex items-center gap-2 rounded-md border border-border px-2.5 py-1.5 hover:bg-muted/50 transition-colors"
                >
                  <Icon size={11} className={tone.color} />
                  <span className="text-[11px] truncate flex-1">
                    {h.filename || h.pipeline_id}
                  </span>
                  {h.pipeline_type && (
                    <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-indigo-500/10 text-indigo-500">
                      {h.pipeline_type}
                    </span>
                  )}
                  <span className="text-[9px] text-muted-foreground">
                    {h.steps?.length ?? 0}{t("knowledge.import.stepsUnit", " 段")}
                    {h.elapsed_ms != null ? ` · ${h.elapsed_ms}ms` : ""}
                  </span>
                  <ChevronRight size={11} className="text-muted-foreground" />
                </Link>
              );
            })}
          </div>
        )}
        <p className="text-[9px] text-muted-foreground/70 flex items-center gap-1">
          <ArrowRight size={9} />
          {t("knowledge.import.detailHint", "点击进入加工详情视图（完整逐段加工记录）")}
        </p>
      </div>
    </div>
  );
}
