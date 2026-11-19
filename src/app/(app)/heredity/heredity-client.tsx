"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  GitBranch, RefreshCw, Plus, Search, Play, RotateCcw,
  Activity, Settings, Database, FileText, History,
  CheckCircle, XCircle, Loader2, ChevronRight,
  ArrowUpCircle, AlertTriangle, Tag,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface ComponentInfo {
  component_id: string;
  component_name: string;
  current_version: string;
  status: string;
  total_versions: number;
  dependencies: Record<string, string>;
  created_at: number;
}

interface MigrationInfo {
  migration_id: string;
  component_id: string;
  from_version: string;
  to_version: string;
  status: string;
  dry_run: boolean;
  started_at: number | null;
  completed_at: number | null;
  error: string | null;
}

interface ChangelogEntry {
  entry_id: string;
  component_id: string;
  version: string;
  change_type: string;
  description: string;
  author: string;
  timestamp: number;
}

interface PlatformInfo {
  platform_version: string;
  total_components: number;
  total_migrations: number;
  total_changelog_entries: number;
  components: ComponentInfo[];
}

function formatTime(ts: number): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

const STATUS_COLORS: Record<string, string> = {
  active: "text-emerald-500 bg-emerald-500/10",
  deprecated: "text-yellow-500 bg-yellow-500/10",
  draft: "text-blue-500 bg-blue-500/10",
  retired: "text-red-500 bg-red-500/10",
  completed: "text-emerald-500 bg-emerald-500/10",
  pending: "text-yellow-500 bg-yellow-500/10",
  running: "text-blue-500 bg-blue-500/10",
  failed: "text-red-500 bg-red-500/10",
  rolled_back: "text-orange-500 bg-orange-500/10",
};

const CHANGE_ICONS: Record<string, React.ElementType> = {
  feature: Plus,
  fix: CheckCircle,
  breaking: AlertTriangle,
  deprecation: XCircle,
  security: Settings,
};

