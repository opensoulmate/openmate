"use client";

// 建流工坊（Flow Studio）— 建流零门槛三件套，/will 沉淀为工作台常驻入口：
// ① 一句话建流：自然语言 → LLM 生成流程定义 → 节点图实时预览 → 点头存为常驻流程
// ② 流程模板库：定时对账/文件监听处理/人工审批链/多 Agent 协作，一键套用改参数
// ③ 可视化编辑：触发器/节点配置/审批人全表单改参（不碰 YAML），YAML 仍可导出；
//    统一编辑器 FlowEditDialog（表单=主，JSON=高级模式）与 will 流程列表「编辑」同一入口。
// 数据口=flow-api.ts（acp-proxy :8092 /flows/studio*）；节点图=flow-graph.tsx 共用组件。
// 生成失败只报因（422 带报因），不做正则兜底假造。

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import {
  Sparkles, LayoutTemplate, Pencil, Save, FileDown, CheckCircle, XCircle,
  Loader2, Plus, Trash2, Copy, Download, RefreshCw, AlertTriangle, Wand2,
  Braces,
} from "lucide-react";
import { BranchGraph, type FlowDef, type FlowNode } from "./flow-graph";
import {
  flowApi, type FlowDefData, type FlowNodeSpec, type TemplateMeta, type TemplateParam,
} from "./flow-api";

type StudioMode = "nl" | "templates" | "editor";

const INPUT_CLS =
  "w-full rounded-lg border border-border bg-background px-3 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-primary/30";
const BTN_PRIMARY_CLS =
  "flex items-center gap-1.5 rounded-lg bg-primary/10 text-primary px-3 py-1.5 text-xs font-medium hover:bg-primary/20 transition-colors disabled:opacity-50";
const BTN_GHOST_CLS =
  "flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted transition-colors disabled:opacity-50";

const NL_EXAMPLE_KEYS = ["ExampleMp", "ExampleRecon", "ExampleFile", "ExampleApproval"] as const;

function lang3(i18nLang: string | undefined): "zh" | "en" | "ja" {
  const base = (i18nLang || "zh").split("-")[0];
  return base === "en" || base === "ja" ? base : "zh";
}

// ── 标准小控件（页面只传数据/回调，标记不外泄）──

function Field({ label, value, onChange, placeholder, mono, rows }: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder?: string; mono?: boolean; rows?: number;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-[10px] text-muted-foreground">{label}</span>
      {rows ? (
        <textarea
          value={value} onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder} rows={rows}
          className={cn(INPUT_CLS, "resize-y", mono && "font-mono")}
        />
      ) : (
        <input
          value={value} onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className={cn(INPUT_CLS, mono && "font-mono")}
        />
      )}
    </label>
  );
}

function NumField({ label, value, onChange, placeholder }: {
  label: string; value: number; onChange: (v: number) => void; placeholder?: string;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-[10px] text-muted-foreground">{label}</span>
      <input
        type="number" value={Number.isFinite(value) ? value : 0}
        onChange={(e) => onChange(Number(e.target.value))} placeholder={placeholder}
        className={INPUT_CLS}
      />
    </label>
  );
}

function SelectField({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void;
  options: Array<{ value: string; label: string }>;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-[10px] text-muted-foreground">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} className={INPUT_CLS}>
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
    </label>
  );
}

function JsonArea({ label, value, onChange }: {
  label: string; value: Record<string, unknown> | undefined; onChange: (v: Record<string, unknown>) => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2));
  const [bad, setBad] = useState(false);
  useEffect(() => {
    setText(JSON.stringify(value ?? {}, null, 2));
    setBad(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(value ?? {})]);
  return (
    <label className="block space-y-1">
      <span className="text-[10px] text-muted-foreground">{label}</span>
      <textarea
        value={text} rows={3}
        onChange={(e) => {
          setText(e.target.value);
          try {
            const parsed = JSON.parse(e.target.value || "{}");
            setBad(!(parsed && typeof parsed === "object" && !Array.isArray(parsed)));
            if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) onChange(parsed);
          } catch {
            setBad(true);
          }
        }}
        className={cn(INPUT_CLS, "font-mono resize-y", bad && "border-red-500/50")}
      />
    </label>
  );
}

function MessageBar({ message }: { message: { type: "ok" | "err"; text: string } | null }) {
  if (!message) return null;
  return (
    <div className={cn(
      "rounded-lg px-3 py-1.5 text-xs flex items-center gap-1.5",
      message.type === "ok"
        ? "bg-emerald-500/10 text-emerald-500"
        : "bg-red-500/10 text-red-500",
    )}>
      {message.type === "ok" ? <CheckCircle size={12} /> : <AlertTriangle size={12} />}
      {message.text}
    </div>
  );
}

