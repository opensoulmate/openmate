"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { useRef, useEffect, useCallback, useState } from "react";
import { createPortal } from "react-dom";
import { getUserName, getUserId, clearUser, getUserRole } from "@/lib/api-client";
import {
  MessageSquare, Users, BookOpen, Workflow, Settings, Brain, Activity,
  LayoutDashboard, Bell, Server, GraduationCap, Network, Share2, Search,
  FileText, Clock, GitBranch, Zap, Sparkles, Puzzle, Plug, FolderKanban,
  Camera, Download, Tag, User, Bot, Droplets, Dna, Eye, Shield, Bone,
  Volume2, Layers, Link2, Home, MousePointer, Mic, ImageIcon, Smile,
  Stethoscope, Cpu, Bolt, Heart, Gauge, BarChart3, Package, ScrollText,
  History, Store, Pill, LogOut, DollarSign, Terminal, Briefcase, GitMerge,
} from "lucide-react";
import { fetchPluginNavItems, getCachedPluginNavItems, mergePluginNavItems, PluginNavItem } from "@/lib/plugin-nav";
import { BottomBar as OpenFaceBottomBar } from "@opensoulmate/openface";

interface BottomNavItem {
  /** 仅管理员可见（非 admin 角色从导航过滤） */
  adminOnly?: boolean;
  href: string;
  label: string;
  icon: React.ElementType;
}

// 图标映射表 — 将字符串映射到lucide-react组件
const ICON_MAP: Record<string, React.ElementType> = {
  MessageSquare, Users, BookOpen, Workflow, Settings, Brain, Activity,
  LayoutDashboard, Bell, Server, GraduationCap, Network, Share2, Search,
  FileText, Clock, GitBranch, Zap, Sparkles, Puzzle, Plug, FolderKanban,
  Camera, Download, Tag, User, Bot, Droplets, Dna, Eye, Shield, Bone,
  Volume2, Layers, Link2, Home, MousePointer, Mic, ImageIcon, Smile,
  Stethoscope, Cpu, Bolt, Heart, Gauge, BarChart3, Package, ScrollText,
  History, Store, Pill, LogOut, DollarSign, Briefcase, GitMerge,
};

const DEFAULT_ICON = FileText;

