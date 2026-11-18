"use client";

import type { WorkflowNodeType } from "@/stores/workflow-store";
import {
  Play,
  Bot,
  Wrench,
  GitBranch,
  Repeat,
  Code2,
  Database,
  Square,
  Globe,
  Bell,
  Zap,
  Terminal,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

interface PaletteItem {
  type: WorkflowNodeType;
  labelKey: string;
  icon: React.ElementType;
  color: string;
  group: "flow" | "action";
}

const paletteItems: PaletteItem[] = [
  // Flow control（与画布节点同语义色：trigger/logic/end）
  { type: "start", labelKey: "nodePalette.start", icon: Play, color: "text-success", group: "flow" },
  { type: "condition", labelKey: "nodePalette.condition", icon: GitBranch, color: "text-warning", group: "flow" },
  { type: "loop", labelKey: "nodePalette.loop", icon: Repeat, color: "text-warning", group: "flow" },
  { type: "end", labelKey: "nodePalette.end", icon: Square, color: "text-error", group: "flow" },
  // Actions（ai=主强调 / action=info）
  { type: "llm", labelKey: "nodePalette.llm", icon: Bot, color: "text-primary", group: "action" },
  { type: "http", labelKey: "nodePalette.http", icon: Globe, color: "text-info", group: "action" },
  { type: "notify", labelKey: "nodePalette.notify", icon: Bell, color: "text-info", group: "action" },
  { type: "organ", labelKey: "nodePalette.organ", icon: Zap, color: "text-info", group: "action" },
  { type: "knowledge", labelKey: "nodePalette.knowledge", icon: Database, color: "text-primary", group: "action" },
  { type: "script", labelKey: "nodePalette.script", icon: Terminal, color: "text-info", group: "action" },
  { type: "tool", labelKey: "nodePalette.tool", icon: Wrench, color: "text-info", group: "action" },
  { type: "code", labelKey: "nodePalette.code", icon: Code2, color: "text-info", group: "action" },
];

const nodeTypeMap: Record<WorkflowNodeType, string> = {
  start: "startNode",
  llm: "llmNode",
  tool: "toolNode",
  condition: "conditionNode",
  loop: "loopNode",
  code: "codeNode",
  knowledge: "knowledgeNode",
  http: "httpNode",
  notify: "notifyNode",
  organ: "organNode",
  script: "scriptNode",
  end: "endNode",
};

export function NodePalette() {
  const { t } = useTranslation();

  const onDragStart = (e: React.DragEvent, item: PaletteItem) => {
    e.dataTransfer.setData("application/reactflow-type", nodeTypeMap[item.type]);
    e.dataTransfer.setData("application/reactflow-data-type", item.type);
    e.dataTransfer.setData("application/reactflow-label", t(item.labelKey));
    e.dataTransfer.effectAllowed = "move";
  };

  const flowItems = paletteItems.filter((i) => i.group === "flow");
  const actionItems = paletteItems.filter((i) => i.group === "action");

  return (
    <div className="flex h-full w-48 shrink-0 flex-col border-r border-border bg-card">
      <div className="flex h-10 shrink-0 items-center border-b border-border px-3">
        <h3 className="text-xs font-medium text-foreground">{t("nodePalette.title")}</h3>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-3">
        {/* Flow control */}
        <div>
          <div className="px-2 mb-1 text-[10px] font-medium text-muted-foreground uppercase tracking-wider">{t("nodePalette.flowControl")}</div>
          <div className="space-y-1">
            {flowItems.map((item) => (
              <div
                key={item.type}
                draggable
                onDragStart={(e) => onDragStart(e, item)}
                className={cn(
                  "flex cursor-grab items-center gap-2.5 rounded-lg border border-border bg-background px-3 py-2",
                  "transition-colors hover:border-primary/40 hover:bg-accent active:cursor-grabbing",
                )}
              >
                <item.icon size={15} className={item.color} />
                <span className="text-xs font-medium text-foreground">{t(item.labelKey)}</span>
              </div>
            ))}
          </div>
        </div>
        {/* Actions */}
        <div>
          <div className="px-2 mb-1 text-[10px] font-medium text-muted-foreground uppercase tracking-wider">{t("nodePalette.actions")}</div>
          <div className="space-y-1">
            {actionItems.map((item) => (
              <div
                key={item.type}
                draggable
                onDragStart={(e) => onDragStart(e, item)}
                className={cn(
                  "flex cursor-grab items-center gap-2.5 rounded-lg border border-border bg-background px-3 py-2",
                  "transition-colors hover:border-primary/40 hover:bg-accent active:cursor-grabbing",
                )}
              >
                <item.icon size={15} className={item.color} />
                <span className="text-xs font-medium text-foreground">{t(item.labelKey)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
