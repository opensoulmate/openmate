"use client"

import { useEffect, useState, useCallback } from "react"
import { useTranslation } from "react-i18next"
import { getApiBaseUrl } from "@/lib/api-client"
import { cn } from "@/lib/utils"
import {
  Tag, Loader2, Plus, Trash2, Edit3, X, Check, Palette, Hash, RefreshCw,
} from "lucide-react"
import { PageLayout } from '@/components/page-layout';

interface TagItem {
  id: string
  name: string
  color?: string
  count?: number
  usage_count?: number
  created_at?: string
  [key: string]: unknown
}

const DEFAULT_COLORS = [
  "#ef4444", "#f97316", "#f59e0b", "#eab308",
  "#84cc16", "#22c55e", "#10b981", "#14b8a6",
  "#06b6d4", "#0ea5e9", "#3b82f6", "#6366f1",
  "#8b5cf6", "#a855f7", "#d946ef", "#ec4899",
]

export function TagsClient() {
  const { t } = useTranslation()
  const apiBase = getApiBaseUrl()
  const [tags, setTags] = useState<TagItem[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [formName, setFormName] = useState("")
  const [formColor, setFormColor] = useState(DEFAULT_COLORS[6])
  const [submitting, setSubmitting] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const fetchTags = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/tags/`)
      if (res.ok) {
        const data = await res.json()
        setTags(Array.isArray(data) ? data : data.tags || data.items || [])
      }
    } catch {} finally {
      setLoading(false)
    }
  }, [apiBase])

  useEffect(() => { fetchTags() }, [fetchTags])

  const resetForm = () => {
    setFormName("")
    setFormColor(DEFAULT_COLORS[6])
    setEditingId(null)
    setShowForm(false)
  }

  const handleCreate = async () => {
    if (!formName.trim()) return
    setSubmitting(true)
    try {
      const res = await fetch(`${apiBase}/api/tags/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: formName.trim(), color: formColor }),
      })
      if (res.ok) {
        resetForm()
        await fetchTags()
      }
    } catch {} finally {
      setSubmitting(false)
    }
  }

  const handleUpdate = async () => {
    if (!formName.trim() || !editingId) return
    setSubmitting(true)
    try {
      const res = await fetch(`${apiBase}/api/tags/${editingId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: formName.trim(), color: formColor }),
      })
      if (res.ok) {
        resetForm()
        await fetchTags()
      }
    } catch {} finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (id: string) => {
    setDeletingId(id)
    try {
      const res = await fetch(`${apiBase}/api/tags/${id}`, { method: "DELETE" })
      if (res.ok) await fetchTags()
    } catch {} finally {
      setDeletingId(null)
    }
  }

  const startEdit = (tag: TagItem) => {
    setEditingId(tag.id)
    setFormName(tag.name)
    setFormColor(tag.color || DEFAULT_COLORS[6])
    setShowForm(true)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
      <PageLayout title="Tags">
        
    <div className="px-3 lg:px-6 py-4 lg:py-6 max-w-6xl mx-auto space-y-4 lg:space-y-6 h-full overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-gradient-to-br from-amber-500/20 to-orange-500/20">
            <Tag className="w-6 h-6 text-amber-400" />
          </div>
          <div>
            <h1 className="text-xl lg:text-2xl font-bold">{t("tags.title") || "Tags"}</h1>
            <p className="text-xs lg:text-sm text-muted-foreground">
              {t("tags.subtitle") || "Organize your content with tags"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => fetchTags()}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-card border border-border text-xs lg:text-sm hover:bg-muted/50 transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
            {t("common.refresh") || "Refresh"}
          </button>
          <button
            onClick={() => { resetForm(); setShowForm(!showForm) }}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-amber-500 text-white text-xs lg:text-sm font-medium hover:bg-amber-600 transition-colors"
          >
            <Plus className="w-4 h-4" />
            {t("tags.addTag") || "Add Tag"}
          </button>
        </div>
      </div>

      {/* Create / Edit Form */}
      {showForm && (
        <div className="bg-card border border-border rounded-xl p-3 lg:p-4 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
              {editingId ? (
                <>
                  <Edit3 className="w-4 h-4 text-amber-400" />
                  {t("tags.editTag") || "Edit Tag"}
                </>
              ) : (
                <>
                  <Plus className="w-4 h-4 text-amber-400" />
                  {t("tags.createTag") || "Create Tag"}
                </>
              )}
            </h3>
            <button
              onClick={resetForm}
              className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="flex flex-col sm:flex-row gap-3">
            <div className="flex-1">
              <label className="text-xs text-muted-foreground mb-1 block">
                {t("tags.nameLabel") || "Tag name"}
              </label>
              <div className="relative">
                <Hash className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <input
                  type="text"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && (editingId ? handleUpdate() : handleCreate())}
                  placeholder={t("tags.namePlaceholder") || "Enter tag name..."}
                  className="w-full bg-muted/30 border border-border rounded-lg pl-9 pr-4 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-amber-500/30 focus:border-amber-500/50 transition-all"
                  autoFocus
                />
              </div>
            </div>

            <div>
              <label className="text-xs text-muted-foreground mb-1 block">
                {t("tags.colorLabel") || "Color"}
              </label>
              <div className="flex items-center gap-2">
                <div
                  className="w-8 h-8 rounded-lg border border-border shrink-0"
                  style={{ backgroundColor: formColor }}
                />
                <div className="flex flex-wrap gap-1.5">
                  {DEFAULT_COLORS.map(c => (
                    <button
                      key={c}
                      onClick={() => setFormColor(c)}
                      className={cn(
                        "w-5 h-5 rounded-md border transition-all",
                        formColor === c
                          ? "border-white scale-125 shadow-lg"
                          : "border-transparent hover:scale-110"
                      )}
                      style={{ backgroundColor: c }}
                    />
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div className="flex justify-end gap-2">
            <button
              onClick={resetForm}
              className="px-3 py-2 rounded-lg text-xs lg:text-sm text-muted-foreground hover:bg-muted/50 border border-border transition-colors"
            >
              {t("common.cancel") || "Cancel"}
            </button>
            <button
              onClick={editingId ? handleUpdate : handleCreate}
              disabled={submitting || !formName.trim()}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-amber-500 text-white text-xs lg:text-sm font-medium hover:bg-amber-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Check className="w-4 h-4" />
              )}
              {editingId
                ? (t("tags.updateBtn") || "Update")
                : (t("tags.createBtn") || "Create")}
            </button>
          </div>
        </div>
      )}

      {/* Tags Grid */}
      {tags.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground/50">
          <Tag className="w-12 h-12 mx-auto mb-2" />
          <p className="text-xs lg:text-sm">
            {t("tags.empty") || "No tags yet. Create your first tag to get started."}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
          {tags.map(tag => {
            const tagColor = tag.color || "#6366f1"
            const usageCount = tag.count ?? tag.usage_count
            return (
              <div
                key={tag.id}
                className="bg-card border border-border rounded-xl p-3 hover:border-border/80 transition-all group relative"
              >
                <div className="flex items-center gap-2.5 mb-2">
                  <div
                    className="w-3 h-3 rounded-full shrink-0 ring-2 ring-offset-1 ring-offset-card"
                    style={{
                      backgroundColor: tagColor,
                      boxShadow: `0 0 0 2px var(--bg-card, #1a1a2e), 0 0 0 4px ${tagColor}40`,
                    }}
                  />
                  <span className="text-xs lg:text-sm font-medium truncate">{tag.name}</span>
                </div>

                {usageCount != null && (
                  <div className="text-xs text-muted-foreground mb-2 flex items-center gap-1">
                    <Palette className="w-3 h-3" />
                    {usageCount} {t("tags.uses") || "uses"}
                  </div>
                )}

                {tag.created_at && (
                  <div className="text-xs text-muted-foreground/50 mb-2">
                    {new Date(tag.created_at).toLocaleDateString()}
                  </div>
                )}

                {/* Action buttons */}
                <div className="flex gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => startEdit(tag)}
                    className="flex-1 flex items-center justify-center gap-1 p-1.5 rounded-lg bg-muted/30 text-muted-foreground hover:bg-amber-500/10 hover:text-amber-400 transition-colors"
                    title={t("tags.edit") || "Edit"}
                  >
                    <Edit3 className="w-3 h-3" />
                    <span className="text-[10px]">{t("tags.edit") || "Edit"}</span>
                  </button>
                  <button
                    onClick={() => handleDelete(tag.id)}
                    disabled={deletingId === tag.id}
                    className="flex-1 flex items-center justify-center gap-1 p-1.5 rounded-lg bg-muted/30 text-muted-foreground hover:bg-red-500/10 hover:text-red-400 transition-colors disabled:opacity-50"
                    title={t("common.delete") || "Delete"}
                  >
                    {deletingId === tag.id ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <Trash2 className="w-3 h-3" />
                    )}
                    <span className="text-[10px]">{t("common.delete") || "Delete"}</span>
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  
      </PageLayout>
    )
}
