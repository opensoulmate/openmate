"use client"

import { useEffect, useState, useCallback, useRef, useMemo } from "react"
import { getApiBaseUrl } from "@/lib/api-client"
import { useTranslation } from "react-i18next"
import {
  Activity, Search, GitBranch, Play, Pause, SkipForward, SkipBack,
  RefreshCw, Clock, Zap, Hash, Filter, ChevronRight, ChevronDown,
  BarChart3, Layers, AlertCircle, CheckCircle, XCircle, Eye,
  Copy, Terminal, MessageSquare, Bot, Wrench, ArrowLeft, Gauge,
} from "lucide-react"
import { PageLayout } from '@/components/page-layout'

// ── Types ──────────────────────────────────────────────────────

interface EvolutionStep {
  stage: string
  ts: string
  msg: string
  details: Record<string, string>
}

interface EvolutionTask {
  taskId: string
  strand: string
  cycle: number
  startTime: string
  endTime: string | null
  status: "running" | "success" | "error"
  steps: EvolutionStep[]
  summary: string
}

interface TimelineData {
  tasks: EvolutionTask[]
  goals: any[]
  totalTasks: number
  successCount: number
  errorCount: number
}

// ── Constants ──────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  running: "#3b82f6",
  success: "#22c55e",
  error: "#ef4444",
  pending: "#94a3b8",
}

const STAGE_COLORS: Record<string, string> = {
  turn: "#5b8cff",
  observe: "#06b6d4",
  plan: "#a855f7",
  plan_review: "#c084fc",
  implement: "#f59e0b",
  execute: "#f97316",
  evaluate: "#eab308",
  reflect: "#34d399",
  verdict: "#22c55e",
  error: "#ef4444",
  start: "#6b7280",
  submit: "#10b981",
  verify: "#8b5cf6",
  code_review: "#ec4899",
  locate: "#14b8a6",
  turn_debug: "#f43f5e",
  receive: "#0ea5e9",
  plan_pipeline: "#7c3aed",
  execute_partner: "#d946ef",
  execute_partners: "#e879f9",
}

const STAGE_LABELS: Record<string, string> = {
  turn: "开始进化",
  observe: "观察",
  plan: "规划",
  plan_review: "方案审查",
  implement: "实现",
  execute: "执行",
  evaluate: "评估",
  reflect: "反思",
  verdict: "判定",
  error: "错误",
  start: "启动",
  submit: "提交",
  verify: "验证",
  code_review: "代码审查",
  locate: "定位",
  turn_debug: "调试",
  receive: "接收",
  plan_pipeline: "流水线规划",
  execute_partner: "伙伴执行",
  execute_partners: "伙伴们执行",
}

// ── Helpers ────────────────────────────────────────────────────

function pad(n: number) { return String(n).padStart(2, "0") }

