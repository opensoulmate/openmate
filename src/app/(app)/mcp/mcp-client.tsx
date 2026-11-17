"use client";
import { useState, useEffect, useCallback } from "react";
import {
  Plug,
  PlugZap,
  Search,
  Wrench,
  Check,
  X,
  RefreshCw,
  Plus,
  Trash2,
  Edit3,
  Power,
  PowerOff,
  AlertCircle,
  CheckCircle,
  Loader2,
  ChevronDown,
  ChevronRight,
  Server,
  Settings,
  Play,
} from "lucide-react";
import { getApiBaseUrl, getToken } from "@/lib/api-client";
import { useTranslation } from "react-i18next";
import { PageLayout } from '@/components/page-layout';
import { DetailPanel } from '@/components/detail-panel';
import { useAppStore } from '@/stores/app-store';
import { LeftPanel } from '@/components/left-panel';
import { cn } from "@/lib/utils";

interface McpTool {
  name: string;
  description: string;
  inputSchema?: Record<string, unknown>;
}

interface McpServer {
  id: string;
  name: string;
  description: string;
  url: string;
  transport: string;
  connected: boolean;
  enabled: boolean;
  tools: McpTool[];
  config: Record<string, unknown>;
  created_at: number;
  last_connected: number | null;
  error: string | null;
}

interface ToolTestState {
  server: McpServer;
  tool: McpTool;
  args: string;
  result: any;
  loading: boolean;
  error: string | null;
}

