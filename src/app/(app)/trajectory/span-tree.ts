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

// ── 树内搜索/过滤（2026-10-02，06:30轮遗留#4销账） ─────────────────────
// "树内匹配span高亮+过滤（只看tool_call/error）"：在树形观察面内直接
// 搜索/筛事件，不必切回扁平搜索视图。语义（VS Code树搜索同款）：
// 命中节点+其祖先上下文保留，未命中的兄弟子树整枝剪掉；祖先=上下文
// （不亮），命中行=高亮。过滤态自动全展开（剪过的树=全是相关路径）。

export interface TreeFilter {
  /** 自由文本：event_type/content/agent_id/status 不区分大小写子串；空=不按文本过滤 */
  query: string
  /** 事件类型白名单（空/缺省=全部）——"只看tool_call" */
  types?: Set<string>
  /** true=只看status≠ok的错误/异常节点 */
  errorsOnly?: boolean
  /**
   * status精确机器值白名单（空/缺省=全部）——跨会话搜索status过滤钻取进树
   * （"整合"携带面）。与errorsOnly有意不对称：errorsOnly=status≠ok**放宽**档，
   * statuses={'error'}只认机器值error恰一档（warn/timeout等不冒充error——
   * "未知不猜/不冒充"纪律）。节点status缺省/空按"ok"归一后匹配
   * （与errorsOnly同口径）。
   */
  statuses?: Set<string>
}

/** 节点status归一（缺省/空串→"ok"，与errorsOnly判定同口径） */
function nodeStatus(node: SpanNode): string {
  return node.status || "ok"
}

/** 节点是否命中过滤条件（文本×类型×错误×status四条件AND） */
export function nodeMatches(node: SpanNode, filter: TreeFilter): boolean {
  const q = (filter.query || "").trim().toLowerCase()
  if (q) {
    const hay = `${node.event_type || ""}\n${node.content || ""}\n${node.agent_id || ""}\n${node.status || ""}`.toLowerCase()
    if (!hay.includes(q)) return false
  }
  if (filter.types && filter.types.size > 0 && !filter.types.has(node.event_type)) return false
  if (filter.errorsOnly && nodeStatus(node) === "ok") return false
  if (filter.statuses && filter.statuses.size > 0 && !filter.statuses.has(nodeStatus(node))) return false
  return true
}

/**
 * 过滤条件是否生效（"全部"chip激活态/摘要行显隐的单一真源——
 * SpanTreeBody与工具栏共用，禁止两处各算各的漂移）。
 */
export function filterIsActive(filter: TreeFilter): boolean {
  return (
    (filter.query || "").trim() !== "" ||
    (filter.types?.size ?? 0) > 0 ||
    !!filter.errorsOnly ||
    (filter.statuses?.size ?? 0) > 0
  )
}

/** Set相等（顺序无关）——sameFilter用 */
function sameSet(a?: Set<string>, b?: Set<string>): boolean {
  const sa = a || new Set<string>()
  const sb = b || new Set<string>()
  if (sa.size !== sb.size) return false
  for (const v of sa) if (!sb.has(v)) return false
  return true
}

/**
 * 两过滤条件是否相同（"来自搜索"来源chip显隐：当前过滤态仍等于钻取
 * 携带的初始态=来源可见；用户一改动即消失，不冒充"还来自搜索"）。
 */
export function sameFilter(a: TreeFilter, b: TreeFilter): boolean {
  return (
    (a.query || "").trim() === (b.query || "").trim() &&
    sameSet(a.types, b.types) &&
    sameSet(a.statuses, b.statuses) &&
    !!a.errorsOnly === !!b.errorsOnly
  )
}

/**
 * 过滤树：保留命中节点+祖先上下文，剪掉未命中兄弟子树。
 * 返回新树（浅拷贝节点、children=剪后数组），**不改原树**（React state友好）。
 * 命中节点的未命中子节点也被剪（结果集=命中路径，可解释性优先）。
 */
export function filterTree(roots: SpanNode[], filter: TreeFilter): SpanNode[] {
  const out: SpanNode[] = []
  for (const node of roots || []) {
    const keptChildren = filterTree(node.children || [], filter)
    if (nodeMatches(node, filter) || keptChildren.length > 0) {
      out.push({ ...node, children: keptChildren })
    }
  }
  return out
}

/** 命中节点id集（行高亮用）——pre-order遍历，命中=nodeMatches */
export function matchIds(roots: SpanNode[], filter: TreeFilter): Set<string> {
  const out = new Set<string>()
  const walk = (nodes: SpanNode[]) => {
    for (const node of nodes || []) {
      if (nodeMatches(node, filter)) out.add(node.id)
      walk(node.children || [])
    }
  }
  walk(roots || [])
  return out
}

