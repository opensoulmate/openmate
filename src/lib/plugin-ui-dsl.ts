/**
 * UI DSL 类型 + 归一化 — 声明式 UI DSL（层①）前端侧
 *
 * 设计文档: docs/plugin-system-design.md §2
 * JSON 清单 → 宿主原生渲染；插件不写前端代码。
 * 双端校验：宿主装载时 plugin/ui_dsl.py 拒绝非法 DSL；这里做归一化与前向兼容兜底。
 */

export interface DslFieldOption {
  value: string;
  label: string;
}

export interface DslField {
  name: string;
  label: string;
  widget: "text" | "textarea" | "number" | "select" | "checkbox" | "date";
  required?: boolean;
  placeholder?: string;
  default?: string | number | boolean | null;
  options?: DslFieldOption[];
}

export interface DslSubmit {
  label?: string;
  tool: string;
  target_panel?: string;
}

export interface DslFormPanel {
  type: "form";
  id: string;
  title: string;
  fields: DslField[];
  submit?: DslSubmit;
}

export interface DslListColumn {
  key: string;
  label: string;
}

export interface DslListPanel {
  type: "list";
  id: string;
  title: string;
  columns: DslListColumn[];
  source?: { tool: string; args?: Record<string, unknown> };
  empty_text?: string;
  /** 实时面板：按秒轮询刷新（≥2 生效；0/缺省=只在提交后刷新） */
  auto_refresh_seconds?: number;
}

export interface DslReportItem {
  label: string;
  value?: string | number | boolean | null;
}

export interface DslReportSection {
  kind: "kv" | "text" | "bars";
  title?: string;
  content?: string;
  items?: DslReportItem[];
}

export interface DslReportPanel {
  type: "report";
  id: string;
  title: string;
  sections: DslReportSection[];
}

export type DslPanel = DslFormPanel | DslListPanel | DslReportPanel;

export interface DslPage {
  version: 1;
  page: { title: string; description?: string };
  panels: DslPanel[];
}

const WIDGETS = new Set(["text", "textarea", "number", "select", "checkbox", "date"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeField(raw: unknown): DslField | null {
  if (!isRecord(raw)) return null;
  const name = raw.name;
  const widget = typeof raw.widget === "string" && WIDGETS.has(raw.widget) ? (raw.widget as DslField["widget"]) : "text";
  if (typeof name !== "string" || !name) return null;
  const field: DslField = {
    name,
    label: typeof raw.label === "string" ? raw.label : name,
    widget,
    required: Boolean(raw.required),
    placeholder: typeof raw.placeholder === "string" ? raw.placeholder : "",
    default: (raw.default as DslField["default"]) ?? null,
    options: Array.isArray(raw.options)
      ? raw.options.filter(isRecord).map((opt) => ({
          value: String(opt.value ?? ""),
          label: String(opt.label ?? opt.value ?? ""),
        }))
      : [],
  };
  return field;
}

function normalizePanel(raw: unknown): DslPanel | null {
  if (!isRecord(raw)) return null;
  const id = raw.id;
  const type = raw.type;
  if (typeof id !== "string" || !id) return null;
  const title = typeof raw.title === "string" ? raw.title : id;
  if (type === "form") {
    const submit = isRecord(raw.submit) ? raw.submit : undefined;
    return {
      type: "form",
      id,
      title,
      fields: Array.isArray(raw.fields) ? raw.fields.map(normalizeField).filter((f): f is DslField => f !== null) : [],
      submit: submit && typeof submit.tool === "string"
        ? {
            label: typeof submit.label === "string" ? submit.label : "提交",
            tool: submit.tool,
            target_panel: typeof submit.target_panel === "string" ? submit.target_panel : "",
          }
        : undefined,
    };
  }
  if (type === "list") {
    const source = isRecord(raw.source) ? raw.source : undefined;
    return {
      type: "list",
      id,
      title,
      columns: Array.isArray(raw.columns)
        ? raw.columns.filter(isRecord).map((col) => ({ key: String(col.key ?? ""), label: String(col.label ?? col.key ?? "") }))
        : [],
      source: source && typeof source.tool === "string"
        ? { tool: source.tool, args: isRecord(source.args) ? source.args : {} }
        : undefined,
      empty_text: typeof raw.empty_text === "string" ? raw.empty_text : "暂无数据",
      auto_refresh_seconds:
        typeof raw.auto_refresh_seconds === "number" && raw.auto_refresh_seconds >= 2
          ? Math.floor(raw.auto_refresh_seconds)
          : 0,
    };
  }
  if (type === "report") {
    return {
      type: "report",
      id,
      title,
      sections: Array.isArray(raw.sections) ? raw.sections.filter(isRecord).map((sec) => ({
        kind: (sec.kind === "kv" || sec.kind === "bars" ? sec.kind : "text") as DslReportSection["kind"],
        title: typeof sec.title === "string" ? sec.title : "",
        content: typeof sec.content === "string" ? sec.content : "",
        items: Array.isArray(sec.items)
          ? sec.items.filter(isRecord).map((item) => ({ label: String(item.label ?? ""), value: item.value as DslReportItem["value"] }))
          : [],
      })) : [],
    };
  }
  return null; // 未知面板类型：丢弃（前向兼容，不整页失败）
}

/** 归一化 DSL 页面；结构不合法返回 null，非法面板逐个丢弃 */
export function normalizeDslPage(raw: unknown): DslPage | null {
  if (!isRecord(raw)) return null;
  if (raw.version !== 1) return null;
  const page = isRecord(raw.page) ? raw.page : {};
  if (!Array.isArray(raw.panels)) return null;
  const panels = raw.panels.map(normalizePanel).filter((p): p is DslPanel => p !== null);
  if (panels.length === 0) return null;
  return {
    version: 1,
    page: {
      title: typeof page.title === "string" ? page.title : "",
      description: typeof page.description === "string" ? page.description : "",
    },
    panels,
  };
}