function formatDay(ts: string) {
  const d = new Date(ts)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function formatTime(ts: string) {
  const d = new Date(ts)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatDuration(start: string, end: string | null) {
  if (!end) return "进行中…"
  const ms = new Date(end).getTime() - new Date(start).getTime()
  if (ms < 60000) return `${Math.round(ms / 1000)}秒`
  if (ms < 3600000) return `${Math.round(ms / 60000)}分钟`
  return `${(ms / 3600000).toFixed(1)}小时`
}

function escapeHTML(s: string) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")
}

function dayKey(ts: string) { return formatDay(ts) }

// ── Gantt Chart (ECharts) ─────────────────────────────────────

function GanttChart({
  tasks,
  selectedId,
  onSelect,
}: {
  tasks: EvolutionTask[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const chartRef = useRef<HTMLDivElement>(null)
  const instanceRef = useRef<any>(null)

  useEffect(() => {
    if (!chartRef.current) return
    const echarts = (window as any).echarts
    if (!echarts) return

    if (instanceRef.current) instanceRef.current.dispose()
    const chart = echarts.init(chartRef.current, null, { renderer: "canvas" })
    instanceRef.current = chart

    const categories = tasks.map(t => `${t.strand} #${t.cycle}`)
    const now = Date.now()

    const barData = tasks.map((t, i) => {
      const start = new Date(t.startTime).getTime()
      const end = t.endTime ? new Date(t.endTime).getTime() : now
      return {
        name: t.taskId,
        value: [i, start, end],
        itemStyle: {
          color: STATUS_COLORS[t.status] || "#6b7280",
          borderRadius: 4,
        },
        task: t,
      }
    })

    // 子步骤散点
    const scatterData: any[] = []
    tasks.forEach((t, i) => {
      t.steps.forEach(s => {
        if (["plan", "implement", "evaluate", "reflect", "verdict", "error"].includes(s.stage)) {
          scatterData.push({
            value: [i, new Date(s.ts).getTime()],
            itemStyle: { color: STAGE_COLORS[s.stage] || "#6b7280" },
            step: s,
            task: t,
          })
        }
      })
    })

    chart.setOption({
      backgroundColor: "transparent",
      animation: true,
      animationDuration: 500,
      tooltip: {
        trigger: "item",
        backgroundColor: "#1a1f2e",
        borderColor: "rgba(148,163,184,0.2)",
        textStyle: { color: "#e8eef7", fontSize: 12 },
        formatter: (params: any) => {
          if (params.seriesIndex === 0) {
            const t = params.data.task as EvolutionTask
            const dur = formatDuration(t.startTime, t.endTime)
            return `<div style="max-width:300px">
              <div style="font-weight:600;margin-bottom:4px">${t.strand} 周期 #${t.cycle}</div>
              <div style="color:#93a0b4;font-size:11px">${t.summary || "无摘要"}</div>
              <div style="margin-top:6px;font-size:11px">
                <span style="color:${STATUS_COLORS[t.status]}">${t.status}</span> · ${dur} · ${t.steps.length} 步骤
              </div>
            </div>`
          }
          const s = params.data.step as EvolutionStep
          return `<div style="max-width:280px">
            <div style="font-weight:600;color:${STAGE_COLORS[s.stage] || "#93a0b4"}">${STAGE_LABELS[s.stage] || s.stage}</div>
            <div style="color:#93a0b4;font-size:11px;margin-top:2px">${escapeHTML(s.msg)}</div>
            <div style="color:#6b7a90;font-size:10px;margin-top:4px">${formatTime(s.ts)}</div>
          </div>`
        },
      },
      dataZoom: [
        { type: "inside", xAxisIndex: 0, filterMode: "none" },
        { type: "inside", yAxisIndex: 0, filterMode: "none" },
      ],
      grid: { left: 100, right: 24, top: 16, bottom: 24 },
      xAxis: {
        type: "time",
        axisLine: { lineStyle: { color: "rgba(148,163,184,0.2)" } },
        axisTick: { show: false },
        axisLabel: { color: "#6b7a90", fontSize: 10 },
        splitLine: { show: true, lineStyle: { color: "rgba(148,163,184,0.06)" } },
      },
      yAxis: {
        type: "category",
        data: categories,
        inverse: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: "#93a0b4", fontSize: 11 },
      },
      series: [
        {
          name: "进化任务",
          type: "bar",
          barWidth: 20,
          encode: { x: [1, 2], y: 0 },
          data: barData,
          emphasis: { itemStyle: { shadowBlur: 10, shadowColor: "rgba(91,140,255,0.3)" } },
        },
        {
          name: "进化节点",
          type: "scatter",
          symbolSize: 8,
          data: scatterData,
        },
      ],
    })

    chart.on("click", (params: any) => {
      if (params.seriesIndex === 0 && params.data?.task) {
        onSelect(params.data.task.taskId)
      }
    })

    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(chartRef.current)
    return () => { ro.disconnect(); chart.dispose() }
  }, [tasks, onSelect])

  return <div ref={chartRef} style={{ width: "100%", height: Math.max(200, tasks.length * 36 + 60) }} />
}

// ── Overview Chart (ECharts) ───────────────────────────────────

function OverviewChart({ tasks }: { tasks: EvolutionTask[] }) {
  const chartRef = useRef<HTMLDivElement>(null)
  const instanceRef = useRef<any>(null)

  useEffect(() => {
    if (!chartRef.current) return
    const echarts = (window as any).echarts
    if (!echarts) return

    if (instanceRef.current) instanceRef.current.dispose()
    const chart = echarts.init(chartRef.current, null, { renderer: "canvas" })
    instanceRef.current = chart

    // 按日聚合
    const dayMap = new Map<string, { success: number; error: number; running: number }>()
    tasks.forEach(t => {
      const dk = dayKey(t.startTime)
      if (!dayMap.has(dk)) dayMap.set(dk, { success: 0, error: 0, running: 0 })
      const d = dayMap.get(dk)!
      d[t.status as keyof typeof d]++
    })
    const days = [...dayMap.keys()].sort()

    // 能力指数
    let level = 42
    const levelSeries = days.map(d => {
      const dayTasks = tasks.filter(t => dayKey(t.startTime) === d)
      const add = dayTasks.filter(t => t.status === "success").length * 2
      level = Math.min(100, level + add)
      return Number(level.toFixed(1))
    })

    chart.setOption({
      backgroundColor: "transparent",
      animationDuration: 700,
      tooltip: {
        trigger: "axis",
        backgroundColor: "#1a1f2e",
        borderColor: "rgba(148,163,184,0.2)",
        textStyle: { color: "#e8eef7", fontSize: 12 },
      },
      legend: { show: false },
      grid: { left: 40, right: 40, top: 24, bottom: 24 },
      xAxis: {
        type: "category",
        data: days.map(d => d.slice(5)),
        axisLine: { lineStyle: { color: "rgba(148,163,184,0.2)" } },
        axisTick: { show: false },
        axisLabel: { color: "#6b7a90", fontSize: 11 },
      },
      yAxis: [
        {
          type: "value",
          name: "任务",
          nameTextStyle: { color: "#6b7a90", fontSize: 10 },
          splitLine: { lineStyle: { color: "rgba(148,163,184,0.08)" } },
          axisLabel: { color: "#6b7a90", fontSize: 10 },
        },
        {
          type: "value",
          name: "能力",
          min: 0,
          max: 100,
          nameTextStyle: { color: "#6b7a90", fontSize: 10 },
          splitLine: { show: false },
          axisLabel: { color: "#6b7a90", fontSize: 10 },
        },
      ],
      series: [
        {
          name: "成功",
          type: "bar",
          stack: "total",
          barWidth: 16,
          itemStyle: { color: "#22c55e", borderRadius: [0, 0, 0, 0] },
          emphasis: { focus: "series" },
          data: days.map(d => dayMap.get(d)?.success || 0),
        },
        {
          name: "失败",
          type: "bar",
          stack: "total",
          barWidth: 16,
          itemStyle: { color: "#ef4444" },
          emphasis: { focus: "series" },
          data: days.map(d => dayMap.get(d)?.error || 0),
        },
        {
          name: "进行中",
          type: "bar",
          stack: "total",
          barWidth: 16,
          itemStyle: { color: "#3b82f6", borderRadius: [4, 4, 0, 0] },
          emphasis: { focus: "series" },
          data: days.map(d => dayMap.get(d)?.running || 0),
        },
        {
          name: "能力指数",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbol: "circle",
          symbolSize: 7,
          data: levelSeries,
          lineStyle: { width: 3, color: "#5b8cff" },
          itemStyle: { color: "#5b8cff", borderColor: "#0b0f14", borderWidth: 2 },
          areaStyle: {
            color: {
              type: "linear", x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(91,140,255,0.35)" },
                { offset: 1, color: "rgba(91,140,255,0.02)" },
              ],
            },
          },
          z: 3,
        },
      ],
    })

    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(chartRef.current)
    return () => { ro.disconnect(); chart.dispose() }
  }, [tasks])

  return <div ref={chartRef} style={{ width: "100%", height: 240 }} />
}

// ── Detail Panel ───────────────────────────────────────────────

function DetailPanel({ task, onClose }: { task: EvolutionTask | null; onClose: () => void }) {
  if (!task) {
    return (
      <div style={{
        display: "grid", placeContent: "center", minHeight: 280,
        textAlign: "center", color: "var(--muted-foreground)", gap: 10,
      }}>
        <div style={{
          width: 42, height: 42, margin: "0 auto", borderRadius: "50%",
          border: "1px solid var(--border)", display: "grid", placeItems: "center",
          color: "var(--primary)",
        }}>◎</div>
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6 }}>
          选择左侧任意进化任务<br />查看详情
        </p>
      </div>
    )
  }

  const statusColor = STATUS_COLORS[task.status]
  const statusLabel = task.status === "success" ? "成功" : task.status === "error" ? "失败" : "进行中"
  const stages = [...new Set(task.steps.map(s => s.stage))]

  return (
    <div style={{ padding: 0 }}>
      {/* Header */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
          <span style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "3px 8px", borderRadius: 999, fontSize: 11,
            color: task.strand === "strand_a" ? "#5b8cff" : "#c084fc",
            background: task.strand === "strand_a" ? "rgba(91,140,255,0.16)" : "rgba(192,132,252,0.16)",
            border: `1px solid ${task.strand === "strand_a" ? "rgba(91,140,255,0.28)" : "rgba(192,132,252,0.28)"}`,
          }}>
            {task.strand === "strand_a" ? "链A" : "链B"} · 周期 #{task.cycle}
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--muted-foreground)" }}>
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: statusColor }} />
            {statusLabel}
          </span>
        </div>
        <h3 style={{ margin: "8px 0 6px", fontSize: 16, fontWeight: 650, letterSpacing: "-0.02em" }}>
          {task.summary || `进化周期 #${task.cycle}`}
        </h3>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          <span style={{ fontSize: 11, color: "var(--muted-foreground)", border: "1px solid var(--border)", borderRadius: 999, padding: "2px 8px" }}>
            {formatDay(task.startTime)} {formatTime(task.startTime)}
          </span>
          <span style={{ fontSize: 11, color: "var(--muted-foreground)", border: "1px solid var(--border)", borderRadius: 999, padding: "2px 8px" }}>
            {formatDuration(task.startTime, task.endTime)}
          </span>
          <span style={{ fontSize: 11, color: "var(--muted-foreground)", border: "1px solid var(--border)", borderRadius: 999, padding: "2px 8px" }}>
            {task.steps.length} 步骤
          </span>
        </div>
      </div>

      {/* Stage badges */}
      <div style={{ marginBottom: 14 }}>
        <h5 style={{ margin: "0 0 8px", fontSize: 12, color: "var(--muted-foreground)", letterSpacing: "0.06em", textTransform: "uppercase" }}>
          涉及阶段
        </h5>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {stages.map(s => (
            <span key={s} style={{
              fontSize: 11, padding: "2px 8px", borderRadius: 999,
              color: STAGE_COLORS[s] || "#93a0b4",
              background: `${STAGE_COLORS[s] || "#93a0b4"}22`,
              border: `1px solid ${STAGE_COLORS[s] || "#93a0b4"}33`,
            }}>
              {STAGE_LABELS[s] || s}
            </span>
          ))}
        </div>
      </div>

      {/* Steps timeline */}
      <div>
        <h5 style={{ margin: "0 0 8px", fontSize: 12, color: "var(--muted-foreground)", letterSpacing: "0.06em", textTransform: "uppercase" }}>
          进化步骤
        </h5>
        <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 8 }}>
          {task.steps.map((s, i) => (
            <li key={i} style={{ display: "grid", gridTemplateColumns: "18px 1fr", gap: 8, fontSize: 12, color: "var(--muted-foreground)", lineHeight: 1.5 }}>
              <span style={{
                width: 18, height: 18, borderRadius: "50%", display: "grid", placeItems: "center",
                background: `${STAGE_COLORS[s.stage] || "#6b7280"}22`,
                color: STAGE_COLORS[s.stage] || "#6b7280",
                fontSize: 10, fontWeight: 700,
              }}>{i + 1}</span>
              <div>
                <span style={{ color: STAGE_COLORS[s.stage] || "#93a0b4", fontWeight: 600, marginRight: 6 }}>
                  {STAGE_LABELS[s.stage] || s.stage}
                </span>
                <span style={{ fontSize: 11 }}>{s.msg.length > 120 ? s.msg.slice(0, 120) + "…" : s.msg}</span>
                <span style={{ fontSize: 10, color: "var(--muted-foreground)", marginLeft: 8 }}>{formatTime(s.ts)}</span>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}

// ── Main Component ─────────────────────────────────────────────

export function EvolutionTimeline() {
  const { t } = useTranslation()
  const [data, setData] = useState<TimelineData | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [filter, setFilter] = useState<"all" | "strand_a" | "strand_b">("all")
  const [statusFilter, setStatusFilter] = useState<"all" | "success" | "error" | "running">("all")
  const [query, setQuery] = useState("")
  const [days, setDays] = useState(7)
  const apiBase = getApiBaseUrl()

  const fetchData = useCallback(async () => {
    try {
      setLoading(true)
      const resp = await fetch(`${apiBase}/api/evolution/timeline?days=${days}`)
      if (resp.ok) {
        const d = await resp.json()
        setData(d)
        if (!selectedId && d.tasks.length > 0) {
          setSelectedId(d.tasks[d.tasks.length - 1].taskId)
        }
      }
    } catch (e) {
      console.error("Failed to fetch evolution timeline:", e)
    } finally {
      setLoading(false)
    }
  }, [apiBase, days, selectedId])

  useEffect(() => { fetchData() }, [fetchData])

  const filteredTasks = useMemo(() => {
    if (!data) return []
    return data.tasks.filter(t => {
      if (filter !== "all" && t.strand !== filter) return false
      if (statusFilter !== "all" && t.status !== statusFilter) return false
      if (query) {
        const q = query.toLowerCase()
        const hay = [t.summary, t.strand, String(t.cycle), ...t.steps.map(s => s.msg)].join(" ").toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [data, filter, statusFilter, query])

  const selectedTask = useMemo(() => {
    return data?.tasks.find(t => t.taskId === selectedId) || null
  }, [data, selectedId])

  return (
    <PageLayout
      title="Agent 自我进化"
      icon={<Activity className="w-5 h-5" />}
      headerActions={
        <button onClick={fetchData} style={{
          padding: "6px 12px", borderRadius: 8, border: "1px solid var(--border)",
          background: "transparent", cursor: "pointer", display: "flex", alignItems: "center", gap: 6,
          fontSize: 12, color: "var(--muted-foreground)",
        }}>
          <RefreshCw size={14} /> 刷新
        </button>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {/* Stats */}
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {[
            { label: "总进化次数", value: data?.totalTasks ?? "—", icon: Layers, color: "#3b82f6" },
            { label: "成功", value: data?.successCount ?? "—", icon: CheckCircle, color: "#22c55e" },
            { label: "失败", value: data?.errorCount ?? "—", icon: XCircle, color: "#ef4444" },
            { label: "双链", value: "A + B", icon: GitBranch, color: "#a855f7" },
          ].map(c => (
            <div key={c.label} style={{
              flex: "1 1 120px", padding: "12px 14px", borderRadius: 14,
              background: `linear-gradient(135deg, ${c.color}11, ${c.color}05)`,
              border: `1px solid ${c.color}33`,
              display: "flex", alignItems: "center", gap: 12,
            }}>
              <c.icon size={18} style={{ color: c.color, opacity: 0.8 }} />
              <div>
                <div style={{ fontSize: 11, color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{c.label}</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: c.color, lineHeight: 1.2 }}>{c.value}</div>
              </div>
            </div>
          ))}
        </div>

        {/* Overview Chart */}
        <div style={{
          border: "1px solid var(--border)", borderRadius: 16, padding: "18px 18px 8px",
          background: "linear-gradient(180deg, rgba(255,255,255,0.025), transparent 40%), var(--card)",
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <div>
              <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>进化概览</h3>
              <p style={{ margin: "4px 0 0", color: "var(--muted-foreground)", fontSize: 12 }}>按日聚合的进化任务与能力指数</p>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              {[
                { label: "成功", color: "#22c55e" },
                { label: "失败", color: "#ef4444" },
                { label: "进行中", color: "#3b82f6" },
                { label: "能力", color: "#5b8cff" },
              ].map(l => (
                <span key={l.label} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted-foreground)" }}>
                  <i style={{ width: 8, height: 8, borderRadius: "50%", background: l.color }} />
                  {l.label}
                </span>
              ))}
            </div>
          </div>
          {loading ? (
            <div style={{ height: 240, display: "grid", placeContent: "center", color: "var(--muted-foreground)" }}>加载中…</div>
          ) : data?.tasks.length ? (
            <OverviewChart tasks={data.tasks} />
          ) : (
            <div style={{ height: 240, display: "grid", placeContent: "center", color: "var(--muted-foreground)" }}>暂无进化数据</div>
          )}
        </div>

        {/* Toolbar */}
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {[
              { key: "all" as const, label: "全部", color: "#5b8cff" },
              { key: "strand_a" as const, label: "链A", color: "#5b8cff" },
              { key: "strand_b" as const, label: "链B", color: "#c084fc" },
            ].map(f => (
              <button key={f.key} onClick={() => setFilter(f.key)} style={{
                appearance: "none", border: "1px solid var(--border)", borderRadius: 999,
                padding: "7px 12px", fontSize: 12, cursor: "pointer",
                color: filter === f.key ? "#fff" : "var(--muted-foreground)",
                background: filter === f.key ? f.color : "rgba(255,255,255,0.02)",
                borderColor: filter === f.key ? "transparent" : "var(--border)",
                boxShadow: filter === f.key ? `0 8px 20px ${f.color}33` : "none",
                transition: "all 0.15s ease",
              }}>
                {f.label}
              </button>
            ))}
            {[
              { key: "all" as const, label: "全部状态", color: "#5b8cff" },
              { key: "success" as const, label: "成功", color: "#22c55e" },
              { key: "error" as const, label: "失败", color: "#ef4444" },
              { key: "running" as const, label: "进行中", color: "#3b82f6" },
            ].map(f => (
              <button key={f.key} onClick={() => setStatusFilter(f.key)} style={{
                appearance: "none", border: "1px solid var(--border)", borderRadius: 999,
                padding: "7px 12px", fontSize: 12, cursor: "pointer",
                color: statusFilter === f.key ? "#fff" : "var(--muted-foreground)",
                background: statusFilter === f.key ? f.color : "rgba(255,255,255,0.02)",
                borderColor: statusFilter === f.key ? "transparent" : "var(--border)",
                boxShadow: statusFilter === f.key ? `0 8px 20px ${f.color}33` : "none",
                transition: "all 0.15s ease",
              }}>
                {f.label}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <div style={{
              display: "flex", alignItems: "center", gap: 8, padding: "8px 12px",
              borderRadius: 8, border: "1px solid var(--border)", background: "var(--background)",
            }}>
              <Search size={16} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
              <input
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="搜索进化任务…"
                style={{ border: "none", outline: "none", background: "transparent", fontSize: 13, color: "var(--foreground)", width: 180 }}
              />
            </div>
            <select value={days} onChange={e => setDays(Number(e.target.value))} style={{
              padding: "8px 12px", borderRadius: 8, border: "1px solid var(--border)",
              background: "var(--background)", color: "var(--foreground)", fontSize: 13,
            }}>
              <option value={1}>1天</option>
              <option value={3}>3天</option>
              <option value={7}>7天</option>
              <option value={14}>14天</option>
              <option value={30}>30天</option>
            </select>
          </div>
        </div>

        {/* Main: Gantt + Detail */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 380px", gap: 16, alignItems: "start" }}>
          {/* Gantt Chart */}
          <div style={{
            border: "1px solid var(--border)", borderRadius: 16, padding: 18,
            background: "linear-gradient(180deg, rgba(255,255,255,0.025), transparent 40%), var(--card)",
            overflow: "auto",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <div>
                <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>进化甘特图</h3>
                <p style={{ margin: "4px 0 0", color: "var(--muted-foreground)", fontSize: 12 }}>
                  每条代表一个进化周期 · 散点标记关键节点 · 点击查看详情
                </p>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                {Object.entries(STAGE_COLORS).filter(([k]) => ["turn", "plan", "implement", "evaluate", "reflect", "verdict", "error"].includes(k)).map(([k, c]) => (
                  <span key={k} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10, color: "var(--muted-foreground)" }}>
                    <i style={{ width: 6, height: 6, borderRadius: "50%", background: c }} />
                    {STAGE_LABELS[k] || k}
                  </span>
                ))}
              </div>
            </div>
            {loading ? (
              <div style={{ height: 200, display: "grid", placeContent: "center", color: "var(--muted-foreground)" }}>加载中…</div>
            ) : filteredTasks.length ? (
              <GanttChart tasks={filteredTasks} selectedId={selectedId} onSelect={setSelectedId} />
            ) : (
              <div style={{ height: 200, display: "grid", placeContent: "center", color: "var(--muted-foreground)" }}>
                没有匹配的进化任务
              </div>
            )}
          </div>

          {/* Detail Panel */}
          <div style={{
            border: "1px solid var(--border)", borderRadius: 16, padding: 16,
            background: "linear-gradient(180deg, rgba(255,255,255,0.025), transparent 40%), var(--card)",
            position: "sticky", top: 16, maxHeight: "calc(100vh - 120px)", overflow: "auto",
          }}>
            <h3 style={{ margin: "0 0 12px", fontSize: 12, color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
              任务详情
            </h3>
            <DetailPanel task={selectedTask} onClose={() => setSelectedId(null)} />
          </div>
        </div>
      </div>
    </PageLayout>
  )
}
