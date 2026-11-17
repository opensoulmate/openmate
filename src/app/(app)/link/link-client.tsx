"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  Link2, RefreshCw, Plus, Trash2, Play, Pause,
  Webhook, Globe, Plug, Activity, Settings, Send,
  Clock, Filter,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface Connector {
  connector_id: string;
  name: string;
  type: string;
  status: string;
  endpoint: string;
  created_at: number;
  event_count: number;
}

interface LinkEvent {
  event_id: string;
  connector_id: string;
  connector_name?: string;
  direction: string;
  event_type: string;
  payload_summary: string;
  status: string;
  timestamp: number;
}

export function LinkClient() {
  const { t } = useTranslation();
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [selected, setSelected] = useState<Connector | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState("webhook_in");
  const [newEndpoint, setNewEndpoint] = useState("");
  const [newSecret, setNewSecret] = useState("");
  const [testPayload, setTestPayload] = useState("{}");
  const [testResult, setTestResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [events, setEvents] = useState<LinkEvent[]>([]);
  const [showEvents, setShowEvents] = useState(false);
  const [eventFilter, setEventFilter] = useState("");
  const isMobile = useIsMobile();
  const apiBase = getApiBaseUrl();

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/link/health`);
      setHealth(await res.json());
    } catch {}
  }, [apiBase]);

  const fetchConnectors = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/link/connectors`);
      const data = await res.json();
      setConnectors(data.connectors || []);
    } catch {}
  }, [apiBase]);

  const fetchEvents = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (eventFilter) params.set("connector_id", eventFilter);
      const res = await fetch(`${apiBase}/api/link/events?${params}`);
      const data = await res.json();
      setEvents(data.events || []);
    } catch {}
  }, [apiBase, eventFilter]);

  useEffect(() => {
    fetchHealth();
    fetchConnectors();
  }, [fetchHealth, fetchConnectors]);

  useEffect(() => {
    if (showEvents) fetchEvents();
  }, [showEvents, fetchEvents]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/link/connectors`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newName,
          type: newType,
          endpoint: newEndpoint,
          secret: newSecret,
        }),
      });
      if (res.ok) {
        setShowCreate(false);
        setNewName(""); setNewEndpoint(""); setNewSecret("");
        fetchConnectors();
        fetchHealth();
      }
    } catch {} finally { setLoading(false); }
  };

  const handleDelete = async (id: string) => {
    if (!confirm(t("link.confirmDelete"))) return;
    try {
      await fetch(`${apiBase}/api/link/connectors/${id}`, { method: "DELETE" });
      setSelected(null);
      fetchConnectors();
      fetchHealth();
    } catch {}
  };

  const handleTest = async (id: string) => {
    try {
      const res = await fetch(`${apiBase}/api/link/connectors/${id}/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: testPayload,
      });
      setTestResult(await res.json());
    } catch {}
  };

  const handleSend = async (id: string) => {
    try {
      const res = await fetch(`${apiBase}/api/link/connectors/${id}/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: testPayload,
      });
      setTestResult(await res.json());
    } catch {}
  };

  const typeIcons: Record<string, React.ElementType> = {
    webhook_in: Webhook,
    webhook_out: Send,
    rest_api: Globe,
    oa_system: Plug,
    custom: Settings,
  };

  const typeColors: Record<string, string> = {
    webhook_in: "text-blue-500 bg-blue-500/10",
    webhook_out: "text-violet-500 bg-violet-500/10",
    rest_api: "text-emerald-500 bg-emerald-500/10",
    oa_system: "text-amber-500 bg-amber-500/10",
    custom: "text-muted-foreground bg-muted-foreground/10",
  };

  return (
      <PageLayout title="突触">
        
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 md:px-3 lg:px-6 py-3 md:py-4">
        <div className="flex items-center gap-2 md:gap-3">
          <Link2 size={18} className="text-teal-500" />
          <h1 className="text-base md:text-lg font-semibold">{t("link.title") || "Link · Integration Gateway"}</h1>
          <span className="rounded-full bg-teal-500/10 px-2 py-0.5 text-[10px] md:text-xs font-medium text-teal-500">
            {t("link.t69735")}
          </span>
        </div>
        <div className="flex items-center gap-1.5 md:gap-2">
          <button onClick={() => setShowEvents(!showEvents)}
            className={cn(
              "flex items-center gap-1 md:gap-1.5 rounded-lg px-2.5 md:px-3 py-1.5 text-xs md:text-xs lg:text-sm transition-colors",
              showEvents ? "bg-teal-500 text-white" : "border border-border hover:bg-muted"
            )}>
            <Activity size={14} /> <span className="hidden lg:inline">{t("link.t65547")}</span>
          </button>
          <button onClick={() => setShowCreate(true)}
            className="flex items-center gap-1 md:gap-1.5 rounded-lg bg-teal-500 px-2.5 md:px-3 py-1.5 text-xs md:text-xs lg:text-sm text-white hover:bg-teal-600">
            <Plus size={14} /> <span className="hidden lg:inline">{t("link.t73119")}</span>
          </button>
          <button onClick={() => { fetchHealth(); fetchConnectors(); }}
            className="flex items-center gap-1 md:gap-1.5 rounded-lg border border-border px-2.5 md:px-3 py-1.5 text-xs md:text-xs lg:text-sm hover:bg-muted">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 lg:p-4 md:p-3 lg:p-6 space-y-4 md:space-y-3 lg:space-y-6">
        {/* Stats */}
        {health && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("link.t81176")}</span>
              <p className="text-xl lg:text-2xl font-bold">{health.total_connectors || 0}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("link.active")}</span>
              <p className="text-xl lg:text-2xl font-bold text-emerald-500">{health.active || 0}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("link.t93835")}</span>
              <p className="text-xl lg:text-2xl font-bold">{health.total_events || 0}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("link.type")}</span>
              <p className="text-xs font-mono mt-1">{Object.entries(health.by_type || {}).map(([k, v]) => `${k}:${v}`).join(" · ")}</p>
            </div>
          </div>
        )}

        {/* Event Log Panel */}
        {showEvents && (
          <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs lg:text-sm font-medium flex items-center gap-2">
                <Activity size={14} className="text-teal-500" />
                {t("link.t65547")}
              </h3>
              <div className="flex items-center gap-2">
                <div className="flex items-center gap-1">
                  <Filter size={12} className="text-muted-foreground" />
                  <select
                    value={eventFilter}
                    onChange={(e) => setEventFilter(e.target.value)}
                    className="rounded border border-border bg-background px-2 py-1 text-xs"
                  >
                    <option value="">{t("link.t32774")}</option>
                    {connectors.map((c) => (
                      <option key={c.connector_id} value={c.connector_id}>{c.name}</option>
                    ))}
                  </select>
                </div>
                <button onClick={fetchEvents}
                  className="rounded p-1 text-muted-foreground hover:bg-muted">
                  <RefreshCw size={12} />
                </button>
              </div>
            </div>
            {events.length === 0 ? (
              <p className="text-xs text-muted-foreground text-center py-4">{t("link.noEvents")}</p>
            ) : (
              <div className="max-h-60 overflow-y-auto space-y-1">
                {events.map((e) => (
                  <div key={e.event_id} className="flex items-center gap-3 rounded-lg px-3 py-2 hover:bg-muted/50">
                    <span className={cn(
                      "text-[10px] font-medium px-1.5 py-0.5 rounded",
                      e.direction === "inbound" ? "bg-blue-500/10 text-blue-500" : "bg-violet-500/10 text-violet-500"
                    )}>
                      {e.direction === "inbound" ? t("link.t27996") : t("link.t65703")}
                    </span>
                    <span className="text-xs font-medium">{e.connector_name || e.connector_id}</span>
                    <span className="text-xs text-muted-foreground truncate flex-1">{e.payload_summary}</span>
                    <span className={cn(
                      "text-[10px]",
                      e.status === "success" ? "text-emerald-500" : "text-red-500"
                    )}>
                      {e.status}
                    </span>
                    <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                      <Clock size={10} />
                      {new Date(e.timestamp * 1000).toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="flex gap-2 lg:gap-6 relative">
          {/* Connector List — full width on mobile, w-80 on desktop */}
          <div className={`${isMobile ? (selected ? "hidden" : "w-full") : "w-80"} space-y-3`}>
            {isMobile && (
              <div className="flex items-center justify-between pb-2 border-b border-border">
                <h3 className="text-xs lg:text-sm font-medium">{t("link.connectors") || "Connectors"}</h3>
                <span className="text-xs text-muted-foreground">{connectors.length}</span>
              </div>
            )}
            {connectors.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Plug size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("link.noConnectors")}</p>
              </div>
            ) : connectors.map((c) => {
              const Icon = typeIcons[c.type] || Plug;
              const colorCls = typeColors[c.type] || "text-muted-foreground bg-muted-foreground/10";
              return (
                <div key={c.connector_id}
                  onClick={() => setSelected(c)}
                  className={cn(
                    "rounded-xl border border-border bg-card p-3 lg:p-4 cursor-pointer transition-all hover:shadow-md",
                    selected?.connector_id === c.connector_id && "ring-2 ring-teal-500"
                  )}>
                  <div className="flex items-center gap-2 mb-1">
                    <div className={cn("rounded-lg p-1.5", colorCls)}><Icon size={14} /></div>
                    <span className="font-medium text-xs lg:text-sm">{c.name}</span>
                    <span className={cn("text-xs ml-auto", c.status === "active" ? "text-emerald-500" : "text-muted-foreground")}>
                      {c.status}
                    </span>
                  </div>
                  <div className="text-xs text-muted-foreground font-mono truncate">{c.endpoint || t("link.t81292")}</div>
                  <div className="text-xs text-muted-foreground mt-1">{t("link.events")}: {c.event_count}</div>
                </div>
              );
            })}
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
                        <h3 className="font-semibold">{selected.name}</h3>
                        <div className="flex gap-2">
                          <button onClick={() => handleTest(selected.connector_id)}
                            className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                            <Activity size={12} /> {t("link.test")}
                          </button>
                          {(selected.type === "webhook_out" || selected.type === "rest_api") && (
                            <button onClick={() => handleSend(selected.connector_id)}
                              className="flex items-center gap-1 rounded-lg border border-teal-500/30 px-3 py-1.5 text-xs text-teal-600 hover:bg-teal-500/10">
                              <Send size={12} /> {t("link.send")}
                            </button>
                          )}
                          <button onClick={() => handleDelete(selected.connector_id)}
                            className="flex items-center gap-1 rounded-lg border border-red-500/30 px-3 py-1.5 text-xs text-red-500 hover:bg-red-500/10">
                            <Trash2 size={12} /> {t("link.delete")}
                          </button>
                        </div>
                      </div>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 lg:gap-4 text-xs lg:text-sm">
                        <div><span className="text-muted-foreground">{t("link.t10867")}</span> {selected.type}</div>
                        <div><span className="text-muted-foreground">{t("link.t50013")}</span> {selected.status}</div>
                        <div className="col-span-2"><span className="text-muted-foreground">端点：</span> <span className="font-mono text-xs">{selected.endpoint}</span></div>
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground">{t("link.t90867")}</label>
                        <textarea value={testPayload} onChange={(e) => setTestPayload(e.target.value)}
                          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm font-mono min-h-[80px] mt-1" />
                      </div>
                      {testResult && (
                        <div className="rounded-lg border border-border bg-muted p-3">
                          <pre className="text-xs">{JSON.stringify(testResult, null, 2)}</pre>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            )
          ) : (
            selected && (
              <div className="flex-1 space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold">{selected.name}</h3>
                  <div className="flex gap-2">
                    <button onClick={() => handleTest(selected.connector_id)}
                      className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                      <Activity size={12} /> {t("link.test")}
                    </button>
                    {(selected.type === "webhook_out" || selected.type === "rest_api") && (
                      <button onClick={() => handleSend(selected.connector_id)}
                        className="flex items-center gap-1 rounded-lg border border-teal-500/30 px-3 py-1.5 text-xs text-teal-600 hover:bg-teal-500/10">
                        <Send size={12} /> {t("link.send")}
                      </button>
                    )}
                    <button onClick={() => handleDelete(selected.connector_id)}
                      className="flex items-center gap-1 rounded-lg border border-red-500/30 px-3 py-1.5 text-xs text-red-500 hover:bg-red-500/10">
                      <Trash2 size={12} /> {t("link.delete")}
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 lg:gap-4 text-xs lg:text-sm">
                  <div><span className="text-muted-foreground">{t("link.t10867")}</span> {selected.type}</div>
                  <div><span className="text-muted-foreground">{t("link.t50013")}</span> {selected.status}</div>
                  <div className="col-span-2"><span className="text-muted-foreground">端点：</span> <span className="font-mono text-xs">{selected.endpoint}</span></div>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">{t("link.t90867")}</label>
                  <textarea value={testPayload} onChange={(e) => setTestPayload(e.target.value)}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm font-mono min-h-[80px] mt-1" />
                </div>
                {testResult && (
                  <div className="rounded-lg border border-border bg-muted p-3">
                    <pre className="text-xs">{JSON.stringify(testResult, null, 2)}</pre>
                  </div>
                )}
              </div>
            )
          )}
        </div>

        {/* Create Modal */}
        {showCreate && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
            <div className="w-full max-w-md rounded-xl border border-border bg-card p-3 lg:p-6 space-y-4">
              <h3 className="font-semibold">{t("link.t30523")}</h3>
              <input value={newName} onChange={(e) => setNewName(e.target.value)}
                placeholder={t("link.connectorName")} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
              <select value={newType} onChange={(e) => setNewType(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm">
                <option value="webhook_in">Webhook ({t("link.t27996")})</option>
                <option value="webhook_out">Webhook ({t("link.t65703")})</option>
                <option value="rest_api">REST API</option>
                <option value="oa_system">{t("link.oaSystem")}</option>
                <option value="custom">{t("link.custom")}</option>
              </select>
              <input value={newEndpoint} onChange={(e) => setNewEndpoint(e.target.value)}
                placeholder={t("link.endpointUrlPlaceholder")} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
              <input value={newSecret} onChange={(e) => setNewSecret(e.target.value)}
                placeholder={t("link.t09336")} type="password" className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
              <div className="flex justify-end gap-2">
                <button onClick={() => setShowCreate(false)}
                  className="rounded-lg border border-border px-4 py-2 text-xs lg:text-sm hover:bg-muted">{t("common.cancel")}</button>
                <button onClick={handleCreate} disabled={loading}
                  className="rounded-lg bg-teal-500 px-4 py-2 text-xs lg:text-sm text-white hover:bg-teal-600 disabled:opacity-50">
                  {loading ? t("link.creating") : t("link.create")}
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
