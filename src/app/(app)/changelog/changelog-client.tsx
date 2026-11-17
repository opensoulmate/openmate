"use client";
import { useState, useEffect, useCallback } from "react";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import { useTranslation } from "react-i18next";
import {
  GitBranch, RefreshCw, Package, ArrowRight, Clock,
  CheckCircle, AlertTriangle, Search, Filter, ChevronDown,
  Layers, Link2, Database,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface Component {
  component_id: string;
  component_name: string;
  current_version: string;
  status: string;
  total_versions: number;
  dependencies: Record<string, string>;
  created_at: number;
}

interface ChangelogEntry {
  component_id: string;
  version: string;
  changes: string[];
  timestamp: number;
}

interface HealthData {
  status: string;
  registry: {
    total_components: number;
    total_versions: number;
    version_statuses: Record<string, number>;
    total_changelog_entries: number;
    platform_version: string;
  };
  migrations: {
    total_components: number;
    applied_scripts: number;
    pending_scripts: number;
  };
}

export function ChangelogClient() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const [tab, setTab] = useState<"components" | "changelog" | "dependencies">("components");
  const [components, setComponents] = useState<Component[]>([]);
  const [changelog, setChangelog] = useState<ChangelogEntry[]>([]);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [search, setSearch] = useState("");
  const [filterStatus, setFilterStatus] = useState<string>("all");

  const fetchComponents = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/heredity/components`);
      const data = await res.json();
      setComponents(data.components || []);
    } catch {}
  }, [apiBase]);

  const fetchChangelog = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/heredity/changelog?limit=100`);
      const data = await res.json();
      setChangelog(data.changelog || data.entries || []);
    } catch {}
  }, [apiBase]);

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/heredity/health`);
      setHealth(await res.json());
    } catch {}
  }, [apiBase]);

  useEffect(() => {
    fetchComponents();
    fetchHealth();
    if (tab === "changelog") fetchChangelog();
  }, [tab, fetchComponents, fetchHealth, fetchChangelog]);

  const filtered = components.filter((c) => {
    if (search && !c.component_name.toLowerCase().includes(search.toLowerCase()) &&
        !c.component_id.toLowerCase().includes(search.toLowerCase())) return false;
    if (filterStatus !== "all" && c.status !== filterStatus) return false;
    return true;
  });

  // Build dependency edges
  const edges: Array<{ from: string; to: string; constraint: string }> = [];
  components.forEach((c) => {
    Object.entries(c.dependencies || {}).forEach(([dep, constraint]) => {
      edges.push({ from: c.component_id, to: dep, constraint });
    });
  });

  const tabs = [
    { id: "components" as const, label: t("heredity.components", "Components"), icon: Package },
    { id: "changelog" as const, label: t("heredity.changelog", "Changelog"), icon: Clock },
    { id: "dependencies" as const, label: t("heredity.dependencies", "Dependencies"), icon: Link2 },
  ];

  return (
      <PageLayout title="变更日志">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-3">
          <GitBranch size={20} className="text-emerald-500" />
          <h1 className="text-lg font-semibold">{t("heredity.title", "System Changelog")}</h1>
          {health && (
            <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-500">
              v{health.registry.platform_version} · {health.registry.total_components} components
            </span>
          )}
        </div>
        <button
          onClick={() => { fetchComponents(); fetchHealth(); if (tab === "changelog") fetchChangelog(); }}
          className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted"
        >
          <RefreshCw size={14} /> {t("heredity.refresh", "Refresh")}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 lg:p-6 space-y-3 lg:space-y-6">
        {/* Stats */}
        {health && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2 lg:gap-4">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("heredity.components", "Components")}</span>
              <p className="text-xl lg:text-2xl font-bold">{health.registry.total_components}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("heredity.versions", "Total Versions")}</span>
              <p className="text-xl lg:text-2xl font-bold">{health.registry.total_versions}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("heredity.changelogEntries", "Changelog Entries")}</span>
              <p className="text-xl lg:text-2xl font-bold">{health.registry.total_changelog_entries}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("heredity.active", "Active")}</span>
              <p className="text-xl lg:text-2xl font-bold text-emerald-500">
                {health.registry.version_statuses?.active || 0}
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("heredity.platformVersion", "Platform Version")}</span>
              <p className="text-xl lg:text-2xl font-bold">v{health.registry.platform_version}</p>
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-2">
          {tabs.map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={cn("flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs lg:text-sm",
                tab === t.id ? "bg-emerald-500/10 text-emerald-600 font-medium" : "hover:bg-muted text-muted-foreground")}>
              <t.icon size={14} /> {t.label}
            </button>
          ))}
        </div>

        {/* Search + Filter */}
        {tab === "components" && (
          <div className="flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1 max-w-sm">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t("heredity.searchComponents", "Search components...")}
                className="w-full rounded-lg border border-border bg-background pl-9 pr-3 py-2 text-xs lg:text-sm"
              />
            </div>
            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
              className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm"
            >
              <option value="all">{t("heredity.allStatus", "All Status")}</option>
              <option value="active">{t("heredity.active", "Active")}</option>
              <option value="deprecated">{t("heredity.deprecated", "Deprecated")}</option>
            </select>
          </div>
        )}

        {/* Components Tab */}
        {tab === "components" && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2 lg:gap-4">
            {filtered.map((c) => (
              <div key={c.component_id} className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3 hover:border-primary/20 transition-colors">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Package size={16} className="text-primary" />
                    <span className="font-medium text-xs lg:text-sm">{c.component_name}</span>
                  </div>
                  <span className={cn("text-xs px-2 py-0.5 rounded-full",
                    c.status === "active" ? "bg-emerald-500/10 text-emerald-500" : "bg-muted text-muted-foreground")}>
                    {c.status}
                  </span>
                </div>
                <div className="flex items-center gap-2 lg:gap-4 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <Layers size={12} /> v{c.current_version}
                  </span>
                  <span className="flex items-center gap-1">
                    <Clock size={12} /> {c.total_versions} version{c.total_versions !== 1 ? "s" : ""}
                  </span>
                </div>
                {Object.keys(c.dependencies).length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(c.dependencies).map(([dep, constraint]) => (
                      <span key={dep} className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground">
                        {dep} {constraint}
                      </span>
                    ))}
                  </div>
                )}
                <p className="text-[10px] text-muted-foreground">
                  Registered: {new Date(c.created_at * 1000).toLocaleDateString(undefined)}
                </p>
              </div>
            ))}
          </div>
        )}

        {/* Changelog Tab */}
        {tab === "changelog" && (
          <div className="space-y-3">
            {changelog.length === 0 ? (
              <div className="rounded-xl border border-border bg-card p-8 text-center">
                <Clock size={32} className="mx-auto mb-3 text-muted-foreground" />
                <p className="text-xs lg:text-sm text-muted-foreground">{t("heredity.noChangelog", "No changelog entries yet")}</p>
                <p className="text-xs text-muted-foreground mt-1">{t("heredity.versionChanges", "Version changes will appear here")}</p>
              </div>
            ) : (
              <div className="rounded-xl border border-border overflow-x-auto">
                <table className="w-full text-xs lg:text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/30">
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground text-xs">{t("heredity.components", "Component")}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground text-xs">{t("heredity.version", "Version")}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground text-xs">{t("heredity.changes", "Changes")}</th>
                      <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground text-xs">{t("heredity.date", "Date")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {changelog.map((entry, i) => (
                      <tr key={i} className="border-b border-border last:border-0 hover:bg-muted/30">
                        <td className="px-2 lg:px-4 py-2.5 text-xs font-medium">{entry.component_id}</td>
                        <td className="px-2 lg:px-4 py-2.5 text-xs font-mono">v{entry.version}</td>
                        <td className="px-2 lg:px-4 py-2.5 text-xs">
                          {entry.changes?.map((change, j) => (
                            <span key={j} className="inline-block rounded bg-muted px-1.5 py-0.5 text-[10px] mr-1 mb-0.5">
                              {change}
                            </span>
                          )) || "—"}
                        </td>
                        <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground">
                          {new Date(entry.timestamp * 1000).toLocaleDateString(undefined)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Dependencies Tab */}
        {tab === "dependencies" && (
          <div className="space-y-4">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <h3 className="text-xs lg:text-sm font-medium mb-3">{t("heredity.dependencyGraph", "Dependency Graph")}</h3>
              <div className="space-y-2">
                {edges.map((edge, i) => {
                  const fromComp = components.find((c) => c.component_id === edge.from);
                  const toComp = components.find((c) => c.component_id === edge.to);
                  return (
                    <div key={i} className="flex items-center gap-2 text-xs">
                      <span className="rounded bg-primary/10 px-2 py-1 font-medium text-primary">
                        {fromComp?.component_name || edge.from}
                      </span>
                      <ArrowRight size={12} className="text-muted-foreground" />
                      <span className="rounded bg-emerald-500/10 px-2 py-1 font-medium text-emerald-600">
                        {toComp?.component_name || edge.to}
                      </span>
                      <span className="font-mono text-muted-foreground">{edge.constraint}</span>
                    </div>
                  );
                })}
              </div>
              {edges.length === 0 && (
                <p className="text-xs lg:text-sm text-muted-foreground text-center py-4">{t("heredity.noDeps", "No dependencies registered")}</p>
              )}
            </div>

            {/* Dependency summary */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2 lg:gap-4">
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <h3 className="text-xs lg:text-sm font-medium mb-3">{t("heredity.mostDepended", "Most Depended On")}</h3>
                <div className="space-y-2">
                  {Object.entries(
                    edges.reduce((acc, e) => { acc[e.to] = (acc[e.to] || 0) + 1; return acc; }, {} as Record<string, number>)
                  )
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 5)
                    .map(([dep, count]) => {
                      const comp = components.find((c) => c.component_id === dep);
                      return (
                        <div key={dep} className="flex items-center justify-between">
                          <span className="text-xs font-medium">{comp?.component_name || dep}</span>
                          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                            {count} {t("heredity.dependent", "dependent")}
                          </span>
                        </div>
                      );
                    })}
                </div>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <h3 className="text-xs lg:text-sm font-medium mb-3">{t("heredity.independent", "Independent Components")}</h3>
                <div className="space-y-2">
                  {components
                    .filter((c) => Object.keys(c.dependencies).length === 0)
                    .map((c) => (
                      <div key={c.component_id} className="flex items-center gap-2">
                        <CheckCircle size={12} className="text-emerald-500" />
                        <span className="text-xs">{c.component_name}</span>
                      </div>
                    ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
