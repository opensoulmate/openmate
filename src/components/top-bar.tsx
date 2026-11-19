"use client";

import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import {
  Bell, Search, Settings,
} from "lucide-react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { HealthWidget } from "@/components/health-widget";
import { SuperEllipse } from "@/components/ui/super-ellipse";
import { TOP_DOMAINS, domainForPath, type DomainDef } from "@/lib/menu-domains";
import { TopBar as OpenFaceTopBar } from "@opensoulmate/openface";

interface TopBarProps {
  eventCount?: number;
}

export function TopBar({ eventCount = 0 }: TopBarProps) {
  const { t } = useTranslation();
  const pathname = usePathname();

  // 顶栏一级位 = 功能域入口（通知/进化/系统管理；设置=右侧超椭圆钮）
  // 原顶栏 指标/仪表盘/下载/插件 已按归属表归入对应域二级（docs/reports/menu-hierarchy-map.md）
  const navItems: Array<{ id: string; icon: React.ElementType; label: string; href: string; badge?: number }> =
    TOP_DOMAINS.filter((domain: DomainDef) => domain.id !== "settings").map((domain: DomainDef) => ({
      id: domain.id,
      icon: domain.icon,
      label: t(domain.labelKey, domain.fallback),
      href: domain.home,
      ...(domain.id === "notify" && eventCount > 0 ? { badge: eventCount } : {}),
    }));

  const currentDomain = domainForPath(pathname);

  type NavItem = { id: string; icon: React.ElementType; label: string; href: string; badge?: number };

  const renderItem = (item: NavItem) => {
    const Icon = item.icon;
    // 一级位选中 = 当前路径归属该功能域（bg-primary/10 rounded，禁下划线）
    const active = currentDomain?.id === item.id;

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
          <img src="/app-icon-512.png" alt="OpenMate" className="h-6 sm:h-7 w-auto shrink-0" />
          <span className="text-sm font-semibold text-foreground hidden sm:inline">OpenMate</span>
        </Link>
      }
      middle={
        <>
          <HealthWidget />
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
            currentDomain?.id === "settings"
              ? "text-primary"
              : "text-muted-foreground hover:text-foreground"
          )}
          title={t("nav.dom.settings", "设置")}
        >
          <SuperEllipse
            size={36}
            fill={currentDomain?.id === "settings" ? "var(--color-primary)" : "var(--color-muted)"}
          >
            <Settings size={18} className={currentDomain?.id === "settings" ? "text-primary-foreground" : "text-muted-foreground"} />
          </SuperEllipse>
        </Link>
      }
    />
  );
}
