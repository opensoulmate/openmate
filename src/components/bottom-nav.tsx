"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { useRef, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { getUserName, getUserId, clearUser } from "@/lib/api-client";
import {
  MessageSquare, Settings, User, LogOut, Terminal,
} from "lucide-react";
import {
  BOTTOM_DOMAINS, BUILTIN_DOMAINS, ICON_NAME_MAP, domainForPath, panelRoutes,
  type DomainDef, type L2Item,
} from "@/lib/menu-domains";
import {
  fetchPluginMenus, getCachedPluginMenus, resolvePluginMenus,
  type PluginMenu,
} from "@/lib/plugin-nav";
import { BottomBar as OpenFaceBottomBar } from "@opensoulmate/openface";

interface BottomNavItem {
  /** 仅管理员可见（非 admin 角色从导航过滤） */
  adminOnly?: boolean;
  href: string;
  label: string;
  icon: React.ElementType;
}

// 底栏一级位 = 功能域入口（docs/reports/menu-hierarchy-map.md 归属表）
// 滑动面板顺序从这里派生（swipeable-panels：单源防漂移）——域首页 + 二级条目全量。
export const baseNavItems: BottomNavItem[] = BOTTOM_DOMAINS.flatMap((domain) =>
  [
    { href: domain.home, label: domain.labelKey, icon: domain.icon },
    ...domain.items.map((item: L2Item) => ({
      href: item.href,
      label: item.labelKey,
      icon: item.icon,
    })),
  ].filter((entry, index, all) =>
    all.findIndex((e) => e.href === entry.href) === index,
  ),
);

/** 底栏一级位视图（内置核心域 + 插件贡献域，标签已解析） */
interface L1View {
  key: string;
  label: string;
  href: string;
  icon: React.ElementType;
  domainId: string;
  isChat?: boolean;
}

function buildBottomL1(
  t: ReturnType<typeof useTranslation>["t"],
  pluginMenus: PluginMenu[],
): L1View[] {
  const { own } = resolvePluginMenus(pluginMenus);
  const views: L1View[] = BOTTOM_DOMAINS.map((domain) => ({
    key: domain.id,
    label: t(domain.labelKey, domain.fallback),
    href: domain.home,
    icon: domain.icon,
    domainId: domain.id,
    isChat: domain.id === "chat",
  }));
  for (const menu of own) {
    // 插件贡献域（安装长出/卸载收走）——仅落底栏一级位的插件域
    const icon = ICON_NAME_MAP[menu.icon] || ICON_NAME_MAP.Puzzle;
    const view: L1View = {
      key: `plugin:${menu.pluginId}`,
      label: menu.label,
      href: menu.home,
      icon,
      domainId: `plugin:${menu.pluginId}`,
    };
    if (menu.position === "start") views.unshift(view);
    else views.push(view);
  }
  return views;
}

/** 当前路径归属的一级域 id（内置 + 插件域） */
export function activeDomainId(pathname: string, pluginMenus: PluginMenu[]): string | null {
  const { own } = resolvePluginMenus(pluginMenus);
  let best: { id: string; len: number } | null = null;
  const match = (href: string) => pathname === href || pathname.startsWith(`${href}/`);
  for (const menu of own) {
    for (const href of [menu.home, ...menu.entries.map((e) => e.href)]) {
      if (match(href) && (!best || href.length > best.len)) {
        best = { id: `plugin:${menu.pluginId}`, len: href.length };
      }
    }
  }
  for (const domain of BUILTIN_DOMAINS) {
    for (const href of [domain.home, ...domain.items.map((i) => i.href)]) {
      if (match(href) && (!best || href.length > best.len)) {
        best = { id: domain.id, len: href.length };
      }
    }
  }
  return best?.id ?? null;
}

interface BottomNavProps {
  totalUnread?: number;
  onOpenConversations?: () => void;
}

