"use client";

import { memo, useEffect } from "react";
import {
  Handle,
  Position,
  useStore,
  useUpdateNodeInternals,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { genPath, genRectPath } from "@/components/ui/super-ellipse";
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

// ── 节点固定几何（宽高显式声明：免测量、鹰眼图/连线初始化确定性）────────
export const NODE_WIDTH = 200;
export const NODE_HEIGHT = 53;

/** 节点归一化：显式 width/height（React Flow v12 需要节点尺寸才能初始化鹰眼图/连线） */
export function prepareNodes(nodes: Node<WorkflowNodeData>[]): Node<WorkflowNodeData>[] {
  return nodes.map((n) => ({ ...n, width: NODE_WIDTH, height: NODE_HEIGHT }));
}

/** 超椭圆壳 path（固定几何，模块级算一次） */
const SHELL_PATH = genRectPath(NODE_WIDTH, NODE_HEIGHT);

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
  const d = SHELL_PATH;
  const showDataHandle = DATA_OUT_TYPES.has(data.type);

  return (
    <div
      className={cn(
        "wf-node group/node relative",
        selected && "-translate-y-px",
      )}
      style={{
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        transition: "transform var(--transition-fast)",
      }}
    >
      {/* 超椭圆形状层：卡底 ±类型微染 + 左侧类型色条 */}
      <div
        className="absolute inset-0"
        style={{ clipPath: `path('${d}')`, overflow: "hidden" }}
      >
        <div className="absolute inset-0" style={{ background: "var(--color-card)" }} />
        <div className="absolute inset-0" style={{ background: color, opacity: 0.055 }} />
        <div
          className="absolute inset-y-0 left-0 w-[3.5px]"
          style={{ background: color, opacity: 0.9 }}
        />
      </div>

      {/* 超椭圆描边层：hover 描边提亮、选中主强调描边+柔光、调试态 warning 虚线 */}
      <svg
        className="pointer-events-none absolute inset-0"
        width={NODE_WIDTH}
        height={NODE_HEIGHT}
        style={{ overflow: "visible" }}
      >
        <path
          d={d}
          fill="none"
          strokeWidth={selected ? 1.5 : 1}
          className={cn("wf-node-stroke", selected && "wf-node-stroke-selected")}
        />
        {debug && (
          <path
            d={d}
            fill="none"
            stroke="var(--color-warning)"
            strokeWidth={1.5}
            strokeDasharray="5 4"
          />
        )}
      </svg>

      {/* 内容层：图标 chip + 名称/说明双行 */}
      <div className="relative flex h-full items-center gap-2.5 pl-4 pr-3">
        <div
          className="flex h-[26px] w-[26px] shrink-0 items-center justify-center"
          style={{
            clipPath: `path('${genPath(13)}')`,
            background: `color-mix(in srgb, ${color} 15%, transparent)`,
          }}
        >
          <Icon size={14} style={{ color }} strokeWidth={2} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="truncate text-xs font-medium leading-4 text-foreground">{data.label}</p>
          <p className="mt-px truncate text-[10px] leading-3 text-foreground/70">
            {data.description || t(`nodePalette.${data.type}`, data.type)}
          </p>
        </div>
        {debug && (
          <span
            className="flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] font-medium text-foreground"
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

      {/* 连接点：顶部入点 / 底部执行出点 / 右侧菱形数据出点 */}
      {showTarget && (
        <Handle
          type="target"
          position={Position.Top}
          className="!h-2.5 !w-2.5 !rounded-full !border-2 !border-[var(--color-card)] !bg-muted-foreground"
        />
      )}

      {sourceHandleCount === 1 && (
        <Handle
          type="source"
          position={Position.Bottom}
          className="!h-2.5 !w-2.5 !rounded-full !border-2 !border-[var(--color-card)] !bg-muted-foreground"
        />
      )}
      {sourceHandleCount > 1 && (
        <>
          <Handle
            type="source"
            position={Position.Bottom}
            id="true"
            className="!h-2.5 !w-2.5 !rounded-full !border-2 !border-[var(--color-card)] !bg-[var(--color-success)]"
            style={{ left: "30%" }}
          />
          <Handle
            type="source"
            position={Position.Bottom}
            id="false"
            className="!h-2.5 !w-2.5 !rounded-full !border-2 !border-[var(--color-card)] !bg-[var(--color-warning)]"
            style={{ left: "70%" }}
          />
        </>
      )}
      {showDataHandle && (
        <Handle
          type="source"
          position={Position.Right}
          id="data"
          className="!h-2.5 !w-2.5 !rotate-45 !rounded-[2px] !border-2 !border-[var(--color-card)] !bg-[var(--color-info)]"
          style={{ top: "50%" }}
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
export const branchVar: Record<"then" | "else", string> = {
  then: "var(--color-success)",
  else: "var(--color-warning)",
};

/** 分支标签（骑线小徽章：实底 punch 线条 + 彩点 + 前景文字，双主题 WCAG 安全） */
export function BranchChip({ branch, className }: { branch: "then" | "else"; className?: string }) {
  const color = branchVar[branch];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-1.5 py-[1px] text-[9px] font-medium text-foreground",
        className,
      )}
      style={{
        background: "var(--color-card)",
        boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${color} 45%, transparent)`,
      }}
    >
      <span
        className="h-[5px] w-[5px] rounded-full"
        style={{ background: color }}
      />
      {branch}
    </span>
  );
}

export const ConditionNode = memo(function ConditionNode({ data, selected }: NodeProps) {
  return (
    <div className="relative">
      <BaseNodeInner
        data={data as unknown as WorkflowNodeData}
        selected={selected}
        sourceHandleCount={2}
      />
      {/* then/else 双分支标签：骑在出线起点上，随 30%/70% 双出点对齐 */}
      <div
        className="pointer-events-none absolute flex justify-center"
        style={{ left: "30%", top: "calc(100% + 12px)", transform: "translateX(-50%)" }}
      >
        <BranchChip branch="then" />
      </div>
      <div
        className="pointer-events-none absolute flex justify-center"
        style={{ left: "70%", top: "calc(100% + 12px)", transform: "translateX(-50%)" }}
      >
        <BranchChip branch="else" />
      </div>
    </div>
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
