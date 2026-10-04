"use client"

import { useEffect, useState, useCallback } from "react"
import { useTranslation } from "react-i18next"
import { getApiBaseUrl } from "@/lib/api-client"
import {
  Dna, Loader2, Plus, Trash2, Play, Search, Tag, Package,
  ChevronDown, ChevronRight, Copy, CheckCircle, X, Settings,
  Download, Upload, CopyPlus, History, RotateCcw, RefreshCw,
} from "lucide-react"
import { PageLayout } from '@/components/page-layout';
import { skillsListUrl } from "./skill-list-url"
import { sourceLabelKey, sourceTone, categoryLabelKey, labelOrRaw } from "./skill-display"

interface Template {
  template_id: string
  name: string
  category: string
  description: string
  version: string
  author: string
  tags: string[]
  config: Record<string, unknown>
  variables: Array<{ name: string; type: string; default?: string; description?: string }>
  usage_count: number
  builtin: boolean
}

interface GeneHealth {
  status: string
  total_templates: number
  builtin_count: number
  user_count: number
  by_category: Record<string, number>
}

// 学习型技能（learned_skills行——GET /api/gene/skill库存盘点端点）
interface LearnedSkill {
  skill_id: string
  name: string
  description: string
  category: string
  usage_count: number
  success_count: number
  success_rate: number
  created_at: number
  last_used_at: number
  source_session: string
  // 来源列（skill_learner.py口径归一后透出：extract/seed/test/unknown，不猜）
  source: string
  tags: string[]
  parameters: string[]
}

// 技能审计事件（skill_audit行——ADD/UPDATE/DELETE/FAIL/REVERT）
interface AuditEvent {
  audit_id: number
  skill_id: string
  event: string
  actor: string
  reason: string
  created_at: number
}

// 回滚预览（revert-preview零写入先行——Agent Zero _time_travel语义）
interface RevertPreview {
  skill_id: string
  event: AuditEvent
  action: string
  changes: Record<string, { from: unknown; to: unknown }>
}

type ActiveTab = "browse" | "create" | "instantiate" | "import" | "skills"

const CATEGORY_COLORS: Record<string, string> = {
  agent: "#3b82f6",
  knowledge_base: "#a855f7",
  workflow: "#22c55e",
  skill: "#eab308",
}