// ── YAML 导出面板（导出/复制/下载）──

function YamlPanel({ yaml, title }: { yaml: string; title: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded-lg border border-border bg-muted/40">
      <div className="flex items-center justify-between gap-2 px-3 py-2">
        <span className="text-[10px] font-semibold text-muted-foreground">{title}</span>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => {
              navigator.clipboard?.writeText(yaml).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              });
            }}
            className={BTN_GHOST_CLS}
          >
            <Copy size={12} /> {copied ? t("will.studioCopied", "已复制") : t("will.studioCopy", "复制")}
          </button>
          <a
            href={`data:text/yaml;charset=utf-8,${encodeURIComponent(yaml)}`}
            download="flow.yaml"
            className={BTN_GHOST_CLS}
          >
            <Download size={12} /> {t("will.studioDownload", "下载 .yaml")}
          </a>
        </div>
      </div>
      <pre className="max-h-64 overflow-auto px-3 pb-3 text-[10px] font-mono text-foreground/80 whitespace-pre-wrap">
        {yaml}
      </pre>
    </div>
  );
}

// ── 预览卡（生成/模板/编辑共用：节点图上屏 + 改名 + 保存 + YAML）──

function PreviewCard({ definition: initial, onSaved, onRegenerate }: {
  definition: FlowDefData;
  onSaved: () => void;
  onRegenerate?: () => void;
}) {
  const { t } = useTranslation();
  const [defn, setDefn] = useState<FlowDefData>(initial);
  const [showYaml, setShowYaml] = useState(false);
  const [yamlText, setYamlText] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  useEffect(() => {
    setDefn(initial);
    setYamlText("");
    setShowYaml(false);
    setMessage(null);
  }, [initial]);

  const graphDef: FlowDef = useMemo(() => ({
    id: defn.id, name: defn.name, version: defn.version,
    triggers: defn.triggers, nodes: defn.nodes as FlowNode[],
  }), [defn]);

  const triggerText = (tr: { type: string; cron?: string; event?: string }) =>
    tr.type === "cron"
      ? `cron ${tr.cron}`
      : tr.type === "event"
        ? `${t("will.triggerEvent", "事件触发")} ${tr.event}`
        : t("will.triggerManual", "手动触发");

  const save = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const result = await flowApi.save({ ...defn, observability: defn.observability });
      setYamlText(result.yaml);
      setMessage({
        type: "ok",
        text: t("will.studioSaved", { id: result.flow_id, defaultValue: "已存为常驻流程 {{id}}（重启不丢）" }),
      });
      onSaved();
    } catch (e) {
      setMessage({ type: "err", text: t("will.studioSaveFailed", { msg: String(e), defaultValue: "保存失败：{{msg}}" }) });
    } finally {
      setSaving(false);
    }
  };

  const toggleYaml = async () => {
    if (showYaml) {
      setShowYaml(false);
      return;
    }
    // YAML 真源=后端 dump_flow：打开时实时取（含未保存的表单改动）
    try {
      const res = await flowApi.preview(defn);
      setYamlText(res.yaml);
      setShowYaml(true);
    } catch (e) {
      setMessage({ type: "err", text: String(e) });
    }
  };

  return (
    <div className="rounded-xl border border-primary/30 bg-card">
      <div className="flex items-center gap-2 p-3 lg:p-4 border-b border-border flex-wrap">
        <Wand2 size={16} className="text-primary shrink-0" />
        <div className="flex-1 min-w-[180px] grid grid-cols-2 gap-2">
          <Field label={t("will.studioName", "流程名称")} value={defn.name}
            onChange={(v) => setDefn({ ...defn, name: v })} />
          <Field label={t("will.studioFlowId", "流程 id")} value={defn.id} mono
            onChange={(v) => setDefn({ ...defn, id: v.trim() })} />
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          <button onClick={save} disabled={saving} className={BTN_PRIMARY_CLS}>
            {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
            {t("will.studioSave", "存为常驻流程")}
          </button>
          <button onClick={toggleYaml} className={BTN_GHOST_CLS}>
            <FileDown size={12} /> {t("will.studioViewYaml", "YAML")}
          </button>
          {onRegenerate && (
            <button onClick={onRegenerate} className={BTN_GHOST_CLS}>
              <RefreshCw size={12} /> {t("will.studioRegenerate", "重新生成")}
            </button>
          )}
        </div>
      </div>

      <div className="px-3 lg:px-4 pt-3 flex items-center gap-2 flex-wrap text-[10px] text-muted-foreground">
        {defn.triggers.map((tr, i) => (
          <span key={i} className="rounded bg-muted px-1.5 py-0.5">{triggerText(tr)}</span>
        ))}
        <span>{t("will.nodesCount", "节点")} {defn.nodes.length}</span>
        <span>v{defn.version}</span>
      </div>

      {/* 节点图实时预览（所见即所得） */}
      <div className="p-3 lg:p-4">
        <BranchGraph defn={graphDef} />
      </div>

      {showYaml && (
        <div className="px-3 lg:px-4 pb-3">
          <YamlPanel
            yaml={yamlText}
            title={t("will.studioYamlTitle", "流程定义 YAML（可导出）")}
          />
        </div>
      )}

      <div className="px-3 lg:px-4 pb-3">
        <MessageBar message={message} />
      </div>
    </div>
  );
}

