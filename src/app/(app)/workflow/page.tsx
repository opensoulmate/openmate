"use client";
import { useState } from "react";
import dynamic from "next/dynamic";
import { cn } from "@/lib/utils";
import { Workflow, Clock, Zap } from "lucide-react";
import { PageLayout } from "@/components/page-layout";
const WorkflowClient = dynamic(() => import("./workflow-client").then((m) => m.WorkflowClient), { ssr: false });
const CronClient = dynamic(() => import("../cron/cron-client").then((m) => m.CronClient), { ssr: false });
const PipelineClient = dynamic(() => import("../pipeline/pipeline-client").then((m) => m.PipelineClient), { ssr: false });

// menu-audit P1：旧页组件整体搬入 hub tab（不重写），迁移后旧 page.tsx 删除（组件由 tab 挂载）。
const TABS = [
  { id: "flow", label: "流程", icon: Workflow },
  { id: "cron", label: "定时", icon: Clock },
  { id: "pipeline", label: "流水线", icon: Zap },
];

export default function HubPage() {
  const [tab, setTab] = useState("flow");
  return (
    <PageLayout title="工作流">
      <div className="flex h-full flex-col overflow-hidden">
        <div className="flex items-center gap-1 border-b border-border px-3 lg:px-6 py-2 overflow-x-auto">
          {TABS.map((x) => (
            <button
              key={x.id}
              onClick={() => setTab(x.id)}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs lg:text-sm transition-colors whitespace-nowrap",
                tab === x.id ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground hover:bg-muted"
              )}
            >
              <x.icon size={14} /> {x.label}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-hidden">
          {tab === "flow" && <WorkflowClient />}
          {tab === "cron" && <CronClient />}
          {tab === "pipeline" && <PipelineClient />}
        </div>
      </div>
    </PageLayout>
  );
}
