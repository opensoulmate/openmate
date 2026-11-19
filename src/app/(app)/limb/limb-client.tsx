"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  RefreshCw, Plus, Play, Trash2, StopCircle,
  FileCode, Terminal, Loader2, CheckCircle,
  XCircle, Clock, MousePointer, Zap,
} from "lucide-react";
import { useIsMobile } from "@/hooks/use-mobile";
import { PageLayout } from '@/components/page-layout';

interface RPATask {
  task_id: string;
  name: string;
  status: string;
  priority: number;
  progress: number;
  current_step: number;
  total_steps: number;
  elapsed_seconds: number;
  created_at: number;
  error: string;
  tags: string[];
  results?: any[];
  actions?: any[];
}

interface Template {
  template_id: string;
  name: string;
  description: string;
  category: string;
  action_count: number;
  variables: any[];
  tags: string[];
}

interface LimbStats {
  total_tasks: number;
  by_status: Record<string, number>;
  queue_length: number;
  running: number;
  total_executed: number;
  total_succeeded: number;
  total_failed: number;
  success_rate: number;
  templates: number;
}

function statusColor(s: string): string {
  if (s === "completed") return "text-emerald-500";
  if (s === "running") return "text-blue-500";
  if (s === "failed") return "text-red-500";
  if (s === "cancelled") return "text-amber-500";
  return "text-muted-foreground";
}