export function McpClient() {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showAdd, setShowAdd] = useState(false);
  const [actionLoading, setActionLoading] = useState<Record<string, string>>({});
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [stats, setStats] = useState({ total_servers: 0, connected: 0, disconnected: 0, total_tools: 0 });
  const [selectedServer, setSelectedServer] = useState<McpServer | null>(null);
  const [selectedTool, setSelectedTool] = useState<ToolTestState | null>(null);

  const setPageSidebar = useAppStore((s) => s.setPageSidebar);
  const setPageWorkspace = useAppStore((s) => s.setPageWorkspace);

  // New server form
  const [newName, setNewName] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newTransport, setNewTransport] = useState("stdio");

  const apiBase = getApiBaseUrl();
  const headers = { Authorization: `Bearer ${getToken()}`, "Content-Type": "application/json" };

  const showToast = useCallback((message: string, type: "success" | "error") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  }, []);

  const fetchServers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/mcp/servers`, { headers });
      if (res.ok) {
        const data = await res.json();
        setServers(data.servers || []);
      }
    } catch (e) {
      console.error("Failed to fetch MCP servers:", e);
    }
    setLoading(false);
  }, [apiBase]);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/mcp/stats`, { headers });
      if (res.ok) {
        const data = await res.json();
        setStats(data);
      }
    } catch {}
  }, [apiBase]);

  useEffect(() => {
    fetchServers();
    fetchStats();
  }, [fetchServers, fetchStats]);

  const filtered = servers.filter((s) => {
    if (!query) return true;
    const q = query.toLowerCase();
    return (
      s.name.toLowerCase().includes(q) ||
      s.description.toLowerCase().includes(q) ||
      s.tools.some((t) => t.name.toLowerCase().includes(q))
    );
  });

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // ── Actions ────────────────────────────────────────────

  const handleConnect = async (server: McpServer) => {
    setActionLoading((prev) => ({ ...prev, [server.id]: server.connected ? "disconnect" : "connect" }));
    try {
      const endpoint = server.connected ? "disconnect" : "connect";
      const res = await fetch(`${apiBase}/api/mcp/servers/${server.id}/${endpoint}`, {
        method: "POST",
        headers,
      });
      if (res.ok) {
        showToast(`${server.name} ${server.connected ? (t("mcp.disconnected") || "Disconnected") : (t("mcp.connected") || "Connected")}`, "success");
        fetchServers();
        fetchStats();
      } else {
        const data = await res.json();
        showToast(data.detail || (t("mcp.operationFailed") || "Operation failed"), "error");
      }
    } catch {
      showToast(t("mcp.networkError") || "Network error", "error");
    }
    setActionLoading((prev) => ({ ...prev, [server.id]: "" }));
  };

  const handleDelete = async (server: McpServer) => {
    if (!confirm(`${t("mcp.confirmDelete") || "Delete MCP server"} "${server.name}"？`)) return;
    try {
      const res = await fetch(`${apiBase}/api/mcp/servers/${server.id}`, {
        method: "DELETE",
        headers,
      });
      if (res.ok) {
        showToast(`${server.name} ${t("mcp.deleted") || "deleted"}`, "success");
        fetchServers();
        fetchStats();
        if (selectedServer?.id === server.id) setSelectedServer(null);
      }
    } catch {
      showToast(t("mcp.deleteFailed") || "Delete failed", "error");
    }
  };

  const handleAdd = async () => {
    if (!newName || !newUrl) {
      showToast(t("mcp.nameUrlRequired") || "Name and URL are required", "error");
      return;
    }
    try {
      const res = await fetch(`${apiBase}/api/mcp/servers`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          name: newName,
          url: newUrl,
          description: newDesc,
          transport: newTransport,
        }),
      });
      if (res.ok) {
        showToast(`${newName} ${t("mcp.added") || "added"}`, "success");
        setShowAdd(false);
        setNewName("");
        setNewUrl("");
        setNewDesc("");
        setNewTransport("stdio");
        fetchServers();
        fetchStats();
      } else {
        const data = await res.json();
        showToast(data.detail || (t("mcp.addFailed") || "Add failed"), "error");
      }
    } catch {
      showToast(t("mcp.networkError") || "Network error", "error");
    }
  };

  const handleToggleEnabled = async (server: McpServer) => {
    try {
      const res = await fetch(`${apiBase}/api/mcp/servers/${server.id}`, {
        method: "PATCH",
        headers,
        body: JSON.stringify({ enabled: !server.enabled }),
      });
      if (res.ok) {
        showToast(`${server.name} ${server.enabled ? (t("mcp.disabled") || "Disabled") : (t("mcp.enabled") || "Enabled")}`, "success");
        fetchServers();
      }
    } catch {
      showToast(t("mcp.operationFailed") || "Operation failed", "error");
    }
  };

  const handleTestTool = (server: McpServer, tool: McpTool) => {
    setSelectedTool({ server, tool, args: "{}", result: null, loading: false, error: null });
  };

  const handleExecuteTool = async () => {
    if (!selectedTool) return;
    setSelectedTool(prev => prev ? { ...prev, loading: true, error: null } : null);
    try {
      const args = JSON.parse(selectedTool.args);
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 30000);
      const res = await fetch(`${apiBase}/api/mcp/tools/call`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          server_id: selectedTool.server.id,
          tool_name: selectedTool.tool.name,
          arguments: args,
        }),
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      const data = await res.json();
      if (res.ok) {
        setSelectedTool(prev => prev ? { ...prev, result: data, loading: false } : null);
      } else {
        setSelectedTool(prev => prev ? { ...prev, error: data.detail || "执行失败", loading: false } : null);
      }
    } catch (e: any) {
      const msg = e?.name === "AbortError" ? "执行超时（30秒）" : "JSON 格式错误或网络异常";
      setSelectedTool(prev => prev ? { ...prev, error: msg, loading: false } : null);
    }
  };

  // Keep selectedServer in sync with servers state
  useEffect(() => {
    if (selectedServer) {
      const updated = servers.find((s) => s.id === selectedServer.id);
      if (updated) setSelectedServer(updated);
    }
  }, [servers]);

  // ── Register sidebar content ────────────────────────────────────
  useEffect(() => {
    setPageSidebar(
      <LeftPanel
        items={servers}
        filter={(item, q) => item.name.toLowerCase().includes(q) || item.tools.some(t => (typeof t === "string" ? t : t.name).toLowerCase().includes(q))}
        placeholder={t("mcp.searchPlaceholder") || "搜索服务器..."}
        header={
          <div className="flex items-center gap-2 px-2 pb-2 shrink-0">
            <span className="flex items-center gap-1 rounded-full bg-green-500/10 px-2 py-0.5 text-[10px] text-green-400">
              <span className="h-1.5 w-1.5 rounded-full bg-green-400" />{stats.connected}
            </span>
            <span className="flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
              <Wrench size={9} />{stats.total_tools} {t("mcp.tools") || "Tools"}
            </span>
          </div>
        }
        renderItem={(server) => (
          <button
            key={server.id}
            onClick={() => setSelectedServer(server)}
            className={cn(
              "w-full text-left px-2 py-2 rounded-lg transition-colors",
              selectedServer?.id === server.id
                ? "bg-primary/15 border border-primary/30"
                : "hover:bg-muted/50 border border-transparent"
            )}
          >
            <div className="flex items-center gap-2 min-w-0">
              <div className={cn(
                "flex h-6 w-6 items-center justify-center rounded shrink-0",
                server.connected ? "bg-green-500/10 text-green-400" : "bg-muted text-muted-foreground"
              )}>
                <Server size={12} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-xs font-medium truncate">{server.name}</span>
                  {server.connected ? (
                    <span className="h-1.5 w-1.5 rounded-full bg-green-400 shrink-0" />
                  ) : (
                    <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/30 shrink-0" />
                  )}
                  {!server.enabled && (
                    <span className="text-[9px] px-1 py-0.5 rounded bg-yellow-500/10 text-yellow-400 shrink-0">Off</span>
                  )}
                </div>
                <p className="text-[10px] text-muted-foreground truncate">
                  {server.tools.length} {t("mcp.tools") || "tools"} · {server.transport}
                </p>
              </div>
            </div>
          </button>
        )}
        emptyState={
          <div className="px-2 py-8 text-center text-muted-foreground/50">
            <Plug className="w-8 h-8 mx-auto mb-1.5" />
            <p className="text-xs">{t("mcp.noServers") || "No MCP servers yet"}</p>
          </div>
        }
      />
    );
    return () => setPageSidebar(null);
  }, [servers, filtered, loading, query, stats, selectedServer, t, setPageSidebar, setShowAdd]);

  // ── Register workspace content ──────────────────────────────────
  useEffect(() => {
    if (!selectedServer) {
      setPageWorkspace(null);
      return;
    }
    const busy = actionLoading[selectedServer.id];
    setPageWorkspace(
      <DetailPanel
        title={selectedServer.name}
        subtitle={selectedServer.description || selectedServer.url}
        icon={<Server className="w-5 h-5 text-primary" />}
        badge={selectedServer.connected ? (t("mcp.connectedBadge") || "Connected") : (t("mcp.notConnected") || "Disconnected")}
        onClose={() => setSelectedServer(null)}
        headerActions={
          <div className="flex items-center gap-1">
            <button
              onClick={() => handleToggleEnabled(selectedServer)}
              className={cn(
                "rounded-md p-1.5 transition-colors",
                selectedServer.enabled
                  ? "text-yellow-400 hover:bg-yellow-500/10"
                  : "text-green-400 hover:bg-green-500/10"
              )}
              title={selectedServer.enabled ? (t("mcp.disable") || "Disable") : (t("mcp.enable") || "Enable")}
            >
              {selectedServer.enabled ? <Power size={13} /> : <PowerOff size={13} />}
            </button>
            <button
              onClick={() => handleConnect(selectedServer)}
              disabled={!!busy}
              className={cn(
                "flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors",
                selectedServer.connected
                  ? "border border-red-500/30 text-red-400 hover:bg-red-500/10"
                  : "bg-primary text-primary-foreground hover:bg-primary/90",
                busy ? "opacity-50" : ""
              )}
            >
              {busy ? <Loader2 size={11} className="animate-spin" /> : selectedServer.connected ? <PlugZap size={11} /> : <Plug size={11} />}
              {selectedServer.connected ? (t("mcp.disconnect") || "Disconnect") : (t("mcp.connect") || "Connect")}
            </button>
          </div>
        }
        sections={[
          {
            title: t("mcp.serverInfo") || "Server Info",
            items: [
              { label: "ID", value: selectedServer.id, icon: <Settings className="w-3.5 h-3.5" /> },
              { label: "URL", value: <span className="font-mono text-xs break-all">{selectedServer.url}</span> },
              { label: t("mcp.transport") || "Transport", value: selectedServer.transport },
              { label: t("mcp.registeredAt") || "Registered", value: new Date(selectedServer.created_at * 1000).toLocaleString('zh-CN', { fractionalSecondDigits: 3 }) },
              ...(selectedServer.last_connected ? [{ label: t("mcp.lastConnected") || "Last Connected", value: new Date(selectedServer.last_connected * 1000).toLocaleString('zh-CN', { fractionalSecondDigits: 3 }) }] : []),
            ],
          },
          {
            title: t("mcp.status") || "Status",
            items: [
              {
                label: t("mcp.connection") || "Connection",
                value: (
                  <span className={cn(
                    "inline-flex items-center gap-1 text-xs",
                    selectedServer.connected ? "text-green-400" : "text-muted-foreground"
                  )}>
                    {selectedServer.connected ? <CheckCircle size={12} /> : <AlertCircle size={12} />}
                    {selectedServer.connected ? (t("mcp.connected") || "Connected") : (t("mcp.notConnected") || "Not connected")}
                  </span>
                ),
              },
              {
                label: t("mcp.enabledStatus") || "Enabled",
                value: selectedServer.enabled ? "✓" : "—",
                icon: selectedServer.enabled
                  ? <CheckCircle className="w-3.5 h-3.5 text-green-400" />
                  : <AlertCircle className="w-3.5 h-3.5 text-muted-foreground" />,
              },
              ...(selectedServer.error ? [{
                label: t("mcp.error") || "Error",
                value: <span className="text-red-400 text-xs">{selectedServer.error}</span>,
                icon: <AlertCircle className="w-3.5 h-3.5 text-red-400" />,
              }] : []),
            ],
          },
          {
            title: `${t("mcp.availableTools") || "Available Tools"} (${selectedServer.tools.length})`,
            items: selectedServer.tools.length === 0
              ? [{ label: "", value: <span className="text-xs text-muted-foreground">{t("mcp.noToolsMessage") || "No registered tools. Tools will be auto-discovered after connecting."}</span> }]
              : selectedServer.tools.map((tool) => ({
                  label: tool.name,
                  value: (
                    <div className="flex items-center justify-between gap-2">
                      <span>{tool.description || <span className="text-muted-foreground">—</span>}</span>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleTestTool(selectedServer, tool); }}
                        disabled={!selectedServer.connected}
                        className="rounded-md bg-primary/10 px-2 py-1 text-[10px] text-primary hover:bg-primary/20 disabled:opacity-30 shrink-0"
                      >
                        {t("mcp.test") || "测试"}
                      </button>
                    </div>
                  ),
                  icon: <Wrench className="w-3.5 h-3.5 text-primary" />,
                })),
          },
        ]}
      />
    );
    return () => setPageWorkspace(null);
  }, [selectedServer, actionLoading, t, setPageWorkspace]);

  // ── Render ─────────────────────────────────────────────

  return (
    <PageLayout title="MCP 客户端">
      <div className="flex flex-col h-full overflow-hidden">
        {/* Toast */}
        {toast && (
          <div className={`fixed top-4 right-4 z-50 flex items-center gap-2 rounded-lg px-4 py-2 lg:py-3 text-xs lg:text-sm shadow-lg animate-in slide-in-from-top-2 ${
            toast.type === "success" ? "bg-green-600 text-white" : "bg-red-600 text-white"
          }`}>
            {toast.type === "success" ? <CheckCircle size={16} /> : <AlertCircle size={16} />}
            {toast.message}
          </div>
        )}

        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-border px-3 lg:px-6 py-2 lg:py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
              <Plug size={18} className="text-primary" />
            </div>
            <div>
              <h1 className="text-base lg:text-lg font-semibold">MCP 服务器</h1>
              <p className="text-[10px] lg:text-xs text-muted-foreground">{t("mcp.serviceManagement") || "Model Context Protocol service management"}</p>
            </div>
          </div>
          <div className="flex items-center gap-2 self-start sm:self-auto">
            {/* Stats badges */}
            <div className="hidden md:flex items-center gap-2 mr-4">
              <span className="flex items-center gap-1 rounded-full bg-green-500/10 px-2.5 py-1 text-xs text-green-400">
                <span className="h-1.5 w-1.5 rounded-full bg-green-400" />{stats.connected} {t("mcp.connectedBadge") || "Connected"}
              </span>
              <span className="flex items-center gap-1 rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">
                <Wrench size={10} />{stats.total_tools} {t("mcp.tools") || "Tools"}
              </span>
            </div>
            <button onClick={() => { fetchServers(); fetchStats(); }}
              className="rounded-lg border border-border px-3 py-2 text-xs lg:text-sm hover:bg-accent transition-colors">
              <RefreshCw size={14} />
            </button>
            <button onClick={() => setShowAdd(!showAdd)}
              className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs lg:text-sm text-primary-foreground hover:bg-primary/90 transition-colors">
              <Plus size={14} /> {t("mcp.addServer") || "Add Server"}
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-2 lg:py-4 space-y-4">
          {/* Search */}
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("mcp.searchPlaceholder") || "Search servers or tools..."}
              className="w-full rounded-lg border border-border bg-muted/50 pl-9 pr-4 py-2.5 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-primary/30 transition-shadow"
            />
          </div>

          {/* Add Server Form */}
          {showAdd && (
            <div className="rounded-xl border border-primary/30 bg-primary/5 p-5 space-y-4">
              <h3 className="text-xs lg:text-sm font-medium flex items-center gap-2">
                <Plus size={14} /> {t("mcp.registerNewServer") || "Register New MCP Server"}
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 lg:gap-3">
                <input value={newName} onChange={(e) => setNewName(e.target.value)}
                  placeholder={t("mcp.serverNamePlaceholder") || "Server Name *"} className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-primary/30" />
                <input value={newUrl} onChange={(e) => setNewUrl(e.target.value)}
                  placeholder="URL（如 stdio://server-name）*" className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-primary/30" />
                <input value={newDesc} onChange={(e) => setNewDesc(e.target.value)}
                  placeholder={t("mcp.descriptionPlaceholder") || "Description"} className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-primary/30" />
                <select value={newTransport} onChange={(e) => setNewTransport(e.target.value)}
                  className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-primary/30">
                  <option value="stdio">stdio</option>
                  <option value="sse">SSE</option>
                  <option value="http">HTTP</option>
                </select>
              </div>
              <div className="flex gap-2 justify-end">
                <button onClick={() => setShowAdd(false)} className="rounded-lg border border-border px-4 py-2 text-xs lg:text-sm hover:bg-accent">{t("common.cancel") || "Cancel"}</button>
                <button onClick={handleAdd} className="rounded-lg bg-primary px-4 py-2 text-xs lg:text-sm text-primary-foreground hover:bg-primary/90">{t("common.add") || "Add"}</button>
              </div>
            </div>
          )}

          {/* Server List */}
          {loading ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <Loader2 size={20} className="animate-spin mr-2" /> {t("common.loading") || "Loading..."}
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Plug size={32} className="mb-3 opacity-30" />
              <p className="text-xs lg:text-sm">{query ? (t("mcp.noMatch") || "No matching servers") : (t("mcp.noServers") || "No MCP servers yet")}</p>
            </div>
          ) : (
            <div className="space-y-3">
              {filtered.map((server) => {
                const isExpanded = expanded.has(server.id);
                const busy = actionLoading[server.id];

                return (
                  <div key={server.id} className="group rounded-xl border border-border bg-card overflow-hidden transition-all hover:border-border/80">
                    {/* Server header */}
                    <div className="flex items-center gap-3 px-3 lg:px-5 py-2 lg:py-4">
                      <button onClick={() => toggleExpand(server.id)} className="text-muted-foreground hover:text-foreground transition-colors">
                        {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                      </button>
                      <div className={`flex h-9 w-9 items-center justify-center rounded-lg ${
                        server.connected ? "bg-green-500/10 text-green-400" : "bg-muted text-muted-foreground"
                      }`}>
                        <Server size={16} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-xs lg:text-sm font-medium">{server.name}</span>
                          <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">{server.transport}</span>
                          {server.connected ? (
                            <span className="flex items-center gap-1 text-[10px] text-green-400">
                              <span className="h-1.5 w-1.5 rounded-full bg-green-400" /> {t("mcp.connectedBadge") || "Connected"}
                            </span>
                          ) : (
                            <span className="text-[10px] text-muted-foreground">{t("mcp.notConnected") || "Not connected"}</span>
                          )}
                          {!server.enabled && (
                            <span className="rounded-full bg-yellow-500/10 px-2 py-0.5 text-[10px] text-yellow-400">{t("mcp.disabledBadge") || "Disabled"}</span>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground truncate mt-0.5">{server.description || server.url}</p>
                      </div>
                      <div className="flex items-center gap-1">
                        <span className="text-xs text-muted-foreground mr-2">{server.tools.length} {t("mcp.tools") || "Tools"}</span>
                        <button onClick={() => handleToggleEnabled(server)}
                          className={`rounded-lg p-2 transition-colors ${server.enabled ? "text-yellow-400 hover:bg-yellow-500/10" : "text-green-400 hover:bg-green-500/10"}`}
                          title={server.enabled ? (t("mcp.disable") || "Disable") : (t("mcp.enable") || "Enable")}>
                          {server.enabled ? <Power size={14} /> : <PowerOff size={14} />}
                        </button>
                        <button onClick={() => handleConnect(server)}
                          disabled={!!busy}
                          className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                            server.connected
                              ? "border border-red-500/30 text-red-400 hover:bg-red-500/10"
                              : "bg-primary text-primary-foreground hover:bg-primary/90"
                          } ${busy ? "opacity-50" : ""}`}>
                          {busy ? <Loader2 size={12} className="animate-spin" /> : server.connected ? (t("mcp.disconnect") || "Disconnect") : (t("mcp.connect") || "Connect")}
                        </button>
                        <button onClick={() => handleDelete(server)}
                          className="rounded-lg p-2 text-muted-foreground hover:text-red-400 hover:bg-red-500/10 transition-colors">
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>

                    {/* Expanded: tools + config */}
                    {isExpanded && (
                      <div className="border-t border-border bg-muted/30 px-3 lg:px-5 py-2 lg:py-4 space-y-4">
                        {/* Server info */}
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                          <div><span className="text-muted-foreground">URL:</span> <span className="font-mono">{server.url}</span></div>
                          <div><span className="text-muted-foreground">传输方式：</span> {server.transport}</div>
                          <div><span className="text-muted-foreground">ID:</span> <span className="font-mono">{server.id}</span></div>
                          <div><span className="text-muted-foreground">{t("mcp.registeredAt") || "注册于："}</span> {new Date(server.created_at * 1000).toLocaleDateString()}</div>
                        </div>

                        {/* Error */}
                        {server.error && (
                          <div className="flex items-center gap-2 rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-400">
                            <AlertCircle size={12} /> {server.error}
                          </div>
                        )}

                        {/* Tools */}
                        <div>
                          <h4 className="text-xs font-medium text-muted-foreground mb-2 flex items-center gap-1.5">
                            <Wrench size={12} /> {t("mcp.availableTools") || "Available Tools"} ({server.tools.length})
                          </h4>
                          {server.tools.length === 0 ? (
                            <p className="text-xs text-muted-foreground">{t("mcp.noToolsMessage") || "No registered tools. Tools will be auto-discovered after connecting."}</p>
                          ) : (
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                              {server.tools.map((tool) => (
                                <div key={tool.name} className="flex items-start gap-2 rounded-lg border border-border bg-background px-3 py-2">
                                  <Wrench size={12} className="text-primary mt-0.5 shrink-0" />
                                  <div className="flex-1 min-w-0">
                                    <div className="flex items-center justify-between gap-2">
                                      <span className="text-xs font-medium">{tool.name}</span>
                                      <button
                                        onClick={(e) => { e.stopPropagation(); handleTestTool(server, tool); }}
                                        disabled={!server.connected}
                                        className="rounded-md bg-primary/10 px-2 py-1 text-[10px] text-primary hover:bg-primary/20 disabled:opacity-30 shrink-0"
                                      >
                                        {t("mcp.test") || "测试"}
                                      </button>
                                    </div>
                                    {tool.description && (
                                      <p className="text-[10px] text-muted-foreground mt-0.5">{tool.description}</p>
                                    )}
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Tool Test Modal */}
      {selectedTool && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setSelectedTool(null)}>
          <div className="w-full max-w-2xl max-h-[80vh] overflow-auto rounded-xl bg-background border border-border shadow-2xl" onClick={(e) => e.stopPropagation()}>
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border px-5 py-3">
              <div>
                <h3 className="text-sm font-medium flex items-center gap-2">
                  <Wrench size={14} className="text-primary" />
                  {selectedTool.tool.name}
                </h3>
                <p className="text-xs text-muted-foreground mt-0.5">{selectedTool.tool.description}</p>
              </div>
              <button onClick={() => setSelectedTool(null)} className="rounded-lg p-1.5 hover:bg-muted">
                <X size={16} />
              </button>
            </div>

            {/* Body */}
            <div className="p-5 space-y-4">
              {/* 参数输入 */}
              <div>
                <label className="text-xs font-medium text-muted-foreground mb-1.5 block">
                  {t("mcp.arguments") || "参数"} (JSON)
                </label>
                <textarea
                  value={selectedTool.args}
                  onChange={(e) => setSelectedTool(prev => prev ? { ...prev, args: e.target.value } : null)}
                  placeholder={'{"key": "value"}'}
                  className="w-full h-32 rounded-lg border border-border bg-muted/30 px-3 py-2 font-mono text-xs outline-none focus:ring-2 focus:ring-primary/30"
                />
              </div>

              {/* 执行按钮 */}
              <button
                onClick={handleExecuteTool}
                disabled={selectedTool.loading}
                className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                {selectedTool.loading ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                {t("mcp.execute") || "执行"}
              </button>

              {/* 错误 */}
              {selectedTool.error && (
                <div className="rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-400 flex items-center gap-2">
                  <AlertCircle size={12} /> {selectedTool.error}
                </div>
              )}

              {/* 结果 */}
              {selectedTool.result && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1.5 block">
                    {t("mcp.result") || "执行结果"}
                  </label>
                  <pre className="rounded-lg bg-muted/50 border border-border p-3 text-xs overflow-auto max-h-60 font-mono">
                    {JSON.stringify(selectedTool.result, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </PageLayout>
  );
}
