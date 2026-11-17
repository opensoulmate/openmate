"use client"

/**
 * Architecture Monitor — 架构监控面板
 * 
 * 实时显示P0组件状态：
 * - 写入围栏: 活跃写入锁、等待队列
 * - 通道队列: 队列深度、各lane等待数
 * - Timeouts: 各层超时统计
 * - Tool Errors: 错误统计、doom loop检测
 * - Edit Guard: 编辑统计
 * - Context Compressor: 压缩统计
 * - Observability: span统计
 * - Permissions: 当前权限级别
 */

import { useState, useEffect } from "react"
import {
  Shield,
  Clock,
  AlertTriangle,
  FileEdit,
  Layers,
  Activity,
  Lock,
  BarChart3,
  RefreshCw,
  CheckCircle,
  XCircle,
  AlertCircle,
} from "lucide-react"

interface ArchStats {
  uptime: string
  total_operations: number
  writer_fence: {
    active_claims: Record<string, {
      writer_id: string
      held_for: string
      last_hb: string
      stale: boolean
    }>
    waiting: Record<string, number>
  }
  lane_queue: {
    queue_depth: number
    running: number
    waiting_by_lane: Record<string, number>
    oldest_wait: string
    enqueued: number
    completed: number
    failed: number
  }
  timeouts: {
    active_operations: Array<{
      key: string
      layer: string
      running_for: string
      attempt: number
    }>
    recent_timeouts: number
    by_layer: Record<string, {
      timeouts: number
      limit: number
      retries: number
    }>
  }
  tool_errors: {
    recent_errors: number
    by_category: Record<string, number>
    by_tool: Record<string, number>
    doom_loop: {
      consecutive_failures: number
      recent_errors: number
      last_success: string
    }
  }
  edit_guard: {
    read_files: number
    snapshots: number
    config: Record<string, any>
  }
  context_compressor: {
    active_work_states: number
    compression_count: number
    max_tokens: number
  }
  observability: {
    total_spans: number
    active_spans: number
    total_traces: number
    total_errors: number
    by_type: Record<string, number>
    by_status: Record<string, number>
  }
  permissions: {
    current_level: string
  }
  eval_pipeline: {
    total_cases: number
    total_evaluations: number
    recent_pass_rate: number
  }
  context_budget?: {
    total_tokens: number
    usage_percent: number
  }
  tool_cache?: {
    hit_rate: string
    total_hits: number
  }
  checkpoints?: {
    total_checkpoints: number
    tracked_sessions: number
  }
  tracer?: {
    total_traces: number
    total_events: number
  }
  cost_tracker?: {
    daily_cost: string
    monthly_cost: string
  }
  retry_manager?: {
    success_rate: string
    retries_performed: number
  }
  event_bus?: {
    total_events: number
    total_subscribers: number
  }
  session_fsm?: {
    active_sessions: number
    total_transitions: number
  }
  output_validator?: {
    pass_rate: string
    total_validations: number
  }
  memory_engine?: {
    total_memories: number
    avg_importance: string
  }
  stream_manager?: {
    active_streams: number
    total_streams: number
  }
  parallel_executor?: {
    parallel_calls: number
    total_calls: number
  }
}

interface HealthStatus {
  status: string
  healthy: boolean
  checks: Record<string, {
    healthy: boolean
    [key: string]: any
  }>
  uptime: string
  total_operations: number
}

