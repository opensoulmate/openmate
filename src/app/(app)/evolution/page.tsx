"use client";
import { useState } from "react";
import dynamic from "next/dynamic";
import { cn } from "@/lib/utils";
import { Activity, Pill, GitBranch, ScrollText, History } from "lucide-react";
import { PageLayout } from "@/components/page-layout";

// menu-audit P1：演进 hub 吸收 healer/heredity/changelog/timeline（旧组件整体搬入 tab，
// 不重写），迁移后旧 page.tsx 删除（组件由 tab 挂载）。trajectory 已归 system「轨迹」。
const EvolutionOverview = dynamic(() => import("./evolution-overview").then((m) => m.EvolutionOverview), { ssr: false });
const HealerTab = dynamic(() => import("../healer/healer-client").then((m) => m.HealerClient), { ssr: false });
const VersionTab = dynamic(() => import("../heredity/heredity-client").then((m) => m.HeredityClient), { ssr: false });
const ChangelogTab = dynamic(() => import("../changelog/changelog-client").then((m) => m.ChangelogClient), { ssr: false });
const TimelineTab = dynamic(() => import("../timeline/timeline-client").then((m) => m.TimelineClient), { ssr: false });

const TABS = [
  { id: "overview", label: "概览", icon: Activity },
  { id: "healer", label: "自愈", icon: Pill },
  { id: "version", label: "版本", icon: GitBranch },
  { id: "changelog", label: "变更", icon: ScrollText },
  { id: "timeline", label: "时间线", icon: History },
];

export default function EvolutionPage() {
  const [tab, setTab] = useState("overview");
  return (
    <PageLayout title="进化">
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
          {tab === "overview" && <EvolutionOverview />}
          {tab === "healer" && <HealerTab />}
          {tab === "version" && <VersionTab />}
          {tab === "changelog" && <ChangelogTab />}
          {tab === "timeline" && <TimelineTab />}
        </div>
      </div>
    </PageLayout>
  );
}