export function HeredityClient() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"components" | "migrations" | "changelog" | "platform">("components");
  const [platform, setPlatform] = useState<PlatformInfo | null>(null);
  const [components, setComponents] = useState<ComponentInfo[]>([]);
  const [migrations, setMigrations] = useState<MigrationInfo[]>([]);
  const [changelog, setChangelog] = useState<ChangelogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedComponent, setSelectedComponent] = useState<string | null>(null);
  const [componentDetail, setComponentDetail] = useState<any>(null);
  const [compatCheck, setCompatCheck] = useState<any>(null);

  // Create migration form
  const [migComponentId, setMigComponentId] = useState("");
  const [migFrom, setMigFrom] = useState("");
  const [migTo, setMigTo] = useState("");

  const apiBase = getApiBaseUrl();

  const fetchPlatform = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/heredity/platform`);
      const data = await res.json();
      setPlatform(data);
      setComponents(data.components || []);
    } catch {}
  }, [apiBase]);

  const fetchMigrations = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/heredity/migrations`);
      const data = await res.json();
      setMigrations(data.migrations || []);
    } catch {}
  }, [apiBase]);

  const fetchChangelog = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/heredity/changelog?limit=100`);
      const data = await res.json();
      setChangelog(data.changelog || []);
    } catch {}
  }, [apiBase]);

  const fetchComponentDetail = useCallback(async (id: string) => {
    try {
      const res = await fetch(`${apiBase}/api/heredity/components/${id}`);
      setComponentDetail(await res.json());
      const compRes = await fetch(`${apiBase}/api/heredity/compatibility/${id}`);
      setCompatCheck(await compRes.json());
    } catch {}
  }, [apiBase]);

  useEffect(() => { fetchPlatform(); }, [fetchPlatform]);
  useEffect(() => { if (tab === "migrations") fetchMigrations(); }, [tab, fetchMigrations]);
  useEffect(() => { if (tab === "changelog") fetchChangelog(); }, [tab, fetchChangelog]);
  useEffect(() => { if (selectedComponent) fetchComponentDetail(selectedComponent); }, [selectedComponent, fetchComponentDetail]);

  const handleCreateMigration = async () => {
    if (!migComponentId || !migFrom || !migTo) return;
    setLoading(true);
    try {
      await fetch(`${apiBase}/api/heredity/migrations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ component_id: migComponentId, from_version: migFrom, to_version: migTo }),
      });
      setMigComponentId(""); setMigFrom(""); setMigTo("");
      fetchMigrations();
    } catch {} finally { setLoading(false); }
  };

  const handleExecuteMigration = async (id: string) => {
    setLoading(true);
    try {
      await fetch(`${apiBase}/api/heredity/migrations/${id}/execute`, { method: "POST" });
      fetchMigrations(); fetchPlatform();
    } catch {} finally { setLoading(false); }
  };

  const handleRollbackMigration = async (id: string) => {
    setLoading(true);
    try {
      await fetch(`${apiBase}/api/heredity/migrations/${id}/rollback`, { method: "POST" });
      fetchMigrations();
    } catch {} finally { setLoading(false); }
  };

  const handleBumpPlatform = async (type: string) => {
    setLoading(true);
    try {
      await fetch(`${apiBase}/api/heredity/platform/bump?bump_type=${type}`, { method: "POST" });
      fetchPlatform(); fetchChangelog();
    } catch {} finally { setLoading(false); }
  };

  const filteredComponents = components.filter(c =>
    !searchQuery || c.component_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    c.component_id.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const tabs = [
    { id: "components" as const, label: t("heredity.components") || t('heredity.version'), icon: Database },
    { id: "migrations" as const, label: t("heredity.migrations") || t('heredity.text1'), icon: ArrowUpCircle },
    { id: "changelog" as const, label: t("heredity.changelog") || t('heredity.changelog'), icon: History },
    { id: "platform" as const, label: t("heredity.platform") || t('heredity.platformVersion'), icon: Settings },
  ];

  return (
      <PageLayout title="遗传链">
        
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-teal-500/10">
            <GitBranch size={18} className="text-teal-500 shrink-0" />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg lg:text-xl font-semibold truncate">{t('heredity.title') || t('heredity.text4')}</h1>
            <p className="text-xs lg:text-sm text-muted-foreground truncate">{t('organTag.heredity')}</p>
          </div>
          {platform && (
            <span className="rounded-full bg-muted px-1.5 lg:px-2 py-0.5 text-[10px] lg:text-xs text-muted-foreground shrink-0">
              v{platform.platform_version}
            </span>
          )}
        </div>
        <button onClick={() => { fetchPlatform(); tab === "migrations" && fetchMigrations(); tab === "changelog" && fetchChangelog(); }}
          className="flex items-center gap-1.5 rounded-lg border border-border px-2 lg:px-3 py-1.5 text-xs lg:text-sm hover:bg-muted transition-colors shrink-0 touch-manipulation">
          <RefreshCw size={14} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4 lg:py-6 space-y-3 lg:space-y-6">
        {/* Stats */}
        {platform && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
            <StatCard icon={Database} label={t('heredity.totalComponents') || t('heredity.text6')} value={String(platform.total_components)}
              sub={t('heredity.registered') || t('heredity.registered')} color="text-teal-500" bg="bg-teal-500/10" />
            <StatCard icon={ArrowUpCircle} label={t('heredity.totalMigrations') || t('heredity.t84905')} value={String(platform.total_migrations)}
              sub={t('heredity.plans') || t('heredity.text8')} color="text-blue-500" bg="bg-blue-500/10" />
            <StatCard icon={History} label={t('heredity.changelogEntries') || t('heredity.t39839')} value={String(platform.total_changelog_entries)}
              sub={t('heredity.entries') || t('heredity.text10')} color="text-violet-500" bg="bg-violet-500/10" />
            <StatCard icon={Tag} label={t('heredity.platformVersion') || t('heredity.platformVersion')} value={`v${platform.platform_version}`}
              sub={t('heredity.currentRelease') || t('heredity.currentRelease')} color="text-emerald-500" bg="bg-emerald-500/10" />
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-2">
          {tabs.map((tabItem) => (
            <button key={tabItem.id} onClick={() => setTab(tabItem.id)}
              className={cn("flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs lg:text-sm transition-colors",
                tab === tabItem.id ? "bg-teal-500/10 text-teal-600 font-medium" : "hover:bg-muted text-muted-foreground")}>
              <tabItem.icon size={14} /> {tabItem.label}
            </button>
          ))}
        </div>

        {/* Components Tab */}
        {tab === "components" && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 lg:gap-3">
              <div className="relative flex-1 max-w-xs">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t("heredity.searchComponents") || "Search components..."}
                  className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-teal-500/20" />
              </div>
              <span className="text-xs text-muted-foreground">{filteredComponents.length} {t('heredity.componentsCount') || t('heredity.t55429')}</span>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-4">
              {/* Component List */}
              <div className="space-y-2">
                {filteredComponents.map((c) => (
                  <button key={c.component_id}
                    onClick={() => setSelectedComponent(c.component_id)}
                    className={cn("w-full text-left rounded-xl border p-3 lg:p-4 transition-colors",
                      selectedComponent === c.component_id ? "border-teal-500/50 bg-teal-500/5" : "border-border bg-card hover:border-teal-500/30")}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="text-xs lg:text-sm font-medium">{c.component_name}</span>
                        <span className={cn("rounded-full px-2 py-0.5 text-[10px]", STATUS_COLORS[c.status] || "bg-muted")}>
                          {c.status}
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs lg:text-sm font-bold text-teal-500">v{c.current_version}</span>
                        <ChevronRight size={14} className="text-muted-foreground" />
                      </div>
                    </div>
                    <div className="mt-2 flex items-center gap-2 lg:gap-3 text-xs text-muted-foreground">
                      <span>ID: {c.component_id}</span>
                      <span>{c.total_versions} {t('heredity.versions') || t('heredity.versions')}</span>
                      <span>{Object.keys(c.dependencies).length} {t('heredity.deps') || t('heredity.dependencies')}</span>
                    </div>
                  </button>
                ))}
              </div>

              {/* Component Detail */}
              {componentDetail && (
                <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="text-xs lg:text-sm font-semibold">{componentDetail.component_name}</h3>
                    <span className="text-lg font-bold text-teal-500">v{componentDetail.current.version}</span>
                  </div>

                  {/* Compatibility */}
                  {compatCheck && (
                    <div className={cn("rounded-lg border p-3 text-xs lg:text-sm",
                      compatCheck.compatible ? "border-emerald-500/30 bg-emerald-500/5" : "border-red-500/30 bg-red-500/5")}>
                      <div className="flex items-center gap-2">
                        {compatCheck.compatible ? <CheckCircle size={14} className="text-emerald-500" /> : <XCircle size={14} className="text-red-500" />}
                        <span className="font-medium">{compatCheck.compatible ? t('heredity.t69119') : t('heredity.t28492')}</span>
                      </div>
                      {compatCheck.issues?.map((issue: any, i: number) => (
                        <p key={i} className="mt-1 text-xs text-red-400">{issue.dependency}: {issue.issue} ({t("heredity.requires") || "requires"} {issue.required}, {t("heredity.actual") || "actual"} {issue.actual})</p>
                      ))}
                    </div>
                  )}

                  {/* Dependencies */}
                  {Object.keys(componentDetail.current.dependencies).length > 0 && (
                    <div>
                      <h4 className="text-xs text-muted-foreground mb-2">{t('heredity.dependencies') || t('heredity.dependencies')}</h4>
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(componentDetail.current.dependencies).map(([dep, ver]) => (
                          <span key={dep} className="rounded-full bg-teal-500/10 px-3 py-1 text-xs text-teal-600">
                            {dep} {String(ver)}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Version History */}
                  <div>
                    <h4 className="text-xs text-muted-foreground mb-2">{t('heredity.versionHistory') || t('heredity.versionHistory')}</h4>
                    <div className="space-y-1">
                      {componentDetail.versions?.map((v: any, i: number) => (
                        <div key={i} className="flex items-center gap-2 lg:gap-3 text-xs lg:text-sm py-1">
                          <span className={cn("rounded-full px-2 py-0.5 text-[10px]", STATUS_COLORS[v.status] || "bg-muted")}>
                            {v.status}
                          </span>
                          <span className="font-mono text-xs lg:text-sm">v{v.version}</span>
                          <span className="text-xs text-muted-foreground">{formatTime(v.created_at)}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Release Notes */}
                  {componentDetail.current.release_notes && (
                    <div>
                      <h4 className="text-xs text-muted-foreground mb-2">{t('heredity.releaseNotes') || t('heredity.releaseNotes')}</h4>
                      <p className="text-xs lg:text-sm text-muted-foreground">{componentDetail.current.release_notes}</p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Migrations Tab */}
        {tab === "migrations" && (
          <div className="space-y-4">
            {/* Create Migration */}
            <div className="rounded-xl border border-border p-3 lg:p-4 space-y-3">
              <h3 className="text-xs lg:text-sm font-medium">{t('heredity.createMigration') || t('heredity.t45504')}</h3>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 lg:gap-3">
                <select value={migComponentId} onChange={(e) => setMigComponentId(e.target.value)}
                  className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none">
                  <option value="">{t("heredity.selectComponent") || "Select component..."}</option>
                  {components.map((c) => (
                    <option key={c.component_id} value={c.component_id}>{c.component_name} (v{c.current_version})</option>
                  ))}
                </select>
                <input value={migFrom} onChange={(e) => setMigFrom(e.target.value)}
                  placeholder={t("heredity.fromVersion") || "Source version (e.g. 0.1.0)"}
                  className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-teal-500/20" />
                <input value={migTo} onChange={(e) => setMigTo(e.target.value)}
                  placeholder={t("heredity.toVersion") || "Target version (e.g. 0.2.0)"}
                  className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-teal-500/20" />
                <button onClick={handleCreateMigration} disabled={loading}
                  className="flex items-center justify-center gap-1.5 rounded-lg bg-teal-500 px-4 py-2 text-xs lg:text-sm text-white hover:bg-teal-600 disabled:opacity-50">
                  {loading ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                  {t('common.create') || t('heredity.text22')}
                </button>
              </div>
            </div>

            {/* Migration List */}
            {migrations.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <ArrowUpCircle size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t('heredity.noMigrations') || t('heredity.empty')}</p>
              </div>
            ) : (
              <div className="space-y-2">
                {migrations.map((m) => (
                  <div key={m.migration_id} className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 lg:gap-3">
                        <span className={cn("rounded-full px-2 py-0.5 text-[10px]", STATUS_COLORS[m.status] || "bg-muted")}>
                          {m.status}
                        </span>
                        <span className="text-xs lg:text-sm font-medium">{m.component_id}</span>
                        <span className="font-mono text-xs lg:text-sm text-muted-foreground">
                          {m.from_version} → {m.to_version}
                        </span>
                        {m.dry_run && <span className="rounded-full bg-yellow-500/10 px-2 py-0.5 text-[10px] text-yellow-600">试运行</span>}
                      </div>
                      <div className="flex items-center gap-2">
                        {(m.status === "pending") && (
                          <button onClick={() => handleExecuteMigration(m.migration_id)} disabled={loading}
                            className="flex items-center gap-1 rounded-lg bg-teal-500 px-3 py-1.5 text-xs text-white hover:bg-teal-600 disabled:opacity-50">
                            <Play size={12} /> {t('heredity.execute') || t('heredity.execute')}
                          </button>
                        )}
                        {(m.status === "completed" || m.status === "failed") && (
                          <button onClick={() => handleRollbackMigration(m.migration_id)} disabled={loading}
                            className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50">
                            <RotateCcw size={12} /> {t('heredity.rollback') || t('heredity.rollback')}
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="mt-2 flex items-center gap-2 lg:gap-4 text-xs text-muted-foreground">
                      <span>ID: {m.migration_id}</span>
                      {m.started_at && <span>{t('heredity.started') || t('heredity.start')}: {formatTime(m.started_at)}</span>}
                      {m.completed_at && <span>{t('heredity.completed') || t('heredity.done')}: {formatTime(m.completed_at)}</span>}
                      {m.error && <span className="text-red-400">{t('heredity.error') || t('heredity.error')}: {m.error}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Changelog Tab */}
        {tab === "changelog" && (
          <div className="space-y-2">
            {changelog.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <History size={40} className="mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t('heredity.noChangelog') || t('heredity.text26')}</p>
              </div>
            ) : (
              <div className="space-y-1">
                {changelog.map((entry) => {
                  const Icon = CHANGE_ICONS[entry.change_type] || FileText;
                  return (
                    <div key={entry.entry_id} className="flex items-start gap-2 lg:gap-3 rounded-lg p-3 hover:bg-muted/50 transition-colors">
                      <div className={cn("mt-0.5 rounded-lg p-1.5", STATUS_COLORS[entry.change_type] || "bg-muted")}>
                        <Icon size={12} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-xs lg:text-sm font-medium">{entry.component_id}</span>
                          <span className="font-mono text-xs text-muted-foreground">v{entry.version}</span>
                          <span className={cn("rounded-full px-1.5 py-0.5 text-[10px]", STATUS_COLORS[entry.change_type] || "bg-muted")}>
                            {entry.change_type}
                          </span>
                        </div>
                        <p className="text-xs lg:text-sm text-muted-foreground mt-0.5">{entry.description}</p>
                      </div>
                      <span className="text-xs text-muted-foreground shrink-0">{formatTime(entry.timestamp)}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Platform Tab */}
        {tab === "platform" && platform && (
          <div className="space-y-4">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-lg font-semibold">{t('heredity.platformVersion') || t('heredity.platformVersion')}</h3>
                  <p className="text-2xl lg:text-3xl font-bold text-teal-500 mt-2">v{platform.platform_version}</p>
                </div>
                <div className="flex gap-2">
                  <button onClick={() => handleBumpPlatform("patch")} disabled={loading}
                    className="rounded-lg border border-border px-3 py-2 text-xs lg:text-sm hover:bg-muted disabled:opacity-50">
                    {t("heredity.patch") || "Patch"} +1
                  </button>
                  <button onClick={() => handleBumpPlatform("minor")} disabled={loading}
                    className="rounded-lg border border-border px-3 py-2 text-xs lg:text-sm hover:bg-muted disabled:opacity-50">
                    {t("heredity.minor") || "Minor"} +1
                  </button>
                  <button onClick={() => handleBumpPlatform("major")} disabled={loading}
                    className="rounded-lg bg-teal-500 px-3 py-2 text-xs lg:text-sm text-white hover:bg-teal-600 disabled:opacity-50">
                    {t("heredity.major") || "Major"} +1
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-2 lg:grid-cols-3 gap-2 lg:gap-4 text-center">
                <div className="rounded-lg bg-muted p-3 lg:p-4">
                  <p className="text-xl lg:text-2xl font-bold">{platform.total_components}</p>
                  <p className="text-xs text-muted-foreground">{t('heredity.components') || t('heredity.components')}</p>
                </div>
                <div className="rounded-lg bg-muted p-3 lg:p-4">
                  <p className="text-xl lg:text-2xl font-bold">{platform.total_migrations}</p>
                  <p className="text-xs text-muted-foreground">{t('heredity.migrations') || t('heredity.migrations')}</p>
                </div>
                <div className="rounded-lg bg-muted p-3 lg:p-4">
                  <p className="text-xl lg:text-2xl font-bold">{platform.total_changelog_entries}</p>
                  <p className="text-xs text-muted-foreground">{t('heredity.changes') || t('heredity.changes')}</p>
                </div>
              </div>
            </div>

            {/* Dependency Graph */}
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
              <h3 className="text-xs lg:text-sm font-semibold">{t('heredity.dependencyGraph') || t('heredity.t75585')}</h3>
              <div className="space-y-2">
                {components.map((c) => (
                  <div key={c.component_id} className="flex items-center gap-2 lg:gap-3 text-xs lg:text-sm">
                    <span className="font-mono w-32 truncate">{c.component_id}</span>
                    <ArrowUpCircle size={12} className="text-teal-500 shrink-0" />
                    <div className="flex flex-wrap gap-1">
                      {Object.keys(c.dependencies).length === 0 ? (
                        <span className="text-xs text-muted-foreground">{t('heredity.noDeps') || t('heredity.noDeps')}</span>
                      ) : (
                        Object.entries(c.dependencies).map(([dep, ver]) => (
                          <span key={dep} className="rounded bg-teal-500/10 px-2 py-0.5 text-xs text-teal-600">
                            {dep} {ver}
                          </span>
                        ))
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}

function StatCard({ icon: Icon, label, value, sub, color, bg }: {
  icon: React.ElementType; label: string; value: string; sub: string; color: string; bg: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <div className={cn("rounded-lg p-1.5", bg)}><Icon size={14} className={color} /></div>
      </div>
      <p className="text-xl lg:text-2xl font-bold">{value}</p>
      <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>
    </div>
  );
}
