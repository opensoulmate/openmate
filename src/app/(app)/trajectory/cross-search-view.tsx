"use client"

// 跨会话搜索视图组件——trajectory-client与system/tabs/trajectory-tab两消费者单一真源
// （span-tree-view.tsx同例）。含：
// ① StatusFilterChips：status过滤chip行（全部/仅错误/仅成功——"哪个session有
//    error的tool_call"的过滤面），未映射状态直出机器原值（"未知不猜"）
// ② SessionGroupHeader：搜索结果按会话分组的组头——任务描述+命中数（服务端
//    GROUP BY真值优先，不从截断列表推算）+最近命中样本+"打开会话"跳转
// ③ FlatFilterBar：扁平面携带过滤条（搜索→钻取在无trace树会话/列表视图同样
//    生效——过滤条件chips+命中计数+来源chip+"全部"清除）
// ④ MatchedText：查询命中片段高亮（splitMatch单一真源，树行预览同款样式）

import { useTranslation } from "react-i18next"
import { FolderOpen, Search } from "lucide-react"
import {
  SEARCH_STATUS_OPTIONS,
  statusLabelKey,
  type ServerSessionMatch,
} from "./cross-search"
import { filterIsActive, splitMatch, type TreeFilter } from "./span-tree"

export function StatusFilterChips({
  value,
  onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  const { t } = useTranslation()
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 6,
        marginTop: 8,
        padding: "8px 12px",
        borderRadius: 8,
        background: "hsl(var(--muted) / 0.3)",
        border: "1px solid hsl(var(--border))",
        alignItems: "center",
      }}
    >
      <span
        style={{
          fontSize: 11,
          color: "hsl(var(--muted-foreground))",
          marginRight: 2,
        }}
      >
        {t("trajectory.statusFilter")}
      </span>
      {SEARCH_STATUS_OPTIONS.map((s) => {
        const active = value === s
        const key = statusLabelKey(s)
        const color =
          s === "error" ? "#ef4444" : s === "ok" ? "#22c55e" : "#6b7280"
        return (
          <button
            key={s || "all"}
            onClick={() => onChange(s)}
            style={{
              padding: "3px 10px",
              borderRadius: 12,
              fontSize: 11,
              fontWeight: 500,
              border: `1px solid ${active ? color : "hsl(var(--border))"}`,
              background: active ? `${color}22` : "transparent",
              color: active ? color : "hsl(var(--muted-foreground))",
              cursor: "pointer",
            }}
          >
            {key ? t(key) : s}
          </button>
        )
      })}
    </div>
  )
}

export function SessionGroupHeader({
  sessionId,
  visibleCount,
  meta,
  onOpen,
}: {
  sessionId: string
  visibleCount: number
  meta?: ServerSessionMatch | null
  onOpen: (sessionId: string) => void
}) {
  const { t } = useTranslation()
  // 命中数=服务端GROUP BY真值优先；meta缺失（分组端点失败/超limit）才退回
  // 客户端可见条数，且文案同为"命中"——数字来源不同但都不推算total
  const count = meta ? meta.match_count : visibleCount
  const title =
    meta?.task_description || meta?.agent_id || sessionId.slice(0, 8)
  const sample = meta?.sample || null
  const sampleColor =
    sample?.status === "error"
      ? "#ef4444"
      : sample?.status === "ok"
        ? "#22c55e"
        : "#6b7280"

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "8px 12px",
        borderRadius: 8,
        background: "hsl(var(--muted) / 0.3)",
        border: "1px solid hsl(var(--border))",
        flexWrap: "wrap",
      }}
    >
      <Search size={14} style={{ color: "hsl(var(--primary))", flexShrink: 0 }} />
      <span
        style={{
          fontSize: 13,
          fontWeight: 600,
          color: "hsl(var(--foreground))",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          maxWidth: 320,
        }}
      >
        {title}
      </span>
      <span
        style={{
          fontSize: 10,
          padding: "1px 8px",
          borderRadius: 10,
          background: "hsl(var(--primary) / 0.12)",
          color: "hsl(var(--primary))",
          fontFamily: "monospace",
          flexShrink: 0,
        }}
      >
        {t("trajectory.sessionMatches", { count })}
      </span>
      {sample && (
        <span
          style={{
            fontSize: 11,
            color: "hsl(var(--muted-foreground))",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            maxWidth: 360,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
          title={sample.snippet}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: sampleColor,
              flexShrink: 0,
            }}
          />
          {sample.event_type} · {sample.snippet.slice(0, 80)}
          {sample.sample_truncated ? "…" : ""}
        </span>
      )}
      <button
        onClick={() => onOpen(sessionId)}
        style={{
          marginLeft: "auto",
          padding: "3px 10px",
          borderRadius: 6,
          fontSize: 11,
          border: "1px solid hsl(var(--border))",
          background: "transparent",
          color: "hsl(var(--muted-foreground))",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: 4,
          flexShrink: 0,
        }}
      >
        <FolderOpen size={12} /> {t("trajectory.openSession")}
      </button>
    </div>
  )
}

