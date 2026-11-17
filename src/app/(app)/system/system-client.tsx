"use client";
import { useState } from "react";
import dynamic from "next/dynamic";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { Network, Package, BarChart3, Activity, Gauge, LineChart, Brain, Cpu, Heart } from "lucide-react";
import { PageLayout } from '@/components/page-layout';
import { OverviewTab } from './tabs/overview-tab';
import { TopologyTab } from './tabs/topology-tab';
import { RegistryTab } from './tabs/registry-tab';
import { TrajectoryTab } from './tabs/trajectory-tab';

// menu-audit P1：系统观测类页面**组件实质迁入** system hub 各 tab（旧路由 301 到此）。
// 旧页组件整体搬入 tab 不重写；迁移后旧 page.tsx 删除（组件保留由 tab 挂载）。
const MonitoringTab = dynamic(() => import('../monitoring/monitoring-client').then(m => m.MonitoringClient), { ssr: false });
const MetricsTab = dynamic(() => import('../metrics/metrics-client').then(m => m.MetricsClient), { ssr: false });
const IntelligenceTab = dynamic(() => import('../intelligence/intelligence-client').then(m => m.IntelligenceClient), { ssr: false });
const AiEngineTab = dynamic(() => import('../ai-engine/ai-engine-client').then(m => m.AiEngineClient), { ssr: false });
const VitalTab = dynamic(() => import('../vital/vital-client').then(m => m.VitalClient), { ssr: false });

type TabKey = "overview" | "monitoring" | "topology" | "registry" | "metrics" | "trajectory" | "intelligence" | "architecture" | "vital";

const TABS: { key: TabKey; label: string; fallback: string; icon: React.ElementType }[] = [
  { key: "overview", label: "system.tabs.overview", fallback: "概览", icon: Activity },
  { key: "monitoring", label: "system.tabs.monitoring", fallback: "监控", icon: Gauge },
  { key: "topology", label: "system.tabs.topology", fallback: "拓扑", icon: Network },
  { key: "registry", label: "system.tabs.registry", fallback: "注册表", icon: Package },
  { key: "metrics", label: "system.tabs.metrics", fallback: "指标", icon: LineChart },
  { key: "trajectory", label: "system.tabs.trajectory", fallback: "轨迹", icon: BarChart3 },
  { key: "intelligence", label: "system.tabs.intelligence", fallback: "智能", icon: Brain },
  { key: "architecture", label: "system.tabs.architecture", fallback: "架构", icon: Cpu },
  { key: "vital", label: "system.tabs.vital", fallback: "体征", icon: Heart },
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
            {TABS.map(({ key, label, fallback, icon: Icon }) => (
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
                {t(label, fallback)}
              </button>
            ))}
          </div>
        </div>

        {/* Tab content */}
        <div className="flex-1 overflow-hidden">
          {activeTab === "overview" && <OverviewTab />}
          {activeTab === "monitoring" && <MonitoringTab />}
          {activeTab === "topology" && <TopologyTab />}
          {activeTab === "registry" && <RegistryTab />}
          {activeTab === "metrics" && <MetricsTab />}
          {activeTab === "trajectory" && <TrajectoryTab />}
          {activeTab === "intelligence" && <IntelligenceTab />}
          {activeTab === "architecture" && <AiEngineTab />}
          {activeTab === "vital" && <VitalTab />}
        </div>
      </div>
    </PageLayout>
  );
}
