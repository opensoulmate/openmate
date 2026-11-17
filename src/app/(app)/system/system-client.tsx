"use client";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { Network, Package, BarChart3, Activity } from "lucide-react";
import { PageLayout } from '@/components/page-layout';
import { OverviewTab } from './tabs/overview-tab';
import { TopologyTab } from './tabs/topology-tab';
import { RegistryTab } from './tabs/registry-tab';
import { TrajectoryTab } from './tabs/trajectory-tab';

type TabKey = "overview" | "topology" | "registry" | "trajectory";

const TABS: { key: TabKey; label: string; icon: React.ElementType }[] = [
  { key: "overview", label: "system.tabs.overview", icon: Activity },
  { key: "topology", label: "system.tabs.topology", icon: Network },
  { key: "registry", label: "system.tabs.registry", icon: Package },
  { key: "trajectory", label: "system.tabs.trajectory", icon: BarChart3 },
];

export function SystemClient() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabKey>("overview");

  return (
    <PageLayout title="系统">
      <div className="flex h-full flex-col overflow-hidden">
        {/* Tab bar */}
        <div className="shrink-0 border-b border-border px-3 lg:px-6">
          <div className="flex gap-1 -mb-px overflow-x-auto">
            {TABS.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                onClick={() => setActiveTab(key)}
                className={cn(
                  "flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium rounded-lg transition-colors whitespace-nowrap",
                  activeTab === key
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )}
              >
                <Icon size={14} />
                {t(label, key === "overview" ? "概览" : key === "topology" ? "拓扑" : key === "registry" ? "注册表" : "轨迹")}
              </button>
            ))}
          </div>
        </div>

        {/* Tab content */}
        <div className="flex-1 overflow-hidden">
          {activeTab === "overview" && <OverviewTab />}
          {activeTab === "topology" && <TopologyTab />}
          {activeTab === "registry" && <RegistryTab />}
          {activeTab === "trajectory" && <TrajectoryTab />}
        </div>
      </div>
    </PageLayout>
  );
}
