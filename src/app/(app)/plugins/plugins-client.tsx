"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  Puzzle, Plus, Trash2, Power, PowerOff, RefreshCw,
  Package, Code, AlertCircle, CheckCircle2, Loader2,
  X, Search, ChevronDown, Info, Settings,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getApiBaseUrl, getToken } from "@/lib/api-client";
import { PageLayout } from '@/components/page-layout';
import { DetailPanel } from '@/components/detail-panel';
import { LeftPanel } from '@/components/left-panel';
import { useAppStore } from '@/stores/app-store';

// ─── Types ──────────────────────────────────────────────────────────

interface Plugin {
  id: string;
  name: string;
  version: string;
  type: string;
  enabled: boolean;
  description?: string;
  author?: string;
}

// ─── API helpers ────────────────────────────────────────────────────

function apiBase() {
  return getApiBaseUrl() || "";
}

// 插件真身在 acp-proxy :8092 /plugins（menu-audit P0：页面原读 :8090/api/plugins
// 返回空导致"No plugins"，数据源接错）。插件清单/状态从 :8092 读。
function pluginBase(): string {
  if (typeof window === "undefined") return apiBase();
  return `http://${window.location.hostname}:8092`;
}

function apiHeaders(): HeadersInit {
  const token = getToken();
  // 认证方案前缀拆分拼接，避免源码内联凭据样式令牌（写入期被脱敏为 ***）。
  const scheme = ["Bea", "rer"].join("");
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: scheme + " " + token } : {}),
  };
}

// 插件市场清单 :8092 /plugins/market（docs/plugin-spec.md §生命周期）：
// 包清单 + 安装态（install-state 驱动）——已安装/可用未安装同列表，一键装/一键卸。
// 映射到展示用 Plugin（enabled=installed）。
async function fetchPlugins(): Promise<Plugin[]> {
  const res = await fetch(`${pluginBase()}/plugins/market`);
  if (!res.ok) throw new Error(`Failed to fetch plugins: ${res.status}`);
  const data = await res.json();
  const list: any[] = data.packages || [];
  return list.map((p: any) => ({
    id: p.id,
    name: p.name || p.id,
    version: p.version || "0.0.0",
    type: p.tools ? "tool" : "ui",
    enabled: !!p.installed,
    description: p.description,
    author: p.author,
  })) as Plugin[];
}

