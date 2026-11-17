"use client";

/**
 * UI DSL 渲染器（最小版）— form/list/report 三种面板走宿主组件
 *
 * 设计文档: docs/plugin-system-design.md §2
 * - JSON 清单 → 宿主原生渲染；皮肤来自设计令牌（globals.css），插件不注入样式
 * - 数据绑定唯一来源是 MCP 工具（source.tool / submit.tool），与对话共用同一 handler
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  DslFormPanel,
  DslListPanel,
  DslPage,
  DslPanel,
  DslReportPanel,
  normalizeDslPage,
} from "@/lib/plugin-ui-dsl";

/** 工具调用通道：(MCP 全名, 参数, 是否已审批) → 结果。UI 与对话共用同一后端工具。 */
export type ToolCall = (
  tool: string,
  args: Record<string, unknown>,
  approved?: boolean
) => Promise<Record<string, unknown>>;

interface PanelRendererProps {
  page: DslPage;
  toolCall: ToolCall;
  /** 预取数据：panelId → 数据（list: {items}，report: 结果对象）。测试/SSR 可注入 */
  initialData?: Record<string, Record<string, unknown>>;
  className?: string;
}

export function PanelRenderer({ page, toolCall, initialData, className }: PanelRendererProps) {
  const [results, setResults] = useState<Record<string, Record<string, unknown>>>({});
  const [refreshKey, setRefreshKey] = useState(0);

  const handleSubmitResult = useCallback((targetPanel: string, result: Record<string, unknown>) => {
    if (targetPanel) {
      setResults((prev) => ({ ...prev, [targetPanel]: result }));
    }
    setRefreshKey((key) => key + 1); // 提交成功后刷新带 source 的 list 面板
  }, []);

  return (
    <div className={cn("flex flex-col gap-4", className)} data-slot="plugin-panel-renderer">
      {page.panels.map((panel) => (
        <PanelView
          key={panel.id}
          panel={panel}
          toolCall={toolCall}
          initial={initialData?.[panel.id]}
          result={results[panel.id]}
          refreshKey={refreshKey}
          onSubmitResult={handleSubmitResult}
        />
      ))}
    </div>
  );
}

function PanelView({ panel, toolCall, initial, result, refreshKey, onSubmitResult }: {
  panel: DslPanel;
  toolCall: ToolCall;
  initial?: Record<string, unknown>;
  result?: Record<string, unknown>;
  refreshKey: number;
  onSubmitResult: (targetPanel: string, result: Record<string, unknown>) => void;
}) {
  if (panel.type === "form") {
    return <FormPanelView panel={panel} toolCall={toolCall} onSubmitResult={onSubmitResult} />;
  }
  if (panel.type === "list") {
    return <ListPanelView panel={panel} toolCall={toolCall} initial={initial} refreshKey={refreshKey} />;
  }
  return <ReportPanelView panel={panel} result={result ?? initial} />;
}

function PanelCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-[var(--radius-lg)] border border-border bg-card p-4" data-slot="plugin-panel">
      <h3 className="mb-3 text-sm font-semibold text-foreground">{title}</h3>
      {children}
    </section>
  );
}

/* ─────────────────────────── form 表单面板 ─────────────────────────── */

