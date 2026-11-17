"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl, getToken } from "@/lib/api-client";
import {
  Brain, RefreshCw, CheckCircle, XCircle, Loader2,
  Zap, Shield, GitBranch, BarChart3, Layers,
  Activity, Clock, Cpu, AlertTriangle, ChevronDown,
  ChevronUp, Play, Minimize2, Search, Settings,
  Gauge, Target, Users, ListTodo,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface LayerStatus {
  status: string;
  [key: string]: any;
}

interface EngineStatus {
  layers: {
    prompt: LayerStatus;
    context: LayerStatus;
    harness: LayerStatus;
    loop: LayerStatus;
    graph: LayerStatus;
  };
  total_tasks: number;
  success_rate: number;
  avg_response_time: string;
}

interface ContextState {
  total_tokens: number;
  used_tokens: number;
  usage_percent: number;
  layers: Record<string, { tokens: number; percent: number }>;
  compression_needed: boolean;
}

interface HarnessRoute {
  tool: string;
  mode: string;
  permissions: string;
  guardrails: string[];
}

interface GraphStatus {
  active_groups: number;
  total_agents: number;
  running_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  roles: Record<string, { count: number; busy: number }>;
}

function StatusBadge({ online }: { online: boolean }) {
  return online ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-500">
      <CheckCircle size={10} /> Active
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-500">
      <XCircle size={10} /> Inactive
    </span>
  );
}

