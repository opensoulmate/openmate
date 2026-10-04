"use client"

// SpanTreeView — trajectory嵌套span树渲染组件（2026-10-02 P1）
// Warp orchestration树参照：drill-down（点行聚焦子树）+面包屑（点段返回）+
// subtree rollup（折叠子树显示整棵子树事件/token/耗时）。
// 纯逻辑=single source './span-tree'（无React可独立测试）；图标/配色由
// trajectory-client以getEventIcon/getEventColor props注入（单一真源仍在
// trajectory-shared的EVENT_ICONS/EVENT_COLORS，本组件不复制映射表）。

import { useEffect, useRef, useState } from "react"
import type { ElementType } from "react"
import { ChevronRight, ChevronDown, Crosshair, Layers, Search, X } from "lucide-react"
import { useTranslation } from "react-i18next"
import {
  type SpanNode,
  type SpanTreeRow,
  type TreeFilter,
  flattenVisible,
  toggleExpanded,
  defaultExpandedIds,
  breadcrumbPath,
  findNode,
  subtreeRollup,
  formatDuration,
  formatTokens,
  filterTree,
  matchIds,
  expandAllIds,
  filteredRollup,
  eventTypeCounts,
  countErrors,
  splitMatch,
  clipToIds,
  countSpans,
  filterIsActive,
  sameFilter,
} from "./span-tree"
import { statusLabelKey } from "./cross-search"

export interface SpanTreeViewProps {
  roots: SpanNode[]
  getEventColor: (type: string) => string
  getEventIcon: (type: string) => ElementType
  /** 回放渐进揭示：已揭示事件id集（null/缺省=全部揭示）——树随回放进度生长 */
  revealedIds?: Set<string> | null
  /**
   * 初始过滤条件（搜索→树钻取携带面，2026-10-05"整合"销账）：从跨会话
   * 搜索结果"打开会话"钻进树时预填query/types/statuses——命中高亮+未命中
   * 兄弟剪掉+自动展开，两个搜索面对同一查询一致。null/缺省=不过滤
   * （列表入口打开会话的既有形态逐字不变）。roots变化重置到本初始态
   * （非清空——携带的搜索条件随会话重载恢复）。
   */
  initialFilter?: TreeFilter | null
}

/** 初始过滤态→三份组件state（useState初始化与roots重置共用，禁双份漂移） */
function filterToState(filter: TreeFilter | null | undefined): {
  query: string
  types: Set<string>
  errorsOnly: boolean
  statuses: Set<string>
} {
  return {
    query: (filter?.query || "").trim(),
    types: new Set(filter?.types || []),
    errorsOnly: !!filter?.errorsOnly,
    statuses: new Set(filter?.statuses || []),
  }
}

