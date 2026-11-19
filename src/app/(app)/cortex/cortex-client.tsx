"use client";
import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl, getUserId } from "@/lib/api-client";
import {
  Brain, GitBranch, Users, Lightbulb, Loader2,
  ChevronRight, Play, RotateCcw, Zap, Target,
  ArrowDown, CheckCircle2, Circle, AlertCircle,
  Network, TrendingUp, Star, Award, Search,
  RefreshCw, BookOpen, BarChart3,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

// ── Types ────────────────────────────────────────────────

interface TaskItem {
  index: number;
  description: string;
  dependencies: number[];
  priority: number;
}

interface PlanResult {
  goal: string;
  tasks: TaskItem[];
}

interface AgentStep {
  role: string;
  output: string;
}

interface AgentResult {
  topic: string;
  steps: AgentStep[];
  final_output: string;
}

interface ThinkResult {
  question: string;
  reasoning_steps: string[];
  answer: string;
  confidence: number;
}

interface GraphEntity {
  name: string;
  type: string;
  count: number;
}

interface GraphRelation {
  source: string;
  target: string;
  type: string;
}

interface GraphQueryResult {
  center_entity: string;
  entities: GraphEntity[];
  relations: GraphRelation[];
  depth: number;
}

interface ExtractResult {
  entities: { name: string; type: string }[];
  relations: { source: string; target: string; type: string }[];
  entity_count: number;
  relation_count: number;
}

interface RecommendEntry {
  id: string;
  title: string;
  score?: number;
  snippet?: string;
  tags?: string[];
}


interface QualityScore {
  knowledge_id: string;
  title: string;
  total_score: number;
  grade: string;
  scores: Record<string, number>;
}

interface QualityReport {
  total: number;
  avg_score: number;
  grade: string;
  distribution: Record<string, number>;
  dimension_averages: Record<string, number>;
  top_5: QualityScore[];
  bottom_5: QualityScore[];
}

type TabId = "plan" | "agent" | "think" | "graphrag" | "recommend" | "quality";



const GRADE_COLORS: Record<string, string> = {
  "A+": "text-emerald-500 bg-emerald-500/10",
  "A": "text-emerald-400 bg-emerald-400/10",
  "B+": "text-blue-500 bg-blue-500/10",
  "B": "text-blue-400 bg-blue-400/10",
  "C+": "text-yellow-500 bg-yellow-500/10",
  "C": "text-yellow-400 bg-yellow-400/10",
  "D": "text-red-500 bg-red-500/10",
};

// ── Component ────────────────────────────────────────────

export function CortexClient() {
  const { t } = useTranslation();
  const PRIORITY_LABEL: Record<number, { label: string; color: string }> = {
    1: { label: t('cortex.plan.high'), color: "text-red-500 bg-red-500/10" },
    2: { label: t('cortex.plan.medium'), color: "text-yellow-500 bg-yellow-500/10" },
    3: { label: t('cortex.plan.low'), color: "text-green-500 bg-green-500/10" },
  };
  const apiBase = getApiBaseUrl();
  const userId = getUserId() || "default";
  const [tab, setTab] = useState<TabId>("plan");

  // Plan state
  const [planGoal, setPlanGoal] = useState("");
  const [planResult, setPlanResult] = useState<PlanResult | null>(null);
  const [planLoading, setPlanLoading] = useState(false);

  // Agent state
  const [agentTopic, setAgentTopic] = useState("");
  const [agentResult, setAgentResult] = useState<AgentResult | null>(null);
  const [agentLoading, setAgentLoading] = useState(false);

  // Think state
  const [thinkQuestion, setThinkQuestion] = useState("");
  const [thinkContext, setThinkContext] = useState("");
  const [thinkResult, setThinkResult] = useState<ThinkResult | null>(null);
  const [thinkLoading, setThinkLoading] = useState(false);

  // GraphRAG state
  const [graphragLoading, setGraphragLoading] = useState(false);
  const [graphragBuildResult, setGraphragBuildResult] = useState<any>(null);
  const [graphQueryName, setGraphQueryName] = useState("");
  const [graphQueryDepth, setGraphQueryDepth] = useState(2);
  const [graphQueryResult, setGraphQueryResult] = useState<GraphQueryResult | null>(null);
  const [graphQueryLoading, setGraphQueryLoading] = useState(false);
  const [extractText, setExtractText] = useState("");
  const [extractResult, setExtractResult] = useState<ExtractResult | null>(null);
  const [extractLoading, setExtractLoading] = useState(false);

  // Recommend state
  const [recommendTab, setRecommendTab] = useState<"trending" | "recent" | "related">("trending");
  const [recommendEntries, setRecommendEntries] = useState<RecommendEntry[]>([]);
  const [recommendLoading, setRecommendLoading] = useState(false);
  const [relatedId, setRelatedId] = useState("");
  const [relatedResults, setRelatedResults] = useState<RecommendEntry[]>([]);

  // Quality state
  const [qualityReport, setQualityReport] = useState<QualityReport | null>(null);
  const [qualityLoading, setQualityLoading] = useState(false);
  const [qualityScores, setQualityScores] = useState<QualityScore[]>([]);
  const [batchLoading, setBatchLoading] = useState(false);
  const [singleId, setSingleId] = useState("");
  const [singleScore, setSingleScore] = useState<QualityScore | null>(null);
  const [singleLoading, setSingleLoading] = useState(false);

  const [error, setError] = useState("");

  // ── Original API calls ─────────────────────────────────

  const handlePlan = useCallback(async () => {
    if (!planGoal.trim()) return;
    setPlanLoading(true);
    setError("");
    setPlanResult(null);
    try {
      const res = await fetch(`${apiBase}/api/cortex/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal: planGoal }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      setPlanResult(await res.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setPlanLoading(false);
    }
  }, [planGoal, apiBase]);

  const handleAgent = useCallback(async () => {
    if (!agentTopic.trim()) return;
    setAgentLoading(true);
    setError("");
    setAgentResult(null);
    try {
      const res = await fetch(`${apiBase}/api/cortex/agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: agentTopic }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      setAgentResult(await res.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAgentLoading(false);
    }
  }, [agentTopic, apiBase]);

  const handleThink = useCallback(async () => {
    if (!thinkQuestion.trim()) return;
    setThinkLoading(true);
    setError("");
    setThinkResult(null);
    try {
      const res = await fetch(`${apiBase}/api/cortex/think`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: thinkQuestion, context: thinkContext }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      setThinkResult(await res.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setThinkLoading(false);
    }
  }, [thinkQuestion, thinkContext, apiBase]);

  // ── GraphRAG API calls ─────────────────────────────────

  const handleGraphragBuild = useCallback(async () => {
    setGraphragLoading(true);
    setError("");
    setGraphragBuildResult(null);
    try {
      const res = await fetch(`${apiBase}/api/cortex/graphrag/build?user_id=${userId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      setGraphragBuildResult(await res.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setGraphragLoading(false);
    }
  }, [apiBase, userId]);

  const handleGraphQuery = useCallback(async () => {
    if (!graphQueryName.trim()) return;
    setGraphQueryLoading(true);
    setError("");
    setGraphQueryResult(null);
    try {
      const res = await fetch(`${apiBase}/api/cortex/graphrag/query?user_id=${userId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity_name: graphQueryName, depth: graphQueryDepth }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      setGraphQueryResult(await res.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setGraphQueryLoading(false);
    }
  }, [graphQueryName, graphQueryDepth, apiBase, userId]);

  const handleExtract = useCallback(async () => {
    if (!extractText.trim()) return;
    setExtractLoading(true);
    setError("");
    setExtractResult(null);
    try {
      const res = await fetch(`${apiBase}/api/cortex/graphrag/extract?text=${encodeURIComponent(extractText)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      setExtractResult(await res.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setExtractLoading(false);
    }
  }, [extractText, apiBase]);

  // ── Recommend API calls ────────────────────────────────

  const fetchRecommend = useCallback(async (type: "trending" | "recent") => {
    setRecommendLoading(true);
    setError("");
    setRecommendEntries([]);
    try {
      const res = await fetch(`${apiBase}/api/cortex/recommend/${type}?user_id=${userId}&limit=20`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setRecommendEntries(data.entries || []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRecommendLoading(false);
    }
  }, [apiBase, userId]);

  const handleRelatedRecommend = useCallback(async () => {
    if (!relatedId.trim()) return;
    setRecommendLoading(true);
    setError("");
    setRelatedResults([]);
    try {
      const res = await fetch(`${apiBase}/api/cortex/recommend/${relatedId}?user_id=${userId}&limit=10`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setRelatedResults(data.recommendations || []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRecommendLoading(false);
    }
  }, [relatedId, apiBase, userId]);

  // ── Quality API calls ──────────────────────────────────

  const fetchQualityReport = useCallback(async () => {
    setQualityLoading(true);
    setError("");
    setQualityReport(null);
    try {
      const res = await fetch(`${apiBase}/api/cortex/quality/report?user_id=${userId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setQualityReport(await res.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setQualityLoading(false);
    }
  }, [apiBase, userId]);

  const fetchBatchScores = useCallback(async () => {
    setBatchLoading(true);
    setError("");
    setQualityScores([]);
    try {
      const res = await fetch(`${apiBase}/api/cortex/quality/batch?user_id=${userId}&limit=50`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setQualityScores(data.scores || []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBatchLoading(false);
    }
  }, [apiBase, userId]);

  const handleSingleScore = useCallback(async () => {
    if (!singleId.trim()) return;
    setSingleLoading(true);
    setError("");
    setSingleScore(null);
    try {
      const res = await fetch(`${apiBase}/api/cortex/quality/score/${singleId}?user_id=${userId}`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      setSingleScore(await res.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSingleLoading(false);
    }
  }, [singleId, apiBase, userId]);

  // ── Tab config ───────────────────────────────────────

  const tabs: { id: TabId; label: string; icon: React.ElementType; desc: string }[] = [
    { id: "plan", label: t('cortex.text4'), icon: GitBranch, desc: t('cortex.text5') },
    { id: "agent", label: t('cortex.text6'), icon: Users, desc: t('cortex.text7') },
    { id: "think", label: t('cortex.text8'), icon: Lightbulb, desc: t('cortex.text9') },
    { id: "graphrag", label: "GraphRAG", icon: Network, desc: t('cortex.auto') },
    { id: "recommend", label: t('cortex.recommended'), icon: TrendingUp, desc: t('cortex.text10') },
    { id: "quality", label: t('cortex.text11'), icon: Award, desc: t('cortex.text12') },
  ];

  return (
      <PageLayout title="皮层">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10">
            <Brain size={18} className="text-primary" />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg lg:text-xl font-semibold truncate">OpenCortex</h1>
            <p className="text-xs lg:text-sm text-muted-foreground truncate">{t('organTag.cortex')}</p>
          </div>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex flex-wrap gap-1 border-b border-border px-3 lg:px-6 py-2 overflow-x-auto">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => { setTab(t.id); setError(""); }}
            className={cn(
              "flex items-center gap-2 rounded-lg px-2 lg:px-4 py-2 text-xs lg:text-sm transition-colors whitespace-nowrap",
              tab === t.id
                ? "bg-primary/10 text-primary font-medium"
                : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
            )}
            title={t.desc}
          >
            <t.icon size={15} />
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4 lg:py-6">
        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-2 lg:px-4 py-2 lg:py-3 text-xs lg:text-sm text-red-500">
            <AlertCircle size={16} />
            {error}
          </div>
        )}

        {/* ── Plan Tab ───────────────────────────────── */}
        {tab === "plan" && (
          <div className="space-y-3 lg:space-y-6">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <h3 className="mb-3 flex items-center gap-2 text-xs lg:text-sm font-medium">
                <Target size={15} className="text-primary" />
                {t('cortex.plan.inputGoal')}
              </h3>
              <textarea
                value={planGoal}
                onChange={(e) => setPlanGoal(e.target.value)}
                placeholder={t('cortex.description')}
                className="w-full rounded-lg border border-border bg-background px-2 lg:px-4 py-2 lg:py-3 text-xs lg:text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
                rows={3}
              />
              <div className="mt-3 flex gap-2">
                <button
                  onClick={handlePlan}
                  disabled={planLoading || !planGoal.trim()}
                  className="flex items-center gap-2 rounded-lg bg-primary px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {planLoading ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
                  {planLoading ? t('cortex.text13') : t('cortex.start')}
                </button>
                {planResult && (
                  <button
                    onClick={() => { setPlanResult(null); setPlanGoal(""); }}
                    className="flex items-center gap-2 rounded-lg border border-border px-2 lg:px-4 py-2 text-xs lg:text-sm text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <RotateCcw size={15} />
                    {t('cortex.plan.clear')}
                  </button>
                )}
              </div>
            </div>

            {planResult && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <h3 className="mb-4 flex items-center gap-2 text-xs lg:text-sm font-medium">
                  <GitBranch size={15} className="text-primary" />
                  {t('cortex.plan.taskBreakdown')} · {planResult.tasks.length} {t('cortex.plan.tasks')}
                </h3>
                <div className="space-y-3">
                  {planResult.tasks.map((task, i) => (
                    <div
                      key={i}
                      className="flex items-start gap-2 lg:gap-3 rounded-xl border border-border bg-background p-3 lg:p-4 lg:p-4"
                    >
                      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary">
                        {task.index + 1}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs lg:text-sm font-medium">{task.description}</p>
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", PRIORITY_LABEL[task.priority]?.color)}>
                            {PRIORITY_LABEL[task.priority]?.label || `P${task.priority}`}
                          </span>
                          {task.dependencies.length > 0 && (
                            <span className="text-[11px] text-muted-foreground">
                              {t('cortex.plan.depends')}: {task.dependencies.map(d => `#${d + 1}`).join(', ')}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Agent Tab ──────────────────────────────── */}
        {tab === "agent" && (
          <div className="space-y-3 lg:space-y-6">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <h3 className="mb-3 flex items-center gap-2 text-xs lg:text-sm font-medium">
                <Users size={15} className="text-primary" />
                {t('cortex.agent.inputTopic')}
              </h3>
              <textarea
                value={agentTopic}
                onChange={(e) => setAgentTopic(e.target.value)}
                placeholder={t('cortex.text17')}
                className="w-full rounded-lg border border-border bg-background px-2 lg:px-4 py-2 lg:py-3 text-xs lg:text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
                rows={3}
              />
              <div className="mt-3 flex gap-2">
                <button
                  onClick={handleAgent}
                  disabled={agentLoading || !agentTopic.trim()}
                  className="flex items-center gap-2 rounded-lg bg-primary px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {agentLoading ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />}
                  {agentLoading ? t('cortex.text18') : t('cortex.text19')}
                </button>
                {agentResult && (
                  <button
                    onClick={() => { setAgentResult(null); setAgentTopic(""); }}
                    className="flex items-center gap-2 rounded-lg border border-border px-2 lg:px-4 py-2 text-xs lg:text-sm text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <RotateCcw size={15} />
                    {t('cortex.plan.clear')}
                  </button>
                )}
              </div>
            </div>

            {agentResult && (
              <div className="space-y-4">
                <h3 className="flex items-center gap-2 text-xs lg:text-sm font-medium">
                  <Zap size={15} className="text-primary" />
                  {t('cortex.agent.pipelineResult')} · {agentResult.steps?.length || 0} {t('cortex.agent.steps')}
                </h3>
                {agentResult.steps?.map((step, i) => {
                  const roleColors: Record<string, string> = {
                    researcher: "bg-blue-500/10 text-blue-500 border-blue-500/30",
                    analyzer: "bg-purple-500/10 text-purple-500 border-purple-500/30",
                    writer: "bg-green-500/10 text-green-500 border-green-500/30",
                  };
                  const colorClass = roleColors[step.role?.toLowerCase()] || "bg-muted text-foreground border-border";
                  return (
                    <div key={i} className="rounded-xl border border-border bg-card overflow-hidden">
                      <div className={cn("flex items-center gap-2 border-b px-5 py-2 lg:py-3", colorClass)}>
                        <div className="flex h-6 w-6 items-center justify-center rounded-full bg-current/10 text-xs font-bold">
                          {i + 1}
                        </div>
                        <span className="text-xs lg:text-sm font-medium capitalize">{step.role || `Step ${i + 1}`}</span>
                      </div>
                      <div className="px-5 py-4">
                        <p className="whitespace-pre-wrap text-xs lg:text-sm leading-relaxed text-foreground/90">{step.output}</p>
                      </div>
                    </div>
                  );
                })}
                {agentResult.final_output && (
                  <div className="rounded-xl border border-primary/30 bg-primary/5 p-5">
                    <h4 className="mb-3 flex items-center gap-2 text-xs lg:text-sm font-medium text-primary">
                      <CheckCircle2 size={15} />
                      {t('cortex.agent.finalOutput')}
                    </h4>
                    <p className="whitespace-pre-wrap text-xs lg:text-sm leading-relaxed">{agentResult.final_output}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Think Tab ──────────────────────────────── */}
        {tab === "think" && (
          <div className="space-y-3 lg:space-y-6">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <h3 className="mb-3 flex items-center gap-2 text-xs lg:text-sm font-medium">
                <Lightbulb size={15} className="text-primary" />
                {t('cortex.think.inputQuestion')}
              </h3>
              <textarea
                value={thinkQuestion}
                onChange={(e) => setThinkQuestion(e.target.value)}
                placeholder={t('cortex.text22')}
                className="w-full rounded-lg border border-border bg-background px-2 lg:px-4 py-2 lg:py-3 text-xs lg:text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
                rows={2}
              />
              <textarea
                value={thinkContext}
                onChange={(e) => setThinkContext(e.target.value)}
                placeholder={t('cortex.text23')}
                className="mt-2 w-full rounded-lg border border-border bg-background px-2 lg:px-4 py-2 lg:py-3 text-xs lg:text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
                rows={2}
              />
              <div className="mt-3 flex gap-2">
                <button
                  onClick={handleThink}
                  disabled={thinkLoading || !thinkQuestion.trim()}
                  className="flex items-center gap-2 rounded-lg bg-primary px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {thinkLoading ? <Loader2 size={15} className="animate-spin" /> : <Lightbulb size={15} />}
                  {thinkLoading ? t('cortex.text24') : t('cortex.text25')}
                </button>
                {thinkResult && (
                  <button
                    onClick={() => { setThinkResult(null); setThinkQuestion(""); setThinkContext(""); }}
                    className="flex items-center gap-2 rounded-lg border border-border px-2 lg:px-4 py-2 text-xs lg:text-sm text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <RotateCcw size={15} />
                    {t('cortex.plan.clear')}
                  </button>
                )}
              </div>
            </div>

            {thinkResult && (
              <div className="space-y-4">
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <h3 className="mb-4 flex items-center gap-2 text-xs lg:text-sm font-medium">
                    <Lightbulb size={15} className="text-primary" />
                    {t('cortex.think.reasoningChain')} · {thinkResult.reasoning_steps?.length || 0} {t('cortex.think.steps')}
                  </h3>
                  <div className="space-y-3">
                    {thinkResult.reasoning_steps?.map((step, i) => (
                      <div key={i} className="flex items-start gap-2 lg:gap-3">
                        <div className="flex flex-col items-center">
                          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary">
                            {i + 1}
                          </div>
                          {i < (thinkResult.reasoning_steps?.length || 0) - 1 && (
                            <div className="w-px h-6 bg-border mt-1" />
                          )}
                        </div>
                        <p className="flex-1 pt-0.5 text-xs lg:text-sm leading-relaxed text-foreground/90">{step}</p>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="rounded-xl border border-primary/30 bg-primary/5 p-5">
                  <div className="mb-3 flex items-center justify-between">
                    <h4 className="flex items-center gap-2 text-xs lg:text-sm font-medium text-primary">
                      <CheckCircle2 size={15} />
                      {t('cortex.think.conclusion')}
                    </h4>
                    {thinkResult.confidence !== undefined && (
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">{t('cortex.text28')}</span>
                        <div className="h-2 w-24 rounded-full bg-muted overflow-hidden">
                          <div
                            className="h-full rounded-full bg-primary transition-all"
                            style={{ width: `${Math.round(thinkResult.confidence * 100)}%` }}
                          />
                        </div>
                        <span className="text-xs font-medium text-primary">{Math.round(thinkResult.confidence * 100)}%</span>
                      </div>
                    )}
                  </div>
                  <p className="whitespace-pre-wrap text-xs lg:text-sm leading-relaxed">{thinkResult.answer}</p>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── GraphRAG Tab ───────────────────────────── */}
        {tab === "graphrag" && (
          <div className="space-y-3 lg:space-y-6">
            {/* Build Graph */}
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <h3 className="mb-3 flex items-center gap-2 text-xs lg:text-sm font-medium">
                <Network size={15} className="text-primary" />
                {t('cortex.graphrag.buildTitle')}
              </h3>
              <p className="text-xs text-muted-foreground mb-3">
                {t('cortex.graphrag.buildDesc')}
              </p>
              <div className="flex gap-2">
                <button
                  onClick={handleGraphragBuild}
                  disabled={graphragLoading}
                  className="flex items-center gap-2 rounded-lg bg-primary px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {graphragLoading ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
                  {graphragLoading ? t('cortex.text29') : t('cortex.text30')}
                </button>
                {graphragBuildResult && (
                  <button
                    onClick={() => setGraphragBuildResult(null)}
                    className="flex items-center gap-2 rounded-lg border border-border px-2 lg:px-4 py-2 text-xs lg:text-sm text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <RotateCcw size={15} />
                    {t('cortex.plan.clear')}
                  </button>
                )}
              </div>
              {graphragBuildResult && (
                <div className="mt-4 rounded-xl border border-border bg-background p-3 lg:p-4 lg:p-4">
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-3">
                    <div className="text-center">
                      <p className="text-xl lg:text-2xl font-bold text-primary">{graphragBuildResult.entities_new || 0}</p>
                      <p className="text-xs text-muted-foreground">{t('cortex.text31')}</p>
                    </div>
                    <div className="text-center">
                      <p className="text-xl lg:text-2xl font-bold text-primary">{graphragBuildResult.relations_new || 0}</p>
                      <p className="text-xs text-muted-foreground">{t('cortex.text32')}</p>
                    </div>
                    <div className="text-center">
                      <p className="text-xl lg:text-2xl font-bold text-muted-foreground">{graphragBuildResult.entities_extracted || 0}</p>
                      <p className="text-xs text-muted-foreground">{t('cortex.text33')}</p>
                    </div>
                    <div className="text-center">
                      <p className="text-xl lg:text-2xl font-bold text-muted-foreground">{graphragBuildResult.relations_extracted || 0}</p>
                      <p className="text-xs text-muted-foreground">{t('cortex.text34')}</p>
                    </div>
                  </div>
                  {graphragBuildResult.knowledge_scanned !== undefined && (
                    <p className="mt-3 text-xs text-muted-foreground text-center">
                      {t('cortex.graphrag.scanned')} {graphragBuildResult.knowledge_scanned} {t('cortex.graphrag.entries')}
                    </p>
                  )}
                </div>
              )}
            </div>

            {/* Query Graph */}
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <h3 className="mb-3 flex items-center gap-2 text-xs lg:text-sm font-medium">
                <Search size={15} className="text-primary" />
                {t('cortex.graphrag.queryTitle')}
              </h3>
              <div className="flex gap-2 mb-3">
                <input
                  value={graphQueryName}
                  onChange={(e) => setGraphQueryName(e.target.value)}
                  placeholder={t('cortex.name')}
                  className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
                <select
                  value={graphQueryDepth}
                  onChange={(e) => setGraphQueryDepth(Number(e.target.value))}
                  className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm"
                >
                  <option value={1}>{t('cortex.text37')}</option>
                  <option value={2}>{t('cortex.text38')}</option>
                  <option value={3}>{t('cortex.text39')}</option>
                </select>
                <button
                  onClick={handleGraphQuery}
                  disabled={graphQueryLoading || !graphQueryName.trim()}
                  className="flex items-center gap-2 rounded-lg bg-primary px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {graphQueryLoading ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
                  {t('cortex.graphrag.query')}
                </button>
              </div>

              {graphQueryResult && (
                <div className="space-y-3">
                  <p className="text-xs text-muted-foreground">
                    {t('cortex.graphrag.found')}「{graphQueryResult.center_entity}」，{t('cortex.graphrag.depth')} {graphQueryResult.depth}，
                    {graphQueryResult.entities?.length || 0} {t('cortex.graphrag.entities')}、{graphQueryResult.relations?.length || 0} {t('cortex.graphrag.relations')}
                  </p>
                  {/* Entities */}
                  {graphQueryResult.entities && graphQueryResult.entities.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-2">{t('cortex.text45')}</p>
                      <div className="flex flex-wrap gap-2">
                        {graphQueryResult.entities.map((e, i) => (
                          <span key={i} className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-1 text-xs text-primary">
                            <span className="font-medium">{e.name}</span>
                            <span className="text-primary/60">({e.type})</span>
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {/* Relations */}
                  {graphQueryResult.relations && graphQueryResult.relations.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-2">{t('cortex.text46')}</p>
                      <div className="space-y-1.5">
                        {graphQueryResult.relations.map((r, i) => (
                          <div key={i} className="flex items-center gap-2 text-xs rounded bg-muted/30 px-3 py-1.5">
                            <span className="font-medium">{r.source}</span>
                            <ChevronRight size={12} className="text-muted-foreground" />
                            <span className="rounded bg-primary/10 px-1.5 py-0.5 text-primary">{r.type}</span>
                            <ChevronRight size={12} className="text-muted-foreground" />
                            <span className="font-medium">{r.target}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Extract from Text */}
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <h3 className="mb-3 flex items-center gap-2 text-xs lg:text-sm font-medium">
                <BookOpen size={15} className="text-primary" />
                {t('cortex.graphrag.extractTitle')}
              </h3>
              <textarea
                value={extractText}
                onChange={(e) => setExtractText(e.target.value)}
                placeholder={t('cortex.text47')}
                className="w-full rounded-lg border border-border bg-background px-2 lg:px-4 py-2 lg:py-3 text-xs lg:text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none mb-3"
                rows={4}
              />
              <button
                onClick={handleExtract}
                disabled={extractLoading || !extractText.trim()}
                className="flex items-center gap-2 rounded-lg bg-primary px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                {extractLoading ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />}
                {extractLoading ? t('cortex.text48') : t('cortex.text33')}
              </button>

              {extractResult && (
                <div className="mt-4 space-y-3">
                  <p className="text-xs text-muted-foreground">
                    {t('cortex.graphrag.extracted')} {extractResult.entity_count} {t('cortex.graphrag.entities')}、{extractResult.relation_count} {t('cortex.graphrag.relations')}
                  </p>
                  {extractResult.entities.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-2">{t('cortex.text45')}</p>
                      <div className="flex flex-wrap gap-2">
                        {extractResult.entities.map((e, i) => (
                          <span key={i} className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2.5 py-1 text-xs text-emerald-500">
                            {e.name} <span className="text-emerald-500/60">({e.type})</span>
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {extractResult.relations.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-2">{t('cortex.text46')}</p>
                      <div className="space-y-1.5">
                        {extractResult.relations.map((r, i) => (
                          <div key={i} className="flex items-center gap-2 text-xs rounded bg-muted/30 px-3 py-1.5">
                            <span className="font-medium">{r.source}</span>
                            <ChevronRight size={12} className="text-muted-foreground" />
                            <span className="rounded bg-primary/10 px-1.5 py-0.5 text-primary">{r.type}</span>
                            <ChevronRight size={12} className="text-muted-foreground" />
                            <span className="font-medium">{r.target}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Recommend Tab ──────────────────────────── */}
        {tab === "recommend" && (
          <div className="space-y-3 lg:space-y-6">
            {/* Sub-tabs */}
            <div className="flex gap-1">
              {(["trending", "recent", "related"] as const).map((st) => (
                <button
                  key={st}
                  onClick={() => {
                    setRecommendTab(st);
                    setRecommendEntries([]);
                    setRelatedResults([]);
                    if (st === "trending") fetchRecommend("trending");
                    if (st === "recent") fetchRecommend("recent");
                  }}
                  className={cn(
                    "flex items-center gap-2 rounded-lg px-2 lg:px-4 py-2 text-xs lg:text-sm transition-colors",
                    recommendTab === st
                      ? "bg-primary/10 text-primary font-medium"
                      : "text-muted-foreground hover:bg-muted/50"
                  )}
                >
                  {st === "trending" && <><TrendingUp size={14} /> {t("cortex.recommend.trending")}</>}
                  {st === "recent" && <><RefreshCw size={14} /> {t("cortex.recommend.recent")}</>}
                  {st === "related" && <><Star size={14} /> {t("cortex.recommend.related")}</>}
                </button>
              ))}
            </div>

            {/* Trending / Recent */}
            {(recommendTab === "trending" || recommendTab === "recent") && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-xs lg:text-sm font-medium">
                    {recommendTab === "trending" ? t('cortex.text53') : t('cortex.text54')}
                  </h3>
                  <button
                    onClick={() => fetchRecommend(recommendTab)}
                    disabled={recommendLoading}
                    className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground border border-border transition-colors"
                  >
                    {recommendLoading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                    {t('cortex.recommend.refresh')}
                  </button>
                </div>
                {recommendEntries.length === 0 && !recommendLoading && (
                  <p className="text-xs lg:text-sm text-muted-foreground text-center py-8">
                    {t('cortex.recommend.empty')}
                  </p>
                )}
                <div className="space-y-2">
                  {recommendEntries.map((entry, i) => (
                    <div key={entry.id || i} className="flex items-start gap-2 lg:gap-3 rounded-xl border border-border bg-background p-3 lg:p-4 hover:border-primary/30 transition-colors">
                      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary">
                        {i + 1}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs lg:text-sm font-medium">{entry.title}</p>
                        {entry.snippet && <p className="text-xs text-muted-foreground mt-1">{entry.snippet}</p>}
                        {entry.tags && entry.tags.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1.5">
                            {entry.tags.map((tag, j) => (
                              <span key={j} className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{tag}</span>
                            ))}
                          </div>
                        )}
                      </div>
                      {entry.score !== undefined && (
                        <span className="text-xs font-medium text-primary">{(entry.score * 100).toFixed(0)}%</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Related */}
            {recommendTab === "related" && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <h3 className="mb-3 flex items-center gap-2 text-xs lg:text-sm font-medium">
                  <Star size={15} className="text-primary" />
                  {t('cortex.recommend.relatedTitle')}
                </h3>
                <div className="flex gap-2 mb-4">
                  <input
                    value={relatedId}
                    onChange={(e) => setRelatedId(e.target.value)}
                    placeholder={t('cortex.text58')}
                    className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                  />
                  <button
                    onClick={handleRelatedRecommend}
                    disabled={recommendLoading || !relatedId.trim()}
                    className="flex items-center gap-2 rounded-lg bg-primary px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                  >
                    {recommendLoading ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
                    {t('cortex.recommend.recommendBtn')}
                  </button>
                </div>
                {relatedResults.length > 0 && (
                  <div className="space-y-2">
                    {relatedResults.map((entry, i) => (
                      <div key={entry.id || i} className="flex items-start gap-2 lg:gap-3 rounded-xl border border-border bg-background p-3 lg:p-4 hover:border-primary/30 transition-colors">
                        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary">
                          {i + 1}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-xs lg:text-sm font-medium">{entry.title}</p>
                          {entry.snippet && <p className="text-xs text-muted-foreground mt-1">{entry.snippet}</p>}
                        </div>
                        {entry.score !== undefined && (
                          <span className="text-xs font-medium text-primary">{(entry.score * 100).toFixed(0)}%</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {relatedResults.length === 0 && !recommendLoading && (
                  <p className="text-xs lg:text-sm text-muted-foreground text-center py-8">
                    {t('cortex.recommend.relatedEmpty')}
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Quality Tab ────────────────────────────── */}
        {tab === "quality" && (
          <div className="space-y-3 lg:space-y-6">
            {/* Actions */}
            <div className="flex gap-2">
              <button
                onClick={fetchQualityReport}
                disabled={qualityLoading}
                className="flex items-center gap-2 rounded-lg bg-primary px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                {qualityLoading ? <Loader2 size={15} className="animate-spin" /> : <BarChart3 size={15} />}
                {qualityLoading ? t('cortex.loading') : t('cortex.text59')}
              </button>
              <button
                onClick={fetchBatchScores}
                disabled={batchLoading}
                className="flex items-center gap-2 rounded-lg border border-border px-2 lg:px-4 py-2 text-xs lg:text-sm text-muted-foreground hover:text-foreground transition-colors"
              >
                {batchLoading ? <Loader2 size={15} className="animate-spin" /> : <Award size={15} />}
                {t('cortex.quality.batchScore')}
              </button>
            </div>

            {/* Single Score */}
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <h3 className="mb-3 flex items-center gap-2 text-xs lg:text-sm font-medium">
                <Search size={15} className="text-primary" />
                {t('cortex.quality.singleScore')}
              </h3>
              <div className="flex gap-2">
                <input
                  value={singleId}
                  onChange={(e) => setSingleId(e.target.value)}
                  placeholder={t('cortex.text58')}
                  className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
                <button
                  onClick={handleSingleScore}
                  disabled={singleLoading || !singleId.trim()}
                  className="flex items-center gap-2 rounded-lg bg-primary px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {singleLoading ? <Loader2 size={15} className="animate-spin" /> : <Award size={15} />}
                  {t('cortex.quality.scoreBtn')}
                </button>
              </div>
              {singleScore && (
                <div className="mt-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <p className="text-xs lg:text-sm font-medium">{singleScore.title}</p>
                    <div className="flex items-center gap-2">
                      <span className="text-xl lg:text-2xl font-bold text-primary">{singleScore.total_score?.toFixed(1)}</span>
                      <span className={cn("rounded-full px-2 py-0.5 text-xs font-bold", GRADE_COLORS[singleScore.grade] || "bg-muted text-foreground")}>
                        {singleScore.grade}
                      </span>
                    </div>
                  </div>
                  {singleScore.scores && (
                    <div className="space-y-2">
                      {Object.entries(singleScore.scores).map(([dimName, dimScore], i) => (
                        <div key={i} className="flex items-center gap-2 lg:gap-3">
                          <span className="w-20 text-xs text-muted-foreground">{dimName}</span>
                          <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden">
                            <div
                              className="h-full rounded-full bg-primary transition-all"
                              style={{ width: `${dimScore * 100}%` }}
                            />
                          </div>
                          <span className="w-8 text-xs font-medium text-right">{dimScore?.toFixed(2)}</span>
                          <span className="text-[10px] text-muted-foreground">
                            ({(dimScore * 100).toFixed(0)}%)
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Quality Report */}
            {qualityReport && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <h3 className="mb-4 flex items-center gap-2 text-xs lg:text-sm font-medium">
                  <BarChart3 size={15} className="text-primary" />
                  {t('cortex.quality.report')}
                </h3>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-2 lg:gap-4 mb-4">
                  <div className="text-center rounded-lg bg-background p-3">
                    <p className="text-xl lg:text-2xl font-bold text-primary">{qualityReport.total}</p>
                    <p className="text-xs text-muted-foreground">{t('cortex.text60')}</p>
                  </div>
                  <div className="text-center rounded-lg bg-background p-3">
                    <p className="text-xl lg:text-2xl font-bold text-primary">{qualityReport.avg_score?.toFixed(1)}</p>
                    <p className="text-xs text-muted-foreground">{t('cortex.text61')}</p>
                  </div>
                  <div className="text-center rounded-lg bg-background p-3">
                    <p className="text-xl lg:text-2xl font-bold text-primary">
                      {Object.entries(qualityReport.distribution || {}).sort((a, b) => b[1] - a[1])[0]?.[0] || "-"}
                    </p>
                    <p className="text-xs text-muted-foreground">{t('cortex.text62')}</p>
                  </div>
                </div>

                {/* Distribution */}
                {qualityReport.distribution && Object.keys(qualityReport.distribution).length > 0 && (
                  <div className="mb-4">
                    <p className="text-xs font-medium text-muted-foreground mb-2">{t('cortex.text63')}</p>
                    <div className="flex gap-2">
                      {Object.entries(qualityReport.distribution).sort().map(([grade, count]) => (
                        <div key={grade} className="flex-1 text-center rounded-lg bg-background p-2">
                          <span className={cn("inline-block rounded px-1.5 py-0.5 text-xs font-bold mb-1", GRADE_COLORS[grade] || "bg-muted text-foreground")}>
                            {grade}
                          </span>
                          <p className="text-xs lg:text-sm font-medium">{count}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Dimension Averages */}
                {qualityReport.dimension_averages && Object.keys(qualityReport.dimension_averages).length > 0 && (
                  <div className="mb-4">
                    <p className="text-xs font-medium text-muted-foreground mb-2">{t('cortex.text64')}</p>
                    <div className="space-y-2">
                      {Object.entries(qualityReport.dimension_averages).map(([dim, avg]) => (
                        <div key={dim} className="flex items-center gap-2 lg:gap-3">
                          <span className="w-20 text-xs text-muted-foreground">{dim}</span>
                          <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden">
                            <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${(avg / 10) * 100}%` }} />
                          </div>
                          <span className="w-8 text-xs font-medium text-right">{avg?.toFixed(1)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Top / Bottom */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2 lg:gap-4">
                  {qualityReport.top_5 && qualityReport.top_5.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-emerald-500 mb-2">🏆 {t('cortex.quality.topQuality')}</p>
                      {qualityReport.top_5.map((item, i) => (
                        <div key={i} className="flex items-center gap-2 text-xs py-1.5">
                          <span className="font-medium flex-1">{item.title}</span>
                          <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-bold", GRADE_COLORS[item.grade])}>{item.grade}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {qualityReport.bottom_5 && qualityReport.bottom_5.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-red-500 mb-2">⚠️ {t('cortex.quality.needsImprovement')}</p>
                      {qualityReport.bottom_5.map((item, i) => (
                        <div key={i} className="flex items-center gap-2 text-xs py-1.5">
                          <span className="font-medium flex-1">{item.title}</span>
                          <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-bold", GRADE_COLORS[item.grade])}>{item.grade}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Batch Scores */}
            {qualityScores.length > 0 && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <h3 className="mb-4 flex items-center gap-2 text-xs lg:text-sm font-medium">
                  <Award size={15} className="text-primary" />
                  {t('cortex.quality.batchResult')} · {qualityScores.length} {t('cortex.quality.entries')}
                </h3>
                <div className="space-y-2">
                  {qualityScores.map((item, i) => (
                    <div key={i} className="flex items-center gap-2 lg:gap-3 rounded-xl border border-border bg-background p-3 lg:p-4">
                      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary">
                        {i + 1}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs lg:text-sm font-medium">{item.title}</p>
                        {item.scores && (
                          <div className="flex gap-2 mt-1">
                            {Object.entries(item.scores).slice(0, 3).map(([dimName, dimScore], j) => (
                              <span key={j} className="text-[10px] text-muted-foreground">
                                {dimName}: {(dimScore as number)?.toFixed(2)}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-xs lg:text-sm font-bold text-primary">{item.total_score?.toFixed(1)}</span>
                        <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-bold", GRADE_COLORS[item.grade] || "bg-muted text-foreground")}>
                          {item.grade}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