// ── ① 一句话建流 ──

function NlPane({ onSaved }: { onSaved: () => void }) {
  const { t } = useTranslation();
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<FlowDefData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempts, setAttempts] = useState(0);

  const generate = async () => {
    if (!prompt.trim() || busy) return;
    setBusy(true);
    setError(null);
    setDraft(null);
    try {
      const result = await flowApi.draft(prompt.trim());
      setDraft(result.definition);
      setAttempts(result.attempts);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Sparkles size={16} className="text-primary" />
          {t("will.studioNlTitle", "一句话建流")}
          <span className="text-[10px] font-normal text-muted-foreground">
            {t("will.studioNlHint", "用自然语言描述需求，AI 生成流程定义，节点图上屏确认后存为常驻流程")}
          </span>
        </div>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={3}
          placeholder={t("will.studioNlPlaceholder", "例如：每天晚上检查公众号发布情况，漏发就提醒我")}
          className={cn(INPUT_CLS, "resize-y text-sm")}
        />
        <div className="flex items-center gap-1.5 flex-wrap">
          {NL_EXAMPLE_KEYS.map((key) => (
            <button
              key={key}
              onClick={() => setPrompt(t(`will.studio${key}`, ""))}
              className="rounded-lg bg-muted px-2.5 py-1 text-[10px] text-muted-foreground hover:bg-muted/70 hover:text-foreground transition-colors max-w-[280px] truncate"
            >
              {t(`will.studio${key}`, "")}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={generate} disabled={busy || !prompt.trim()} className={BTN_PRIMARY_CLS}>
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
            {busy ? t("will.studioNlGenerating", "生成中…（LLM 生成 + 引擎校验）") : t("will.studioNlGenerate", "生成流程")}
          </button>
          {attempts > 1 && !busy && (
            <span className="text-[10px] text-muted-foreground">
              {t("will.studioNlAttempts", { n: attempts, defaultValue: "第 {{n}} 次生成通过校验" })}
            </span>
          )}
        </div>
        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-xs text-red-500 flex items-start gap-2">
            <AlertTriangle size={14} className="shrink-0 mt-0.5" />
            <span className="break-all">{t("will.studioNlFailed", "生成失败")}：{error}</span>
          </div>
        )}
      </div>

      {draft && (
        <PreviewCard
          definition={draft}
          onSaved={onSaved}
          onRegenerate={generate}
        />
      )}
    </div>
  );
}

// ── ② 流程模板库 ──

function TemplatesPane({ onSaved }: { onSaved: () => void }) {
  const { t, i18n } = useTranslation();
  const lang = lang3(i18n.language);
  const [templates, setTemplates] = useState<TemplateMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [picked, setPicked] = useState<TemplateMeta | null>(null);
  const [params, setParams] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<FlowDefData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    flowApi.templates()
      .then((res) => setTemplates(res.templates || []))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  const pick = (tpl: TemplateMeta) => {
    setPicked(tpl);
    setDraft(null);
    setError(null);
    setParams(Object.fromEntries(tpl.params.map((p) => [p.key, p.default])));
  };

  const instantiate = async () => {
    if (!picked) return;
    setBusy(true);
    setError(null);
    try {
      const result = await flowApi.instantiate(picked.id, params);
      setDraft(result.definition);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <LayoutTemplate size={16} className="text-primary" />
          {t("will.studioTplTitle", "流程模板库")}
          <span className="text-[10px] font-normal text-muted-foreground">
            {t("will.studioTplHint", "常用场景一键套用，改参数即用（定时对账/文件监听/人工审批链/多 Agent 协作）")}
          </span>
        </div>
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 size={12} className="animate-spin" /> {t("will.loading", "加载中…")}
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
            {templates.map((tpl) => (
              <button
                key={tpl.id}
                onClick={() => pick(tpl)}
                className={cn(
                  "rounded-lg border p-3 text-left transition-colors",
                  picked?.id === tpl.id
                    ? "border-primary/50 bg-primary/5"
                    : "border-border hover:bg-muted/40",
                )}
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="rounded bg-primary/10 text-primary px-1.5 py-0.5 text-[10px]">
                    {tpl.scene[lang]}
                  </span>
                  <span className="text-xs font-medium">{tpl.name[lang]}</span>
                  <code className="text-[10px] text-muted-foreground bg-muted rounded px-1.5 py-0.5">{tpl.id}</code>
                </div>
                <p className="mt-1 text-[10px] text-muted-foreground">{tpl.description[lang]}</p>
              </button>
            ))}
          </div>
        )}
      </div>

      {picked && (
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
          <div className="text-xs font-semibold">
            {t("will.studioTplParams", "套用参数")} · {picked.name[lang]}
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
            {picked.params.map((p: TemplateParam) => (
              <Field
                key={p.key}
                label={p.label[lang] || p.key}
                value={params[p.key] ?? ""}
                onChange={(v) => setParams({ ...params, [p.key]: v })}
                mono={p.type === "cron" || p.key.includes("tool") || p.key.includes("expr")}
              />
            ))}
          </div>
          <div className="flex items-center gap-2">
            <button onClick={instantiate} disabled={busy} className={BTN_PRIMARY_CLS}>
              {busy ? <Loader2 size={12} className="animate-spin" /> : <LayoutTemplate size={12} />}
              {t("will.studioTplInstantiate", "生成预览")}
            </button>
          </div>
          {error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-xs text-red-500 break-all">
              {error}
            </div>
          )}
        </div>
      )}

      {draft && (
        <PreviewCard
          definition={draft}
          onSaved={onSaved}
          onRegenerate={picked ? instantiate : undefined}
        />
      )}
    </div>
  );
}