function statusIcon(s: string) {
  if (s === "completed") return <CheckCircle size={14} className="text-emerald-500" />;
  if (s === "running") return <Loader2 size={14} className="text-blue-500 animate-spin" />;
  if (s === "failed") return <XCircle size={14} className="text-red-500" />;
  if (s === "queued") return <Clock size={14} className="text-muted-foreground" />;
  return <StopCircle size={14} className="text-amber-500" />;
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

const ACTION_ICONS: Record<string, string> = {
  navigate: "🌐", click: "🖱️", type: "⌨️", wait: "⏳",
  screenshot: "📸", extract: "📊", scroll: "↕️", submit: "📨",
  select: "📋", key_press: "🔤", hover: "👆", conditional: "❓",
};

export function LimbClient() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"tasks" | "templates" | "history">("tasks");
  const [stats, setStats] = useState<LimbStats | null>(null);
  const [tasks, setTasks] = useState<RPATask[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [selected, setSelected] = useState<RPATask | null>(null);
  const [loading, setLoading] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [showTemplate, setShowTemplate] = useState<Template | null>(null);
  const isMobile = useIsMobile();

  // Quick create from template
  const [tplVars, setTplVars] = useState<Record<string, string>>({});

  const apiBase = getApiBaseUrl();

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/limb/health`);
      setStats(await res.json());
    } catch {}
  }, [apiBase]);

  const fetchTasks = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/limb/tasks`);
      const data = await res.json();
      setTasks(data.tasks || []);
    } catch {}
  }, [apiBase]);

  const fetchTemplates = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/limb/templates`);
      const data = await res.json();
      setTemplates(data.templates || []);
    } catch {}
  }, [apiBase]);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/limb/history`);
      const data = await res.json();
      setHistory(data.history || []);
    } catch {}
  }, [apiBase]);

  useEffect(() => {
    fetchStats(); fetchTasks();
  }, [fetchStats, fetchTasks]);

  useEffect(() => {
    if (tab === "templates") fetchTemplates();
    if (tab === "history") fetchHistory();
  }, [tab, fetchTemplates, fetchHistory]);

  const handleExecute = async (taskId: string) => {
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/limb/tasks/${taskId}/execute`, { method: "POST" });
      const data = await res.json();
      setSelected(data);
      fetchTasks(); fetchStats();
    } catch {} finally { setLoading(false); }
  };

  const handleCancel = async (taskId: string) => {
    try {
      await fetch(`${apiBase}/api/limb/tasks/${taskId}/cancel`, { method: "POST" });
      fetchTasks(); fetchStats();
    } catch {}
  };

  const handleDelete = async (taskId: string) => {
    try {
      await fetch(`${apiBase}/api/limb/tasks/${taskId}`, { method: "DELETE" });
      setSelected(null);
      fetchTasks();
    } catch {}
  };

  const handleInstantiate = async (templateId: string) => {
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/limb/templates/${templateId}/instantiate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ variables: tplVars }),
      });
      if (res.ok) {
        setShowTemplate(null);
        setTplVars({});
        setTab("tasks");
        fetchTasks(); fetchStats();
      }
    } catch {} finally { setLoading(false); }
  };

  const tabs = [
    { id: "tasks" as const, label: t("limb.tasks") || "Task", icon: Terminal },
    { id: "templates" as const, label: t("limb.templates") || "Templates", icon: FileCode },
    { id: "history" as const, label: t("limb.history") || "History", icon: Clock },
  ];

  return (
      <PageLayout title="四肢">
        
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-orange-500/10">
            <MousePointer size={20} className="text-orange-500" />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg lg:text-xl font-semibold truncate">{t("limb.title") || "Limb · RPA Executor"}</h1>
            <p className="text-xs lg:text-sm text-muted-foreground truncate">{t('organTag.limb')}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => { fetchStats(); fetchTasks(); }}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4 lg:py-6 space-y-3 lg:space-y-6">
        {/* Stats */}
        {stats && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2 lg:gap-4">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("limb.t26095") || "TotalTask"}</span>
              <p className="text-xl lg:text-2xl font-bold">{stats.total_tasks}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("limb.running") || "Running"}</span>
              <p className="text-xl lg:text-2xl font-bold text-blue-500">{stats.running}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("limb.t54694") || "Queue"}</span>
              <p className="text-xl lg:text-2xl font-bold text-amber-500">{stats.queue_length}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("limb.successRate") || "Success Rate"}</span>
              <p className="text-xl lg:text-2xl font-bold text-emerald-500">{stats.success_rate}%</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("limb.templates") || "Templates"}</span>
              <p className="text-xl lg:text-2xl font-bold text-indigo-500">{stats.templates}</p>
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-2">
          {tabs.map((tabItem) => (
            <button key={tabItem.id} onClick={() => setTab(tabItem.id)}
              className={cn("flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs lg:text-sm transition-colors",
                tab === tabItem.id ? "bg-orange-500/10 text-orange-600 font-medium" : "hover:bg-muted text-muted-foreground")}>
              <tabItem.icon size={14} /> {tabItem.label}
            </button>
          ))}
        </div>

        {/* Tasks Tab */}
        {tab === "tasks" && (
          <div className={`flex gap-2 lg:gap-6 ${isMobile ? 'flex-col' : ''} relative`}>
            <div className={`${isMobile ? 'w-full' : 'flex-1'} space-y-3`}>
              {tasks.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                  <Terminal size={40} className="mb-3 opacity-30" />
                  <p className="text-xs lg:text-sm">{t("limb.t14143") || "No tasks yet, create one from a template"}</p>
                </div>
              ) : tasks.map((task) => (
                <div key={task.task_id}
                  onClick={() => setSelected(task)}
                  className={cn(
                    "rounded-xl border border-border bg-card p-3 lg:p-4 cursor-pointer transition-all hover:shadow-md",
                    selected?.task_id === task.task_id && "ring-2 ring-orange-500"
                  )}>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      {statusIcon(task.status)}
                      <span className="font-medium text-xs lg:text-sm">{task.name}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={cn("text-xs", statusColor(task.status))}>{task.status}</span>
                      {task.status === "queued" && (
                        <button onClick={(e) => { e.stopPropagation(); handleExecute(task.task_id); }}
                          className="rounded-md p-1 text-emerald-500 hover:bg-emerald-500/10">
                          <Play size={12} />
                        </button>
                      )}
                      {(task.status === "queued" || task.status === "running") && (
                        <button onClick={(e) => { e.stopPropagation(); handleCancel(task.task_id); }}
                          className="rounded-md p-1 text-amber-500 hover:bg-amber-500/10">
                          <StopCircle size={12} />
                        </button>
                      )}
                    </div>
                  </div>
                  {/* Progress bar */}
                  <div className="flex items-center gap-3">
                    <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
                      <div className={cn("h-full rounded-full transition-all",
                        task.status === "completed" ? "bg-emerald-500" :
                        task.status === "failed" ? "bg-red-500" : "bg-orange-500")}
                        style={{ width: `${task.progress}%` }} />
                    </div>
                    <span className="text-xs text-muted-foreground">
                      {task.current_step}/{task.total_steps}
                    </span>
                    {task.elapsed_seconds > 0 && (
                      <span className="text-xs text-muted-foreground">{task.elapsed_seconds}s</span>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {/* Detail Panel — sidebar sliding on mobile, inline on desktop */}
            {selected && isMobile && (
              <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setSelected(null)} aria-hidden="true" />
            )}
            {selected && isMobile ? (
              <div
                className="absolute inset-y-0 right-0 z-10 h-full w-72 min-w-0 border-l border-border transition-[right] duration-200 ease-linear flex flex-col overflow-hidden bg-card"
                style={{ right: 0 }}
              >
                <div className="flex-1 overflow-y-auto p-4 space-y-4">
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <h3 className="font-semibold text-xs lg:text-sm">{selected.name}</h3>
                      <button onClick={() => handleDelete(selected.task_id)}
                        className="rounded-md p-1.5 text-red-500 hover:bg-red-500/10">
                        <Trash2 size={14} />
                      </button>
                    </div>
                    <div className="space-y-1 text-xs text-muted-foreground">
                      <div className="flex justify-between">
                        <span>ID</span><span className="font-mono">{selected.task_id}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>{t("limb.status") || "Status"}</span><span className={statusColor(selected.status)}>{selected.status}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>{t("limb.t97513") || "Progress"}</span><span>{selected.progress}%</span>
                      </div>
                      <div className="flex justify-between">
                        <span>{t("limb.elapsed") || "Elapsed"}</span><span>{selected.elapsed_seconds}s</span>
                      </div>
                    </div>
                    {selected.error && (
                      <div className="rounded-lg bg-red-500/10 p-3 text-xs text-red-500">
                        {selected.error}
                      </div>
                    )}
                  </div>

                  {selected.results && selected.results.length > 0 && (
                    <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-2">
                      <h4 className="text-xs lg:text-sm font-medium">{t("limb.t62005") || "Execution steps"}</h4>
                      {selected.results.map((r, i) => (
                        <div key={i} className="flex items-start gap-2 text-xs">
                          <span className="shrink-0">{ACTION_ICONS[r.action_type] || "⚡"}</span>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1">
                              {r.success ? <CheckCircle size={10} className="text-emerald-500" /> : <XCircle size={10} className="text-red-500" />}
                              <span className="font-mono">{r.action_type}</span>
                              <span className="text-muted-foreground ml-auto">{r.duration_ms}ms</span>
                            </div>
                            {r.output && <p className="text-muted-foreground truncate">{r.output}</p>}
                            {r.error && <p className="text-red-500">{r.error}</p>}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {selected.actions && selected.actions.length > 0 && (
                    <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-2">
                      <h4 className="text-xs lg:text-sm font-medium">{t("limb.t47867") || "Action List"}</h4>
                      {selected.actions.map((a, i) => (
                        <div key={i} className="flex items-center gap-2 text-xs">
                          <span>{ACTION_ICONS[a.action_type] || "⚡"}</span>
                          <span className="font-mono">{a.action_type}</span>
                          {a.target && <span className="text-muted-foreground truncate">{a.target}</span>}
                          {a.description && <span className="text-muted-foreground ml-auto truncate">{a.description}</span>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : selected ? (
              <div className="w-96 space-y-4">
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="font-semibold text-xs lg:text-sm">{selected.name}</h3>
                    <button onClick={() => handleDelete(selected.task_id)}
                      className="rounded-md p-1.5 text-red-500 hover:bg-red-500/10">
                      <Trash2 size={14} />
                    </button>
                  </div>
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <div className="flex justify-between">
                      <span>ID</span><span className="font-mono">{selected.task_id}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>{t("limb.status") || "Status"}</span><span className={statusColor(selected.status)}>{selected.status}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>{t("limb.t97513") || "Progress"}</span><span>{selected.progress}%</span>
                    </div>
                    <div className="flex justify-between">
                      <span>{t("limb.elapsed") || "Elapsed"}</span><span>{selected.elapsed_seconds}s</span>
                    </div>
                  </div>
                  {selected.error && (
                    <div className="rounded-lg bg-red-500/10 p-3 text-xs text-red-500">
                      {selected.error}
                    </div>
                  )}
                </div>

                {/* Step Results */}
                {selected.results && selected.results.length > 0 && (
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-2">
                    <h4 className="text-xs lg:text-sm font-medium">{t("limb.t62005") || "Execution steps"}</h4>
                    {selected.results.map((r, i) => (
                      <div key={i} className="flex items-start gap-2 text-xs">
                        <span className="shrink-0">{ACTION_ICONS[r.action_type] || "⚡"}</span>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1">
                            {r.success ? <CheckCircle size={10} className="text-emerald-500" /> : <XCircle size={10} className="text-red-500" />}
                            <span className="font-mono">{r.action_type}</span>
                            <span className="text-muted-foreground ml-auto">{r.duration_ms}ms</span>
                          </div>
                          {r.output && <p className="text-muted-foreground truncate">{r.output}</p>}
                          {r.error && <p className="text-red-500">{r.error}</p>}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Actions list */}
                {selected.actions && selected.actions.length > 0 && (
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-2">
                    <h4 className="text-xs lg:text-sm font-medium">{t("limb.t47867") || "Action List"}</h4>
                    {selected.actions.map((a, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span>{ACTION_ICONS[a.action_type] || "⚡"}</span>
                        <span className="font-mono">{a.action_type}</span>
                        {a.target && <span className="text-muted-foreground truncate">{a.target}</span>}
                        {a.description && <span className="text-muted-foreground ml-auto truncate">{a.description}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : null}
          </div>
        )}

        {/* Templates Tab */}
        {tab === "templates" && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 lg:gap-4">
            {templates.map((tpl) => (
              <div key={tpl.template_id}
                className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3 hover:shadow-md transition-all">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="font-semibold text-xs lg:text-sm">{tpl.name}</h3>
                    <p className="text-xs text-muted-foreground mt-1">{tpl.description}</p>
                  </div>
                  <span className="rounded-full bg-orange-500/10 px-2 py-0.5 text-[10px] text-orange-500 font-medium">
                    {tpl.category}
                  </span>
                </div>
                <div className="flex gap-2 text-xs text-muted-foreground">
                  <span>{(t("limb.actionCount") || "{{count}} actions").replace("{count}", String(tpl.action_count))}</span>
                  <span>{(t("limb.variableCount") || "{{count}} variables").replace("{count}", String(tpl.variables.length))}</span>
                </div>
                <div className="flex gap-1">
                  {tpl.tags.map((tag) => (
                    <span key={tag} className="rounded-full bg-muted px-2 py-0.5 text-[10px]">{tag}</span>
                  ))}
                </div>
                <button onClick={() => setShowTemplate(tpl)}
                  className="flex items-center gap-1.5 rounded-lg bg-orange-500 px-3 py-2 text-xs lg:text-sm text-white hover:bg-orange-600 w-full justify-center">
                  <Zap size={14} /> {t("limb.t26840") || "Use Template"}
                </button>
              </div>
            ))}
          </div>
        )}

        {/* History Tab */}
        {tab === "history" && (
          <div className="space-y-3">
            {history.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Clock size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("limb.noHistory") || "No execution history"}</p>
              </div>
            ) : (
              <div className="rounded-xl border border-border overflow-x-auto">
                <table className="w-full text-xs lg:text-sm min-w-[500px]">
                  <thead>
                    <tr className="border-b border-border bg-muted/30">
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t("limb.time") || "Time"}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t("limb.name") || "Name"}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t("limb.status") || "Status"}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t("limb.steps") || "Steps"}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t("limb.elapsed") || "Elapsed"}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((h, i) => (
                      <tr key={i} className="border-b border-border last:border-0 hover:bg-muted/30">
                        <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground">{formatTime(h.completed_at)}</td>
                        <td className="px-2 lg:px-4 py-2.5">{h.name}</td>
                        <td className="px-2 lg:px-4 py-2.5">
                          <span className={cn("text-xs font-medium", statusColor(h.status))}>{h.status}</span>
                        </td>
                        <td className="px-2 lg:px-4 py-2.5 text-xs">{h.steps}</td>
                        <td className="px-2 lg:px-4 py-2.5 text-xs">{h.elapsed_seconds}s</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Template Instantiate Modal */}
        {showTemplate && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
            <div className="w-full max-w-md rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-4">
              <h3 className="font-semibold">{t("limb.t26840") || "Use Template"}: {showTemplate.name}</h3>
              <p className="text-xs lg:text-sm text-muted-foreground">{showTemplate.description}</p>
              {showTemplate.variables.map((v: any) => (
                <div key={v.name}>
                  <label className="text-xs text-muted-foreground">{v.description || v.name}</label>
                  <input value={tplVars[v.name] || v.default || ""}
                    onChange={(e) => setTplVars({ ...tplVars, [v.name]: e.target.value })}
                    placeholder={v.description}
                    className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                </div>
              ))}
              <div className="flex justify-end gap-2">
                <button onClick={() => { setShowTemplate(null); setTplVars({}); }}
                  className="rounded-lg border border-border px-2 lg:px-4 py-2 text-xs lg:text-sm hover:bg-muted">{t("limb.cancel") || "Cancel"}</button>
                <button onClick={() => handleInstantiate(showTemplate.template_id)} disabled={loading}
                  className="rounded-lg bg-orange-500 px-2 lg:px-4 py-2 text-xs lg:text-sm text-white hover:bg-orange-600 disabled:opacity-50">
                  {loading ? <Loader2 size={14} className="animate-spin" /> : (t("limb.createTask") || "Create Task")}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
