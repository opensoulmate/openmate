// 跨会话搜索纯函数——trajectory-client与system/tabs/trajectory-tab两消费者单一真源
// （span-tree.ts同例：两消费者import同一模块，不复制本地副本防双实现漂移）。
//
// 背景（2026-10-04 P0 live实锤）：doSearch曾传params.set("q", ...)而opensoul
// /api/trajectory/search只读keyword → 文本搜索被静默忽略（q=探测词返回全量事件）。
// 参数名钉死在本纯函数里（render-test回归钉：不许再出现params.set("q")）；
// 后端已容错q别名（keyword非空优先），两端谁先改都不再哑掉文本过滤。
//
// 跨session搜索（"哪个session有error的tool_call"）：groupEventsBySession把扁平
// 命中按会话分组（组序=最近命中降序），分组计数优先吃后端/search/sessions的
// GROUP BY真值（不从截断的事件列表推算——"total推算说谎"纪律）。
//
// 搜索→树钻取整合（2026-10-05，"树内搜索与扁平搜索整合"销账）：
// treeFilterFromSearch把搜索条件适配成树内TreeFilter（SpanTreeView
// initialFilter携带）——从搜索结果"打开会话"钻进span树时命中行高亮/
// 未命中兄弟剪掉，两个搜索面（扁平分组×树内）对同一查询一致。

import type { TreeFilter } from "./span-tree"

export interface CrossSearchQueryInput {
  query: string
  selectedTypes: Set<string> | string[]
  status: string
}

/** 搜索URL查询串（keyword canonical + event_type单选 + status过滤）。 */
export function buildSearchQueryString({
  query,
  selectedTypes,
  status,
}: CrossSearchQueryInput): string {
  const params = new URLSearchParams()
  const kw = query.trim()
  if (kw) params.set("keyword", kw)
  const types = Array.from(selectedTypes)
  if (types.length === 1) params.set("event_type", types[0])
  if (status) params.set("status", status)
  return params.toString()
}

export interface SessionMatchGroup<T = unknown> {
  session_id: string
  /** 客户端可见命中条数（服务端GROUP BY真值在meta.match_count，UI优先显示真值） */
  visible_count: number
  /** 组内最近命中时间（ISO串，字典序可比） */
  latest_at: string
  events: T[]
}

/** 扁平命中事件按session_id分组，组序=最近命中降序（事件保持原序）。 */
export function groupEventsBySession<
  T extends { session_id: string; created_at: string },
>(events: T[]): SessionMatchGroup<T>[] {
  const byId = new Map<string, SessionMatchGroup<T>>()
  for (const ev of events) {
    const g = byId.get(ev.session_id)
    if (g) {
      g.events.push(ev)
      g.visible_count += 1
      if (ev.created_at > g.latest_at) g.latest_at = ev.created_at
    } else {
      byId.set(ev.session_id, {
        session_id: ev.session_id,
        visible_count: 1,
        latest_at: ev.created_at,
        events: [ev],
      })
    }
  }
  return Array.from(byId.values()).sort((a, b) =>
    a.latest_at < b.latest_at ? 1 : a.latest_at > b.latest_at ? -1 : 0
  )
}

/** 后端 /api/trajectory/search/sessions 的单组形状。 */
export interface ServerSessionMatch {
  session_id: string
  match_count: number
  first_match_at: string
  last_match_at: string
  task_description: string | null
  agent_id: string | null
  session_status: string | null
  sample: {
    event_id: string
    event_type: string
    status: string
    created_at: string
    snippet: string
    sample_truncated: boolean
  } | null
}

export const SEARCH_STATUS_OPTIONS = ["", "error", "ok"] as const

/**
 * 跨会话搜索条件 → 树内TreeFilter（搜索→树钻取"整合"的携带适配器）。
 *
 * 语义（精确不放宽）：status是**精确机器值**——status='error'钻进树后
 * statuses={'error'}只认error恰一档，**不**映射成errorsOnly（那是status≠ok
 * 的放宽档，warn/timeout会冒充error命中——"未知不猜/不冒充"纪律）；
 * status=''不携带status过滤。types保留全部勾选（搜索侧event_type单选是
 * URL约束，树侧白名单本就支持多选）。errorsOnly恒false（树里的错误chip
 * 是独立用户动作，不替用户预设）。
 */
export function treeFilterFromSearch({
  query,
  selectedTypes,
  status,
}: CrossSearchQueryInput): TreeFilter {
  return {
    query: (query || "").trim(),
    types: new Set(Array.from(selectedTypes)),
    errorsOnly: false,
    statuses: status ? new Set([status]) : new Set<string>(),
  }
}

/**
 * 状态过滤chip的i18n key；未映射状态返回null→调用方直出机器原值
 * （"未知不猜"显示层纪律，skill-display.ts同例）。
 */
export function statusLabelKey(status: string): string | null {
  switch (status) {
    case "":
      return "trajectory.statusAll"
    case "error":
      return "trajectory.statusError"
    case "ok":
      return "trajectory.statusOk"
    default:
      return null
  }
}
