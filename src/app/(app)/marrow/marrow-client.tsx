"use client"

import { useEffect, useState, useCallback } from "react"
import { getApiBaseUrl } from "@/lib/api-client"
import { cn } from "@/lib/utils"
import {
  Bone, Plus, Trash2, RefreshCw, Download, Upload, HardDrive,
  CheckCircle, XCircle, Loader2, Archive, RotateCcw, FileText,
  Activity, ArrowUpDown, Clock, Play, Pause, CalendarClock,
} from "lucide-react"
import { useTranslation } from "react-i18next"
import { PageLayout } from '@/components/page-layout';

interface MarrowHealth {
  status: string
  component: string
  backup: { total_backups: number; total_size_bytes: number; backup_dir: string }
  scheduler: { active_schedules: number; total_schedules: number; running: boolean }
  export_dir: string
}

interface Backup {
  backup_id: string
  name: string
  description: string
  created_at: string
  size_bytes: number
  file_count: number
  checksum: string
  tags: string[]
}

interface ExportJob {
  job_id: string
  format: string
  record_count: number
  size_bytes: number
  file_path: string
  created_at: string
}

interface ScheduledBackup {
  schedule_id: string
  name: string
  source_dirs: string[]
  cron_expr: string
  interval_seconds: number
  description: string
  tags: string[]
  enabled: boolean
  created_at: number
  last_run_at: number
  next_run_at: number
  run_count: number
  last_backup_id: string
}

type ActiveTab = "overview" | "backups" | "schedules" | "migration"

