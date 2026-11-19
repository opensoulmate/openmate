"use client";

// ── 工作流鹰眼图（MiniMap 重画）────────────────────────────────────────
// 不用默认灰块样式：缩略节点与主画布节点同规格（普通圆角矩形卡 + 类型色描边），
// 视口框=描边+半透明填充+圆角，容器无边框（圆角+柔影），随深浅主题联动。

import { memo, useEffect } from "react";
import { MiniMap, type MiniMapNodeProps, type Node } from "@xyflow/react";
import { nodeTypeColor } from "./workflow-nodes";
import type { WorkflowNodeData } from "@/stores/workflow-store";

const VIEWPORT_FILL_CLASS = "wf-minimap-viewport-fill";

/** 缩略节点：普通圆角矩形卡（与左栏/画布节点同语言：卡底 + 类型色描边），选中描主强调 */
const MiniMapTile = memo(function MiniMapTile({
  x,
  y,
  width,
  height,
  color,
  borderRadius,
  selected,
  shapeRendering,
  className,
}: MiniMapNodeProps) {
  const r = Math.max(1, Math.min(borderRadius, height / 2, width / 2));
  return (
    <g className={className} shapeRendering={shapeRendering}>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx={r}
        style={{
          fill: "var(--color-background)",
          stroke: `color-mix(in srgb, ${color} 55%, transparent)`,
          strokeWidth: 1,
        }}
      />
      {selected && (
        <rect
          x={x - 0.5}
          y={y - 0.5}
          width={width + 1}
          height={height + 1}
          rx={r + 0.5}
          fill="none"
          style={{ stroke: "var(--color-primary)", strokeWidth: 1.2 }}
        />
      )}
    </g>
  );
});

/** 与主画布一致的缩略节点色（按节点类型的语义色） */
function tileColor(node: Node): string {
  return nodeTypeColor((node.data as WorkflowNodeData)?.type ?? "tool");
}

function roundedRectPath(vx: number, vy: number, vw: number, vh: number, r: number): string {
  const rr = Math.max(0, Math.min(r, vw / 2, vh / 2));
  return [
    `M${vx + rr},${vy}`,
    `h${vw - rr * 2}`,
    `a${rr},${rr} 0 0 1 ${rr},${rr}`,
    `v${vh - rr * 2}`,
    `a${rr},${rr} 0 0 1 ${-rr},${rr}`,
    `h${-(vw - rr * 2)}`,
    `a${rr},${rr} 0 0 1 ${-rr},${-rr}`,
    `v${-(vh - rr * 2)}`,
    `a${rr},${rr} 0 0 1 ${rr},${-rr}`,
    "z",
  ].join(" ");
}

const SUBPATH_RE =
  /M\s*(-?[\d.eE]+)\s*,\s*(-?[\d.eE]+)\s*h\s*(-?[\d.eE]+)\s*v\s*(-?[\d.eE]+)\s*h\s*(-?[\d.eE]+)\s*z/g;

/**
 * 视口框圆角 + 半透明填充：
 * 库的 mask 是「外框-视口」evenodd path（尖角、无填充），把视口子路径改写为
 * 圆角，并在 mask 之前补一层半透明视口填充（描边由 mask 自带）。
 */
function patchMinimapMask(mask: SVGPathElement) {
  const d = mask.getAttribute("d") ?? "";
  const matches = Array.from(d.matchAll(SUBPATH_RE));
  if (matches.length < 2) return;
  const last = matches[matches.length - 1];
  const vx = Number(last[1]);
  const vy = Number(last[2]);
  const vw = Number(last[3]);
  const vh = Number(last[4]);
  if (![vx, vy, vw, vh].every(Number.isFinite) || vw <= 0 || vh <= 0) return;

  const rounded = roundedRectPath(vx, vy, vw, vh, 6);
  const head = d.slice(0, last.index).replace(/\s+$/, "");
  const nextD = `${head} ${rounded}`;
  if (nextD !== d) mask.setAttribute("d", nextD);

  // 半透明视口填充层（垫在 mask 之下，盖住缩略节点之上）
  const svg = mask.ownerSVGElement;
  if (svg) {
    let fill = svg.querySelector<SVGPathElement>(`path.${VIEWPORT_FILL_CLASS}`);
    if (!fill) {
      fill = document.createElementNS("http://www.w3.org/2000/svg", "path");
      fill.setAttribute("class", VIEWPORT_FILL_CLASS);
      fill.setAttribute("pointer-events", "none");
      mask.parentNode?.insertBefore(fill, mask);
    }
    if (fill.getAttribute("d") !== rounded) fill.setAttribute("d", rounded);
  }
}

/** 鹰眼图容器：无边框、圆角、柔影、毛玻璃底 */
export function WorkflowMiniMap() {
  useEffect(() => {
    const mask = document.querySelector<SVGPathElement>(".wf-minimap .react-flow__minimap-mask");
    if (!mask) return;
    patchMinimapMask(mask);
    const mo = new MutationObserver(() => patchMinimapMask(mask));
    mo.observe(mask, { attributes: true, attributeFilter: ["d"] });
    return () => mo.disconnect();
  }, []);

  return (
    <>
      <style>{`
        .wf-minimap.react-flow__minimap {
          border: 0;
          overflow: hidden;
          border-radius: var(--radius-md);
          box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18), 0 0 0 1px color-mix(in srgb, var(--color-border) 55%, transparent);
          backdrop-filter: blur(6px);
        }
        .wf-minimap .${VIEWPORT_FILL_CLASS} {
          fill: color-mix(in srgb, var(--color-primary) 10%, transparent);
        }
        .wf-minimap .react-flow__minimap-mask {
          stroke: color-mix(in srgb, var(--color-primary) 55%, transparent);
          stroke-width: 1.25;
        }
      `}</style>
      <MiniMap
        position="bottom-right"
        style={{ width: 216, height: 152 }}
        className="wf-minimap"
        bgColor="color-mix(in srgb, var(--color-card) 82%, transparent)"
        nodeComponent={MiniMapTile}
        nodeColor={tileColor}
        nodeStrokeWidth={0}
        nodeBorderRadius={3}
        maskColor="color-mix(in srgb, var(--color-background) 58%, transparent)"
        zoomable
        pannable
        ariaLabel="工作流鹰眼图"
      />
    </>
  );
}
