"use client"

import { useEffect, useState, useCallback, useRef } from "react"
import { useTranslation } from "react-i18next"
import { getApiBaseUrl } from "@/lib/api-client"
import {
  Zap, Loader2, Activity, Radio, Server, Send, Plus, Trash2,
  RefreshCw, MessageSquare, Clock, Filter, Globe, Wifi, WifiOff,
  ChevronDown, ChevronRight, Copy,
} from "lucide-react"
import { PageLayout } from '@/components/page-layout';

interface BusStats {
  total_events: number
  total_nodes: number
  online_nodes: number
  total_subscriptions: number
  topics: string[]
}

interface NerveHealth {
  status: string
  component: string
  bus: BusStats
}

interface NerveEvent {
  id: string
  topic: string
  data: Record<string, unknown>
  source: string
  timestamp: string
  delivered_to: string[]
}

interface Subscription {
  id: string
  topic_pattern: string
  callback_url: string
  created_at: string
  delivery_count: number
  last_delivery: string | null
}

interface Node {
  node_id: string
  node_type: string
  status: string
  metadata: Record<string, unknown>
  registered_at: string
  last_heartbeat: string
  event_count: number
}

type ActiveTab = "overview" | "events" | "subscriptions" | "nodes" | "publish"

export function NerveClient() {
  const { t } = useTranslation()
  const apiBase = getApiBaseUrl()

  const [health, setHealth] = useState<NerveHealth | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<ActiveTab>("overview")

  // Events state
  const [events, setEvents] = useState<NerveEvent[]>([])
  const [eventTopicFilter, setEventTopicFilter] = useState("")
  const [expandedEvent, setExpandedEvent] = useState<string | null>(null)

  // Subscriptions state
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([])
  const [newSubId, setNewSubId] = useState("")
  const [newSubPattern, setNewSubPattern] = useState("")
  const [newSubCallback, setNewSubCallback] = useState("")

  // Nodes state
  const [nodes, setNodes] = useState<Node[]>([])
  const [newNodeId, setNewNodeId] = useState("")
  const [newNodeType, setNewNodeType] = useState("soma")

  // Publish state
  const [pubTopic, setPubTopic] = useState("")
  const [pubData, setPubData] = useState('{\n  \n}')
  const [pubSource, setPubSource] = useState("")
  const [pubResult, setPubResult] = useState<NerveEvent | null>(null)
  const [pubLoading, setPubLoading] = useState(false)

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/nerve/health`)
      setHealth(await res.json())
    } catch {}
  }, [apiBase])

  const fetchEvents = useCallback(async () => {
    try {
      const params = new URLSearchParams({ limit: "100" })
      if (eventTopicFilter) params.set("topic", eventTopicFilter)
      const res = await fetch(`${apiBase}/api/nerve/events?${params}`)
      const data = await res.json()
      setEvents(data.events || [])
    } catch {}
  }, [apiBase, eventTopicFilter])

  const fetchSubscriptions = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/nerve/subscriptions`)
      const data = await res.json()
      setSubscriptions(data.subscriptions || [])
    } catch {}
  }, [apiBase])

  const fetchNodes = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/nerve/nodes`)
      const data = await res.json()
      setNodes(data.nodes || [])
    } catch {}
  }, [apiBase])

  useEffect(() => {
    Promise.all([fetchHealth(), fetchEvents(), fetchSubscriptions(), fetchNodes()])
      .finally(() => setLoading(false))
  }, [fetchHealth, fetchEvents, fetchSubscriptions, fetchNodes])

  // Auto-refresh events
  useEffect(() => {
    if (activeTab !== "events") return
    const interval = setInterval(fetchEvents, 5000)
    return () => clearInterval(interval)
  }, [activeTab, fetchEvents])

  const handlePublish = useCallback(async () => {
    setPubLoading(true)
    setPubResult(null)
    try {
      let data = {}
      try { data = JSON.parse(pubData) } catch {}
      const res = await fetch(`${apiBase}/api/nerve/publish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: pubTopic, data, source: pubSource }),
      })
      setPubResult(await res.json())
      await fetchEvents()
      await fetchHealth()
    } catch {} finally {
      setPubLoading(false)
    }
  }, [pubTopic, pubData, pubSource, apiBase, fetchEvents, fetchHealth])

  const handleSubscribe = useCallback(async () => {
    if (!newSubId || !newSubPattern) return
    try {
      await fetch(`${apiBase}/api/nerve/subscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subscriber_id: newSubId,
          topic_pattern: newSubPattern,
          callback_url: newSubCallback,
        }),
      })
      setNewSubId("")
      setNewSubPattern("")
      setNewSubCallback("")
      await fetchSubscriptions()
    } catch {}
  }, [newSubId, newSubPattern, newSubCallback, apiBase, fetchSubscriptions])

  const handleUnsubscribe = useCallback(async (id: string) => {
    try {
      await fetch(`${apiBase}/api/nerve/subscribe/${id}`, { method: "DELETE" })
      await fetchSubscriptions()
    } catch {}
  }, [apiBase, fetchSubscriptions])

  const handleRegisterNode = useCallback(async () => {
    if (!newNodeId) return
    try {
      await fetch(`${apiBase}/api/nerve/nodes/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node_id: newNodeId, node_type: newNodeType }),
      })
      setNewNodeId("")
      await fetchNodes()
    } catch {}
  }, [newNodeId, newNodeType, apiBase, fetchNodes])

  const handleRemoveNode = useCallback(async (nodeId: string) => {
    try {
      await fetch(`${apiBase}/api/nerve/nodes/${nodeId}`, { method: "DELETE" })
      await fetchNodes()
    } catch {}
  }, [apiBase, fetchNodes])

  const handleHeartbeat = useCallback(async (nodeId: string) => {
    try {
      await fetch(`${apiBase}/api/nerve/nodes/heartbeat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node_id: nodeId }),
      })
      await fetchNodes()
    } catch {}
  }, [apiBase, fetchNodes])

  const formatTime = (iso: string) => {
    try { return new Date(iso).toLocaleString('zh-CN', { fractionalSecondDigits: 3 }) } catch { return iso }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text).catch(() => {})
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const bus = health?.bus

  const tabs: Array<{ key: ActiveTab; label: string; icon: typeof Zap }> = [
    { key: "overview", label: t('nerve.text1'), icon: Activity },
    { key: "events", label: t('nerve.t84238'), icon: MessageSquare },
    { key: "subscriptions", label: t('nerve.t98886'), icon: Radio },
    { key: "nodes", label: t('nerve.t83932'), icon: Server },
    { key: "publish", label: t('nerve.publishEvent'), icon: Send },
  ]

  return (
      <PageLayout title="神经">
        
    <div className="p-4 sm:p-3 lg:p-6 max-w-6xl mx-auto space-y-3 lg:space-y-6">
      {/* Header */}
      <div className="flex items-center gap-2 lg:gap-3">
        <div className="p-2 rounded-lg bg-gradient-to-br from-blue-500/20 to-purple-500/20">
          <Zap className="w-6 h-6 text-blue-400" />
        </div>
        <div>
          <h1 className="text-xl lg:text-2xl font-bold">⚡ Nerve — {t('nerve.title')}</h1>
          <p className="text-xs lg:text-sm text-muted-foreground">{t('nerve.message')}</p>
        </div>
      </div>

      {/* Stats Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 lg:gap-3 sm:gap-2 lg:gap-4">
        {[
          { label: t('nerve.t93835'), value: bus?.total_events ?? 0, icon: MessageSquare, color: "blue" },
          { label: t('nerve.t23323'), value: bus?.total_nodes ?? 0, icon: Server, color: "purple" },
          { label: t('nerve.t89895'), value: bus?.online_nodes ?? 0, icon: Wifi, color: "green" },
          { label: t('nerve.t45854'), value: bus?.total_subscriptions ?? 0, icon: Radio, color: "yellow" },
        ].map(s => {
          const Icon = s.icon
          return (
            <div key={s.label} className={`bg-card border border-${s.color}-500/20 rounded-xl p-3 lg:p-4`}>
              <div className="flex items-center gap-2 mb-1">
                <Icon className={`w-4 h-4 text-${s.color}-400`} />
                <span className="text-xs text-muted-foreground">{s.label}</span>
              </div>
              <div className="text-xl lg:text-2xl font-bold">{s.value}</div>
            </div>
          )
        })}
      </div>

      {/* Tab Navigation */}
      <div className="flex gap-1 sm:gap-2 border-b border-border pb-2 overflow-x-auto scrollbar-none">
        {tabs.map(tab => {
          const Icon = tab.icon
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-1.5 sm:gap-2 px-2.5 sm:px-4 py-2 rounded-t-lg text-xs sm:text-xs lg:text-sm font-medium transition-all whitespace-nowrap ${
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
          {/* Topics */}
          {bus?.topics && bus.topics.length > 0 && (
            <div className="bg-card border border-border rounded-xl p-5">
              <h3 className="font-semibold mb-3 flex items-center gap-2">
                <Globe className="w-4 h-4" /> {t('nerve.activeTopics')}
              </h3>
              <div className="flex flex-wrap gap-2">
                {bus.topics.map(topic => (
                  <button
                    key={topic}
                    onClick={() => { setEventTopicFilter(topic); setActiveTab("events") }}
                    className="px-3 py-1.5 rounded-full text-xs font-mono bg-blue-500/10 text-blue-400 border border-blue-500/20 hover:bg-blue-500/20 transition-colors"
                  >
                    {topic}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Quick Actions */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-2 lg:gap-4">
            <button
              onClick={() => setActiveTab("publish")}
              className="bg-card border border-border rounded-xl p-5 text-left hover:border-blue-500/30 hover:bg-blue-500/5 transition-all group"
            >
              <Send className="w-6 h-6 text-blue-400 mb-2 group-hover:scale-110 transition-transform" />
              <h4 className="font-semibold text-xs lg:text-sm">{t('nerve.text5')}</h4>
              <p className="text-xs text-muted-foreground mt-1">{t('nerve.text10')}</p>
            </button>
            <button
              onClick={() => setActiveTab("subscriptions")}
              className="bg-card border border-border rounded-xl p-5 text-left hover:border-purple-500/30 hover:bg-purple-500/5 transition-all group"
            >
              <Radio className="w-6 h-6 text-purple-400 mb-2 group-hover:scale-110 transition-transform" />
              <h4 className="font-semibold text-xs lg:text-sm">{t('nerve.text11')}</h4>
              <p className="text-xs text-muted-foreground mt-1">{t('nerve.text12')}</p>
            </button>
            <button
              onClick={() => setActiveTab("nodes")}
              className="bg-card border border-border rounded-xl p-5 text-left hover:border-green-500/30 hover:bg-green-500/5 transition-all group"
            >
              <Server className="w-6 h-6 text-green-400 mb-2 group-hover:scale-110 transition-transform" />
              <h4 className="font-semibold text-xs lg:text-sm">{t('nerve.text4')}</h4>
              <p className="text-xs text-muted-foreground mt-1">{t('nerve.register')}</p>
            </button>
          </div>

          {/* Recent Events Preview */}
          <div className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold flex items-center gap-2">
                <Clock className="w-4 h-4" /> {t('nerve.recentEvents')}
              </h3>
              <button
                onClick={() => setActiveTab("events")}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                {t('nerve.viewAll')} →
              </button>
            </div>
            {events.length === 0 ? (
              <p className="text-xs lg:text-sm text-muted-foreground/50 text-center py-3 lg:py-6">{t('nerve.empty')}</p>
            ) : (
              <div className="space-y-2">
                {events.slice(-5).reverse().map(evt => (
                  <div key={evt.id} className="flex items-center gap-2 lg:gap-3 text-xs lg:text-sm p-2 bg-muted/20 rounded-lg">
                    <span className="text-xs text-muted-foreground font-mono whitespace-nowrap">
                      {formatTime(evt.timestamp)}
                    </span>
                    <span className="px-2 py-0.5 bg-blue-500/10 text-blue-400 rounded text-xs font-mono">{evt.topic}</span>
                    {evt.source && <span className="text-xs text-muted-foreground">from {evt.source}</span>}
                    <span className="ml-auto text-xs text-muted-foreground">
                      → {evt.delivered_to.length} subscribers
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Events Tab */}
      {activeTab === "events" && (
        <div className="space-y-4">
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 sm:gap-2 lg:gap-3">
            <div className="flex items-center gap-2 flex-1">
              <Filter className="w-4 h-4 text-muted-foreground" />
              <input
                type="text"
                value={eventTopicFilter}
                onChange={(e) => setEventTopicFilter(e.target.value)}
                placeholder={t('nerve.text14')}
                className="flex-1 bg-card border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
              />
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={fetchEvents}
                className="flex items-center gap-1 px-3 py-2 bg-muted/30 border border-border rounded-lg text-xs lg:text-sm hover:bg-muted/50 transition-colors"
              >
                <RefreshCw className="w-3.5 h-3.5" /> {t('nerve.refresh')}
              </button>
              <span className="text-xs text-muted-foreground">{t('nerve.total')} {events.length} {t('nerve.items')}</span>
            </div>
          </div>

          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="max-h-[600px] overflow-y-auto">
              {events.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground/50">
                  <MessageSquare className="w-12 h-12 mb-2" />
                  <p className="text-xs lg:text-sm">{t('nerve.empty')}</p>
                </div>
              ) : (
                <div className="divide-y divide-border/50">
                  {events.slice().reverse().map(evt => (
                    <div key={evt.id} className="hover:bg-muted/20 transition-colors">
                      <button
                        onClick={() => setExpandedEvent(expandedEvent === evt.id ? null : evt.id)}
                        className="w-full text-left p-3 flex items-center gap-2 lg:gap-3"
                      >
                        {expandedEvent === evt.id ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                        <span className="text-xs text-muted-foreground font-mono whitespace-nowrap">
                          {formatTime(evt.timestamp)}
                        </span>
                        <span className="px-2 py-0.5 bg-blue-500/10 text-blue-400 rounded text-xs font-mono">{evt.topic}</span>
                        {evt.source && <span className="text-xs text-muted-foreground">from {evt.source}</span>}
                        <span className="ml-auto text-xs text-muted-foreground">
                          → {evt.delivered_to.length}
                        </span>
                      </button>
                      {expandedEvent === evt.id && (
                        <div className="px-3 pb-3 pt-0 ml-8">
                          <div className="bg-muted/30 rounded-lg p-3 space-y-2">
                            <div className="flex items-center justify-between">
                              <span className="text-xs text-muted-foreground">ID: {evt.id}</span>
                              <button
                                onClick={() => copyToClipboard(JSON.stringify(evt, null, 2))}
                                className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
                              >
                                <Copy className="w-3 h-3" /> {t('nerve.copy')}
                              </button>
                            </div>
                            <pre className="text-xs font-mono bg-background/50 rounded p-2 overflow-x-auto">
                              {JSON.stringify(evt.data, null, 2)}
                            </pre>
                            {evt.delivered_to.length > 0 && (
                              <div className="text-xs text-muted-foreground">
                                {t('nerve.deliveredTo')}: {evt.delivered_to.join(", ")}
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Subscriptions Tab */}
      {activeTab === "subscriptions" && (
        <div className="space-y-3 lg:space-y-6">
          {/* Add Subscription */}
          <div className="bg-card border border-border rounded-xl p-5">
            <h3 className="font-semibold mb-4 flex items-center gap-2">
              <Plus className="w-4 h-4" /> {t('nerve.addSubscription')}
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 lg:gap-3">
              <input
                type="text"
                value={newSubId}
                onChange={(e) => setNewSubId(e.target.value)}
                placeholder={t('nerve.t82001')}
                className="bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
              />
              <input
                type="text"
                value={newSubPattern}
                onChange={(e) => setNewSubPattern(e.target.value)}
                placeholder={t('nerve.text19')}
                className="bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
              />
              <input
                type="text"
                value={newSubCallback}
                onChange={(e) => setNewSubCallback(e.target.value)}
                placeholder={t('nerve.text20')}
                className="bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
              />
            </div>
            <button
              onClick={handleSubscribe}
              disabled={!newSubId || !newSubPattern}
              className="mt-3 px-4 py-2 bg-blue-500/10 text-blue-400 border border-blue-500/30 rounded-lg text-xs lg:text-sm font-medium hover:bg-blue-500/20 transition-colors disabled:opacity-40"
            >
              {t('nerve.subscribe')}
            </button>
          </div>

          {/* Subscription List */}
          <div className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold">{t('nerve.activeSubscriptions')} ({subscriptions.length})</h3>
              <button onClick={fetchSubscriptions} className="text-xs text-muted-foreground hover:text-foreground">
                <RefreshCw className="w-3 h-3" />
              </button>
            </div>
            {subscriptions.length === 0 ? (
              <p className="text-xs lg:text-sm text-muted-foreground/50 text-center py-3 lg:py-6">{t('nerve.text22')}</p>
            ) : (
              <div className="space-y-2">
                {subscriptions.map(sub => (
                  <div key={sub.id} className="flex items-center gap-2 lg:gap-3 p-3 bg-muted/20 rounded-lg">
                    <Radio className="w-4 h-4 text-purple-400" />
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-xs lg:text-sm">{sub.id}</span>
                        <span className="px-2 py-0.5 bg-purple-500/10 text-purple-400 rounded text-xs font-mono">
                          {sub.topic_pattern}
                        </span>
                      </div>
                      <div className="text-xs text-muted-foreground mt-1">
                        {t('nerve.deliveryCount')} {sub.delivery_count} {t('nerve.times')}
                        {sub.last_delivery && ` · {t('nerve.lastDelivery')}: ${formatTime(sub.last_delivery)}`}
                        {sub.callback_url && ` · ${sub.callback_url}`}
                      </div>
                    </div>
                    <button
                      onClick={() => handleUnsubscribe(sub.id)}
                      className="text-red-400 hover:text-red-300 p-1"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Nodes Tab */}
      {activeTab === "nodes" && (
        <div className="space-y-3 lg:space-y-6">
          {/* Register Node */}
          <div className="bg-card border border-border rounded-xl p-5">
            <h3 className="font-semibold mb-4 flex items-center gap-2">
              <Plus className="w-4 h-4" /> {t('nerve.registerNode')}
            </h3>
            <div className="flex flex-col sm:flex-row gap-2 lg:gap-3">
              <input
                type="text"
                value={newNodeId}
                onChange={(e) => setNewNodeId(e.target.value)}
                placeholder={t('nerve.t28135')}
                className="flex-1 bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
              />
              <select
                value={newNodeType}
                onChange={(e) => setNewNodeType(e.target.value)}
                className="bg-muted/30 border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
              >
                <option value="soma">Soma</option>
                <option value="sense">Sense</option>
                <option value="vein">Vein</option>
                <option value="will">Will</option>
                <option value="custom">自定义</option>
              </select>
              <button
                onClick={handleRegisterNode}
                disabled={!newNodeId}
                className="px-4 py-2 bg-green-500/10 text-green-400 border border-green-500/30 rounded-lg text-xs lg:text-sm font-medium hover:bg-green-500/20 transition-colors disabled:opacity-40"
              >
                {t('nerve.register')}
              </button>
            </div>
          </div>

          {/* Node List */}
          <div className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold">{t('nerve.nodeList')} ({nodes.length})</h3>
              <button onClick={fetchNodes} className="text-xs text-muted-foreground hover:text-foreground">
                <RefreshCw className="w-3 h-3" />
              </button>
            </div>
            {nodes.length === 0 ? (
              <p className="text-xs lg:text-sm text-muted-foreground/50 text-center py-3 lg:py-6">{t('nerve.text28')}</p>
            ) : (
              <div className="space-y-2">
                {nodes.map(node => (
                  <div key={node.node_id} className="flex items-center gap-2 lg:gap-3 p-3 bg-muted/20 rounded-lg">
                    <div className={`w-2.5 h-2.5 rounded-full ${node.status === "online" ? "bg-green-500" : "bg-muted-foreground/40"}`} />
                    <Server className="w-4 h-4 text-muted-foreground" />
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-xs lg:text-sm">{node.node_id}</span>
                        <span className="px-2 py-0.5 bg-muted/50 rounded text-xs">{node.node_type}</span>
                        <span className={`text-xs ${node.status === "online" ? "text-green-400" : "text-muted-foreground"}`}>
                          {node.status}
                        </span>
                      </div>
                      <div className="text-xs text-muted-foreground mt-1">
                        {t('nerve.registered')}: {formatTime(node.registered_at)}
                        {node.last_heartbeat && ` · {t('nerve.heartbeat')}: ${formatTime(node.last_heartbeat)}`}
                      </div>
                    </div>
                    <button
                      onClick={() => handleHeartbeat(node.node_id)}
                      className="px-2 py-1 text-xs bg-blue-500/10 text-blue-400 border border-blue-500/20 rounded hover:bg-blue-500/20 transition-colors"
                    >
                      {t('nerve.heartbeat')}
                    </button>
                    <button
                      onClick={() => handleRemoveNode(node.node_id)}
                      className="text-red-400 hover:text-red-300 p-1"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Publish Tab */}
      {activeTab === "publish" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-6">
          <div className="space-y-4">
            <h3 className="font-semibold">{t('nerve.text5')}</h3>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">主题 *</label>
              <input
                type="text"
                value={pubTopic}
                onChange={(e) => setPubTopic(e.target.value)}
                placeholder={t('nerve.text31')}
                className="w-full bg-card border border-border rounded-lg px-3 py-2 text-xs lg:text-sm font-mono"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t('nerve.text32')}</label>
              <input
                type="text"
                value={pubSource}
                onChange={(e) => setPubSource(e.target.value)}
                placeholder={t('nerve.t04967')}
                className="w-full bg-card border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t('nerve.t06701')}</label>
              <textarea
                value={pubData}
                onChange={(e) => setPubData(e.target.value)}
                rows={8}
                className="w-full bg-card border border-border rounded-lg px-3 py-2 text-xs lg:text-sm font-mono resize-none"
              />
            </div>
            <button
              onClick={handlePublish}
              disabled={!pubTopic || pubLoading}
              className="w-full py-2.5 bg-gradient-to-r from-blue-500 to-purple-500 text-white rounded-lg font-medium text-xs lg:text-sm disabled:opacity-40 disabled:cursor-not-allowed hover:from-blue-600 hover:to-purple-600 transition-all flex items-center justify-center gap-2"
            >
              {pubLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
              {pubLoading ? t('nerve.text35') : t('nerve.publishEvent')}
            </button>
          </div>

          <div className="space-y-4">
            <h3 className="font-semibold">{t('nerve.text36')}</h3>
            <div className="min-h-[300px] bg-card border border-border rounded-xl p-3 lg:p-4">
              {pubResult ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-green-400">
                    <Activity className="w-4 h-4" />
                    <span className="text-xs lg:text-sm font-medium">{t('nerve.text37')}</span>
                  </div>
                  <div className="bg-muted/30 rounded-lg p-3 space-y-2 text-xs">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">ID</span>
                      <span className="font-mono">{pubResult.id}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">主题</span>
                      <span className="font-mono text-blue-400">{pubResult.topic}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">{t('nerve.text17')}</span>
                      <span>{pubResult.delivered_to.length} subscribers</span>
                    </div>
                  </div>
                  <pre className="text-xs font-mono bg-background/50 rounded p-2 overflow-x-auto">
                    {JSON.stringify(pubResult, null, 2)}
                  </pre>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-muted-foreground/50">
                  <Send className="w-12 h-12 mb-2" />
                  <p className="text-xs lg:text-sm">{t('nerve.fillAndClick')}{t('nerve.publishEvent')}</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  
      </PageLayout>
    )
}