export function SpanTreeView({ roots, getEventColor, getEventIcon, revealedIds = null, initialFilter = null }: SpanTreeViewProps) {
  const { t } = useTranslation()
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => defaultExpandedIds(roots))
  const [focusId, setFocusId] = useState<string | null>(null)
  // 树内搜索/过滤（06:30轮遗留#4）：query×类型白名单×errorsOnly×status精确值
  const init = filterToState(initialFilter)
  const [query, setQuery] = useState(init.query)
  const [typeFilter, setTypeFilter] = useState<Set<string>>(() => init.types)
  const [errorsOnly, setErrorsOnly] = useState(init.errorsOnly)
  const [statusFilter, setStatusFilter] = useState<Set<string>>(() => init.statuses)

  // roots变化（切换会话/重新拉取）=重置展开/聚焦/搜索到初始过滤态
  // （携带的搜索条件随会话重载恢复；无携带=清空，既有行为逐字不变）。
  // initialFilter走ref不进deps：调用方内联构造新引用也不会触发重置风暴
  // （重置只随roots变化发生，与旧deps=[roots]行为同节奏）
  const initialFilterRef = useRef(initialFilter)
  initialFilterRef.current = initialFilter
  useEffect(() => {
    setExpandedIds(defaultExpandedIds(roots))
    setFocusId(null)
    const s = filterToState(initialFilterRef.current)
    setQuery(s.query)
    setTypeFilter(s.types)
    setErrorsOnly(s.errorsOnly)
    setStatusFilter(s.statuses)
  }, [roots])

  const focusNode = focusId ? findNode(roots, focusId) : null
  const viewRoots = focusNode ? [focusNode] : roots
  const crumbs = focusNode ? breadcrumbPath(roots, focusNode.id) : []
  const filter: TreeFilter = { query: query.trim(), types: typeFilter, errorsOnly, statuses: statusFilter }
  const typeChips = eventTypeCounts(roots)
  const errorCount = countErrors(roots)
  // "来自搜索"来源chip：当前过滤态仍=钻取携带的初始态才亮（用户一改动即
  // 消失，不冒充"还来自搜索"——sameFilter单一真源）
  const fromSearch = !!initialFilter && filterIsActive(filter) && sameFilter(filter, initialFilter)
  const toggleType = (type: string) =>
    setTypeFilter(prev => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  const clearFilters = () => {
    setQuery(""); setTypeFilter(new Set<string>()); setErrorsOnly(false); setStatusFilter(new Set<string>())
  }

  return (
    <div>
      {/* 搜索/过滤工具栏（树内搜索——不必切回扁平搜索视图） */}
      <div style={{
        display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap",
        padding: "6px 8px", marginBottom: 8, borderRadius: 8,
        background: "hsl(var(--muted) / 0.3)", border: "1px solid hsl(var(--border))",
      }}>
        <Search size={13} style={{ color: "hsl(var(--muted-foreground))", flexShrink: 0 }} />
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder={t("trajectory.searchEventsPlaceholder")}
          style={{
            flex: 1, minWidth: 140, border: "none", outline: "none", background: "transparent",
            fontSize: 12, color: "hsl(var(--foreground))",
          }}
        />
        {query !== "" && (
          <button
            onClick={() => setQuery("")}
            title={t("trajectory.search")}
            style={{
              border: "none", background: "none", cursor: "pointer", padding: 2,
              display: "flex", alignItems: "center", color: "hsl(var(--muted-foreground))",
            }}
          >
            <X size={12} />
          </button>
        )}
        {/* "来自搜索"来源chip（钻取携带面可见性）：当前过滤态仍=搜索携带的
            初始态才亮，用户一改动即消失；点"全部"可整体清除 */}
        {fromSearch && (
          <span
            title={t("trajectory.fromSearchHint")}
            style={{
              fontSize: 10, padding: "2px 8px", borderRadius: 999,
              border: "1px dashed hsl(var(--primary))",
              background: "hsl(var(--primary) / 0.08)",
              color: "hsl(var(--primary))", fontWeight: 600,
              display: "flex", alignItems: "center", gap: 4, lineHeight: 1.6,
              whiteSpace: "nowrap",
            }}
          >
            <Search size={10} />
            {t("trajectory.fromSearch")}
          </span>
        )}
        <FilterChip
          label={t("common.all")}
          active={typeFilter.size === 0 && !errorsOnly && statusFilter.size === 0}
          onClick={clearFilters}
        />
        {/* status精确值chip（搜索携带面）：点掉即解除该status过滤；不提供
            树内新增入口——status过滤的入口在搜索框（两面分工明确） */}
        {Array.from(statusFilter).sort().map(s => (
          <FilterChip
            key={s}
            label={t(statusLabelKey(s) ?? s)}
            active
            onClick={() =>
              setStatusFilter(prev => {
                const next = new Set(prev)
                next.delete(s)
                return next
              })
            }
          />
        ))}
        {typeChips.map(c => (
          <FilterChip
            key={c.type}
            label={c.type}
            count={c.count}
            active={typeFilter.has(c.type)}
            onClick={() => toggleType(c.type)}
          />
        ))}
        {errorCount > 0 && (
          <FilterChip
            label={t("common.error")}
            count={errorCount}
            active={errorsOnly}
            onClick={() => setErrorsOnly(v => !v)}
          />
        )}
      </div>
      {/* 面包屑（drill-down导航）：聚焦时显示 root→focus 路径，点段跳转 */}
      {focusNode ? (
        <div style={{
          display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap",
          padding: "6px 10px", marginBottom: 8, borderRadius: 8,
          background: "hsl(var(--muted) / 0.3)", border: "1px solid hsl(var(--border))",
          fontSize: 12,
        }}>
          <button
            onClick={() => setFocusId(null)}
            style={{
              color: "hsl(var(--primary))", background: "none", border: "none",
              cursor: "pointer", fontSize: 12, display: "flex", alignItems: "center", gap: 4,
            }}
            title={t("trajectory.allSpans")}
          >
            <Layers size={12} /> {t("trajectory.allSpans")}
          </button>
          {crumbs.map((c, i) => (
            <span key={c.id} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <ChevronRight size={11} style={{ color: "hsl(var(--muted-foreground))", opacity: 0.6 }} />
              <button
                onClick={() => { if (i < crumbs.length - 1) setFocusId(c.id) }}
                style={{
                  color: i === crumbs.length - 1 ? "hsl(var(--foreground))" : "hsl(var(--primary))",
                  background: "none", border: "none", cursor: i === crumbs.length - 1 ? "default" : "pointer",
                  fontSize: 12, fontWeight: i === crumbs.length - 1 ? 600 : 400,
                  padding: 0,
                }}
              >
                {c.event_type || c.id.slice(0, 8)}
              </button>
            </span>
          ))}
        </div>
      ) : (
        <div style={{
          padding: "4px 10px", marginBottom: 8, fontSize: 11,
          color: "hsl(var(--muted-foreground))",
        }}>
          {t("trajectory.treeHint")}
        </div>
      )}

      {/* span树行（搜索/过滤/高亮逻辑在SpanTreeBody内=可独立SSR测试） */}
      <SpanTreeBody
        roots={viewRoots}
        filter={filter}
        revealedIds={revealedIds}
        expandedIds={expandedIds}
        onToggle={id => setExpandedIds(prev => toggleExpanded(prev, id))}
        onFocus={setFocusId}
        getEventColor={getEventColor}
        getEventIcon={getEventIcon}
      />
    </div>
  )
}