export function MarrowClient() {
  const apiBase = getApiBaseUrl()
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<ActiveTab>("overview")
  const [health, setHealth] = useState<MarrowHealth | null>(null)
  const [backups, setBackups] = useState<Backup[]>([])
  const [exports, setExports] = useState<ExportJob[]>([])
  const [schedules, setSchedules] = useState<ScheduledBackup[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [restoring, setRestoring] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null)

  // Create backup form
  const [showCreateBackup, setShowCreateBackup] = useState(false)
  const [backupForm, setBackupForm] = useState({ name: "", description: "", sourceDirs: "~/.opensoul/data", tags: "" })

  // Create schedule form
  const [showCreateSchedule, setShowCreateSchedule] = useState(false)
  const [scheduleForm, setScheduleForm] = useState({ name: "", sourceDirs: "~/.opensoul/data", interval: "daily", description: "", tags: "" })

  // Import
  const [importing, setImporting] = useState(false)
  const [importFormat, setImportFormat] = useState("json")

  const refresh = useCallback(async () => {
    try {
      const [h, b, e, s] = await Promise.all([
        fetch(`${apiBase}/api/marrow/health`).then(r => r.json()),
        fetch(`${apiBase}/api/marrow/backups`).then(r => r.json()).catch(() => ({ backups: [] })),
        fetch(`${apiBase}/api/marrow/exports`).then(r => r.json()).catch(() => ({ exports: [] })),
        fetch(`${apiBase}/api/marrow/schedules`).then(r => r.json()).catch(() => ({ schedules: [] })),
      ])
      setHealth(h)
      setBackups(b.backups || [])
      setExports(e.exports || [])
      setSchedules(s.schedules || [])
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [apiBase])

  useEffect(() => { refresh() }, [refresh])

  const showMsg = (type: "ok" | "err", text: string) => {
    setMessage({ type, text })
    setTimeout(() => setMessage(null), 4000)
  }

  const handleCreateBackup = async () => {
    setCreating(true)
    try {
      const sourceDirs = backupForm.sourceDirs.split(",").map(s => s.trim()).filter(Boolean)
      const res = await fetch(`${apiBase}/api/marrow/backup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_dirs: sourceDirs,
          name: backupForm.name || undefined,
          description: backupForm.description || undefined,
          tags: backupForm.tags ? backupForm.tags.split(",").map(s => s.trim()) : [],
        }),
      })
      if (res.ok) {
        const data = await res.json()
        showMsg("ok", `${t("marrow.backupCreated") || "Backup created"}: ${data.backup_id} (${data.file_count} ${t("marrow.files") || "files"})`)
        setShowCreateBackup(false)
        setBackupForm({ name: "", description: "", sourceDirs: "~/.opensoul/data", tags: "" })
        refresh()
      } else {
        const err = await res.json()
        showMsg("err", err.detail || (t("marrow.backupFailed") || "Backup failed"))
      }
    } catch (e: any) {
      showMsg("err", e.message || (t("common.networkError") || "Network error"))
    }
    setCreating(false)
  }

  const handleRestore = async (backupId: string) => {
    if (!confirm(`${t("marrow.confirmRestoreBackup") || "Confirm restore backup"} "${backupId}"? ${t("marrow.willOverwrite") || "This will overwrite current data."}`)) return
    setRestoring(backupId)
    try {
      const res = await fetch(`${apiBase}/api/marrow/restore/${backupId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ backup_id: backupId, target_dir: "~/.opensoul/restore" }),
      })
      if (res.ok) {
        showMsg("ok", t("marrow.restoreSuccess") || "Restore successful")
      } else {
        const err = await res.json()
        showMsg("err", err.detail || (t("marrow.restoreFailed") || "Restore failed"))
      }
    } catch (e: any) {
      showMsg("err", e.message || (t("common.networkError") || "Network error"))
    }
    setRestoring(null)
  }

  const handleDeleteBackup = async (backupId: string) => {
    if (!confirm(`${t("marrow.confirmDeleteBackup") || "Confirm delete backup"} "${backupId}"?`)) return
    setDeleting(backupId)
    try {
      await fetch(`${apiBase}/api/marrow/backups/${backupId}`, { method: "DELETE" })
      showMsg("ok", t("marrow.backupDeleted") || "Backup deleted")
      refresh()
    } catch (e: any) {
      showMsg("err", e.message || (t("common.deleteFailed") || "Delete failed"))
    }
    setDeleting(null)
  }

  // ── Schedule handlers ──
  const handleCreateSchedule = async () => {
    setCreating(true)
    try {
      const sourceDirs = scheduleForm.sourceDirs.split(",").map(s => s.trim()).filter(Boolean)
      const res = await fetch(`${apiBase}/api/marrow/schedules`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: scheduleForm.name,
          source_dirs: sourceDirs,
          interval: scheduleForm.interval,
          description: scheduleForm.description || undefined,
          tags: scheduleForm.tags ? scheduleForm.tags.split(",").map(s => s.trim()) : [],
        }),
      })
      if (res.ok) {
        const data = await res.json()
        showMsg("ok", `${t("marrow.scheduleCreated") || "Scheduled backup created"}: ${data.name}`)
        setShowCreateSchedule(false)
        setScheduleForm({ name: "", sourceDirs: "~/.opensoul/data", interval: "daily", description: "", tags: "" })
        refresh()
      } else {
        const err = await res.json()
        showMsg("err", err.detail || (t("common.createFailed") || "Create failed"))
      }
    } catch (e: any) {
      showMsg("err", e.message || (t("common.networkError") || "Network error"))
    }
    setCreating(false)
  }

  const handleToggleSchedule = async (scheduleId: string, enabled: boolean) => {
    try {
      await fetch(`${apiBase}/api/marrow/schedules/${scheduleId}/toggle`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      })
      showMsg("ok", enabled ? (t("common.enabled") || "Enabled") : (t("common.paused") || "Paused"))
      refresh()
    } catch (e: any) {
      showMsg("err", e.message || (t("common.actionFailed") || "Action failed"))
    }
  }

  const handleDeleteSchedule = async (scheduleId: string) => {
    if (!confirm(t("marrow.confirmDeleteSchedule") || "Delete this scheduled backup?")) return
    try {
      await fetch(`${apiBase}/api/marrow/schedules/${scheduleId}`, { method: "DELETE" })
      showMsg("ok", t("marrow.scheduleDeleted") || "Scheduled backup deleted")
      refresh()
    } catch (e: any) {
      showMsg("err", e.message || (t("common.deleteFailed") || "Delete failed"))
    }
  }

  const handleRunDue = async () => {
    try {
      const res = await fetch(`${apiBase}/api/marrow/schedules/run-due`, { method: "POST" })
      const data = await res.json()
      showMsg("ok", `${t("marrow.runComplete") || "Execution complete"}: ${data.count} ${t("marrow.tasks") || "tasks"}`)
      refresh()
    } catch (e: any) {
      showMsg("err", e.message || (t("common.executeFailed") || "Execution failed"))
    }
  }

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setImporting(true)
    try {
      const formData = new FormData()
      formData.append("file", file)
      formData.append("format", importFormat)
      const res = await fetch(`${apiBase}/api/marrow/import`, { method: "POST", body: formData })
      if (res.ok) {
        const data = await res.json()
        showMsg("ok", `${t("marrow.importSuccess") || "Import successful"}: ${data.records} ${t("marrow.records") || "records"}`)
      } else {
        const err = await res.json()
        showMsg("err", err.detail || (t("marrow.importFailed") || "Import failed"))
      }
    } catch (ex: any) {
      showMsg("err", ex.message || (t("marrow.importFailed") || "Import failed"))
    }
    setImporting(false)
    e.target.value = ""
  }

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  const formatInterval = (expr: string, secs: number) => {
    const map: Record<string, string> = {
      hourly: t("marrow.hourly") || "Hourly",
      daily: t("marrow.daily") || "Daily",
      weekly: t("marrow.weekly") || "Weekly",
    }
    return map[expr] || `${t("marrow.every") || "Every"} ${secs} ${t("marrow.seconds") || "seconds"}`
  }

  const formatNextRun = (ts: number) => {
    if (!ts) return "—"
    const d = new Date(ts * 1000)
    const now = Date.now()
    const diff = ts * 1000 - now
    if (diff <= 0) return t("marrow.runSoon") || "Running soon"
    if (diff < 3600000) return `${Math.round(diff / 60000)} ${t("marrow.minutesLater") || "minutes later"}`
    if (diff < 86400000) return `${Math.round(diff / 3600000)} ${t("marrow.hoursLater") || "hours later"}`
    return d.toLocaleString('zh-CN', { fractionalSecondDigits: 3 })
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const tabs: Array<{ key: ActiveTab; label: string; icon: typeof Bone }> = [
    { key: "overview", label: t("marrow.tabOverview") || "Overview", icon: Activity },
    { key: "backups", label: t("marrow.tabBackups") || "Backup Management", icon: Archive },
    { key: "schedules", label: t("marrow.tabSchedules") || "Scheduled Backups", icon: CalendarClock },
    { key: "migration", label: t("marrow.tabMigration") || "Migration", icon: ArrowUpDown },
  ]

  return (
      <PageLayout title="骨髓">
        
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="shrink-0 px-3 lg:px-6 pt-6 pb-4">
        <div className="flex items-center gap-2 lg:gap-3 mb-1">
          <div className="p-2 rounded-lg bg-gradient-to-br from-amber-500/20 to-orange-500/20">
            <Bone className="w-5 h-5 text-amber-400" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-foreground">{t("marrow.title") || "Marrow — Backup System"}</h1>
            <p className="text-xs lg:text-sm text-muted-foreground">{t("marrow.subtitle") || "Backup & restore, scheduled backups, data migration, disaster recovery"}</p>
          </div>
        </div>

        {/* Tab Navigation */}
        <div className="flex gap-1 sm:gap-2 border-b border-border pb-2 mt-4 overflow-x-auto scrollbar-none">
          {tabs.map(tab => {
            const Icon = tab.icon
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={cn(
                  "flex items-center gap-1.5 px-2 sm:px-3 py-1.5 text-xs sm:text-xs lg:text-sm rounded-t-lg transition-colors whitespace-nowrap",
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

      {/* Message Toast */}
      {message && (
        <div className={cn(
          "mx-6 mb-2 flex items-center gap-2 p-3 rounded-lg border text-xs lg:text-sm",
          message.type === "ok"
            ? "border-green-500/30 bg-green-500/10 text-green-400"
            : "border-red-500/30 bg-red-500/10 text-red-400"
        )}>
          {message.type === "ok" ? <CheckCircle className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
          {message.text}
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-3 lg:px-6 pb-6">

        {/* ── Overview Tab ── */}
        {activeTab === "overview" && (
          <div className="space-y-3 lg:space-y-6">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 lg:gap-4">
              {[
                { label: t("marrow.totalBackups") || "Total Backups", value: health?.backup.total_backups ?? 0, icon: Archive, color: "text-blue-400", bg: "from-blue-500/20 to-blue-600/10" },
                { label: t("marrow.totalSize") || "Total Size", value: formatSize(health?.backup.total_size_bytes ?? 0), icon: HardDrive, color: "text-amber-400", bg: "from-amber-500/20 to-amber-600/10" },
                { label: t("marrow.scheduledTasks") || "Scheduled Tasks", value: health?.scheduler.active_schedules ?? 0, icon: CalendarClock, color: "text-cyan-400", bg: "from-cyan-500/20 to-cyan-600/10" },
                { label: t("marrow.status") || "Status", value: health?.status === "ok" ? (t("common.normal") || "Normal") : (t("common.abnormal") || "Abnormal"), icon: CheckCircle, color: health?.status === "ok" ? "text-green-400" : "text-red-400", bg: health?.status === "ok" ? "from-green-500/20 to-green-600/10" : "from-red-500/20 to-red-600/10" },
              ].map(s => (
                <div key={s.label} className={cn("rounded-xl border border-border bg-gradient-to-br p-3 lg:p-4", s.bg)}>
                  <s.icon className={cn("w-4 h-4 mb-2", s.color)} />
                  <div className={cn("text-xl lg:text-2xl font-bold", s.color)}>{s.value}</div>
                  <div className="text-xs text-muted-foreground mt-1">{s.label}</div>
                </div>
              ))}
            </div>

            {/* Paths */}
            <div className="p-4 rounded-xl border border-border bg-card space-y-2">
              <h3 className="text-xs lg:text-sm font-semibold text-foreground mb-2">{t("marrow.storagePaths") || "Storage Paths"}</h3>
              <div className="flex items-center gap-2 text-xs lg:text-sm">
                <span className="text-muted-foreground">{t("marrow.backupDir") || "Backup Directory"}:</span>
                <code className="px-2 py-0.5 rounded bg-muted text-xs font-mono">{health?.backup.backup_dir || "~/.opensoul/backups"}</code>
              </div>
              <div className="flex items-center gap-2 text-xs lg:text-sm">
                <span className="text-muted-foreground">{t("marrow.exportDir") || "Export directory"}:</span>
                <code className="px-2 py-0.5 rounded bg-muted text-xs font-mono">{health?.export_dir || "~/.opensoul/exports"}</code>
              </div>
              <div className="flex items-center gap-2 text-xs lg:text-sm">
                <span className="text-muted-foreground">{t("marrow.scheduler") || "Scheduler"}:</span>
                <span className={cn("text-xs font-medium", health?.scheduler.running ? "text-green-400" : "text-red-400")}>
                  {health?.scheduler.running ? (t("common.running") || "Running") : (t("common.stopped") || "Stopped")}
                </span>
              </div>
            </div>

            {/* Quick Actions */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 lg:gap-4">
              <button
                onClick={() => { setActiveTab("backups"); setShowCreateBackup(true) }}
                className="p-4 rounded-xl border border-border bg-card hover:border-primary/40 transition-all text-left"
              >
                <Plus className="w-5 h-5 text-primary mb-2" />
                <div className="text-xs lg:text-sm font-semibold text-foreground">{t("marrow.createBackup") || "Create Backup"}</div>
                <div className="text-xs text-muted-foreground">{t("marrow.snapshotCurrent") || "Snapshot current system data"}</div>
              </button>
              <button
                onClick={() => { setActiveTab("schedules"); setShowCreateSchedule(true) }}
                className="p-4 rounded-xl border border-border bg-card hover:border-primary/40 transition-all text-left"
              >
                <CalendarClock className="w-5 h-5 text-cyan-400 mb-2" />
                <div className="text-xs lg:text-sm font-semibold text-foreground">{t("marrow.scheduledBackup") || "Scheduled Backups"}</div>
                <div className="text-xs text-muted-foreground">{t("marrow.setAutoBackup") || "Set up automatic backup schedule"}</div>
              </button>
              <button
                onClick={() => setActiveTab("migration")}
                className="p-4 rounded-xl border border-border bg-card hover:border-primary/40 transition-all text-left"
              >
                <ArrowUpDown className="w-5 h-5 text-purple-400 mb-2" />
                <div className="text-xs lg:text-sm font-semibold text-foreground">{t("marrow.dataMigration") || "Data Migration"}</div>
                <div className="text-xs text-muted-foreground">{t("marrow.importExport") || "Import/Export data"}</div>
              </button>
            </div>
          </div>
        )}

        {/* ── Backups Tab ── */}
        {activeTab === "backups" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xs lg:text-sm font-semibold text-foreground">{t("marrow.backups") || "Backup List"}</h3>
              <div className="flex gap-2">
                <button onClick={refresh} className="p-1.5 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground">
                  <RefreshCw className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setShowCreateBackup(!showCreateBackup)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs lg:text-sm hover:opacity-90"
                >
                  <Plus className="w-3.5 h-3.5" /> {t("marrow.createBackup") || "Create Backup"}
                </button>
              </div>
            </div>

            {/* Create Backup Form */}
            {showCreateBackup && (
              <div className="p-4 rounded-xl border border-border bg-card space-y-3">
                <div className="grid grid-cols-2 gap-2 lg:gap-3">
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">{t("marrow.backupName") || "Backup Name"}</label>
                    <input value={backupForm.name} onChange={e => setBackupForm({ ...backupForm, name: e.target.value })}
                      placeholder={t("marrow.backupNamePlaceholder")} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">{t("marrow.tags") || "Tags"} ({t("common.commaSeparated") || "Comma separated"})</label>
                    <input value={backupForm.tags} onChange={e => setBackupForm({ ...backupForm, tags: e.target.value })}
                      placeholder={t("marrow.tagsPlaceholder")} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  </div>
                  <div className="col-span-2">
                    <label className="text-xs text-muted-foreground mb-1 block">{t("marrow.sourceDirs") || "Source Directories"} ({t("common.commaSeparated") || "Comma separated"})</label>
                    <input value={backupForm.sourceDirs} onChange={e => setBackupForm({ ...backupForm, sourceDirs: e.target.value })}
                      placeholder={t("marrow.sourceDirsPlaceholder")} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm font-mono" />
                  </div>
                  <div className="col-span-2">
                    <label className="text-xs text-muted-foreground mb-1 block">{t("marrow.description") || "Description"}</label>
                    <input value={backupForm.description} onChange={e => setBackupForm({ ...backupForm, description: e.target.value })}
                      placeholder={t("marrow.descriptionPlaceholder") || "Backup description (optional)"} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  </div>
                </div>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setShowCreateBackup(false)} className="px-3 py-1.5 rounded-lg border border-border text-xs lg:text-sm text-muted-foreground hover:text-foreground">{t("common.cancel") || "Cancel"}</button>
                  <button onClick={handleCreateBackup} disabled={creating}
                    className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs lg:text-sm font-medium hover:opacity-90 disabled:opacity-40">
                    {creating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Archive className="w-3.5 h-3.5" />}
                    {creating ? (t("common.creating") || "Creating...") : (t("common.create") || "Create")}
                  </button>
                </div>
              </div>
            )}

            {/* Backup List */}
            <div className="space-y-2">
              {backups.length === 0 && (
                <div className="text-center py-8 text-muted-foreground text-xs lg:text-sm">{t("marrow.noBackups") || "No backups yet"}</div>
              )}
              {backups.map(b => (
                <div key={b.backup_id} className="p-4 rounded-xl border border-border bg-card">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2 lg:gap-3">
                      <Archive className="w-5 h-5 text-amber-400" />
                      <div>
                        <div className="text-xs lg:text-sm font-semibold text-foreground">{b.name || b.backup_id}</div>
                        <div className="text-xs text-muted-foreground">
                          {new Date(b.created_at).toLocaleString('zh-CN', { fractionalSecondDigits: 3 })} · {b.file_count} {t("marrow.files") || "files"} · {formatSize(b.size_bytes)}
                        </div>
                        {b.description && <div className="text-xs text-muted-foreground mt-1">{b.description}</div>}
                        {b.tags && b.tags.length > 0 && (
                          <div className="flex gap-1 mt-1">
                            {b.tags.map(tag => (
                              <span key={tag} className="px-1.5 py-0.5 rounded bg-muted text-[10px] text-muted-foreground">{tag}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <button onClick={() => handleRestore(b.backup_id)} disabled={restoring === b.backup_id}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs bg-green-500/10 text-green-400 hover:bg-green-500/20 transition-colors disabled:opacity-40"
                        title={t("marrow.restore") || "Restore"}>
                        {restoring === b.backup_id ? <Loader2 className="w-3 h-3 animate-spin" /> : <RotateCcw className="w-3 h-3" />}
                        {t("marrow.restore") || "Restore"}
                      </button>
                      <button onClick={() => handleDeleteBackup(b.backup_id)} disabled={deleting === b.backup_id}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors disabled:opacity-40"
                        title={t("common.delete") || "Delete"}>
                        {deleting === b.backup_id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                        {t("common.delete") || "Delete"}
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Schedules Tab ── */}
        {activeTab === "schedules" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xs lg:text-sm font-semibold text-foreground">{t("marrow.scheduledBackups") || "Scheduled Backups"}</h3>
              <div className="flex gap-2">
                <button onClick={handleRunDue}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-500/10 text-amber-400 text-xs lg:text-sm hover:bg-amber-500/20">
                  <Play className="w-3.5 h-3.5" /> {t("marrow.runDue") || "Run Due Tasks"}
                </button>
                <button
                  onClick={() => setShowCreateSchedule(!showCreateSchedule)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs lg:text-sm hover:opacity-90"
                >
                  <Plus className="w-3.5 h-3.5" /> {t("marrow.createSchedule") || "Create Scheduled Backup"}
                </button>
              </div>
            </div>

            {/* Create Schedule Form */}
            {showCreateSchedule && (
              <div className="p-4 rounded-xl border border-border bg-card space-y-3">
                <div className="grid grid-cols-2 gap-2 lg:gap-3">
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">{t("marrow.scheduleName") || "Task Name"}</label>
                    <input value={scheduleForm.name} onChange={e => setScheduleForm({ ...scheduleForm, name: e.target.value })}
                      placeholder={t("marrow.dailyBackup") || "Daily data backup"} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">{t("marrow.interval") || "Interval"}</label>
                    <select value={scheduleForm.interval} onChange={e => setScheduleForm({ ...scheduleForm, interval: e.target.value })}
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm">
                      <option value="hourly">{t("marrow.hourly") || "Hourly"}</option>
                      <option value="daily">{t("marrow.daily") || "Daily"}</option>
                      <option value="weekly">{t("marrow.weekly") || "Weekly"}</option>
                    </select>
                  </div>
                  <div className="col-span-2">
                    <label className="text-xs text-muted-foreground mb-1 block">{t("marrow.sourceDirs") || "Source Directories"}</label>
                    <input value={scheduleForm.sourceDirs} onChange={e => setScheduleForm({ ...scheduleForm, sourceDirs: e.target.value })}
                      placeholder={t("marrow.sourceDirsPlaceholder")} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm font-mono" />
                  </div>
                  <div className="col-span-2">
                    <label className="text-xs text-muted-foreground mb-1 block">{t("marrow.tags") || "Tags"}</label>
                    <input value={scheduleForm.tags} onChange={e => setScheduleForm({ ...scheduleForm, tags: e.target.value })}
                      placeholder={t("marrow.scheduleTagsPlaceholder")} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  </div>
                </div>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setShowCreateSchedule(false)} className="px-3 py-1.5 rounded-lg border border-border text-xs lg:text-sm text-muted-foreground hover:text-foreground">{t("common.cancel") || "Cancel"}</button>
                  <button onClick={handleCreateSchedule} disabled={creating || !scheduleForm.name}
                    className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs lg:text-sm font-medium hover:opacity-90 disabled:opacity-40">
                    {creating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CalendarClock className="w-3.5 h-3.5" />}
                    {creating ? (t("common.creating") || "Creating...") : (t("common.create") || "Create")}
                  </button>
                </div>
              </div>
            )}

            {/* Schedule List */}
            <div className="space-y-2">
              {schedules.length === 0 && (
                <div className="text-center py-8 text-muted-foreground text-xs lg:text-sm">{t("marrow.noSchedules") || "No scheduled backups"}</div>
              )}
              {schedules.map(s => (
                <div key={s.schedule_id} className={cn("p-4 rounded-xl border bg-card", s.enabled ? "border-border" : "border-border/50 opacity-60")}>
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2 lg:gap-3">
                      <CalendarClock className={cn("w-5 h-5", s.enabled ? "text-cyan-400" : "text-muted-foreground")} />
                      <div>
                        <div className="text-xs lg:text-sm font-semibold text-foreground">{s.name}</div>
                        <div className="text-xs text-muted-foreground">
                          {formatInterval(s.cron_expr, s.interval_seconds)} · {t("marrow.runCount") || "Run count"}: {s.run_count} · {t("marrow.nextRun") || "Next run"}: {formatNextRun(s.next_run_at)}
                        </div>
                        {s.source_dirs && (
                          <div className="text-xs text-muted-foreground mt-1 font-mono">{s.source_dirs.join(", ")}</div>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <button onClick={() => handleToggleSchedule(s.schedule_id, !s.enabled)}
                        className={cn("flex items-center gap-1 px-2.5 py-1 rounded-md text-xs transition-colors",
                          s.enabled ? "bg-amber-500/10 text-amber-400 hover:bg-amber-500/20" : "bg-green-500/10 text-green-400 hover:bg-green-500/20")}>
                        {s.enabled ? <><Pause className="w-3 h-3" /> {t("common.pause") || "Pause"}</> : <><Play className="w-3 h-3" /> {t("common.enable") || "Enable"}</>}
                      </button>
                      <button onClick={() => handleDeleteSchedule(s.schedule_id)}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors">
                        <Trash2 className="w-3 h-3" /> {t("common.delete") || "Delete"}
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Migration Tab ── */}
        {activeTab === "migration" && (
          <div className="space-y-3 lg:space-y-6">
            {/* Export */}
            <div className="p-4 rounded-xl border border-border bg-card">
              <h3 className="text-xs lg:text-sm font-semibold text-foreground mb-3">{t("marrow.exportData") || "Export Data"}</h3>
              <p className="text-xs text-muted-foreground mb-3">{t("marrow.exportDesc") || "Export knowledge base data in standard format"}</p>
              <button
                onClick={async () => {
                  try {
                    const res = await fetch(`${apiBase}/api/marrow/export`, {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ format: "json" }),
                    })
                    if (res.ok) {
                      const data = await res.json()
                      showMsg("ok", `${t("marrow.exportSuccess") || "Export complete"}: ${data.records} ${t("marrow.records") || "records"}`)
                      refresh()
                    }
                  } catch { showMsg("err", t("marrow.exportFailed") || "Export failed") }
                }}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-purple-500/10 text-purple-400 text-xs lg:text-sm hover:bg-purple-500/20"
              >
                <Download className="w-4 h-4" /> {t("marrow.startExport") || "Start Export"}
              </button>
            </div>

            {/* Import */}
            <div className="p-4 rounded-xl border border-border bg-card">
              <h3 className="text-xs lg:text-sm font-semibold text-foreground mb-3">{t("marrow.importData") || "Import Data"}</h3>
              <p className="text-xs text-muted-foreground mb-3">{t("marrow.importDesc") || "Import data from backup files or standard formats"}</p>
              <div className="flex items-center gap-2 lg:gap-3">
                <select value={importFormat} onChange={e => setImportFormat(e.target.value)}
                  className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm">
                  <option value="json">JSON</option>
                  <option value="csv">CSV</option>
                  <option value="markdown">Markdown</option>
                </select>
                <label className="flex items-center gap-2 px-4 py-2 rounded-lg bg-green-500/10 text-green-400 text-xs lg:text-sm hover:bg-green-500/20 cursor-pointer">
                  <Upload className="w-4 h-4" />
                  {importing ? (t("common.importing") || "Importing...") : (t("marrow.selectFile") || "Select file")}
                  <input type="file" className="hidden" onChange={handleImport} accept=".json,.csv,.md,.txt" disabled={importing} />
                </label>
              </div>
            </div>

            {/* Export History */}
            <div>
              <h3 className="text-xs lg:text-sm font-semibold text-foreground mb-3">{t("marrow.exportHistory") || "Export History"}</h3>
              {exports.length === 0 ? (
                <div className="text-center py-3 lg:py-6 text-muted-foreground text-xs lg:text-sm">{t("marrow.noExports") || "No exports yet"}</div>
              ) : (
                <div className="space-y-2">
                  {exports.map(exp => (
                    <div key={exp.job_id} className="p-3 rounded-lg border border-border bg-card flex items-center justify-between">
                      <div className="flex items-center gap-2 lg:gap-3">
                        <FileText className="w-4 h-4 text-purple-400" />
                        <div>
                          <div className="text-xs lg:text-sm text-foreground">{exp.format.toUpperCase()} · {exp.record_count} {t("marrow.records") || "records"} · {formatSize(exp.size_bytes)}</div>
                          <div className="text-xs text-muted-foreground">{new Date(exp.created_at).toLocaleString('zh-CN', { fractionalSecondDigits: 3 })}</div>
                        </div>
                      </div>
                      <a href={`${apiBase}/api/marrow/exports/${exp.job_id}/download`}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs bg-blue-500/10 text-blue-400 hover:bg-blue-500/20">
                        <Download className="w-3 h-3" /> {t("common.download") || "Download"}
                      </a>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    )
}
