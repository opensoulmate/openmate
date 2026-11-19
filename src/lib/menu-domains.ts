// menu-domains.ts — 一级菜单（功能域）+ 二级清单的唯一数据源
//
// 信息架构（用户定稿 2026-11-19）：顶部菜单和底部菜单为**一级菜单**（功能域入口），
// 左侧为**二级菜单**（跟随当前一级模块渲染其子功能）。归属表：docs/reports/menu-hierarchy-map.md。
// 铁律：①左二级只显示当前域子功能 ②一级项绝不允许在左侧重复出现（域标签≠页标签）
// ③二级平铺、无复读一级名的分组大标题 ④同一功能只在一个层级出现一次
// ⑤功能零删减（恢复令）——74 个现有一级入口逐个归位，路由全量可达。

import type { LucideIcon } from "lucide-react";
import {
  Activity, Bell, BookOpen, Bolt, Bot, Bone, Brain, Briefcase, Calculator, Camera, Clock, Code, Compass, Cpu,
  Download, Droplets, Dna, Eye, FileText, FolderKanban, GitBranch, GitMerge, GraduationCap,
  Heart, History, Home, ImageIcon, Inbox, Layers, Link2, LayoutDashboard, MessageSquare,
  Mic, MousePointer, Network, Package, Pill, Plug, Puzzle, ScrollText, Search,
  Settings, Share2, Shield, Smile, Sparkles, Store, Stethoscope, Tag, User, Users,
  Volume2, Workflow, Zap, Bookmark,
} from "lucide-react";

export interface L2Item {
  href: string;
  labelKey: string;
  fallback: string;
  icon: LucideIcon;
  /** 健康圆点键（器官页，复用 /api/health/all） */
  organKey?: string;
  adminOnly?: boolean;
}

export interface DomainDef {
  id: string;
  labelKey: string;
  fallback: string;
  icon: LucideIcon;
  home: string;
  bar: "top" | "bottom";
  items: L2Item[];
}

// ── 内置核心域（13 个，常驻）────────────────────────────────────────
// 二级清单 = 该域子功能页（平铺）；域首页标签与一级位标签不同名（零重复）。

