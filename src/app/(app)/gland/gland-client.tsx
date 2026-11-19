"use client"

import { useEffect, useState, useCallback } from "react"
import { getApiBaseUrl } from "@/lib/api-client"
import { useTranslation } from "react-i18next"
import { cn } from "@/lib/utils"
import { useIsMobile } from "@/hooks/use-mobile"
import {
  Zap, Plus, Trash2, RefreshCw, TestTube2, Key, Activity,
  Settings, CheckCircle, XCircle, Loader2, Eye, EyeOff,
  Server, Cpu, DollarSign, BarChart3,
} from "lucide-react"
import { PageLayout } from '@/components/page-layout';

interface Provider {
  name: string
  base_url: string
  models: Record<string, string>
  enabled: boolean
  priority: number
}

interface GlandHealth {
  status: string
  component: string
  providers: { total: number; enabled: number; unhealthy: number }
  keys: { total: number }
  token_meter: {
    total_tokens: number
    budget_limit: number | null
    remaining_budget: number | null
    call_count: number
    by_model: Record<string, number>
    by_user: Record<string, number>
    by_provider: Record<string, number>
  }
}

interface KeyInfo {
  provider: string
  keys: Array<{ masked: string; added_at: string }>
}

interface RecentRecord {
  provider: string
  model: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  user_id: string | null
  timestamp: number
}

type ActiveTab = "overview" | "providers" | "keys" | "usage"