export function ArchitectureMonitor() {
  const [stats, setStats] = useState<ArchStats | null>(null)
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [opensoulStats, setOpensoulStats] = useState<any>(null)

  const fetchStats = async () => {
    try {
      const [statsRes, healthRes, intentRes, ltmRes] = await Promise.all([
        fetch("http://127.0.0.1:8092/api/architecture/stats"),
        fetch("http://127.0.0.1:8092/api/architecture/health"),
        fetch("http://127.0.0.1:8090/api/intelligence/intent/stats"),
        fetch("http://127.0.0.1:8090/api/hippo/ltm/stats"),
      ])
      
      if (statsRes.ok) {
        const statsData = await statsRes.json()
        setStats(statsData)
      }
      
      if (healthRes.ok) {
        const healthData = await healthRes.json()
        setHealth(healthData)
      }

      // OpenSoul模块统计
      if (intentRes.ok && ltmRes.ok) {
        const [intentData, ltmData] = await Promise.all([
          intentRes.json(),
          ltmRes.json(),
        ])
        setOpensoulStats({
          intent: intentData,
          ltm: ltmData,
        })
      }
      
      setError(null)
    } catch (e) {
      setError("无法连接到ACP代理")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchStats()
    
    if (autoRefresh) {
      const interval = setInterval(fetchStats, 5000)
      return () => clearInterval(interval)
    }
  }, [autoRefresh])

  if (loading) {
    return (
      <div className="p-6 text-center text-gray-400">
        <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2" />
        加载中...
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 text-center">
        <XCircle className="w-8 h-8 text-red-500 mx-auto mb-2" />
        <p className="text-red-400">{error}</p>
      </div>
    )
  }

  return (
    <div className="p-4 space-y-4 overflow-y-auto h-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-blue-400" />
          架构监控面板
        </h2>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`px-3 py-1 rounded text-sm ${
              autoRefresh
                ? "bg-blue-600 text-white"
                : "bg-gray-700 text-gray-300"
            }`}
          >
            {autoRefresh ? "自动刷新" : "手动刷新"}
          </button>
          <button
            onClick={fetchStats}
            className="p-2 rounded bg-gray-700 text-gray-300 hover:bg-gray-600"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Health Status */}
      {health && (
        <div className={`p-4 rounded-lg border ${
          health.healthy
            ? "bg-green-900/20 border-green-700"
            : "bg-yellow-900/20 border-yellow-700"
        }`}>
          <div className="flex items-center gap-2 mb-2">
            {health.healthy ? (
              <CheckCircle className="w-5 h-5 text-green-400" />
            ) : (
              <AlertCircle className="w-5 h-5 text-yellow-400" />
            )}
            <span className={`font-medium ${
              health.healthy ? "text-green-400" : "text-yellow-400"
            }`}>
              {health.status === "healthy" ? "系统健康" : "系统降级"}
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
            <div className="text-gray-300">
              运行时间: <span className="text-white">{health.uptime}</span>
            </div>
            <div className="text-gray-300">
              总操作: <span className="text-white">{health.total_operations}</span>
            </div>
          </div>
        </div>
      )}

      {/* Component Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* 写入围栏 */}
        <ComponentCard
          icon={<Lock className="w-5 h-5 text-purple-400" />}
          title="写入围栏"
          subtitle="并发写入保护"
        >
          {stats && (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between text-gray-300">
                <span>活跃锁</span>
                <span className="text-white">
                  {Object.keys(stats.writer_fence?.active_claims ?? {}).length}
                </span>
              </div>
              <div className="flex justify-between text-gray-300">
                <span>等待中</span>
                <span className="text-white">
                  {Object.values(stats.writer_fence?.waiting ?? {}).reduce((a, b) => a + b, 0)}
                </span>
              </div>
            </div>
          )}
        </ComponentCard>

        {/* 通道队列 */}
        <ComponentCard
          icon={<Layers className="w-5 h-5 text-blue-400" />}
          title="通道队列"
          subtitle="消息优先级队列"
        >
          {stats && (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between text-gray-300">
                <span>队列深度</span>
                <span className="text-white">{stats.lane_queue.queue_depth}</span>
              </div>
              <div className="flex justify-between text-gray-300">
                <span>运行中</span>
                <span className="text-white">{stats.lane_queue.running}</span>
              </div>
              <div className="flex justify-between text-gray-300">
                <span>最旧等待</span>
                <span className="text-white">{stats.lane_queue.oldest_wait}</span>
              </div>
            </div>
          )}
        </ComponentCard>

        {/* Timeouts */}
        <ComponentCard
          icon={<Clock className="w-5 h-5 text-yellow-400" />}
          title="分层超时"
          subtitle="runtime/model/provider/wait"
        >
          {stats && (
            <div className="space-y-2 text-sm">
              {Object.entries(stats.timeouts.by_layer).map(([layer, info]) => (
                <div key={layer} className="flex justify-between text-gray-300">
                  <span className="capitalize">{layer}</span>
                  <span className="text-white">
                    {info.timeouts} 次超时 (限制 {info.limit}s)
                  </span>
                </div>
              ))}
            </div>
          )}
        </ComponentCard>

        {/* Tool Errors */}
        <ComponentCard
          icon={<AlertTriangle className="w-5 h-5 text-red-400" />}
          title="工具错误"
          subtitle="三级处理 + doom loop"
        >
          {stats && (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between text-gray-300">
                <span>最近错误</span>
                <span className="text-white">{stats.tool_errors.recent_errors}</span>
              </div>
              <div className="flex justify-between text-gray-300">
                <span>连续失败</span>
                <span className={`${
                  stats.tool_errors.doom_loop.consecutive_failures > 3
                    ? "text-red-400"
                    : "text-white"
                }`}>
                  {stats.tool_errors.doom_loop.consecutive_failures}
                </span>
              </div>
            </div>
          )}
        </ComponentCard>

        {/* Edit Guard */}
        <ComponentCard
          icon={<FileEdit className="w-5 h-5 text-green-400" />}
          title="编辑安全"
          subtitle="锚点 + 原子写入 + 校验"
        >
          {stats && (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between text-gray-300">
                <span>已读文件</span>
                <span className="text-white">{stats.edit_guard.read_files}</span>
              </div>
              <div className="flex justify-between text-gray-300">
                <span>快照数</span>
                <span className="text-white">{stats.edit_guard.snapshots}</span>
              </div>
            </div>
          )}
        </ComponentCard>

        {/* Observability */}
        <ComponentCard
          icon={<Activity className="w-5 h-5 text-cyan-400" />}
          title="可观测性"
          subtitle="session/run/trace打点"
        >
          {stats && (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between text-gray-300">
                <span>总Span</span>
                <span className="text-white">{stats.observability?.total_spans ?? 0}</span>
              </div>
              <div className="flex justify-between text-gray-300">
                <span>活跃Span</span>
                <span className="text-white">{stats.observability?.active_spans ?? 0}</span>
              </div>
              <div className="flex justify-between text-gray-300">
                <span>错误数</span>
                <span className="text-white">{stats.observability?.total_errors ?? 0}</span>
              </div>
            </div>
          )}
        </ComponentCard>

        {/* Permissions */}
        <ComponentCard
          icon={<Shield className="w-5 h-5 text-orange-400" />}
          title="权限策略"
          subtitle="plan/ask/auto/full"
        >
          {stats && (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between text-gray-300">
                <span>当前级别</span>
                <span className={`font-medium ${
                  stats.permissions.current_level === "full"
                    ? "text-red-400"
                    : stats.permissions.current_level === "auto"
                    ? "text-yellow-400"
                    : "text-green-400"
                }`}>
                  {stats.permissions.current_level.toUpperCase()}
                </span>
              </div>
            </div>
          )}
        </ComponentCard>

        {/* Eval Pipeline */}
        <ComponentCard
          icon={<BarChart3 className="w-5 h-5 text-pink-400" />}
          title="评估流水线"
          subtitle="prompt/工具修改门禁"
        >
          {stats && (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between text-gray-300">
                <span>测试用例</span>
                <span className="text-white">{stats.eval_pipeline.total_cases}</span>
              </div>
              <div className="flex justify-between text-gray-300">
                <span>通过率</span>
                <span className={`${
                  stats.eval_pipeline.recent_pass_rate >= 0.8
                    ? "text-green-400"
                    : "text-yellow-400"
                }`}>
                  {(stats.eval_pipeline.recent_pass_rate * 100).toFixed(0)}%
                </span>
              </div>
            </div>
          )}
        </ComponentCard>

        {/* P1 Components - Compact Grid */}
        <P1Components stats={stats} opensoulStats={opensoulStats} />
      </div>
    </div>
  )
}