// ── ③ 可视化编辑（不碰 YAML；YAML 仍可导出）──

function NodeEditor({ node, index, onChange }: {
  node: FlowNodeSpec; index: number; onChange: (patch: Partial<FlowNodeSpec>) => void;
}) {
  const { t } = useTranslation();
  const setRetry = (patch: Partial<NonNullable<FlowNodeSpec["retry"]>>) =>
    onChange({ retry: { max: 0, backoff: "fixed", base_seconds: 1, ...node.retry, ...patch } });

  return (
    <div className="rounded-lg border border-border bg-muted/30 p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="rounded bg-primary/10 text-primary px-1.5 py-0.5 text-[10px] font-medium">
          #{index + 1}
        </span>
        <code className="text-xs font-semibold">{node.id}</code>
        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{node.type}</span>
      </div>

      {/* 通用参数 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        <NumField label={t("will.studioEdTimeout", "软超时（秒，只记不杀）")}
          value={node.timeout_seconds ?? 0}
          onChange={(v) => onChange({ timeout_seconds: v })} />
        <SelectField label={t("will.studioEdOnError", "失败处置")}
          value={node.on_error || "fail"}
          onChange={(v) => onChange({ on_error: v })}
          options={[
            { value: "fail", label: "fail" },
            { value: "skip", label: "skip" },
          ]} />
        <NumField label={t("will.studioEdRetryMax", "重试次数")}
          value={node.retry?.max ?? 0} onChange={(v) => setRetry({ max: v })} />
        <SelectField label={t("will.studioEdBackoff", "退避")}
          value={node.retry?.backoff || "fixed"}
          onChange={(v) => setRetry({ backoff: v })}
          options={[{ value: "fixed", label: "fixed" }, { value: "exponential", label: "exponential" }]} />
      </div>

      {/* 分类型参数 */}
      {node.type === "tool" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
          <Field label={t("will.studioEdTool", "工具（plugin__tool）")} value={node.tool || ""}
            mono onChange={(v) => onChange({ tool: v })} />
          <JsonArea label={t("will.studioEdArgs", "参数（JSON，$.路径可引用上游）")}
            value={node.args} onChange={(v) => onChange({ args: v })} />
        </div>
      )}

      {node.type === "decision" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-2">
          <Field label={t("will.studioEdWhen", "条件表达式 when")} value={node.when || ""} mono
            onChange={(v) => onChange({ when: v })} />
          <Field label={t("will.studioEdThen", "成立 → 节点 id")} value={node.then || ""} mono
            onChange={(v) => onChange({ then: v })} />
          <Field label={t("will.studioEdElse", "不成立 → 节点 id")} value={node.else || ""} mono
            onChange={(v) => onChange({ else: v })} />
        </div>
      )}

      {node.type === "loop" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
          <Field label={t("will.studioEdOver", "遍历来源 over")} value={node.over || ""} mono
            onChange={(v) => onChange({ over: v })} />
          <Field label={t("will.studioEdBody", "body 节点 id（逗号分隔）")}
            value={(node.body || []).join(", ")} mono
            onChange={(v) => onChange({ body: v.split(",").map((s) => s.trim()).filter(Boolean) })} />
        </div>
      )}

      {(node.type === "approval" || node.type === "human" || node.type === "agent") && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
          {node.type !== "approval" && (
            <>
              <Field label={t("will.studioEdTask", "任务指令")} value={node.task || ""} rows={2}
                onChange={(v) => onChange({ task: v })} />
              <Field label={t("will.studioEdUser", "审批人/收件人 user")} value={node.user || ""}
                onChange={(v) => onChange({ user: v })} />
              <Field label={t("will.studioEdRole", "组织角色 role")} value={node.role || ""}
                onChange={(v) => onChange({ role: v })} />
            </>
          )}
          <Field label={t("will.studioEdForm", "表单/交付说明")} value={node.form || ""} rows={2}
            onChange={(v) => onChange({ form: v })} />
          <NumField label={t("will.studioEdTimeoutHours", "期限（小时）")}
            value={node.timeout_hours ?? 72} onChange={(v) => onChange({ timeout_hours: v })} />
          <SelectField label={t("will.studioEdOnTimeout", "超时处置")}
            value={node.on_timeout || "fail"}
            onChange={(v) => onChange({ on_timeout: v })}
            options={[{ value: "fail", label: "fail" }, { value: "skip", label: "skip" }]} />
          {node.type === "agent" && (
            <Field label={t("will.studioEdTarget", "目标 Agent")} value={node.target_agent || "soulmate"}
              onChange={(v) => onChange({ target_agent: v })} />
          )}
          <label className="flex items-center gap-2 text-[10px] text-muted-foreground pt-4">
            <input
              type="checkbox" checked={!!node.review}
              onChange={(e) => onChange({ review: e.target.checked })}
              className="rounded border-border"
            />
            {t("will.studioEdReview", "验收门（驳回不算完成）")}
          </label>
        </div>
      )}

      {node.type === "action" && (
        <div className="space-y-2">
          {(node.actions || []).map((action, ai) => (
            <div key={ai} className="grid grid-cols-2 lg:grid-cols-4 gap-2 items-end">
              <SelectField label={t("will.studioEdActionType", "动作类型")}
                value={action.type}
                onChange={(v) => {
                  const actions = [...(node.actions || [])];
                  actions[ai] = { ...actions[ai], type: v };
                  onChange({ actions });
                }}
                options={[
                  { value: "notify", label: "notify" },
                  { value: "emit-event", label: "emit-event" },
                  { value: "update-panel", label: "update-panel" },
                  { value: "compress-handoff", label: "compress-handoff" },
                ]} />
              <Field label={t("will.studioEdChannel", "渠道 channel")} value={action.channel || ""}
                onChange={(v) => {
                  const actions = [...(node.actions || [])];
                  actions[ai] = { ...actions[ai], channel: v };
                  onChange({ actions });
                }} />
              <Field label={t("will.studioEdMessage", "消息 message")} value={action.message || ""}
                onChange={(v) => {
                  const actions = [...(node.actions || [])];
                  actions[ai] = { ...actions[ai], message: v };
                  onChange({ actions });
                }} />
              <button
                onClick={() => onChange({ actions: (node.actions || []).filter((_, ri) => ri !== ai) })}
                className={cn(BTN_GHOST_CLS, "text-red-500")}
              >
                <Trash2 size={12} /> {t("will.studioRemove", "删除")}
              </button>
            </div>
          ))}
          <button
            onClick={() => onChange({
              actions: [...(node.actions || []), { type: "notify", channel: "in-app", message: "" }],
            })}
            className={BTN_GHOST_CLS}
          >
            <Plus size={12} /> {t("will.studioEdAddAction", "加动作")}
          </button>
        </div>
      )}
    </div>
  );
}

