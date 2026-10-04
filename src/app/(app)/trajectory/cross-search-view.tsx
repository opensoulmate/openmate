"use client"

// 跨会话搜索视图组件——trajectory-client与system/tabs/trajectory-tab两消费者单一真源
// （span-tree-view.tsx同例）。含：
// ① StatusFilterChips：status过滤chip行（全部/仅错误/仅成功——"哪个session有
//    error的tool_call"的过滤面），未映射状态直出机器原值（"未知不猜"）
// ② SessionGroupHeader：搜索结果按会话分组的组头——任务描述+命中数（服务端
//    GROUP BY真值优先，不从截断列表推算）+最近命中样本+"打开会话"跳转

import { useTranslation } from "react-i18next"
import { FolderOpen, Search } from "lucide-react"
import {
  SEARCH_STATUS_OPTIONS,
  statusLabelKey,
  type ServerSessionMatch,
} from "./cross-search"

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