export function BottomNav({ totalUnread = 0, onOpenConversations }: BottomNavProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { t, i18n } = useTranslation();
  const activeRef = useRef<HTMLAnchorElement>(null);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const [menuPos, setMenuPos] = useState<{ bottom: number; left: number } | null>(null);

  // 插件贡献域（L1/L2 菜单动态生成：安装长出/卸载收走）
  const [pluginMenus, setPluginMenus] = useState<PluginMenu[]>([]);

  const userId = getUserName() || getUserId() || "用户";

  useEffect(() => {
    const refetch = () => {
      fetchPluginMenus(i18n.language).then(setPluginMenus).catch(() => {
        setPluginMenus(getCachedPluginMenus());
      });
    };
    refetch();
    // 插件装/卸后菜单即刻长出/收走（docs/plugin-spec.md §生命周期）
    window.addEventListener("openmate-plugins-changed", refetch);
    return () => window.removeEventListener("openmate-plugins-changed", refetch);
  }, [i18n.language]);

  // 底栏一级位：内置核心域 + 插件贡献域（功能域入口）
  const l1Items: L1View[] = buildBottomL1(t, pluginMenus);
  const currentDomainId = activeDomainId(pathname, pluginMenus);

  // Auto-scroll to active item
  useEffect(() => {
    if (activeRef.current) {
      activeRef.current.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
    }
  }, [pathname]);

  // Close user menu on outside click
  useEffect(() => {
    if (!userMenuOpen) return;
    const handler = (e: MouseEvent) => {
      const inBtn = userMenuRef.current && userMenuRef.current.contains(e.target as Node);
      const inPopup = popupRef.current && popupRef.current.contains(e.target as Node);
      if (!inBtn && !inPopup) setUserMenuOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [userMenuOpen]);

  function handleLogout() {
    clearUser();
    localStorage.removeItem("openmate-api-url");
    window.location.href = "/login";
  }

  return (
    <OpenFaceBottomBar
      left={
        <div className="relative w-12 h-full flex items-center justify-center" ref={userMenuRef}>
          <button
            onClick={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              setMenuPos({ bottom: window.innerHeight - r.top + 8, left: Math.min(Math.max(8, r.left), Math.max(8, window.innerWidth - 232)) });
              setUserMenuOpen(!userMenuOpen);
            }}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-[11px] font-semibold text-primary-foreground transition-transform hover:scale-105"
          >
            {userId[0].toUpperCase()}
          </button>

          {/* User popup menu */}
          {userMenuOpen && menuPos && createPortal(
            <div ref={popupRef} style={{ position: 'fixed', bottom: menuPos.bottom, left: menuPos.left }} className="w-56 rounded-xl border border-border bg-card shadow-lg overflow-hidden z-[100]">
              <div className="p-3 border-b border-border">
                <div className="flex items-center gap-2.5">
                  <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary text-sm font-medium text-primary-foreground">
                    {userId[0].toUpperCase()}
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate">{userId}</div>
                    <div className="text-[10px] text-muted-foreground">v0.1.0</div>
                  </div>
                </div>
              </div>
              <div className="py-1">
                <button onClick={() => { router.push('/settings#account'); setUserMenuOpen(false); }}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-sm hover:bg-muted/50 transition-colors">
                  <User size={14} className="text-muted-foreground" />
                  {t("account.viewProfile", "个人资料")}
                </button>
                <button onClick={() => { router.push('/settings'); setUserMenuOpen(false); }}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-sm hover:bg-muted/50 transition-colors">
                  <Settings size={14} className="text-muted-foreground" />
                  {t("nav.settings", "设置")}
                </button>
              </div>
              <div className="border-t border-border py-1">
                <button onClick={handleLogout}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-sm hover:bg-muted/50 transition-colors text-red-500">
                  <LogOut size={14} />
                  {t("account.logout", "退出登录")}
                </button>
              </div>
            </div>, document.body
          )}
        </div>
      }
      middle={
        <>
          {l1Items.map((item) => {
            // 一级位选中 = 当前路径归属该功能域（bg-primary/10 rounded，禁下划线）
            const active = currentDomainId === item.domainId;
            const Icon = item.icon;
            const isChatActive = item.isChat && active;
            return isChatActive ? (
              <button
                key={item.key}
                onClick={() => onOpenConversations?.()}
                className={cn(
                  "relative flex shrink-0 flex-col items-center justify-center gap-0.5 rounded-lg px-2.5 py-1.5 text-[9px] font-medium transition-colors min-w-[48px]",
                  "text-primary bg-primary/10"
                )}
              >
                <div className="relative">
                  <Icon size={16} strokeWidth={2.2} />
                  {totalUnread > 0 && (
                    <span className="absolute -top-1.5 -right-2 flex items-center justify-center min-w-[14px] h-3.5 px-0.5 rounded-full bg-red-500 text-white text-[7px] font-bold leading-none">
                      {totalUnread > 99 ? '99+' : totalUnread}
                    </span>
                  )}
                </div>
                <span className="truncate max-w-[56px] leading-tight">{item.label}</span>
              </button>
            ) : (
              <Link
                key={item.key}
                href={item.href}
                ref={active ? activeRef : undefined}
                className={cn(
                  "relative flex shrink-0 flex-col items-center justify-center gap-0.5 rounded-lg px-2.5 py-1.5 text-[9px] font-medium transition-colors min-w-[48px]",
                  active
                    ? "text-primary bg-primary/10"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent"
                )}
              >
                <div className="relative">
                  <Icon size={16} strokeWidth={active ? 2.2 : 1.5} />
                  {item.isChat && totalUnread > 0 && (
                    <span className="absolute -top-1.5 -right-2 flex items-center justify-center min-w-[14px] h-3.5 px-0.5 rounded-full bg-red-500 text-white text-[7px] font-bold leading-none">
                      {totalUnread > 99 ? '99+' : totalUnread}
                    </span>
                  )}
                </div>
                <span className="truncate max-w-[56px] leading-tight">{item.label}</span>
              </Link>
            );
          })}
        </>
      }
      right={
        <div className="relative w-12 h-full flex items-center justify-center">
          <button
            onClick={() => window.dispatchEvent(new CustomEvent('openmate-terminal-toggle'))}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-primary-foreground transition-transform hover:scale-105"
            title={t('terminal.title', '终端')}
          >
            <Terminal size={14} />
          </button>
        </div>
      }
    />
  );
}