/** 全部可展开节点id（过滤态自动展开：剪过的树全是相关路径） */
export function expandAllIds(roots: SpanNode[]): Set<string> {
  const out = new Set<string>()
  const walk = (nodes: SpanNode[]) => {
    for (const node of nodes || []) {
      if (isExpandable(node)) out.add(node.id)
      walk(node.children || [])
    }
  }
  walk(roots || [])
  return out
}

/**
 * 过滤态rollup：对（剪过的）children递归求和，公式与opensoul
 * store.py get_trace_tree的rollup()逐项一致
 * （events=1+Σ子、tokens=自身+Σ子、duration=自身+Σ子）。
 * 过滤态不能用subtree_*（那是全树汇总，剪过之后是假数）。
 */
export function filteredRollup(node: SpanNode): SubtreeRollup {
  let events = 1
  let tokens = node.token_usage ?? 0
  let durationMs = node.duration_ms ?? 0
  for (const child of node.children || []) {
    const r = filteredRollup(child)
    events += r.events
    tokens += r.tokens
    durationMs += r.durationMs
  }
  return { events, tokens, durationMs: Math.round(durationMs * 10) / 10 }
}

/** 事件类型计数（过滤chips数据源）：按数量降序、类型名升序稳定 */
export function eventTypeCounts(roots: SpanNode[]): Array<{ type: string; count: number }> {
  const counts = new Map<string, number>()
  const walk = (nodes: SpanNode[]) => {
    for (const node of nodes || []) {
      counts.set(node.event_type, (counts.get(node.event_type) || 0) + 1)
      walk(node.children || [])
    }
  }
  walk(roots || [])
  return [...counts.entries()]
    .map(([type, count]) => ({ type, count }))
    .sort((a, b) => b.count - a.count || (a.type < b.type ? -1 : 1))
}

/** status≠ok节点计数（errorsOnly chip徽标） */
export function countErrors(roots: SpanNode[]): number {
  let n = 0
  const walk = (nodes: SpanNode[]) => {
    for (const node of nodes || []) {
      if ((node.status || "ok") !== "ok") n++
      walk(node.children || [])
    }
  }
  walk(roots || [])
  return n
}

/**
 * 文本按查询切成高亮段（不区分大小写、全部非重叠命中）。
 * 段text拼回=原文（render-test钉住）；空查询=单段不亮。
 */
export function splitMatch(text: string, query: string): Array<{ text: string; hit: boolean }> {
  const q = (query || "").trim()
  if (!q) return [{ text: text || "", hit: false }]
  const lower = (text || "").toLowerCase()
  const needle = q.toLowerCase()
  const out: Array<{ text: string; hit: boolean }> = []
  let pos = 0
  for (;;) {
    const idx = lower.indexOf(needle, pos)
    if (idx < 0) break
    if (idx > pos) out.push({ text: (text || "").slice(pos, idx), hit: false })
    out.push({ text: (text || "").slice(idx, idx + needle.length), hit: true })
    pos = idx + needle.length
  }
  if (pos < (text || "").length) out.push({ text: (text || "").slice(pos), hit: false })
  return out.length > 0 ? out : [{ text: text || "", hit: false }]
}

// ── 回放渐进揭示（2026-10-04，"回放与树互斥"销账）────────────────────────
// 回放与树协同：回放进度（events[0..replayIdx]）→已揭示事件id集→树按执行序
// 渐进揭示（未来的span不出现=与扁平回放 events.slice 同语义）。裁剪语义与
// filterTree同族：id∈ids的节点保留+其祖先上下文保留（子树已揭示但父行不在
// ids时——fork/import数据可能出现——父行仍作上下文保留），未揭示子孙剪掉；
// 不改原树（React state友好）。

/**
 * 按已揭示id集裁剪树（回放渐进揭示）。
 * 保留：id∈ids 的节点 + 其祖先上下文行；剪掉：未揭示且无已揭示子孙的子树。
 * 返回新树（浅拷贝节点、children=裁后数组），**不改原树**。
 */
export function clipToIds(roots: SpanNode[], ids: Set<string>): SpanNode[] {
  const out: SpanNode[] = []
  for (const node of roots || []) {
    const keptChildren = clipToIds(node.children || [], ids)
    if (ids.has(node.id) || keptChildren.length > 0) {
      out.push({ ...node, children: keptChildren })
    }
  }
  return out
}

/** 树内span总数（回放"已揭示 X / Y"的Y；与clipToIds同一遍历口径） */
export function countSpans(roots: SpanNode[]): number {
  let n = 0
  const walk = (nodes: SpanNode[]) => {
    for (const node of nodes || []) {
      n += 1
      walk(node.children || [])
    }
  }
  walk(roots || [])
  return n
}