// Settings is NOT in scrollable items — it's fixed on the right
const baseNavItems: BottomNavItem[] = [
  { href: "/chat", label: "nav.chat", icon: MessageSquare },
  { href: "/ai-groups", label: "nav.aiGroups", icon: Users },
  { href: "/knowledge", label: "nav.knowledge", icon: BookOpen },
  { href: "/learn", label: "nav.learn", icon: GraduationCap },
  { href: "/graph", label: "nav.graph", icon: Network },
  { href: "/knowledge-requests", label: "nav.kbSharing", icon: Share2 },
  { href: "/cron", label: "nav.cron", icon: Clock },
  { href: "/workflow", label: "nav.workflow", icon: Workflow },
  { href: "/pipeline", label: "nav.pipeline", icon: Zap },
  { href: "/skills", label: "nav.skills", icon: Puzzle },
  { href: "/mcp", label: "nav.mcp", icon: Plug },
  { href: "/workspace", label: "nav.workspace", icon: FolderKanban },
  { href: "/capture", label: "nav.capture", icon: Camera },
  { href: "/tags", label: "nav.tags", icon: Tag },
  { href: "/body-map", label: "nav.bodyMap", icon: User },
  { href: "/soma", label: "nav.soma", icon: Bot },
  { href: "/discovery", label: "nav.discovery", icon: Search },
  { href: "/cortex", label: "nav.cortex", icon: Cpu },
  { href: "/vein", label: "nav.vein", icon: Droplets },
  { href: "/gene", label: "nav.gene", icon: Dna },
  { href: "/vital", label: "nav.vital", icon: Activity },
  { href: "/gland", label: "nav.gland", icon: Zap },
  { href: "/hippo", label: "nav.hippo", icon: Brain },
  { href: "/reflex", label: "nav.reflex", icon: Bolt },
  { href: "/heredity", label: "nav.heredity", icon: GitBranch },
  { href: "/pulse", label: "nav.pulse", icon: Heart },
  { href: "/nerve", label: "nav.nerve", icon: Zap },
  { href: "/sense", label: "nav.sense", icon: Eye },
  { href: "/immune", label: "nav.immune", icon: Shield },
  { href: "/marrow", label: "nav.marrow", icon: Bone },
  { href: "/echo", label: "nav.echo", icon: Volume2 },
  { href: "/mirror", label: "nav.mirror", icon: Layers },
  { href: "/link", label: "nav.link", icon: Link2 },
  { href: "/nest", label: "nav.nest", icon: Home },
  { href: "/limb", label: "nav.limb", icon: MousePointer },
  { href: "/voice", label: "nav.voice", icon: Mic },
  { href: "/vision", label: "nav.vision", icon: ImageIcon },
  { href: "/mind", label: "nav.mind", icon: Smile },
  { href: "/soul", label: "nav.soul", icon: Brain },
  { href: "/soma-admin", adminOnly: true, label: "nav.somaAdmin", icon: Bot },
  { href: "/admin", adminOnly: true, label: "nav.admin", icon: Shield },
  { href: "/permission", adminOnly: true, label: "nav.permission", icon: Shield },
  { href: "/enterprise", label: "nav.enterprise", icon: Shield },
  { href: "/sessions", label: "nav.sessions", icon: History },
  { href: "/spending", label: "nav.spending", icon: DollarSign },
  { href: "/intelligence", label: "nav.intelligence", icon: Brain },
  { href: "/ai-engine", label: "nav.aiEngine", icon: Cpu },
  { href: "/healer", label: "nav.healer", icon: Pill },
  { href: "/topology", label: "nav.topology", icon: Network },
  { href: "/registry", label: "nav.registry", icon: Package },
  { href: "/trajectory", label: "nav.trajectory", icon: Activity },
  { href: "/timeline", label: "nav.timeline", icon: History },
  { href: "/changelog", label: "nav.changelog", icon: ScrollText },
  { href: "/docs", label: "文档中心", icon: FileText },
  { href: "/devlog", label: "开发记录", icon: ScrollText },
  { href: "/marketplace", label: "nav.marketplace", icon: Store },
];

interface BottomNavProps {
  totalUnread?: number;
  onOpenConversations?: () => void;
}

export function BottomNav({ totalUnread = 0, onOpenConversations }: BottomNavProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { t } = useTranslation();
  const activeRef = useRef<HTMLAnchorElement>(null);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const [menuPos, setMenuPos] = useState<{ bottom: number; left: number } | null>(null);

  // 插件导航项状态
  const [pluginNavItems, setPluginNavItems] = useState<PluginNavItem[]>([]);

  const userId = getUserName() || getUserId() || "用户";

  // 获取插件导航项
  useEffect(() => {
    fetchPluginNavItems().then(items => {
      setPluginNavItems(items);
    }).catch(() => {
      setPluginNavItems(getCachedPluginNavItems());
    });
  }, []);

  // 合并基础导航项和插件导航项
  const role = getUserRole();
  const navItems: BottomNavItem[] = [
    ...baseNavItems.filter(item => !item.adminOnly || role === 'admin'),
    ...pluginNavItems.map(item => ({
      href: item.href,
      label: item.label,
      icon: ICON_MAP[item.icon] || DEFAULT_ICON,
    })),
  ];

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
          {navItems.map((item) => {
            const active = pathname.startsWith(item.href);
            const Icon = item.icon;
            const isChat = item.href === '/chat';
            const isChatActive = isChat && active;
            return isChatActive ? (
              <button
                key={item.href}
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
                <span className="truncate max-w-[56px] leading-tight">{t(item.label)}</span>
              </button>
            ) : (
              <Link
                key={item.href}
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
                  {isChat && totalUnread > 0 && (
                    <span className="absolute -top-1.5 -right-2 flex items-center justify-center min-w-[14px] h-3.5 px-0.5 rounded-full bg-red-500 text-white text-[7px] font-bold leading-none">
                      {totalUnread > 99 ? '99+' : totalUnread}
                    </span>
                  )}
                </div>
                <span className="truncate max-w-[56px] leading-tight">{t(item.label)}</span>
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