function LayerCard({
  name,
  icon: Icon,
  layer,
}: {
  name: string;
  icon: React.ElementType;
  layer: LayerStatus;
}) {
  const isActive = layer.status === "active";
  const entries = Object.entries(layer).filter(([k]) => k !== "status");
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Icon size={16} className="text-primary" />
          <span className="text-xs lg:text-sm font-medium">{name}</span>
        </div>
        <StatusBadge online={isActive} />
      </div>
      <div className="space-y-1.5">
        {entries.map(([key, val]) => (
          <div key={key} className="flex justify-between text-xs">
            <span className="text-muted-foreground capitalize">{key.replace(/_/g, " ")}</span>
            <span className="font-mono">{typeof val === "number" ? val.toLocaleString() : String(val)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function TokenBar({ label, tokens, percent, color }: { label: string; tokens: number; percent: number; color: string }) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-mono">{tokens.toLocaleString()} tokens ({percent}%)</span>
      </div>
      <div className="h-2 rounded-full bg-muted overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${Math.min(percent, 100)}%` }} />
      </div>
    </div>
  );
}

export function AiEngineClient() {
  const { t } = useTranslation();
  const [engineStatus, setEngineStatus] = useState<EngineStatus | null>(null);
  const [contextState, setContextState] = useState<ContextState | null>(null);
  const [harnessRoutes, setHarnessRoutes] = useState<HarnessRoute[]>([]);
  const [graphStatus, setGraphStatus] = useState<GraphStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [showRoutes, setShowRoutes] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeInput, setAnalyzeInput] = useState("");
  const [analyzeResult, setAnalyzeResult] = useState<any>(null);
  const [compressing, setCompressing] = useState(false);

  const apiBase = getApiBaseUrl();
  const headers = { Authorization: `Bearer ${getToken()}` };

  const fetchAll = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const [statusRes, contextRes, routesRes, graphRes] = await Promise.all([
        fetch(`${apiBase}/api/ai-engine/status`, { headers }),
        fetch(`${apiBase}/api/ai-engine/context`, { headers }),
        fetch(`${apiBase}/api/ai-engine/harness/routes`, { headers }),
        fetch(`${apiBase}/api/ai-engine/graph/status`, { headers }),
      ]);
      if (statusRes.ok) setEngineStatus(await statusRes.json());
      if (contextRes.ok) setContextState(await contextRes.json());
      if (routesRes.ok) setHarnessRoutes(await routesRes.json());
      if (graphRes.ok) setGraphStatus(await graphRes.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => fetchAll(true), 15000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchAll]);

  const handleAnalyze = async () => {
    if (!analyzeInput.trim()) return;
    setAnalyzing(true);
    setAnalyzeResult(null);
    try {
      const res = await fetch(`${apiBase}/api/ai-engine/analyze`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ task: analyzeInput }),
      });
      if (res.ok) setAnalyzeResult(await res.json());
      else setError(`Analyze failed: ${res.status}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analyze failed");
    } finally {
      setAnalyzing(false);
    }
  };

  const handleCompress = async () => {
    setCompressing(true);
    try {
      const res = await fetch(`${apiBase}/api/ai-engine/context/compress`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (res.ok) {
        const data = await res.json();
        setAnalyzeResult(data);
        fetchAll(true);
      } else setError(`Compress failed: ${res.status}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Compress failed");
    } finally {
      setCompressing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="animate-spin text-muted-foreground" size={24} />
      </div>
    );
  }

  const successPct = engineStatus ? Math.round(engineStatus.success_rate * 100) : 0;

  return (
      <PageLayout title="AI 引擎">
        
    <div className="h-full overflow-y-auto p-3 lg:p-6 space-y-3 lg:space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Brain size={24} className="text-primary" />
          <div>
            <h1 className="text-lg lg:text-xl font-semibold">{t("aiEngine.title", "AI Engine")}</h1>
            <p className="text-xs text-muted-foreground">{t("aiEngine.subtitle", "Prompt · Context · Harness · Loop · Graph")}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={cn("rounded-lg px-3 py-1.5 text-xs border", autoRefresh ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground")}
          >
            <Clock size={12} className="inline mr-1" />{autoRefresh ? "自动" : "手动"}
          </button>
          <button onClick={() => fetchAll()} className="rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
            <RefreshCw size={12} />
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-xs text-red-500 flex items-center gap-2">
          <AlertTriangle size={14} />{error}
        </div>
      )}

      {/* Overview Cards */}
      <div className="grid grid-cols-2 gap-2 lg:gap-4 sm:grid-cols-4">
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs text-muted-foreground mb-1">{t("aiEngine.totalTasks", "Total Tasks")}</div>
          <div className="text-xl lg:text-2xl font-bold">{engineStatus?.total_tasks ?? 0}</div>
        </div>
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs text-muted-foreground mb-1">{t("aiEngine.successRate", "Success Rate")}</div>
          <div className={cn("text-xl lg:text-2xl font-bold", successPct >= 90 ? "text-emerald-500" : successPct >= 70 ? "text-yellow-500" : "text-red-500")}>
            {successPct}%
          </div>
        </div>
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs text-muted-foreground mb-1">{t("aiEngine.avgResponse", "Avg Response")}</div>
          <div className="text-xl lg:text-2xl font-bold">{engineStatus?.avg_response_time ?? "—"}</div>
        </div>
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="text-xs text-muted-foreground mb-1">{t("aiEngine.agents", "Agents")}</div>
          <div className="text-xl lg:text-2xl font-bold">{graphStatus?.total_agents ?? 0}</div>
          <div className="text-[10px] text-muted-foreground">{graphStatus?.running_tasks ?? 0} 运行中</div>
        </div>
      </div>

      {/* Layers */}
      {engineStatus?.layers && (
        <div>
          <h2 className="text-xs lg:text-sm font-semibold mb-3 flex items-center gap-2"><Layers size={14} />{t("aiEngine.layers", "Engine Layers")}</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            <LayerCard name="提示" icon={Brain} layer={engineStatus.layers.prompt} />
            <LayerCard name="上下文" icon={BarChart3} layer={engineStatus.layers.context} />
            <LayerCard name="编排" icon={Shield} layer={engineStatus.layers.harness} />
            <LayerCard name="循环" icon={Activity} layer={engineStatus.layers.loop} />
            <LayerCard name="图谱" icon={GitBranch} layer={engineStatus.layers.graph} />
          </div>
        </div>
      )}

      {/* Context Usage */}
      {contextState && (
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
              <Gauge size={14} />{t("aiEngine.contextUsage", "Context Token Usage")}
              {contextState.compression_needed && (
                <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-yellow-500/10 px-2 py-0.5 text-[10px] font-medium text-yellow-500">
                  <AlertTriangle size={10} /> Compression Needed
                </span>
              )}
            </h2>
            <button
              onClick={handleCompress}
              disabled={compressing}
              className="rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
            >
              {compressing ? <Loader2 size={12} className="animate-spin" /> : <Minimize2 size={12} className="inline mr-1" />}
              {t("aiEngine.compress", "Compress")}
            </button>
          </div>
          <div className="mb-4">
            <TokenBar
              label={`Total: ${contextState.total_tokens.toLocaleString()} tokens`}
              tokens={contextState.used_tokens}
              percent={contextState.usage_percent}
              color={contextState.usage_percent > 80 ? "bg-red-500" : contextState.usage_percent > 60 ? "bg-yellow-500" : "bg-emerald-500"}
            />
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(contextState.layers).map(([name, data]) => (
              <div key={name} className="rounded-lg border border-border/50 bg-muted/30 p-3">
                <div className="text-xs font-medium capitalize mb-1">{name.replace(/_/g, " ")}</div>
                <div className="flex justify-between text-[10px] text-muted-foreground">
                  <span>{data.tokens.toLocaleString()} tokens</span>
                  <span>{data.percent}%</span>
                </div>
                <div className="mt-1 h-1.5 rounded-full bg-muted overflow-hidden">
                  <div className="h-full rounded-full bg-primary/60" style={{ width: `${data.percent}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Graph Status */}
      {graphStatus && (
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
          <h2 className="text-xs lg:text-sm font-semibold mb-3 flex items-center gap-2"><GitBranch size={14} />{t("aiEngine.graphStatus", "Graph Status")}</h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5 mb-4">
            <div className="text-center">
              <div className="text-lg font-bold">{graphStatus.active_groups}</div>
              <div className="text-[10px] text-muted-foreground">群组</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-bold">{graphStatus.total_agents}</div>
              <div className="text-[10px] text-muted-foreground">Agent</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-bold text-blue-500">{graphStatus.running_tasks}</div>
              <div className="text-[10px] text-muted-foreground">运行中</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-bold text-emerald-500">{graphStatus.completed_tasks}</div>
              <div className="text-[10px] text-muted-foreground">已完成</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-bold text-red-500">{graphStatus.failed_tasks}</div>
              <div className="text-[10px] text-muted-foreground">失败</div>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {Object.entries(graphStatus.roles).map(([role, data]) => (
              <div key={role} className="flex items-center justify-between rounded-lg border border-border/50 bg-muted/30 p-3">
                <div className="flex items-center gap-2">
                  {role === "advisor" ? <Brain size={14} /> : role === "executor" ? <Zap size={14} /> : <Shield size={14} />}
                  <span className="text-xs font-medium capitalize">{role}</span>
                </div>
                <div className="text-xs text-muted-foreground">
                  <span className="font-mono">{data.count}</span> 共 · <span className="font-mono text-yellow-500">{data.busy}</span> 忙碌
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Harness Routes */}
      <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
        <button
          onClick={() => setShowRoutes(!showRoutes)}
          className="flex items-center justify-between w-full"
        >
          <h2 className="text-xs lg:text-sm font-semibold flex items-center gap-2"><Settings size={14} />{t("aiEngine.harnessRoutes", "Harness Tool Routes")} ({harnessRoutes.length})</h2>
          {showRoutes ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
        {showRoutes && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="pb-2 text-left font-medium text-muted-foreground">工具</th>
                  <th className="pb-2 text-left font-medium text-muted-foreground">模式</th>
                  <th className="pb-2 text-left font-medium text-muted-foreground">权限</th>
                  <th className="pb-2 text-left font-medium text-muted-foreground">护栏</th>
                </tr>
              </thead>
              <tbody>
                {harnessRoutes.map((route, i) => (
                  <tr key={i} className="border-b border-border/50">
                    <td className="py-2 font-mono">{route.tool}</td>
                    <td className="py-2">
                      <span className={cn("rounded px-1.5 py-0.5 text-[10px]",
                        route.mode === "RAP" ? "bg-emerald-500/10 text-emerald-500" :
                        route.mode === "RGW" ? "bg-blue-500/10 text-blue-500" :
                        "bg-orange-500/10 text-orange-500"
                      )}>{route.mode}</span>
                    </td>
                    <td className="py-2 text-muted-foreground">{route.permissions}</td>
                    <td className="py-2">
                      <div className="flex flex-wrap gap-1">
                        {route.guardrails.map((g, j) => (
                          <span key={j} className="rounded bg-muted px-1.5 py-0.5 text-[10px]">{g}</span>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Analyze Task */}
      <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
        <h2 className="text-xs lg:text-sm font-semibold mb-3 flex items-center gap-2"><Search size={14} />{t("aiEngine.analyzeTask", "Analyze Task")}</h2>
        <div className="flex gap-2">
          <input
            value={analyzeInput}
            onChange={(e) => setAnalyzeInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAnalyze()}
            placeholder={t("aiEngine.analyzePlaceholder", "Describe a task to analyze...")}
            className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm"
          />
          <button
            onClick={handleAnalyze}
            disabled={analyzing || !analyzeInput.trim()}
            className="rounded-lg bg-primary px-4 py-2 text-xs lg:text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {analyzing ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          </button>
        </div>
        {analyzeResult && (
          <pre className="mt-3 rounded-lg bg-muted p-3 text-xs overflow-x-auto max-h-48 overflow-y-auto">
            {JSON.stringify(analyzeResult, null, 2)}
          </pre>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
