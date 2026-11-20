'use client';

// OrganNavSidebar — 默认左栏（页面未注册 pageSidebar 时的兜底导航）
// 器官导航（25 组件三层）+ 工具页组；纯导航 + 健康状态圆点（复用 /api/health/all）。
// 2026-11-20 范围纠偏恢复原样（用户正解：只有器官收进二级菜单，其余菜单不动），
// 本形态即器官二级菜单正解：25 器官一个不少。插件菜单贡献（docs/plugin-spec.md）
// 在尾部分节渲染——安装长出/卸载收走，join 条目并入目标插件分节，宁可乱但全。

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";
import {
  Brain, Cpu, Zap, Droplets, Bot, Eye, Sparkles, Users,
  Shield, Activity, Bone, Stethoscope, Dna, Volume2, Layers, Link2,
  Bolt, GitBranch, Home, Heart, MousePointer, Mic, ImageIcon, Smile,
  LayoutDashboard, BookOpen, Search, Network, Workflow, Clock, Puzzle,
  Plug, FolderKanban, GraduationCap, Inbox,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import { LeftPanel } from "@/components/left-panel";
import {
  fetchPluginMenus, getCachedPluginMenus,
  type PluginMenuItem, type PluginMenu,
} from "@/lib/plugin-nav";

interface NavEntry {
  key: string;
  labelKey: string;
  fallback: string;
  tag?: string; // 4 字功能说明（与躯体地图同文案；名字后弱化小字）
  href: string;
  icon: LucideIcon;
}

interface NavSection {
  titleKey: string;
  titleFallback: string;
  hasHealth: boolean;
  items: NavEntry[];
}

// ── 25 器官：核心 8 / 底座 8 / 高级 9 ─────────────────────────────

const SECTIONS: NavSection[] = [
  {
    titleKey: "nav.core",
    titleFallback: "核心",
    hasHealth: true,
    items: [
      { key: "soul", labelKey: "nav.soul", fallback: "Soul", tag: "记忆中枢", href: "/soul", icon: Brain },
      { key: "cortex", labelKey: "nav.cortex", fallback: "皮层", tag: "认知规划", href: "/cortex", icon: Cpu },
      { key: "nerve", labelKey: "nav.nerve", fallback: "神经", tag: "事件总线", href: "/nerve", icon: Zap },
      { key: "vein", labelKey: "nav.vein", fallback: "血管", tag: "文件管理", href: "/vein", icon: Droplets },
      { key: "soma", labelKey: "nav.soma", fallback: "躯体", tag: "外部连接", href: "/soma", icon: Bot },
      { key: "sense", labelKey: "nav.sense", fallback: "感官", tag: "感知解析", href: "/sense", icon: Eye },
      { key: "will", labelKey: "nav.will", fallback: "意志", tag: "流程编排", href: "/will", icon: Sparkles },
      { key: "mate", labelKey: "nav.mate", fallback: "伙伴", tag: "客户端本体", href: "/mate", icon: Users },
    ],
  },
  {
    titleKey: "nav.baseTier",
    titleFallback: "底座",
    hasHealth: true,
    items: [
      { key: "immune", labelKey: "nav.immune", fallback: "免疫", tag: "安全防护", href: "/immune", icon: Shield },
      { key: "vital", labelKey: "nav.vital", fallback: "体征", tag: "系统监控", href: "/vital", icon: Activity },
      { key: "marrow", labelKey: "nav.marrow", fallback: "骨髓", tag: "备份恢复", href: "/marrow", icon: Bone },
      { key: "gland", labelKey: "nav.gland", fallback: "腺体", tag: "模型网关", href: "/gland", icon: Stethoscope },
      { key: "gene", labelKey: "nav.gene", fallback: "基因", tag: "预制模板", href: "/gene", icon: Dna },
      { key: "echo", labelKey: "nav.echo", fallback: "回声", tag: "消息推送", href: "/echo", icon: Volume2 },
      { key: "mirror", labelKey: "nav.mirror", fallback: "镜像", tag: "沙箱测试", href: "/mirror", icon: Layers },
      { key: "link", labelKey: "nav.link", fallback: "突触", tag: "双向集成", href: "/link", icon: Link2 },
    ],
  },
  {
    titleKey: "nav.advancedTier",
    titleFallback: "高级",
    hasHealth: true,
    items: [
      { key: "hippo", labelKey: "nav.hippo", fallback: "海马体", tag: "记忆管理", href: "/hippo", icon: Brain },
      { key: "reflex", labelKey: "nav.reflex", fallback: "反射", tag: "高速应答", href: "/reflex", icon: Bolt },
      { key: "heredity", labelKey: "nav.heredity", fallback: "遗传链", tag: "版本演化", href: "/heredity", icon: GitBranch },
      { key: "nest", labelKey: "nav.nest", fallback: "巢穴", tag: "租户隔离", href: "/nest", icon: Home },
      { key: "pulse", labelKey: "nav.pulse", fallback: "脉搏", tag: "时序节拍", href: "/pulse", icon: Heart },
      { key: "limb", labelKey: "nav.limb", fallback: "四肢", tag: "RPA执行", href: "/limb", icon: MousePointer },
      { key: "voice", labelKey: "nav.voice", fallback: "声带", tag: "语音合成", href: "/voice", icon: Mic },
      { key: "vision", labelKey: "nav.vision", fallback: "视觉", tag: "图表绘制", href: "/vision", icon: ImageIcon },
      { key: "mind", labelKey: "nav.mind", fallback: "心智", tag: "人格调节", href: "/mind", icon: Smile },
    ],
  },
  {
    titleKey: "nav.toolsGroup",
    titleFallback: "工具",
    hasHealth: false,
    items: [
      { key: "dashboard", labelKey: "nav.dashboard", fallback: "仪表盘", href: "/dashboard", icon: LayoutDashboard },
      { key: "knowledge", labelKey: "nav.knowledge", fallback: "知识库", href: "/knowledge", icon: BookOpen },
      { key: "search", labelKey: "nav.search", fallback: "搜索", href: "/search", icon: Search },
      { key: "graph", labelKey: "nav.graph", fallback: "知识图谱", href: "/graph", icon: Network },
      { key: "workflow", labelKey: "nav.workflow", fallback: "工作流", href: "/workflow", icon: Workflow },
      { key: "tasks", labelKey: "nav.tasks", fallback: "任务收发台", href: "/tasks", icon: Inbox },
      { key: "cron", labelKey: "nav.cron", fallback: "定时任务", href: "/cron", icon: Clock },
      { key: "skills", labelKey: "nav.skills", fallback: "技能市场", href: "/skills", icon: Puzzle },
      { key: "mcp", labelKey: "nav.mcp", fallback: "MCP客户端", href: "/mcp", icon: Plug },
      { key: "workspace", labelKey: "nav.workspace", fallback: "工作区", href: "/workspace", icon: FolderKanban },
      { key: "learn", labelKey: "nav.learn", fallback: "学习", href: "/learn", icon: GraduationCap },
    ],
  },
];

// ── 器官上下文判定（app-shell 用）────────────────────────────────────
// 器官二级菜单只在「器官」一级入口（/body-map）及器官详情页显示——
// 其他页面左侧=各自的二级子菜单或不显示，绝不见谁都弹 25 器官（用户令）。
const ORGAN_HREFS: string[] = SECTIONS.slice(0, 3).flatMap((s) => s.items.map((i) => i.href));

export function isOrganRoute(pathname: string): boolean {
  if (pathname === "/body-map" || pathname.startsWith("/body-map/")) return true;
  return ORGAN_HREFS.some((h) => pathname === h || pathname.startsWith(`${h}/`));
}

export function OrganNavSidebar() {
  const pathname = usePathname();
  const { t, i18n } = useTranslation();
  const [organHealth, setOrganHealth] = useState<Record<string, "ok" | "error" | "loading">>({});
  const [pluginMenus, setPluginMenus] = useState<PluginMenu[]>([]);

  // 插件菜单贡献（清单驱动，安装长出/卸载收走）
  useEffect(() => {
    const refetch = () => {
      fetchPluginMenus(i18n.language).then(setPluginMenus).catch(() => {
        setPluginMenus(getCachedPluginMenus());
      });
    };
    refetch();
    window.addEventListener("openmate-plugins-changed", refetch);
    return () => window.removeEventListener("openmate-plugins-changed", refetch);
  }, [i18n.language]);

  // 健康状态圆点（复用 dashboard 的 /api/health/all 写法，失败静默=纯导航）
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

  const refreshHealth = () => {
    const apiBase = getApiBaseUrl();
    if (!apiBase) return;
    fetch(`${apiBase}/api/health/all`, { signal: AbortSignal.timeout(8000) })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { if (data?.organs) setOrganHealth(data.organs); })
      .catch(() => {});
  };

  // ── 插件菜单贡献分节（宁可乱但全）────────────────────────────────────
  // 每个插件菜单一个分节；join 并入目标插件（如 商机发现→智能投标）时合并进
  // 目标分节；join 目标缺席/并入内置域的（流程运行台→自动化、进化回流→进化）
  // 自立分节——插件二级条目一条不少、零手写死。
  interface PluginSection {
    key: string;
    title: string;
    items: PluginMenuItem[];
  }
  const pluginSections: PluginSection[] = (() => {
    const byId = new Map(pluginMenus.map((m) => [m.pluginId, m]));
    const merged = new Map<string, PluginSection>();
    for (const menu of pluginMenus) {
      const targetId =
        menu.join && byId.has(menu.join) && menu.join !== menu.pluginId
          ? menu.join
          : menu.pluginId;
      let section = merged.get(targetId);
      if (!section) {
        section = {
          key: `plugin-section:${targetId}`,
          title: (byId.get(targetId) || menu).label,
          items: [],
        };
        merged.set(targetId, section);
      }
      section.items.push(...menu.entries);
    }
    return [...merged.values()];
  })();

  return (
    <LeftPanel
      placeholder={t("organNav.searchPlaceholder", "搜索导航...")}
      header={
        <div className="px-3 pb-2 flex items-center gap-3 text-[10px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-emerald-500" />
            {Object.values(organHealth).filter((s) => s === "ok").length}
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-red-500" />
            {Object.values(organHealth).filter((s) => s === "error").length}
          </span>
          <button
            onClick={refreshHealth}
            className="ml-auto text-muted-foreground hover:text-foreground transition-colors"
            aria-label={t("organNav.refreshHealth", "刷新健康状态")}
          >
            ↻
          </button>
        </div>
      }
      renderContent={(query) => (
        <div className="flex flex-col gap-3 py-1">
          {SECTIONS.map((section) => {
            const visible = section.items.filter(
              (it) =>
                !query ||
                t(it.labelKey, it.fallback).toLowerCase().includes(query) ||
                it.href.includes(query),
            );
            if (visible.length === 0) return null;
            return (
              <div key={section.titleKey}>
                <div className="px-3 pb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  {t(section.titleKey, section.titleFallback)}
                </div>
                <div className="flex flex-col">
                  {visible.map((it) => {
                    const Icon = it.icon;
                    const active = pathname === it.href || pathname.startsWith(`${it.href}/`);
                    const status = organHealth[it.key];
                    return (
                      <Link
                        key={it.key}
                        href={it.href}
                        className={cn(
                          "no-underline flex items-center gap-2 rounded px-3 py-2 text-xs text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground",
                          active && "bg-primary/10 text-primary hover:bg-primary/10 hover:text-primary",
                        )}
                      >
                        <Icon className="h-3.5 w-3.5 shrink-0" />
                        <span className="truncate">{t(it.labelKey, it.fallback)}</span>
                        {it.tag && (
                          <span className="shrink-0 text-[10px] text-muted-foreground/60">{it.tag}</span>
                        )}
                        {section.hasHealth && (
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
              </div>
            );
          })}
          {pluginSections
            .filter(
              (section) =>
                !query ||
                section.title.toLowerCase().includes(query) ||
                section.items.some(
                  (it) =>
                    it.label.toLowerCase().includes(query) || it.href.includes(query),
                ),
            )
            .map((section) => {
              const visible = section.items.filter(
                (it) =>
                  !query ||
                  it.label.toLowerCase().includes(query) ||
                  it.href.includes(query),
              );
              if (visible.length === 0) return null;
              return (
                <div key={section.key}>
                  <div className="px-3 pb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                    {section.title}
                  </div>
                  <div className="flex flex-col">
                    {visible.map((it) => {
                      const active =
                        pathname === it.href || pathname.startsWith(`${it.href}/`);
                      return (
                        <Link
                          key={it.id}
                          href={it.href}
                          className={cn(
                            "no-underline flex items-center gap-2 rounded px-3 py-2 text-xs text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground",
                            active && "bg-primary/10 text-primary hover:bg-primary/10 hover:text-primary",
                          )}
                        >
                          <Puzzle className="h-3.5 w-3.5 shrink-0" />
                          <span className="truncate">{it.label}</span>
                        </Link>
                      );
                    })}
                  </div>
                </div>
              );
            })}
        </div>
      )}
    />
  );
}