export const BUILTIN_DOMAINS: DomainDef[] = [
  {
    id: "chat", labelKey: "nav.dom.chat", fallback: "对话", icon: MessageSquare,
    home: "/chat", bar: "bottom",
    items: [
      { href: "/chat", labelKey: "nav.sub.session", fallback: "会话", icon: MessageSquare },
      { href: "/sessions", labelKey: "nav.sessions", fallback: "会话管理", icon: History },
      { href: "/mate", labelKey: "nav.mate", fallback: "伙伴", icon: Users },
    ],
  },
  {
    id: "ai-groups", labelKey: "nav.dom.aiGroups", fallback: "AI 群组", icon: Users,
    home: "/ai-groups", bar: "bottom",
    items: [
      { href: "/ai-groups", labelKey: "nav.sub.groupChat", fallback: "群聊", icon: Users },
    ],
  },
  {
    id: "knowledge", labelKey: "nav.dom.knowledge", fallback: "知识管理", icon: BookOpen,
    home: "/knowledge", bar: "bottom",
    items: [
      { href: "/knowledge", labelKey: "nav.knowledge", fallback: "知识库", icon: BookOpen },
      { href: "/graph", labelKey: "nav.graph", fallback: "知识图谱", icon: Network },
      { href: "/tags", labelKey: "nav.tags", fallback: "标签管理", icon: Tag },
      { href: "/knowledge-requests", labelKey: "nav.sub.kbApproval", fallback: "共享审批", icon: Share2 },
      { href: "/search", labelKey: "nav.sub.searchAll", fallback: "统一搜索", icon: Search },
      { href: "/docs", labelKey: "nav.sub.docs", fallback: "文档中心", icon: FileText },
      { href: "/devlog", labelKey: "nav.sub.devlog", fallback: "开发记录", icon: ScrollText },
      { href: "/dev-specs", labelKey: "nav.sub.devSpecs", fallback: "规范", icon: FileText },
      { href: "/hippo", labelKey: "nav.hippo", fallback: "海马体", icon: Brain, organKey: "hippo" },
    ],
  },
  {
    id: "learn", labelKey: "nav.dom.learn", fallback: "学习", icon: GraduationCap,
    home: "/learn", bar: "bottom",
    items: [
      { href: "/learn", labelKey: "nav.sub.courses", fallback: "课程", icon: GraduationCap },
      { href: "/learn/create", labelKey: "nav.sub.createCourse", fallback: "创建课程", icon: Sparkles },
    ],
  },
  {
    id: "automation", labelKey: "nav.dom.automation", fallback: "自动化", icon: Workflow,
    home: "/workflow", bar: "bottom",
    items: [
      { href: "/workflow", labelKey: "nav.sub.flow", fallback: "流程", icon: Workflow },
      { href: "/cron", labelKey: "nav.cron", fallback: "定时任务", icon: Clock },
      { href: "/tasks", labelKey: "nav.tasks", fallback: "任务收发台", icon: Inbox },
      { href: "/pipeline", labelKey: "nav.pipeline", fallback: "流水线", icon: GitBranch },
      { href: "/pulse", labelKey: "nav.pulse", fallback: "脉搏", icon: Heart, organKey: "pulse" },
      { href: "/will", labelKey: "nav.will", fallback: "意志", icon: Sparkles, organKey: "will" },
      { href: "/nerve", labelKey: "nav.nerve", fallback: "神经", icon: Zap, organKey: "nerve" },
    ],
  },
  {
    id: "organs", labelKey: "nav.dom.organs", fallback: "器官", icon: User,
    home: "/body-map", bar: "bottom",
    items: [
      { href: "/body-map", labelKey: "nav.bodyMap", fallback: "躯体地图", icon: User },
      { href: "/soma", labelKey: "nav.soma", fallback: "躯体", icon: Bot, organKey: "soma" },
      { href: "/discovery", labelKey: "nav.discovery", fallback: "发现引擎", icon: Search },
      { href: "/capture", labelKey: "nav.capture", fallback: "采集", icon: Camera },
      { href: "/cortex", labelKey: "nav.cortex", fallback: "皮层", icon: Cpu, organKey: "cortex" },
      { href: "/sense", labelKey: "nav.sense", fallback: "感官", icon: Eye, organKey: "sense" },
      { href: "/mirror", labelKey: "nav.mirror", fallback: "镜像", icon: Layers, organKey: "mirror" },
      { href: "/link", labelKey: "nav.link", fallback: "突触", icon: Link2, organKey: "link" },
      { href: "/reflex", labelKey: "nav.reflex", fallback: "反射", icon: Bolt, organKey: "reflex" },
      { href: "/limb", labelKey: "nav.limb", fallback: "四肢", icon: MousePointer, organKey: "limb" },
      { href: "/voice", labelKey: "nav.voice", fallback: "声带", icon: Mic, organKey: "voice" },
      { href: "/vision", labelKey: "nav.vision", fallback: "视觉", icon: ImageIcon, organKey: "vision" },
      { href: "/mind", labelKey: "nav.mind", fallback: "心智", icon: Smile, organKey: "mind" },
    ],
  },
  {
    id: "workbench", labelKey: "nav.dom.workbench", fallback: "工作台", icon: FolderKanban,
    home: "/workspace", bar: "bottom",
    items: [
      { href: "/workspace", labelKey: "nav.workspace", fallback: "工作区", icon: FolderKanban },
      { href: "/download", labelKey: "nav.download", fallback: "下载管理", icon: Download },
      { href: "/vein", labelKey: "nav.vein", fallback: "血管", icon: Droplets, organKey: "vein" },
    ],
  },
  {
    id: "market", labelKey: "nav.dom.market", fallback: "能力市场", icon: Store,
    home: "/marketplace", bar: "bottom",
    items: [
      { href: "/marketplace", labelKey: "nav.sub.pluginMarket", fallback: "插件市场", icon: Store },
      { href: "/skills", labelKey: "nav.skills", fallback: "技能市场", icon: Puzzle },
      { href: "/plugins", labelKey: "nav.sub.pluginMgmt", fallback: "插件管理", icon: Plug },
      { href: "/mcp", labelKey: "nav.mcp", fallback: "MCP客户端", icon: Plug },
      { href: "/gene", labelKey: "nav.gene", fallback: "基因", icon: Dna, organKey: "gene" },
      { href: "/plugins/bookmarks", labelKey: "nav.sub.bookmarks", fallback: "书签", icon: Bookmark },
      { href: "/plugins/daily-digest", labelKey: "nav.dailyDigest", fallback: "每日摘要", icon: ScrollText },
      { href: "/plugins/pomodoro", labelKey: "nav.sub.pomodoro", fallback: "番茄钟", icon: Clock },
      { href: "/plugins/quick-notes", labelKey: "nav.sub.quickNotes", fallback: "便签", icon: FileText },
      { href: "/plugins/smart-calc", labelKey: "nav.sub.smartCalc", fallback: "计算器", icon: Calculator },
      { href: "/plugins/snippets", labelKey: "nav.sub.snippets", fallback: "代码片段", icon: Code },
      { href: "/plugins/system-doctor", labelKey: "nav.sub.systemDoctor", fallback: "系统医生", icon: Stethoscope },
    ],
  },
  {
    id: "notify", labelKey: "nav.dom.notify", fallback: "通知", icon: Bell,
    home: "/notifications", bar: "top",
    items: [
      { href: "/notifications", labelKey: "nav.notifications", fallback: "通知中心", icon: Bell },
      { href: "/echo", labelKey: "nav.echo", fallback: "回声", icon: Volume2, organKey: "echo" },
    ],
  },
  {
    id: "evolution", labelKey: "nav.dom.evolution", fallback: "进化", icon: Dna,
    home: "/evolution", bar: "top",
    items: [
      // /evolution（域首页）不再二级复读（一级位标签同名，铁律②）；以下为域内其余子功能
      { href: "/heredity", labelKey: "nav.heredity", fallback: "遗传链", icon: GitBranch, organKey: "heredity" },
      { href: "/changelog", labelKey: "nav.changelog", fallback: "版本日志", icon: ScrollText },
      { href: "/trajectory", labelKey: "nav.trajectory", fallback: "轨迹", icon: Activity },
      { href: "/timeline", labelKey: "nav.timeline", fallback: "时间线", icon: History },
    ],
  },
  {
    id: "system", labelKey: "nav.dom.system", fallback: "系统管理", icon: Shield,
    home: "/system", bar: "top",
    items: [
      { href: "/system", labelKey: "nav.system", fallback: "系统总览", icon: Shield },
      { href: "/dashboard", labelKey: "nav.dashboard", fallback: "仪表盘", icon: LayoutDashboard },
      { href: "/monitoring", labelKey: "nav.sub.monitoring", fallback: "监控", icon: Activity },
      { href: "/metrics", labelKey: "nav.metrics", fallback: "Prometheus指标", icon: LayoutDashboard },
      { href: "/topology", labelKey: "nav.topology", fallback: "系统拓扑", icon: Network },
      { href: "/registry", labelKey: "nav.registry", fallback: "组件注册表", icon: Package },
      { href: "/vital", labelKey: "nav.vital", fallback: "体征", icon: Activity, organKey: "vital" },
      { href: "/intelligence", labelKey: "nav.intelligence", fallback: "智能分析", icon: Brain },
      { href: "/ai-engine", labelKey: "nav.aiEngine", fallback: "AI 引擎", icon: Cpu },
      { href: "/gland", labelKey: "nav.gland", fallback: "腺体", icon: Stethoscope, organKey: "gland" },
      { href: "/spending", labelKey: "nav.spending", fallback: "开销", icon: Activity },
      { href: "/marrow", labelKey: "nav.marrow", fallback: "骨髓", icon: Bone, organKey: "marrow" },
      { href: "/immune", labelKey: "nav.immune", fallback: "免疫", icon: Shield, organKey: "immune" },
      { href: "/healer", labelKey: "nav.healer", fallback: "自愈系统", icon: Pill },
      { href: "/admin", labelKey: "nav.sub.adminConsole", fallback: "管理后台", icon: Shield },
      { href: "/permission", labelKey: "nav.permission", fallback: "权限管理", icon: Shield, adminOnly: true },
      { href: "/enterprise", labelKey: "nav.enterprise", fallback: "企业管理", icon: Shield },
      { href: "/soma-admin", labelKey: "nav.somaAdmin", fallback: "Soma管理", icon: Bot, adminOnly: true },
      { href: "/soul", labelKey: "nav.soul", fallback: "Soul管理", icon: Brain, organKey: "soul" },
      { href: "/nest", labelKey: "nav.nest", fallback: "巢穴", icon: Home, organKey: "nest" },
    ],
  },
  {
    id: "settings", labelKey: "nav.dom.settings", fallback: "设置", icon: Settings,
    home: "/settings", bar: "top",
    items: [
      // /settings（域首页）不二级复读（铁律②）；设置分节即页内上下文导航（pageSidebar）
      { href: "/onboarding", labelKey: "nav.sub.onboarding", fallback: "新手引导", icon: Compass },
    ],
  },
];

