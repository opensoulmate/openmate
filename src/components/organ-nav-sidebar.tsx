'use client';

// OrganNavSidebar — 器官二级菜单（左栏形态①）
// 用户令（2026-11-20）："器官页左栏=25 器官一个不多一个不少，零工具零其他内容"——
// 核心 8 / 底座 8 / 高级 9，只在「器官」一级入口（/body-map）及器官详情页显示。
// 工具组已清（"那些一级菜单的东西为什么要在二级菜单再重复放一遍"）；
// 插件 join 拼接块已拆（用户没要）；布局走 l2-layout.tsx 统一模板。

import { usePathname } from "next/navigation";
import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";
import {
  Brain, Cpu, Zap, Droplets, Bot, Eye, Sparkles, Users,
  Shield, Activity, Bone, Stethoscope, Dna, Volume2, Layers, Link2,
  Bolt, GitBranch, Home, Heart, MousePointer, Mic, ImageIcon, Smile,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import { LeftPanel } from "@/components/left-panel";
import { L2List, L2Group, L2Row } from "@/components/l2-layout";

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
  items: NavEntry[];
}

// ── 25 器官：核心 8 / 底座 8 / 高级 9（一个不多一个不少）───────────────

const SECTIONS: NavSection[] = [
  {
    titleKey: "nav.core",
    titleFallback: "核心",
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
];

// ── 器官上下文判定（app-shell 用）────────────────────────────────────
// 器官二级菜单只在「器官」一级入口（/body-map）及器官详情页显示——
// 其他页面左侧=各自的二级子菜单或不显示，绝不见谁都弹 25 器官（用户令）。
const ORGAN_HREFS: string[] = SECTIONS.flatMap((s) => s.items.map((i) => i.href));

export function isOrganRoute(pathname: string): boolean {
  if (pathname === "/body-map" || pathname.startsWith("/body-map/")) return true;
  return ORGAN_HREFS.some((h) => pathname === h || pathname.startsWith(`${h}/`));
}

export function OrganNavSidebar() {
  const pathname = usePathname();
  const { t } = useTranslation();
  const [organHealth, setOrganHealth] = useState<Record<string, "ok" | "error" | "loading">>({});

  // 健康状态圆点（失败静默=纯导航；圆点在行内，不占额外内容）
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

  return (
    <LeftPanel
      placeholder={t("organNav.searchPlaceholder", "搜索导航...")}
      renderContent={(query) => (
        <L2List>
          {SECTIONS.map((section) => {
            const visible = section.items.filter(
              (it) =>
                !query ||
                t(it.labelKey, it.fallback).toLowerCase().includes(query) ||
                it.href.includes(query),
            );
            if (visible.length === 0) return null;
            return (
              <L2Group key={section.titleKey} title={t(section.titleKey, section.titleFallback)}>
                {visible.map((it) => {
                  const active = pathname === it.href || pathname.startsWith(`${it.href}/`);
                  const status = organHealth[it.key];
                  return (
                    <L2Row
                      key={it.key}
                      href={it.href}
                      icon={it.icon}
                      label={t(it.labelKey, it.fallback)}
                      tag={it.tag}
                      active={active}
                      right={
                        <span
                          className={cn(
                            "ml-auto w-2 h-2 rounded-full shrink-0",
                            status === "ok" && "bg-emerald-500",
                            status === "error" && "bg-red-500",
                            (!status || status === "loading") && "bg-muted-foreground/30 animate-pulse",
                          )}
                        />
                      }
                    />
                  );
                })}
              </L2Group>
            );
          })}
        </L2List>
      )}
    />
  );
}
