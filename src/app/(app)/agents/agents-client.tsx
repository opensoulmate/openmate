'use client';
import { useState, useEffect, useRef, useCallback } from 'react';
import { Bot, CheckCircle, XCircle, Loader2, Download, RefreshCw, Play, Pause, Trash2, ArrowUpCircle, Terminal, Monitor, Apple, ChevronDown, ChevronRight, Square, CheckSquare, RotateCcw } from 'lucide-react';
import { getApiBaseUrl, getToken } from '@/lib/api-client';
import { useTranslation } from 'react-i18next';
import { PageLayout } from '@/components/page-layout';
import { DetailPanel, useDetailPanel } from '@/components/detail-panel';

interface AgentInfo {
  id: string; name: string; binary: string; description: string; icon: string;
  available: boolean; version?: string; path?: string; installCommand?: string; os?: string;
  skillsManaged?: boolean;
  provider?: string; model?: string;
}

interface InstallState {
  status: 'idle' | 'starting' | 'running' | 'done' | 'error';
  progress: number;
  lines: string[];
  error?: string;
}

export function AgentsClient() {
  const { t } = useTranslation();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [os, setOs] = useState('linux');
  const [filter, setFilter] = useState<'all' | 'available' | 'unavailable'>('all');
  const [category, setCategory] = useState("all");
  const [installs, setInstalls] = useState<Record<string, InstallState>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchAction, setBatchAction] = useState<string | null>(null);
  const [configuringAgent, setConfiguringAgent] = useState<string | null>(null);
  const [agentConfigs, setAgentConfigs] = useState<Record<string, {provider: string; model: string; custom?: boolean}>>({});
  const [providers, setProviders] = useState<Array<{name: string; base_url: string; models: Record<string, string>}>>([]);
  const eventSources = useRef<Record<string, EventSource>>({});
  const { detailItem, openDetail, closeDetail, isOpen: hasDetail } = useDetailPanel();

  const handleAgentClick = useCallback((agent: AgentInfo) => {
    openDetail(agent.id, 'agent', agent);
  }, [openDetail]);

  const detect = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${getApiBaseUrl()}/api/agents/detect`, { headers: { Authorization: `Bearer ${getToken()}` } });
      if (res.ok) {
        const data = await res.json();
        setOs(data.os || 'linux');
        setAgents(data.agents || []);
      }
    } catch (e) { console.error('Agent detection failed:', e); }
    setLoading(false);
  }, []);

  useEffect(() => { 
    detect();
    // Load Gland providers
    fetch(`${getApiBaseUrl()}/api/gland/providers`, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then(r => r.json()).then(d => setProviders(d.providers || [])).catch(() => {});
    // Load saved agent configs from localStorage
    try {
      const saved = localStorage.getItem('openmate-agent-configs');
      if (saved) setAgentConfigs(JSON.parse(saved));
    } catch {}
  }, [detect]);
  useEffect(() => { return () => { Object.values(eventSources.current).forEach(es => es.close()); }; }, []);

  // ─── Actions ──────────────────────────────────────────────────

  const startInstall = async (agent: AgentInfo) => {
    if (!agent.installCommand) return;
    setInstalls(prev => ({ ...prev, [agent.id]: { status: 'starting', progress: 0, lines: [] } }));
    setExpanded(prev => ({ ...prev, [agent.id]: true }));

    try {
      const res = await fetch(`${getApiBaseUrl()}/api/agents/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({ agent_id: agent.id }),
      });
      const data = await res.json();
      if (!data.success) {
        setInstalls(prev => ({ ...prev, [agent.id]: { status: 'error', progress: 0, lines: [], error: data.error } }));
        return;
      }
      setInstalls(prev => ({ ...prev, [agent.id]: { status: 'running', progress: 5, lines: [] } }));
      const es = new EventSource(`${getApiBaseUrl()}/api/agents/install/${agent.id}/progress`);
      eventSources.current[agent.id] = es;
      es.onmessage = (event) => {
        try {
          const d = JSON.parse(event.data);
          setInstalls(prev => {
            const cur = prev[agent.id] || { status: 'running', progress: 0, lines: [] };
            return { ...prev, [agent.id]: { status: d.status || cur.status, progress: d.progress ?? cur.progress, lines: d.line ? [...cur.lines, d.line] : cur.lines, error: d.error || cur.error } };
          });
          if (d.status === 'done' || d.status === 'error') { es.close(); delete eventSources.current[agent.id]; if (d.status === 'done') setTimeout(detect, 2000); }
        } catch {}
      };
      es.onerror = () => { es.close(); delete eventSources.current[agent.id]; };
    } catch { setInstalls(prev => ({ ...prev, [agent.id]: { status: 'error', progress: 0, lines: [], error: 'Network error' } })); }
  };

  const handleUninstall = async (agent: AgentInfo) => {
    if (!confirm(t('agents.confirmUninstall', { name: agent.name }))) return;
    try {
      const res = await fetch(`${getApiBaseUrl()}/api/agents/uninstall`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({ agent_id: agent.id }),
      });
      const data = await res.json();
      if (data.success) setTimeout(detect, 1000);
    } catch {}
  };

  const handleUpdate = async (agent: AgentInfo) => {
    setInstalls(prev => ({ ...prev, [agent.id]: { status: 'starting', progress: 0, lines: [] } }));
    setExpanded(prev => ({ ...prev, [agent.id]: true }));
    try {
      const res = await fetch(`${getApiBaseUrl()}/api/agents/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({ agent_id: agent.id }),
      });
      const data = await res.json();
      if (data.success) {
        setInstalls(prev => ({ ...prev, [agent.id]: { status: 'running', progress: 5, lines: [] } }));
        const es = new EventSource(`${getApiBaseUrl()}/api/agents/install/${agent.id}/progress`);
        eventSources.current[agent.id] = es;
        es.onmessage = (event) => {
          try {
            const d = JSON.parse(event.data);
            setInstalls(prev => {
              const cur = prev[agent.id] || { status: 'running', progress: 0, lines: [] };
              return { ...prev, [agent.id]: { status: d.status || cur.status, progress: d.progress ?? cur.progress, lines: d.line ? [...cur.lines, d.line] : cur.lines, error: d.error || cur.error } };
            });
            if (d.status === 'done' || d.status === 'error') { es.close(); delete eventSources.current[agent.id]; if (d.status === 'done') setTimeout(detect, 2000); }
          } catch {}
        };
        es.onerror = () => { es.close(); delete eventSources.current[agent.id]; };
      }
    } catch {}
  };

  // ─── Batch Operations ─────────────────────────────────────────

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    const visible = filtered.map(a => a.id);
    if (visible.every(id => selected.has(id))) {
      setSelected(new Set());
    } else {
      setSelected(new Set(visible));
    }
  };

  const batchInstall = async () => {
    setBatchAction('install');
    const toInstall = agents.filter(a => selected.has(a.id) && !a.available);
    for (const agent of toInstall) {
      await startInstall(agent);
      await new Promise(r => setTimeout(r, 500));
    }
    setBatchAction(null);
  };

  const batchUninstall = async () => {
    if (!confirm(t('agents.confirmBatchUninstall', { count: selected.size }))) return;
    setBatchAction('uninstall');
    const toUninstall = agents.filter(a => selected.has(a.id) && a.available);
    for (const agent of toUninstall) {
      await handleUninstall(agent);
      await new Promise(r => setTimeout(r, 300));
    }
    setBatchAction(null);
    setSelected(new Set());
  };

  // ─── Filter ───────────────────────────────────────────────────

  const filtered = agents.filter(a => {
    const matchCategory = category === "all" || (a as any).category === category;
    if (!matchCategory) return false;
    if (filter === 'available') return a.available;
    if (filter === 'unavailable') return !a.available;
    return true;
  });
  const availableCount = agents.filter(a => a.available).length;

  if (loading) return <div className="flex items-center justify-center h-full"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>;

  // Build workspace content from detail item
  const workspaceContent = detailItem?.type === 'agent' ? (
    <DetailPanel
      title={detailItem.data.name}
      subtitle={detailItem.data.description}
      icon={<span className="text-lg">{detailItem.data.icon}</span>}
      badge={detailItem.data.available ? '可用' : '不可用'}
      onClose={closeDetail}
      sections={[
        {
          title: '基本信息',
          items: [
            { label: 'ID', value: detailItem.data.id },
            { label: '二进制', value: detailItem.data.binary || 'N/A' },
            { label: '路径', value: detailItem.data.path || 'N/A' },
            { label: '操作系统', value: detailItem.data.os || 'N/A' },
          ],
        },
        {
          title: '配置',
          items: [
            { label: '提供商', value: detailItem.data.provider || '默认' },
            { label: '模型', value: detailItem.data.model || '默认' },
          ],
        },
      ]}
    />
  ) : null;

  // 测试连接：真 ACP 探测（POST /api/agents/probe）——检测成功=真握手，禁一刀切判定
  const [testing, setTesting] = useState<Record<string, 'running' | 'ok' | 'fail'>>({});
  const testConnection = useCallback(async (agent: AgentInfo) => {
    setTesting(prev => ({ ...prev, [agent.id]: 'running' }));
    try {
      const res = await fetch(`${getApiBaseUrl()}/api/agents/probe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({ agent_id: agent.id }),
      });
      const data = await res.json();
      setTesting(prev => ({ ...prev, [agent.id]: data?.ok ? 'ok' : 'fail' }));
    } catch {
      setTesting(prev => ({ ...prev, [agent.id]: 'fail' }));
    }
  }, []);

  // ── 自定义Agent：一行配置即Agent（借鉴 AionUi配置行/Paseo extends:acp/Octo AgentCard/Paperclip配置）──
  const [showAddAgent, setShowAddAgent] = useState(false);
  const [newAgent, setNewAgent] = useState({ id: '', name: '', icon: '🤖', description: '', cmd: '', cwd: '', transport: 'stdio' });
  const acpBase = typeof window !== 'undefined' ? `http://${window.location.hostname}:8092` : 'http://127.0.0.1:8092';
  const loadCustom = useCallback(async () => {
    try {
      const res = await fetch(`${acpBase}/api/agents`);
      const data = await res.json();
      const rows = ((data.agents || []) as any[]).filter(a => a.custom).map(a => ({
        id: a.id, name: a.name || a.id, icon: a.icon || '🤖', description: a.description || '',
        available: !!a.installed, version: '', installCommand: '', custom: true, binary: a.cmd?.[0] || '',
      })) as AgentInfo[];
      if (rows.length) setAgents(prev => [...rows, ...prev.filter(p => !rows.some(r => r.id === p.id))]);
    } catch { /* 代理不可达时列表保持原样 */ }
  }, [acpBase]);
  useEffect(() => { loadCustom(); /* eslint-disable-next-line */ }, []);
  const submitCustomAgent = useCallback(async () => {
    const cmdParts = newAgent.cmd.trim().split(/\s+/).filter(Boolean);
    if (!newAgent.id.trim() || !cmdParts.length) return;
    try {
      const res = await fetch(`${acpBase}/api/agents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: newAgent.id.trim(), name: newAgent.name.trim() || newAgent.id.trim(),
          icon: newAgent.icon || '🤖', description: newAgent.description.trim(),
          cmd: cmdParts, cwd: newAgent.cwd.trim() || undefined, transport: newAgent.transport,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setShowAddAgent(false);
        setNewAgent({ id: '', name: '', icon: '🤖', description: '', cmd: '', cwd: '', transport: 'stdio' });
        await detect(); await loadCustom();
      } else { alert(data.detail || '保存失败'); }
    } catch { alert('代理不可达'); }
  }, [newAgent, acpBase, detect, loadCustom]);
  const deleteCustomAgent = useCallback(async (id: string) => {
    try { await fetch(`${acpBase}/api/agents/${id}`, { method: 'DELETE' }); } catch { /* 假删也要从列表消失 */ }
    setAgents(prev => prev.filter(a => a.id !== id));
  }, [acpBase]);

  return (
    <PageLayout
      title={t('agents.title')}
      icon={<Bot className="w-5 h-5" />}
      badge={agents.length + " 个Agent"}
      workspace={workspaceContent}
      headerActions={
        <>
        <button onClick={detect} className="px-2 lg:px-4 py-2 rounded-lg border hover:bg-muted flex items-center gap-2 text-xs lg:text-sm">
          <RefreshCw className="w-4 h-4" /> {t('agents.autoDetect')}
        </button>
        <button onClick={() => setShowAddAgent(true)} className="px-2 lg:px-4 py-2 rounded-lg border hover:bg-muted flex items-center gap-2 text-xs lg:text-sm">
          ＋ {t('agents.addAgent')}
        </button>
        </>
      }
    >
      <div className="px-3 lg:px-6 py-4 lg:py-6 h-full overflow-y-auto">
        {/* Stats */}

      <div className="grid grid-cols-2 md:grid-cols-3 gap-2 md:gap-4 mb-3 lg:mb-6">
        <div className="p-4 rounded-xl border bg-card"><p className="text-xl lg:text-2xl font-bold text-primary">{agents.length}</p><p className="text-xs lg:text-sm text-muted-foreground">{t('agents.title')}</p></div>
        <div className="p-4 rounded-xl border bg-card"><p className="text-xl lg:text-2xl font-bold text-green-500">{availableCount}</p><p className="text-xs lg:text-sm text-muted-foreground">{t('agents.available')}</p></div>
        <div className="p-4 rounded-xl border bg-card"><p className="text-xl lg:text-2xl font-bold text-muted-foreground">{agents.length - availableCount}</p><p className="text-xs lg:text-sm text-muted-foreground">{t('agents.unavailable')}</p></div>
      </div>

      {/* Filter + Batch */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
        <div className="flex gap-2 flex-wrap">
          {(['all', 'available', 'unavailable'] as const).map(f => (
            <button key={f} onClick={() => setFilter(f)} className={`px-3 py-1.5 rounded-lg text-xs lg:text-sm transition-colors touch-manipulation ${filter === f ? 'bg-primary text-primary-foreground' : 'border hover:bg-muted'}`}>
              {f === 'all' ? t('agents.allAgents') : f === 'available' ? t('agents.onlyAvailable') : t('agents.onlyUnavailable')}
            </button>
          ))}
        </div>
        {selected.size > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">{t('agents.selectAll')} {selected.size}</span>
            <button onClick={batchInstall} disabled={!!batchAction}
              className="px-3 py-1.5 rounded-lg text-xs bg-primary text-primary-foreground hover:bg-primary/90 flex items-center gap-1 disabled:opacity-50 touch-manipulation">
              {batchAction === 'install' ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />} {t('agents.batchInstall')}
            </button>
            <button onClick={batchUninstall} disabled={!!batchAction}
              className="px-3 py-1.5 rounded-lg text-xs border border-red-500/30 text-red-500 hover:bg-red-500/5 flex items-center gap-1 disabled:opacity-50 touch-manipulation">
              {batchAction === 'uninstall' ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />} {t('agents.deleteAgent')}
            </button>
            <button onClick={() => setSelected(new Set())} className="px-2 py-1.5 rounded-lg text-xs border hover:bg-muted touch-manipulation">{t('agents.deselectAll')}</button>
          </div>
        )}
      </div>

      {/* Select all */}
      <div className="flex items-center gap-2 mb-3">
        <button onClick={toggleSelectAll} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground touch-manipulation">
          {filtered.length > 0 && filtered.every(a => selected.has(a.id)) ? <CheckSquare size={14} /> : <Square size={14} />}
          {t('agents.selectAll')}
        </button>
      </div>

      {/* Agent Grid */}
      <div className="flex flex-col gap-2">
        {filtered.map(agent => {
          const install = installs[agent.id];
          const isInstalling = install && (install.status === 'running' || install.status === 'starting');
          const isExpanded = expanded[agent.id];
          const isDetailSelected = detailItem?.id === agent.id;
          const isSelected = selected.has(agent.id);

          return (
            <div key={agent.id} className={`rounded-xl border bg-card transition-all cursor-pointer ${isDetailSelected ? 'border-primary ring-1 ring-primary/30' : isSelected ? 'border-primary/50' : agent.available ? 'border-green-500/30' : 'border-border'} hover:border-primary/50`}
                  onClick={() => handleAgentClick(agent)}>
              {/* AionUi布局铁律：每个agent独立一行——logo｜名称+状态药丸｜测试连接｜编辑 */}
              <div className="px-3 py-2.5 flex items-center gap-3">
                <button onClick={(e) => { e.stopPropagation(); toggleSelect(agent.id); }} className="shrink-0 touch-manipulation">
                  {isSelected ? <CheckSquare size={15} className="text-primary" /> : <Square size={15} className="text-muted-foreground" />}
                </button>
                {(agent as any).logo ? (
                  <img src={(agent as any).logo} alt={agent.name} className="w-9 h-9 rounded-lg object-contain bg-muted shrink-0" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
                ) : (
                  <span className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center text-lg shrink-0">{agent.icon}</span>
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="font-medium truncate text-sm">{agent.name}</h3>
                    {agent.available ? (
                      <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-green-500/10 text-green-600 shrink-0">{t('agents.installed')}{agent.version ? ` v${agent.version}` : ''}</span>
                    ) : (
                      <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-muted text-muted-foreground shrink-0">{t('agents.unavailable')}</span>
                    )}
                  </div>
                  {agent.description && <p className="text-xs text-muted-foreground truncate hidden sm:block">{agent.description}</p>}
                </div>
                <button onClick={(e) => { e.stopPropagation(); testConnection(agent); }}
                  className="px-2.5 py-1.5 rounded-lg text-xs border hover:bg-muted flex items-center gap-1 shrink-0 touch-manipulation" title={t('agents.testConnection')}>
                  {testing[agent.id] === 'running' ? <Loader2 className="w-3 h-3 animate-spin" />
                    : testing[agent.id] === 'ok' ? <CheckCircle className="w-3 h-3 text-green-500" />
                    : testing[agent.id] === 'fail' ? <XCircle className="w-3 h-3 text-red-500" />
                    : null}
                  {testing[agent.id] === 'ok' ? 'OK' : testing[agent.id] === 'fail' ? 'FAIL' : t('agents.testConnection')}
                </button>
                <button onClick={(e) => { e.stopPropagation(); setConfiguringAgent(configuringAgent === agent.id ? null : agent.id); }}
                  className="px-2.5 py-1.5 rounded-lg text-xs border hover:bg-muted shrink-0 touch-manipulation" title={t('agents.modelConfig')}>
                  {t('agents.edit')}
                </button>
                {agent.available ? (
                  <>
                    <button onClick={(e) => { e.stopPropagation(); handleUpdate(agent); }} disabled={!!isInstalling}
                      className="px-2 py-1.5 rounded-lg text-xs border hover:bg-muted disabled:opacity-50 touch-manipulation" title={t('agents.restart')}>
                      <ArrowUpCircle className="w-3 h-3" />
                    </button>
                    <button onClick={(e) => { e.stopPropagation(); (agent as any).custom ? deleteCustomAgent(agent.id) : handleUninstall(agent); }}
                      className="px-2 py-1.5 rounded-lg text-xs border border-red-500/30 text-red-500 hover:bg-red-500/5 touch-manipulation" title={t('agents.deleteAgent')}>
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </>
                ) : (
                  <button onClick={(e) => { e.stopPropagation(); startInstall(agent); }} disabled={!!isInstalling}
                    className="px-2.5 py-1.5 rounded-lg text-xs border hover:bg-muted flex items-center gap-1 disabled:opacity-50 touch-manipulation">
                    {isInstalling ? <><Loader2 className="w-3 h-3 animate-spin" /> {t('agents.installing')}</> : <><Download className="w-3 h-3" /> {t('agents.install')}</>}
                  </button>
                )}
              </div>

              {/* Install progress */}
              {install && install.status !== 'idle' && (
                <div className="border-t border-border">
                  <div className="h-1 bg-muted">
                    <div className={`h-full transition-all duration-500 ${install.status === 'done' ? 'bg-green-500' : install.status === 'error' ? 'bg-red-500' : 'bg-primary'}`}
                      style={{ width: `${install.progress}%` }} />
                  </div>
                  <div className="px-2 lg:px-4 py-2 flex items-center justify-between">
                    <div className="flex items-center gap-2 text-xs">
                      {install.status === 'done' && <span className="text-green-500 flex items-center gap-1"><CheckCircle className="w-3 h-3" /> {t('agents.installDone')}</span>}
                      {install.status === 'error' && <span className="text-red-500">{install.error}</span>}
                      {install.status === 'running' && <span className="text-muted-foreground flex items-center gap-1"><Loader2 className="w-3 h-3 animate-spin" /> {install.progress}%</span>}
                      {install.status === 'starting' && <span className="text-muted-foreground">{t('agents.detecting')}</span>}
                    </div>
                    {install.lines.length > 0 && (
                      <button onClick={(e) => { e.stopPropagation(); setExpanded(prev => ({ ...prev, [agent.id]: !prev[agent.id] })); }} className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1">
                        {isExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}{t('agents.logs')}
                      </button>
                    )}
                  </div>
                  {isExpanded && install.lines.length > 0 && (
                    <div className="px-2 lg:px-4 pb-3 max-h-40 overflow-y-auto">
                      <pre className="text-[10px] text-muted-foreground font-mono whitespace-pre-wrap">{install.lines.slice(-20).join('\n')}</pre>
                    </div>
                  )}
                </div>
              )}

              {/* Install command hint */}
              {agent.installCommand && !agent.available && !install && (
                <div className="px-2 lg:px-4 pb-3">
                  <div className="p-2 rounded-lg bg-muted text-xs font-mono flex items-center gap-1.5 text-muted-foreground"><Terminal className="w-3 h-3 shrink-0" />{agent.installCommand}</div>
                </div>
              )}

              {/* Model config panel */}
              {agent.available && configuringAgent === agent.id && (
                <div className="border-t border-border px-2 lg:px-4 py-3 space-y-2">
                  <div className="text-xs font-medium text-muted-foreground mb-2">{t('agents.modelConfig')}</div>
                  <label className="flex items-center gap-2 text-xs cursor-pointer">
                    <input type="checkbox" checked={!agentConfigs[agent.id]?.custom}
                      onChange={() => {
                        const newConfigs = { ...agentConfigs };
                        delete newConfigs[agent.id];
                        setAgentConfigs(newConfigs);
                        localStorage.setItem('openmate-agent-configs', JSON.stringify(newConfigs));
                      }} />
                    {t('agents.useGlobalDefault')}
                  </label>
                  <label className="flex items-center gap-2 text-xs cursor-pointer">
                    <input type="checkbox" checked={!!agentConfigs[agent.id]?.custom}
                      onChange={() => {
                        const newConfigs = { ...agentConfigs, [agent.id]: { custom: true, provider: '', model: '' } };
                        setAgentConfigs(newConfigs);
                        localStorage.setItem('openmate-agent-configs', JSON.stringify(newConfigs));
                      }} />
                    {t('agents.customModel')}
                  </label>
                  {agentConfigs[agent.id]?.custom && (
                    <>
                      <div className="flex gap-2 items-center">
                        <label className="text-xs text-muted-foreground w-16">Provider</label>
                        <select
                          value={agentConfigs[agent.id]?.provider || ''}
                          onChange={e => {
                            const cfg = { ...agentConfigs[agent.id], provider: e.target.value, model: '' };
                            const newConfigs = { ...agentConfigs, [agent.id]: cfg };
                            setAgentConfigs(newConfigs);
                            localStorage.setItem('openmate-agent-configs', JSON.stringify(newConfigs));
                          }}
                          className="flex-1 px-2 py-1 rounded border border-border bg-background text-xs"
                        >
                          <option value="">{t('agents.selectProvider')}</option>
                          {providers.map(p => (
                            <option key={p.name} value={p.name}>{p.name}</option>
                          ))}
                        </select>
                      </div>
                      <div className="flex gap-2 items-center">
                        <label className="text-xs text-muted-foreground w-16">Model</label>
                        <select
                          value={agentConfigs[agent.id]?.model || ''}
                          onChange={e => {
                            const cfg = { ...agentConfigs[agent.id], model: e.target.value };
                            const newConfigs = { ...agentConfigs, [agent.id]: cfg };
                            setAgentConfigs(newConfigs);
                            localStorage.setItem('openmate-agent-configs', JSON.stringify(newConfigs));
                          }}
                          className="flex-1 px-2 py-1 rounded border border-border bg-background text-xs"
                        >
                          <option value="">{t('agents.selectModel')}</option>
                          {(() => {
                            const cfg = agentConfigs[agent.id];
                            const prov = providers.find(p => p.name === cfg?.provider);
                            if (!prov) return null;
                            return Object.entries(prov.models).map(([type, model]) => (
                              <option key={`${type}-${model}`} value={model}>{type}: {model}</option>
                            ));
                          })()}
                        </select>
                      </div>
                    </>
                  )}
                  <div className="text-xs text-muted-foreground">
                    {agentConfigs[agent.id]?.custom 
                      ? (agentConfigs[agent.id]?.provider && agentConfigs[agent.id]?.model 
                          ? <span className="text-green-500">✓ {agentConfigs[agent.id].provider}/{agentConfigs[agent.id].model}</span>
                          : t('agents.selectProviderAndModel'))
                      : t('agents.usingGlobalConfig')}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
      </div>
      {/* 添加自定义Agent：一行配置即注册（注册=检测=列表一条真源） */}
      {showAddAgent && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setShowAddAgent(false)}>
          <div className="bg-card rounded-xl border p-4 w-full max-w-md space-y-2" onClick={e => e.stopPropagation()}>
            <h3 className="font-medium text-sm mb-1">{t('agents.addAgent')}</h3>
            <div className="grid grid-cols-2 gap-2">
              <label className="text-xs text-muted-foreground">ID（英文唯一标识）
                <input value={newAgent.id} onChange={e => setNewAgent({ ...newAgent, id: e.target.value })}
                  className="mt-1 w-full px-2 py-1.5 rounded border border-border bg-background text-xs" placeholder="my-agent" />
              </label>
              <label className="text-xs text-muted-foreground">名称
                <input value={newAgent.name} onChange={e => setNewAgent({ ...newAgent, name: e.target.value })}
                  className="mt-1 w-full px-2 py-1.5 rounded border border-border bg-background text-xs" placeholder="我的Agent" />
              </label>
              <label className="text-xs text-muted-foreground">图标（emoji）
                <input value={newAgent.icon} onChange={e => setNewAgent({ ...newAgent, icon: e.target.value })}
                  className="mt-1 w-full px-2 py-1.5 rounded border border-border bg-background text-xs" />
              </label>
              <label className="text-xs text-muted-foreground">传输方式
                <select value={newAgent.transport} onChange={e => setNewAgent({ ...newAgent, transport: e.target.value })}
                  className="mt-1 w-full px-2 py-1.5 rounded border border-border bg-background text-xs">
                  <option value="stdio">stdio</option>
                  <option value="pty">pty</option>
                </select>
              </label>
            </div>
            <label className="text-xs text-muted-foreground block">描述
              <input value={newAgent.description} onChange={e => setNewAgent({ ...newAgent, description: e.target.value })}
                className="mt-1 w-full px-2 py-1.5 rounded border border-border bg-background text-xs" />
            </label>
            <label className="text-xs text-muted-foreground block">启动命令（空格分隔，ACP协议入口）
              <input value={newAgent.cmd} onChange={e => setNewAgent({ ...newAgent, cmd: e.target.value })}
                className="mt-1 w-full px-2 py-1.5 rounded border border-border bg-background text-xs font-mono" placeholder="python3 my_agent_acp.py --stdio" />
            </label>
            <label className="text-xs text-muted-foreground block">工作目录（可选）
              <input value={newAgent.cwd} onChange={e => setNewAgent({ ...newAgent, cwd: e.target.value })}
                className="mt-1 w-full px-2 py-1.5 rounded border border-border bg-background text-xs font-mono" placeholder="/home/climbing" />
            </label>
            <div className="flex justify-end gap-2 pt-1">
              <button onClick={() => setShowAddAgent(false)} className="px-3 py-1.5 rounded-lg border text-xs">取消</button>
              <button onClick={submitCustomAgent} className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs">保存并注册</button>
            </div>
          </div>
        </div>
      )}
    </PageLayout>
  );
}
