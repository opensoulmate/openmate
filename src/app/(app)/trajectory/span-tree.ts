// span-tree.ts — trajectory span树视图纯逻辑单一真源（2026-10-02 P1）
//
// 目标（dev-report 04:20轮遗留#5点名的"观察面下一步天然目标"）：
// 多agent编排树UI首个落地——langfuse Trace→Observation嵌套span树渲染，
// 参照Warp orchestration树（drill-down+面包屑+subtree rollup，SUMMARY前端
// 差距表P1首行）与Warp subtree rollup（04:20轮store既有rollup字段被真实
// 数据激活，缺的只是树形渲染）。
//
// 数据单一真源=opensoul GET /api/trajectory/sessions/{sid}/trace
// （src/trajectory/store.py get_trace_tree：parent_event_id→父子边+环防护+
// subtree rollup）。本模块**不在前端重复建树**（禁止双实现漂移——
// utils.llm_transport合并轮教训），只做视图逻辑：可见行展开/折叠、
// drill-down面包屑、rollup展示取值。
//
// 无React依赖 → render-test-span-tree.cjs 用ts.transpileModule真实编译
// require调用（user-message.ts/composeAskAnswers同款惯例）。

export interface SpanNode {
  id: string
  event_type: string
  agent_id: string
  content: string
  status: string
  token_usage: number
  duration_ms: number
  created_at: string
  parent_event_id: string | null
  children: SpanNode[]
  // get_trace_tree（store.py rollup()）附加字段——可缺省（容错回退自身值）
  depth?: number
  subtree_tokens?: number
  subtree_duration_ms?: number
  subtree_events?: number
}

export interface SpanTreeRow {
  node: SpanNode
  /** 从传入roots起算的遍历深度（0基）——drill-down聚焦子树时自动归零 */
  depth: number
  hasChildren: boolean
  expanded: boolean
}

export interface SubtreeRollup {
  events: number
  tokens: number
  durationMs: number
}

/** 节点是否可展开（有子节点） */
export function isExpandable(node: SpanNode): boolean {
  return Array.isArray(node.children) && node.children.length > 0
}

/** DFS查找节点（面包屑/聚焦用）；不存在返回null */
export function findNode(roots: SpanNode[], id: string): SpanNode | null {
  for (const node of roots || []) {
    if (node.id === id) return node
    const hit = findNode(node.children || [], id)
    if (hit) return hit
  }
  return null
}

/**
 * 展开集=pre-order遍历所有可展开节点，深度<2的默认展开。
 * 默认形态：回合根+llm_call层可见，tool_call层折叠成rollup摘要
 * （Warp树默认形态：上层结构可见、细节收进子树）。
 */
export function defaultExpandedIds(roots: SpanNode[]): Set<string> {
  const out = new Set<string>()
  const walk = (nodes: SpanNode[], depth: number) => {
    for (const node of nodes || []) {
      if (isExpandable(node) && depth < 2) out.add(node.id)
      walk(node.children || [], depth + 1)
    }
  }
  walk(roots || [], 0)
  return out
}

/**
 * 当前展开状态下可见的行（pre-order，折叠节点的子树不出现）。
 * depth=遍历深度（不依赖服务端node.depth，drill-down归零自动成立）。
 */
export function flattenVisible(
  roots: SpanNode[],
  expandedIds: Set<string>,
): SpanTreeRow[] {
  const rows: SpanTreeRow[] = []
  const walk = (nodes: SpanNode[], depth: number) => {
    for (const node of nodes || []) {
      const hasChildren = isExpandable(node)
      const expanded = hasChildren && expandedIds.has(node.id)
      rows.push({ node, depth, hasChildren, expanded })
      if (expanded) walk(node.children || [], depth + 1)
    }
  }
  walk(roots || [], 0)
  return rows
}

/** 切换展开/折叠（返回新Set，React state友好） */
export function toggleExpanded(prev: Set<string>, id: string): Set<string> {
  const next = new Set(prev)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}

/**
 * 面包屑路径：root→focus节点的祖先链（含focus自身）。
 * 节点不存在返回[]（调用方回落全树视图，不炸）。
 */
export function breadcrumbPath(roots: SpanNode[], focusId: string): SpanNode[] {
  const path: SpanNode[] = []
  const walk = (nodes: SpanNode[]): boolean => {
    for (const node of nodes || []) {
      path.push(node)
      if (node.id === focusId) return true
      if (walk(node.children || [])) return true
      path.pop()
    }
    return false
  }
  return walk(roots || []) ? path : []
}

/**
 * 子树rollup展示取值——优先get_trace_tree的subtree_*汇总（Warp subtree
 * rollup：折叠时显示整棵子树的事件/token/耗时），缺省回退节点自身值
 * （旧数据/无rollup字段不显示NaN）。
 */
export function subtreeRollup(node: SpanNode): SubtreeRollup {
  return {
    events: node.subtree_events ?? 1,
    tokens: node.subtree_tokens ?? node.token_usage ?? 0,
    durationMs: node.subtree_duration_ms ?? node.duration_ms ?? 0,
  }
}

// ── 展示格式化（自trajectory-client.tsx逐字迁移=单一真源，输出字符串
//    与迁移前逐字一致，render-test钉住防回归） ──────────────────────────

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.round(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
}

export function formatTokens(n: number): string {
  if (n < 1000) return String(n)
  if (n < 1000000) return `${(n / 1000).toFixed(1)}k`
  return `${(n / 1000000).toFixed(1)}M`
}
