"use client"

// SpanTreeView — trajectory嵌套span树渲染组件（2026-10-02 P1）
// Warp orchestration树参照：drill-down（点行聚焦子树）+面包屑（点段返回）+
// subtree rollup（折叠子树显示整棵子树事件/token/耗时）。
// 纯逻辑=single source './span-tree'（无React可独立测试）；图标/配色由
// trajectory-client以getEventIcon/getEventColor props注入（单一真源仍在
// trajectory-client的EVENT_ICONS/EVENT_COLORS，本组件不复制映射表）。

import { useEffect, useState } from "react"
import type { ElementType } from "react"
import { ChevronRight, ChevronDown, Crosshair, Layers } from "lucide-react"
import { useTranslation } from "react-i18next"
import {
  type SpanNode,
  type SpanTreeRow,
  flattenVisible,
  toggleExpanded,
  defaultExpandedIds,
  breadcrumbPath,
  findNode,
  subtreeRollup,
  formatDuration,
  formatTokens,
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

  // roots变化（切换会话/重新拉取）=重置展开与聚焦（不残留上一棵树状态）
  useEffect(() => {
    setExpandedIds(defaultExpandedIds(roots))
    setFocusId(null)
  }, [roots])

  const focusNode = focusId ? findNode(roots, focusId) : null
  const viewRoots = focusNode ? [focusNode] : roots
  const rows: SpanTreeRow[] = flattenVisible(viewRoots, expandedIds)
  const crumbs = focusNode ? breadcrumbPath(roots, focusNode.id) : []

  return (
    <div>
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

      {/* span树行 */}
      {rows.length === 0 ? (
        <div style={{ textAlign: "center", padding: 40, color: "hsl(var(--muted-foreground))" }}>
          <Layers size={32} style={{ opacity: 0.3, marginBottom: 8 }} />
          <p style={{ fontSize: 14 }}>{t("trajectory.noEventsInSession")}</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {rows.map(row => (
            <SpanTreeRowView
              key={row.node.id}
              row={row}
              getEventColor={getEventColor}
              getEventIcon={getEventIcon}
              onToggle={() => setExpandedIds(prev => toggleExpanded(prev, row.node.id))}
              onFocus={() => setFocusId(row.node.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function SpanTreeRowView({
  row, getEventColor, getEventIcon, onToggle, onFocus,
}: {
  row: SpanTreeRow
  getEventColor: (type: string) => string
  getEventIcon: (type: string) => ElementType
  onToggle: () => void
  onFocus: () => void
}) {
  const { t } = useTranslation()
  const { node, depth, hasChildren, expanded } = row
  const color = getEventColor(node.event_type)
  const Icon = getEventIcon(node.event_type)
  const rollup = subtreeRollup(node)
  const preview = (node.content || "").replace(/\s+/g, " ").trim().slice(0, 120)

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
      }}
    >
      {/* 展开/折叠（有子节点才有）——点击不触发drill-down */}
      <span
        onClick={e => { e.stopPropagation(); if (hasChildren) onToggle() }}
        style={{ width: 14, display: "flex", alignItems: "center", flexShrink: 0 }}
      >
        {hasChildren ? (
          expanded ? <ChevronDown size={13} style={{ color, cursor: "pointer" }} />
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
          {preview}
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
