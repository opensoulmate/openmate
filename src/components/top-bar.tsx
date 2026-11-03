"use client";

import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import {
  Bell, Search, Download, Settings,
  BarChart3, Gauge, Shield, Plug, Dna,
} from "lucide-react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { HealthWidget } from "@/components/health-widget";
import { SuperEllipse } from "@/components/ui/super-ellipse";
import { TopBar as OpenFaceTopBar } from "@opensoulmate/openface";

interface TopBarProps {
  eventCount?: number;
}

export function TopBar({ eventCount = 0 }: TopBarProps) {
  const { t } = useTranslation();
  const pathname = usePathname();

  const statusItems = [
    { id: "metrics", icon: BarChart3, label: t("nav.metrics", "指标"), href: "/metrics" },
  ];

  const navItems = [
    { id: "notifications", icon: Bell, label: t("nav.notifications", "通知"), href: "/notifications", ...(eventCount > 0 ? { badge: eventCount } : {}) },
    { id: "dashboard", icon: Gauge, label: t("nav.dashboard", "仪表盘"), href: "/dashboard" },
    { id: "download", icon: Download, label: t("nav.download", "下载"), href: "/download" },
    { id: "evolution", icon: Dna, label: t("nav.evolution", "进化"), href: "/evolution" },
    { id: "plugins", icon: Plug, label: t("nav.plugins", "插件"), href: "/plugins" },
    { id: "system", icon: Shield, label: t("nav.system", "系统"), href: "/system" },
  ];

  type NavItem = { id: string; icon: React.ElementType; label: string; href: string; badge?: number };

  const renderItem = (item: NavItem) => {
    const Icon = item.icon;
    const active = item.href && pathname.startsWith(item.href);

    return (
      <Link
        key={item.id}
        href={item.href}
        className={cn(
          "relative flex shrink-0 items-center gap-1 rounded-md px-2 py-1.5 text-[10px] font-medium transition-colors",
          active
            ? "text-primary bg-primary/10"
            : "text-muted-foreground hover:text-foreground hover:bg-accent"
        )}
        title={item.label}
      >
        <div className="relative">
          <Icon size={14} />
          {item.badge && item.badge > 0 && (
            <span className="absolute -top-1 -right-1.5 flex items-center justify-center min-w-[12px] h-3 px-0.5 rounded-full bg-red-500 text-white text-[6px] font-bold leading-none">
              {item.badge > 99 ? '99+' : item.badge}
            </span>
          )}
        </div>
        <span className="hidden lg:inline truncate">{item.label}</span>
      </Link>
    );
  };

  return (
    <OpenFaceTopBar
      left={
        <Link href="/chat" className="flex items-center gap-1.5 shrink-0 px-3 hover:opacity-80 transition-opacity">
          <img src="/logo.svg" alt="OpenMate" className="w-7 h-7" />
          <span className="text-sm font-semibold text-foreground hidden sm:inline">OpenMate</span>
        </Link>
      }
      middle={
        <>
          <HealthWidget />
          {statusItems.map(renderItem)}
          <div className="w-px h-4 bg-border shrink-0 mx-1" />
          {navItems.map(renderItem)}
          <button
            onClick={() => document.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true, ctrlKey: true }))}
            className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1.5 text-[10px] font-medium transition-colors text-muted-foreground hover:text-foreground hover:bg-accent"
            title={t("nav.search", "搜索")}
          >
            <Search size={14} />
            <span className="hidden lg:inline truncate">{t("nav.search", "搜索")}</span>
            <kbd className="hidden lg:inline pointer-events-none select-none rounded border border-border bg-muted px-1 text-[9px] font-mono text-muted-foreground">⌘K</kbd>
          </button>
        </>
      }
      right={
        <Link
          href="/settings"
          className={cn(
            "flex shrink-0 items-center justify-center h-full px-3 transition-colors",
            pathname.startsWith("/settings")
              ? "text-primary"
              : "text-muted-foreground hover:text-foreground"
          )}
          title={t("nav.settings", "设置")}
        >
          <SuperEllipse
            size={36}
            fill={pathname.startsWith("/settings") ? "var(--color-primary)" : "var(--color-muted)"}
          >
            <Settings size={18} className={pathname.startsWith("/settings") ? "text-primary-foreground" : "text-muted-foreground"} />
          </SuperEllipse>
        </Link>
      }
    />
  );
}
