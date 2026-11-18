"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { Settings, Shield, Building2, Bot } from "lucide-react";
import { PageLayout } from "@/components/page-layout";
import { SystemTab } from "./tabs/system-tab";
import { PermissionTab } from "./tabs/permission-tab";
import { EnterpriseTab } from "./tabs/enterprise-tab";
import { SomaTab } from "./tabs/soma-tab";

type AdminTabId = "system" | "permission" | "enterprise" | "soma";

const TABS: { id: AdminTabId; labelKey: string; fallback: string; icon: React.ReactNode }[] = [
  { id: "system", labelKey: "admin.tabSystem", fallback: "系统管理", icon: <Settings size={14} /> },
  { id: "permission", labelKey: "admin.tabPermission", fallback: "权限管理", icon: <Shield size={14} /> },
  { id: "enterprise", labelKey: "admin.tabEnterprise", fallback: "企业管理", icon: <Building2 size={14} /> },
  { id: "soma", labelKey: "admin.tabSoma", fallback: "Soma管理", icon: <Bot size={14} /> },
];

export function AdminClient() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<AdminTabId>("system");

  return (
    <PageLayout title="管理">
      <div className="flex h-full flex-col overflow-hidden">
        {/* Unified Tab Bar */}
        <div className="flex items-center gap-1 border-b border-border px-3 lg:px-6 py-2 overflow-x-auto">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs lg:text-sm transition-colors whitespace-nowrap",
                activeTab === tab.id
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted-foreground hover:bg-muted"
              )}
            >
              {tab.icon}
              {t(tab.labelKey, tab.fallback)}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        <div className="flex-1 overflow-hidden">
          {activeTab === "system" && <SystemTab />}
          {activeTab === "permission" && <PermissionTab />}
          {activeTab === "enterprise" && <EnterpriseTab />}
          {activeTab === "soma" && <SomaTab />}
        </div>
      </div>
    </PageLayout>
  );
}
