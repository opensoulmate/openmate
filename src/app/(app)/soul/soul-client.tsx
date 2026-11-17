"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  Brain, RefreshCw, Activity, Settings, BookOpen, Network,
  Server, CheckCircle, XCircle, AlertTriangle, Cpu, Zap,
  Monitor, Database, Plug, Bot, Gauge, BarChart3,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface HealthCheck {
  status: string;
  service?: string;
  message?: string;
  latency_ms?: number;
  [key: string]: unknown;
}

interface HealthAll {
  [key: string]: HealthCheck;
}

type TabId = "dashboard" | "knowledge" | "graph" | "agent" | "llm" | "settings";

export default function SoulClient() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabId>("dashboard");
  const apiBase = getApiBaseUrl();

  // Dashboard state
  const [vitalHealth, setVitalHealth] = useState<HealthCheck | null>(null);
  const [healthAll, setHealthAll] = useState<HealthAll | null>(null);
  const [loading, setLoading] = useState(false);

  // Knowledge state
  const [knowledgeHealth, setKnowledgeHealth] = useState<HealthCheck | null>(null);
  const [knowledgeItems, setKnowledgeItems] = useState<any[]>([]);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);

  // Graph state
  const [graphHealth, setGraphHealth] = useState<HealthCheck | null>(null);
  const [graphStats, setGraphStats] = useState<any>(null);
  const [graphLoading, setGraphLoading] = useState(false);

  // Agent state
  const [agentHealth, setAgentHealth] = useState<HealthCheck | null>(null);
  const [agentList, setAgentList] = useState<any[]>([]);
  const [agentLoading, setAgentLoading] = useState(false);

  // LLM state
  const [llmHealth, setLlmHealth] = useState<HealthCheck | null>(null);
  const [llmConfig, setLlmConfig] = useState<any>(null);
  const [llmLoading, setLlmLoading] = useState(false);

  // Settings state
  const [settings, setSettings] = useState<any>(null);
  const [settingsLoading, setSettingsLoading] = useState(false);

  const fetchDashboard = useCallback(async () => {
    setLoading(true);
    try {
      const [vitalRes, allRes] = await Promise.all([
        fetch(`${apiBase}/api/vital/health`).then(r => r.json()).catch(() => null),
        fetch(`${apiBase}/api/health/all`).then(r => r.json()).catch(() => null),
      ]);
      setVitalHealth(vitalRes);
      setHealthAll(allRes);
    } catch {} finally { setLoading(false); }
  }, [apiBase]);

  const fetchKnowledge = useCallback(async () => {
    setKnowledgeLoading(true);
    try {
      const [healthRes, itemsRes] = await Promise.all([
        fetch(`${apiBase}/api/knowledge/health`).then(r => r.json()).catch(() => null),
        fetch(`${apiBase}/api/knowledge/`).then(r => r.json()).catch(() => []),
      ]);
      setKnowledgeHealth(healthRes);
      setKnowledgeItems(Array.isArray(itemsRes) ? itemsRes : itemsRes?.items || []);
    } catch {} finally { setKnowledgeLoading(false); }
  }, [apiBase]);

  const fetchGraph = useCallback(async () => {
    setGraphLoading(true);
    try {
      const [healthRes, statsRes] = await Promise.all([
        fetch(`${apiBase}/api/graph/health`).then(r => r.json()).catch(() => null),
        fetch(`${apiBase}/api/graph/stats`).then(r => r.json()).catch(() => null),
      ]);
      setGraphHealth(healthRes);
      setGraphStats(statsRes);
    } catch {} finally { setGraphLoading(false); }
  }, [apiBase]);

  const fetchAgent = useCallback(async () => {
    setAgentLoading(true);
    try {
      const [healthRes, listRes] = await Promise.all([
        fetch(`${apiBase}/api/agent/health`).then(r => r.json()).catch(() => null),
        fetch(`${apiBase}/api/agent/`).then(r => r.json()).catch(() => []),
      ]);
      setAgentHealth(healthRes);
      setAgentList(Array.isArray(listRes) ? listRes : listRes?.agents || []);
    } catch {} finally { setAgentLoading(false); }
  }, [apiBase]);

  const fetchLlm = useCallback(async () => {
    setLlmLoading(true);
    try {
      const [healthRes, configRes] = await Promise.all([
        fetch(`${apiBase}/api/llm/health`).then(r => r.json()).catch(() => null),
        fetch(`${apiBase}/api/llm/config`).then(r => r.json()).catch(() => null),
      ]);
      setLlmHealth(healthRes);
      setLlmConfig(configRes);
    } catch {} finally { setLlmLoading(false); }
  }, [apiBase]);

  const fetchSettings = useCallback(async () => {
    setSettingsLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/vital/metrics`).then(r => r.json()).catch(() => null);
      setSettings(res);
    } catch {} finally { setSettingsLoading(false); }
  }, [apiBase]);

  useEffect(() => { fetchDashboard(); }, [fetchDashboard]);

  useEffect(() => {
    if (activeTab === "knowledge") fetchKnowledge();
    if (activeTab === "graph") fetchGraph();
    if (activeTab === "agent") fetchAgent();
    if (activeTab === "llm") fetchLlm();
    if (activeTab === "settings") fetchSettings();
  }, [activeTab, fetchKnowledge, fetchGraph, fetchAgent, fetchLlm, fetchSettings]);

  const getStatusColor = (status: string) => {
    if (status === "ok" || status === "healthy" || status === "running") return "text-emerald-500";
    if (status === "degraded" || status === "warning") return "text-amber-500";
    return "text-red-500";
  };

  const getStatusIcon = (status: string) => {
    if (status === "ok" || status === "healthy" || status === "running") return <CheckCircle size={14} className="text-emerald-500" />;
    if (status === "degraded" || status === "warning") return <AlertTriangle size={14} className="text-amber-500" />;
    return <XCircle size={14} className="text-red-500" />;
  };

  const tabs: { id: TabId; label: string; icon: React.ReactNode }[] = [
    { id: "dashboard", label: t("soul.tabDashboard"), icon: <Monitor size={14} /> },
    { id: "knowledge", label: t("soul.tabKnowledge"), icon: <BookOpen size={14} /> },
    { id: "graph", label: t("soul.tabGraph"), icon: <Network size={14} /> },
    { id: "agent", label: t("soul.tabAgent"), icon: <Bot size={14} /> },
    { id: "llm", label: t("soul.tabLlm"), icon: <Cpu size={14} /> },
    { id: "settings", label: t("soul.tabSettings"), icon: <Settings size={14} /> },
  ];

  return (
      <PageLayout title="灵魂">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-3 lg:py-4 gap-2">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <Brain size={18} className="text-violet-500 shrink-0" />
          <h1 className="text-sm lg:text-lg font-semibold truncate">{t("soul.title")}</h1>
          <span className="rounded-full bg-violet-500/10 px-1.5 lg:px-2 py-0.5 text-[10px] lg:text-xs font-medium text-violet-500 shrink-0">
            {t("soul.badge")}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={() => { fetchDashboard(); }}
            className="flex items-center gap-1.5 rounded-lg border border-border px-2 lg:px-3 py-1.5 text-xs lg:text-sm hover:bg-muted touch-manipulation">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex items-center gap-1 border-b border-border px-3 lg:px-6 py-2 overflow-x-auto">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs lg:text-sm transition-colors whitespace-nowrap",
              activeTab === tab.id
                ? "bg-violet-500/10 text-violet-600 font-medium"
                : "text-muted-foreground hover:bg-muted"
            )}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-3 lg:p-6 space-y-3 lg:space-y-6">

        {/* ── Dashboard Tab ───────────────────────────────────────── */}
        {activeTab === "dashboard" && (
          <>
            {/* Health Overview Cards */}
            {healthAll && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
                {Object.entries(healthAll).map(([key, check]) => (
                  <div key={key} className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs text-muted-foreground capitalize">{key}</span>
                      {getStatusIcon(check.status)}
                    </div>
                    <p className={cn("text-xs lg:text-sm font-semibold", getStatusColor(check.status))}>
                      {check.status}
                    </p>
                    {check.latency_ms !== undefined && (
                      <span className="text-[10px] text-muted-foreground">{check.latency_ms}ms</span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Vital Health Detail */}
            {vitalHealth && (
              <div className="rounded-xl border border-border bg-card p-5 space-y-3">
                <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                  <Activity size={14} className="text-violet-500" />
                  {t("soul.vitalHealth")}
                </h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-3 text-xs">
                  <div>
                    <span className="text-muted-foreground">{t("soul.status")}:</span>{" "}
                    <span className={cn("font-medium", getStatusColor(vitalHealth.status))}>
                      {vitalHealth.status}
                    </span>
                  </div>
                  {vitalHealth.message && (
                    <div className="col-span-2">
                      <span className="text-muted-foreground">{t("soul.message")}:</span>{" "}
                      <span>{vitalHealth.message}</span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Service Grid */}
            {healthAll && (
              <div className="rounded-xl border border-border bg-card p-5 space-y-4">
                <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                  <Server size={14} className="text-violet-500" />
                  {t("soul.serviceStatus")}
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2 lg:gap-3">
                  {Object.entries(healthAll).map(([key, check]) => (
                    <div key={key} className="flex items-center justify-between rounded-lg border border-border p-3">
                      <div className="flex items-center gap-2 lg:gap-3">
                        <div className={cn("rounded-lg p-2",
                          check.status === "ok" || check.status === "healthy" ? "bg-emerald-500/10" :
                          check.status === "degraded" ? "bg-amber-500/10" : "bg-red-500/10")}>
                          {getStatusIcon(check.status)}
                        </div>
                        <div>
                          <p className="text-xs lg:text-sm font-medium capitalize">{key}</p>
                          {check.service && <p className="text-xs text-muted-foreground">{check.service}</p>}
                        </div>
                      </div>
                      <div className="text-right">
                        <span className={cn("text-xs font-medium", getStatusColor(check.status))}>
                          {check.status}
                        </span>
                        {check.latency_ms !== undefined && (
                          <p className="text-[10px] text-muted-foreground">{check.latency_ms}ms</p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {loading && (
              <div className="flex items-center justify-center py-16 text-muted-foreground text-xs lg:text-sm">
                {t("soul.loading")}
              </div>
            )}
          </>
        )}

        {/* ── Knowledge Tab ───────────────────────────────────────── */}
        {activeTab === "knowledge" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                <BookOpen size={16} className="text-violet-500" />
                {t("soul.knowledgeTitle")}
              </h2>
              <button onClick={fetchKnowledge}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                <RefreshCw size={12} /> {t("soul.refresh")}
              </button>
            </div>

            {/* Knowledge Health */}
            {knowledgeHealth && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className="flex items-center gap-2 mb-1">
                  {getStatusIcon(knowledgeHealth.status)}
                  <span className="text-xs lg:text-sm font-medium">{t("soul.knowledgeHealth")}</span>
                  <span className={cn("text-xs font-medium", getStatusColor(knowledgeHealth.status))}>
                    {knowledgeHealth.status}
                  </span>
                </div>
                {knowledgeHealth.message && (
                  <p className="text-xs text-muted-foreground">{knowledgeHealth.message}</p>
                )}
              </div>
            )}

            {/* Knowledge Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <span className="text-xs text-muted-foreground">{t("soul.totalEntries")}</span>
                <p className="text-xl lg:text-2xl font-bold">{knowledgeItems.length}</p>
              </div>
            </div>

            {/* Knowledge List */}
            {knowledgeLoading ? (
              <div className="flex items-center justify-center py-16 text-muted-foreground text-xs lg:text-sm">{t("soul.loading")}</div>
            ) : knowledgeItems.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Database size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("soul.noKnowledge")}</p>
              </div>
            ) : (
              <div className="rounded-xl border border-border overflow-x-auto">
                <table className="w-full text-xs lg:text-sm min-w-[480px]">
                  <thead>
                    <tr className="border-b border-border bg-muted/50">
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">{t("soul.kbTitle")}</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">{t("soul.kbDomain")}</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">{t("soul.kbTags")}</th>
                      <th className="px-2 lg:px-4 py-2 text-left text-xs font-medium text-muted-foreground">{t("soul.kbCreated")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {knowledgeItems.slice(0, 50).map((item: any, idx: number) => (
                      <tr key={item.id || idx} className="border-b border-border last:border-0 hover:bg-muted/30">
                        <td className="px-2 lg:px-4 py-2 font-medium">{item.title || "-"}</td>
                        <td className="px-2 lg:px-4 py-2 text-muted-foreground">{item.domain || "-"}</td>
                        <td className="px-2 lg:px-4 py-2">
                          <div className="flex flex-wrap gap-1">
                            {(item.tags || []).map((tag: string) => (
                              <span key={tag} className="rounded-full bg-violet-500/10 px-2 py-0.5 text-[10px] text-violet-600">{tag}</span>
                            ))}
                          </div>
                        </td>
                        <td className="px-2 lg:px-4 py-2 text-xs text-muted-foreground">
                          {item.created_at ? new Date(item.created_at).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' , fractionalSecondDigits: 3 }) : "-"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* ── Graph Tab ───────────────────────────────────────────── */}
        {activeTab === "graph" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                <Network size={16} className="text-violet-500" />
                {t("soul.graphTitle")}
              </h2>
              <button onClick={fetchGraph}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                <RefreshCw size={12} /> {t("soul.refresh")}
              </button>
            </div>

            {/* Graph Health */}
            {graphHealth && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className="flex items-center gap-2 mb-1">
                  {getStatusIcon(graphHealth.status)}
                  <span className="text-xs lg:text-sm font-medium">{t("soul.graphHealth")}</span>
                  <span className={cn("text-xs font-medium", getStatusColor(graphHealth.status))}>
                    {graphHealth.status}
                  </span>
                </div>
              </div>
            )}

            {/* Graph Stats */}
            {graphStats && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("soul.graphNodes")}</span>
                  <p className="text-xl lg:text-2xl font-bold">{graphStats.entities ?? graphStats.nodes ?? 0}</p>
                </div>
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("soul.graphEdges")}</span>
                  <p className="text-xl lg:text-2xl font-bold">{graphStats.relations ?? graphStats.edges ?? 0}</p>
                </div>
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("soul.graphTypes")}</span>
                  <p className="text-xl lg:text-2xl font-bold">{graphStats.types ?? graphStats.entity_types ?? 0}</p>
                </div>
              </div>
            )}

            {graphLoading && (
              <div className="flex items-center justify-center py-16 text-muted-foreground text-xs lg:text-sm">{t("soul.loading")}</div>
            )}
          </div>
        )}

        {/* ── Agent Tab ───────────────────────────────────────────── */}
        {activeTab === "agent" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                <Bot size={16} className="text-violet-500" />
                {t("soul.agentTitle")}
              </h2>
              <button onClick={fetchAgent}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                <RefreshCw size={12} /> {t("soul.refresh")}
              </button>
            </div>

            {/* Agent Health */}
            {agentHealth && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className="flex items-center gap-2 mb-1">
                  {getStatusIcon(agentHealth.status)}
                  <span className="text-xs lg:text-sm font-medium">{t("soul.agentHealth")}</span>
                  <span className={cn("text-xs font-medium", getStatusColor(agentHealth.status))}>
                    {agentHealth.status}
                  </span>
                </div>
              </div>
            )}

            {/* Agent List */}
            {agentLoading ? (
              <div className="flex items-center justify-center py-16 text-muted-foreground text-xs lg:text-sm">{t("soul.loading")}</div>
            ) : agentList.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Plug size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("soul.noAgents")}</p>
              </div>
            ) : (
              <div className="space-y-3">
                {agentList.map((agent: any, idx: number) => (
                  <div key={agent.id || agent.agent_id || idx} className="rounded-xl border border-border bg-card p-3 lg:p-4">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2 lg:gap-3">
                        <div className="rounded-lg bg-violet-500/10 p-2">
                          <Bot size={14} className="text-violet-500" />
                        </div>
                        <div>
                          <p className="font-medium text-xs lg:text-sm">{agent.name || agent.agent_id || `Agent ${idx + 1}`}</p>
                          <p className="text-xs text-muted-foreground font-mono">{agent.type || agent.agent_type || "-"}</p>
                        </div>
                      </div>
                      <span className={cn("text-xs font-medium px-2 py-0.5 rounded-full",
                        agent.status === "online" || agent.status === "running" ? "bg-emerald-500/10 text-emerald-500" :
                        agent.status === "error" ? "bg-red-500/10 text-red-500" :
                        "bg-muted-foreground/10 text-muted-foreground")}>
                        {agent.status || "unknown"}
                      </span>
                    </div>
                    {agent.capabilities && (
                      <div className="flex flex-wrap gap-1 mt-2">
                        {(Array.isArray(agent.capabilities) ? agent.capabilities : []).map((cap: string) => (
                          <span key={cap} className="rounded-full bg-violet-500/10 px-2 py-0.5 text-[10px] text-violet-600">{cap}</span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── LLM Tab ─────────────────────────────────────────────── */}
        {activeTab === "llm" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                <Cpu size={16} className="text-violet-500" />
                {t("soul.llmTitle")}
              </h2>
              <button onClick={fetchLlm}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                <RefreshCw size={12} /> {t("soul.refresh")}
              </button>
            </div>

            {/* LLM Health */}
            {llmHealth && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className="flex items-center gap-2 mb-1">
                  {getStatusIcon(llmHealth.status)}
                  <span className="text-xs lg:text-sm font-medium">{t("soul.llmHealth")}</span>
                  <span className={cn("text-xs font-medium", getStatusColor(llmHealth.status))}>
                    {llmHealth.status}
                  </span>
                </div>
                {llmHealth.message && (
                  <p className="text-xs text-muted-foreground">{llmHealth.message}</p>
                )}
              </div>
            )}

            {/* LLM Config */}
            {llmConfig && (
              <div className="rounded-xl border border-border bg-card p-5 space-y-4">
                <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                  <Settings size={14} className="text-violet-500" />
                  {t("soul.llmConfig")}
                </h3>
                <div className="grid grid-cols-2 gap-2 lg:gap-3 text-xs">
                  {llmConfig.provider && (
                    <div>
                      <span className="text-muted-foreground">{t("soul.llmProvider")}:</span>{" "}
                      <span className="font-medium">{llmConfig.provider}</span>
                    </div>
                  )}
                  {llmConfig.model && (
                    <div>
                      <span className="text-muted-foreground">{t("soul.llmModel")}:</span>{" "}
                      <span className="font-medium">{llmConfig.model}</span>
                    </div>
                  )}
                  {llmConfig.base_url && (
                    <div className="col-span-2">
                      <span className="text-muted-foreground">{t("soul.llmBaseUrl")}:</span>{" "}
                      <span className="font-mono">{llmConfig.base_url}</span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {llmLoading && (
              <div className="flex items-center justify-center py-16 text-muted-foreground text-xs lg:text-sm">{t("soul.loading")}</div>
            )}
          </div>
        )}

        {/* ── Settings Tab ────────────────────────────────────────── */}
        {activeTab === "settings" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                <Settings size={16} className="text-violet-500" />
                {t("soul.settingsTitle")}
              </h2>
              <button onClick={fetchSettings}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                <RefreshCw size={12} /> {t("soul.refresh")}
              </button>
            </div>

            {/* System Metrics */}
            {settings && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("soul.systemUptime")}</span>
                  <p className="text-lg font-bold">{settings.uptime || settings.uptime_seconds || "-"}</p>
                </div>
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("soul.systemVersion")}</span>
                  <p className="text-lg font-bold">{settings.version || "-"}</p>
                </div>
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("soul.totalRequests")}</span>
                  <p className="text-lg font-bold">{settings.total_requests ?? settings.requests ?? "-"}</p>
                </div>
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                  <span className="text-xs text-muted-foreground">{t("soul.memoryUsage")}</span>
                  <p className="text-lg font-bold">{settings.memory_usage ?? settings.memory ?? "-"}</p>
                </div>
              </div>
            )}

            {/* System Info */}
            <div className="rounded-xl border border-border bg-card p-5 space-y-4">
              <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                <Gauge size={14} className="text-violet-500" />
                {t("soul.systemInfo")}
              </h3>
              <div className="grid grid-cols-2 gap-2 lg:gap-3 text-xs">
                <div>
                  <span className="text-muted-foreground">{t("soul.apiUrl")}:</span>{" "}
                  <span className="font-mono">{apiBase}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">{t("soul.soulPort")}:</span>{" "}
                  <span className="font-mono">8090</span>
                </div>
              </div>
            </div>

            {settingsLoading && (
              <div className="flex items-center justify-center py-16 text-muted-foreground text-xs lg:text-sm">{t("soul.loading")}</div>
            )}
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