/** 底栏一级位（内置核心域） */
export const BOTTOM_DOMAINS = BUILTIN_DOMAINS.filter((d) => d.bar === "bottom");
/** 顶栏一级位（内置核心域） */
export const TOP_DOMAINS = BUILTIN_DOMAINS.filter((d) => d.bar === "top");

export function isAdminItem(item: L2Item, role: string | null | undefined): boolean {
  return !item.adminOnly || role === "admin";
}

/** 清单图标名 → lucide 组件（插件 manifest 声明 icon 字符串，宿主零硬编码菜单） */
export const ICON_NAME_MAP: Record<string, LucideIcon> = {
  Activity, Bell, BookOpen, Bolt, Bot, Bone, Brain, Briefcase, Calculator, Camera,
  Clock, Code, Compass, Cpu, Download, Droplets, Dna, Eye, FileText, FolderKanban,
  GitBranch, GitMerge, GraduationCap, Heart, History, Home, ImageIcon, Inbox, Layers,
  Link2, LayoutDashboard, MessageSquare, Mic, MousePointer, Network, Package, Pill,
  Plug, Puzzle, ScrollText, Search, Settings, Share2, Shield, Smile, Sparkles, Store,
  Stethoscope, Tag, User, Users, Volume2, Workflow, Zap, Bookmark,
};