function P1Components({ stats, opensoulStats }: { stats: ArchStats | null; opensoulStats: any }) {
  if (!stats) return null

  const p1Items = [
    {
      name: "上下文预算",
      value: `${stats.context_budget?.usage_percent ?? 0}%`,
      detail: `${stats.context_budget?.total_tokens ?? 0} tokens`,
      color: "text-blue-400",
    },
    {
      name: "工具缓存",
      value: stats.tool_cache?.hit_rate ?? "0%",
      detail: `${stats.tool_cache?.total_hits ?? 0} hits`,
      color: "text-green-400",
    },
    {
      name: "检查点",
      value: `${stats.checkpoints?.total_checkpoints ?? 0}`,
      detail: `${stats.checkpoints?.tracked_sessions ?? 0} sessions`,
      color: "text-purple-400",
    },
    {
      name: "行为追踪",
      value: `${stats.tracer?.total_traces ?? 0}`,
      detail: `${stats.tracer?.total_events ?? 0} events`,
      color: "text-cyan-400",
    },
    {
      name: "成本追踪",
      value: stats.cost_tracker?.daily_cost ?? "$0",
      detail: `月: ${stats.cost_tracker?.monthly_cost ?? "$0"}`,
      color: "text-yellow-400",
    },
    {
      name: "重试管理",
      value: stats.retry_manager?.success_rate ?? "N/A",
      detail: `${stats.retry_manager?.retries_performed ?? 0} retries`,
      color: "text-orange-400",
    },
    {
      name: "事件总线",
      value: `${stats.event_bus?.total_events ?? 0}`,
      detail: `${stats.event_bus?.total_subscribers ?? 0} subscribers`,
      color: "text-pink-400",
    },
    {
      name: "状态机",
      value: `${stats.session_fsm?.active_sessions ?? 0}`,
      detail: `${stats.session_fsm?.total_transitions ?? 0} transitions`,
      color: "text-indigo-400",
    },
    {
      name: "输出验证",
      value: stats.output_validator?.pass_rate ?? "N/A",
      detail: `${stats.output_validator?.total_validations ?? 0} checks`,
      color: "text-emerald-400",
    },
    {
      name: "记忆引擎",
      value: `${stats.memory_engine?.total_memories ?? 0}`,
      detail: `avg: ${stats.memory_engine?.avg_importance ?? "0"}`,
      color: "text-rose-400",
    },
    {
      name: "流式管理",
      value: `${stats.stream_manager?.active_streams ?? 0}`,
      detail: `${stats.stream_manager?.total_streams ?? 0} total`,
      color: "text-sky-400",
    },
    {
      name: "并行执行",
      value: `${stats.parallel_executor?.parallel_calls ?? 0}`,
      detail: `${stats.parallel_executor?.total_calls ?? 0} calls`,
      color: "text-violet-400",
    },
    // OpenSoul 9模块
    {
      name: "意图分类",
      value: opensoulStats?.intent?.total_classified ?? 0,
      detail: `${opensoulStats?.intent?.known_intents?.length ?? 0} 种意图`,
      color: "text-teal-400",
    },
    {
      name: "长期记忆",
      value: opensoulStats?.ltm?.total_memories ?? 0,
      detail: `${opensoulStats?.ltm?.by_type?.episodic ?? 0} episodic`,
      color: "text-amber-400",
    },
  ]

  return (
    <div className="md:col-span-2">
      <div className="text-sm font-medium text-gray-300 mb-3">P1 增强组件</div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {p1Items.map((item) => (
          <div
            key={item.name}
            className="p-3 rounded-lg bg-gray-800/30 border border-gray-700/50"
          >
            <div className="text-xs text-gray-400 mb-1">{item.name}</div>
            <div className={`text-lg font-semibold ${item.color}`}>
              {item.value}
            </div>
            <div className="text-xs text-gray-500 mt-1">{item.detail}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function ComponentCard({
  icon,
  title,
  subtitle,
  children,
}: {
  icon: React.ReactNode
  title: string
  subtitle: string
  children: React.ReactNode
}) {
  return (
    <div className="p-4 rounded-lg bg-gray-800/50 border border-gray-700">
      <div className="flex items-center gap-2 mb-3">
        {icon}
        <div>
          <div className="font-medium text-white">{title}</div>
          <div className="text-xs text-gray-400">{subtitle}</div>
        </div>
      </div>
      {children}
    </div>
  )
}