function FilterChip({
  label, count, active, onClick,
}: {
  label: string
  count?: number
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      style={{
        fontSize: 10, padding: "2px 8px", borderRadius: 999, cursor: "pointer",
        border: `1px solid ${active ? "hsl(var(--primary))" : "hsl(var(--border))"}`,
        background: active ? "hsl(var(--primary) / 0.15)" : "transparent",
        color: active ? "hsl(var(--primary))" : "hsl(var(--muted-foreground))",
        fontWeight: active ? 600 : 400,
        display: "flex", alignItems: "center", gap: 4, lineHeight: 1.6,
      }}
    >
      {label}
      {typeof count === "number" && <span style={{ opacity: 0.7 }}>{count}</span>}
    </button>
  )
}

export interface SpanTreeBodyProps {
  /** 聚焦后的视图根（SpanTreeView传viewRoots） */
  roots: SpanNode[]
  filter: TreeFilter
  /** 回放渐进揭示id集（null/缺省=全部揭示） */
  revealedIds?: Set<string> | null
  expandedIds: Set<string>
  onToggle: (id: string) => void
  onFocus: (id: string) => void
  getEventColor: (type: string) => string
  getEventIcon: (type: string) => ElementType
}

/**
 * 树行渲染体（无自身状态=可SSR直测）：搜索/过滤/高亮全在内。
 * 过滤语义=filterTree剪树（命中+祖先上下文）+自动全展开+命中行高亮；
 * 回放语义=clipToIds按已揭示id集渐进揭示（同族祖先上下文保留，Σrollup
 * 同过滤态对可见子树重算——subtree_*是全树汇总，裁过之后是假数）；
 * 非过滤/非回放态Σrollup用服务端subtree_*（单一真源）。
 */
