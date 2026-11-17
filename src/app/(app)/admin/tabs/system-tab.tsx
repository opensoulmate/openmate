"use client";

import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  Shield,
  Trash2,
  Database,
  RefreshCw,
  Download,
  Activity,
  CheckCircle,
  XCircle,
  Loader2,
  Zap,
  HardDrive,
  Clock,
} from "lucide-react";

interface ActionResult {
  action: string;
  status: string;
  data?: any;
  error?: string;
  cleared?: number;
  cleaned?: number;
  total?: number;
}

interface SystemOverview {
  timestamp: number;
  health: {
    status: string;
    healthy: number;
    total: number;
    organs: Record<string, string>;
  };
  stats: Record<string, any>;
}

export function SystemTab() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const [overview, setOverview] = useState<SystemOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [actions, setActions] = useState<Record<string, { loading: boolean; result: ActionResult | null }>>({});

  const fetchOverview = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/admin/overview`, { signal: AbortSignal.timeout(10000) });
      if (res.ok) {
        setOverview(await res.json());
      }
    } catch (e) {
      console.error("Failed to fetch overview", e);
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    fetchOverview();
    const interval = setInterval(fetchOverview, 30000);
    return () => clearInterval(interval);
  }, [fetchOverview]);

  const runAction = async (name: string, endpoint: string, method: string = "POST", body?: any) => {
    setActions((prev) => ({ ...prev, [name]: { loading: true, result: null } }));
    try {
      const opts: RequestInit = {
        method,
        signal: AbortSignal.timeout(30000),
        headers: body ? { "Content-Type": "application/json" } : {},
      };
      if (body) opts.body = JSON.stringify(body);
      const res = await fetch(`${apiBase}${endpoint}`, opts);
      const data = await res.json();
      setActions((prev) => ({ ...prev, [name]: { loading: false, result: data } }));
      setTimeout(fetchOverview, 1000);
    } catch (e: any) {
      setActions((prev) => ({
        ...prev,
        [name]: { loading: false, result: { action: name, status: "error", error: e.message } },
      }));
    }
  };

  const healthyOrgans = overview?.health?.healthy ?? 0;
  const totalOrgans = overview?.health?.total ?? 0;
  const allHealthy = healthyOrgans === totalOrgans;

  const veinStats = overview?.stats?.vein;
  const glandStats = overview?.stats?.gland;
  const trajectoryStats = overview?.stats?.trajectory;

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-3 lg:py-4">
        <div className="flex items-center gap-2">
          <Shield size={20} className="text-primary" />
          <h1 className="text-lg font-semibold">系统管理</h1>
        </div>
        <button
          onClick={fetchOverview}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-muted px-3 text-xs font-medium hover:bg-accent"
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 lg:p-6">
        {/* System Status */}
        <div className="mb-3 lg:mb-6 grid gap-2 lg:gap-4 sm:grid-cols-4">
          <div className="rounded-lg border border-border bg-card p-3 lg:p-4">
            <div className="flex items-center gap-2 mb-2">
              {allHealthy ? (
                <CheckCircle size={16} className="text-green-500" />
              ) : (
                <XCircle size={16} className="text-red-500" />
              )}
              <span className="text-xs lg:text-sm font-medium">系统健康</span>
            </div>
            <p className="text-xl lg:text-2xl font-bold">{healthyOrgans}/{totalOrgans}</p>
            <p className="text-xs text-muted-foreground">器官在线</p>
          </div>

          <div className="rounded-lg border border-border bg-card p-3 lg:p-4">
            <div className="flex items-center gap-2 mb-2">
              <HardDrive size={16} className="text-blue-500" />
              <span className="text-xs lg:text-sm font-medium">文件存储</span>
            </div>
            <p className="text-xl lg:text-2xl font-bold">{veinStats?.store?.total_files ?? 0}</p>
            <p className="text-xs text-muted-foreground">个文件已存储</p>
          </div>

          <div className="rounded-lg border border-border bg-card p-3 lg:p-4">
            <div className="flex items-center gap-2 mb-2">
              <Zap size={16} className="text-amber-500" />
              <span className="text-xs lg:text-sm font-medium">LLM 用量</span>
            </div>
            <p className="text-xl lg:text-2xl font-bold">{glandStats?.total_tokens?.toLocaleString() ?? 0}</p>
            <p className="text-xs text-muted-foreground">个 tokens 已用</p>
          </div>

          <div className="rounded-lg border border-border bg-card p-3 lg:p-4">
            <div className="flex items-center gap-2 mb-2">
              <Activity size={16} className="text-violet-500" />
              <span className="text-xs lg:text-sm font-medium">轨迹</span>
            </div>
            <p className="text-xl lg:text-2xl font-bold">{trajectoryStats?.total_sessions ?? 0}</p>
            <p className="text-xs text-muted-foreground">个会话已记录</p>
          </div>
        </div>

        {/* 快捷操作 */}
        <h2 className="mb-4 text-xs lg:text-sm font-semibold text-muted-foreground uppercase tracking-wider">快捷操作</h2>
        <div className="grid gap-2 lg:gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <ActionCard
            icon={<Trash2 size={18} />}
            title="清空所有缓存"
            description="Clear cached data across Vein, Reflex, and Voice components"
            color="text-red-500"
            bg="bg-red-500/10"
            loading={actions["clearCaches"]?.loading}
            result={actions["clearCaches"]?.result}
            onRun={() => runAction("clearCaches", "/api/admin/caches/clear")}
          />
          <ActionCard
            icon={<RefreshCw size={18} />}
            title="清理过期数据"
            description="Remove expired sessions, uploads, sandboxes, and cache entries"
            color="text-amber-500"
            bg="bg-amber-500/10"
            loading={actions["cleanup"]?.loading}
            result={actions["cleanup"]?.result}
            onRun={() => runAction("cleanup", "/api/admin/cleanup")}
          />
          <ActionCard
            icon={<Database size={18} />}
            title="立即备份"
            description="Create an immediate backup of knowledge bases and configuration"
            color="text-blue-500"
            bg="bg-blue-500/10"
            loading={actions["backup"]?.loading}
            result={actions["backup"]?.result}
            onRun={() => runAction("backup", "/api/admin/backup", "POST", {
              name: `backup-${new Date().toISOString().slice(0, 10)}`,
              description: "Manual backup via admin panel",
              include_knowledge: true,
              include_config: true,
              include_memories: true,
            })}
          />
          <ActionCard
            icon={<Download size={18} />}
            title="导出系统配置"
            description="Download current system configuration as JSON"
            color="text-green-500"
            bg="bg-green-500/10"
            loading={actions["exportConfig"]?.loading}
            result={actions["exportConfig"]?.result}
            onRun={async () => {
              setActions((prev) => ({ ...prev, exportConfig: { loading: true, result: null } }));
              try {
                const res = await fetch(`${apiBase}/api/admin/export/config`, { signal: AbortSignal.timeout(10000) });
                const data = await res.json();
                const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `opensoul-config-${new Date().toISOString().slice(0, 10)}.json`;
                a.click();
                URL.revokeObjectURL(url);
                setActions((prev) => ({ ...prev, exportConfig: { loading: false, result: { action: "exportConfig", status: "ok" } } }));
              } catch (e: any) {
                setActions((prev) => ({ ...prev, exportConfig: { loading: false, result: { action: "exportConfig", status: "error", error: e.message } } }));
              }
            }}
          />
          <ActionCard
            icon={<Download size={18} />}
            title="下载系统报告"
            description="Generate and download a comprehensive system report with all component statuses and stats"
            color="text-cyan-500"
            bg="bg-cyan-500/10"
            loading={actions["systemReport"]?.loading}
            result={actions["systemReport"]?.result}
            onRun={async () => {
              setActions((prev) => ({ ...prev, systemReport: { loading: true, result: null } }));
              try {
                const res = await fetch(`${apiBase}/api/admin/report`, { signal: AbortSignal.timeout(15000) });
                const data = await res.json();
                const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `opensoul-report-${new Date().toISOString().slice(0, 10)}.json`;
                a.click();
                URL.revokeObjectURL(url);
                setActions((prev) => ({ ...prev, systemReport: { loading: false, result: { action: "systemReport", status: "ok", data: { healthy: data.summary?.healthy_organs, total: data.summary?.total_organs } } } }));
              } catch (e: any) {
                setActions((prev) => ({ ...prev, systemReport: { loading: false, result: { action: "systemReport", status: "error", error: e.message } } }));
              }
            }}
          />
          <ActionCard
            icon={<Activity size={18} />}
            title="完整健康检查"
            description="Run comprehensive health check on all 25+ components"
            color="text-violet-500"
            bg="bg-violet-500/10"
            loading={actions["healthCheck"]?.loading}
            result={actions["healthCheck"]?.result}
            onRun={() => runAction("healthCheck", "/api/diagnostics/check-all", "GET")}
          />
          <ActionCard
            icon={<Clock size={18} />}
            title="刷新概览"
            description="Force refresh system overview data from all components"
            color="text-cyan-500"
            bg="bg-cyan-500/10"
            loading={false}
            result={null}
            onRun={fetchOverview}
          />
        </div>

        {/* Organ Grid */}
        {overview?.health?.organs && (
          <>
            <h2 className="mt-8 mb-4 text-xs lg:text-sm font-semibold text-muted-foreground uppercase tracking-wider">
              Organ Status ({healthyOrgans}/{totalOrgans})
            </h2>
            <div className="grid gap-2 grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8">
              {Object.entries(overview.health.organs).map(([name, status]) => (
                <div
                  key={name}
                  className={`rounded-md border px-3 py-2 text-center text-xs ${
                    status === "ok"
                      ? "border-green-500/30 bg-green-500/5 text-green-600"
                      : "border-red-500/30 bg-red-500/5 text-red-600"
                  }`}
                >
                  <div className="font-medium capitalize">{name}</div>
                  <div className="text-[10px] opacity-70">{status}</div>
                </div>
              ))}
            </div>
          </>
        )}

        {/* Component Statistics */}
        {overview?.stats && Object.keys(overview.stats).length > 0 && (
          <>
            <h2 className="mt-8 mb-4 text-xs lg:text-sm font-semibold text-muted-foreground uppercase tracking-wider">
              Component Statistics ({Object.keys(overview.stats).length} monitored)
            </h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {overview.stats.vein && (
                <StatsCard
                  title={t("admin.veinFileStorage") || "Vein File Storage"}
                  emoji="🩸"
                  items={[
                    { label: "Files", value: overview.stats.vein.store?.total_files ?? 0 },
                    { label: "Dedup Savings", value: `${((overview.stats.vein.store?.dedup_savings_bytes ?? 0) / 1024).toFixed(1)}KB` },
                    { label: "Cache Hit Rate", value: `${overview.stats.vein.cache?.hit_rate ?? 0}%` },
                    { label: "Versions", value: overview.stats.vein.store?.versioning?.total_versions ?? 0 },
                  ]}
                />
              )}
              {overview.stats.gland && (
                <StatsCard
                  title={t("admin.glandModelGateway") || "Gland Model Gateway"}
                  emoji="🧪"
                  items={[
                    { label: "Total Tokens", value: (overview.stats.gland.total_tokens ?? 0).toLocaleString() },
                    { label: "Call Count", value: overview.stats.gland.call_count ?? 0 },
                    { label: "Providers", value: (overview.stats.gland.providers ?? []).length },
                  ]}
                />
              )}
              {overview.stats.immune && (
                <StatsCard
                  title={t("admin.immuneSecurityAudit") || "Immune Security Audit"}
                  emoji="🛡"
                  items={[
                    { label: "Audit Entries", value: overview.stats.immune.audit?.total_entries ?? 0 },
                    { label: "Patterns", value: overview.stats.immune.moderator?.patterns ?? 0 },
                    { label: "Blacklist", value: overview.stats.immune.access_control?.blacklist_count ?? 0 },
                  ]}
                />
              )}
              {overview.stats.gene && (
                <StatsCard
                  title={t("admin.geneTemplateLibrary") || "Gene Template Library"}
                  emoji="🧬"
                  items={[
                    { label: "Templates", value: overview.stats.gene.total_templates ?? 0 },
                    { label: "Built-in", value: overview.stats.gene.builtin_count ?? 0 },
                    { label: "用户", value: overview.stats.gene.user_count ?? 0 },
                    { label: "Categories", value: Object.keys(overview.stats.gene.by_category ?? {}).length },
                  ]}
                />
              )}
              {overview.stats.hippo && (
                <StatsCard
                  title={t("admin.hippoMemoryMgmt") || "Hippo Memory Management"}
                  emoji="🧠"
                  items={[
                    { label: "Memories", value: overview.stats.hippo.memory?.total_memories ?? 0 },
                    { label: "活跃", value: overview.stats.hippo.memory?.active ?? 0 },
                    { label: "Sessions", value: overview.stats.hippo.sessions?.total_sessions ?? 0 },
                    { label: "Strategy", value: overview.stats.hippo.decay_config?.strategy ?? "N/A" },
                  ]}
                />
              )}
              {overview.stats.vital && (
                <StatsCard
                  title={t("admin.vitalSystemMonitor") || "Vital System Monitor"}
                  emoji="📊"
                  items={[
                    { label: "CPU", value: `${overview.stats.vital.system?.cpu_percent ?? 0}%` },
                    { label: "内存", value: `${overview.stats.vital.system?.memory_percent ?? 0}%` },
                    { label: "Disk", value: `${overview.stats.vital.system?.disk_percent ?? 0}%` },
                    { label: "Alerts", value: overview.stats.vital.alerts?.active ?? 0 },
                  ]}
                />
              )}
              {overview.stats.mind && (
                <StatsCard
                  title={t("admin.mindPersonalitySys") || "Mind Personality System"}
                  emoji="💭"
                  items={[
                    { label: "Personalities", value: overview.stats.mind.personality?.total_personalities ?? 0 },
                    { label: "活跃", value: overview.stats.mind.active_personality ?? "N/A" },
                    { label: "Emotions Analyzed", value: overview.stats.mind.emotion?.total_analyzed ?? 0 },
                  ]}
                />
              )}
              {overview.stats.vision && (
                <StatsCard
                  title={t("admin.visionVisualCenter") || "Vision Visual Center"}
                  emoji="🎨"
                  items={[
                    { label: "Generated", value: overview.stats.vision.total_generated ?? 0 },
                    { label: "Engine", value: overview.stats.vision.engine ?? "N/A" },
                    { label: "Saved Outputs", value: overview.stats.vision.saved_outputs ?? 0 },
                  ]}
                />
              )}
              {overview.stats.pipeline && (
                <StatsCard
                  title={t("admin.pipelineSmartPipeline") || "Pipeline Smart Pipeline"}
                  emoji="🔄"
                  items={[
                    { label: "Total Runs", value: overview.stats.pipeline.total_runs ?? 0 },
                    { label: "Steps Executed", value: overview.stats.pipeline.total_steps_executed ?? 0 },
                    { label: "Pipelines", value: overview.stats.pipeline.available_pipelines?.length ?? 0 },
                  ]}
                />
              )}
              {overview.stats.trajectory && (
                <StatsCard
                  title={t("admin.trajectoryTrack") || "Trajectory Track"}
                  emoji="📈"
                  items={[
                    { label: "Sessions", value: overview.stats.trajectory.total_sessions ?? 0 },
                    { label: "Events", value: overview.stats.trajectory.total_events ?? 0 },
                    { label: "Tokens", value: (overview.stats.trajectory.total_tokens ?? 0).toLocaleString() },
                  ]}
                />
              )}
              {overview.stats.reflex && (
                <StatsCard
                  title={t("admin.reflexHighSpeed") || "Reflex High-Speed"}
                  emoji="⚡"
                  items={[
                    { label: "Cache Entries", value: overview.stats.reflex.cache?.total_entries ?? 0 },
                    { label: "Hit Rate", value: `${overview.stats.reflex.cache?.hit_rate_percent ?? 0}%` },
                    { label: "Threshold", value: overview.stats.reflex.cache?.similarity_threshold ?? 0 },
                  ]}
                />
              )}
              {overview.stats.mirror && (
                <StatsCard
                  title={t("admin.mirrorSandbox") || "Mirror Sandbox"}
                  emoji="🪞"
                  items={[
                    { label: "Sandboxes", value: overview.stats.mirror.total_sandboxes ?? 0 },
                    { label: "活跃", value: overview.stats.mirror.active ?? 0 },
                  ]}
                />
              )}
              {overview.stats.echo && (
                <StatsCard
                  title={t("admin.echoMessagePush") || "Echo Message Push"}
                  emoji="🔊"
                  items={[
                    { label: "Messages", value: overview.stats.echo.total_messages ?? 0 },
                    { label: "Sent", value: overview.stats.echo.sent ?? 0 },
                    { label: "失败", value: overview.stats.echo.failed ?? 0 },
                    { label: "Channels", value: overview.stats.echo.channels_enabled ?? 0 },
                  ]}
                />
              )}
              {overview.stats.link && (
                <StatsCard
                  title={t("admin.linkBidirectional") || "Link Bidirectional"}
                  emoji="🔗"
                  items={[
                    { label: "Connectors", value: overview.stats.link.total_connectors ?? 0 },
                    { label: "活跃", value: overview.stats.link.active ?? 0 },
                    { label: "Events", value: overview.stats.link.total_events ?? 0 },
                  ]}
                />
              )}
              {overview.stats.marrow && (
                <StatsCard
                  title={t("admin.marrowDisaster") || "Marrow Disaster Recovery"}
                  emoji="🦴"
                  items={[
                    { label: "Backups", value: overview.stats.marrow.backup?.total_backups ?? 0 },
                    { label: "大小", value: `${((overview.stats.marrow.backup?.total_size_bytes ?? 0) / 1024).toFixed(1)}KB` },
                    { label: "Schedules", value: overview.stats.marrow.scheduler?.total_schedules ?? 0 },
                  ]}
                />
              )}
              {overview.stats.sense && (
                <StatsCard
                  title={t("admin.senseSensory") || "Sense Sensory"}
                  emoji="👁"
                  items={[
                    { label: "OCR", value: overview.stats.sense.engines?.ocr?.available ? "✓" : "✗" },
                    { label: "ASR", value: overview.stats.sense.engines?.asr?.available ? "✓" : "✗" },
                    { label: "Multimodal", value: overview.stats.sense.engines?.multimodal?.available ? "✓" : "✗" },
                  ]}
                />
              )}
              {overview.stats.nerve && (
                <StatsCard
                  title={t("admin.nerveNeural") || "Nerve Neural"}
                  emoji="⚡"
                  items={[
                    { label: "Events", value: overview.stats.nerve.total_events ?? 0 },
                    { label: "Nodes", value: overview.stats.nerve.total_nodes ?? 0 },
                    { label: "在线", value: overview.stats.nerve.online_nodes ?? 0 },
                    { label: "Subscriptions", value: overview.stats.nerve.total_subscriptions ?? 0 },
                  ]}
                />
              )}
              {overview.stats.will && (
                <StatsCard
                  title={t("admin.willVolition") || "Will · Volition System"}
                  emoji="✨"
                  items={[
                    { label: "Workflows", value: overview.stats.will.total_workflows ?? 0 },
                    { label: "活跃", value: overview.stats.will.active_workflows ?? 0 },
                    { label: "Executions", value: overview.stats.will.total_executions ?? 0 },
                    { label: "Success Rate", value: `${overview.stats.will.success_rate ?? 0}%` },
                  ]}
                />
              )}
              {overview.stats.limb && (
                <StatsCard
                  title={t("admin.limbLimbs") || "Limb Extremities"}
                  emoji="💪"
                  items={[
                    { label: "Tasks", value: overview.stats.limb.total_tasks ?? 0 },
                    { label: "Executed", value: overview.stats.limb.total_executed ?? 0 },
                    { label: "运行中", value: overview.stats.limb.running ?? 0 },
                    { label: "Templates", value: overview.stats.limb.templates ?? 0 },
                  ]}
                />
              )}
              {overview.stats.pulse && (
                <StatsCard
                  title={t("admin.pulseHeartbeat") || "Pulse Heartbeat"}
                  emoji="💓"
                  items={[
                    { label: "Uptime", value: `${Math.round((overview.stats.pulse.uptime_seconds ?? 0) / 60)}m` },
                    { label: "Ticks", value: overview.stats.pulse.total_ticks ?? 0 },
                    { label: "Signals", value: overview.stats.pulse.total_signals ?? 0 },
                  ]}
                />
              )}
              {overview.stats.heredity && (
                <StatsCard
                  title={t("admin.heredityGenetic") || "Heredity Genetic"}
                  emoji="🔗"
                  items={[
                    { label: "状态", value: overview.stats.heredity.status ?? "ok" },
                  ]}
                />
              )}
              {overview.stats.cortex && (
                <StatsCard
                  title={t("admin.cortexCortex") || "Cortex"}
                  emoji="🧠"
                  items={[
                    { label: "Total Calls", value: overview.stats.cortex.usage?.total_calls ?? 0 },
                    { label: "Plan Calls", value: overview.stats.cortex.usage?.plan_calls ?? 0 },
                    { label: "Agent Calls", value: overview.stats.cortex.usage?.agent_calls ?? 0 },
                    { label: "Think Calls", value: overview.stats.cortex.usage?.think_calls ?? 0 },
                  ]}
                />
              )}
              {overview.stats.voice && (
                <StatsCard
                  title={t("admin.voiceVocal") || "Voice Vocal"}
                  emoji="🎤"
                  items={[
                    { label: "Synthesized", value: overview.stats.voice.engine?.total_synthesized ?? 0 },
                    { label: "Characters", value: (overview.stats.voice.engine?.total_characters ?? 0).toLocaleString() },
                    { label: "Profiles", value: overview.stats.voice.profiles?.total_profiles ?? 0 },
                    { label: "Cache Size", value: `${((overview.stats.voice.engine?.cache?.size_bytes ?? 0) / 1024).toFixed(1)}KB` },
                  ]}
                />
              )}
              {overview.stats.nest && (
                <StatsCard
                  title={t("admin.nestMultiTenant") || "Nest Multi-Tenant"}
                  emoji="🏠"
                  items={[
                    { label: "Tenants", value: overview.stats.nest.tenants?.total_tenants ?? 0 },
                    { label: "Documents", value: overview.stats.nest.tenants?.total_documents ?? 0 },
                    { label: "Access Checks", value: overview.stats.nest.isolation?.total_access_checks ?? 0 },
                    { label: "Policies", value: overview.stats.nest.isolation?.policies_count ?? 0 },
                  ]}
                />
              )}
              {overview.stats.knowledge && (
                <StatsCard
                  title={t("admin.knowledgeBase") || "Knowledge Base"}
                  emoji="📚"
                  items={[
                    { label: "Entries", value: overview.stats.knowledge.total_entries ?? 0 },
                    { label: "Recent 24h", value: overview.stats.knowledge.recent_24h ?? 0 },
                    { label: "Top Users", value: (overview.stats.knowledge.top_users ?? []).length },
                  ]}
                />
              )}
              {overview.stats.agent && (
                <StatsCard
                  title={t("admin.agentSystem") || "Agent System"}
                  emoji="🤖"
                  items={[
                    { label: "Agents", value: overview.stats.agent.total_agents ?? 0 },
                    { label: "Types", value: Object.keys(overview.stats.agent.by_type ?? {}).length },
                  ]}
                />
              )}
              {overview.stats.graph && (
                <StatsCard
                  title={t("admin.graphKnowledgeGraph") || "Graph Knowledge Graph"}
                  emoji="🌐"
                  items={[
                    { label: "Entities", value: overview.stats.graph.total_entities ?? 0 },
                    { label: "Relations", value: overview.stats.graph.total_relations ?? 0 },
                    { label: "Types", value: Object.keys(overview.stats.graph.by_type ?? {}).length },
                  ]}
                />
              )}
              {overview.stats.entity && (
                <StatsCard
                  title={t("admin.entityEntity") || "Entity"}
                  emoji="🏷"
                  items={[
                    { label: "Entities", value: overview.stats.entity.total_entities ?? 0 },
                    { label: "Types", value: Object.keys(overview.stats.entity.by_type ?? {}).length },
                  ]}
                />
              )}
              {overview.stats.search && (
                <StatsCard
                  title={t("admin.searchSearch") || "Search"}
                  emoji="🔍"
                  items={[
                    { label: "Searchable", value: overview.stats.search.searchable_entries ?? 0 },
                    { label: "Modes", value: (overview.stats.search.modes ?? []).length },
                  ]}
                />
              )}
              {overview.stats.capture && (
                <StatsCard
                  title={t("admin.captureCollection") || "Capture Collection"}
                  emoji="📸"
                  items={[
                    { label: "Captures", value: overview.stats.capture.total_captures ?? 0 },
                    { label: "Pages", value: overview.stats.capture.page_captures ?? 0 },
                    { label: "Selections", value: overview.stats.capture.selection_captures ?? 0 },
                    { label: "Recent 24h", value: overview.stats.capture.recent_24h ?? 0 },
                  ]}
                />
              )}
              {overview.stats.workflow && (
                <StatsCard
                  title={t("admin.workflowWorkflow") || "Workflow Pipeline"}
                  emoji="⚙"
                  items={[
                    { label: "Tasks", value: overview.stats.workflow.total_tasks ?? 0 },
                    { label: "活跃", value: overview.stats.workflow.active_tasks ?? 0 },
                    { label: "Types", value: Object.keys(overview.stats.workflow.by_type ?? {}).length },
                  ]}
                />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ActionCard({
  icon,
  title,
  description,
  color,
  bg,
  loading,
  result,
  onRun,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  color: string;
  bg: string;
  loading?: boolean;
  result: ActionResult | null;
  onRun: () => void;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-3 lg:p-4">
      <div className="flex items-start gap-3">
        <div className={`flex h-9 w-9 items-center justify-center rounded-md ${bg} ${color}`}>
          {icon}
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-xs lg:text-sm font-medium">{title}</h3>
          <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-2">
        <button
          onClick={onRun}
          disabled={loading}
          className="inline-flex h-7 items-center gap-1.5 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {loading ? <Loader2 size={12} className="animate-spin" /> : null}
          {loading ? "Running..." : "Execute"}
        </button>

        {result && (
          <span className={`text-xs ${result.status === "ok" ? "text-green-500" : "text-red-500"}`}>
            {result.status === "ok" ? (
              <>
                <CheckCircle size={12} className="inline mr-1" />
                {result.cleared !== undefined
                  ? `Cleared ${result.cleared}/${result.total}`
                  : result.cleaned !== undefined
                  ? `Cleaned ${result.cleaned}/${result.total}`
                  : "完成"}
              </>
            ) : (
              <>
                <XCircle size={12} className="inline mr-1" />
                {result.error || "失败"}
              </>
            )}
          </span>
        )}
      </div>
    </div>
  );
}

function StatsCard({
  title,
  emoji,
  items,
}: {
  title: string;
  emoji: string;
  items: { label: string; value: string | number }[];
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-lg">{emoji}</span>
        <h3 className="text-xs lg:text-sm font-medium">{title}</h3>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {items.map((item, i) => (
          <div key={i}>
            <div className="text-[10px] text-muted-foreground uppercase tracking-wider">{item.label}</div>
            <div className="text-xs lg:text-sm font-semibold">{item.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
