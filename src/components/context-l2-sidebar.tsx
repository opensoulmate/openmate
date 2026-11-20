"use client";

// L2ContextSidebar — 非器官页的左侧二级清单（左侧 = 当前一级的二级，用户三层架构）
//
// 2026-11-20 用户令："左侧=当前一级的二级清单，不是全局常驻器官表"：
// ①插件上下文（/plugins/<id>/*）：该插件菜单的二级条目（join 并入它的条目合节，
//   如 商机发现→智能投标）——插件装/卸即长出/收走（docs/plugin-spec.md §菜单契约）；
// ②内置域 join 条目（流程运行台→自动化域、进化回流→进化域）：并入目标域页面；
// ③无二级 → 不渲染（叶子页面不显示侧栏/紧凑态）。器官页不在本组件范围
//   （organ-nav-sidebar 承载，仅器官上下文挂载）。

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";
import { Puzzle, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { LeftPanel } from "@/components/left-panel";
import { domainForPath, ICON_NAME_MAP } from "@/lib/menu-domains";
import {
  fetchPluginMenus, getCachedPluginMenus,
  type PluginMenuItem, type PluginMenu,
} from "@/lib/plugin-nav";

export interface L2Row {
  key: string;
  href: string;
  label: string;
  icon: LucideIcon;
}

/**
 * 纯函数：当前路径 → 左侧二级清单行（插件上下文优先，其次内置域 join 条目）。
 * 无二级返回 []（=叶子页，不显示侧栏）。
 */
export function l2RowsFor(pathname: string, menus: PluginMenu[]): L2Row[] {
  const rows: L2Row[] = [];
  const iconOf = (name?: string) => ICON_NAME_MAP[name || ""] || ICON_NAME_MAP.Puzzle || Puzzle;

  // ①插件上下文：/plugins/<id>… 且该插件有菜单贡献（含 join 并入它的条目）
  const m = pathname.match(/^\/plugins\/([^/]+)/);
  if (m) {
    const id = m[1];
    const menu = menus.find((x) => x.pluginId === id);
    if (menu) {
      const entries: PluginMenuItem[] = [];
      for (const other of menus) {
        if (other !== menu && other.join === id) {
          entries.push(...other.entries.filter((e) => e.position === "start"));
        }
      }
      entries.push(...menu.entries);
      for (const other of menus) {
        if (other !== menu && other.join === id) {
          entries.push(...other.entries.filter((e) => e.position !== "start"));
        }
      }
      return entries.map((e) => ({
        key: `plugin:${id}:${e.id}`,
        href: e.href,
        label: e.label,
        icon: iconOf(menu.icon),
      }));
    }
  }

  // ②内置域 join 条目（如 流程运行台→自动化域 /workflow 族、进化回流→进化域）
  const domain = domainForPath(pathname);
  if (domain) {
    for (const menu of menus) {
      if (menu.join === domain.id) {
        rows.push(
          ...menu.entries.map((e) => ({
            key: `join:${menu.pluginId}:${e.id}`,
            href: e.href,
            label: e.label,
            icon: iconOf(menu.icon),
          })),
        );
      }
    }
  }
  return rows;
}

/** dumb 渲染块（app-shell 与 pageSidebar 拼接时用同款行样式） */
export function L2RowsBlock({ rows }: { rows: L2Row[] }) {
  const pathname = usePathname();
  const { t } = useTranslation();
  if (rows.length === 0) return null;
  return (
    <div className="flex flex-col py-1">
      <div className="px-3 pb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {t("sidebar.l2Section", "二级菜单")}
      </div>
      {rows.map((row) => {
        const Icon = row.icon;
        const active = pathname === row.href || pathname.startsWith(`${row.href}/`);
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
          </Link>
        );
      })}
    </div>
  );
}

/** 独立使用：插件上下文二级清单（无 join 拼接诉求的场景） */
export function L2ContextSidebar() {
  const pathname = usePathname();
  const { t, i18n } = useTranslation();
  const [menus, setMenus] = useState<PluginMenu[]>([]);

  useEffect(() => {
    const refetch = () => {
      fetchPluginMenus(i18n.language).then(setMenus).catch(() => {
        setMenus(getCachedPluginMenus());
      });
    };
    refetch();
    window.addEventListener("openmate-plugins-changed", refetch);
    return () => window.removeEventListener("openmate-plugins-changed", refetch);
  }, [i18n.language]);

  const rows = l2RowsFor(pathname, menus);
  if (rows.length === 0) return null;
  return (
    <LeftPanel
      placeholder={t("organNav.searchPlaceholder", "搜索导航...")}
      renderContent={(query) => (
        <L2RowsBlock
          rows={rows.filter(
            (row) =>
              !query ||
              row.label.toLowerCase().includes(query) ||
              row.href.includes(query),
          )}
        />
      )}
    />
  );
}