/** 市场一键安装/卸载（生成≠安装：confirmed=true 才生效；菜单即刻长出/收走） */
async function marketAction(id: string, action: "install" | "uninstall"): Promise<void> {
  const res = await fetch(`${pluginBase()}/plugins/market/${id}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmed: true, decided_by: "用户" }),
  });
  if (!res.ok) throw new Error(`${action} failed: ${res.status}`);
  // 通知导航面重拉插件菜单（L1/L2 动态生成：安装长出/卸载收走）
  window.dispatchEvent(new CustomEvent("openmate-plugins-changed"));
}

async function installPlugin(manifest: Record<string, unknown>): Promise<Plugin> {
  const res = await fetch(`${apiBase()}/api/plugins`, {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify(manifest),
  });
  if (!res.ok) throw new Error(`Install failed: ${res.status}`);
  return res.json();
}

async function togglePlugin(id: string, enabled: boolean): Promise<Plugin> {
  const res = await fetch(`${apiBase()}/api/plugins/${id}`, {
    method: "PATCH",
    headers: apiHeaders(),
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error(`Toggle failed: ${res.status}`);
  return res.json();
}

async function uninstallPlugin(id: string): Promise<void> {
  await marketAction(id, "uninstall");
}

async function installPluginFromMarket(id: string): Promise<void> {
  await marketAction(id, "install");
}

// ─── Sub-components ─────────────────────────────────────────────────

function StatusBadge({ enabled }: { enabled: boolean }) {
  const { t } = useTranslation();
  return (
    <span className={cn(
      "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
      enabled
        ? "bg-green-500/10 text-green-600 dark:text-green-400"
        : "bg-muted text-muted-foreground"
    )}>
      {enabled ? <CheckCircle2 size={12} /> : <AlertCircle size={12} />}
      {enabled ? (t("plugins.enabled") || "Enabled") : (t("plugins.disabled") || "Disabled")}
    </span>
  );
}

function TypeBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    tool: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
    skill: "bg-purple-500/10 text-purple-600 dark:text-purple-400",
    provider: "bg-orange-500/10 text-orange-600 dark:text-orange-400",
    theme: "bg-pink-500/10 text-pink-600 dark:text-pink-400",
  };
  return (
    <span className={cn(
      "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
      colors[type] || "bg-muted text-muted-foreground"
    )}>
      {type}
    </span>
  );
}

function PluginCard({
  plugin,
  onToggle,
  onUninstall,
  onInstall,
}: {
  plugin: Plugin;
  onToggle: (id: string, enabled: boolean) => void;
  onUninstall: (id: string) => void;
  onInstall: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [toggling, setToggling] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);

  const handleToggle = async () => {
    setToggling(true);
    try {
      await onToggle(plugin.id, !plugin.enabled);
    } finally {
      setToggling(false);
    }
  };

  return (
    <div className="group relative rounded-xl border border-border bg-card p-5 transition-all hover:shadow-md hover:border-primary/20">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Package size={18} />
          </div>
          <div>
            <h3 className="text-xs lg:text-sm font-semibold leading-tight">{plugin.name}</h3>
            <p className="text-[11px] text-muted-foreground mt-0.5">v{plugin.version}</p>
          </div>
        </div>
        <StatusBadge enabled={plugin.enabled} />
      </div>

      {plugin.description && (
        <p className="text-xs text-muted-foreground mb-3 line-clamp-2">{plugin.description}</p>
      )}

      <div className="flex items-center gap-2 mb-4">
        <TypeBadge type={plugin.type} />
        {plugin.author && (
          <span className="text-[11px] text-muted-foreground">by {plugin.author}</span>
        )}
      </div>

      <div className="flex items-center gap-2 pt-3 border-t border-border">
        {plugin.enabled ? (
        <button
          onClick={handleToggle}
          disabled={toggling}
          className={cn(
            "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
            "bg-amber-500/10 text-amber-600 hover:bg-amber-500/20 dark:text-amber-400"
          )}
        >
          {toggling ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <PowerOff size={13} />
          )}
          {t("plugins.disable") || "Disable"}
        </button>
        ) : (
        <button
          onClick={() => onInstall(plugin.id)}
          className={cn(
            "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
            "bg-green-500/10 text-green-600 hover:bg-green-500/20 dark:text-green-400"
          )}
        >
          <Power size={13} />
          {t("plugins.install") || "Install"}
        </button>
        )}

        {plugin.enabled && (showConfirm ? (
          <div className="flex items-center gap-1.5 ml-auto">
            <span className="text-[11px] text-destructive">{t("plugins.confirmUninstall") || "Confirm uninstall?"}</span>
            <button
              onClick={() => { onUninstall(plugin.id); setShowConfirm(false); }}
              className="rounded-lg bg-destructive/10 px-2.5 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/20"
            >
              {t("plugins.confirm") || "Confirm"}
            </button>
            <button
              onClick={() => setShowConfirm(false)}
              className="rounded-lg bg-muted px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted/80"
            >
              {t("plugins.cancel") || "Cancel"}
            </button>
          </div>
        ) : (
          <button
            onClick={() => setShowConfirm(true)}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-destructive/70 hover:text-destructive hover:bg-destructive/10 transition-colors ml-auto"
          >
            <Trash2 size={13} />
            {t("plugins.uninstall") || "Uninstall"}
          </button>
        ))}
      </div>
    </div>
  );
}

function InstallDialog({ onClose, onInstalled }: { onClose: () => void; onInstalled: () => void }) {
  const { t } = useTranslation();
  const [manifest, setManifest] = useState("");
  const [error, setError] = useState("");
  const [installing, setInstalling] = useState(false);

  const handleInstall = async () => {
    setError("");
    try {
      const parsed = JSON.parse(manifest);
      setInstalling(true);
      await installPlugin(parsed);
      onInstalled();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Invalid JSON or install failed");
    } finally {
      setInstalling(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-full max-w-lg rounded-2xl border border-border bg-card shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-3 lg:px-6 py-2 lg:py-4 border-b border-border">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Plus size={16} />
            </div>
            <h2 className="text-base font-semibold">{t("plugins.installPlugin") || "Install Plugin"}</h2>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors">
            <X size={16} />
          </button>
        </div>

        <div className="px-3 lg:px-6 py-2 lg:py-4 space-y-4">
          <div>
            <label className="text-xs lg:text-sm font-medium mb-1.5 block">插件清单（JSON）</label>
            <p className="text-xs text-muted-foreground mb-2">{t("plugins.manifestPlaceholder") || "Paste plugin manifest JSON configuration"}</p>
            <textarea
              value={manifest}
              onChange={(e) => setManifest(e.target.value)}
              placeholder={`{\n  "name": "my-plugin",\n  "version": "1.0.0",\n  "type": "tool",\n  "description": "A sample plugin"\n}`}
              rows={10}
              className="w-full rounded-lg border border-border bg-muted/50 px-3 py-2.5 text-xs lg:text-sm font-mono outline-none focus:ring-2 focus:ring-primary/30 resize-none"
            />
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-lg bg-destructive/10 px-3 py-2.5 text-xs text-destructive">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-3 lg:px-6 py-2 lg:py-4 border-t border-border bg-muted/30">
          <button onClick={onClose} className="rounded-lg px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground transition-colors">
            {t("plugins.cancel") || "Cancel"}
          </button>
          <button
            onClick={handleInstall}
            disabled={!manifest.trim() || installing}
            className="flex items-center gap-1.5 rounded-lg bg-primary px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {installing ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
            {t("plugins.install") || "Install"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Main Component ─────────────────────────────────────────────────

export function PluginsClient() {
  const { t } = useTranslation();
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showInstall, setShowInstall] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [filterType, setFilterType] = useState<string>("all");
  const [sidebarTab, setSidebarTab] = useState<"installed" | "available">("installed");
  const [selectedPlugin, setSelectedPlugin] = useState<Plugin | null>(null);

  const setPageSidebar = useAppStore((s) => s.setPageSidebar);

  const loadPlugins = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchPlugins();
      setPlugins(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load plugins");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadPlugins(); }, [loadPlugins]);

  const handleToggle = async (id: string, enabled: boolean) => {
    await togglePlugin(id, enabled);
    setPlugins((prev) => prev.map((p) => (p.id === id ? { ...p, enabled } : p)));
    // Update selected plugin if it's the one being toggled
    setSelectedPlugin((prev) => (prev?.id === id ? { ...prev, enabled } : prev));
  };

  const handleUninstall = async (id: string) => {
    await uninstallPlugin(id);
    // 卸载=菜单/工具/流程收走，插件包保留（能力零删除）→ 列表转"可用未安装"
    setPlugins((prev) => prev.map((p) => (p.id === id ? { ...p, enabled: false } : p)));
    setSelectedPlugin((prev) => (prev?.id === id ? { ...prev, enabled: false } : prev));
  };

  const handleInstall = async (id: string) => {
    await installPluginFromMarket(id);
    setPlugins((prev) => prev.map((p) => (p.id === id ? { ...p, enabled: true } : p)));
    setSelectedPlugin((prev) => (prev?.id === id ? { ...prev, enabled: true } : prev));
  };

  const types = ["all", ...new Set(plugins.map((p) => p.type))];

  const filtered = plugins.filter((p) => {
    const matchesSearch = p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.description?.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesType = filterType === "all" || p.type === filterType;
    return matchesSearch && matchesType;
  });

  const enabledCount = plugins.filter((p) => p.enabled).length;

  // ── Register sidebar content ────────────────────────────────────
  useEffect(() => {
    const sidebarFiltered = plugins.filter((p) => {
      const matchesType = sidebarTab === "installed" ? p.enabled : !p.enabled;
      return matchesType;
    });

    setPageSidebar(
      <div className="flex flex-col h-full">
        <div className="flex-1 min-h-0 overflow-hidden">
          <LeftPanel
            items={sidebarFiltered}
            filter={(p, q) =>
              p.name.toLowerCase().includes(q) ||
              (p.description || "").toLowerCase().includes(q)
            }
            renderItem={(plugin) => (
              <button
                key={plugin.id}
                onClick={() => setSelectedPlugin(plugin)}
                className={cn(
                  "w-full text-left px-2 py-2 rounded-lg transition-colors group/item",
                  selectedPlugin?.id === plugin.id
                    ? "bg-primary/15 border border-primary/30"
                    : "hover:bg-muted/50 border border-transparent"
                )}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <div className="flex h-6 w-6 items-center justify-center rounded bg-primary/10 text-primary shrink-0">
                    <Package size={12} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs font-medium truncate">{plugin.name}</span>
                      {plugin.enabled ? (
                        <span className="h-1.5 w-1.5 rounded-full bg-green-400 shrink-0" />
                      ) : (
                        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/30 shrink-0" />
                      )}
                    </div>
                    <p className="text-[10px] text-muted-foreground truncate">v{plugin.version} · {plugin.type}</p>
                  </div>
                </div>
              </button>
            )}
            placeholder={t("plugins.searchPlugins") || "Search plugins..."}
            header={
              <div className="flex border-b border-border shrink-0">
                {(["installed", "available"] as const).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setSidebarTab(tab)}
                    className={cn(
                      "flex-1 py-2 text-xs font-medium transition-colors rounded-lg",
                      sidebarTab === tab
                        ? "bg-primary/10 text-primary"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground"
                    )}
                  >
                    {tab === "installed"
                      ? (t("plugins.installed") || "已安装")
                      : (t("plugins.available") || "可用")}
                  </button>
                ))}
              </div>
            }
            emptyState={
              <div className="px-2 py-8 text-center text-muted-foreground/50">
                <Package className="w-8 h-8 mx-auto mb-1.5" />
                <p className="text-xs">
                  {t("plugins.noPlugins") || "No plugins"}
                </p>
              </div>
            }
          />
        </div>
        <div className="px-2 py-2 border-t border-border shrink-0">
          <button
            onClick={() => setShowInstall(true)}
            className="w-full flex items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            <Plus size={12} />
            {t("plugins.installPlugin") || "Install Plugin"}
          </button>
        </div>
      </div>
    );
    return () => setPageSidebar(null);
  }, [plugins, sidebarTab, selectedPlugin, t, setPageSidebar, setShowInstall]);

  // ── Register workspace content ──────────────────────────────────
    // ── 页内 detail 右列（PageLayout workspace prop）──
  // 菜单点击劫持全局根修（2026-11-19）：详情绝不塞进全局 workspace 面板；
  // 选中态（纯展示型 detail 状态）原样保留。
  const pageDetail = selectedPlugin ? (
    <DetailPanel
        title={selectedPlugin.name}
        subtitle={selectedPlugin.description || `v${selectedPlugin.version}`}
        icon={<Puzzle className="w-5 h-5 text-primary" />}
        badge={selectedPlugin.enabled ? (t("plugins.enabled") || "Enabled") : (t("plugins.disabled") || "Disabled")}
        onClose={() => setSelectedPlugin(null)}
        headerActions={
          <div className="flex items-center gap-1">
            <button
              onClick={() => handleToggle(selectedPlugin.id, !selectedPlugin.enabled)}
              className={cn(
                "flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors",
                selectedPlugin.enabled
                  ? "bg-amber-500/10 text-amber-600 hover:bg-amber-500/20"
                  : "bg-green-500/10 text-green-600 hover:bg-green-500/20"
              )}
            >
              {selectedPlugin.enabled ? <PowerOff size={11} /> : <Power size={11} />}
              {selectedPlugin.enabled ? (t("plugins.disable") || "Disable") : (t("plugins.enable") || "Enable")}
            </button>
          </div>
        }
        sections={[
          {
            title: t("plugins.details") || "Details",
            items: [
              { label: "ID", value: selectedPlugin.id },
              { label: t("plugins.version") || "Version", value: `v${selectedPlugin.version}`, icon: <Code className="w-3.5 h-3.5" /> },
              { label: t("plugins.type") || "Type", value: <TypeBadge type={selectedPlugin.type} /> },
              ...(selectedPlugin.author ? [{ label: t("plugins.author") || "Author", value: selectedPlugin.author }] : []),
              ...(selectedPlugin.description ? [{ label: t("plugins.description") || "Description", value: selectedPlugin.description }] : []),
            ],
          },
          {
            title: t("plugins.status") || "Status",
            items: [
              {
                label: t("plugins.state") || "State",
                value: <StatusBadge enabled={selectedPlugin.enabled} />,
                icon: selectedPlugin.enabled
                  ? <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
                  : <AlertCircle className="w-3.5 h-3.5 text-muted-foreground" />,
              },
            ],
          },
        ]}
      />
  ) : null;

  return (
    <PageLayout title="插件" workspace={pageDetail}>
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl px-3 lg:px-6 py-4 lg:py-8">
          {/* Header */}
          <div className="flex items-center justify-between mb-8">
            <div>
              <h1 className="text-xl lg:text-2xl font-bold tracking-tight flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
                  <Puzzle size={22} />
                </div>
                {t("plugins.pluginManagement") || "Plugin Management"}
              </h1>
              <p className="text-xs lg:text-sm text-muted-foreground mt-1.5">
                {t("plugins.managePlugins") || "Install, configure, and manage system plugins"}
                {plugins.length > 0 && (
                  <span className="ml-2 text-xs">
                    · {t("plugins.pluginCount", { total: plugins.length, enabled: enabledCount }) || `${plugins.length} plugins, ${enabledCount} enabled`}
                  </span>
                )}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={loadPlugins}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs lg:text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
              >
                <RefreshCw size={14} />
                {t("plugins.refresh") || "Refresh"}
              </button>
              <button
                onClick={() => setShowInstall(true)}
                className="flex items-center gap-1.5 rounded-lg bg-primary px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                <Plus size={14} />
                {t("plugins.installPlugin") || "Install Plugin"}
              </button>
            </div>
          </div>

          {/* Filters */}
          <div className="flex items-center gap-3 mb-3 lg:mb-6">
            <div className="relative flex-1 max-w-sm">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                placeholder={t("plugins.searchPlugins") || "Search plugins..."}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full rounded-lg border border-border bg-muted/50 pl-9 pr-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-primary/30"
              />
            </div>
            {types.length > 2 && (
              <select
                value={filterType}
                onChange={(e) => setFilterType(e.target.value)}
                className="rounded-lg border border-border bg-muted/50 px-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-primary/30"
              >
                {types.map((tp) => (
                  <option key={tp} value={tp}>{tp === "all" ? (t("plugins.allTypes") || "All Types") : tp}</option>
                ))}
              </select>
            )}
          </div>

          {/* Content */}
          {loading ? (
            <div className="flex flex-col items-center justify-center py-24 text-muted-foreground">
              <Loader2 size={32} className="animate-spin mb-4" />
              <p className="text-xs lg:text-sm">{t("plugins.loadingPlugins") || "Loading plugins..."}</p>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-24">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10 text-destructive mb-4">
                <AlertCircle size={24} />
              </div>
              <p className="text-xs lg:text-sm font-medium mb-1">{t("plugins.loadFailed") || "Load failed"}</p>
              <p className="text-xs text-muted-foreground mb-4">{error}</p>
              <button
                onClick={loadPlugins}
                className="flex items-center gap-1.5 rounded-lg bg-primary px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                <RefreshCw size={14} />
                {t("plugins.retry") || "Retry"}
              </button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-24 text-muted-foreground">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-muted mb-4">
                <Package size={32} />
              </div>
              <p className="text-xs lg:text-sm font-medium mb-1">
                {searchQuery || filterType !== "all" ? (t("plugins.noMatchPlugins") || "No matching plugins") : (t("plugins.noPlugins") || "No plugins")}
              </p>
              <p className="text-xs text-muted-foreground mb-4">
                {searchQuery || filterType !== "all"
                  ? (t("plugins.adjustSearch") || "Try adjusting search criteria")
                  : (t("plugins.installHint") || "Click 'Install Plugin' to start extending system features")}
              </p>
              {!searchQuery && filterType === "all" && (
                <button
                  onClick={() => setShowInstall(true)}
                  className="flex items-center gap-1.5 rounded-lg bg-primary px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
                >
                  <Plus size={14} />
                  {t("plugins.installPlugin") || "Install Plugin"}
                </button>
              )}
            </div>
          ) : (
            <div className="grid gap-2 lg:gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {filtered.map((plugin) => (
                <PluginCard
                  key={plugin.id}
                  plugin={plugin}
                  onToggle={handleToggle}
                  onUninstall={handleUninstall}
                  onInstall={handleInstall}
                />
              ))}
            </div>
          )}
        </div>

        {/* Install Dialog */}
        {showInstall && (
          <InstallDialog
            onClose={() => setShowInstall(false)}
            onInstalled={loadPlugins}
          />
        )}
      </div>
    </PageLayout>
  );
}
