"use client";

// ── 工作流连线（贝塞尔 + 箭头 + 类型色）────────────────────────────────
// 三类语义连线，与节点语义色体系同源（主强调 1 + 语义 4，全站 token 对齐）：
//   exec  执行流   —— 主强调紫，实线
//   data  数据流   —— info 青，虚线（数据口菱形 → 节点）
//   branch 判断分支 —— then=branch-then 沉稳绿 / else=branch-else 中性淡紫灰，实线（分支标签贴右缘出点旁，见 workflow-nodes）
// 动效克制：仅调试/执行途经的连线做虚线流动，其余静态。

import {
  BaseEdge,
  getBezierPath,
  MarkerType,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import { cn } from "@/lib/utils";
import { branchVar } from "./workflow-nodes";

export type WorkflowEdgeKind = "exec" | "data" | "branch";

export interface WorkflowEdgeData {
  kind?: WorkflowEdgeKind;
  branch?: "then" | "else";
  animated?: boolean;
  [key: string]: unknown;
}

const execVar = "var(--color-primary)";
const dataVar = "var(--color-info)";

/** 连线语义色（与 workflow-nodes 的节点语义色同源） */
export function edgeStrokeVar(data?: WorkflowEdgeData | null): string {
  if (data?.kind === "branch") return branchVar[data?.branch === "then" ? "then" : "else"];
  if (data?.kind === "data") return dataVar;
  return execVar;
}

/** 连线参数（贝塞尔曲率克制、箭头小三角） */
function edgeMarker(stroke: string) {
  return { type: MarkerType.ArrowClosed as const, width: 15, height: 15, color: stroke };
}

/**
 * 归一化连线：补齐 type/data/markerEnd（旧存档连线无 kind 时按执行流处理）。
 * debugNodeId 途经的连线点亮为虚线流动（动效克制：非全量动画）。
 */
export function prepareEdges(edges: Edge[], debugNodeId?: string | null): Edge[] {
  return edges.map((e) => {
    const data = (e.data ?? {}) as WorkflowEdgeData;
    const kind: WorkflowEdgeKind = data.kind ?? "exec";
    const animated = data.animated ?? (e.source === debugNodeId || e.target === debugNodeId);
    const next: WorkflowEdgeData = { ...data, kind, animated };
    return {
      ...e,
      type: "flowEdge",
      data: next,
      markerEnd: e.markerEnd ?? edgeMarker(edgeStrokeVar(next)),
    } as Edge;
  });
}

/** 新连线（onConnect 用）：按来源出点判定类型 —— 数据口=数据流、判断出点=判断分支 */
export function buildEdge(params: {
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
  sourceType?: string;
}): Edge {
  const handle = params.sourceHandle ?? undefined;
  const isBranch = params.sourceType === "conditionNode";
  const kind: WorkflowEdgeKind = handle === "data" ? "data" : isBranch ? "branch" : "exec";
  const branch = handle === "true" ? "then" : handle === "false" ? "else" : undefined;
  const data: WorkflowEdgeData = kind === "branch" ? { kind, branch } : { kind };
  return {
    id: `e-${params.source}-${params.target}-${Date.now()}`,
    source: params.source,
    target: params.target,
    sourceHandle: handle,
    targetHandle: params.targetHandle ?? undefined,
    type: "flowEdge",
    data,
    markerEnd: edgeMarker(edgeStrokeVar(data)),
  };
}

/** 单一连线渲染器：类型色 + 贝塞尔 + 箭头 */
export function WorkflowEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
  markerEnd,
}: EdgeProps) {
  const d = (data ?? {}) as WorkflowEdgeData;
  const kind: WorkflowEdgeKind = d.kind ?? "exec";
  const stroke = edgeStrokeVar(d);
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    curvature: 0.32,
  });

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{
          stroke,
          strokeWidth: selected ? 2.2 : 1.6,
          strokeDasharray: kind === "data" ? "5 4" : undefined,
        }}
        className={cn("wf-edge", selected && "wf-edge-selected", d.animated && "wf-edge-animated")}
      />
    </>
  );
}

export const edgeTypes = { flowEdge: WorkflowEdge };