function FormPanelView({
  panel,
  toolCall,
  onSubmitResult,
}: {
  panel: DslFormPanel;
  toolCall: ToolCall;
  onSubmitResult: (targetPanel: string, result: Record<string, unknown>) => void;
}) {
  const [values, setValues] = useState<Record<string, string | boolean>>(() => {
    const init: Record<string, string | boolean> = {};
    for (const field of panel.fields) {
      init[field.name] = field.default === null || field.default === undefined
        ? field.widget === "checkbox" ? false : ""
        : String(field.default);
    }
    return init;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = panel.submit;
  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!submit || busy) return;
    for (const field of panel.fields) {
      if (field.required && !values[field.name]) {
        setError(`请填写「${field.label}」`);
        return;
      }
    }
    setBusy(true);
    setError("");
    try {
      const args: Record<string, unknown> = {};
      for (const field of panel.fields) {
        const value = values[field.name];
        args[field.name] = field.widget === "number" ? Number(value) : value;
      }
      const result = await toolCall(submit.tool, args);
      onSubmitResult(submit.target_panel ?? "", result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <PanelCard title={panel.title}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3" data-slot="plugin-form">
        {panel.fields.map((field) => (
          <label key={field.name} className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">
              {field.label}
              {field.required ? <span className="ml-1 text-destructive">*</span> : null}
            </span>
            {field.widget === "select" ? (
              <select
                className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm text-foreground outline-none focus-visible:border-ring"
                value={String(values[field.name] ?? "")}
                onChange={(e) => setValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
              >
                {(field.options ?? []).map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            ) : field.widget === "textarea" ? (
              <textarea
                className="w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm text-foreground outline-none focus-visible:border-ring"
                placeholder={field.placeholder}
                value={String(values[field.name] ?? "")}
                onChange={(e) => setValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
              />
            ) : field.widget === "checkbox" ? (
              <input
                type="checkbox"
                className="size-4 accent-[var(--color-primary,#6c7086)]"
                checked={Boolean(values[field.name])}
                onChange={(e) => setValues((prev) => ({ ...prev, [field.name]: e.target.checked }))}
              />
            ) : (
              <Input
                type={field.widget === "number" ? "number" : field.widget === "date" ? "date" : "text"}
                placeholder={field.placeholder}
                value={String(values[field.name] ?? "")}
                onChange={(e) => setValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
              />
            )}
          </label>
        ))}
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        {submit ? (
          <Button type="submit" disabled={busy} className="self-start">
            {busy ? "提交中…" : submit.label}
          </Button>
        ) : null}
      </form>
    </PanelCard>
  );
}

/* ─────────────────────────── list 列表面板 ─────────────────────────── */

function ListPanelView({
  panel,
  toolCall,
  initial,
  refreshKey,
}: {
  panel: DslListPanel;
  toolCall: ToolCall;
  initial?: Record<string, unknown>;
  refreshKey: number;
}) {
  const [rows, setRows] = useState<Record<string, unknown>[]>(() => extractRows(initial));
  const [error, setError] = useState("");

  useEffect(() => {
    if (!panel.source?.tool) return;
    let cancelled = false;
    toolCall(panel.source.tool, panel.source.args ?? {})
      .then((data) => {
        if (!cancelled) setRows(extractRows(data));
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panel.id, refreshKey]);

  return (
    <PanelCard title={panel.title}>
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{panel.empty_text}</p>
      ) : (
        <table className="w-full text-sm" data-slot="plugin-list">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted-foreground">
              {panel.columns.map((col) => (
                <th key={col.key} className="px-2 py-1.5 font-medium">
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index} className="border-b border-border/50 text-foreground">
                {panel.columns.map((col) => (
                  <td key={col.key} className="px-2 py-1.5">
                    {formatCell(row[col.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </PanelCard>
  );
}

/* ─────────────────────────── report 报告面板 ─────────────────────────── */

function ReportPanelView({
  panel,
  result,
}: {
  panel: DslReportPanel;
  result?: Record<string, unknown>;
}) {
  // 表单提交结果注入（target_panel 绑定）：结果对象按 kv 展示
  const sections = result
    ? [
        {
          kind: "kv" as const,
          title: "最新结果",
          items: Object.entries(result).map(([key, value]) => ({ label: key, value: formatCell(value) })),
        },
      ]
    : panel.sections;

  return (
    <PanelCard title={panel.title}>
      <div className="flex flex-col gap-3" data-slot="plugin-report">
        {sections.map((section, index) => (
          <div key={index}>
            {section.title ? (
              <p className="mb-1 text-xs font-medium text-muted-foreground">{section.title}</p>
            ) : null}
            {section.kind === "text" ? (
              <p className="text-sm text-foreground">{section.content}</p>
            ) : section.kind === "kv" ? (
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                {(section.items ?? []).map((item, i) => (
                  <div key={i} className="contents">
                    <dt className="text-muted-foreground">{item.label}</dt>
                    <dd className="text-foreground">{formatCell(item.value)}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <div className="flex flex-col gap-1">
                {(() => {
                  const items = section.items ?? [];
                  const max = Math.max(1, ...items.map((item) => Number(item.value) || 0));
                  return items.map((item, i) => (
                    <div key={i} className="flex items-center gap-2 text-sm">
                      <span className="w-24 shrink-0 truncate text-muted-foreground">{item.label}</span>
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-border/60">
                        <div
                          className="h-full rounded-full bg-primary/70"
                          style={{ width: `${((Number(item.value) || 0) / max) * 100}%` }}
                        />
                      </div>
                      <span className="w-12 text-right text-foreground">{formatCell(item.value)}</span>
                    </div>
                  ));
                })()}
              </div>
            )}
          </div>
        ))}
      </div>
    </PanelCard>
  );
}

/* ─────────────────────────── 公共小工具 ─────────────────────────── */

function extractRows(data: unknown): Record<string, unknown>[] {
  const asRows = (value: unknown): Record<string, unknown>[] =>
    Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null) : [];
  if (data && typeof data === "object" && "items" in data) {
    return asRows((data as { items: unknown }).items);
  }
  return asRows(data);
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/* ─────────────────────────── 插件 DSL 页面容器 ─────────────────────────── */

function getApiBase(): string {
  if (typeof window === "undefined") return "";
  return `http://${window.location.hostname}:8092`;
}

interface PluginDslViewProps {
  pluginId: string;
  /** DSL 页面 id，默认 home */
  pageId?: string;
}

/**
 * 按 pluginId 拉取 UI DSL 页面并渲染；DSL 不存在时回调 onMissing（保持"插件未找到"语义）。
 * 工具调用走 POST /plugins/{id}/tools/{tool}；审批闸 409 时弹确认后带 approved 重试。
 */
export function PluginDslView({ pluginId, pageId = "home", onMissing }: PluginDslViewProps & { onMissing?: () => void }) {
  const [page, setPage] = useState<DslPage | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setPage(null);
    setMissing(false);
    setError("");
    fetch(`${getApiBase()}/plugins/${pluginId}/ui/${pageId}`)
      .then(async (res) => {
        if (res.status === 404) {
          if (!cancelled) {
            setMissing(true);
            onMissing?.();
          }
          return null;
        }
        if (!res.ok) throw new Error(`DSL 加载失败（${res.status}）`);
        return res.json();
      })
      .then((raw) => {
        if (!raw || cancelled) return;
        const normalized = normalizeDslPage(raw);
        if (normalized) setPage(normalized);
        else {
          setMissing(true);
          onMissing?.();
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pluginId, pageId]);

  const toolCall = useCallback<ToolCall>(
    async (tool, args, approved = false) => {
      const shortName = tool.startsWith(`${pluginId}__`) ? tool.slice(pluginId.length + 2) : tool;
      const call = async (isApproved: boolean) =>
        fetch(`${getApiBase()}/plugins/${pluginId}/tools/${shortName}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ args, approved: isApproved }),
        });
      let res = await call(approved);
      if (res.status === 409) {
        const detail = await res.json().catch(() => null);
        const message = detail?.detail?.message ?? "该工具需要用户审批后方可调用";
        if (typeof window !== "undefined" && window.confirm(`${message}\n\n是否批准本次调用？`)) {
          res = await call(true);
        } else {
          throw new Error(message);
        }
      }
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(typeof detail?.detail === "string" ? detail.detail : `工具调用失败（${res.status}）`);
      }
      return res.json();
    },
    [pluginId]
  );

  if (missing) {
    return (
      <div className="flex flex-col items-center justify-center p-8 text-center">
        <div className="mb-4 text-6xl">🔌</div>
        <h2 className="mb-2 text-lg font-semibold">插件未找到</h2>
        <p className="text-muted-foreground">插件 “{pluginId}” 不存在或未启用</p>
      </div>
    );
  }
  if (error) {
    return <p className="p-8 text-sm text-destructive">{error}</p>;
  }
  if (!page) {
    return <p className="p-8 text-sm text-muted-foreground">加载插件页面…</p>;
  }
  return (
    <div className="h-full overflow-auto p-4">
      <div className="mx-auto flex max-w-3xl flex-col gap-4">
        <div>
          <h2 className="text-lg font-semibold text-foreground">{page.page.title}</h2>
          {page.page.description ? (
            <p className="mt-1 text-sm text-muted-foreground">{page.page.description}</p>
          ) : null}
        </div>
        <PanelRenderer page={page} toolCall={toolCall} />
      </div>
    </div>
  );
}
