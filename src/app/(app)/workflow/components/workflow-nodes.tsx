"use client";

import { memo, useEffect, type CSSProperties } from "react";
import {
  Handle,
  Position,
  useStore,
  useUpdateNodeInternals,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { useTranslation } from "react-i18next";
import i18n from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { WorkflowNodeData, WorkflowNodeType } from "@/stores/workflow-store";
import {
  Play,
  Bot,
  Wrench,
  GitBranch,
  Repeat,
  Code2,
  Database,
  Square,
  Loader2,
  Globe,
  Bell,
  Zap,
  Terminal,
} from "lucide-react";

// ── 类型语义分类（设计系统：主强调 1 + 语义 4，与全站 token 对齐）─────────
// ai=主强调紫(primary) / trigger=success / action=info / logic=warning / end=error
// 节点只用语义色做「色条+图标+微染底」，文字一律走前景/次级文字层级（WCAG AA）。
export type NodeSemantic = "trigger" | "ai" | "action" | "logic" | "end";

export const nodeSemantic: Record<WorkflowNodeType, NodeSemantic> = {
  start: "trigger",
  llm: "ai",
  knowledge: "ai",
  tool: "action",
  http: "action",
  code: "action",
  script: "action",
  organ: "action",
  notify: "action",
  condition: "logic",
  loop: "logic",
  end: "end",
};

/** 语义色 CSS 变量（SVG/样式通用；随深浅主题联动） */
export const semanticVar: Record<NodeSemantic, string> = {
  trigger: "var(--color-success)",
  ai: "var(--color-primary)",
  action: "var(--color-info)",
  logic: "var(--color-warning)",
  end: "var(--color-error)",
};

export function nodeTypeColor(type: WorkflowNodeType | string): string {
  return semanticVar[nodeSemantic[type as WorkflowNodeType] ?? "action"];
}

/** 可输出数据的节点（右侧菱形数据口，数据流连线的来源） */
const DATA_OUT_TYPES: Set<WorkflowNodeType> = new Set([
  "llm",
  "tool",
  "http",
  "knowledge",
  "code",
  "script",
]);

const nodeIcons: Record<WorkflowNodeData["type"], React.ElementType> = {
  start: Play,
  llm: Bot,
  tool: Wrench,
  condition: GitBranch,
  loop: Repeat,
  code: Code2,
  knowledge: Database,
  http: Globe,
  notify: Bell,
  organ: Zap,
  script: Terminal,
  end: Square,
};

// ── 节点几何（高固定、宽随内容收紧凑：显式声明免测量，鹰眼图/连线初始化确定性）──
export const NODE_HEIGHT = 53;
const NODE_MIN_WIDTH = 140;
const NODE_MAX_WIDTH = 228;

/** 副标题兜底描述（无自述时的描述性文案，禁止复读类型名） */
export const nodeDescFallback: Record<WorkflowNodeType, string> = {
  start: "手动/定时触发入口",
  condition: "条件分支判断",
  loop: "循环迭代控制",
  end: "流程结束出口",
  llm: "大模型推理调用",
  http: "外部接口请求",
  notify: "消息通知推送",
  organ: "器官能力调用",
  knowledge: "知识库检索",
  script: "脚本执行动作",
  tool: "工具能力调用",
  code: "自定义代码块",
};

/** 文本宽估算（确定性、免 DOM 测量）：CJK/全角≈字号，拉丁≈0.56 字号 */
function estimateTextWidth(text: string, fontSize: number): number {
  let w = 0;
  for (const ch of text) {
    const code = ch.codePointAt(0) ?? 0;
    w += code > 0x2e7f ? fontSize : fontSize * 0.56;
  }
  return w;
}

/** 节点宽度随内容收紧凑（左内距 + 图标 + 间距 + 文本 + 调试徽章 + 右内距），区间夹紧。
 *  与左栏节点面板同规格（px-3 + 图标 + gap-2.5）；副标题文案取 i18n 实际渲染串 */
export function nodeWidth(data: WorkflowNodeData, debugActive?: boolean): number {
  const label = String(data.label ?? "");
  const desc =
    String(data.description ?? "").trim() ||
    i18n.t(`nodeDesc.${data.type}`, nodeDescFallback[data.type] ?? "");
  const textW = Math.max(estimateTextWidth(label, 12), estimateTextWidth(desc, 10), 40);
  const labelZone = data.type === "condition" ? 42 : 12;
  const width = 12 + 17 + 10 + textW + labelZone + (debugActive ? 48 : 0);
  return Math.round(Math.min(NODE_MAX_WIDTH, Math.max(NODE_MIN_WIDTH, width)));
}

/** 节点归一化：显式 width/height（React Flow v12 需要节点尺寸才能初始化鹰眼图/连线） */
export function prepareNodes(nodes: Node<WorkflowNodeData>[]): Node<WorkflowNodeData>[] {
  return nodes.map((n) => ({
    ...n,
    width: nodeWidth(n.data, Boolean((n.data as { debugActive?: boolean }).debugActive)),
    height: NODE_HEIGHT,
  }));
}

// ── 测量尺寸（超椭圆 path 需要真实宽高）────────────────────────────────
/**
 * 连线初始化自愈：React Flow 受控模式下「测量↔setNodes」竞态会丢 handleBounds
 * 且不再复测（上游 xyflow#6102），表现为节点隐形/连线消失/鹰眼图空。
 * 检出无 handleBounds 的节点，强制 updateNodeInternals 复测。
 */
export function WorkflowFlowHealer() {
  const stuckIds = useStore((s) =>
    Array.from(s.nodeLookup.values())
      .filter((n: any) => !n.hidden && !n.internals?.handleBounds)
      .map((n: any) => n.id),
    (a: string[], b: string[]) => a.length === b.length && a.every((id, i) => id === b[i]),
  );
  const updateNodeInternals = useUpdateNodeInternals();
  useEffect(() => {
    if (stuckIds.length > 0) updateNodeInternals(stuckIds);
  }, [stuckIds, updateNodeInternals]);
  return null;
}

interface BaseNodeProps {
  data: WorkflowNodeData;
  selected?: boolean;
  sourceHandleCount?: number;
  showTarget?: boolean;
  debugActive?: boolean;
}

function BaseNodeInner({
  data,
  selected,
  sourceHandleCount = 1,
  showTarget = true,
  debugActive,
}: BaseNodeProps) {
  const { t } = useTranslation();
  // debugActive 由画布注入 data（workflow-client 派生），props 兜底
  const debug = debugActive ?? Boolean((data as { debugActive?: boolean }).debugActive);
  const color = nodeTypeColor(data.type);
  const Icon = nodeIcons[data.type];
  const w = nodeWidth(data, debug);
  const showDataHandle = DATA_OUT_TYPES.has(data.type);
  // 副标题：自述优先，缺省用描述性文案（禁止复读类型名）；与标题雷同时隐藏
  const subtitle =
    String(data.description ?? "").trim() || t(`nodeDesc.${data.type}`, nodeDescFallback[data.type] ?? "");
  const showSubtitle = Boolean(subtitle) && subtitle !== data.label;

  return (
    <div
      className={cn(
        "wf-node group/node relative",
        selected && "-translate-y-px",
      )}
      style={{
        width: w,
        height: NODE_HEIGHT,
        transition: "transform var(--transition-fast)",
      }}
    >
      {/* 节点卡：与左栏节点面板同规格（wf-node-card 普通圆角矩形）；调试态 warning 虚线框 */}
      <div
        className={cn("wf-node-card absolute inset-0", selected && "wf-node-card-selected")}
      />
      {debug && <div className="wf-node-debug absolute inset-0 pointer-events-none" />}

      {/* 内容层：纯色图标（与面板同款无框）+ 名称/说明双行；条件节点右侧让位输出标签 */}
      <div className={cn("relative flex h-full items-center gap-2.5 px-3", sourceHandleCount > 1 && "pr-[42px]")}>
        <Icon size={17} style={{ color }} strokeWidth={2} className="shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="truncate text-xs font-medium leading-4 text-foreground">{data.label}</p>
          {showSubtitle && (
            <p className="mt-px truncate text-[10px] leading-3 text-foreground/70">{subtitle}</p>
          )}
        </div>
        {/* 分支输出标签：卡内右缘、一行一输出、小号弱化字色（对齐右缘出点内侧），卡外零文字零徽章 */}
        {sourceHandleCount > 1 && (
          <>
            <span className="wf-branch-label" style={{ top: "30%", color: "var(--color-branch-then-weak)" }}>then</span>
            <span className="wf-branch-label" style={{ top: "70%", color: "var(--color-branch-else-weak)" }}>else</span>
          </>
        )}
        {debug && (
          <span
            className="wf-chip flex shrink-0 items-center gap-1 px-1.5 py-0.5 text-[9px] font-medium text-foreground"
            style={{
              background: `color-mix(in srgb, var(--color-warning) 16%, transparent)`,
              boxShadow: "inset 0 0 0 1px color-mix(in srgb, var(--color-warning) 35%, transparent)",
            }}
          >
            <Loader2 size={9} className="animate-spin" style={{ color: "var(--color-warning)" }} />
            {t("workflowBuilder.debug", "调试")}
          </span>
        )}
      </div>

      {/* 连接点：顶部入点 / 底部执行出点 / 条件分支右侧双出点（上=then 下=else）/ 右侧菱形数据出点（统一 wf-port 语言） */}
      {showTarget && (
        <Handle type="target" position={Position.Top} className="wf-port" />
      )}

      {sourceHandleCount === 1 && (
        <Handle type="source" position={Position.Bottom} className="wf-port" />
      )}
      {sourceHandleCount > 1 && (
        <>
          <Handle
            type="source"
            position={Position.Right}
            id="true"
            className="wf-port"
            style={{ top: "30%", "--wf-port-fill": branchVar.then } as CSSProperties}
          />
          <Handle
            type="source"
            position={Position.Right}
            id="false"
            className="wf-port"
            style={{ top: "70%", "--wf-port-fill": branchVar.else } as CSSProperties}
          />
        </>
      )}
      {showDataHandle && (
        <Handle
          type="source"
          position={Position.Right}
          id="data"
          className="wf-port wf-port-data"
          style={{ top: "50%", "--wf-port-fill": "var(--color-info)" } as CSSProperties}
        />
      )}
    </div>
  );
}

// ── Individual node components ──────────────────────────────────────────────

export const StartNode = memo(function StartNode({ data, selected }: NodeProps) {
  return (
    <BaseNodeInner
      data={data as unknown as WorkflowNodeData}
      selected={selected}
      showTarget={false}
      sourceHandleCount={1}
    />
  );
});

export const LLMNode = memo(function LLMNode({ data, selected }: NodeProps) {
  return (
    <BaseNodeInner
      data={data as unknown as WorkflowNodeData}
      selected={selected}
      sourceHandleCount={1}
    />
  );
});

export const ToolNode = memo(function ToolNode({ data, selected }: NodeProps) {
  return (
    <BaseNodeInner
      data={data as unknown as WorkflowNodeData}
      selected={selected}
      sourceHandleCount={1}
    />
  );
});

// ── 判断分支（decision）：then/else 双分支 ────────────────────────────────
// then=低饱和沉稳绿 / else=中性淡紫灰：分支语义色 token（globals.css 派生自
// success/muted-foreground，随全部主题联动），与节点图标色（logic=warning）错开、
// 标签圆点与出点圆点同色同形
export const branchVar: Record<"then" | "else", string> = {
  then: "var(--color-branch-then)",
  else: "var(--color-branch-else)",
};

export const ConditionNode = memo(function ConditionNode({ data, selected }: NodeProps) {
  return (
    <BaseNodeInner
      data={data as unknown as WorkflowNodeData}
      selected={selected}
      sourceHandleCount={2}
    />
  );
});

export const LoopNode = memo(function LoopNode({ data, selected }: NodeProps) {
  return (
    <BaseNodeInner
      data={data as unknown as WorkflowNodeData}
      selected={selected}
      sourceHandleCount={1}
    />
  );
});

export const CodeNode = memo(function CodeNode({ data, selected }: NodeProps) {
  return (
    <BaseNodeInner
      data={data as unknown as WorkflowNodeData}
      selected={selected}
      sourceHandleCount={1}
    />
  );
});

export const KnowledgeNode = memo(function KnowledgeNode({ data, selected }: NodeProps) {
  return (
    <BaseNodeInner
      data={data as unknown as WorkflowNodeData}
      selected={selected}
      sourceHandleCount={1}
    />
  );
});

export const HTTPNode = memo(function HTTPNode({ data, selected }: NodeProps) {
  return (
    <BaseNodeInner
      data={data as unknown as WorkflowNodeData}
      selected={selected}
      sourceHandleCount={1}
    />
  );
});

export const NotifyNode = memo(function NotifyNode({ data, selected }: NodeProps) {
  return (
    <BaseNodeInner
      data={data as unknown as WorkflowNodeData}
      selected={selected}
      sourceHandleCount={1}
    />
  );
});

export const OrganNode = memo(function OrganNode({ data, selected }: NodeProps) {
  return (
    <BaseNodeInner
      data={data as unknown as WorkflowNodeData}
      selected={selected}
      sourceHandleCount={1}
    />
  );
});

export const ScriptNode = memo(function ScriptNode({ data, selected }: NodeProps) {
  return (
    <BaseNodeInner
      data={data as unknown as WorkflowNodeData}
      selected={selected}
      sourceHandleCount={1}
    />
  );
});

export const EndNode = memo(function EndNode({ data, selected }: NodeProps) {
  return (
    <BaseNodeInner
      data={data as unknown as WorkflowNodeData}
      selected={selected}
      showTarget={true}
      sourceHandleCount={0}
    />
  );
});

export const nodeTypes = {
  startNode: StartNode,
  llmNode: LLMNode,
  toolNode: ToolNode,
  conditionNode: ConditionNode,
  loopNode: LoopNode,
  codeNode: CodeNode,
  knowledgeNode: KnowledgeNode,
  httpNode: HTTPNode,
  notifyNode: NotifyNode,
  organNode: OrganNode,
  scriptNode: ScriptNode,
  endNode: EndNode,
};
