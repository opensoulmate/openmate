"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import { useTranslation } from "react-i18next";
import {
  Upload, RefreshCw, Loader2, CheckCircle, XCircle, AlertTriangle,
  Shield, Eye, Brain, Droplets, Play, Clock, FileText, Zap,
  ChevronRight, ChevronDown, History, Settings, ArrowRight,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface PipelineStep {
  step: string;
  status: string;
  [key: string]: unknown;
}

interface PipelineResult {
  pipeline_id: string;
  status: string;
  file_id?: string;
  pipeline_type: string;
  steps: PipelineStep[];
  started_at: number;
  finished_at: number;
  elapsed_ms: number;
  error?: string;
}

interface PipelineType {
  key: string;
  label: string;
  description: string;
}

interface PipelineStage {
  key: string;
  label: string;
  emoji: string;
  description: string;
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

const STATUS_CONFIG: Record<string, { color: string; bg: string; icon: typeof CheckCircle }> = {
  ok: { color: "text-emerald-500", bg: "bg-emerald-500/10", icon: CheckCircle },
  completed: { color: "text-emerald-500", bg: "bg-emerald-500/10", icon: CheckCircle },
  error: { color: "text-red-500", bg: "bg-red-500/10", icon: XCircle },
  skipped: { color: "text-yellow-500", bg: "bg-yellow-500/10", icon: AlertTriangle },
  blocked: { color: "text-red-500", bg: "bg-red-500/10", icon: Shield },
  running: { color: "text-blue-500", bg: "bg-blue-500/10", icon: Loader2 },
};

export function PipelineClient() {
  const apiBase = getApiBaseUrl();
  const fileRef = useRef<HTMLInputElement>(null);
  const { t } = useTranslation();

  // State
  const [pipeline, setPipeline] = useState("auto");
  const [userId, setUserId] = useState("default");
  const [tags, setTags] = useState("");
  const [skipImmune, setSkipImmune] = useState(false);
  const [skipKnowledge, setSkipKnowledge] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<PipelineResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<PipelineResult[]>([]);
  const [types, setTypes] = useState<PipelineType[]>([]);
  const [stages, setStages] = useState<PipelineStage[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [selectedHistory, setSelectedHistory] = useState<PipelineResult | null>(null);

  // Fetch pipeline types
  useEffect(() => {
    if (!apiBase) return;
    fetch(`${apiBase}/api/pipeline/types`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setTypes(data.types || []);
          setStages(data.stages || []);
        }
      })
      .catch(() => {});
  }, [apiBase]);

  // Fetch history
  const fetchHistory = useCallback(async () => {
    if (!apiBase) return;
    try {
      const res = await fetch(`${apiBase}/api/pipeline/history`);
      if (res.ok) {
        const data = await res.json();
        setHistory(data.pipelines || []);
      }
    } catch {}
  }, [apiBase]);

  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  // Upload handler
  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;

    setUploading(true);
    setError(null);
    setResult(null);

    const formData = new FormData();
    formData.append("file", file);
    formData.append("pipeline", pipeline);
    formData.append("user_id", userId);
    formData.append("tags", tags);
    formData.append("skip_immune", String(skipImmune));
    formData.append("skip_knowledge", String(skipKnowledge));

    try {
      const res = await fetch(`${apiBase}/api/pipeline/upload`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || data.error || "Pipeline failed");
      } else {
        setResult(data);
        fetchHistory();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setUploading(false);
    }
  };

  return (
      <PageLayout title="流水线">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-indigo-500/10">
            <Zap size={18} className="text-indigo-500" />
          </div>
          <div>
            <h1 className="text-base font-semibold">{t('pipeline.title')}</h1>
            <p className="text-xs text-muted-foreground">{t('pipeline.subtitle')}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { setShowHistory(!showHistory); setSelectedHistory(null); }}
            className={cn(
              "flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs transition-colors",
              showHistory ? "bg-primary/10 border-primary/20 text-primary" : "border-border hover:bg-muted"
            )}
          >
            <History size={12} />
            {t('pipeline.historyWithCount', { count: history.length })}
          </button>
          <button
            onClick={() => setShowSettings(!showSettings)}
            className={cn(
              "flex items-center gap-2 rounded-[24px] border px-3 py-1.5 text-xs transition-colors",
              showSettings ? "bg-primary/10 border-primary/20 text-primary" : "border-border hover:bg-muted"
            )}
          >
            <Settings size={12} />
            {t('pipeline.config')}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto p-3 lg:p-6 space-y-3 lg:space-y-6">

          {/* Pipeline Flow Visualization */}
          <div className="rounded-xl border border-border bg-card p-5">
            <h2 className="text-xs lg:text-sm font-medium mb-4">{t('pipeline.processingFlow')}</h2>
            <div className="flex items-center justify-between gap-2">
              {stages.map((stage, i) => (
                <div key={stage.key} className="flex items-center gap-2">
                  <div className="flex flex-col items-center gap-1.5">
                    <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-muted/50 text-lg">
                      {stage.emoji}
                    </div>
                    <span className="text-[10px] font-medium text-muted-foreground">{stage.label}</span>
                  </div>
                  {i < stages.length - 1 && (
                    <ArrowRight size={14} className="text-muted-foreground/40 shrink-0" />
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Upload Area */}
          <div className="rounded-xl border border-border bg-card p-5 space-y-4">
            <h2 className="text-xs lg:text-sm font-medium">{t('pipeline.uploadFile')}</h2>

            {/* Pipeline type selector */}
            <div className="flex flex-wrap gap-2">
              {types.map(pt => (
                <button
                  key={pt.key}
                  onClick={() => setPipeline(pt.key)}
                  className={cn(
                    "rounded-lg border px-3 py-1.5 text-xs transition-colors",
                    pipeline === pt.key
                      ? "bg-primary/10 border-primary/30 text-primary font-medium"
                      : "border-border hover:bg-muted text-muted-foreground"
                  )}
                  title={pt.description}
                >
                  {pt.label}
                </button>
              ))}
            </div>

            {/* File input */}
            <div className="flex items-center gap-2 lg:gap-3">
              <input
                ref={fileRef}
                type="file"
                className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm file:mr-3 file:rounded-md file:border-0 file:bg-muted file:px-3 file:py-1 file:text-xs file:font-medium hover:file:bg-muted/80"
              />
              <button
                onClick={handleUpload}
                disabled={uploading}
                className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                {uploading ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Play size={14} />
                )}
                {uploading ? t('pipeline.processing') : t('pipeline.startProcessing')}
              </button>
            </div>

            {/* Advanced settings (inline) */}
            {showSettings && (
              <div className="rounded-lg border border-dashed border-border p-3 lg:p-4 space-y-3">
                <div className="grid grid-cols-2 gap-2 lg:gap-3">
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">{t('pipeline.userId')}</label>
                    <input
                      value={userId}
                      onChange={e => setUserId(e.target.value)}
                      className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs"
                      placeholder="default"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">{t('pipeline.tagsLabel')}</label>
                    <input
                      value={tags}
                      onChange={e => setTags(e.target.value)}
                      className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs"
                      placeholder="标签1, 标签2"
                    />
                  </div>
                </div>
                <div className="flex items-center gap-2 lg:gap-4">
                  <label className="flex items-center gap-2 text-xs cursor-pointer">
                    <input
                      type="checkbox"
                      checked={skipImmune}
                      onChange={e => setSkipImmune(e.target.checked)}
                      className="rounded"
                    />
                    {t('pipeline.skipSecurity')}
                  </label>
                  <label className="flex items-center gap-2 text-xs cursor-pointer">
                    <input
                      type="checkbox"
                      checked={skipKnowledge}
                      onChange={e => setSkipKnowledge(e.target.checked)}
                      className="rounded"
                    />
                    {t('pipeline.skipKnowledge')}
                  </label>
                </div>
              </div>
            )}
          </div>

          {/* Error */}
          {error && (
            <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-3 lg:p-4 flex items-start gap-2 lg:gap-3">
              <XCircle size={16} className="text-red-500 shrink-0 mt-0.5" />
              <div>
                <p className="text-xs lg:text-sm font-medium text-red-500">{t('pipeline.pipelineFailed')}</p>
                <p className="text-xs text-red-500/80 mt-1">{error}</p>
              </div>
            </div>
          )}

          {/* Result */}
          {result && (
            <PipelineResultCard result={result} stages={stages} />
          )}

          {/* Selected History Item */}
          {selectedHistory && (
            <PipelineResultCard result={selectedHistory} stages={stages} />
          )}

          {/* History List */}
          {showHistory && history.length > 0 && (
            <div className="rounded-xl border border-border bg-card p-5 space-y-3">
              <h2 className="text-xs lg:text-sm font-medium">{t('pipeline.executionHistory')}</h2>
              <div className="space-y-2">
                {history.map((h) => {
                  const cfg = STATUS_CONFIG[h.status] || STATUS_CONFIG.error;
                  const Icon = cfg.icon;
                  return (
                    <button
                      key={h.pipeline_id}
                      onClick={() => setSelectedHistory(h)}
                      className={cn(
                        "flex w-full items-center gap-2 lg:gap-3 rounded-lg border p-3 text-left transition-colors hover:bg-muted/50",
                        selectedHistory?.pipeline_id === h.pipeline_id ? "border-primary/30 bg-primary/5" : "border-border"
                      )}
                    >
                      <Icon size={14} className={cfg.color} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-medium">{h.pipeline_type}</span>
                          <span className={cn("text-[10px] px-1.5 py-0.5 rounded-full", cfg.color, cfg.bg)}>
                            {h.status}
                          </span>
                        </div>
                        <span className="text-[10px] text-muted-foreground">
                          {h.pipeline_id} · {h.elapsed_ms}ms · {h.steps.length} {t('pipeline.steps')}
                        </span>
                      </div>
                      <ChevronRight size={14} className="text-muted-foreground" />
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {showHistory && history.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <History size={32} className="mb-3 opacity-50" />
              <p className="text-xs lg:text-sm">{t('pipeline.noHistory')}</p>
              <p className="text-xs mt-1">{t('pipeline.noHistoryHint')}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  
      </PageLayout>
    );
}

function PipelineResultCard({ result, stages }: { result: PipelineResult; stages: PipelineStage[] }) {
  const { t } = useTranslation();
  const cfg = STATUS_CONFIG[result.status] || STATUS_CONFIG.error;
  const Icon = cfg.icon;

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      {/* Result header */}
      <div className={cn("flex items-center justify-between px-5 py-3 border-b", cfg.bg)}>
        <div className="flex items-center gap-2 lg:gap-3">
          <Icon size={16} className={cfg.color} />
          <div>
            <span className="text-xs lg:text-sm font-medium">
              {result.status === "completed" ? t('pipeline.completed') : result.status === "blocked" ? t('pipeline.blocked') : t('pipeline.partialComplete')}
            </span>
            <span className="text-xs text-muted-foreground ml-2">{result.pipeline_id}</span>
          </div>
        </div>
        <div className="flex items-center gap-2 lg:gap-3 text-xs text-muted-foreground">
          <span>{result.pipeline_type}</span>
          <span>{result.elapsed_ms}ms</span>
        </div>
      </div>

      {/* Steps */}
      <div className="p-5 space-y-3">
        {result.steps.map((step, i) => {
          const stepCfg = STATUS_CONFIG[step.status] || STATUS_CONFIG.error;
          const StepIcon = STEP_ICONS[step.step] || Zap;
          const StepStatusIcon = stepCfg.icon;
          const stageInfo = stages.find(s => step.step.includes(s.key));

          return (
            <div key={i} className="flex items-start gap-2 lg:gap-3">
              {/* Connector line */}
              <div className="flex flex-col items-center">
                <div className={cn("flex items-center justify-center w-8 h-8 rounded-lg", stepCfg.bg)}>
                  <StepIcon size={14} className={stepCfg.color} />
                </div>
                {i < result.steps.length - 1 && (
                  <div className="w-px h-4 bg-border mt-1" />
                )}
              </div>

              {/* Step details */}
              <div className="flex-1 min-w-0 pt-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium">
                    {stageInfo?.label || step.step}
                  </span>
                  <StepStatusIcon size={12} className={stepCfg.color} />
                  <span className={cn("text-[10px] px-1.5 py-0.5 rounded-full", stepCfg.color, stepCfg.bg)}>
                    {step.status}
                  </span>
                </div>

                {/* Step-specific details */}
                <div className="mt-1 text-[11px] text-muted-foreground space-y-0.5">
                  {step.file_id != null && <p>{t('pipeline.fileId')}: {String(step.file_id).slice(0, 16)}...</p>}
                  {step.name != null && <p>{t('pipeline.fileName')}: {String(step.name)}</p>}
                  {step.size != null && <p>{t('pipeline.size')}: {Number(step.size).toLocaleString()} bytes</p>}
                  {step.text_length != null && <p>{t('pipeline.extractedText')}: {Number(step.text_length).toLocaleString()} {t('pipeline.chars')}</p>}
                  {step.confidence != null && <p>{t('pipeline.confidence')}: {(Number(step.confidence) * 100).toFixed(1)}%</p>}
                  {step.engine != null && <p>{t('pipeline.engine')}: {String(step.engine)}</p>}
                  {step.is_safe != null && (
                    <p className={step.is_safe ? "text-emerald-500" : "text-red-500"}>
                      {step.is_safe ? t('pipeline.safe') : t('pipeline.risky')} {step.risk_level != null && `(${String(step.risk_level)})`}
                    </p>
                  )}
                  {step.error != null && <p className="text-red-500">{t('pipeline.errorLabel')}: {String(step.error)}</p>}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Error footer */}
      {result.error && (
        <div className="border-t border-border px-5 py-3 bg-red-500/5">
          <p className="text-xs text-red-500">{result.error}</p>
        </div>
      )}
    </div>
  );
}
