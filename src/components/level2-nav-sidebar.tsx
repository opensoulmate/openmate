'use client';

// Level2NavSidebar — 左侧二级菜单（跟随当前一级模块渲染其子功能清单）
//
// 信息架构（用户定稿 2026-11-19）：顶/底=一级（功能域），左侧=二级（当前域子功能）。
// 铁律：①只显示当前一级模块的子功能 ②一级项绝不允许在左侧重复出现
// ③平铺、无复读一级名的分组大标题 ④同一功能只在一个层级出现一次。
// 插件贡献域/插件 join 二级条目随安装动态生成、卸载收走（manifest 驱动，零硬编码）。
// 会话页/AI 群组页专属侧栏不动（app-shell 四态注册制，本组件只接默认态与拼接态）。

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";
import { Puzzle, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { getApiBaseUrl, getUserRole } from "@/lib/api-client";
import { LeftPanel } from "@/components/left-panel";
import {
  BUILTIN_DOMAINS, ICON_NAME_MAP, isAdminItem, type L2Item,
} from "@/lib/menu-domains";
import {
  fetchPluginMenus, getCachedPluginMenus, resolvePluginMenus,
  type PluginMenuItem, type PluginMenu,
} from "@/lib/plugin-nav";
import { activeDomainId } from "@/components/bottom-nav";

/** 二级清单行（内置域子功能 or 插件贡献条目，同形状渲染） */
interface Row {
  key: string;
  href: string;
  label: string;
  icon: LucideIcon;
  organKey?: string;
}

export function Level2NavSidebar() {
  const pathname = usePathname();
  const { t, i18n } = useTranslation();
  const [organHealth, setOrganHealth] = useState<Record<string, "ok" | "error" | "loading">>({});
  const [pluginMenus, setPluginMenus] = useState<PluginMenu[]>([]);

  useEffect(() => {
    const refetch = () => {
      fetchPluginMenus(i18n.language).then(setPluginMenus).catch(() => {
        setPluginMenus(getCachedPluginMenus());
      });
    };
    refetch();
    // 插件装/卸后二级菜单即刻长出/收走
    window.addEventListener("openmate-plugins-changed", refetch);
    return () => window.removeEventListener("openmate-plugins-changed", refetch);
  }, [i18n.language]);

  // 健康状态圆点（复用 /api/health/all，失败静默=纯导航）
  useEffect(() => {
    const apiBase = getApiBaseUrl();
    if (!apiBase) return;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    fetch(`${apiBase}/api/health/all`, { signal: ctrl.signal })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { if (data?.organs) setOrganHealth(data.organs); })
      .catch(() => {})
      .finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); ctrl.abort(); };
  }, []);

  // ── 当前一级模块 → 二级清单（子功能，零一级项重复）──────────────────
  const role = getUserRole();
  const domainId = activeDomainId(pathname, pluginMenus);
  const { joinEntries } = resolvePluginMenus(pluginMenus);
  const rows: Row[] = [];

  const pushEntry = (entry: PluginMenuItem) => {
    rows.push({
      key: `plugin-entry:${entry.href}`,
      href: entry.href,
      label: entry.label,
      icon: ICON_NAME_MAP.Puzzle ?? Puzzle,
    });
  };

  const builtin = BUILTIN_DOMAINS.find((d) => d.id === domainId);
  const pluginMenu = pluginMenus.find((m) => `plugin:${m.pluginId}` === domainId);

  if (builtin) {
    const joins = joinEntries.get(builtin.id);
    if (joins) joins.start.forEach(pushEntry);
    for (const item of builtin.items as L2Item[]) {
      if (!isAdminItem(item, role)) continue;
      rows.push({
        key: item.href,
        href: item.href,
        label: t(item.labelKey, item.fallback),
        icon: item.icon,
        organKey: item.organKey,
      });
    }
    if (joins) joins.end.forEach(pushEntry);
  } else if (pluginMenu) {
    const joins = joinEntries.get(`plugin:${pluginMenu.pluginId}`);
    if (joins) joins.start.forEach(pushEntry);
    for (const entry of pluginMenu.entries) pushEntry(entry);
    if (joins) joins.end.forEach(pushEntry);
  }

  return (
    <LeftPanel
      placeholder={t("organNav.searchPlaceholder", "搜索导航...")}
      renderContent={(query) => (
        <div className="flex flex-col py-1">
          {rows
            .filter(
              (row) =>
                !query ||
                row.label.toLowerCase().includes(query) ||
                row.href.includes(query),
            )
            .map((row) => {
              const Icon = row.icon;
              const active = pathname === row.href || pathname.startsWith(`${row.href}/`);
              const status = row.organKey ? organHealth[row.organKey] : undefined;
              return (
                <Link
                  key={row.key}
                  href={row.href}
                  className={cn(
                    "no-underline flex items-center gap-2 rounded px-3 py-2 text-xs text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground",
                    active && "bg-primary/10 text-primary hover:bg-primary/10 hover:text-primary",
                  )}
                >
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{row.label}</span>
                  {row.organKey && (
                    <span
                      className={cn(
                        "ml-auto w-2 h-2 rounded-full shrink-0",
                        status === "ok" && "bg-emerald-500",
                        status === "error" && "bg-red-500",
                        (!status || status === "loading") && "bg-muted-foreground/30 animate-pulse",
                      )}
                    />
                  )}
                </Link>
              );
            })}
        </div>
      )}
    />
  );
}
