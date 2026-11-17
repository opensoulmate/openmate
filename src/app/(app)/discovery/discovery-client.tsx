"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  Search, RefreshCw, Monitor, Terminal, Wifi, Plug,
  CheckCircle, XCircle, Clock, Activity, Settings,
  Database, Globe, FolderOpen, Play, Square, AlertTriangle,
  Server, Cpu, HardDrive, Network, Layers, ChevronDown, ChevronRight,
  Plus, Trash2, Eye, Zap, ArrowRight, FileText,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface ProcessInfo {
  pid: number;
  user: string;
  cpu: number;
  mem: number;
  command: string;
  executable?: string;
  description?: string;
}

interface CLITool {
  name: string;
  path: string;
  version: string;
  description: string;
}

interface ServiceInfo {
  protocol: string;
  local_address: string;
  local_port: number;
  state: string;
  pid?: number;
  process_name?: string;
  description?: string;
}

interface AdapterInfo {
  name: string;
  description: string;
  registered_at: number;
}

interface ScanResult {
  processes: ProcessInfo[];
  cli_tools: CLITool[];
  services: ServiceInfo[];
  adapters: AdapterInfo[];
  scan_duration_ms: number;
  timestamp: string;
}

type Tab = "scan" | "adapters";

export function DiscoveryClient() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const [tab, setTab] = useState<Tab>("scan");
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [processes, setProcesses] = useState<ProcessInfo[]>([]);
  const [cliTools, setCliTools] = useState<CLITool[]>([]);
  const [services, setServices] = useState<ServiceInfo[]>([]);
  const [adapters, setAdapters] = useState<AdapterInfo[]>([]);
  const [processFilter, setProcessFilter] = useState("");
  const [toolFilter, setToolFilter] = useState("");
  const [expandedProcess, setExpandedProcess] = useState<number | null>(null);
  const [showAddAdapter, setShowAddAdapter] = useState(false);
  const [adapterType, setAdapterType] = useState<"rest" | "database" | "filesystem">("rest");
  const [adapterConfig, setAdapterConfig] = useState<Record<string, string>>({});
  const [adapterResult, setAdapterResult] = useState<string>("");
  const [testingAdapter, setTestingAdapter] = useState(false);
  const [restProbeUrl, setRestProbeUrl] = useState("");
  const [restProbeResult, setRestProbeResult] = useState<string>("");
  const [dbQueryInput, setDbQueryInput] = useState("");
  const [dbQueryResult, setDbQueryResult] = useState<string>("");
  const [fsPath, setFsPath] = useState("/");
  const [fsListing, setFsListing] = useState<{name:string;is_dir:boolean;size:number}[]>([]);
  const [activeAdapterId, setActiveAdapterId] = useState<string | null>(null);

  const fetchJson = useCallback(async (path: string, opts?: RequestInit) => {
    const r = await fetch(`${apiBase}/api/soma/discovery${path}`, opts);
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    return r.json();
  }, [apiBase]);

  const loadScanData = useCallback(async () => {
    try {
      const [procs, tools, svcs, adpts] = await Promise.all([
        fetchJson("/processes"),
        fetchJson("/cli-tools"),
        fetchJson("/services"),
        fetchJson("/adapters"),
      ]);
      setProcesses(procs);
      setCliTools(tools);
      setServices(svcs);
      setAdapters(adpts);
    } catch (e) {
      console.error("Failed to load scan data:", e);
    }
  }, [fetchJson]);

  useEffect(() => { loadScanData(); }, [loadScanData]);

  const runFullScan = async () => {
    setScanning(true);
    try {
      const result = await fetchJson("/scan");
      setScanResult(result);
      setProcesses(result.processes || []);
      setCliTools(result.cli_tools || []);
      setServices(result.services || []);
      setAdapters(result.adapters || []);
    } catch (e) {
      console.error("Scan failed:", e);
    } finally {
      setScanning(false);
    }
  };

  const configureAdapter = async () => {
    setTestingAdapter(true);
    setAdapterResult("");
    try {
      let endpoint = "";
      let body: Record<string, unknown> = {};
      if (adapterType === "rest") {
        endpoint = "/adapters/rest/configure";
        body = { base_url: adapterConfig.url || "", headers: adapterConfig.headers ? JSON.parse(adapterConfig.headers || "{}") : {}, auth_type: adapterConfig.auth_type || "none", auth_token: adapterConfig.auth_token || "" };
      } else if (adapterType === "database") {
        endpoint = "/adapters/database/configure";
        body = { db_type: adapterConfig.db_type || "sqlite", connection_string: adapterConfig.connection_string || "" };
      } else {
        endpoint = "/adapters/filesystem/configure";
        body = { root_path: adapterConfig.root_path || "/", watch: adapterConfig.watch === "true", patterns: adapterConfig.patterns ? adapterConfig.patterns.split(",").map(s => s.trim()) : [] };
      }
      const result = await fetchJson(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setAdapterResult(JSON.stringify(result, null, 2));
      loadScanData();
    } catch (e: unknown) {
      setAdapterResult(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setTestingAdapter(false);
    }
  };

  const probeRest = async () => {
    if (!restProbeUrl) return;
    setTestingAdapter(true);
    try {
      const r = await fetchJson("/adapters/rest/probe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: restProbeUrl }) });
      setRestProbeResult(JSON.stringify(r, null, 2));
    } catch (e: unknown) {
      setRestProbeResult(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setTestingAdapter(false);
    }
  };

  const queryDb = async (q?: string) => {
    const query = q || dbQueryInput;
    if (!query) return;
    setTestingAdapter(true);
    try {
      const r = await fetchJson("/adapters/database/query", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query }) });
      setDbQueryResult(JSON.stringify(r, null, 2));
    } catch (e: unknown) {
      setDbQueryResult(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setTestingAdapter(false);
    }
  };

  const listTables = async () => {
    setTestingAdapter(true);
    try {
      const r = await fetchJson("/adapters/database/tables", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
      setDbQueryResult(JSON.stringify(r, null, 2));
    } catch (e: unknown) {
      setDbQueryResult(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setTestingAdapter(false);
    }
  };

  const listFs = async (path?: string) => {
    const p = path || fsPath;
    setTestingAdapter(true);
    try {
      const r = await fetchJson(`/adapters/filesystem/list?path=${encodeURIComponent(p)}`);
      setFsListing(r.entries || r.files || []);
      setFsPath(p);
    } catch (e: unknown) {
      console.error(e);
    } finally {
      setTestingAdapter(false);
    }
  };

  const filteredProcesses = processes.filter(p =>
    !processFilter || (p.executable || "").toLowerCase().includes(processFilter.toLowerCase()) || p.command.toLowerCase().includes(processFilter.toLowerCase())
  );

  const filteredTools = cliTools.filter(t =>
    !toolFilter || t.name.toLowerCase().includes(toolFilter.toLowerCase()) || (t.description || "").toLowerCase().includes(toolFilter.toLowerCase())
  );

  const statusColor = (s: string) => {
    if (s === "ok" || s === "active" || s === "running" || s === "LISTEN") return "text-emerald-400";
    if (s === "error" || s === "stopped") return "text-red-400";
    return "text-yellow-400";
  };

  return (
      <PageLayout title="发现引擎">
        
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 lg:px-6 py-4 border-b border-border bg-card/50 backdrop-blur">
        <div className="flex items-center gap-2 lg:gap-3">
          <div className="p-2 rounded-xl bg-gradient-to-br from-cyan-500/20 to-blue-500/20">
            <Search className="w-5 h-5 text-cyan-400" />
          </div>
          <div>
            <h1 className="text-lg lg:text-xl font-bold">发现引擎</h1>
            <p className="text-xs text-muted-foreground">本地软件扫描与适配器管理</p>
          </div>
        </div>
        <button onClick={runFullScan} disabled={scanning} className={cn("flex items-center gap-2 px-2 lg:px-4 py-2 rounded-lg text-xs lg:text-sm font-medium transition-all touch-manipulation", scanning ? "bg-muted text-muted-foreground" : "bg-gradient-to-r from-cyan-600 to-blue-600 text-white hover:from-cyan-500 hover:to-blue-500 shadow-lg shadow-cyan-500/20")}>
          <RefreshCw className={cn("w-4 h-4", scanning && "animate-spin")} />
          {scanning ? "扫描中..." : "完整扫描"}
        </button>
      </div>

      {/* Scan Summary */}
      {scanResult && (
        <div className="px-3 lg:px-6 py-2 lg:py-3 border-b border-border bg-card/30 flex items-center gap-2 lg:gap-6 flex-wrap text-xs lg:text-sm">
          <span className="flex items-center gap-1.5 text-emerald-400"><CheckCircle className="w-4 h-4" /> Scan complete in {scanResult.scan_duration_ms}ms</span>
          <span className="text-muted-foreground">•</span>
          <span>{processes.length} processes</span>
          <span>{cliTools.length} CLI tools</span>
          <span>{services.length} services</span>
          <span>{adapters.length} adapters</span>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 px-3 lg:px-6 pt-3 border-b border-border">
        {(["scan", "adapters"] as Tab[]).map(t => (
          <button key={t} onClick={() => setTab(t)} className={cn("px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium rounded-lg transition-colors touch-manipulation", tab === t ? "bg-cyan-500/10 text-cyan-400" : "text-muted-foreground hover:text-foreground hover:bg-muted/30")}>
            {t === "scan" ? <><Monitor className="w-4 h-4 inline mr-1.5" />系统扫描</> : <><Plug className="w-4 h-4 inline mr-1.5" />适配器</>}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4">
        {tab === "scan" && (
          <div className="space-y-3 lg:space-y-6">
            {/* Stats Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
              {[
                { icon: Cpu, label: "进程", value: processes.length, color: "cyan" },
                { icon: Terminal, label: "CLI 工具", value: cliTools.length, color: "emerald" },
                { icon: Network, label: "服务", value: services.length, color: "violet" },
                { icon: Plug, label: "适配器", value: adapters.length, color: "amber" },
              ].map(s => (
                <div key={s.label} className="p-4 rounded-xl border border-border bg-card/50 hover:bg-card/80 transition-colors">
                  <div className="flex items-center gap-2 mb-2">
                    <s.icon className={cn("w-4 h-4", `text-${s.color}-400`)} />
                    <span className="text-xs text-muted-foreground">{s.label}</span>
                  </div>
                  <p className="text-xl lg:text-2xl font-bold">{s.value}</p>
                </div>
              ))}
            </div>

            {/* Running Processes */}
            <div className="rounded-xl border border-border bg-card/30">
              <div className="flex items-center justify-between px-2 lg:px-4 py-2 lg:py-3 border-b border-border">
                <h3 className="font-semibold flex items-center gap-2"><Cpu className="w-4 h-4 text-cyan-400" /> 运行中的进程 ({filteredProcesses.length})</h3>
                <input value={processFilter} onChange={e => setProcessFilter(e.target.value)} placeholder="过滤进程..." className="w-full sm:w-64 px-3 py-1.5 rounded-lg border border-border bg-background text-xs lg:text-sm" />
              </div>
              <div className="max-h-80 overflow-y-auto overflow-x-auto">
                <table className="w-full text-xs lg:text-sm min-w-[550px]">
                  <thead className="sticky top-0 bg-card/90 backdrop-blur">
                    <tr className="text-left text-muted-foreground">
                      <th className="px-2 lg:px-4 py-2">PID</th>
                      <th className="px-2 lg:px-4 py-2">名称</th>
                      <th className="px-2 lg:px-4 py-2">用户</th>
                      <th className="px-2 lg:px-4 py-2 text-right">CPU%</th>
                      <th className="px-2 lg:px-4 py-2 text-right">MEM%</th>
                      <th className="px-2 lg:px-4 py-2">命令</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredProcesses.slice(0, 100).map(p => (
                      <tr key={p.pid} className={cn("border-t border-border/50 hover:bg-muted/30 cursor-pointer", expandedProcess === p.pid && "bg-muted/50")} onClick={() => setExpandedProcess(expandedProcess === p.pid ? null : p.pid)}>
                        <td className="px-2 lg:px-4 py-2 font-mono text-xs">{p.pid}</td>
                        <td className="px-2 lg:px-4 py-2 font-medium">{p.executable || p.command.split("/").pop()?.split(" ")[0] || "-"}</td>
                        <td className="px-2 lg:px-4 py-2 text-muted-foreground">{p.user}</td>
                        <td className={cn("px-2 lg:px-4 py-2 text-right font-mono", p.cpu > 50 ? "text-red-400" : p.cpu > 10 ? "text-yellow-400" : "text-emerald-400")}>{p.cpu.toFixed(1)}</td>
                        <td className={cn("px-2 lg:px-4 py-2 text-right font-mono", p.mem > 50 ? "text-red-400" : p.mem > 10 ? "text-yellow-400" : "text-emerald-400")}>{p.mem.toFixed(1)}</td>
                        <td className="px-2 lg:px-4 py-2 text-xs text-muted-foreground truncate max-w-xs">{p.command}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {filteredProcesses.length > 100 && <p className="text-center text-xs text-muted-foreground py-2">Showing 100 of {filteredProcesses.length}</p>}
              </div>
            </div>

            {/* CLI Tools */}
            <div className="rounded-xl border border-border bg-card/30">
              <div className="flex items-center justify-between px-2 lg:px-4 py-2 lg:py-3 border-b border-border">
                <h3 className="font-semibold flex items-center gap-2"><Terminal className="w-4 h-4 text-emerald-400" /> CLI 工具 ({filteredTools.length})</h3>
                <input value={toolFilter} onChange={e => setToolFilter(e.target.value)} placeholder="过滤工具..." className="w-full sm:w-64 px-3 py-1.5 rounded-lg border border-border bg-background text-xs lg:text-sm" />
              </div>
              <div className="max-h-60 overflow-y-auto p-3 lg:p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {filteredTools.map(tool => (
                  <div key={tool.name} className="flex items-center gap-2 px-3 py-2 rounded-lg border text-xs lg:text-sm border-emerald-500/30 bg-emerald-500/5">
                    <CheckCircle className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                    <span className="font-medium truncate">{tool.name}</span>
                    {tool.version && <span className="text-xs text-muted-foreground ml-auto">{tool.version}</span>}
                  </div>
                ))}
              </div>
            </div>

            {/* Network Services */}
            <div className="rounded-xl border border-border bg-card/30">
              <div className="px-2 lg:px-4 py-2 lg:py-3 border-b border-border">
                <h3 className="font-semibold flex items-center gap-2"><Network className="w-4 h-4 text-violet-400" /> 监听中的服务 ({services.length})</h3>
              </div>
              <div className="max-h-60 overflow-y-auto overflow-x-auto">
                <table className="w-full text-xs lg:text-sm min-w-[500px]">
                  <thead className="sticky top-0 bg-card/90 backdrop-blur">
                    <tr className="text-left text-muted-foreground">
                      <th className="px-2 lg:px-4 py-2">协议</th>
                      <th className="px-2 lg:px-4 py-2">地址</th>
                      <th className="px-2 lg:px-4 py-2">端口</th>
                      <th className="px-2 lg:px-4 py-2">状态</th>
                      <th className="px-2 lg:px-4 py-2">进程</th>
                      <th className="px-2 lg:px-4 py-2">PID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {services.map((s, i) => (
                      <tr key={i} className="border-t border-border/50 hover:bg-muted/30">
                        <td className="px-2 lg:px-4 py-2 font-mono text-xs uppercase">{s.protocol}</td>
                        <td className="px-2 lg:px-4 py-2 font-mono text-xs">{s.local_address}</td>
                        <td className="px-2 lg:px-4 py-2 font-mono text-xs font-bold">{s.local_port}</td>
                        <td className={cn("px-2 lg:px-4 py-2 text-xs", statusColor(s.state))}>{s.state}</td>
                        <td className="px-2 lg:px-4 py-2">{s.process_name}</td>
                        <td className="px-2 lg:px-4 py-2 font-mono text-xs">{s.pid}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {tab === "adapters" && (
          <div className="space-y-3 lg:space-y-6">
            {/* Adapter List */}
            <div className="rounded-xl border border-border bg-card/30">
              <div className="flex items-center justify-between px-2 lg:px-4 py-2 lg:py-3 border-b border-border">
                <h3 className="font-semibold flex items-center gap-2"><Plug className="w-4 h-4 text-amber-400" /> Configured Adapters ({adapters.length})</h3>
                <button onClick={() => setShowAddAdapter(!showAddAdapter)} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-600 text-white text-xs lg:text-sm hover:bg-amber-500 transition-colors touch-manipulation">
                  <Plus className="w-3.5 h-3.5" /> Add Adapter
                </button>
              </div>
              {adapters.length === 0 ? (
                <div className="p-8 text-center text-muted-foreground">
                  <Plug className="w-8 h-8 mx-auto mb-2 opacity-50" />
                  <p>尚未配置适配器。</p>
                  <p className="text-xs mt-1">添加 REST、数据库或文件系统适配器以开始使用。</p>
                </div>
              ) : (
                <div className="p-4 grid grid-cols-1 sm:grid-cols-2 gap-2 lg:gap-3">
                  {adapters.map(a => (
                    <div key={a.name} onClick={() => setActiveAdapterId(activeAdapterId === a.name ? null : a.name)} className={cn("p-4 rounded-xl border cursor-pointer transition-all", activeAdapterId === a.name ? "border-amber-500 bg-amber-500/10" : "border-border hover:border-amber-500/50 bg-card/50")}>
                      <div className="flex items-center gap-2 lg:gap-3 mb-2">
                        <Server className="w-5 h-5 text-amber-400" />
                        <div className="flex-1 min-w-0">
                          <p className="font-medium truncate">{a.name}</p>
                          <p className="text-xs text-muted-foreground truncate">{a.description}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 lg:gap-3 text-xs text-muted-foreground">
                        <span className="flex items-center gap-1"><Clock className="w-3 h-3" /> {new Date(a.registered_at * 1000).toLocaleDateString()}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Add Adapter Form */}
            {showAddAdapter && (
              <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-5 space-y-4">
                <h3 className="font-semibold flex items-center gap-2"><Plus className="w-4 h-4 text-amber-400" /> 配置新适配器</h3>
                <div className="flex gap-2">
                  {(["rest", "database", "filesystem"] as const).map(t => (
                    <button key={t} onClick={() => setAdapterType(t)} className={cn("px-2 lg:px-4 py-2 rounded-lg text-xs lg:text-sm font-medium transition-colors touch-manipulation", adapterType === t ? "bg-amber-600 text-white" : "bg-muted text-muted-foreground hover:text-foreground")}>
                      {t === "rest" ? <><Globe className="w-3.5 h-3.5 inline mr-1" />REST</> : t === "database" ? <><Database className="w-3.5 h-3.5 inline mr-1" />数据库</> : <><FolderOpen className="w-3.5 h-3.5 inline mr-1" />文件系统</>}
                    </button>
                  ))}
                </div>

                {adapterType === "rest" && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 lg:gap-3">
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">基础 URL</label>
                      <input value={adapterConfig.url || ""} onChange={e => setAdapterConfig({...adapterConfig, url: e.target.value})} placeholder="https://api.example.com" className="w-full px-3 py-2 rounded-lg border border-border bg-background text-xs lg:text-sm" />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">认证方式</label>
                      <select value={adapterConfig.auth_type || "none"} onChange={e => setAdapterConfig({...adapterConfig, auth_type: e.target.value})} className="w-full px-3 py-2 rounded-lg border border-border bg-background text-xs lg:text-sm">
                        <option value="none">None</option>
                        <option value="bearer">Bearer Token</option>
                        <option value="basic">Basic 认证</option>
                      </select>
                    </div>
                    <div className="col-span-2">
                      <label className="text-xs text-muted-foreground mb-1 block">认证 Token（如需要）</label>
                      <input value={adapterConfig.auth_token || ""} onChange={e => setAdapterConfig({...adapterConfig, auth_token: e.target.value})} placeholder="Bearer token 或 base64 凭据" className="w-full px-3 py-2 rounded-lg border border-border bg-background text-xs lg:text-sm" type="password" />
                    </div>
                    <div className="col-span-2">
                      <label className="text-xs text-muted-foreground mb-1 block">附加请求头（JSON）</label>
                      <input value={adapterConfig.headers || ""} onChange={e => setAdapterConfig({...adapterConfig, headers: e.target.value})} placeholder='{"X-Custom": "value"}' className="w-full px-3 py-2 rounded-lg border border-border bg-background text-xs lg:text-sm font-mono" />
                    </div>
                  </div>
                )}

                {adapterType === "database" && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 lg:gap-3">
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">数据库类型</label>
                      <select value={adapterConfig.db_type || "sqlite"} onChange={e => setAdapterConfig({...adapterConfig, db_type: e.target.value})} className="w-full px-3 py-2 rounded-lg border border-border bg-background text-xs lg:text-sm">
                        <option value="sqlite">SQLite</option>
                        <option value="postgresql">PostgreSQL</option>
                        <option value="mysql">MySQL</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">连接字符串</label>
                      <input value={adapterConfig.connection_string || ""} onChange={e => setAdapterConfig({...adapterConfig, connection_string: e.target.value})} placeholder="sqlite:///path/to/db.sqlite" className="w-full px-3 py-2 rounded-lg border border-border bg-background text-xs lg:text-sm" />
                    </div>
                  </div>
                )}

                {adapterType === "filesystem" && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 lg:gap-3">
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">根路径</label>
                      <input value={adapterConfig.root_path || ""} onChange={e => setAdapterConfig({...adapterConfig, root_path: e.target.value})} placeholder="/home/user/documents" className="w-full px-3 py-2 rounded-lg border border-border bg-background text-xs lg:text-sm" />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">文件匹配模式（逗号分隔）</label>
                      <input value={adapterConfig.patterns || ""} onChange={e => setAdapterConfig({...adapterConfig, patterns: e.target.value})} placeholder="*.md, *.txt, *.json" className="w-full px-3 py-2 rounded-lg border border-border bg-background text-xs lg:text-sm" />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">监听变更</label>
                      <select value={adapterConfig.watch || "false"} onChange={e => setAdapterConfig({...adapterConfig, watch: e.target.value})} className="w-full px-3 py-2 rounded-lg border border-border bg-background text-xs lg:text-sm">
                        <option value="false">否</option>
                        <option value="true">是</option>
                      </select>
                    </div>
                  </div>
                )}

                <div className="flex gap-2">
                  <button onClick={configureAdapter} disabled={testingAdapter} className="flex items-center gap-2 px-2 lg:px-4 py-2 rounded-lg bg-amber-600 text-white text-xs lg:text-sm hover:bg-amber-500 transition-colors disabled:opacity-50 touch-manipulation">
                    {testingAdapter ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
                    Configure & Test
                  </button>
                </div>
                {adapterResult && (
                  <pre className="p-3 rounded-lg bg-background border border-border text-xs font-mono overflow-x-auto max-h-48">{adapterResult}</pre>
                )}
              </div>
            )}

            {/* Quick Actions */}
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2 lg:gap-4">
              {/* REST 探测 */}
              <div className="rounded-xl border border-border bg-card/30 p-3 lg:p-4 space-y-3">
                <h4 className="font-medium flex items-center gap-2 text-xs lg:text-sm"><Globe className="w-4 h-4 text-blue-400" /> REST 探测</h4>
                <input value={restProbeUrl} onChange={e => setRestProbeUrl(e.target.value)} placeholder="https://httpbin.org/get" className="w-full px-3 py-2 rounded-lg border border-border bg-background text-xs lg:text-sm" />
                <button onClick={probeRest} disabled={testingAdapter || !restProbeUrl} className="w-full px-3 py-2 rounded-lg bg-blue-600 text-white text-xs lg:text-sm hover:bg-blue-500 disabled:opacity-50 transition-colors touch-manipulation">
                  {testingAdapter ? <RefreshCw className="w-3.5 h-3.5 animate-spin inline" /> : <Play className="w-3.5 h-3.5 inline" />} Probe
                </button>
                {restProbeResult && <pre className="p-2 rounded bg-background border border-border text-xs font-mono overflow-x-auto max-h-32">{restProbeResult}</pre>}
              </div>

              {/* DB Query */}
              <div className="rounded-xl border border-border bg-card/30 p-3 lg:p-4 space-y-3">
                <h4 className="font-medium flex items-center gap-2 text-xs lg:text-sm"><Database className="w-4 h-4 text-purple-400" /> 数据库查询</h4>
                <input value={dbQueryInput} onChange={e => setDbQueryInput(e.target.value)} placeholder="SELECT * FROM table LIMIT 10" className="w-full px-3 py-2 rounded-lg border border-border bg-background text-xs lg:text-sm font-mono" onKeyDown={e => e.key === "Enter" && queryDb()} />
                <div className="flex gap-2">
                  <button onClick={() => queryDb()} disabled={testingAdapter || !dbQueryInput} className="flex-1 px-3 py-2 rounded-lg bg-purple-600 text-white text-xs lg:text-sm hover:bg-purple-500 disabled:opacity-50 transition-colors touch-manipulation">
                    <Play className="w-3.5 h-3.5 inline" /> Query
                  </button>
                  <button onClick={listTables} disabled={testingAdapter} className="px-3 py-2 rounded-lg bg-muted text-foreground text-xs lg:text-sm hover:bg-muted/80 disabled:opacity-50 transition-colors touch-manipulation">
                    <Layers className="w-3.5 h-3.5 inline" /> Tables
                  </button>
                </div>
                {dbQueryResult && <pre className="p-2 rounded bg-background border border-border text-xs font-mono overflow-x-auto max-h-32">{dbQueryResult}</pre>}
              </div>

              {/* 文件系统浏览 */}
              <div className="rounded-xl border border-border bg-card/30 p-3 lg:p-4 space-y-3">
                <h4 className="font-medium flex items-center gap-2 text-xs lg:text-sm"><FolderOpen className="w-4 h-4 text-emerald-400" /> 文件系统浏览</h4>
                <div className="flex gap-2">
                  <input value={fsPath} onChange={e => setFsPath(e.target.value)} placeholder="/" className="flex-1 px-3 py-2 rounded-lg border border-border bg-background text-xs lg:text-sm" onKeyDown={e => e.key === "Enter" && listFs()} />
                  <button onClick={() => listFs()} disabled={testingAdapter} className="px-3 py-2 rounded-lg bg-emerald-600 text-white text-xs lg:text-sm hover:bg-emerald-500 disabled:opacity-50 transition-colors touch-manipulation">
                    <Eye className="w-3.5 h-3.5" />
                  </button>
                </div>
                <div className="max-h-40 overflow-y-auto space-y-0.5">
                  {fsPath !== "/" && (
                    <button onClick={() => { const parent = fsPath.split("/").slice(0, -1).join("/") || "/"; listFs(parent); }} className="w-full text-left px-2 py-1 rounded text-xs lg:text-sm text-muted-foreground hover:bg-muted/50 flex items-center gap-1.5 touch-manipulation">
                      <ArrowRight className="w-3 h-3 rotate-180" /> ..
                    </button>
                  )}
                  {fsListing.map((f, i) => (
                    <button key={i} onClick={() => { if (f.is_dir) listFs(fsPath === "/" ? `/${f.name}` : `${fsPath}/${f.name}`); }} className={cn("w-full text-left px-2 py-1 rounded text-xs lg:text-sm hover:bg-muted/50 flex items-center gap-1.5 touch-manipulation", f.is_dir ? "text-emerald-400" : "text-foreground")}>
                      {f.is_dir ? <FolderOpen className="w-3 h-3 shrink-0" /> : <FileText className="w-3 h-3 shrink-0" />}
                      <span className="truncate">{f.name}</span>
                      {!f.is_dir && <span className="ml-auto text-xs text-muted-foreground">{(f.size / 1024).toFixed(1)}K</span>}
                    </button>
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