function TriggerEditor({ triggers, onChange }: {
  triggers: FlowDefData["triggers"];
  onChange: (triggers: FlowDefData["triggers"]) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2">
      <div className="text-xs font-semibold text-muted-foreground">
        {t("will.studioEdTriggers", "触发器")}
      </div>
      {triggers.map((tr, i) => (
        <div key={i} className="grid grid-cols-2 lg:grid-cols-4 gap-2 items-end">
          <SelectField label={t("will.studioEdTriggerType", "类型")}
            value={tr.type}
            onChange={(v) => {
              const next = [...triggers];
              next[i] = {
                type: v,
                cron: v === "cron" ? (tr.cron || "0 9 * * *") : undefined,
                event: v === "event" ? (tr.event || "") : undefined,
              };
              onChange(next);
            }}
            options={[
              { value: "cron", label: "cron" },
              { value: "event", label: "event" },
              { value: "manual", label: "manual" },
            ]} />
          {tr.type === "cron" && (
            <Field label={t("will.studioEdCron", "cron（分 时 日 月 周）")} value={tr.cron || ""} mono
              onChange={(v) => { const next = [...triggers]; next[i] = { ...tr, cron: v }; onChange(next); }} />
          )}
          {tr.type === "event" && (
            <Field label={t("will.studioEdEvent", "事件名")} value={tr.event || ""} mono
              onChange={(v) => { const next = [...triggers]; next[i] = { ...tr, event: v }; onChange(next); }} />
          )}
          <button
            onClick={() => onChange(triggers.filter((_, ri) => ri !== i))}
            className={cn(BTN_GHOST_CLS, "text-red-500")}
          >
            <Trash2 size={12} /> {t("will.studioRemove", "删除")}
          </button>
        </div>
      ))}
      <button
        onClick={() => onChange([...triggers, { type: "manual" }])}
        className={BTN_GHOST_CLS}
      >
        <Plus size={12} /> {t("will.studioEdAddTrigger", "加触发器")}
      </button>
    </div>
  );
}

