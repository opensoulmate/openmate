"use client";
import { useState } from "react";
import dynamic from "next/dynamic";
import { cn } from "@/lib/utils";
import { FileText, ScrollText, History } from "lucide-react";
import { PageLayout } from "@/components/page-layout";
const DocsClient = dynamic(() => import("./docs-client").then((m) => m.DocsClient), { ssr: false });
const DevSpecsClient = dynamic(() => import("../dev-specs/dev-specs-client").then((m) => m.DevSpecsClient), { ssr: false });
const DevlogClient = dynamic(() => import("../devlog/devlog-client").then((m) => m.DevlogClient), { ssr: false });

// menu-audit P1：旧页组件整体搬入 hub tab（不重写），迁移后旧 page.tsx 删除（组件由 tab 挂载）。
const TABS = [
  { id: "docs", label: "文档", icon: FileText },
  { id: "specs", label: "规范", icon: ScrollText },
  { id: "devlog", label: "开发记录", icon: History },
];

export default function HubPage() {
  const [tab, setTab] = useState("docs");
  return (
    <PageLayout title="文档中心">
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
          {tab === "docs" && <DocsClient />}
          {tab === "specs" && <DevSpecsClient />}
          {tab === "devlog" && <DevlogClient />}
        </div>
      </div>
    </PageLayout>
  );
}
