"use client"

// SpanTreeView — trajectory嵌套span树渲染组件（2026-10-02 P1）
// Warp orchestration树参照：drill-down（点行聚焦子树）+面包屑（点段返回）+
// subtree rollup（折叠子树显示整棵子树事件/token/耗时）。
// 纯逻辑=single source './span-tree'（无React可独立测试）；图标/配色由
// trajectory-client以getEventIcon/getEventColor props注入（单一真源仍在
// trajectory-client的EVENT_ICONS/EVENT_COLORS，本组件不复制映射表）。

import { useEffect, useState } from "react"
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
} from "./span-tree"

export interface SpanTreeViewProps {
  roots: SpanNode[]
  getEventColor: (type: string) => string
  getEventIcon: (type: string) => ElementType
}

export function SpanTreeView({ roots, getEventColor, getEventIcon }: SpanTreeViewProps) {
  const { t } = useTranslation()
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => defaultExpandedIds(roots))
  const [focusId, setFocusId] = useState<string | null>(null)
  // 树内搜索/过滤（06:30轮遗留#4）：query×类型白名单×errorsOnly
  const [query, setQuery] = useState("")
  const [typeFilter, setTypeFilter] = useState<Set<string>>(() => new Set<string>())
  const [errorsOnly, setErrorsOnly] = useState(false)

  // roots变化（切换会话/重新拉取）=重置展开/聚焦/搜索（不残留上一棵树状态）
  useEffect(() => {
    setExpandedIds(defaultExpandedIds(roots))
    setFocusId(null)
    setQuery("")
    setTypeFilter(new Set<string>())
    setErrorsOnly(false)
  }, [roots])

  const focusNode = focusId ? findNode(roots, focusId) : null
  const viewRoots = focusNode ? [focusNode] : roots
  const crumbs = focusNode ? breadcrumbPath(roots, focusNode.id) : []
  const filter: TreeFilter = { query: query.trim(), types: typeFilter, errorsOnly }
  const typeChips = eventTypeCounts(roots)
  const errorCount = countErrors(roots)
  const toggleType = (type: string) =>
    setTypeFilter(prev => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  const clearFilters = () => { setQuery(""); setTypeFilter(new Set<string>()); setErrorsOnly(false) }

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
        <FilterChip
          label={t("common.all")}
          active={typeFilter.size === 0 && !errorsOnly}
          onClick={clearFilters}
        />
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
  expandedIds: Set<string>
  onToggle: (id: string) => void
  onFocus: (id: string) => void
  getEventColor: (type: string) => string
  getEventIcon: (type: string) => ElementType
}

/**
 * 树行渲染体（无自身状态=可SSR直测）：搜索/过滤/高亮全在内。
 * 过滤语义=filterTree剪树（命中+祖先上下文）+自动全展开+命中行高亮；
 * 过滤态Σrollup用filteredRollup（对剪后子树求和），非过滤态用服务端
 * subtree_*（单一真源）。
 */
export function SpanTreeBody({
  roots, filter, expandedIds, onToggle, onFocus, getEventColor, getEventIcon,
}: SpanTreeBodyProps) {
  const { t } = useTranslation()
  const q = (filter.query || "").trim()
  const filterActive = q !== "" || (filter.types?.size ?? 0) > 0 || !!filter.errorsOnly
  const filteredRoots = filterActive ? filterTree(roots, filter) : roots
  const effExpanded = filterActive ? expandAllIds(filteredRoots) : expandedIds
  const rows: SpanTreeRow[] = flattenVisible(filteredRoots, effExpanded)
  const hits = filterActive ? matchIds(filteredRoots, filter) : new Set<string>()
  const filterLabels = [
    ...(filter.types && filter.types.size > 0 ? [...filter.types].sort() : []),
    ...(filter.errorsOnly ? [t("common.error")] : []),
  ]

  return (
    <div>
      {/* 过滤结果摘要（结果数/生效条件——过滤态可见性声明） */}
      {filterActive && (
        <div style={{
          padding: "2px 10px", marginBottom: 6, fontSize: 11,
          color: "hsl(var(--muted-foreground))",
          display: "flex", gap: 10, flexWrap: "wrap",
        }}>
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
              filterActive={filterActive}
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
  // 过滤态Σ=对剪后子树重算（subtree_*是全树汇总，剪过之后是假数）；
  // 非过滤态=服务端subtree_*单一真源。
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