export function SpanTreeBody({
  roots, filter, revealedIds = null, expandedIds, onToggle, onFocus, getEventColor, getEventIcon,
}: SpanTreeBodyProps) {
  const { t } = useTranslation()
  const q = (filter.query || "").trim()
  // filterIsActive=过滤生效判定单一真源（工具栏"全部"chip激活态同源）
  const filterActive = filterIsActive(filter)
  const clipActive = revealedIds != null
  const baseRoots = clipActive ? clipToIds(roots, revealedIds) : roots
  const filteredRoots = filterActive ? filterTree(baseRoots, filter) : baseRoots
  // 回放态同样自动全展开（渐进生长的全是已发生路径，不做折叠残影）
  const effExpanded = filterActive || clipActive ? expandAllIds(filteredRoots) : expandedIds
  const rows: SpanTreeRow[] = flattenVisible(filteredRoots, effExpanded)
  const hits = filterActive ? matchIds(filteredRoots, filter) : new Set<string>()
  const filterLabels = [
    ...(filter.types && filter.types.size > 0 ? [...filter.types].sort() : []),
    ...(filter.errorsOnly ? [t("common.error")] : []),
    // status精确值条件也进摘要（"生效条件可见"——钻取携带的status不静默）
    ...(filter.statuses && filter.statuses.size > 0
      ? [...filter.statuses].sort().map(s => t(statusLabelKey(s) ?? s))
      : []),
  ]

  return (
    <div>
      {/* 过滤/回放结果摘要（结果数/生效条件——可见性声明） */}
      {(filterActive || clipActive) && (
        <div style={{
          padding: "2px 10px", marginBottom: 6, fontSize: 11,
          color: "hsl(var(--muted-foreground))",
          display: "flex", gap: 10, flexWrap: "wrap",
        }}>
          {clipActive && (
            <span>{t("trajectory.replayRevealed", {
              shown: countSpans(filteredRoots),
              total: countSpans(roots),
              defaultValue: "回放中：已揭示 {{shown}}/{{total}} 个span",
            })}</span>
          )}
          {q !== "" && <span>{t("trajectory.resultsFor", { count: hits.size, query: q })}</span>}
          {filterLabels.length > 0 && (
            <span>{t("trajectory.filtered", { types: filterLabels.join(", ") })}</span>
          )}
        </div>
      )}
      {rows.length === 0 ? (
        <div style={{ textAlign: "center", padding: 40, color: "hsl(var(--muted-foreground))" }}>
          <Layers size={32} style={{ opacity: 0.3, marginBottom: 8 }} />
          <p style={{ fontSize: 14 }}>
            {filterActive ? t("trajectory.noMatchingEvents") : t("trajectory.noEventsInSession")}
          </p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {rows.map(row => (
            <SpanTreeRowView
              key={row.node.id}
              row={row}
              filterActive={filterActive || clipActive}
              hit={hits.has(row.node.id)}
              query={q}
              getEventColor={getEventColor}
              getEventIcon={getEventIcon}
              onToggle={() => onToggle(row.node.id)}
              onFocus={() => onFocus(row.node.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function SpanTreeRowView({
  row, filterActive, hit, query, getEventColor, getEventIcon, onToggle, onFocus,
}: {
  row: SpanTreeRow
  filterActive: boolean
  hit: boolean
  query: string
  getEventColor: (type: string) => string
  getEventIcon: (type: string) => ElementType
  onToggle: () => void
  onFocus: () => void
}) {
  const { t } = useTranslation()
  const { node, depth, hasChildren, expanded } = row
  const color = getEventColor(node.event_type)
  const Icon = getEventIcon(node.event_type)
  // 过滤/回放态Σ=对裁后子树重算（subtree_*是全树汇总，裁过之后是假数）；
  // 非过滤/非回放态=服务端subtree_*单一真源（filterActive在此含回放裁剪态）。
  const rollup = filterActive ? filteredRollup(node) : subtreeRollup(node)
  const preview = (node.content || "").replace(/\s+/g, " ").trim().slice(0, 120)
  const segments = splitMatch(preview, query)

  return (
    <div
      onClick={onFocus}
      title={t("trajectory.treeHint")}
      style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "5px 8px", borderRadius: 6, cursor: "pointer",
        borderLeft: `3px solid ${color}`,
        background: `${color}08`,
        border: `1px solid ${color}22`,
        borderLeftWidth: 3,
        marginLeft: depth * 20,
        transition: "all 0.15s",
        // 命中行高亮环（祖先上下文行不亮）
        boxShadow: hit ? `0 0 0 1.5px ${color}66` : undefined,
      }}
    >
      {/* 展开/折叠（有子节点才有）——点击不触发drill-down；
          过滤态自动全展开，折叠开关隐藏（开了也压不动，不做死控件） */}
      <span
        onClick={e => { e.stopPropagation(); if (hasChildren && !filterActive) onToggle() }}
        style={{ width: 14, display: "flex", alignItems: "center", flexShrink: 0 }}
      >
        {hasChildren ? (
          filterActive ? (
            <ChevronDown size={13} style={{ color, opacity: 0.5 }} />
          ) : expanded ? <ChevronDown size={13} style={{ color, cursor: "pointer" }} />
            : <ChevronRight size={13} style={{ color, cursor: "pointer" }} />
        ) : null}
      </span>

      <Icon size={13} style={{ color, flexShrink: 0 }} />
      <span style={{
        fontSize: 10, padding: "1px 6px", borderRadius: 4, flexShrink: 0,
        background: `${color}22`, color, fontWeight: 600,
      }}>
        {node.event_type}
      </span>
      {node.status && node.status !== "ok" && (
        <span style={{
          fontSize: 9, padding: "1px 5px", borderRadius: 4, flexShrink: 0,
          background: "#ef444422", color: "#ef4444",
        }}>
          {node.status}
        </span>
      )}
      {node.agent_id && (
        <span style={{
          fontSize: 9, padding: "1px 5px", borderRadius: 4, flexShrink: 0,
          background: "hsl(var(--muted))", color: "hsl(var(--muted-foreground))",
        }}>
          {node.agent_id}
        </span>
      )}
      {preview && (
        <span style={{
          fontSize: 11, color: "hsl(var(--muted-foreground))", flex: 1, minWidth: 0,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        }}>
          {/* 查询命中片段高亮（splitMatch单一真源）；空查询=单段不高亮 */}
          {segments.map((seg, i) => seg.hit ? (
            <span key={i} style={{
              background: "hsl(var(--primary) / 0.28)", color: "hsl(var(--foreground))",
              fontWeight: 600, borderRadius: 2,
            }}>{seg.text}</span>
          ) : (
            <span key={i}>{seg.text}</span>
          ))}
        </span>
      )}
      {/* subtree rollup（Warp subtree-rollup）：折叠子树的整棵汇总 */}
      <span style={{
        fontSize: 10, color: "hsl(var(--muted-foreground))", flexShrink: 0,
        display: "flex", alignItems: "center", gap: 6,
        fontWeight: hasChildren && !expanded ? 600 : 400,
      }}>
        {hasChildren && !expanded && (
          <span style={{ color, opacity: 0.9 }}>
            Σ{rollup.events} {t("trajectory.events")}
          </span>
        )}
        <span>{formatTokens(rollup.tokens)} {t("trajectory.tok")}</span>
        <span>{formatDuration(rollup.durationMs)}</span>
      </span>
      {/* drill-down按钮（行点击同效，图标=可发现性） */}
      <span style={{ flexShrink: 0, display: "flex", opacity: 0.5 }}>
        <Crosshair size={12} style={{ color: "hsl(var(--muted-foreground))" }} />
      </span>
    </div>
  )
}
