"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  RefreshCw, Plus, Play, Pause, Trash2,
  Camera, Terminal, Settings, Box, Loader2, Layers,
  Sparkles, FileText, Zap, Copy,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface Sandbox {
  sandbox_id: string;
  name: string;
  status: string;
  created_at: number;
  snapshot_count: number;
  log_count: number;
}

interface SandboxTemplate {
  template_id: string;
  name: string;
  description: string;
  icon: string;
  config: any;
  variables: Record<string, string>;
  tags: string[];
  category: string;
  usage_count: number;
}

export function MirrorClient() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"sandboxes" | "templates">("sandboxes");
  const [sandboxes, setSandboxes] = useState<Sandbox[]>([]);
  const [templates, setTemplates] = useState<SandboxTemplate[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [selected, setSelected] = useState<Sandbox | null>(null);
  const [selectedTemplate, setSelectedTemplate] = useState<SandboxTemplate | null>(null);
  const [logs, setLogs] = useState<any[]>([]);
  const [variables, setVariables] = useState<any>({});
  const [showCreate, setShowCreate] = useState(false);
  const [showCreateFromTemplate, setShowCreateFromTemplate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [loading, setLoading] = useState(false);
  const [logMessage, setLogMessage] = useState("");
  const [varKey, setVarKey] = useState("");
  const [varValue, setVarValue] = useState("");
  const [templateVarOverrides, setTemplateVarOverrides] = useState<Record<string, string>>({});
  const isMobile = useIsMobile();
  const apiBase = getApiBaseUrl();

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/mirror/health`);
      setHealth(await res.json());
    } catch {}
  }, [apiBase]);

  const fetchSandboxes = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/mirror/sandboxes`);
      const data = await res.json();
      setSandboxes(data.sandboxes || []);
    } catch {}
  }, [apiBase]);

  const fetchTemplates = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/mirror/templates`);
      const data = await res.json();
      setTemplates(data.templates || []);
    } catch {}
  }, [apiBase]);

  const fetchLogs = useCallback(async (id: string) => {
    try {
      const res = await fetch(`${apiBase}/api/mirror/sandboxes/${id}/logs`);
      const data = await res.json();
      setLogs(data.logs || []);
    } catch {}
  }, [apiBase]);

  const fetchVariables = useCallback(async (id: string) => {
    try {
      const res = await fetch(`${apiBase}/api/mirror/sandboxes/${id}/variables`);
      const data = await res.json();
      setVariables(data.variables || {});
    } catch {}
  }, [apiBase]);

  useEffect(() => {
    fetchHealth();
    if (tab === "sandboxes") fetchSandboxes();
    if (tab === "templates") fetchTemplates();
  }, [tab, fetchHealth, fetchSandboxes, fetchTemplates]);

  useEffect(() => {
    if (selected) {
      fetchLogs(selected.sandbox_id);
      fetchVariables(selected.sandbox_id);
    }
  }, [selected, fetchLogs, fetchVariables]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/mirror/sandboxes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName, description: newDesc }),
      });
      if (res.ok) {
        setShowCreate(false);
        setNewName(""); setNewDesc("");
        fetchSandboxes();
        fetchHealth();
      }
    } catch {} finally { setLoading(false); }
  };

  const handleCreateFromTemplate = async (tpl: SandboxTemplate) => {
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/mirror/templates/${tpl.template_id}/instantiate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          template_id: tpl.template_id,
          variables: templateVarOverrides,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setShowCreateFromTemplate(false);
        setSelectedTemplate(null);
        setTemplateVarOverrides({});
        setTab("sandboxes");
        fetchSandboxes();
        fetchHealth();
      }
    } catch {} finally { setLoading(false); }
  };

  const handleSnapshot = async (id: string) => {
    try {
      await fetch(`${apiBase}/api/mirror/sandboxes/${id}/snapshot`, { method: "POST" });
      fetchSandboxes();
    } catch {}
  };

  const handlePause = async (id: string) => {
    try {
      await fetch(`${apiBase}/api/mirror/sandboxes/${id}/pause`, { method: "POST" });
      fetchSandboxes();
      if (selected?.sandbox_id === id) setSelected({ ...selected, status: "paused" });
    } catch {}
  };

  const handleResume = async (id: string) => {
    try {
      await fetch(`${apiBase}/api/mirror/sandboxes/${id}/resume`, { method: "POST" });
      fetchSandboxes();
      if (selected?.sandbox_id === id) setSelected({ ...selected, status: "active" });
    } catch {}
  };

  const handleDestroy = async (id: string) => {
    if (!confirm(t("mirror.t91607") || "Destroy this sandbox?")) return;
    try {
      await fetch(`${apiBase}/api/mirror/sandboxes/${id}`, { method: "DELETE" });
      setSelected(null);
      fetchSandboxes();
      fetchHealth();
    } catch {}
  };

  const handleAddLog = async (id: string) => {
    if (!logMessage.trim()) return;
    try {
      await fetch(`${apiBase}/api/mirror/sandboxes/${id}/log`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ level: "info", message: logMessage }),
      });
      setLogMessage("");
      fetchLogs(id);
    } catch {}
  };

  const handleSetVariable = async (id: string) => {
    if (!varKey.trim()) return;
    try {
      await fetch(`${apiBase}/api/mirror/sandboxes/${id}/variables`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: varKey, value: varValue }),
      });
      setVarKey(""); setVarValue("");
      fetchVariables(id);
    } catch {}
  };

  const handleCleanup = async () => {
    try {
      await fetch(`${apiBase}/api/mirror/cleanup`, { method: "POST" });
      fetchSandboxes();
      fetchHealth();
    } catch {}
  };

  const statusColor = (s: string) => {
    if (s === "active") return "text-emerald-500";
    if (s === "paused") return "text-amber-500";
    return "text-muted-foreground";
  };

  const categoryColor = (cat: string) => {
    if (cat === "workflow") return "bg-blue-500/10 text-blue-500";
    if (cat === "agent") return "bg-purple-500/10 text-purple-500";
    if (cat === "connector") return "bg-amber-500/10 text-amber-500";
    return "bg-muted text-muted-foreground";
  };

  const tabs = [
    { id: "sandboxes" as const, label: t("mirror.t22079") || "Sandboxes", icon: Box },
    { id: "templates" as const, label: t("mirror.templates") || "Templates", icon: FileText },
  ];

  return (
      <PageLayout title="镜像">
        
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-3 lg:py-4 gap-2">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <Layers size={18} className="text-indigo-500 shrink-0" />
          <h1 className="text-sm lg:text-lg font-semibold truncate">{t("mirror.title") || "Mirror · Sandbox Testing"}</h1>
          <span className="rounded-full bg-indigo-500/10 px-1.5 lg:px-2 py-0.5 text-[10px] lg:text-xs font-medium text-indigo-500 shrink-0 hidden sm:inline">
            {t("mirror.t13347") || "Isolated environment"}
          </span>
        </div>
        <div className="flex items-center gap-1.5 lg:gap-2 shrink-0">
          {tab === "sandboxes" && (
            <>
              <button onClick={handleCleanup}
                className="flex items-center gap-1 rounded-lg border border-border px-2 lg:px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted touch-manipulation">
                <Sparkles size={12} />
                <span className="hidden sm:inline">{t("mirror.cleanup") || "Cleanup"}</span>
              </button>
              <button onClick={() => setShowCreate(true)}
                className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs lg:text-sm text-white hover:bg-indigo-600">
                <Plus size={14} /> {t("mirror.t40266") || "Create Sandbox"}
              </button>
            </>
          )}
          <button onClick={() => { fetchHealth(); tab === "sandboxes" && fetchSandboxes(); tab === "templates" && fetchTemplates(); }}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 lg:p-6 space-y-3 lg:space-y-6">
        {/* Stats */}
        {health && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2 lg:gap-4">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("mirror.t22079") || "Sandboxes"}</span>
              <p className="text-xl lg:text-2xl font-bold">{health.total_sandboxes || 0}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("mirror.active") || "Active"}</span>
              <p className="text-xl lg:text-2xl font-bold text-emerald-500">{health.active || 0}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("mirror.paused") || "Paused"}</span>
              <p className="text-xl lg:text-2xl font-bold text-amber-500">{health.paused || 0}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("mirror.ttl") || "Storage"}</span>
              <p className="text-xs font-mono truncate mt-1">{health.sandbox_dir}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("mirror.totalTemplates") || "Templates"}</span>
              <p className="text-xl lg:text-2xl font-bold">{health.templates?.total_templates || 0}</p>
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-2">
          {tabs.map((tabItem) => (
            <button key={tabItem.id} onClick={() => setTab(tabItem.id)}
              className={cn("flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs lg:text-sm",
                tab === tabItem.id ? "bg-indigo-500/10 text-indigo-600 font-medium" : "hover:bg-muted text-muted-foreground")}>
              <tabItem.icon size={14} /> {tabItem.label}
            </button>
          ))}
        </div>

        {/* Sandboxes Tab */}
        {tab === "sandboxes" && (
          <div className="flex gap-2 lg:gap-6 relative">
            {/* Sandbox List — full width on mobile, w-80 on desktop */}
            <div className={`${isMobile ? (selected ? "hidden" : "w-full") : "w-80"} space-y-3`}>
              {isMobile && (
                <div className="flex items-center justify-between pb-2 border-b border-border">
                  <h3 className="text-xs lg:text-sm font-medium">{t("mirror.sandboxes") || "Sandboxes"}</h3>
                  <span className="text-xs text-muted-foreground">{sandboxes.length}</span>
                </div>
              )}
              {sandboxes.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                  <Box size={40} className="mb-3 opacity-30" />
                  <p className="text-xs lg:text-sm">{t("mirror.noSandboxes") || "No sandboxes yet"}</p>
                  <button onClick={() => setTab("templates")}
                    className="mt-3 text-xs text-indigo-500 hover:underline">{t("mirror.createFromTemplate") || "Create from template →"}</button>
                </div>
              ) : sandboxes.map((sb) => (
                <div key={sb.sandbox_id}
                  onClick={() => setSelected(sb)}
                  className={cn(
                    "rounded-xl border border-border bg-card p-3 lg:p-4 cursor-pointer transition-all hover:shadow-md",
                    selected?.sandbox_id === sb.sandbox_id && "ring-2 ring-indigo-500"
                  )}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-medium text-xs lg:text-sm">{sb.name}</span>
                    <span className={cn("text-xs", statusColor(sb.status))}>{sb.status}</span>
                  </div>
                  <div className="flex gap-3 text-xs text-muted-foreground">
                    <span>{t("mirror.snapshot") || "Snapshots"}: {sb.snapshot_count}</span>
                    <span>{t("mirror.log") || "Logs"}: {sb.log_count}</span>
                  </div>
                </div>
              ))}
            </div>

            {/* Detail Panel — sidebar sliding on mobile, inline on desktop */}
            {isMobile && !!selected && (
              <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setSelected(null)} aria-hidden="true" />
            )}
            {isMobile ? (
              !!selected && (
                <div
                  className="absolute inset-y-0 right-0 z-10 h-full w-72 min-w-0 border-l border-border transition-[right] duration-200 ease-linear flex flex-col overflow-hidden bg-card"
                  style={{ right: 0 }}
                >
                  <div className="flex-1 overflow-y-auto p-3 lg:p-4 space-y-4">
                    {selected && (
                      <>
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <h3 className="font-semibold">{selected.name}</h3>
                            <span className={cn("text-xs px-2 py-0.5 rounded-full",
                              selected.status === "active" ? "bg-emerald-500/10 text-emerald-500" :
                              selected.status === "paused" ? "bg-amber-500/10 text-amber-500" :
                              "bg-muted text-muted-foreground"
                            )}>{selected.status}</span>
                          </div>
                          <div className="flex gap-2">
                            {selected.status === "active" ? (
                              <button onClick={() => handlePause(selected.sandbox_id)}
                                className="flex items-center gap-1 rounded-lg border border-amber-500/30 px-3 py-1.5 text-xs text-amber-600 hover:bg-amber-500/10">
                                <Pause size={12} /> {t("mirror.pause") || "Pause"}
                              </button>
                            ) : selected.status === "paused" ? (
                              <button onClick={() => handleResume(selected.sandbox_id)}
                                className="flex items-center gap-1 rounded-lg border border-emerald-500/30 px-3 py-1.5 text-xs text-emerald-600 hover:bg-emerald-500/10">
                                <Play size={12} /> {t("mirror.resume") || "Resume"}
                              </button>
                            ) : null}
                            <button onClick={() => handleSnapshot(selected.sandbox_id)}
                              className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                              <Camera size={12} /> {t("mirror.snapshot") || "Snapshot"}
                            </button>
                            <button onClick={() => handleDestroy(selected.sandbox_id)}
                              className="flex items-center gap-1 rounded-lg border border-red-500/30 px-3 py-1.5 text-xs text-red-500 hover:bg-red-500/10">
                              <Trash2 size={12} /> {t("mirror.destroy") || "Destroy"}
                            </button>
                          </div>
                        </div>

                        {/* Variables */}
                        <div className="rounded-xl border border-border p-3 lg:p-4">
                          <h4 className="text-xs lg:text-sm font-medium mb-2">{t("mirror.variables") || "Variables"}</h4>
                          {Object.keys(variables).length === 0 ? (
                            <p className="text-xs text-muted-foreground">{t("mirror.t74225") || "No variables"}</p>
                          ) : (
                            <div className="space-y-1 mb-3">
                              {Object.entries(variables).map(([k, v]) => (
                                <div key={k} className="flex gap-2 text-xs">
                                  <span className="font-mono text-indigo-500">{k}</span>
                                  <span className="text-muted-foreground">=</span>
                                  <span className="font-mono">{String(v)}</span>
                                </div>
                              ))}
                            </div>
                          )}
                          <div className="flex gap-2 mt-2">
                            <input value={varKey} onChange={(e) => setVarKey(e.target.value)}
                              placeholder={t("mirror.t13221") || "Key"} className="flex-1 rounded border border-border bg-background px-2 py-1 text-xs" />
                            <input value={varValue} onChange={(e) => setVarValue(e.target.value)}
                              placeholder={t("mirror.t13221") || "Value"} className="flex-1 rounded border border-border bg-background px-2 py-1 text-xs" />
                            <button onClick={() => handleSetVariable(selected.sandbox_id)}
                              className="rounded bg-indigo-500 px-2 py-1 text-xs text-white hover:bg-indigo-600">
                              {t("mirror.t85783") || "Set"}
                            </button>
                          </div>
                        </div>

                        {/* Logs */}
                        <div className="rounded-xl border border-border p-3 lg:p-4">
                          <div className="flex items-center justify-between mb-2">
                            <h4 className="text-xs lg:text-sm font-medium">{t("mirror.log") || "Logs"} ({logs.length})</h4>
                            <div className="flex gap-2">
                              <input value={logMessage} onChange={(e) => setLogMessage(e.target.value)}
                                onKeyDown={(e) => e.key === "Enter" && handleAddLog(selected.sandbox_id)}
                                placeholder={t("mirror.t74580") || "Enter log message"} className="rounded border border-border bg-background px-2 py-1 text-xs w-48" />
                              <button onClick={() => handleAddLog(selected.sandbox_id)}
                                className="rounded bg-indigo-500 px-2 py-1 text-xs text-white hover:bg-indigo-600">
                                {t("mirror.logAction") || "Record"}
                              </button>
                            </div>
                          </div>
                          <div className="max-h-60 overflow-y-auto space-y-1">
                            {logs.length === 0 ? (
                              <p className="text-xs text-muted-foreground">{t("mirror.t59742") || "No logs"}</p>
                            ) : logs.map((l, i) => (
                              <div key={i} className="flex gap-2 text-xs">
                                <span className="text-muted-foreground shrink-0">{new Date(l.ts * 1000).toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}</span>
                                <span className={cn("font-mono", l.level === "error" ? "text-red-500" : l.level === "warn" ? "text-amber-500" : "text-foreground")}>
                                  {l.message}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                </div>
              )
            ) : (
              selected && (
                <div className="flex-1 space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <h3 className="font-semibold">{selected.name}</h3>
                      <span className={cn("text-xs px-2 py-0.5 rounded-full",
                        selected.status === "active" ? "bg-emerald-500/10 text-emerald-500" :
                        selected.status === "paused" ? "bg-amber-500/10 text-amber-500" :
                        "bg-muted text-muted-foreground"
                      )}>{selected.status}</span>
                    </div>
                    <div className="flex gap-2">
                      {selected.status === "active" ? (
                        <button onClick={() => handlePause(selected.sandbox_id)}
                          className="flex items-center gap-1 rounded-lg border border-amber-500/30 px-3 py-1.5 text-xs text-amber-600 hover:bg-amber-500/10">
                          <Pause size={12} /> {t("mirror.pause") || "Pause"}
                        </button>
                      ) : selected.status === "paused" ? (
                        <button onClick={() => handleResume(selected.sandbox_id)}
                          className="flex items-center gap-1 rounded-lg border border-emerald-500/30 px-3 py-1.5 text-xs text-emerald-600 hover:bg-emerald-500/10">
                          <Play size={12} /> {t("mirror.resume") || "Resume"}
                        </button>
                      ) : null}
                      <button onClick={() => handleSnapshot(selected.sandbox_id)}
                        className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                        <Camera size={12} /> {t("mirror.snapshot") || "Snapshot"}
                      </button>
                      <button onClick={() => handleDestroy(selected.sandbox_id)}
                        className="flex items-center gap-1 rounded-lg border border-red-500/30 px-3 py-1.5 text-xs text-red-500 hover:bg-red-500/10">
                        <Trash2 size={12} /> {t("mirror.destroy") || "Destroy"}
                      </button>
                    </div>
                  </div>

                  {/* Variables */}
                  <div className="rounded-xl border border-border p-3 lg:p-4">
                    <h4 className="text-xs lg:text-sm font-medium mb-2">{t("mirror.variables") || "Variables"}</h4>
                    {Object.keys(variables).length === 0 ? (
                      <p className="text-xs text-muted-foreground">{t("mirror.t74225") || "No variables"}</p>
                    ) : (
                      <div className="space-y-1 mb-3">
                        {Object.entries(variables).map(([k, v]) => (
                          <div key={k} className="flex gap-2 text-xs">
                            <span className="font-mono text-indigo-500">{k}</span>
                            <span className="text-muted-foreground">=</span>
                            <span className="font-mono">{String(v)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="flex gap-2 mt-2">
                      <input value={varKey} onChange={(e) => setVarKey(e.target.value)}
                        placeholder={t("mirror.t13221") || "Key"} className="flex-1 rounded border border-border bg-background px-2 py-1 text-xs" />
                      <input value={varValue} onChange={(e) => setVarValue(e.target.value)}
                        placeholder={t("mirror.t13221") || "Value"} className="flex-1 rounded border border-border bg-background px-2 py-1 text-xs" />
                      <button onClick={() => handleSetVariable(selected.sandbox_id)}
                        className="rounded bg-indigo-500 px-2 py-1 text-xs text-white hover:bg-indigo-600">
                        {t("mirror.t85783") || "Set"}
                      </button>
                    </div>
                  </div>

                  {/* Logs */}
                  <div className="rounded-xl border border-border p-3 lg:p-4">
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="text-xs lg:text-sm font-medium">{t("mirror.log") || "Logs"} ({logs.length})</h4>
                      <div className="flex gap-2">
                        <input value={logMessage} onChange={(e) => setLogMessage(e.target.value)}
                          onKeyDown={(e) => e.key === "Enter" && handleAddLog(selected.sandbox_id)}
                          placeholder={t("mirror.t74580") || "Enter log message"} className="rounded border border-border bg-background px-2 py-1 text-xs w-48" />
                        <button onClick={() => handleAddLog(selected.sandbox_id)}
                          className="rounded bg-indigo-500 px-2 py-1 text-xs text-white hover:bg-indigo-600">
                          {t("mirror.logAction") || "Record"}
                        </button>
                      </div>
                    </div>
                    <div className="max-h-60 overflow-y-auto space-y-1">
                      {logs.length === 0 ? (
                        <p className="text-xs text-muted-foreground">{t("mirror.t59742") || "No logs"}</p>
                      ) : logs.map((l, i) => (
                        <div key={i} className="flex gap-2 text-xs">
                          <span className="text-muted-foreground shrink-0">{new Date(l.ts * 1000).toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}</span>
                          <span className={cn("font-mono", l.level === "error" ? "text-red-500" : l.level === "warn" ? "text-amber-500" : "text-foreground")}>
                            {l.message}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )
            )}
          </div>
        )}

        {/* Templates Tab */}
        {tab === "templates" && (
          <div className="space-y-4">
            <h3 className="text-xs lg:text-sm font-medium">{t("mirror.sandboxTemplates") || "Sandbox Templates"}</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2 lg:gap-4">
              {templates.map((tpl) => (
                <div key={tpl.template_id}
                  className={cn(
                    "rounded-xl border border-border bg-card p-3 lg:p-4 cursor-pointer transition-all hover:shadow-md",
                    selectedTemplate?.template_id === tpl.template_id && "ring-2 ring-indigo-500"
                  )}
                  onClick={() => { setSelectedTemplate(tpl); setTemplateVarOverrides({}); }}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-lg">{tpl.icon}</span>
                    <span className="font-medium text-xs lg:text-sm">{tpl.name}</span>
                    <span className={cn("text-[10px] px-1.5 py-0.5 rounded-full ml-auto", categoryColor(tpl.category))}>
                      {tpl.category}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground mb-3">{tpl.description}</p>
                  <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
                    <span>{t("mirror.variableLabel") || "变量："} {Object.keys(tpl.variables).length}</span>
                    <span>{t("mirror.usageLabel") || "用法："} {tpl.usage_count}</span>
                    <span>TTL: {tpl.config?.ttl_seconds || 3600}s</span>
                  </div>
                  {tpl.tags.length > 0 && (
                    <div className="flex gap-1 mt-2">
                      {tpl.tags.map((tag) => (
                        <span key={tag} className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{tag}</span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* Template Action Panel */}
            {selectedTemplate && (
              <div className="rounded-xl border border-indigo-500/30 bg-indigo-500/5 p-3 lg:p-4 space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-lg">{selectedTemplate.icon}</span>
                    <h4 className="font-medium">{t("mirror.createSandboxFromTemplate") || "从模板创建沙盒："} {selectedTemplate.name}</h4>
                  </div>
                  <button onClick={() => setSelectedTemplate(null)} className="text-xs text-muted-foreground hover:text-foreground">✕</button>
                </div>

                {/* Variable Overrides */}
                {Object.keys(selectedTemplate.variables).length > 0 && (
                  <div className="grid grid-cols-2 gap-3">
                    {Object.entries(selectedTemplate.variables).map(([k, v]) => (
                      <div key={k}>
                        <label className="text-xs text-muted-foreground font-mono">{k}</label>
                        <input
                          value={templateVarOverrides[k] ?? v}
                          onChange={(e) => setTemplateVarOverrides({ ...templateVarOverrides, [k]: e.target.value })}
                          placeholder={k}
                          className="mt-1 w-full rounded border border-border bg-background px-2 py-1.5 text-xs"
                        />
                      </div>
                    ))}
                  </div>
                )}

                <button onClick={() => handleCreateFromTemplate(selectedTemplate)} disabled={loading}
                  className="flex items-center gap-2 rounded-lg bg-indigo-500 px-4 py-2 text-xs lg:text-sm text-white hover:bg-indigo-600 disabled:opacity-50">
                  {loading ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
                  {t("mirror.createSandbox") || "Create Sandbox"}
                </button>
              </div>
            )}
          </div>
        )}

        {/* Create Modal */}
        {showCreate && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
            <div className="w-full max-w-md rounded-xl border border-border bg-card p-3 lg:p-6 space-y-4">
              <h3 className="font-semibold">{t("mirror.t40266") || "Create Sandbox"}</h3>
              <input value={newName} onChange={(e) => setNewName(e.target.value)}
                placeholder={t("mirror.sandboxName") || "Sandbox Name"} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
              <input value={newDesc} onChange={(e) => setNewDesc(e.target.value)}
                placeholder={t("mirror.t65256") || "Description (optional)"} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
              <div className="flex justify-end gap-2">
                <button onClick={() => setShowCreate(false)}
                  className="rounded-lg border border-border px-4 py-2 text-xs lg:text-sm hover:bg-muted">{t("common.cancel") || "Cancel"}</button>
                <button onClick={handleCreate} disabled={loading}
                  className="rounded-lg bg-indigo-500 px-4 py-2 text-xs lg:text-sm text-white hover:bg-indigo-600 disabled:opacity-50">
                  {loading ? <Loader2 size={14} className="animate-spin" /> : t("mirror.create") || "Create"}
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
