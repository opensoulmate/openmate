"use client"

import { useEffect, useState, useCallback } from "react"
import { useTranslation } from "react-i18next"
import { getApiBaseUrl } from "@/lib/api-client"
import {
  Shield, Loader2, AlertTriangle, CheckCircle, XCircle,
  Eye, Ban, Unlock, Clock, Activity, FileText, Search,
  Plus, Trash2, RefreshCw, Settings, Globe, Lock,
  Zap, Crosshair,
} from "lucide-react"
import { PageLayout } from '@/components/page-layout';

interface ModuleStats {
  [k: string]: unknown
}

interface ImmuneHealth {
  status: string
  modules: Record<string, ModuleStats>
}

interface ModerateResult {
  is_safe: boolean
  risk_level: string
  findings: Array<{ type: string; label: string; risk: string }>
  redacted_text: string
  original_length: number
}

interface AuditEntry {
  timestamp: number
  action: string
  client_ip: string
  endpoint: string
  detail: string
  risk_level: string
}

interface IPEntry {
  ip: string
  reason: string
  added_at: number
  ttl_seconds: number | null
}

type ActiveTab = "overview" | "moderate" | "ratelimit" | "ip" | "audit" | "intrusion"

export function ImmuneClient() {
  const { t } = useTranslation()
  const apiBase = getApiBaseUrl()

  const [health, setHealth] = useState<ImmuneHealth | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<ActiveTab>("overview")

  // Content moderation state
  const [moderateText, setModerateText] = useState("")
  const [moderateResult, setModerateResult] = useState<ModerateResult | null>(null)
  const [moderateLoading, setModerateLoading] = useState(false)

  // Rate limit state
  const [rateLimitStats, setRateLimitStats] = useState<Record<string, unknown> | null>(null)
  const [rpm, setRpm] = useState(60)
  const [rph, setRph] = useState(1000)
  const [burst, setBurst] = useState(20)

  // IP management state
  const [ipLists, setIpLists] = useState<{ blacklist: IPEntry[]; whitelist: IPEntry[] }>({ blacklist: [], whitelist: [] })
  const [newIp, setNewIp] = useState("")
  const [newIpReason, setNewIpReason] = useState("")

  // Audit log state
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([])
  const [auditFilter, setAuditFilter] = useState("")
  const [auditStats, setAuditStats] = useState<Record<string, unknown> | null>(null)

  // Intrusion detection state
  const [intrusionStats, setIntrusionStats] = useState<Record<string, unknown> | null>(null)
  const [threats, setThreats] = useState<Array<Record<string, unknown>>>([])
  const [blockedIps, setBlockedIps] = useState<Array<Record<string, unknown>>>([])
  const [inspectIp, setInspectIp] = useState("")
  const [inspectPath, setInspectPath] = useState("/")
  const [inspectBody, setInspectBody] = useState("")
  const [inspectResult, setInspectResult] = useState<Record<string, unknown> | null>(null)

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/immune/health`)
      setHealth(await res.json())
    } catch {}
  }, [apiBase])

  const fetchRateLimitStats = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/immune/rate-limit/stats`)
      setRateLimitStats(await res.json())
    } catch {}
  }, [apiBase])

  const fetchIpLists = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/immune/ip/lists`)
      setIpLists(await res.json())
    } catch {}
  }, [apiBase])

  const fetchAuditLog = useCallback(async () => {
    try {
      const params = new URLSearchParams({ limit: "200" })
      if (auditFilter) params.set("risk_level", auditFilter)
      const res = await fetch(`${apiBase}/api/immune/audit/log?${params}`)
      const data = await res.json()
      setAuditEntries(data.entries || [])
    } catch {}
  }, [apiBase, auditFilter])

  const fetchAuditStats = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/immune/audit/stats`)
      setAuditStats(await res.json())
    } catch {}
  }, [apiBase])

  const fetchIntrusionStats = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/immune/intrusion/stats`)
      setIntrusionStats(await res.json())
    } catch {}
  }, [apiBase])

  const fetchThreats = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/immune/intrusion/threats?limit=100`)
      const data = await res.json()
      setThreats(data.threats || [])
    } catch {}
  }, [apiBase])

  const fetchBlockedIps = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/immune/intrusion/blocked`)
      const data = await res.json()
      setBlockedIps(data.blocked_ips || [])
    } catch {}
  }, [apiBase])

  const handleInspect = useCallback(async () => {
    if (!inspectIp.trim()) return
    try {
      const res = await fetch(`${apiBase}/api/immune/intrusion/inspect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip: inspectIp, path: inspectPath, body: inspectBody, method: "GET" }),
      })
      setInspectResult(await res.json())
    } catch {}
  }, [inspectIp, inspectPath, inspectBody, apiBase])

  useEffect(() => {
    Promise.all([fetchHealth(), fetchRateLimitStats(), fetchIpLists(), fetchAuditLog(), fetchAuditStats()])
      .finally(() => setLoading(false))
  }, [fetchHealth, fetchRateLimitStats, fetchIpLists, fetchAuditLog, fetchAuditStats])

  const handleModerate = useCallback(async () => {
    if (!moderateText.trim()) return
    setModerateLoading(true)
    setModerateResult(null)
    try {
      const res = await fetch(`${apiBase}/api/immune/moderate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: moderateText }),
      })
      setModerateResult(await res.json())
    } catch (e: any) {
      setModerateResult(null)
    } finally {
      setModerateLoading(false)
    }
  }, [moderateText, apiBase])

  const handleUpdateRateLimit = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        requests_per_minute: String(rpm),
        requests_per_hour: String(rph),
        burst_size: String(burst),
      })
      await fetch(`${apiBase}/api/immune/rate-limit/config?${params}`, { method: "PUT" })
      await fetchRateLimitStats()
    } catch {}
  }, [rpm, rph, burst, apiBase, fetchRateLimitStats])

  const handleAddIp = useCallback(async (list: "blacklist" | "whitelist") => {
    if (!newIp.trim()) return
    try {
      await fetch(`${apiBase}/api/immune/ip/${list}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip: newIp, reason: newIpReason }),
      })
      setNewIp("")
      setNewIpReason("")
      await fetchIpLists()
    } catch {}
  }, [newIp, newIpReason, apiBase, fetchIpLists])

  const handleRemoveIp = useCallback(async (ip: string, list: "blacklist" | "whitelist") => {
    try {
      await fetch(`${apiBase}/api/immune/ip/${list}/${ip}`, { method: "DELETE" })
      await fetchIpLists()
    } catch {}
  }, [apiBase, fetchIpLists])

  const formatTimestamp = (ts: number) => {
    return new Date(ts * 1000).toLocaleString('zh-CN', { fractionalSecondDigits: 3 })
  }

  const getRiskColor = (level: string) => {
    switch (level) {
      case "high": return "text-red-400 bg-red-500/10 border-red-500/30"
      case "medium": return "text-yellow-400 bg-yellow-500/10 border-yellow-500/30"
      case "low": return "text-blue-400 bg-blue-500/10 border-blue-500/30"
      default: return "text-muted-foreground bg-muted/30 border-border"
    }
  }

  const getActionIcon = (action: string) => {
    if (action.includes("BLOCK") || action.includes("BLACKLIST")) return <Ban className="w-3.5 h-3.5 text-red-400" />
    if (action.includes("LIMIT")) return <Clock className="w-3.5 h-3.5 text-yellow-400" />
    if (action.includes("CONFIG")) return <Settings className="w-3.5 h-3.5 text-blue-400" />
    return <Activity className="w-3.5 h-3.5 text-muted-foreground" />
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const modules = health?.modules || {}
  const rlStats = modules.rate_limiter as Record<string, unknown> || {}
  const acStats = modules.access_control as Record<string, unknown> || {}
  const auditModStats = modules.audit as Record<string, unknown> || {}
  const modStats = modules.moderator as Record<string, unknown> || {}

  const tabs: Array<{ key: ActiveTab; label: string; icon: typeof Shield }> = [
    { key: "overview", label: t('immune.overview1'), icon: Activity },
    { key: "moderate", label: t('immune.contentModeration'), icon: Eye },
    { key: "ratelimit", label: t('immune.rateLimit1'), icon: Clock },
    { key: "ip", label: t('immune.t21173'), icon: Globe },
    { key: "intrusion", label: "Intrusion Detection", icon: Crosshair },
    { key: "audit", label: t('immune.auditLog1'), icon: FileText },
  ]

  return (
      <PageLayout title="免疫">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-red-500/20 to-pink-500/20">
            <Shield className="w-6 h-6 text-red-400" />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg lg:text-xl font-semibold truncate">{t('immune.t05460')}</h1>
            <p className="text-xs lg:text-sm text-muted-foreground truncate">{t('organTag.immune')}</p>
          </div>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4 lg:py-6 space-y-3 lg:space-y-6">

      {/* Tab Navigation */}
      <div className="flex gap-1 sm:gap-2 border-b border-border pb-2 overflow-x-auto scrollbar-none">
        {tabs.map(tab => {
          const Icon = tab.icon
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-1.5 sm:gap-2 px-2.5 sm:px-2 lg:px-4 py-2 rounded-t-lg text-xs sm:text-xs lg:text-sm font-medium transition-all whitespace-nowrap ${
                activeTab === tab.key
                  ? "bg-card border border-b-0 border-border text-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
              }`}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
            </button>
          )
        })}
      </div>

      {/* Overview Tab */}
      {activeTab === "overview" && (
        <div className="space-y-3 lg:space-y-6">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 lg:gap-4">
            {[
              { label: t('immune.auditRules'), value: String(modStats.patterns ?? 0), icon: Eye },
              { label: t('immune.t24575'), value: String((acStats.blacklist_count as number) ?? 0), icon: Ban },
              { label: t('immune.t96396'), value: String((acStats.whitelist_count as number) ?? 0), icon: Unlock },
              { label: t('immune.entries4'), value: String((auditModStats.total_entries as number) ?? 0), icon: FileText },
            ].map(item => {
              const Icon = item.icon
              return (
                <div key={item.label} className="bg-card border border-border rounded-xl p-3 lg:p-4 lg:p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <Icon className="w-4 h-4 text-muted-foreground" />
                    <span className="text-xs text-muted-foreground">{item.label}</span>
                  </div>
                  <div className="text-xl lg:text-2xl font-bold">{item.value}</div>
                </div>
              )
            })}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-6">
            {/* Rate Limiter Config */}
            <div className="bg-card border border-border rounded-xl p-3 lg:p-4 space-y-4">
              <h3 className="font-semibold flex items-center gap-2">
                <Clock className="w-4 h-4" /> {t('immune.rateLimitConfig')}
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">{t('immune.t70683')}</label>
                  <input
                    type="number"
                    value={rpm}
                    onChange={(e) => setRpm(Number(e.target.value))}
                    className="w-full bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm mt-1"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">{t('immune.t02202')}</label>
                  <input
                    type="number"
                    value={rph}
                    onChange={(e) => setRph(Number(e.target.value))}
                    className="w-full bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm mt-1"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">{t('immune.t22418')}</label>
                  <input
                    type="number"
                    value={burst}
                    onChange={(e) => setBurst(Number(e.target.value))}
                    className="w-full bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm mt-1"
                  />
                </div>
              </div>
              <button
                onClick={handleUpdateRateLimit}
                className="px-2 lg:px-4 py-2 bg-red-500/10 text-red-400 border border-red-500/30 rounded-lg text-xs lg:text-sm font-medium hover:bg-red-500/20 transition-colors"
              >
                {t('immune.t45063')}
              </button>
            </div>

            {/* Quick Stats */}
            <div className="bg-card border border-border rounded-xl p-3 lg:p-4 space-y-4">
              <h3 className="font-semibold flex items-center gap-2">
                <Activity className="w-4 h-4" /> {t('immune.rateLimitStats')}
              </h3>
              <div className="space-y-3">
                {rlStats && Object.entries(rlStats).filter(([k]) => !["config"].includes(k)).map(([k, v]) => (
                  <div key={k} className="flex justify-between items-center">
                    <span className="text-xs lg:text-sm text-muted-foreground capitalize">{k.replace(/_/g, " ")}</span>
                    <span className="text-xs lg:text-sm font-semibold">{typeof v === "object" ? JSON.stringify(v) : String(v)}</span>
                  </div>
                ))}
              </div>
              <button
                onClick={fetchRateLimitStats}
                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                <RefreshCw className="w-3 h-3" /> {t('common.refresh')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Content Moderation Tab */}
      {activeTab === "moderate" && (
        <div className="space-y-3 lg:space-y-6">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-6">
            <div className="space-y-4">
              <h3 className="font-semibold">{t('immune.contentModerationtest')}</h3>
              <p className="text-xs text-muted-foreground">
                {t('immune.t85847')}
              </p>
              <textarea
                value={moderateText}
                onChange={(e) => setModerateText(e.target.value)}
                placeholder={t('immune.contentinput')}
                rows={8}
                className="w-full bg-card border border-border rounded-xl p-3 lg:p-4 lg:p-4 text-xs lg:text-sm resize-none focus:outline-none focus:ring-2 focus:ring-red-500/30"
              />
              <button
                onClick={handleModerate}
                disabled={!moderateText.trim() || moderateLoading}
                className="w-full py-2.5 bg-gradient-to-r from-red-500 to-pink-500 text-white rounded-lg font-medium text-xs lg:text-sm disabled:opacity-40 disabled:cursor-not-allowed hover:from-red-600 hover:to-pink-600 transition-all flex items-center justify-center gap-2"
              >
                {moderateLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Eye className="w-4 h-4" />}
                {moderateLoading ? t('immune.t46482') : t('immune.start4')}
              </button>
            </div>

            <div className="space-y-4">
              <h3 className="font-semibold">{t('immune.result4')}</h3>
              <div className="min-h-[300px] bg-card border border-border rounded-xl p-3 lg:p-4 lg:p-4">
                {moderateResult ? (
                  <div className="space-y-4">
                    <div className={`flex items-center gap-2 p-3 rounded-lg border ${
                      moderateResult.is_safe
                        ? "border-green-500/30 bg-green-500/10 text-green-400"
                        : "border-red-500/30 bg-red-500/10 text-red-400"
                    }`}>
                      {moderateResult.is_safe ? <CheckCircle className="w-5 h-5" /> : <XCircle className="w-5 h-5" />}
                      <span className="font-semibold">
                        {moderateResult.is_safe ? t('immune.t62071') : t('immune.t44208')}
                      </span>
                      <span className={`ml-auto text-xs px-2 py-0.5 rounded border ${getRiskColor(moderateResult.risk_level)}`}>
                        {moderateResult.risk_level}
                      </span>
                    </div>

                    {moderateResult.findings.length > 0 && (
                      <div className="space-y-2">
                        <h4 className="text-xs font-medium text-muted-foreground">{t('immune.t85574')}</h4>
                        {moderateResult.findings.map((f, i) => (
                          <div key={i} className="flex items-center gap-2 p-2 bg-muted/30 rounded-lg">
                            <AlertTriangle className="w-3.5 h-3.5 text-yellow-400" />
                            <span className="text-xs lg:text-sm">{f.label}</span>
                            <span className={`ml-auto text-xs px-2 py-0.5 rounded border ${getRiskColor(f.risk)}`}>
                              {f.risk}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}

                    {moderateResult.redacted_text && (
                      <div>
                        <h4 className="text-xs font-medium text-muted-foreground mb-2">{t('immune.t85574')}</h4>
                        <div className="bg-muted/30 rounded-lg p-3 text-xs lg:text-sm whitespace-pre-wrap">
                          {moderateResult.redacted_text}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center h-full text-muted-foreground/50">
                    <Eye className="w-12 h-12 mb-2" />
                    <p className="text-xs lg:text-sm">{t('immune.t86001')}</p>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Rate Limit Tab */}
      {activeTab === "ratelimit" && (
        <div className="space-y-3 lg:space-y-6">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-6">
            <div className="bg-card border border-border rounded-xl p-3 lg:p-4 space-y-4">
              <h3 className="font-semibold">{t('immune.rateLimitConfig1')}</h3>
              <div className="space-y-3">
                <div>
                  <label className="text-xs text-muted-foreground">{t('immune.t92355')}</label>
                  <input
                    type="number"
                    value={rpm}
                    onChange={(e) => setRpm(Number(e.target.value))}
                    className="w-full bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm mt-1"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">{t('immune.t47802')}</label>
                  <input
                    type="number"
                    value={rph}
                    onChange={(e) => setRph(Number(e.target.value))}
                    className="w-full bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm mt-1"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">{t('immune.t22418')}</label>
                  <input
                    type="number"
                    value={burst}
                    onChange={(e) => setBurst(Number(e.target.value))}
                    className="w-full bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm mt-1"
                  />
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={handleUpdateRateLimit}
                  className="flex-1 py-2 bg-red-500/10 text-red-400 border border-red-500/30 rounded-lg text-xs lg:text-sm font-medium hover:bg-red-500/20 transition-colors"
                >
                  {t('immune.t45063')}
                </button>
                <button
                  onClick={async () => {
                    await fetch(`${apiBase}/api/immune/rate-limit/reset`, { method: "POST" })
                    await fetchRateLimitStats()
                  }}
                  className="px-2 lg:px-4 py-2 bg-muted/30 border border-border rounded-lg text-xs lg:text-sm hover:bg-muted/50 transition-colors"
                >
                  {t('immune.t94139')}
                </button>
              </div>
            </div>

            <div className="bg-card border border-border rounded-xl p-3 lg:p-4 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold">{t('immune.t87501')}</h3>
                <button onClick={fetchRateLimitStats} className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1">
                  <RefreshCw className="w-3 h-3" /> {t('common.refresh')}
                </button>
              </div>
              {rateLimitStats ? (
                <div className="space-y-3">
                  {Object.entries(rateLimitStats).filter(([k]) => k !== "config").map(([k, v]) => (
                    <div key={k} className="flex justify-between items-center p-2 bg-muted/20 rounded-lg">
                      <span className="text-xs lg:text-sm text-muted-foreground capitalize">{k.replace(/_/g, " ")}</span>
                      <span className="text-xs lg:text-sm font-mono font-semibold">{typeof v === "object" ? JSON.stringify(v) : String(v)}</span>
                    </div>
                  ))}
                  {(() => {
                    const cfg = rateLimitStats.config as Record<string, unknown> | undefined
                    if (!cfg) return null
                    return (
                      <div className="pt-2 border-t border-border">
                        <div className="text-xs text-muted-foreground mb-2">{t('immune.t56016')}</div>
                        {Object.entries(cfg).map(([k, v]) => (
                          <div key={k} className="flex justify-between text-xs">
                            <span className="text-muted-foreground">{k}</span>
                            <span className="font-mono">{String(v)}</span>
                          </div>
                        ))}
                      </div>
                    )
                  })()}
                </div>
              ) : (
                <div className="text-center text-muted-foreground/50 py-8">{t('common.loading')}</div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* IP Control Tab */}
      {activeTab === "ip" && (
        <div className="space-y-3 lg:space-y-6">
          {/* Add IP */}
          <div className="bg-card border border-border rounded-xl p-3 lg:p-4">
            <h3 className="font-semibold mb-4">{t('immune.t80747')}</h3>
            <div className="flex flex-col sm:flex-row gap-3">
              <input
                type="text"
                value={newIp}
                onChange={(e) => setNewIp(e.target.value)}
                placeholder={t('immune.t10240')}
                className="flex-1 bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
              />
              <input
                type="text"
                value={newIpReason}
                onChange={(e) => setNewIpReason(e.target.value)}
                placeholder={t('immune.t60561')}
                className="w-48 bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
              />
              <button
                onClick={() => handleAddIp("blacklist")}
                className="px-2 lg:px-4 py-2 bg-red-500/10 text-red-400 border border-red-500/30 rounded-lg text-xs lg:text-sm font-medium hover:bg-red-500/20 transition-colors flex items-center gap-1"
              >
                <Ban className="w-3.5 h-3.5" /> {t('immune.t76125')}
              </button>
              <button
                onClick={() => handleAddIp("whitelist")}
                className="px-2 lg:px-4 py-2 bg-green-500/10 text-green-400 border border-green-500/30 rounded-lg text-xs lg:text-sm font-medium hover:bg-green-500/20 transition-colors flex items-center gap-1"
              >
                <Unlock className="w-3.5 h-3.5" /> {t('immune.t69667')}
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-6">
            {/* Blacklist */}
            <div className="bg-card border border-border rounded-xl p-3 lg:p-4">
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-semibold flex items-center gap-2">
                  <Ban className="w-4 h-4 text-red-400" /> {t('immune.t66502')}
                </h3>
                <button onClick={fetchIpLists} className="text-xs text-muted-foreground hover:text-foreground">
                  <RefreshCw className="w-3 h-3" />
                </button>
              </div>
              <div className="space-y-2 max-h-[400px] overflow-y-auto">
                {ipLists.blacklist.length === 0 ? (
                  <p className="text-xs lg:text-sm text-muted-foreground/50 text-center py-2 lg:py-4">{t('immune.t81144')}</p>
                ) : (
                  ipLists.blacklist.map((entry, i) => (
                    <div key={i} className="flex items-center gap-2 p-2 bg-red-500/5 border border-red-500/20 rounded-lg">
                      <code className="text-xs lg:text-sm font-mono">{entry.ip}</code>
                      {entry.reason && <span className="text-xs text-muted-foreground truncate flex-1">{entry.reason}</span>}
                      <button
                        onClick={() => handleRemoveIp(entry.ip, "blacklist")}
                        className="text-red-400 hover:text-red-300 p-1"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Whitelist */}
            <div className="bg-card border border-border rounded-xl p-3 lg:p-4">
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-semibold flex items-center gap-2">
                  <Unlock className="w-4 h-4 text-green-400" /> {t('immune.t04967')}
                </h3>
              </div>
              <div className="space-y-2 max-h-[400px] overflow-y-auto">
                {ipLists.whitelist.length === 0 ? (
                  <p className="text-xs lg:text-sm text-muted-foreground/50 text-center py-2 lg:py-4">{t('immune.t47217')}</p>
                ) : (
                  ipLists.whitelist.map((entry, i) => (
                    <div key={i} className="flex items-center gap-2 p-2 bg-green-500/5 border border-green-500/20 rounded-lg">
                      <code className="text-xs lg:text-sm font-mono">{entry.ip}</code>
                      {entry.reason && <span className="text-xs text-muted-foreground truncate flex-1">{entry.reason}</span>}
                      <button
                        onClick={() => handleRemoveIp(entry.ip, "whitelist")}
                        className="text-red-400 hover:text-red-300 p-1"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Intrusion Detection Tab */}
      {activeTab === "intrusion" && (
        <div className="space-y-3 lg:space-y-6">
          {/* Stats row */}
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-2 lg:gap-4">
            {[
              { label: "Inspected", value: String((intrusionStats?.total_inspected as number) ?? 0), icon: Eye },
              { label: "Threats", value: String((intrusionStats?.total_threats as number) ?? 0), icon: AlertTriangle },
              { label: "Auto-Blocked", value: String((intrusionStats?.total_blocked as number) ?? 0), icon: Ban },
              { label: "Active Blocks", value: String((intrusionStats?.active_blocks as number) ?? 0), icon: Lock },
              { label: "Suspicious IPs", value: String((intrusionStats?.suspicious_ips as number) ?? 0), icon: Globe },
            ].map(item => {
              const Icon = item.icon
              return (
                <div key={item.label} className="bg-card border border-border rounded-xl p-3 lg:p-4 lg:p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <Icon className="w-4 h-4 text-muted-foreground" />
                    <span className="text-xs text-muted-foreground">{item.label}</span>
                  </div>
                  <div className="text-xl lg:text-2xl font-bold">{item.value}</div>
                </div>
              )
            })}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-6">
            {/* Request Inspector */}
            <div className="bg-card border border-border rounded-xl p-3 lg:p-4 space-y-4">
              <h3 className="font-semibold flex items-center gap-2">
                <Crosshair className="w-4 h-4" /> Request Inspector
              </h3>
              <p className="text-xs text-muted-foreground">
                Test a request against the intrusion detection engine. Enter an IP, path, and optional body to scan.
              </p>
              <div className="space-y-3">
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={inspectIp}
                    onChange={(e) => setInspectIp(e.target.value)}
                    placeholder={t("immune.sourceIpPlaceholder")}
                    className="w-40 bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
                  />
                  <input
                    type="text"
                    value={inspectPath}
                    onChange={(e) => setInspectPath(e.target.value)}
                    placeholder={t("immune.attackPayloadPlaceholder")}
                    className="flex-1 bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm font-mono"
                  />
                </div>
                <textarea
                  value={inspectBody}
                  onChange={(e) => setInspectBody(e.target.value)}
                  placeholder={t("immune.requestBodyPlaceholder")}
                  rows={3}
                  className="w-full bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm font-mono resize-none"
                />
                <button
                  onClick={handleInspect}
                  disabled={!inspectIp.trim()}
                  className="w-full py-2.5 bg-gradient-to-r from-red-500 to-orange-500 text-white rounded-lg font-medium text-xs lg:text-sm disabled:opacity-40 disabled:cursor-not-allowed hover:from-red-600 hover:to-orange-600 transition-all flex items-center justify-center gap-2"
                >
                  <Crosshair className="w-4 h-4" />
                  Inspect Request
                </button>
              </div>

              {inspectResult && (
                <div className="space-y-2">
                  <div className={`flex items-center gap-2 p-3 rounded-lg border ${
                    (inspectResult.threats_found as number) === 0
                      ? "border-green-500/30 bg-green-500/10 text-green-400"
                      : "border-red-500/30 bg-red-500/10 text-red-400"
                  }`}>
                    {(inspectResult.threats_found as number) === 0
                      ? <CheckCircle className="w-5 h-5" />
                      : <XCircle className="w-5 h-5" />}
                    <span className="font-semibold">
                      {(inspectResult.threats_found as number) === 0 ? "安全——未检测到威胁" : `${inspectResult.threats_found} threat(s) detected`}
                    </span>
                  </div>
                  {(inspectResult.threats as Array<Record<string, unknown>>)?.map((threat, i) => (
                    <div key={i} className="p-3 bg-red-500/5 border border-red-500/20 rounded-lg space-y-1">
                      <div className="flex items-center gap-2">
                        <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
                        <span className="text-xs lg:text-sm font-medium">{String(threat.attack_type)}</span>
                        <span className={`ml-auto text-xs px-2 py-0.5 rounded border ${getRiskColor(String(threat.threat_level))}`}>
                          {String(threat.threat_level)}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground">{String(threat.detail)}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Auto-Blocked IPs */}
            <div className="bg-card border border-border rounded-xl p-3 lg:p-4">
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-semibold flex items-center gap-2">
                  <Ban className="w-4 h-4 text-red-400" /> {t('immune.blockedIps')}
                </h3>
                <button onClick={() => { fetchBlockedIps(); fetchIntrusionStats(); }} className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1">
                  <RefreshCw className="w-3 h-3" /> {t('common.refresh')}
                </button>
              </div>
              <div className="space-y-2 max-h-[400px] overflow-y-auto">
                {blockedIps.length === 0 ? (
                  <p className="text-xs lg:text-sm text-muted-foreground/50 text-center py-2 lg:py-4">{t('immune.noBlockedIps')}</p>
                ) : (
                  blockedIps.map((entry, i) => (
                    <div key={i} className="flex items-center gap-2 p-2 bg-red-500/5 border border-red-500/20 rounded-lg">
                      <Ban className="w-3.5 h-3.5 text-red-400" />
                      <code className="text-xs lg:text-sm font-mono">{String(entry.ip)}</code>
                      <span className="text-xs text-muted-foreground">{String(entry.threat_count)} {t('immune.threatCount')}</span>
                      <span className="text-xs text-muted-foreground">
                        {Array.isArray(entry.threat_types) ? (entry.threat_types as string[]).join(", ") : ""}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Recent Threats */}
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="flex items-center justify-between p-3 lg:p-4 border-b border-border">
              <h3 className="font-semibold flex items-center gap-2">
                <Zap className="w-4 h-4 text-yellow-400" /> {t('immune.threats')}
              </h3>
              <button onClick={fetchThreats} className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1">
                <RefreshCw className="w-3 h-3" /> {t('common.refresh')}
              </button>
            </div>
            <div className="max-h-[400px] overflow-y-auto overflow-x-auto">
              {threats.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground/50">
                  <Shield className="w-12 h-12 mb-2" />
                  <p className="text-xs lg:text-sm">{t('immune.noThreats')}</p>
                </div>
              ) : (
                <table className="w-full text-xs lg:text-sm min-w-[600px]">
                  <thead className="sticky top-0 bg-muted/50">
                    <tr className="text-xs text-muted-foreground">
                      <th className="text-left p-3 font-medium">{t('immune.time')}</th>
                      <th className="text-left p-3 font-medium">{t('immune.type')}</th>
                      <th className="text-left p-3 font-medium">IP</th>
                      <th className="text-left p-3 font-medium">{t('immune.path')}</th>
                      <th className="text-left p-3 font-medium">{t('immune.detail')}</th>
                      <th className="text-left p-3 font-medium">{t('immune.level')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {threats.map((threat, i) => (
                      <tr key={i} className="border-t border-border/50 hover:bg-muted/20 transition-colors">
                        <td className="p-3 text-xs text-muted-foreground font-mono whitespace-nowrap">
                          {formatTimestamp(threat.timestamp as number)}
                        </td>
                        <td className="p-3">
                          <span className="text-xs font-mono">{String(threat.attack_type)}</span>
                        </td>
                        <td className="p-3 font-mono text-xs">{String(threat.source_ip)}</td>
                        <td className="p-3 text-xs font-mono max-w-[200px] truncate">{String(threat.path)}</td>
                        <td className="p-3 text-xs text-muted-foreground max-w-[250px] truncate">{String(threat.detail)}</td>
                        <td className="p-3">
                          <span className={`text-xs px-2 py-0.5 rounded border ${getRiskColor(String(threat.threat_level))}`}>
                            {String(threat.threat_level)}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Audit Log Tab */}
      {activeTab === "audit" && (
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <select
              value={auditFilter}
              onChange={(e) => { setAuditFilter(e.target.value); }}
              className="bg-card border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
            >
              <option value="">{t('immune.all1')}</option>
              <option value="high">{t('immune.high6')}</option>
              <option value="medium">{t('immune.medium4')}</option>
              <option value="low">{t('immune.low4')}</option>
            </select>
            <button
              onClick={fetchAuditLog}
              className="flex items-center gap-1 px-3 py-2 bg-muted/30 border border-border rounded-lg text-xs lg:text-sm hover:bg-muted/50 transition-colors"
            >
              <RefreshCw className="w-3.5 h-3.5" /> {t('common.refresh')}
            </button>
            <span className="text-xs text-muted-foreground ml-auto">
              {t('immune.auditRecordCount', { count: auditEntries.length })}
            </span>
          </div>

          <div className="bg-card border border-border rounded-xl overflow-x-auto">
            <div className="max-h-[600px] overflow-y-auto">
              {auditEntries.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground/50">
                  <FileText className="w-12 h-12 mb-2" />
                  <p className="text-xs lg:text-sm">{t('immune.noAudit')}</p>
                </div>
              ) : (
                <table className="w-full text-xs lg:text-sm min-w-[550px]">
                  <thead className="sticky top-0 bg-muted/50">
                    <tr className="text-xs text-muted-foreground">
                      <th className="text-left p-3 font-medium">{t('immune.time3')}</th>
                      <th className="text-left p-3 font-medium">{t('immune.action2')}</th>
                      <th className="text-left p-3 font-medium">IP</th>
                      <th className="text-left p-3 font-medium">{t('immune.detail1')}</th>
                      <th className="text-left p-3 font-medium">{t('immune.t44032')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditEntries.map((entry, i) => (
                      <tr key={i} className="border-t border-border/50 hover:bg-muted/20 transition-colors">
                        <td className="p-3 text-xs text-muted-foreground font-mono whitespace-nowrap">
                          {formatTimestamp(entry.timestamp)}
                        </td>
                        <td className="p-3">
                          <div className="flex items-center gap-1.5">
                            {getActionIcon(entry.action)}
                            <span className="text-xs">{entry.action}</span>
                          </div>
                        </td>
                        <td className="p-3 font-mono text-xs">{entry.client_ip || "—"}</td>
                        <td className="p-3 text-xs text-muted-foreground max-w-[300px] truncate">{entry.detail}</td>
                        <td className="p-3">
                          <span className={`text-xs px-2 py-0.5 rounded border ${getRiskColor(entry.risk_level)}`}>
                            {entry.risk_level}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  
            </div>
</PageLayout>
    )
}