/** 路由归属：最长匹配（域首页 + 二级条目全量参与），/knowledge-requests 不被 /knowledge 抢位 */
export function domainForPath(
  pathname: string,
  extraDomains: DomainDef[] = [],
): DomainDef | null {
  const all = [...extraDomains, ...BUILTIN_DOMAINS];
  let best: { domain: DomainDef; len: number } | null = null;
  const match = (href: string) =>
    pathname === href || pathname.startsWith(`${href}/`);
  for (const domain of all) {
    const hrefs = [domain.home, ...domain.items.map((i) => i.href)];
    for (const href of hrefs) {
      if (match(href) && (!best || href.length > best.len)) {
        best = { domain, len: href.length };
      }
    }
  }
  return best?.domain ?? null;
}

/** 滑动面板顺序 = 一级位顺序（自菜单派生，单源防漂移；见 swipeable-panels.tsx 注释） */
export function panelRoutes(extraDomains: DomainDef[] = []): string[] {
  const routes: string[] = [];
  for (const domain of [...BOTTOM_DOMAINS, ...extraDomains.filter((d) => d.bar === "bottom")]) {
    routes.push(domain.home);
    for (const item of domain.items) {
      if (!routes.includes(item.href)) routes.push(item.href);
    }
  }
  return routes;
}