export function GeneClient() {
  const { t } = useTranslation()
  const CATEGORY_LABELS: Record<string, string> = {
    agent: "Agent",
    knowledge_base: t("gene.knowledgeBase"),
    workflow: t("gene.workflow"),
    skill: t("gene.skill"),
  }
  const apiBase = getApiBaseUrl()
  const [health, setHealth] = useState<GeneHealth | null>(null)
  const [templates, setTemplates] = useState<Template[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<ActiveTab>("browse")
  const [selectedCategory, setSelectedCategory] = useState<string>("")
  const [searchQuery, setSearchQuery] = useState<string>("")
  const [expandedTemplate, setExpandedTemplate] = useState<string | null>(null)

  // Create state
  const [newName, setNewName] = useState("")
  const [newCategory, setNewCategory] = useState("agent")
  const [newDescription, setNewDescription] = useState("")
  const [newTags, setNewTags] = useState("")
  const [newConfig, setNewConfig] = useState("{\n  \n}")
  const [createLoading, setCreateLoading] = useState(false)
  const [createResult, setCreateResult] = useState<{ template_id: string } | null>(null)

  // Instantiate state
  const [instTemplateId, setInstTemplateId] = useState("")
  const [instVariables, setInstVariables] = useState("{}")
  const [instLoading, setInstLoading] = useState(false)
  const [instResult, setInstResult] = useState<Record<string, unknown> | null>(null)

  // Import state
  const [importJson, setImportJson] = useState("")
  const [importLoading, setImportLoading] = useState(false)
  const [importResult, setImportResult] = useState<Record<string, unknown> | null>(null)
  const [importOverwrite, setImportOverwrite] = useState(false)

  // Skills tab state（技能库：审计视图+一键回滚）
  const [skills, setSkills] = useState<LearnedSkill[]>([])
  const [skillsLoading, setSkillsLoading] = useState(false)
  const [skillTotal, setSkillTotal] = useState(0)
  const [skillHasMore, setSkillHasMore] = useState(false)
  const [skillLoadingMore, setSkillLoadingMore] = useState(false)
  // keyset翻页游标（GET /skill的next_cursor单一真源）——"加载更多"锚定上一页末行
  // 的排序键，页边界不随翻页间隙的增删移位（offset=skills.length曾重复/漏行）。
  const [skillCursor, setSkillCursor] = useState<string | null>(null)
  // 分类过滤（GET /skill的categories facet单一真源）——chip选项集+全局计数从后端
  // facet渲染，不从分页rows推断（limit截断会漏分类=指标说谎）；skillCategory=""=全部
  const [skillCategory, setSkillCategory] = useState("")
  const [skillCategories, setSkillCategories] = useState<Array<{ category: string; count: number }>>([])
  // 可回滚事件集=后端AUDIT_STATE_EVENTS单一真源（GET /skill的revertible_events透出），
  // 按钮可见性据此判断——不再前端硬编码"FAIL除外"（新增事件类型两端漂移）
  const [revertibleEvents, setRevertibleEvents] = useState<string[]>(["ADD", "UPDATE", "DELETE", "REVERT"])
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null)
  const [skillHistory, setSkillHistory] = useState<Record<string, AuditEvent[]>>({})
  const [revertPreview, setRevertPreview] = useState<RevertPreview | null>(null)
  const [reverting, setReverting] = useState(false)
  const [revertResult, setRevertResult] = useState<string>("")

  const fetchAll = useCallback(async () => {
    try {
      const [h, t] = await Promise.all([
        fetch(`${apiBase}/api/gene/health`).then(r => r.json()),
        fetch(`${apiBase}/api/gene/templates`).then(r => r.json()).catch(() => ({ templates: [] })),
      ])
      setHealth(h)
      setTemplates(t.templates || t || [])
    } catch {} finally {
      setLoading(false)
    }
  }, [apiBase])

  useEffect(() => { fetchAll() }, [fetchAll])

  const handleCreate = useCallback(async () => {
    setCreateLoading(true)
    setCreateResult(null)
    try {
      let config = {}
      try { config = JSON.parse(newConfig) } catch {}
      const tags = newTags.split(",").map(s => s.trim()).filter(Boolean)
      const res = await fetch(`${apiBase}/api/gene/templates`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newName,
          category: newCategory,
          description: newDescription,
          tags,
          config,
        }),
      })
      const data = await res.json()
      setCreateResult(data)
      await fetchAll()
    } catch {} finally {
      setCreateLoading(false)
    }
  }, [newName, newCategory, newDescription, newTags, newConfig, apiBase, fetchAll])

  const handleDelete = useCallback(async (templateId: string) => {
    try {
      await fetch(`${apiBase}/api/gene/templates/${templateId}`, { method: "DELETE" })
      await fetchAll()
    } catch {}
  }, [apiBase, fetchAll])

  const handleInstantiate = useCallback(async () => {
    if (!instTemplateId) return
    setInstLoading(true)
    setInstResult(null)
    try {
      let variables = {}
      try { variables = JSON.parse(instVariables) } catch {}
      const res = await fetch(`${apiBase}/api/gene/templates/${instTemplateId}/instantiate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ variables }),
      })
      setInstResult(await res.json())
    } catch {} finally {
      setInstLoading(false)
    }
  }, [instTemplateId, instVariables, apiBase])

  const handleExport = useCallback(async (templateId: string) => {
    try {
      const res = await fetch(`${apiBase}/api/gene/templates/${templateId}/export`)
      const data = await res.json()
      const blob = new Blob([JSON.stringify(data.template, null, 2)], { type: "application/json" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `${templateId}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch {}
  }, [apiBase])

  const handleExportAll = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/gene/export?include_builtin=true`)
      const data = await res.json()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `gene-templates-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch {}
  }, [apiBase])

  const handleClone = useCallback(async (templateId: string) => {
    try {
      const res = await fetch(`${apiBase}/api/gene/templates/${templateId}/clone`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      })
      if (res.ok) await fetchAll()
    } catch {}
  }, [apiBase, fetchAll])

  const handleImport = useCallback(async () => {
    setImportLoading(true)
    setImportResult(null)
    try {
      let templates: Record<string, unknown>[] = []
      try {
        const parsed = JSON.parse(importJson)
        // Support both single template and bundle format
        if (Array.isArray(parsed)) {
          templates = parsed
        } else if (parsed.templates && Array.isArray(parsed.templates)) {
          templates = parsed.templates
        } else if (parsed.template_id || parsed.name) {
          templates = [parsed]
        }
      } catch {
        setImportResult({ error: t("gene.failed") })
        return
      }
      const res = await fetch(`${apiBase}/api/gene/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ templates, overwrite: importOverwrite }),
      })
      const data = await res.json()
      setImportResult(data)
      if (data.imported > 0) await fetchAll()
    } catch {} finally {
      setImportLoading(false)
    }
  }, [importJson, importOverwrite, apiBase, fetchAll])

  // ── 技能库（learned_skills）：库存盘点+审计历史+回滚 ──────────────

  const fetchSkills = useCallback(async (cursor: string | null = null, append = false) => {
    if (append) setSkillLoadingMore(true)
    else setSkillsLoading(true)
    try {
      // keyset翻页（"分页offset稳定性"销账）：cursor=null=首屏；append=带上一页末行
      // 游标续页——页边界不随翻页间隙的增删移位（offset=已加载条数曾重复/漏行）
      const res = await fetch(skillsListUrl(apiBase, cursor, skillCategory))
      const data = await res.json()
      const rows: LearnedSkill[] = data.skills || []
      setSkills(prev => (append ? [...prev, ...rows] : rows))
      // total=库存真实总数（不是本页条数——count只是len(本页)，122技能只显示100的病根）
      // 选了分类后=该分类真实总数（随过滤变，facet不变——标准faceted-search语义）
      setSkillTotal(typeof data.total === "number" ? data.total : rows.length)
      setSkillHasMore(Boolean(data.has_more))
      // 游标=后端next_cursor单一真源；无下一页=null（加载更多按钮随之隐藏）
      setSkillCursor(typeof data.next_cursor === "string" && data.next_cursor ? data.next_cursor : null)
      // 分类facet=全局分布单一真源（chip选项集+计数），过滤/分页/刷新均随之漂移
      if (Array.isArray(data.categories)) setSkillCategories(data.categories)
      if (Array.isArray(data.revertible_events) && data.revertible_events.length > 0) {
        setRevertibleEvents(data.revertible_events)
      }
    } catch {} finally {
      setSkillsLoading(false)
      setSkillLoadingMore(false)
    }
  }, [apiBase, skillCategory])

  useEffect(() => { fetchSkills() }, [fetchSkills])

  const loadSkillHistory = useCallback(async (skillId: string) => {
    try {
      const res = await fetch(`${apiBase}/api/gene/skill/${skillId}/history`)
      const data = await res.json()
      setSkillHistory(prev => ({ ...prev, [skillId]: data.events || [] }))
    } catch {}
  }, [apiBase])

  const toggleSkillHistory = useCallback(async (skillId: string) => {
    if (expandedSkill === skillId) {
      setExpandedSkill(null)
      return
    }
    setExpandedSkill(skillId)
    if (!skillHistory[skillId]) await loadSkillHistory(skillId)
  }, [expandedSkill, skillHistory, loadSkillHistory])

  const handleRevertPreview = useCallback(async (skillId: string, auditId: number) => {
    setRevertResult("")
    try {
      const res = await fetch(`${apiBase}/api/gene/skill/${skillId}/revert-preview?audit_id=${auditId}`)
      const data = await res.json()
      if (res.ok) setRevertPreview(data as RevertPreview)
      else setRevertResult(String(data.detail || "preview failed"))
    } catch {}
  }, [apiBase])

  const handleRevert = useCallback(async () => {
    if (!revertPreview) return
    setReverting(true)
    setRevertResult("")
    try {
      const { skill_id: skillId, event } = revertPreview
      const res = await fetch(
        `${apiBase}/api/gene/skill/${skillId}/revert?audit_id=${event.audit_id}`,
        { method: "POST" },
      )
      const data = await res.json()
      if (res.ok) {
        setRevertResult(t("gene.revertDone"))
        setRevertPreview(null)
        await fetchSkills()
        await loadSkillHistory(skillId)
      } else {
        setRevertResult(String(data.detail || "revert failed"))
      }
    } catch {
      setRevertResult("revert request failed")
    } finally {
      setReverting(false)
    }
  }, [revertPreview, apiBase, fetchSkills, loadSkillHistory, t])

  const fmtTime = (ts: number) => (ts ? new Date(ts * 1000).toLocaleString() : "—")

  const filtered = templates.filter(t => {
    if (selectedCategory && t.category !== selectedCategory) return false
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      return t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q) || t.tags.some(tag => tag.toLowerCase().includes(q))
    }
    return true
  })

  const categories = Object.keys(health?.by_category || {})

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const tabs: Array<{ key: ActiveTab; label: string; icon: typeof Dna }> = [
    { key: "browse", label: t("gene.text4"), icon: Package },
    { key: "create", label: t("gene.text5"), icon: Plus },
    { key: "instantiate", label: t("gene.text6"), icon: Play },
    { key: "import", label: t("gene.export"), icon: Download },
    { key: "skills", label: t("gene.skillLibraryTab"), icon: History },
  ]

  return (
      <PageLayout title="Gene">
        
    <div className="p-4 sm:p-3 lg:p-6 max-w-6xl mx-auto space-y-3 lg:space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-lg bg-gradient-to-br from-emerald-500/20 to-teal-500/20">
          <Dna className="w-6 h-6 text-emerald-400" />
        </div>
        <div>
          <h1 className="text-xl lg:text-2xl font-bold">🧬 Gene — {t("gene.text7")}</h1>
          <p className="text-xs lg:text-sm text-muted-foreground">{t("gene.text8")}</p>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-2 lg:gap-4">
        {[
          { label: t("gene.text9"), value: health?.total_templates ?? 0 },
          { label: t("gene.text10"), value: health?.builtin_count ?? 0 },
          { label: t("gene.user"), value: health?.user_count ?? 0 },
          { label: t("gene.text11"), value: categories.length },
        ].map(s => (
          <div key={s.label} className="bg-card border border-border rounded-xl p-3 lg:p-4">
            <div className="text-xl lg:text-2xl font-bold">{s.value}</div>
            <div className="text-xs text-muted-foreground">{s.label}</div>
          </div>
        ))}
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

      {/* Skills Tab — 技能库：审计视图 + 一键回滚（revert-preview零写入先行） */}
      {activeTab === "skills" && (
        <div className="space-y-4">
          <div className="flex items-start justify-between gap-2">
            <div>
              <h3 className="font-semibold flex items-center gap-2">
                <History className="w-4 h-4" /> {t("gene.skillLibrary")}
              </h3>
              <p className="text-xs text-muted-foreground mt-1">{t("gene.skillLibraryDesc")}</p>
              {skillTotal > 0 && (
                <p className="text-xs text-muted-foreground mt-1">
                  {t("gene.skillCount", { shown: skills.length, total: skillTotal })}
                </p>
              )}
            </div>
            <button
              onClick={() => fetchSkills()}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-muted/30 text-muted-foreground hover:bg-muted/50 transition-all shrink-0"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${skillsLoading ? "animate-spin" : ""}`} />
              {t("gene.refresh")}
            </button>
          </div>

          {/* 分类过滤chip——选项集+全局计数=categories facet单一真源（"全部"+各分类）；
              点击切过滤：skillCategory变更→fetchSkills重建→useEffect自动带参重查，
              分页从0重来；chip计数是全局分布（过滤后不变），"已显示X/Y"的Y随过滤变 */}
          {skillCategories.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              <button
                onClick={() => setSkillCategory("")}
                className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-all ${
                  skillCategory === ""
                    ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-400"
                    : "bg-muted/30 border-border text-muted-foreground hover:text-foreground hover:bg-muted/50"
                }`}
              >
                {t("gene.allCategories")}
                {/* 全局总数=facet求和（不是skillTotal——过滤时skillTotal是过滤后的） */}
                <span className="ml-1 text-[10px] opacity-70">{skillCategories.reduce((n, c) => n + c.count, 0)}</span>
              </button>
              {skillCategories.map(c => (
                <button
                  key={c.category}
                  onClick={() => setSkillCategory(c.category)}
                  className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-all ${
                    skillCategory === c.category
                      ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-400"
                      : "bg-muted/30 border-border text-muted-foreground hover:text-foreground hover:bg-muted/50"
                  }`}
                >
                  {labelOrRaw(t, categoryLabelKey(c.category), c.category)}
                  <span className="ml-1 text-[10px] opacity-70">{c.count}</span>
                </button>
              ))}
            </div>
          )}

          {revertResult && (
            <div className="rounded-lg p-3 text-xs lg:text-sm bg-emerald-500/10 text-emerald-400">
              {revertResult}
            </div>
          )}

          {/* 回滚预览：先看逐字段diff再动手（Agent Zero _time_travel preview语义） */}
          {revertPreview && (
            <div className="bg-card border border-amber-500/40 rounded-xl p-3 lg:p-4 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <h4 className="text-sm font-semibold flex items-center gap-2 min-w-0">
                  <RotateCcw className="w-4 h-4 text-amber-400 shrink-0" />
                  <span className="truncate">{t("gene.revertPreviewTitle")}</span>
                  <span className="text-xs text-muted-foreground font-mono shrink-0">
                    #{revertPreview.event.audit_id} · {revertPreview.action}
                  </span>
                </h4>
                <button
                  onClick={() => setRevertPreview(null)}
                  className="text-muted-foreground hover:text-foreground shrink-0"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="space-y-1 text-xs font-mono">
                {Object.entries(revertPreview.changes).map(([field, ch]) => (
                  <div key={field} className="flex items-center gap-2">
                    <span className="text-muted-foreground shrink-0 w-28 truncate">{field}</span>
                    <span className="text-red-400 truncate">{JSON.stringify(ch.from)}</span>
                    <span className="text-muted-foreground shrink-0">→</span>
                    <span className="text-emerald-400 truncate">{JSON.stringify(ch.to)}</span>
                  </div>
                ))}
                {Object.keys(revertPreview.changes).length === 0 && (
                  <div className="text-muted-foreground">{t("gene.noChanges")}</div>
                )}
              </div>
              <button
                onClick={handleRevert}
                disabled={reverting}
                className="w-full py-2 bg-gradient-to-r from-amber-500 to-orange-500 text-white rounded-lg font-medium text-xs lg:text-sm disabled:opacity-40 disabled:cursor-not-allowed hover:from-amber-600 hover:to-orange-600 transition-all flex items-center justify-center gap-2"
              >
                {reverting ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-4 h-4" />}
                {t("gene.revertConfirm")}
              </button>
            </div>
          )}

          {skillsLoading && skills.length === 0 ? (
            <div className="flex items-center justify-center h-24">
              <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
            </div>
          ) : skills.length === 0 ? (
            <div className="bg-card border border-border rounded-xl p-6 text-center text-sm text-muted-foreground">
              {t("gene.noSkills")}
            </div>
          ) : (
            <div className="space-y-2">
              {skills.map(skill => (
                <div key={skill.skill_id} className="bg-card border border-border rounded-xl overflow-hidden">
                  <button
                    onClick={() => toggleSkillHistory(skill.skill_id)}
                    className="w-full flex items-center gap-3 p-3 text-left hover:bg-muted/30 transition-colors"
                  >
                    {expandedSkill === skill.skill_id ? (
                      <ChevronDown className="w-4 h-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="w-4 h-4 shrink-0 text-muted-foreground" />
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium truncate">{skill.name}</div>
                      <div className="text-xs text-muted-foreground truncate">{skill.description}</div>
                    </div>
                    <div className="flex items-center gap-2 lg:gap-3 shrink-0 text-xs text-muted-foreground">
                      <span className="px-2 py-0.5 rounded bg-muted/40">{labelOrRaw(t, categoryLabelKey(skill.category), skill.category)}</span>
                      {/* 来源chip（后端source口径归一直出）：extract绿/seed蓝/test玫红/unknown灰，未映射值虚线框直出原值 */}
                      <span className={`px-2 py-0.5 rounded ${sourceTone(skill.source || "")}`}>{labelOrRaw(t, sourceLabelKey(skill.source || ""), skill.source || "—")}</span>
                      <span>{t("gene.skillUsage")} {skill.usage_count}</span>
                      <span className={skill.success_rate >= 0.8 ? "text-emerald-400" : skill.success_rate >= 0.5 ? "text-amber-400" : "text-red-400"}>
                        {t("gene.skillSuccessRate")} {(skill.success_rate * 100).toFixed(0)}%
                      </span>
                      <span className="hidden lg:inline">{fmtTime(skill.last_used_at || skill.created_at)}</span>
                    </div>
                  </button>
                  {expandedSkill === skill.skill_id && (
                    <div className="border-t border-border p-3 space-y-2">
                      <div className="text-xs text-muted-foreground">
                        {t("gene.sourceKind")}: <span className="font-mono">{skill.source || "—"}</span> · {t("gene.skillSource")}: <span className="font-mono">{skill.source_session || "—"}</span>
                        {skill.tags.length > 0 && <> · {t("gene.skillTags")}: {skill.tags.join(", ")}</>}
                      </div>
                      <h5 className="text-xs font-semibold flex items-center gap-1.5">
                        <History className="w-3.5 h-3.5" /> {t("gene.skillHistory")}
                      </h5>
                      {(skillHistory[skill.skill_id] || []).length === 0 ? (
                        <div className="text-xs text-muted-foreground">{t("gene.historyEmpty")}</div>
                      ) : (
                        <div className="space-y-1">
                          {(skillHistory[skill.skill_id] || []).map(ev => (
                            <div key={ev.audit_id} className="flex items-center gap-2 text-xs bg-muted/20 rounded-lg px-2.5 py-1.5">
                              <span className={`px-1.5 py-0.5 rounded font-mono shrink-0 ${
                                ev.event === "FAIL" ? "bg-red-500/15 text-red-400"
                                : ev.event === "REVERT" ? "bg-amber-500/15 text-amber-400"
                                : "bg-emerald-500/15 text-emerald-400"
                              }`}>{ev.event}</span>
                              <span className="text-muted-foreground truncate flex-1">
                                #{ev.audit_id} · {ev.actor || "—"} · {ev.reason || "—"}
                              </span>
                              <span className="text-muted-foreground shrink-0 hidden sm:inline">{fmtTime(ev.created_at)}</span>
                              {revertibleEvents.includes(ev.event) && (
                                <button
                                  onClick={() => handleRevertPreview(skill.skill_id, ev.audit_id)}
                                  className="shrink-0 px-2 py-1 rounded bg-amber-500/15 text-amber-400 hover:bg-amber-500/25 transition-colors"
                                >
                                  {t("gene.revertToBefore")}
                                </button>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
              {skillHasMore && (
                <button
                  onClick={() => fetchSkills(skillCursor, true)}
                  disabled={skillLoadingMore || !skillCursor}
                  className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl text-xs font-medium bg-muted/30 text-muted-foreground hover:bg-muted/50 transition-all disabled:opacity-50"
                >
                  {skillLoadingMore && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  {t("gene.loadMore")}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* Browse Tab */}
      {activeTab === "browse" && (
        <div className="space-y-4">
          {/* Search Bar */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t("gene.search")}
              className="w-full bg-card border border-border rounded-lg pl-10 pr-4 py-2.5 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-500/50 transition-all"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>

          {/* Category Filter */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => setSelectedCategory("")}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                !selectedCategory
                  ? "bg-foreground text-background"
                  : "bg-muted/30 text-muted-foreground hover:bg-muted/50"
              }`}
            >
              {t("gene.ta8b0c")}
            </button>
            {categories.map(cat => {
              const color = CATEGORY_COLORS[cat] || "#6b7280"
              return (
                <button
                  key={cat}
                  onClick={() => setSelectedCategory(selectedCategory === cat ? "" : cat)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                    selectedCategory === cat
                      ? "text-white"
                      : "text-muted-foreground hover:opacity-80"
                  }`}
                  style={{
                    backgroundColor: selectedCategory === cat ? color : `${color}15`,
                    border: `1px solid ${selectedCategory === cat ? color : `${color}30`}`,
                    color: selectedCategory === cat ? "#fff" : color,
                  }}
                >
                  {CATEGORY_LABELS[cat] || cat} ({health?.by_category?.[cat] ?? 0})
                </button>
              )
            })}
          </div>

          {/* Template Grid */}
          {filtered.length === 0 ? (
            <div className="text-center py-16 text-muted-foreground/50">
              <Package className="w-12 h-12 mx-auto mb-2" />
              <p className="text-xs lg:text-sm">{t("gene.empty")}</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-4">
              {filtered.map(tmpl => {
                const color = CATEGORY_COLORS[tmpl.category] || "#6b7280"
                const expanded = expandedTemplate === tmpl.template_id
                return (
                  <div
                    key={tmpl.template_id}
                    className="bg-card border border-border rounded-xl overflow-hidden hover:border-border/80 transition-all"
                  >
                    <button
                      onClick={() => setExpandedTemplate(expanded ? null : tmpl.template_id)}
                      className="w-full text-left p-3 lg:p-4"
                    >
                      <div className="flex items-start gap-3">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-1">
                            <span
                              className="px-2 py-0.5 rounded text-xs font-medium"
                              style={{ background: `${color}20`, color, border: `1px solid ${color}40` }}
                            >
                              {CATEGORY_LABELS[tmpl.category] || tmpl.category}
                            </span>
                            {tmpl.builtin && (
                              <span className="text-xs text-muted-foreground bg-muted/30 px-2 py-0.5 rounded">{t("gene.text12")}</span>
                            )}
                            <span className="text-xs text-muted-foreground ml-auto">v{tmpl.version}</span>
                          </div>
                          <h4 className="font-semibold text-xs lg:text-sm">{tmpl.name}</h4>
                          <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{tmpl.description}</p>
                        </div>
                        {expanded ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
                      </div>
                    </button>

                    {expanded && (
                      <div className="border-t border-border p-3 lg:p-4 space-y-3 bg-muted/10">
                        <div className="flex items-center gap-2 lg:gap-4 text-xs text-muted-foreground">
                          <span>ID: <code className="font-mono">{tmpl.template_id}</code></span>
                          <span>{t("gene.text13")}: {tmpl.author}</span>
                          <span>{t("gene.text14")}: {tmpl.usage_count}{t("gene.text15")}</span>
                        </div>

                        {tmpl.tags.length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {tmpl.tags.map(tag => (
                              <span key={tag} className="px-2 py-0.5 bg-muted/30 rounded text-xs flex items-center gap-1">
                                <Tag className="w-2.5 h-2.5" /> {tag}
                              </span>
                            ))}
                          </div>
                        )}

                        {tmpl.variables.length > 0 && (
                          <div>
                            <h5 className="text-xs font-medium text-muted-foreground mb-2">{t("gene.text16")}</h5>
                            <div className="space-y-1">
                              {tmpl.variables.map((v, i) => (
                                <div key={i} className="flex items-center gap-2 text-xs bg-muted/20 rounded p-2">
                                  <code className="font-mono text-emerald-400">{v.name}</code>
                                  <span className="text-muted-foreground">({v.type})</span>
                                  {v.default && <span className="text-muted-foreground">= {v.default}</span>}
                                  {v.description && <span className="text-muted-foreground ml-auto">{v.description}</span>}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {Object.keys(tmpl.config).length > 0 && (
                          <div>
                            <h5 className="text-xs font-medium text-muted-foreground mb-2">{t("gene.text17")}</h5>
                            <pre className="text-xs font-mono bg-background/50 rounded p-2 overflow-x-auto max-h-40">
                              {JSON.stringify(tmpl.config, null, 2)}
                            </pre>
                          </div>
                        )}

                        <div className="flex gap-2 pt-2">
                          <button
                            onClick={() => {
                              setInstTemplateId(tmpl.template_id)
                              setActiveTab("instantiate")
                            }}
                            className="flex items-center gap-1 px-3 py-1.5 bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded-lg text-xs font-medium hover:bg-emerald-500/20 transition-colors"
                          >
                            <Play className="w-3 h-3" /> {t("gene.text6")}
                          </button>
                          <button
                            onClick={() => handleExport(tmpl.template_id)}
                            className="flex items-center gap-1 px-3 py-1.5 bg-blue-500/10 text-blue-400 border border-blue-500/20 rounded-lg text-xs font-medium hover:bg-blue-500/20 transition-colors"
                          >
                            <Download className="w-3 h-3" /> {t("gene.text18")}
                          </button>
                          <button
                            onClick={() => handleClone(tmpl.template_id)}
                            className="flex items-center gap-1 px-3 py-1.5 bg-purple-500/10 text-purple-400 border border-purple-500/20 rounded-lg text-xs font-medium hover:bg-purple-500/20 transition-colors"
                          >
                            <CopyPlus className="w-3 h-3" /> {t("gene.text19")}
                          </button>
                          {!tmpl.builtin && (
                            <button
                              onClick={() => handleDelete(tmpl.template_id)}
                              className="flex items-center gap-1 px-3 py-1.5 bg-red-500/10 text-red-400 border border-red-500/20 rounded-lg text-xs font-medium hover:bg-red-500/20 transition-colors"
                            >
                              <Trash2 className="w-3 h-3" /> {t("gene.delete")}
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Create Tab */}
      {activeTab === "create" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-6">
          <div className="space-y-4">
            <h3 className="font-semibold">{t("gene.text20")}</h3>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t("gene.name")} *</label>
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder={t("gene.text21")}
                className="w-full bg-card border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t("gene.text22")}</label>
              <select
                value={newCategory}
                onChange={(e) => setNewCategory(e.target.value)}
                className="w-full bg-card border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
              >
                <option value="agent">Agent</option>
                <option value="knowledge_base">{t("gene.text1")}</option>
                <option value="workflow">{t("gene.text2")}</option>
                <option value="skill">{t("gene.text3")}</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t("gene.description")}</label>
              <textarea
                value={newDescription}
                onChange={(e) => setNewDescription(e.target.value)}
                placeholder={t("gene.text23")}
                rows={3}
                className="w-full bg-card border border-border rounded-lg px-3 py-2 text-xs lg:text-sm resize-none"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t("gene.tags")}</label>
              <input
                type="text"
                value={newTags}
                onChange={(e) => setNewTags(e.target.value)}
                placeholder="rag, qa, knowledge"
                className="w-full bg-card border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t("gene.text24")}</label>
              <textarea
                value={newConfig}
                onChange={(e) => setNewConfig(e.target.value)}
                rows={6}
                className="w-full bg-card border border-border rounded-lg px-3 py-2 text-xs lg:text-sm font-mono resize-none"
              />
            </div>
            <button
              onClick={handleCreate}
              disabled={!newName || createLoading}
              className="w-full py-2.5 bg-gradient-to-r from-emerald-500 to-teal-500 text-white rounded-lg font-medium text-xs lg:text-sm disabled:opacity-40 disabled:cursor-not-allowed hover:from-emerald-600 hover:to-teal-600 transition-all flex items-center justify-center gap-2"
            >
              {createLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              {createLoading ? t("gene.text25") : t("gene.create")}
            </button>
          </div>

          <div className="space-y-4">
            <h3 className="font-semibold">{t("gene.text26")}</h3>
            <div className="min-h-[300px] bg-card border border-border rounded-xl p-3 lg:p-4">
              {createResult ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-green-400">
                    <CheckCircle className="w-5 h-5" />
                    <span className="font-medium">{t("gene.success")}</span>
                  </div>
                  <div className="bg-muted/30 rounded-lg p-3 text-xs lg:text-sm">
                    <span className="text-muted-foreground">{t("gene.text27")}: </span>
                    <code className="font-mono text-emerald-400">{createResult.template_id}</code>
                  </div>
                  <button
                    onClick={() => setActiveTab("browse")}
                    className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                  >
                    {t("gene.back")} →
                  </button>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-muted-foreground/50">
                  <Settings className="w-12 h-12 mb-2" />
                  <p className="text-xs lg:text-sm">{t("gene.text29")}</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Instantiate Tab */}
      {activeTab === "instantiate" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-6">
          <div className="space-y-4">
            <h3 className="font-semibold">{t("gene.text30")}</h3>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t("gene.select")}</label>
              <select
                value={instTemplateId}
                onChange={(e) => setInstTemplateId(e.target.value)}
                className="w-full bg-card border border-border rounded-lg px-3 py-2 text-xs lg:text-sm"
              >
                <option value="">-- {t("gene.select")} --</option>
                {templates.map(t => (
                  <option key={t.template_id} value={t.template_id}>
                    [{CATEGORY_LABELS[t.category] || t.category}] {t.name}
                  </option>
                ))}
              </select>
            </div>

            {instTemplateId && (() => {
              const tmpl = templates.find(t => t.template_id === instTemplateId)
              if (!tmpl) return null
              return (
                <div className="bg-muted/20 rounded-xl p-3 lg:p-4 space-y-2">
                  <h4 className="font-medium text-xs lg:text-sm">{tmpl.name}</h4>
                  <p className="text-xs text-muted-foreground">{tmpl.description}</p>
                  {tmpl.variables.length > 0 && (
                    <div className="text-xs text-muted-foreground">
                      {t("gene.text31")}: {tmpl.variables.map(v => v.name).join(", ")}
                    </div>
                  )}
                </div>
              )
            })()}

            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t("gene.text32")}</label>
              <textarea
                value={instVariables}
                onChange={(e) => setInstVariables(e.target.value)}
                rows={6}
                className="w-full bg-card border border-border rounded-lg px-3 py-2 text-xs lg:text-sm font-mono resize-none"
                placeholder='{"key": "value"}'
              />
            </div>

            <button
              onClick={handleInstantiate}
              disabled={!instTemplateId || instLoading}
              className="w-full py-2.5 bg-gradient-to-r from-emerald-500 to-teal-500 text-white rounded-lg font-medium text-xs lg:text-sm disabled:opacity-40 disabled:cursor-not-allowed hover:from-emerald-600 hover:to-teal-600 transition-all flex items-center justify-center gap-2"
            >
              {instLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              {instLoading ? t("gene.text33") : t("gene.start")}
            </button>
          </div>

          <div className="space-y-4">
            <h3 className="font-semibold">{t("gene.text34")}</h3>
            <div className="min-h-[300px] bg-card border border-border rounded-xl p-3 lg:p-4">
              {instResult ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-green-400">
                    <CheckCircle className="w-5 h-5" />
                    <span className="font-medium">{t("gene.text35")}</span>
                  </div>
                  <pre className="text-xs font-mono bg-background/50 rounded p-3 overflow-x-auto max-h-[400px]">
                    {JSON.stringify(instResult, null, 2)}
                  </pre>
                  <button
                    onClick={() => navigator.clipboard.writeText(JSON.stringify(instResult, null, 2)).catch(() => {})}
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <Copy className="w-3 h-3" /> {t("gene.copy")}
                  </button>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-muted-foreground/50">
                  <Play className="w-12 h-12 mb-2" />
                  <p className="text-xs lg:text-sm">{t("gene.text37")}</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Import/Export Tab */}
      {activeTab === "import" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-6">
          {/* Import Section */}
          <div className="space-y-4">
            <h3 className="font-semibold flex items-center gap-2">
              <Upload className="w-4 h-4" /> {t("gene.import")}
            </h3>
            <p className="text-xs text-muted-foreground">
              {t("gene.tb6e68")}
            </p>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t("gene.text38")}</label>
              <textarea
                value={importJson}
                onChange={(e) => setImportJson(e.target.value)}
                rows={10}
                className="w-full bg-card border border-border rounded-lg px-3 py-2 text-xs lg:text-sm font-mono resize-none"
                placeholder={'{\n  "name": "My Template",\n  "category": "agent",\n  "description": "...",\n  "tags": ["custom"],\n  "config": {}\n}'}
              />
            </div>
            <label className="flex items-center gap-2 text-xs lg:text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={importOverwrite}
                onChange={(e) => setImportOverwrite(e.target.checked)}
                className="rounded"
              />
              {t("gene.tc2190")}
            </label>
            <button
              onClick={handleImport}
              disabled={!importJson.trim() || importLoading}
              className="w-full py-2.5 bg-gradient-to-r from-blue-500 to-indigo-500 text-white rounded-lg font-medium text-xs lg:text-sm disabled:opacity-40 disabled:cursor-not-allowed hover:from-blue-600 hover:to-indigo-600 transition-all flex items-center justify-center gap-2"
            >
              {importLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
              {importLoading ? t("gene.text39") : t("gene.import")}
            </button>
            {importResult && (
              <div className={`rounded-lg p-3 text-xs lg:text-sm ${importResult.error ? "bg-red-500/10 text-red-400" : "bg-emerald-500/10 text-emerald-400"}`}>
                {importResult.error ? (
                  <span>{String(importResult.error)}</span>
                ) : (
                  <div className="space-y-1">
                    <span>{t("gene.text40")} {String(importResult.imported)}/{String(importResult.total)} {t("gene.text41")}</span>
                    {Array.isArray(importResult.results) && (importResult.results as Array<Record<string, string>>).map((r, i) => (
                      <div key={i} className="text-xs opacity-75">
                        {r.success ? "✅" : "❌"} {r.template_id}: {r.message}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Export Section */}
          <div className="space-y-4">
            <h3 className="font-semibold flex items-center gap-2">
              <Download className="w-4 h-4" /> {t("gene.text42")}
            </h3>
            <p className="text-xs text-muted-foreground">
              {t("gene.t21fff")}
            </p>
            <div className="bg-card border border-border rounded-xl p-3 lg:p-6 space-y-4">
              <div className="grid grid-cols-2 gap-2 lg:gap-4">
                <div className="bg-muted/20 rounded-lg p-3 lg:p-4 text-center">
                  <div className="text-xl lg:text-2xl font-bold">{health?.total_templates ?? 0}</div>
                  <div className="text-xs text-muted-foreground">{t("gene.text43")}</div>
                </div>
                <div className="bg-muted/20 rounded-lg p-3 lg:p-4 text-center">
                  <div className="text-xl lg:text-2xl font-bold">{health?.user_count ?? 0}</div>
                  <div className="text-xs text-muted-foreground">{t("gene.user")}</div>
                </div>
              </div>
              <button
                onClick={handleExportAll}
                className="w-full py-2.5 bg-gradient-to-r from-emerald-500 to-teal-500 text-white rounded-lg font-medium text-xs lg:text-sm hover:from-emerald-600 hover:to-teal-600 transition-all flex items-center justify-center gap-2"
              >
                <Download className="w-4 h-4" /> {t("gene.all")}
              </button>
              <p className="text-xs text-center text-muted-foreground">
                {t("gene.t6ddc6")}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  
      </PageLayout>
    )
}
