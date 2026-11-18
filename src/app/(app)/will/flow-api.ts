// 建流零门槛 — acp-proxy :8092 流程 API 客户端（will 页 / flow-studio 共用取数口）
// 与 will-client 同款直连（flow 引擎宿主 :8092，CORS *）。

export function acpBase(): string {
  if (typeof window === "undefined") return "http://127.0.0.1:8092";
  return `http://${window.location.hostname}:8092`;
}

// ── 规范流程定义（flow_def.dump_flow 单一真源的外键名形状）──

export interface TriggerSpec {
  type: string; // cron | event | manual
  cron?: string;
  event?: string;
}

export interface ActionSpec {
  type: string;
  channel?: string;
  message?: string;
  event?: string;
  panel?: string;
}

export interface FlowNodeSpec {
  id: string;
  type: string; // tool | decision | loop | approval | action | parallel | agent | human
  tool?: string;
  args?: Record<string, unknown>;
  retry?: { max?: number; backoff?: string; base_seconds?: number };
  timeout_seconds?: number;
  on_error?: string;
  when?: string;
  then?: string;
  else?: string;
  over?: string;
  body?: string[];
  form?: string;
  timeout_hours?: number;
  on_timeout?: string;
  actions?: ActionSpec[];
  branches?: Array<{ id: string; tool: string; args?: Record<string, unknown> }>;
  max_concurrency?: number;
  task?: string;
  user?: string;
  role?: string;
  target_agent?: string;
  budget?: { max_tokens?: number; max_cost_usd?: number; max_duration_seconds?: number };
  package?: string;
  review?: boolean;
}

export interface FlowDefData {
  id: string;
  name: string;
  version: number;
  triggers: TriggerSpec[];
  nodes: FlowNodeSpec[];
  observability?: { retain_runs?: number };
}

export interface TemplateParam {
  key: string;
  type: string; // text | cron | number
  default: string;
  label: { zh: string; en: string; ja: string };
}

export interface TemplateMeta {
  id: string;
  name: { zh: string; en: string; ja: string };
  description: { zh: string; en: string; ja: string };
  scene: { zh: string; en: string; ja: string };
  params: TemplateParam[];
}

export interface DraftResult {
  ok: boolean;
  definition: FlowDefData;
  yaml: string;
  flow_id: string;
  name: string;
  attempts: number;
}

export interface SaveResult {
  flow_id: string;
  name: string;
  version: number;
  nodes: number;
  triggers: string[];
  path: string;
  yaml: string;
  definition: FlowDefData;
}

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${acpBase()}${path}`, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail =
      typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail || data);
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return data as T;
}

const post = (path: string, body: unknown) =>
  jsonFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const flowApi = {
  templates: () => jsonFetch<{ templates: TemplateMeta[]; count: number }>("/flows/templates"),
  instantiate: (templateId: string, params: Record<string, string>) =>
    post("/flows/templates/instantiate", { template_id: templateId, params }) as Promise<{
      definition: FlowDefData; yaml: string; flow_id: string; name: string;
    }>,
  draft: (prompt: string) => post("/flows/draft", { prompt }) as Promise<DraftResult>,
  preview: (definition: FlowDefData) =>
    post("/flows/preview", { definition }) as Promise<{
      ok: boolean; flow_id: string; name: string; definition: FlowDefData; yaml: string;
    }>,
  save: (definition: FlowDefData) => post("/flows/save", { definition }) as Promise<SaveResult>,
  exportYaml: (flowId: string) =>
    jsonFetch<{ flow_id: string; yaml: string; definition: FlowDefData }>(
      `/flows/${encodeURIComponent(flowId)}/yaml`),
  getFlow: (flowId: string) =>
    jsonFetch<Record<string, unknown>>(`/flows/${encodeURIComponent(flowId)}`),
};
