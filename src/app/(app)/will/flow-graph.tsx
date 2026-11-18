"use client";

// 流程节点图（decision when/then/else 真分支图）— will 观察窗与建流工坊共用组件。
// 从 will-client 原样搬出（渲染逐字一致）；节点分支字段兼容 else（规范外键名）与
// else_（flow_api asdict 原始形状）。

import { useTranslation } from "react-i18next";

export interface FlowNode {
  id: string;
  type: string;
  tool?: string;
  when?: string;
  then?: string;
  else?: string;
  else_?: string;
  over?: string;
  body?: string[];
  form?: string;
  task?: string;
  role?: string;
  user?: string;
  actions?: Array<{ type: string; message?: string; channel?: string }>;
}

export interface FlowDef {
  id: string;
  name: string;
  version: number;
  plugin_id?: string;
  triggers: Array<{ type: string; cron?: string; event?: string }>;
  nodes: FlowNode[];
  retain_runs?: number;
  observability?: { retain_runs?: number };
}

export const NODE_TYPE_LABEL: Record<string, string> = {
  tool: "will.ntTool", decision: "will.ntDecision", loop: "will.ntLoop",
  approval: "will.ntApproval", action: "will.ntAction", agent: "will.ntAgent",
  human: "will.ntHuman", parallel: "will.ntParallel",
};

function nodeElse(node: FlowNode): string {
  return node.else ?? node.else_ ?? "";
}

// ── 判断分支可视化（decision when/then/else 真分支图）──
export function BranchGraph({ defn }: { defn: FlowDef }) {
  const { t } = useTranslation();
  const nodes = defn.nodes;
  const rowH = 54;
  const height = Math.max(nodes.length * rowH + 20, 120);
  const centerX = 150;
  const typeColor: Record<string, string> = {
    tool: "#3b82f6", decision: "#f59e0b", loop: "#8b5cf6", approval: "#a855f7",
    action: "#10b981", agent: "#06b6d4", human: "#ec4899", parallel: "#6366f1",
  };

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 340 ${height}`} className="w-full min-w-[320px]" style={{ maxHeight: 360 }}>
        <defs>
          <marker id="will-arrow" markerWidth={6} markerHeight={6} refX={5} refY={3} orient="auto">
            <path d="M 0 0 L 6 3 L 0 6 z" fill="currentColor" opacity={0.6} />
          </marker>
        </defs>
        {nodes.map((node, i) => {
          const y = 14 + i * rowH;
          const color = typeColor[node.type] || "#64748b";
          return (
            <g key={node.id}>
              {/* 顺序连接线（非分支后继） */}
              {i < nodes.length - 1 && (
                <line x1={centerX} y1={y + 18} x2={centerX} y2={y + rowH - 18}
                  stroke="currentColor" strokeWidth={1} opacity={0.18} strokeDasharray="3 3" />
              )}
              <rect x={centerX - 70} y={y - 16} width={140} height={32} rx={8}
                fill={color} fillOpacity={0.1} stroke={color} strokeWidth={1.2} />
              <text x={centerX - 58} y={y + 1} fontSize={10} fill="currentColor" fontWeight={600}>{node.id}</text>
              <text x={centerX - 58} y={y + 11} fontSize={8} fill="currentColor" opacity={0.7}>
                {t(NODE_TYPE_LABEL[node.type] || node.type, node.type)}
                {node.type === "tool" && node.tool ? ` · ${node.tool}` : ""}
              </text>
              {/* decision 分支弧线 */}
              {node.type === "decision" && (
                <>
                  {node.then && (
                    <BranchEdge nodes={nodes} fromY={y} targetId={node.then}
                      side="right" color="#10b981" label={`then → ${node.then}`} />
                  )}
                  {nodeElse(node) && (
                    <BranchEdge nodes={nodes} fromY={y} targetId={nodeElse(node)}
                      side="left" color="#ef4444" label={`else → ${nodeElse(node)}`} />
                  )}
                  {node.when && (
                    <text x={centerX + 78} y={y + 3} fontSize={8} fill="currentColor" opacity={0.75}>
                      {t("will.when", "条件")}: {node.when.length > 42 ? node.when.slice(0, 42) + "…" : node.when}
                    </text>
                  )}
                </>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function BranchEdge({ nodes, fromY, targetId, side, color, label }: {
  nodes: FlowNode[]; fromY: number; targetId: string;
  side: "left" | "right"; color: string; label: string;
}) {
  const rowH = 54;
  const centerX = 150;
  const idx = nodes.findIndex((n) => n.id === targetId);
  if (idx < 0) return null;
  const toY = 14 + idx * rowH;
  const laneX = side === "right" ? 315 : 25;
  const startX = side === "right" ? centerX + 70 : centerX - 70;
  return (
    <g>
      <path
        d={`M ${startX} ${fromY} C ${laneX} ${fromY}, ${laneX} ${toY}, ${startX} ${toY}`}
        fill="none" stroke={color} strokeWidth={1.2} opacity={0.75} strokeDasharray="4 3"
        markerEnd="url(#will-arrow)"
      />
      <text x={laneX} y={(fromY + toY) / 2 + 3} fontSize={7.5} fill={color}
        textAnchor={side === "right" ? "end" : "start"} fontWeight={600}>
        {label}
      </text>
    </g>
  );
}