// ── 统一流程编辑器：表单=主，JSON=高级模式（同一入口）——
//    will 流程列表「编辑」与建流工坊「可视化编辑」都进这里，不再有第二套 JSON 对话框 ──

export function FlowEditDialog({ flow, onClose, onSaved, showMsg }: {
  flow: { id: string; name: string; plugin_id?: string };
  onClose: () => void;
  onSaved: () => void;
  showMsg?: (type: "ok" | "err", text: string) => void;
}) {
  const { t } = useTranslation();
  const [defn, setDefn] = useState<FlowDefData | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [triggersText, setTriggersText] = useState("");
  const [nodesText, setNodesText] = useState("");
  const [jsonErr, setJsonErr] = useState("");
  const [showYaml, setShowYaml] = useState(false);
  const [yamlText, setYamlText] = useState("");

  useEffect(() => {
    let alive = true;
    setBusy(true);
    flowApi.getFlow(flow.id)
      .then((raw) => {
        if (!alive) return;
        const d = normalizeDetail(raw);
        setDefn(d);
        setTriggersText(JSON.stringify(d.triggers ?? [], null, 2));
        setNodesText(JSON.stringify(d.nodes ?? [], null, 2));
      })
      .catch((e) => { if (alive) setMessage({ type: "err", text: String(e) }); })
      .finally(() => { if (alive) setBusy(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flow.id]);

  // 高级模式 JSON → 定义（合法返回合并定义；不合法只报因，绝不覆盖表单状态）
  const collect = (): FlowDefData | null => {
    if (!defn) return null;
    if (!advanced) return defn;
    try {
      const triggers = JSON.parse(triggersText || "[]");
      const nodes = JSON.parse(nodesText || "[]");
      if (!Array.isArray(triggers) || !Array.isArray(nodes)) throw new Error("not array");
      setJsonErr("");
      return {
        ...defn,
        triggers: triggers as FlowDefData["triggers"],
        nodes: nodes as FlowNodeSpec[],
      };
    } catch {
      setJsonErr(t("will.studioEdJsonInvalid", "JSON 不合法（触发器/节点均须为数组）"));
      return null;
    }
  };

  const patchNode = (index: number, patch: Partial<FlowNodeSpec>) => {
    if (!defn) return;
    const nodes = [...defn.nodes];
    nodes[index] = { ...nodes[index], ...patch };
    setDefn({ ...defn, nodes });
  };

  const validate = async () => {
    const d = collect();
    if (!d) return;
    setBusy(true);
    try {
      const res = await flowApi.preview(d);
      setMessage({
        type: "ok",
        text: t("will.studioValidOk", { id: res.flow_id, defaultValue: "校验通过：{{id}}" }),
      });
    } catch (e) {
      setMessage({ type: "err", text: String(e) });
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    const d = collect();
    if (!d) return;
    setBusy(true);
    try {
      await flowApi.update(d.id, {
        name: d.name,
        triggers: d.triggers,
        nodes: d.nodes,
        retain_runs: d.observability?.retain_runs ?? 50,
      });
      const text = t("will.savedOk", "参数已保存");
      setMessage({ type: "ok", text });
      showMsg?.("ok", text);
      onSaved();
    } catch (e) {
      const msg = String(e instanceof Error ? e.message : e);
      setMessage({ type: "err", text: msg });
      showMsg?.("err", msg);
    } finally {
      setBusy(false);
    }
  };

  const exportYaml = async () => {
    setBusy(true);
    try {
      const res = await flowApi.exportYaml(flow.id);
      setYamlText(res.yaml);
      setShowYaml(true);
    } catch (e) {
      setMessage({ type: "err", text: String(e) });
    } finally {
      setBusy(false);
    }
  };

  const toAdvanced = () => {
    if (!defn) return;
    setTriggersText(JSON.stringify(defn.triggers ?? [], null, 2));
    setNodesText(JSON.stringify(defn.nodes ?? [], null, 2));
    setJsonErr("");
    setAdvanced(true);
  };
  const toForm = () => {
    const d = collect();
    if (!d) return; // JSON 不合法：留在高级模式改到合法为止
    setDefn(d);
    setJsonErr("");
    setAdvanced(false);
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <div className="rounded-xl border border-border bg-card w-full max-w-3xl p-4 space-y-3 max-h-[88vh] overflow-y-auto">
        <div className="flex items-center gap-2 flex-wrap">
          <Pencil size={15} className="text-primary shrink-0" />
          <div className="text-sm font-semibold">
            {t("will.studioEdDialogTitle", "编辑流程")} ·{" "}
            <code className="text-xs text-muted-foreground">{flow.id}</code>
          </div>
          <div className="flex-1" />
          {/* 同一编辑器两个模式：表单=主，JSON=高级模式 */}
          <div className="flex items-center gap-1 rounded-lg bg-muted p-1">
            <button
              onClick={toForm}
              disabled={!advanced}
              className={cn(
                "flex items-center gap-1 rounded-md px-3 py-1 text-[10px] font-medium transition-colors disabled:opacity-60",
                !advanced ? "bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Wand2 size={11} /> {t("will.studioEdFormMode", "表单模式")}
            </button>
            <button
              onClick={toAdvanced}
              disabled={advanced}
              className={cn(
                "flex items-center gap-1 rounded-md px-3 py-1 text-[10px] font-medium transition-colors disabled:opacity-60",
                advanced ? "bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Braces size={11} /> {t("will.studioEdAdvanced", "高级模式（JSON）")}
            </button>
          </div>
        </div>

        {!defn ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 size={12} className="animate-spin" /> {t("will.loading", "加载中…")}
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
              <Field label={t("will.studioName", "流程名称")} value={defn.name}
                onChange={(v) => setDefn({ ...defn, name: v })} />
              <label className="block space-y-1">
                <span className="text-[10px] text-muted-foreground">{t("will.studioFlowId", "流程 id")}</span>
                <input value={defn.id} readOnly
                  className={cn(INPUT_CLS, "opacity-60 cursor-not-allowed")} />
              </label>
              <NumField label={t("will.studioEdRetain", "保留实例数 retain_runs")}
                value={defn.observability?.retain_runs ?? 50}
                onChange={(v) => setDefn({ ...defn, observability: { retain_runs: v } })} />
              <div className="text-[10px] text-muted-foreground self-end pb-1.5">
                {advanced
                  ? t("will.studioEdAdvancedHint", "高级模式：触发器/节点全量 JSON 直改（表单未覆盖的字段从这里改），仍走同一校验与保存")
                  : t("will.studioEdHint", "改触发器/节点参数/审批人，不用碰 YAML；YAML 仍可导出")}
              </div>
            </div>

            {advanced ? (
              <>
                <label className="block space-y-1">
                  <span className="text-xs text-muted-foreground">
                    {t("will.triggersJson", "触发器（JSON 数组）")}
                  </span>
                  <textarea
                    value={triggersText}
                    onChange={(e) => { setTriggersText(e.target.value); setJsonErr(""); }}
                    rows={3} spellCheck={false}
                    className={cn(INPUT_CLS, "font-mono resize-y", jsonErr && "border-red-500/50")}
                  />
                </label>
                <label className="block space-y-1">
                  <span className="text-xs text-muted-foreground">
                    {t("will.nodesJson", "节点全量配置（JSON 数组：tool/args/when/then…）")}
                  </span>
                  <textarea
                    value={nodesText}
                    onChange={(e) => { setNodesText(e.target.value); setJsonErr(""); }}
                    rows={12} spellCheck={false}
                    className={cn(INPUT_CLS, "font-mono resize-y", jsonErr && "border-red-500/50")}
                  />
                </label>
                {jsonErr && (
                  <div className="rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2 text-xs text-red-500 flex items-start gap-2">
                    <AlertTriangle size={12} className="mt-0.5 shrink-0" /> {jsonErr}
                  </div>
                )}
              </>
            ) : (
              <>
                <TriggerEditor triggers={defn.triggers}
                  onChange={(triggers) => setDefn({ ...defn, triggers })} />
                <div className="space-y-2">
                  <div className="text-xs font-semibold text-muted-foreground">
                    {t("will.studioEdNodes", "节点配置")}
                  </div>
                  {defn.nodes.map((node, i) => (
                    <NodeEditor key={`${node.id}-${i}`} node={node} index={i}
                      onChange={(patch) => patchNode(i, patch)} />
                  ))}
                </div>
              </>
            )}

            <div className="flex items-center gap-2 flex-wrap">
              <button onClick={validate} disabled={busy} className={BTN_GHOST_CLS}>
                {busy ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle size={12} />}
                {t("will.studioValidate", "校验")}
              </button>
              <button onClick={save} disabled={busy} className={BTN_PRIMARY_CLS}>
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                {t("will.save", "保存")}
              </button>
              <button onClick={exportYaml} disabled={busy} className={BTN_GHOST_CLS}>
                <FileDown size={12} /> {t("will.studioExportYaml", "导出 YAML")}
              </button>
              <div className="flex-1" />
              <button onClick={onClose} className={BTN_GHOST_CLS}>
                {t("will.cancel", "取消")}
              </button>
            </div>

            {showYaml && (
              <YamlPanel yaml={yamlText} title={t("will.studioYamlTitle", "流程定义 YAML（可导出）")} />
            )}
            <MessageBar message={message} />
          </>
        )}
      </div>
    </div>
  );
}

// ── ③ 可视化编辑（入口：点流程 → 统一编辑器 FlowEditDialog）──

function EditorPane({ flows, onSaved }: {
  flows: Array<{ id: string; name: string; plugin_id: string }>;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [picked, setPicked] = useState<{ id: string; name: string; plugin_id: string } | null>(null);

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Pencil size={16} className="text-primary" />
          {t("will.studioEdTitle", "可视化编辑")}
          <span className="text-[10px] font-normal text-muted-foreground">
            {t("will.studioEdHint", "改触发器/节点参数/审批人，不用碰 YAML；YAML 仍可导出")}
          </span>
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          {flows.map((flow) => (
            <button
              key={flow.id}
              onClick={() => setPicked(flow)}
              className="rounded-lg border border-border px-2.5 py-1.5 text-[10px] text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
            >
              {flow.name}
              <code className="ml-1 opacity-60">{flow.id}</code>
            </button>
          ))}
          {flows.length === 0 && (
            <span className="text-[10px] text-muted-foreground">
              {t("will.studioEdEmpty", "暂无流程——先用一句话建流或模板库创建")}
            </span>
          )}
        </div>
        <div className="text-[10px] text-muted-foreground">
          {t("will.studioEdEntryHint", "点流程打开统一编辑器：表单改参为主，右上角可切「高级模式（JSON）」直改全量配置")}
        </div>
      </div>

      {picked && (
        <FlowEditDialog
          flow={picked}
          onClose={() => setPicked(null)}
          onSaved={() => { setPicked(null); onSaved(); }}
        />
      )}
    </div>
  );
}

// flow_api asdict 原始形状 → 规范编辑字典（else_ → else；retain_runs → observability）
function normalizeDetail(raw: Record<string, unknown>): FlowDefData {
  const rawNodes = (raw.nodes as Array<Record<string, unknown>>) || [];
  const nodes = rawNodes.map((n) => {
    const { else_: elseLegacy, ...rest } = n;
    return {
      ...rest,
      else: (rest.else as string | undefined) ?? (elseLegacy as string | undefined),
    } as FlowNodeSpec;
  });
  const retain = (raw.retain_runs as number | undefined)
    ?? (raw.observability as { retain_runs?: number } | undefined)?.retain_runs;
  return {
    id: raw.id as string,
    name: raw.name as string,
    version: (raw.version as number) ?? 1,
    triggers: (raw.triggers as FlowDefData["triggers"]) || [],
    nodes,
    observability: { retain_runs: retain ?? 50 },
  };
}

// ── 主组件（/will 页挂载）──

export function FlowStudio({ flows, onSaved }: {
  flows: Array<{ id: string; name: string; plugin_id: string }>;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<StudioMode>("nl");

  const modes: Array<{ id: StudioMode; label: string; icon: typeof Sparkles }> = [
    { id: "nl", label: t("will.studioNlTab", "一句话建流"), icon: Sparkles },
    { id: "templates", label: t("will.studioTplTab", "模板库"), icon: LayoutTemplate },
    { id: "editor", label: t("will.studioEdTab", "可视化编辑"), icon: Pencil },
  ];

  return (
    <div className="space-y-3">
      {/* 分段选择（选中态圆角高亮，禁下划线） */}
      <div className="flex items-center gap-1 rounded-lg bg-muted p-1 w-fit">
        {modes.map((item) => (
          <button
            key={item.id}
            onClick={() => setMode(item.id)}
            className={cn(
              "flex items-center gap-1.5 rounded-md px-4 py-1.5 text-xs font-medium transition-colors",
              mode === item.id
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <item.icon size={12} />
            {item.label}
          </button>
        ))}
      </div>

      {mode === "nl" && <NlPane onSaved={onSaved} />}
      {mode === "templates" && <TemplatesPane onSaved={onSaved} />}
      {mode === "editor" && <EditorPane flows={flows} onSaved={onSaved} />}
    </div>
  );
}