// 只读条件chip（扁平过滤条展示面）/清除按钮——样式对齐span-tree-view的
// FilterChip（视觉一致），但条件chip是**展示**不是入口（扁平面无可编辑
// 过滤态，过滤条件的入口在搜索框；"全部"是唯一动作=整体清除）。
const READONLY_CHIP_STYLE = {
  fontSize: 10, padding: "2px 8px", borderRadius: 999,
  border: "1px solid hsl(var(--primary))",
  background: "hsl(var(--primary) / 0.15)",
  color: "hsl(var(--primary))", fontWeight: 600,
  display: "flex", alignItems: "center", gap: 4, lineHeight: 1.6,
  whiteSpace: "nowrap",
}

/**
 * 扁平面携带过滤条（搜索→钻取在无trace树会话/列表视图的过滤面，2026-10-05
 * 02:18轮遗留#2"携带过滤无处生效"销账）：过滤条件chips（query/types/
 * errorsOnly/status——status未映射直出机器原值，"未知不猜"）+命中计数
 * （吃filterFlatEvents真值不推算）+"来自搜索"来源chip（tooltip=清除指引，
 * 同树面语义）+"全部"清除按钮（onClearAll恢复全量列表）。
 * filter未携带/未生效=渲染null（既有扁平列表形态零变化——回归钉）。
 */
export function FlatFilterBar({
  filter,
  fromSearch,
  matchCount,
  total,
  onClearAll,
}: {
  filter: TreeFilter | null
  fromSearch: boolean
  matchCount: number
  total: number
  onClearAll: () => void
}) {
  const { t } = useTranslation()
  if (!filter || !filterIsActive(filter)) return null
  const q = (filter.query || "").trim()
  const typeChips = filter.types && filter.types.size > 0 ? [...filter.types].sort() : []
  const statusChips = filter.statuses && filter.statuses.size > 0 ? [...filter.statuses].sort() : []
  return (
    <div
      style={{
        display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap",
        padding: "6px 8px", borderRadius: 8,
        background: "hsl(var(--muted) / 0.3)", border: "1px solid hsl(var(--border))",
      }}
    >
      <Search size={13} style={{ color: "hsl(var(--muted-foreground))", flexShrink: 0 }} />
      {/* "来自搜索"来源chip（携带面出处可见，同树面样式/tooltip） */}
      {fromSearch && (
        <span
          title={t("trajectory.fromSearchHint")}
          style={{
            ...READONLY_CHIP_STYLE,
            border: "1px dashed hsl(var(--primary))",
            background: "hsl(var(--primary) / 0.08)",
          }}
        >
          <Search size={10} />
          {t("trajectory.fromSearch")}
        </span>
      )}
      {q !== "" && <span style={READONLY_CHIP_STYLE}>{q}</span>}
      {typeChips.map(ty => (
        <span key={ty} style={READONLY_CHIP_STYLE}>{ty}</span>
      ))}
      {filter.errorsOnly && <span style={READONLY_CHIP_STYLE}>{t("common.error")}</span>}
      {statusChips.map(s => (
        <span key={s} style={READONLY_CHIP_STYLE}>{t(statusLabelKey(s) ?? s)}</span>
      ))}
      {/* 命中计数（filterFlatEvents真值——不从visible推算total） */}
      <span
        style={{
          marginLeft: "auto", fontSize: 11,
          color: "hsl(var(--muted-foreground))", fontFamily: "monospace",
        }}
      >
        {t("trajectory.flatMatches", { match: matchCount, total })}
      </span>
      <button
        onClick={onClearAll}
        title={t("trajectory.fromSearchHint")}
        style={{ ...READONLY_CHIP_STYLE, cursor: "pointer" }}
      >
        {t("common.all")}
      </button>
    </div>
  )
}

/**
 * 查询命中片段高亮（splitMatch单一真源，树行预览同款高亮样式）。
 * 空查询=单段不高亮（与splitMatch契约一致）。
 */
export function MatchedText({ text, query }: { text: string; query: string }) {
  return (
    <>
      {splitMatch(text || "", query || "").map((seg, i) =>
        seg.hit ? (
          <span
            key={i}
            style={{
              background: "hsl(var(--primary) / 0.28)", color: "hsl(var(--foreground))",
              fontWeight: 600, borderRadius: 2,
            }}
          >
            {seg.text}
          </span>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </>
  )
}
