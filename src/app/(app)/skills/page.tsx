"use client";
import { useState } from "react";
import dynamic from "next/dynamic";
import { cn } from "@/lib/utils";
import { Puzzle, Store } from "lucide-react";
import { PageLayout } from "@/components/page-layout";
const SkillsClient = dynamic(() => import("./skills-client").then((m) => m.SkillsClient), { ssr: false });
const MarketplaceClient = dynamic(() => import("../marketplace/marketplace-client").then((m) => m.MarketplaceClient), { ssr: false });

// menu-audit P1：旧页组件整体搬入 hub tab（不重写），迁移后旧 page.tsx 删除（组件由 tab 挂载）。
const TABS = [
  { id: "skills", label: "技能", icon: Puzzle },
  { id: "marketplace", label: "市场", icon: Store },
];

export default function HubPage() {
  const [tab, setTab] = useState("skills");
  return (
    <PageLayout title="能力市场">
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
          {tab === "skills" && <SkillsClient />}
          {tab === "marketplace" && <MarketplaceClient />}
        </div>
      </div>
    </PageLayout>
  );
}