export function GlandClient() {
  const { t } = useTranslation()
  const apiBase = getApiBaseUrl()
  const isMobile = useIsMobile()
  const [activeTab, setActiveTab] = useState<ActiveTab>("overview")
  const [health, setHealth] = useState<GlandHealth | null>(null)
  const [providers, setProviders] = useState<Provider[]>([])
  const [keys, setKeys] = useState<KeyInfo[]>([])
  const [recentRecords, setRecentRecords] = useState<RecentRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [testing, setTesting] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<{ name: string; ok: boolean; msg: string } | null>(null)

  // Add provider form
  const [showAddProvider, setShowAddProvider] = useState(false)
  const [newProvider, setNewProvider] = useState({ name: "", base_url: "", api_key: "", chat_model: "", embed_model: "", priority: 0 })

  // Add key form
  const [showAddKey, setShowAddKey] = useState(false)
  const [newKey, setNewKey] = useState({ provider: "", api_key: "" })

  // Budget
  const [budgetInput, setBudgetInput] = useState("")

  const refresh = useCallback(async () => {
    try {
      const [h, p, k, r] = await Promise.all([
        fetch(`${apiBase}/api/gland/health`).then(r => r.json()),
        fetch(`${apiBase}/api/gland/providers`).then(r => r.json()),
        fetch(`${apiBase}/api/gland/keys`).then(r => r.json()).catch(() => []),
        fetch(`${apiBase}/api/gland/usage/recent?limit=20`).then(r => r.json()).catch(() => ({ records: [] })),
      ])
      setHealth(h)
      setProviders(p.providers || [])
      setKeys(Array.isArray(k) ? k : [])
      setRecentRecords(r.records || [])
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [apiBase])

  useEffect(() => { refresh() }, [refresh])

  const handleTest = async (providerName?: string) => {
    setTesting(providerName || "default")
    setTestResult(null)
    try {
      const res = await fetch(`${apiBase}/api/gland/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(providerName ? { provider: providerName } : {}),
      })
      const data = await res.json()
      if (res.ok) {
        setTestResult({ name: providerName || "auto", ok: true, msg: `Model: ${data.model || "ok"}, latency: ${data.latency_ms || "?"}ms` })
      } else {
        setTestResult({ name: providerName || "auto", ok: false, msg: data.detail || "Connection failed" })
      }
    } catch (e: any) {
      setTestResult({ name: providerName || "auto", ok: false, msg: e.message || "Network error" })
    }
    setTesting(null)
  }

  const handleAddProvider = async () => {
    const models: Record<string, string> = {}
    if (newProvider.chat_model) models.chat = newProvider.chat_model
    if (newProvider.embed_model) models.embedding = newProvider.embed_model
    await fetch(`${apiBase}/api/gland/providers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: newProvider.name,
        base_url: newProvider.base_url,
        models: Object.keys(models).length > 0 ? models : undefined,
        api_key: newProvider.api_key || undefined,
        priority: newProvider.priority,
      }),
    })
    setShowAddProvider(false)
    setNewProvider({ name: "", base_url: "", api_key: "", chat_model: "", embed_model: "", priority: 0 })
    refresh()
  }

  const handleDeleteProvider = async (name: string) => {
    if (!confirm(t("gland.confirmDeleteProvider", { name }))) return
    await fetch(`${apiBase}/api/gland/providers/${name}`, { method: "DELETE" })
    refresh()
  }

  const handleAddKey = async () => {
    await fetch(`${apiBase}/api/gland/keys`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newKey),
    })
    setShowAddKey(false)
    setNewKey({ provider: "", api_key: "" })
    refresh()
  }

  const handleSetBudget = async () => {
    const limit = parseInt(budgetInput) || 0
    await fetch(`${apiBase}/api/gland/budget`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit }),
    })
    setBudgetInput("")
    refresh()
  }

  const formatNumber = (n: number) => n.toLocaleString()

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const tabs: Array<{ key: ActiveTab; label: string; icon: typeof Zap }> = [
    { key: "overview", label: t("gland.tabs.overview") || "Overview", icon: Activity },
    { key: "providers", label: "服务商", icon: Server },
    { key: "keys", label: "API Keys", icon: Key },
    { key: "usage", label: t("gland.tabs.usage") || "Usage Stats", icon: BarChart3 },
  ]

  return (
      <PageLayout title="腺体">
        
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500/20 to-purple-500/20">
            <Zap className="w-5 h-5 text-violet-400" />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg lg:text-xl font-semibold truncate">Gland — {t("gland.title")}</h1>
            <p className="text-xs lg:text-sm text-muted-foreground truncate">{t('organTag.gland')}</p>
          </div>
        </div>
      </div>
      <div className="shrink-0">

        {/* Tab Navigation */}
        <div className="flex gap-1 sm:gap-2 border-b border-border px-3 lg:px-6 py-2 overflow-x-auto scrollbar-none">
          {tabs.map(tab => {
            const Icon = tab.icon
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={cn(
                  "flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm rounded-t-lg transition-colors",
                  activeTab === tab.key
                    ? "bg-card border border-b-0 border-border text-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                )}
              >
                <Icon size={14} />
                {tab.label}
              </button>
            )
          })}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4 lg:py-6">

        {/* ── Overview Tab ── */}
        {activeTab === "overview" && (
          <div className="space-y-3 lg:space-y-6">
            {/* Stats Cards */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 lg:gap-4">
              {[
                { label: "服务商", value: health?.providers.total ?? 0, icon: Server, color: "text-blue-400", bg: "from-blue-500/20 to-blue-600/10" },
                { label: t("gland.enabled") || "Enabled", value: health?.providers.enabled ?? 0, icon: CheckCircle, color: "text-green-400", bg: "from-green-500/20 to-green-600/10" },
                { label: "API Keys", value: health?.keys.total ?? 0, icon: Key, color: "text-purple-400", bg: "from-purple-500/20 to-purple-600/10" },
                { label: t("gland.totalCalls") || "Total Calls", value: health?.token_meter.call_count ?? 0, icon: Activity, color: "text-amber-400", bg: "from-amber-500/20 to-amber-600/10" },
              ].map(s => (
                <div key={s.label} className={cn("rounded-xl border border-border bg-gradient-to-br p-3 lg:p-4", s.bg)}>
                  <div className="flex items-center justify-between mb-2">
                    <s.icon className={cn("w-4 h-4", s.color)} />
                  </div>
                  <div className={cn("text-xl lg:text-2xl font-bold", s.color)}>{formatNumber(s.value as number)}</div>
                  <div className="text-xs text-muted-foreground mt-1">{s.label}</div>
                </div>
              ))}
            </div>

            {/* Provider Health */}
            <div>
              <h3 className="text-xs lg:text-sm font-semibold text-foreground mb-3">{t("gland.providerStatus") || "Provider Status"}</h3>
              <div className="space-y-2">
                {providers.map(p => (
                  <div key={p.name} className="flex items-center gap-2 lg:gap-3 p-3 lg:p-4 rounded-xl border border-border bg-card">
                    <div className={cn("w-2.5 h-2.5 rounded-full", p.enabled ? "bg-green-500" : "bg-red-400")} />
                    <div className="flex-1 min-w-0">
                      <div className="text-xs lg:text-sm font-medium text-foreground">{p.name}</div>
                      <div className="text-xs text-muted-foreground truncate">{p.base_url}</div>
                    </div>
                    <div className="flex gap-1 flex-wrap">
                      {Object.entries(p.models).map(([type, model]) => (
                        <span key={type} className="px-2 py-0.5 rounded text-[11px] bg-muted text-muted-foreground">
                          {type}: {model}
                        </span>
                      ))}
                    </div>
                    <button
                      onClick={() => handleTest(p.name)}
                      disabled={testing === p.name}
                      className="p-1.5 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
                      title={t("gland.testConnection")}
                    >
                      {testing === p.name ? <Loader2 className="w-4 h-4 animate-spin" /> : <TestTube2 className="w-4 h-4" />}
                    </button>
                  </div>
                ))}
              </div>
            </div>

            {/* Test Result */}
            {testResult && (
              <div className={cn(
                "flex items-center gap-2 p-3 rounded-lg border text-xs lg:text-sm",
                testResult.ok
                  ? "border-green-500/30 bg-green-500/10 text-green-400"
                  : "border-red-500/30 bg-red-500/10 text-red-400"
              )}>
                {testResult.ok ? <CheckCircle className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
                <span className="font-medium">{testResult.name}:</span> {testResult.msg}
              </div>
            )}

            {/* Budget */}
            <div className="p-3 lg:p-4 rounded-xl border border-border bg-card">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-xs lg:text-sm font-semibold text-foreground flex items-center gap-2">
                  <DollarSign className="w-4 h-4 text-amber-400" /> {t("gland.tokenBudget") || "Token Budget"}
                </h3>
                <span className="text-xs text-muted-foreground">
                  {health?.token_meter.budget_limit ? t("gland.budgetLimit", { limit: formatNumber(health.token_meter.budget_limit) }) : t("gland.unlimited")}
                </span>
              </div>
              <div className="flex gap-2">
                <input
                  type="number"
                  value={budgetInput}
                  onChange={e => setBudgetInput(e.target.value)}
                  placeholder={t("gland.budgetPlaceholder")}
                  className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm"
                />
                <button onClick={handleSetBudget} className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-xs lg:text-sm font-medium hover:opacity-90">
                  {t("common.save")}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── Providers Tab ── */}
        {activeTab === "providers" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xs lg:text-sm font-semibold text-foreground">{t("gland.providerManagement")}</h3>
              <button
                onClick={() => setShowAddProvider(!showAddProvider)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs lg:text-sm hover:opacity-90"
              >
                <Plus className="w-3.5 h-3.5" /> {t("gland.addProvider")}
              </button>
            </div>

            {/* Add Provider Form */}
            {showAddProvider && (
              <div className="p-3 lg:p-4 rounded-xl border border-border bg-card space-y-3">
                <div className="grid grid-cols-2 gap-2 lg:gap-3">
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">{t("gland.form.nameLabel") || "Name"} *</label>
                    <input value={newProvider.name} onChange={e => setNewProvider({ ...newProvider, name: e.target.value })}
                      placeholder="openai" className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">基础 URL *</label>
                    <input value={newProvider.base_url} onChange={e => setNewProvider({ ...newProvider, base_url: e.target.value })}
                      placeholder="https://api.openai.com/v1" className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">{t("gland.form.chatModel") || "Chat Model"}</label>
                    <input value={newProvider.chat_model} onChange={e => setNewProvider({ ...newProvider, chat_model: e.target.value })}
                      placeholder="gpt-4o" className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">{t("gland.form.embedModel") || "Embedding Model"}</label>
                    <input value={newProvider.embed_model} onChange={e => setNewProvider({ ...newProvider, embed_model: e.target.value })}
                      placeholder="text-embedding-3-small" className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">API 密钥</label>
                    <input value={newProvider.api_key} onChange={e => setNewProvider({ ...newProvider, api_key: e.target.value })}
                      type="password" placeholder="sk-..." className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">{t("gland.form.priority") || "Priority (lower = higher priority)"}</label>
                    <input value={newProvider.priority} onChange={e => setNewProvider({ ...newProvider, priority: parseInt(e.target.value) || 0 })}
                      type="number" className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  </div>
                </div>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setShowAddProvider(false)} className="px-3 py-1.5 rounded-lg border border-border text-xs lg:text-sm text-muted-foreground hover:text-foreground">{t("common.cancel") || "Cancel"}</button>
                  <button onClick={handleAddProvider} disabled={!newProvider.name || !newProvider.base_url}
                    className="px-4 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs lg:text-sm font-medium hover:opacity-90 disabled:opacity-40">{t("gland.form.add") || "Add"}</button>
                </div>
              </div>
            )}

            {/* Provider List */}
            <div className="space-y-2">
              {providers.map(p => (
                <div key={p.name} className="p-3 lg:p-4 rounded-xl border border-border bg-card">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2 lg:gap-3">
                      <div className={cn("w-3 h-3 rounded-full", p.enabled ? "bg-green-500" : "bg-red-400")} />
                      <div>
                        <div className="text-xs lg:text-sm font-semibold text-foreground">{p.name}</div>
                        <div className="text-xs text-muted-foreground">{p.base_url}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">{t("gland.form.priorityLabel") || "Priority"}: {p.priority}</span>
                      <button onClick={() => handleTest(p.name)} disabled={testing === p.name}
                        className="p-1.5 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground" title={t("gland.testConnection") || "Test Connection"}>
                        {testing === p.name ? <Loader2 className="w-4 h-4 animate-spin" /> : <TestTube2 className="w-4 h-4" />}
                      </button>
                      <button onClick={() => handleDeleteProvider(p.name)}
                        className="p-1.5 rounded-md hover:bg-red-500/10 text-muted-foreground hover:text-red-400" title={t("gland.form.delete") || "Delete"}>
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                  <div className="flex gap-2 mt-3 flex-wrap">
                    {Object.entries(p.models).map(([type, model]) => (
                      <span key={type} className="px-2 py-1 rounded-md text-xs bg-muted text-muted-foreground">
                        <span className="text-foreground/60">{type}:</span> {model}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
              {providers.length === 0 && (
                <div className="text-center py-8 text-muted-foreground text-xs lg:text-sm">{t("gland.form.noProviders") || "No Providers"}</div>
              )}
            </div>
          </div>
        )}

        {/* ── Keys Tab ── */}
        {activeTab === "keys" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xs lg:text-sm font-semibold text-foreground">{t("gland.form.apiKeyManagement") || "API Key Management"}</h3>
              <button
                onClick={() => setShowAddKey(!showAddKey)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs lg:text-sm hover:opacity-90"
              >
                <Plus className="w-3.5 h-3.5" /> {t("gland.form.addKey") || "Add Key"}
              </button>
            </div>

            {/* Add Key Form */}
            {showAddKey && (
              <div className="p-3 lg:p-4 rounded-xl border border-border bg-card space-y-3">
                <div className="grid grid-cols-2 gap-2 lg:gap-3">
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">服务商 *</label>
                    <select value={newKey.provider} onChange={e => setNewKey({ ...newKey, provider: e.target.value })}
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm">
                      <option value="">{t("gland.form.selectProvider") || "Select Provider"}</option>
                      {providers.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">API 密钥 *</label>
                    <input value={newKey.api_key} onChange={e => setNewKey({ ...newKey, api_key: e.target.value })}
                      type="password" placeholder="sk-..." className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  </div>
                </div>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setShowAddKey(false)} className="px-3 py-1.5 rounded-lg border border-border text-xs lg:text-sm text-muted-foreground hover:text-foreground">{t("common.cancel") || "Cancel"}</button>
                  <button onClick={handleAddKey} disabled={!newKey.provider || !newKey.api_key}
                    className="px-4 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs lg:text-sm font-medium hover:opacity-90 disabled:opacity-40">{t("gland.form.add") || "Add"}</button>
                </div>
              </div>
            )}

            {/* Keys List */}
            <div className="space-y-2">
              {keys.length > 0 ? keys.map((k, i) => (
                <div key={i} className="flex items-center gap-2 lg:gap-3 p-3 lg:p-4 rounded-xl border border-border bg-card">
                  <Key className="w-4 h-4 text-purple-400" />
                  <div className="flex-1">
                    <div className="text-xs lg:text-sm font-medium text-foreground">{k.provider}</div>
                    <div className="text-xs text-muted-foreground font-mono">{k.keys.map(j => j.masked).join(", ")}</div>
                  </div>
                  <span className="text-xs text-muted-foreground">{k.keys.length} key(s)</span>
                </div>
              )) : (
                <div className="text-center py-8 text-muted-foreground text-xs lg:text-sm">{t("gland.form.noKeys") || "No API keys configured"}</div>
              )}
            </div>
          </div>
        )}

        {/* ── Usage Tab ── */}
        {activeTab === "usage" && (
          <div className="space-y-3 lg:space-y-6">
            {/* Usage by Model */}
            <div>
              <h3 className="text-xs lg:text-sm font-semibold text-foreground mb-3">{t("gland.form.byModel") || "By Model"}</h3>
              {health?.token_meter.by_model && Object.keys(health.token_meter.by_model).length > 0 ? (
                <div className="space-y-2">
                  {Object.entries(health.token_meter.by_model)
                    .sort(([, a], [, b]) => b - a)
                    .map(([model, tokens]) => {
                      const maxTokens = Math.max(...Object.values(health.token_meter.by_model))
                      const pct = maxTokens > 0 ? (tokens / maxTokens) * 100 : 0
                      return (
                        <div key={model} className="p-3 lg:p-4 rounded-xl border border-border bg-card">
                          <div className="flex justify-between items-center mb-1.5">
                            <span className="text-xs lg:text-sm text-foreground font-medium">{model}</span>
                            <span className="text-xs text-muted-foreground">{formatNumber(tokens)} tokens</span>
                          </div>
                          <div className="w-full h-1.5 rounded-full bg-muted overflow-hidden">
                            <div className="h-full rounded-full bg-gradient-to-r from-violet-500 to-purple-400 transition-all" style={{ width: `${pct}%` }} />
                          </div>
                        </div>
                      )
                    })}
                </div>
              ) : (
                <div className="text-center py-3 lg:py-6 text-muted-foreground text-xs lg:text-sm">{t("gland.form.noUsageRecords") || "No Usage Records"}</div>
              )}
            </div>

            {/* Usage by 服务商 */}
            {health?.token_meter.by_provider && Object.keys(health.token_meter.by_provider).length > 0 && (
              <div>
                <h3 className="text-xs lg:text-sm font-semibold text-foreground mb-3">{t("gland.form.byProvider") || "By Provider"}</h3>
                <div className="grid grid-cols-2 lg:grid-cols-3 gap-2 lg:gap-3">
                  {Object.entries(health.token_meter.by_provider).map(([provider, tokens]) => (
                    <div key={provider} className="p-3 lg:p-4 rounded-xl border border-border bg-card">
                      <div className="text-xs lg:text-sm font-medium text-foreground">{provider}</div>
                      <div className="text-lg font-bold text-violet-400">{formatNumber(tokens)}</div>
                      <div className="text-xs text-muted-foreground">tokens</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Recent Records */}
            <div>
              <h3 className="text-xs lg:text-sm font-semibold text-foreground mb-3">{t("gland.form.recentCalls") || "Recent Calls"}</h3>
              {recentRecords.length > 0 ? (
                isMobile ? (
                  <div className="space-y-2">
                    {recentRecords.map((r, i) => (
                      <div key={i} className="rounded-lg border border-border p-3 hover:bg-muted/30 transition-colors">
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-xs font-medium text-foreground">{r.provider}</span>
                          <span className="text-[10px] text-muted-foreground">{new Date(r.timestamp * 1000).toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}</span>
                        </div>
                        <p className="text-[10px] text-muted-foreground mb-1.5 truncate">{r.model}</p>
                        <div className="flex items-center gap-3 text-[10px]">
                          <span className="text-muted-foreground">IN: {formatNumber(r.prompt_tokens)}</span>
                          <span className="text-muted-foreground">OUT: {formatNumber(r.completion_tokens)}</span>
                          <span className="font-medium text-foreground ml-auto">Σ {formatNumber(r.total_tokens)}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                <div className="rounded-xl border border-border overflow-x-auto">
                  <table className="w-full text-xs lg:text-sm min-w-[500px]">
                    <thead>
                      <tr className="bg-muted/50">
                        <th className="text-left px-3 py-2 text-xs font-medium text-muted-foreground">服务商</th>
                        <th className="text-left px-3 py-2 text-xs font-medium text-muted-foreground">模型</th>
                        <th className="text-right px-3 py-2 text-xs font-medium text-muted-foreground">提示</th>
                        <th className="text-right px-3 py-2 text-xs font-medium text-muted-foreground">补全</th>
                        <th className="text-right px-3 py-2 text-xs font-medium text-muted-foreground">合计</th>
                        <th className="text-right px-3 py-2 text-xs font-medium text-muted-foreground">{t("gland.form.time") || "Time"}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentRecords.map((r, i) => (
                        <tr key={i} className="border-t border-border hover:bg-muted/30">
                          <td className="px-3 py-2 text-foreground">{r.provider}</td>
                          <td className="px-3 py-2 text-muted-foreground">{r.model}</td>
                          <td className="px-3 py-2 text-right text-muted-foreground">{formatNumber(r.prompt_tokens)}</td>
                          <td className="px-3 py-2 text-right text-muted-foreground">{formatNumber(r.completion_tokens)}</td>
                          <td className="px-3 py-2 text-right font-medium text-foreground">{formatNumber(r.total_tokens)}</td>
                          <td className="px-3 py-2 text-right text-xs text-muted-foreground">
                            {new Date(r.timestamp * 1000).toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                )
              ) : (
                <div className="text-center py-3 lg:py-6 text-muted-foreground text-xs lg:text-sm">{t("gland.form.noRecentRecords") || "No Recent Records"}</div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    )
}
