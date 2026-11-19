"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  RefreshCw, Plus, Trash2, Home, Shield, Users,
  Settings, BarChart3, Loader2, AlertTriangle,
  CheckCircle, XCircle, Search, Activity, PanelRightOpen,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface Tenant {
  tenant_id: string;
  name: string;
  tier: string;
  status: string;
  namespace: string;
  owner_user_id: string;
  description: string;
  tags: string[];
  created_at: number;
  usage_percent: Record<string, number>;
}

interface Policy {
  resource_type: string;
  namespace_scoped: boolean;
  cross_tenant_allowed: boolean;
  encryption_enabled: boolean;
  audit_access: boolean;
}

interface NestStats {
  tenants: {
    total_tenants: number;
    by_tier: Record<string, number>;
    by_status: Record<string, number>;
    total_storage_bytes: number;
    total_documents: number;
  };
  isolation: {
    total_access_checks: number;
    blocked_attempts: number;
    block_rate: number;
    policies_count: number;
  };
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

function tierColor(tier: string): string {
  if (tier === "enterprise") return "text-amber-500 bg-amber-500/10";
  if (tier === "pro") return "text-indigo-500 bg-indigo-500/10";
  return "text-muted-foreground bg-muted";
}

function statusIcon(s: string) {
  if (s === "active") return <CheckCircle size={14} className="text-emerald-500" />;
  if (s === "suspended") return <XCircle size={14} className="text-red-500" />;
  if (s === "trial") return <Activity size={14} className="text-amber-500" />;
  return <AlertTriangle size={14} className="text-muted-foreground" />;
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

export function NestClient() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"tenants" | "policies" | "audit">("tenants");
  const [stats, setStats] = useState<NestStats | null>(null);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [auditLog, setAuditLog] = useState<any[]>([]);
  const [selected, setSelected] = useState<Tenant | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [loading, setLoading] = useState(false);
  const isMobile = useIsMobile();

  // Create form
  const [newName, setNewName] = useState("");
  const [newTier, setNewTier] = useState("free");
  const [newDesc, setNewDesc] = useState("");
  const [newOwner, setNewOwner] = useState("");

  // Quota check
  const [quotaResource, setQuotaResource] = useState("storage");
  const [quotaResult, setQuotaResult] = useState<any>(null);

  const apiBase = getApiBaseUrl();

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/nest/health`);
      setStats(await res.json());
    } catch {}
  }, [apiBase]);

  const fetchTenants = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/nest/tenants`);
      const data = await res.json();
      setTenants(data.tenants || []);
    } catch {}
  }, [apiBase]);

  const fetchPolicies = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/nest/policies`);
      const data = await res.json();
      setPolicies(data.policies || []);
    } catch {}
  }, [apiBase]);

  const fetchAudit = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/nest/audit?limit=50`);
      const data = await res.json();
      setAuditLog(data.entries || []);
    } catch {}
  }, [apiBase]);

  useEffect(() => {
    fetchStats();
    fetchTenants();
  }, [fetchStats, fetchTenants]);

  useEffect(() => {
    if (tab === "policies") fetchPolicies();
    if (tab === "audit") fetchAudit();
  }, [tab, fetchPolicies, fetchAudit]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/nest/tenants`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newName, tier: newTier, description: newDesc, owner_user_id: newOwner,
        }),
      });
      if (res.ok) {
        setShowCreate(false);
        setNewName(""); setNewDesc(""); setNewOwner("");
        fetchTenants(); fetchStats();
      }
    } catch {} finally { setLoading(false); }
  };

  const handleSuspend = async (id: string) => {
    try {
      await fetch(`${apiBase}/api/nest/tenants/${id}/suspend`, { method: "POST" });
      fetchTenants(); fetchStats();
    } catch {}
  };

  const handleReactivate = async (id: string) => {
    try {
      await fetch(`${apiBase}/api/nest/tenants/${id}/reactivate`, { method: "POST" });
      fetchTenants(); fetchStats();
    } catch {}
  };

  const handleDelete = async (id: string) => {
    if (!confirm(t('nest.t86075'))) return;
    try {
      await fetch(`${apiBase}/api/nest/tenants/${id}`, { method: "DELETE" });
      setSelected(null);
      fetchTenants(); fetchStats();
    } catch {}
  };

  const handleQuotaCheck = async (tenantId: string) => {
    try {
      const res = await fetch(`${apiBase}/api/nest/tenants/${tenantId}/quota/check`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resource: quotaResource, amount: 1 }),
      });
      setQuotaResult(await res.json());
    } catch {}
  };

  const tabs = [
    { id: "tenants" as const, label: t('nest.t67067'), icon: Users },
    { id: "policies" as const, label: t('nest.t05314'), icon: Shield },
    { id: "audit" as const, label: t('nest.t36516'), icon: BarChart3 },
  ];

  return (
      <PageLayout title="巢穴">
        
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-rose-500/10">
            <Home size={20} className="text-rose-500" />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg lg:text-xl font-semibold truncate">{t('nest.text4')}</h1>
            <p className="text-xs lg:text-sm text-muted-foreground truncate">{t('organTag.nest')}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 rounded-lg bg-rose-500 px-3 py-1.5 text-xs lg:text-sm text-white hover:bg-rose-600">
            <Plus size={14} /> {t('nest.createTenant1')}
          </button>
          <button onClick={() => { fetchStats(); fetchTenants(); }}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4 lg:py-6 space-y-3 lg:space-y-6">
        {/* Stats */}
        {stats && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t('nest.text6')}</span>
              <p className="text-xl lg:text-2xl font-bold">{stats.tenants.total_tenants}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t('nest.text7')}</span>
              <p className="text-xl lg:text-2xl font-bold text-indigo-500">{formatBytes(stats.tenants.total_storage_bytes)}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t('nest.text8')}</span>
              <p className="text-xl lg:text-2xl font-bold text-emerald-500">{stats.tenants.total_documents.toLocaleString()}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t('nest.text9')}</span>
              <p className="text-xl lg:text-2xl font-bold text-amber-500">{stats.isolation.blocked_attempts}</p>
              <span className="text-xs text-muted-foreground">{t('nest.blockRate', { rate: stats.isolation.block_rate }) || `拦截率 ${stats.isolation.block_rate}%`}</span>
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-2">
          {tabs.map((tabItem) => (
            <button key={tabItem.id} onClick={() => setTab(tabItem.id)}
              className={cn("flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs lg:text-sm transition-colors",
                tab === tabItem.id ? "bg-rose-500/10 text-rose-600 font-medium" : "hover:bg-muted text-muted-foreground")}>
              <tabItem.icon size={14} /> {tabItem.label}
            </button>
          ))}
        </div>

        {/* Tenants Tab */}
        {tab === "tenants" && (
          <div className="flex gap-2 lg:gap-6 relative">
            <div className={`flex-1 space-y-3 ${isMobile ? 'w-full' : ''}`}>
              {tenants.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                  <Users size={40} className="mb-3 opacity-30" />
                  <p className="text-xs lg:text-sm">{t('nest.empty')}</p>
                </div>
              ) : tenants.map((tenant) => (
                <div key={tenant.tenant_id}
                  onClick={() => { setSelected(tenant); setQuotaResult(null); }}
                  className={cn(
                    "rounded-xl border border-border bg-card p-3 lg:p-4 cursor-pointer transition-all hover:shadow-md",
                    selected?.tenant_id === tenant.tenant_id && "ring-2 ring-rose-500"
                  )}>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      {statusIcon(tenant.status)}
                      <span className="font-medium text-xs lg:text-sm">{tenant.name}</span>
                      <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium", tierColor(tenant.tier))}>
                        {tenant.tier}
                      </span>
                    </div>
                    <span className="text-xs text-muted-foreground">{formatTime(tenant.created_at)}</span>
                  </div>
                  <div className="flex gap-2 lg:gap-4 text-xs text-muted-foreground">
                    <span>NS: {tenant.namespace}</span>
                    <span>{t('nest.status2')} {tenant.status}</span>
                    {tenant.tags.length > 0 && tenant.tags.map((tag) => (
                      <span key={tag} className="rounded-full bg-rose-500/10 px-1.5 py-0.5 text-rose-500">{tag}</span>
                    ))}
                  </div>
                  {/* Usage bars */}
                  {tenant.usage_percent && (
                    <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2">
                      {Object.entries(tenant.usage_percent).map(([key, pct]) => (
                        <div key={key}>
                          <div className="flex justify-between text-[10px] text-muted-foreground mb-0.5">
                            <span>{key}</span>
                            <span>{pct}%</span>
                          </div>
                          <div className="h-1 rounded-full bg-muted overflow-hidden">
                            <div className={cn("h-full rounded-full",
                              pct > 80 ? "bg-red-500" : pct > 50 ? "bg-amber-500" : "bg-emerald-500")}
                              style={{ width: `${Math.min(pct, 100)}%` }} />
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
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
                        <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-3">
                          <div className="flex items-center justify-between">
                            <h3 className="font-semibold text-xs lg:text-sm">{selected.name}</h3>
                            <div className="flex gap-1">
                              {selected.status === "active" ? (
                                <button onClick={() => handleSuspend(selected.tenant_id)}
                                  className="rounded-md p-1.5 text-amber-500 hover:bg-amber-500/10" title={t('nest.suspend')}>
                                  <AlertTriangle size={14} />
                                </button>
                              ) : (
                                <button onClick={() => handleReactivate(selected.tenant_id)}
                                  className="rounded-md p-1.5 text-emerald-500 hover:bg-emerald-500/10" title={t('nest.activate')}>
                                  <CheckCircle size={14} />
                                </button>
                              )}
                              <button onClick={() => handleDelete(selected.tenant_id)}
                                className="rounded-md p-1.5 text-red-500 hover:bg-red-500/10" title={t('nest.delete3')}>
                                <Trash2 size={14} />
                              </button>
                            </div>
                          </div>
                          <div className="space-y-2 text-xs">
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">ID</span>
                              <span className="font-mono">{selected.tenant_id}</span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">{t('nest.text13')}</span>
                              <span className="font-mono">{selected.namespace}</span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">{t('nest.text14')}</span>
                              <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium", tierColor(selected.tier))}>
                                {selected.tier}
                              </span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">{t('nest.status')}</span>
                              <span>{selected.status}</span>
                            </div>
                            {selected.description && (
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">{t('nest.text15')}</span>
                                <span className="text-right max-w-[160px] truncate">{selected.description}</span>
                              </div>
                            )}
                          </div>
                        </div>

                        {/* Quota Check */}
                        <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-3">
                          <h4 className="text-xs lg:text-sm font-medium">{t('nest.text16')}</h4>
                          <div className="flex gap-2">
                            <select value={quotaResource} onChange={(e) => setQuotaResource(e.target.value)}
                              className="flex-1 rounded-lg border border-border bg-background px-2 py-1.5 text-xs">
                              <option value="storage">{t('nest.text17')}</option>
                              <option value="documents">{t('nest.text18')}</option>
                              <option value="api_calls">{t('nest.apiCalls')}</option>
                              <option value="tokens">Token</option>
                              <option value="agents">Agent</option>
                            </select>
                            <button onClick={() => handleQuotaCheck(selected.tenant_id)}
                              className="rounded-lg bg-rose-500 px-3 py-1.5 text-xs text-white hover:bg-rose-600">
                              {t('nest.checkBtn')}
                            </button>
                          </div>
                          {quotaResult && (
                            <div className={cn("rounded-lg p-3 text-xs",
                              quotaResult.allowed ? "bg-emerald-500/10 text-emerald-500" : "bg-red-500/10 text-red-500")}>
                              <p>{quotaResult.allowed ? t('nest.text20') : t('nest.text21')}</p>
                              {quotaResult.current !== undefined && (
                                <p>{t('nest.currentLimit', { current: quotaResult.current, limit: quotaResult.limit, percent: quotaResult.percent }) || `当前: ${quotaResult.current} / 上限: ${quotaResult.limit} (${quotaResult.percent}%)`}</p>
                              )}
                            </div>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                </div>
              )
            ) : (
              selected && (
                <div className="w-80 space-y-4">
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <h3 className="font-semibold text-xs lg:text-sm">{selected.name}</h3>
                      <div className="flex gap-1">
                        {selected.status === "active" ? (
                          <button onClick={() => handleSuspend(selected.tenant_id)}
                            className="rounded-md p-1.5 text-amber-500 hover:bg-amber-500/10" title={t('nest.suspend')}>
                            <AlertTriangle size={14} />
                          </button>
                        ) : (
                          <button onClick={() => handleReactivate(selected.tenant_id)}
                            className="rounded-md p-1.5 text-emerald-500 hover:bg-emerald-500/10" title={t('nest.activate')}>
                            <CheckCircle size={14} />
                          </button>
                        )}
                        <button onClick={() => handleDelete(selected.tenant_id)}
                          className="rounded-md p-1.5 text-red-500 hover:bg-red-500/10" title={t('nest.delete3')}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                    <div className="space-y-2 text-xs">
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">ID</span>
                        <span className="font-mono">{selected.tenant_id}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">{t('nest.text13')}</span>
                        <span className="font-mono">{selected.namespace}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">{t('nest.text14')}</span>
                        <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium", tierColor(selected.tier))}>
                          {selected.tier}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">{t('nest.status')}</span>
                        <span>{selected.status}</span>
                      </div>
                      {selected.description && (
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">{t('nest.text15')}</span>
                          <span className="text-right max-w-[160px] truncate">{selected.description}</span>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Quota Check */}
                  <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-3">
                    <h4 className="text-xs lg:text-sm font-medium">{t('nest.text16')}</h4>
                    <div className="flex gap-2">
                      <select value={quotaResource} onChange={(e) => setQuotaResource(e.target.value)}
                        className="flex-1 rounded-lg border border-border bg-background px-2 py-1.5 text-xs">
                        <option value="storage">{t('nest.text17')}</option>
                        <option value="documents">{t('nest.text18')}</option>
                        <option value="api_calls">{t('nest.apiCalls')}</option>
                        <option value="tokens">Token</option>
                        <option value="agents">Agent</option>
                      </select>
                      <button onClick={() => handleQuotaCheck(selected.tenant_id)}
                        className="rounded-lg bg-rose-500 px-3 py-1.5 text-xs text-white hover:bg-rose-600">
                        {t('nest.checkBtn')}
                      </button>
                    </div>
                    {quotaResult && (
                      <div className={cn("rounded-lg p-3 text-xs",
                        quotaResult.allowed ? "bg-emerald-500/10 text-emerald-500" : "bg-red-500/10 text-red-500")}>
                        <p>{quotaResult.allowed ? t('nest.text20') : t('nest.text21')}</p>
                        {quotaResult.current !== undefined && (
                          <p>{t('nest.currentLimit', { current: quotaResult.current, limit: quotaResult.limit, percent: quotaResult.percent }) || `当前: ${quotaResult.current} / 上限: ${quotaResult.limit} (${quotaResult.percent}%)`}</p>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )
            )}
          </div>
        )}

        {/* Policies Tab */}
        {tab === "policies" && (
          <div className="space-y-3">
            {policies.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <Shield size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t('nest.text23')}</p>
              </div>
            ) : (
              <div className="rounded-xl border border-border overflow-x-auto">
                <table className="w-full text-xs lg:text-sm min-w-[450px]">
                  <thead>
                    <tr className="border-b border-border bg-muted/30">
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t('nest.type')}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t('nest.text24')}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t('nest.text25')}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t('nest.text26')}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t('nest.text27')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {policies.map((p) => (
                      <tr key={p.resource_type} className="border-b border-border last:border-0 hover:bg-muted/30">
                        <td className="px-2 lg:px-4 py-2.5 font-mono text-xs">{p.resource_type}</td>
                        <td className="px-2 lg:px-4 py-2.5">
                          {p.namespace_scoped ? <CheckCircle size={14} className="text-emerald-500" /> : <XCircle size={14} className="text-muted-foreground" />}
                        </td>
                        <td className="px-2 lg:px-4 py-2.5">
                          {p.cross_tenant_allowed ? <CheckCircle size={14} className="text-amber-500" /> : <XCircle size={14} className="text-emerald-500" />}
                        </td>
                        <td className="px-2 lg:px-4 py-2.5">
                          {p.encryption_enabled ? <CheckCircle size={14} className="text-emerald-500" /> : <XCircle size={14} className="text-muted-foreground" />}
                        </td>
                        <td className="px-2 lg:px-4 py-2.5">
                          {p.audit_access ? <CheckCircle size={14} className="text-emerald-500" /> : <XCircle size={14} className="text-muted-foreground" />}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Audit Tab */}
        {tab === "audit" && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <button onClick={fetchAudit}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted">
                <RefreshCw size={12} /> {t('nest.refresh')}
              </button>
              <span className="text-xs text-muted-foreground">{t('nest.records', { count: auditLog.length }) || `${auditLog.length} 条记录`}</span>
            </div>
            {auditLog.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <BarChart3 size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t('nest.text29')}</p>
              </div>
            ) : (
              <div className="rounded-xl border border-border overflow-x-auto">
                <table className="w-full text-xs lg:text-sm min-w-[450px]">
                  <thead>
                    <tr className="border-b border-border bg-muted/30">
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t('nest.time')}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t('nest.text30')}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t('nest.text31')}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t('nest.action')}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t('nest.text32')}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t('nest.text33')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditLog.map((entry, i) => (
                      <tr key={i} className="border-b border-border last:border-0 hover:bg-muted/30">
                        <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground">{formatTime(entry.timestamp)}</td>
                        <td className="px-2 lg:px-4 py-2.5 font-mono text-xs">{entry.tenant_id?.slice(0, 12)}</td>
                        <td className="px-2 lg:px-4 py-2.5 text-xs">{entry.resource_type}</td>
                        <td className="px-2 lg:px-4 py-2.5 text-xs">{entry.action}</td>
                        <td className="px-2 lg:px-4 py-2.5">
                          {entry.allowed ? <CheckCircle size={14} className="text-emerald-500" /> : <XCircle size={14} className="text-red-500" />}
                        </td>
                        <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground max-w-[200px] truncate">{entry.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Create Modal */}
        {showCreate && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
            <div className="w-full max-w-md rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-4">
              <h3 className="font-semibold">{t('nest.text5')}</h3>
              <input value={newName} onChange={(e) => setNewName(e.target.value)}
                placeholder={t('nest.t67136')} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
              <select value={newTier} onChange={(e) => setNewTier(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm">
                <option value="free">{t('nest.free')}</option>
                <option value="pro">{t('nest.pro')}</option>
                <option value="enterprise">{t('nest.enterprise')}</option>
              </select>
              <input value={newOwner} onChange={(e) => setNewOwner(e.target.value)}
                placeholder={t('nest.user')} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
              <input value={newDesc} onChange={(e) => setNewDesc(e.target.value)}
                placeholder={t('nest.text37')} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
              <div className="flex justify-end gap-2">
                <button onClick={() => setShowCreate(false)}
                  className="rounded-lg border border-border px-2 lg:px-4 py-2 text-xs lg:text-sm hover:bg-muted">{t('nest.cancel')}</button>
                <button onClick={handleCreate} disabled={loading}
                  className="rounded-lg bg-rose-500 px-2 lg:px-4 py-2 text-xs lg:text-sm text-white hover:bg-rose-600 disabled:opacity-50">
                  {loading ? <Loader2 size={14} className="animate-spin" /> : t('nest.create2')}
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
