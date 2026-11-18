"use client";

import { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import {
  MessageSquare, BookOpen, Network, Search, GraduationCap,
  Server, Settings, Plus, RefreshCw, Trash2, Moon, Sun,
  Palette, Command, LayoutDashboard, Bell, Users, Puzzle,
  Plug, FolderKanban, Camera, Download, Clock, Workflow,
  GitBranch, Zap, Sparkles, Bot, Cpu, Droplets, Dna,
  Activity, Brain, Bolt, Heart, Home, MousePointer, Mic,
  ImageIcon, Smile, Shield, Bone, Volume2, Layers, Link2,
  Eye, Stethoscope, Gauge, Pill, ScrollText, Store,
  History, Share2, User, FileText, MessageCircle,
} from "lucide-react";

interface CommandItem {
  id: string;
  label: string;
  labelEn: string;
  labelJa: string;
  icon: React.ReactNode;
  action: () => void;
  keywords: string[];
  group: string;
  groupEn: string;
  groupJa: string;
}

const MAX_RECENT = 8;
const RECENT_KEY = "openmate-cmd-recent";

function getRecentIds(): string[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
  } catch { return []; }
}

function pushRecent(id: string) {
  const recent = getRecentIds().filter((r) => r !== id);
  recent.unshift(id);
  localStorage.setItem(RECENT_KEY, JSON.stringify(recent.slice(0, MAX_RECENT)));
}

export function CommandMenu() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const { t, i18n } = useTranslation();
  const lang = i18n.language?.startsWith("zh") ? "zh" : i18n.language?.startsWith("ja") ? "ja" : "en";

  // All navigation items from the sidebar
  const commands: CommandItem[] = useMemo(() => [
    // Core
    { id: "nav-chat", label: "对话", labelEn: "Chat", labelJa: "チャット", icon: <MessageSquare size={16} />, action: () => router.push("/chat"), keywords: ["chat", "对话", "talk"], group: "核心", groupEn: "Core", groupJa: "コア" },
    { id: "nav-dashboard", label: "仪表盘", labelEn: "Dashboard", labelJa: "ダッシュボード", icon: <LayoutDashboard size={16} />, action: () => router.push("/dashboard"), keywords: ["dashboard", "仪表", "home"], group: "核心", groupEn: "Core", groupJa: "コア" },
    { id: "nav-notifications", label: "通知中心", labelEn: "Notifications", labelJa: "通知", icon: <Bell size={16} />, action: () => router.push("/notifications"), keywords: ["notifications", "通知", "alert"], group: "核心", groupEn: "Core", groupJa: "コア" },

    // AI Collaboration
    { id: "nav-ai-groups", label: "AI群", labelEn: "AI Groups", labelJa: "AIグループ", icon: <Users size={16} />, action: () => router.push("/ai-groups"), keywords: ["ai", "群", "group", "swarm"], group: "AI协作", groupEn: "AI Collaboration", groupJa: "AI協力" },
    { id: "nav-agents", label: "Agent 管理", labelEn: "Agent Manager", labelJa: "エージェント管理", icon: <Server size={16} />, action: () => router.push("/settings#agentManage"), keywords: ["agent", "节点", "node", "管理", "settings"], group: "AI协作", groupEn: "AI Collaboration", groupJa: "AI協力" },
    { id: "nav-groups", label: "群组", labelEn: "Groups", labelJa: "グループ", icon: <Users size={16} />, action: () => router.push("/groups"), keywords: ["groups", "群组", "team"], group: "AI协作", groupEn: "AI Collaboration", groupJa: "AI協力" },
    { id: "nav-team", label: "团队", labelEn: "Team", labelJa: "チーム", icon: <Users size={16} />, action: () => router.push("/team"), keywords: ["team", "团队", "协作"], group: "AI协作", groupEn: "AI Collaboration", groupJa: "AI協力" },

    // Knowledge
    { id: "nav-knowledge", label: "知识库", labelEn: "Knowledge", labelJa: "ナレッジ", icon: <BookOpen size={16} />, action: () => router.push("/knowledge"), keywords: ["knowledge", "知识", "kb"], group: "知识管理", groupEn: "Knowledge", groupJa: "ナレッジ" },
    { id: "nav-learn", label: "学习", labelEn: "Learn", labelJa: "学習", icon: <GraduationCap size={16} />, action: () => router.push("/learn"), keywords: ["learn", "学习", "course", "课程"], group: "知识管理", groupEn: "Knowledge", groupJa: "ナレッジ" },
    { id: "nav-graph", label: "图谱", labelEn: "Graph", labelJa: "グラフ", icon: <Network size={16} />, action: () => router.push("/graph"), keywords: ["graph", "图谱", "知识图"], group: "知识管理", groupEn: "Knowledge", groupJa: "ナレッジ" },
    { id: "nav-graph-builder", label: "图谱编排", labelEn: "Graph Builder", labelJa: "グラフビルダー", icon: <Share2 size={16} />, action: () => router.push("/graph-builder"), keywords: ["graph", "builder", "编排"], group: "知识管理", groupEn: "Knowledge", groupJa: "ナレッジ" },
    { id: "nav-search", label: "搜索", labelEn: "Search", labelJa: "検索", icon: <Search size={16} />, action: () => router.push("/search"), keywords: ["search", "搜索", "find"], group: "知识管理", groupEn: "Knowledge", groupJa: "ナレッジ" },

    // Automation
    { id: "nav-cron", label: "定时任务", labelEn: "Cron Jobs", labelJa: "定时ジョブ", icon: <Clock size={16} />, action: () => router.push("/cron"), keywords: ["cron", "定时", "schedule"], group: "自动化", groupEn: "Automation", groupJa: "自動化" },
    { id: "nav-workflow", label: "工作流", labelEn: "Workflow", labelJa: "ワークフロー", icon: <Workflow size={16} />, action: () => router.push("/workflow"), keywords: ["workflow", "工作流"], group: "自动化", groupEn: "Automation", groupJa: "自動化" },
    { id: "nav-workflow-builder", label: "工作流编排", labelEn: "Workflow Builder", labelJa: "ワークフロービルダー", icon: <GitBranch size={16} />, action: () => router.push("/workflow"), keywords: ["workflow", "builder", "编排"], group: "自动化", groupEn: "Automation", groupJa: "自動化" },
    { id: "nav-pipeline", label: "流水线", labelEn: "Pipeline", labelJa: "パイプライン", icon: <Zap size={16} />, action: () => router.push("/pipeline"), keywords: ["pipeline", "流水线"], group: "自动化", groupEn: "Automation", groupJa: "自動化" },
    { id: "nav-will", label: "意志", labelEn: "Will", labelJa: "意志", icon: <Sparkles size={16} />, action: () => router.push("/will"), keywords: ["will", "意志"], group: "自动化", groupEn: "Automation", groupJa: "自動化" },

    // Tools
    { id: "nav-skills", label: "技能", labelEn: "Skills", labelJa: "スキル", icon: <Puzzle size={16} />, action: () => router.push("/skills"), keywords: ["skills", "技能"], group: "工具", groupEn: "Tools", groupJa: "ツール" },
    { id: "nav-mcp", label: "MCP", labelEn: "MCP", labelJa: "MCP", icon: <Plug size={16} />, action: () => router.push("/mcp"), keywords: ["mcp", "protocol"], group: "工具", groupEn: "Tools", groupJa: "ツール" },
    { id: "nav-workspace", label: "工作区", labelEn: "Workspace", labelJa: "ワークスペース", icon: <FolderKanban size={16} />, action: () => router.push("/workspace"), keywords: ["workspace", "工作区", "file"], group: "工具", groupEn: "Tools", groupJa: "ツール" },
    { id: "nav-capture", label: "采集", labelEn: "Capture", labelJa: "キャプチャ", icon: <Camera size={16} />, action: () => router.push("/capture"), keywords: ["capture", "采集", "scrape"], group: "工具", groupEn: "Tools", groupJa: "ツール" },
    { id: "nav-download", label: "下载", labelEn: "Download", labelJa: "ダウンロード", icon: <Download size={16} />, action: () => router.push("/download"), keywords: ["download", "下载"], group: "工具", groupEn: "Tools", groupJa: "ツール" },

    // Organs
    { id: "nav-soma", label: "躯体", labelEn: "Soma", labelJa: "ソマ", icon: <Bot size={16} />, action: () => router.push("/soma"), keywords: ["soma", "躯体", "body"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-cortex", label: "皮层", labelEn: "Cortex", labelJa: "皮質", icon: <Cpu size={16} />, action: () => router.push("/cortex"), keywords: ["cortex", "皮层", "reasoning"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-vein", label: "血管", labelEn: "Vein", labelJa: "血管", icon: <Droplets size={16} />, action: () => router.push("/vein"), keywords: ["vein", "血管", "file", "cache"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-gene", label: "基因", labelEn: "Gene", labelJa: "遺伝子", icon: <Dna size={16} />, action: () => router.push("/gene"), keywords: ["gene", "基因", "template"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-vital", label: "体征", labelEn: "Vital", labelJa: "バイタル", icon: <Activity size={16} />, action: () => router.push("/vital"), keywords: ["vital", "体征", "monitor"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-gland", label: "腺体", labelEn: "Gland", labelJa: "腺", icon: <Zap size={16} />, action: () => router.push("/gland"), keywords: ["gland", "腺体", "model", "llm"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-hippo", label: "海马体", labelEn: "Hippo", labelJa: "海馬", icon: <Brain size={16} />, action: () => router.push("/hippo"), keywords: ["hippo", "海马", "memory"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-reflex", label: "反射", labelEn: "Reflex", labelJa: "反射", icon: <Bolt size={16} />, action: () => router.push("/reflex"), keywords: ["reflex", "反射", "cache"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-heredity", label: "遗传", labelEn: "Heredity", labelJa: "遺伝", icon: <GitBranch size={16} />, action: () => router.push("/heredity"), keywords: ["heredity", "遗传", "version"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-pulse", label: "脉搏", labelEn: "Pulse", labelJa: "脈拍", icon: <Heart size={16} />, action: () => router.push("/pulse"), keywords: ["pulse", "脉搏", "timer"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-nerve", label: "神经", labelEn: "Nerve", labelJa: "神経", icon: <Zap size={16} />, action: () => router.push("/nerve"), keywords: ["nerve", "神经", "event"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-sense", label: "感官", labelEn: "Sense", labelJa: "感覚", icon: <Eye size={16} />, action: () => router.push("/sense"), keywords: ["sense", "感官", "ocr", "asr"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-immune", label: "免疫", labelEn: "Immune", labelJa: "免疫", icon: <Shield size={16} />, action: () => router.push("/immune"), keywords: ["immune", "免疫", "security"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-marrow", label: "骨髓", labelEn: "Marrow", labelJa: "骨髄", icon: <Bone size={16} />, action: () => router.push("/marrow"), keywords: ["marrow", "骨髓", "backup"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-echo", label: "回声", labelEn: "Echo", labelJa: "エコー", icon: <Volume2 size={16} />, action: () => router.push("/echo"), keywords: ["echo", "回声", "push", "notify"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-mirror", label: "镜像", labelEn: "Mirror", labelJa: "ミラー", icon: <Layers size={16} />, action: () => router.push("/mirror"), keywords: ["mirror", "镜像", "sandbox"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-link", label: "突触", labelEn: "Link", labelJa: "リンク", icon: <Link2 size={16} />, action: () => router.push("/link"), keywords: ["link", "突触", "webhook"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-nest", label: "巢穴", labelEn: "Nest", labelJa: "ネスト", icon: <Home size={16} />, action: () => router.push("/nest"), keywords: ["nest", "巢穴", "tenant"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-limb", label: "四肢", labelEn: "Limb", labelJa: "四肢", icon: <MousePointer size={16} />, action: () => router.push("/limb"), keywords: ["limb", "四肢", "rpa"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-voice", label: "声带", labelEn: "Voice", labelJa: "ボイス", icon: <Mic size={16} />, action: () => router.push("/voice"), keywords: ["voice", "声带", "tts"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-vision", label: "视觉", labelEn: "Vision", labelJa: "ビジョン", icon: <ImageIcon size={16} />, action: () => router.push("/vision"), keywords: ["vision", "视觉", "chart"], group: "器官", groupEn: "Organs", groupJa: "器官" },
    { id: "nav-mind", label: "心智", labelEn: "Mind", labelJa: "マインド", icon: <Smile size={16} />, action: () => router.push("/mind"), keywords: ["mind", "心智", "emotion", "personality"], group: "器官", groupEn: "Organs", groupJa: "器官" },

    // System
    { id: "nav-system", label: "系统总览", labelEn: "System Overview", labelJa: "システム概要", icon: <Server size={16} />, action: () => router.push("/system"), keywords: ["system", "系统", "overview"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-admin", label: "管理", labelEn: "Admin", labelJa: "管理", icon: <Shield size={16} />, action: () => router.push("/admin"), keywords: ["admin", "管理"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-diagnostics", label: "诊断", labelEn: "Diagnostics", labelJa: "診断", icon: <Stethoscope size={16} />, action: () => router.push("/diagnostics"), keywords: ["diagnostics", "诊断", "health"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-benchmark", label: "基准测试", labelEn: "Benchmark", labelJa: "ベンチマーク", icon: <Gauge size={16} />, action: () => router.push("/benchmark"), keywords: ["benchmark", "基准", "perf"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-intelligence", label: "智能分析", labelEn: "Intelligence", labelJa: "インテリジェンス", icon: <Brain size={16} />, action: () => router.push("/intelligence"), keywords: ["intelligence", "智能", "insight"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-healer", label: "自愈", labelEn: "Healer", labelJa: "ヒーラー", icon: <Pill size={16} />, action: () => router.push("/healer"), keywords: ["healer", "自愈", "repair"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-topology", label: "拓扑", labelEn: "Topology", labelJa: "トポロジ", icon: <Network size={16} />, action: () => router.push("/topology"), keywords: ["topology", "拓扑", "map"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-trajectory", label: "轨迹", labelEn: "Trajectory", labelJa: "トラジェクトリ", icon: <Activity size={16} />, action: () => router.push("/trajectory"), keywords: ["trajectory", "轨迹", "trace"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-timeline", label: "时间线", labelEn: "Timeline", labelJa: "タイムライン", icon: <History size={16} />, action: () => router.push("/timeline"), keywords: ["timeline", "时间线", "history"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-changelog", label: "变更日志", labelEn: "Changelog", labelJa: "変更履歴", icon: <ScrollText size={16} />, action: () => router.push("/changelog"), keywords: ["changelog", "变更", "log"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-docs", label: "文档中心", labelEn: "Docs Center", labelJa: "ドキュメントセンター", icon: <FileText size={16} />, action: () => router.push("/docs"), keywords: ["docs", "文档", "规范", "spec", "document"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-plugins", label: "插件", labelEn: "Plugins", labelJa: "プラグイン", icon: <Puzzle size={16} />, action: () => router.push("/plugins"), keywords: ["plugins", "插件"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-marketplace", label: "市场", labelEn: "Marketplace", labelJa: "マーケット", icon: <Store size={16} />, action: () => router.push("/marketplace"), keywords: ["marketplace", "市场", "store"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-settings", label: "设置", labelEn: "Settings", labelJa: "設定", icon: <Settings size={16} />, action: () => router.push("/settings"), keywords: ["settings", "设置", "config"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-activity", label: "活动", labelEn: "Activity", labelJa: "アクティビティ", icon: <Activity size={16} />, action: () => router.push("/activity"), keywords: ["activity", "活动", "event"], group: "系统", groupEn: "System", groupJa: "システム" },
    { id: "nav-body-map", label: "身体地图", labelEn: "Body Map", labelJa: "ボディマップ", icon: <Bot size={16} />, action: () => router.push("/body-map"), keywords: ["body", "map", "身体", "器官"], group: "系统", groupEn: "System", groupJa: "システム" },

    // Quick actions
    { id: "new-chat", label: "新建对话", labelEn: "New Chat", labelJa: "新しいチャット", icon: <Plus size={16} />, action: () => router.push("/chat"), keywords: ["new", "新建", "chat"], group: "快捷操作", groupEn: "Quick Actions", groupJa: "クイックアクション" },
    { id: "new-knowledge", label: "新建知识", labelEn: "New Knowledge", labelJa: "新しいナレッジ", icon: <Plus size={16} />, action: () => router.push("/knowledge"), keywords: ["new", "新建", "knowledge"], group: "快捷操作", groupEn: "Quick Actions", groupJa: "クイックアクション" },
    { id: "new-learn", label: "新建课程", labelEn: "New Course", labelJa: "新しいコース", icon: <Plus size={16} />, action: () => router.push("/learn"), keywords: ["new", "新建", "course"], group: "快捷操作", groupEn: "Quick Actions", groupJa: "クイックアクション" },
    { id: "new-workflow", label: "新建工作流", labelEn: "New Workflow", labelJa: "新しいワークフロー", icon: <Plus size={16} />, action: () => router.push("/workflow"), keywords: ["new", "新建", "workflow"], group: "快捷操作", groupEn: "Quick Actions", groupJa: "クイックアクション" },

    // System actions
    { id: "refresh", label: "刷新页面", labelEn: "Refresh Page", labelJa: "ページ更新", icon: <RefreshCw size={16} />, action: () => window.location.reload(), keywords: ["refresh", "刷新", "reload"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "clear-cache", label: "清除缓存", labelEn: "Clear Cache", labelJa: "キャッシュクリア", icon: <Trash2 size={16} />, action: () => { localStorage.clear(); window.location.reload(); }, keywords: ["clear", "清除", "缓存", "cache"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-dark", label: "深色主题", labelEn: "Dark Theme", labelJa: "ダークテーマ", icon: <Moon size={16} />, action: () => { document.documentElement.classList.add("dark"); document.documentElement.classList.remove("light"); }, keywords: ["dark", "深色", "主题", "theme"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-light", label: "浅色主题", labelEn: "Light Theme", labelJa: "ライトテーマ", icon: <Sun size={16} />, action: () => { document.documentElement.classList.remove("dark"); document.documentElement.classList.add("light"); }, keywords: ["light", "浅色", "主题", "theme"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-purple", label: "紫色主题", labelEn: "Purple Theme", labelJa: "パープルテーマ", icon: <Palette size={16} />, action: () => { document.documentElement.classList.add("dark", "theme-purple"); }, keywords: ["purple", "紫色", "主题", "theme"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    // ── 7 套新主题切换命令 ──
    { id: "theme-deep-abyss", label: "深海静谧", labelEn: "Deep Abyss", labelJa: "深海静謐", icon: <Palette size={16} />, action: () => { document.documentElement.classList.remove("light", "dark", "theme-purple", "theme-deep-abyss", "theme-cream-mocha", "theme-nord-night", "theme-obsidian-cyan", "theme-twilight-violet", "theme-dune-sand", "theme-custom"); document.documentElement.classList.add("dark", "theme-deep-abyss"); localStorage.setItem("openmate-theme", "deep-abyss"); }, keywords: ["deep-abyss", "深海", "主题", "theme", "abyss"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-deep-abyss-light", label: "深海静谧（浅色）", labelEn: "Deep Abyss Light", labelJa: "深海静謐（ライト）", icon: <Palette size={16} />, action: () => { document.documentElement.classList.remove("light", "dark", "theme-purple", "theme-deep-abyss", "theme-deep-abyss-light", "theme-cream-mocha", "theme-nord-night", "theme-obsidian-cyan", "theme-twilight-violet", "theme-dune-sand", "theme-custom"); document.documentElement.classList.add("theme-deep-abyss-light"); localStorage.setItem("openmate-theme", "deep-abyss-light"); }, keywords: ["deep-abyss", "light", "深海", "浅色", "theme"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-cream-mocha", label: "奶油摩卡", labelEn: "Cream Mocha", labelJa: "クリームモカ", icon: <Palette size={16} />, action: () => { document.documentElement.classList.remove("light", "dark", "theme-purple", "theme-deep-abyss", "theme-cream-mocha", "theme-nord-night", "theme-obsidian-cyan", "theme-twilight-violet", "theme-dune-sand", "theme-custom"); document.documentElement.classList.add("dark", "theme-cream-mocha"); localStorage.setItem("openmate-theme", "cream-mocha"); }, keywords: ["cream-mocha", "奶油", "摩卡", "主题", "theme", "mocha"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-cream-mocha-light", label: "奶油摩卡（浅色）", labelEn: "Cream Mocha Light", labelJa: "クリームモカ（ライト）", icon: <Palette size={16} />, action: () => { document.documentElement.classList.remove("light", "dark", "theme-purple", "theme-deep-abyss", "theme-cream-mocha", "theme-cream-mocha-light", "theme-nord-night", "theme-obsidian-cyan", "theme-twilight-violet", "theme-dune-sand", "theme-custom"); document.documentElement.classList.add("theme-cream-mocha-light"); localStorage.setItem("openmate-theme", "cream-mocha-light"); }, keywords: ["cream-mocha", "light", "奶油", "浅色", "theme"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-nord-night", label: "北欧极夜", labelEn: "Nord Night", labelJa: "ノルダーク", icon: <Palette size={16} />, action: () => { document.documentElement.classList.remove("light", "dark", "theme-purple", "theme-deep-abyss", "theme-cream-mocha", "theme-nord-night", "theme-obsidian-cyan", "theme-twilight-violet", "theme-dune-sand", "theme-custom"); document.documentElement.classList.add("dark", "theme-nord-night"); localStorage.setItem("openmate-theme", "nord-night"); }, keywords: ["nord-night", "北欧", "极夜", "主题", "theme", "nord"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-nord-night-light", label: "北欧极夜（浅色）", labelEn: "Nord Night Light", labelJa: "ノルダーク（ライト）", icon: <Palette size={16} />, action: () => { document.documentElement.classList.remove("light", "dark", "theme-purple", "theme-deep-abyss", "theme-cream-mocha", "theme-nord-night", "theme-nord-night-light", "theme-obsidian-cyan", "theme-twilight-violet", "theme-dune-sand", "theme-custom"); document.documentElement.classList.add("theme-nord-night-light"); localStorage.setItem("openmate-theme", "nord-night-light"); }, keywords: ["nord-night", "light", "北欧", "浅色", "theme"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-obsidian-cyan", label: "青空黑曜石", labelEn: "Obsidian Cyan", labelJa: "黒曜石シアン", icon: <Palette size={16} />, action: () => { document.documentElement.classList.remove("light", "dark", "theme-purple", "theme-deep-abyss", "theme-cream-mocha", "theme-nord-night", "theme-obsidian-cyan", "theme-twilight-violet", "theme-dune-sand", "theme-custom"); document.documentElement.classList.add("dark", "theme-obsidian-cyan"); localStorage.setItem("openmate-theme", "obsidian-cyan"); }, keywords: ["obsidian-cyan", "青空", "黑曜石", "主题", "theme", "cyan"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-obsidian-cyan-light", label: "青空黑曜石（浅色）", labelEn: "Obsidian Cyan Light", labelJa: "黒曜石シアン（ライト）", icon: <Palette size={16} />, action: () => { document.documentElement.classList.remove("light", "dark", "theme-purple", "theme-deep-abyss", "theme-cream-mocha", "theme-nord-night", "theme-obsidian-cyan", "theme-obsidian-cyan-light", "theme-twilight-violet", "theme-dune-sand", "theme-custom"); document.documentElement.classList.add("theme-obsidian-cyan-light"); localStorage.setItem("openmate-theme", "obsidian-cyan-light"); }, keywords: ["obsidian-cyan", "light", "青空", "浅色", "theme"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-twilight-violet", label: "暮光紫罗兰", labelEn: "Twilight Violet", labelJa: "トワイライトバイオレット", icon: <Palette size={16} />, action: () => { document.documentElement.classList.remove("light", "dark", "theme-purple", "theme-deep-abyss", "theme-cream-mocha", "theme-nord-night", "theme-obsidian-cyan", "theme-twilight-violet", "theme-dune-sand", "theme-custom"); document.documentElement.classList.add("dark", "theme-twilight-violet"); localStorage.setItem("openmate-theme", "twilight-violet"); }, keywords: ["twilight-violet", "暮光", "紫罗兰", "主题", "theme", "violet"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-twilight-violet-light", label: "暮光紫罗兰（浅色）", labelEn: "Twilight Violet Light", labelJa: "トワイライトバイオレット（ライト）", icon: <Palette size={16} />, action: () => { document.documentElement.classList.remove("light", "dark", "theme-purple", "theme-deep-abyss", "theme-cream-mocha", "theme-nord-night", "theme-obsidian-cyan", "theme-twilight-violet", "theme-twilight-violet-light", "theme-dune-sand", "theme-custom"); document.documentElement.classList.add("theme-twilight-violet-light"); localStorage.setItem("openmate-theme", "twilight-violet-light"); }, keywords: ["twilight-violet", "light", "暮光", "浅色", "theme"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-dune-sand", label: "沙丘黄昏", labelEn: "Dune Sand", labelJa: "デューンサンド", icon: <Palette size={16} />, action: () => { document.documentElement.classList.remove("light", "dark", "theme-purple", "theme-deep-abyss", "theme-cream-mocha", "theme-nord-night", "theme-obsidian-cyan", "theme-twilight-violet", "theme-dune-sand", "theme-custom"); document.documentElement.classList.add("dark", "theme-dune-sand"); localStorage.setItem("openmate-theme", "dune-sand"); }, keywords: ["dune-sand", "沙丘", "黄昏", "主题", "theme", "sand"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-dune-sand-light", label: "沙丘黄昏（浅色）", labelEn: "Dune Sand Light", labelJa: "デューンサンド（ライト）", icon: <Palette size={16} />, action: () => { document.documentElement.classList.remove("light", "dark", "theme-purple", "theme-deep-abyss", "theme-cream-mocha", "theme-nord-night", "theme-obsidian-cyan", "theme-twilight-violet", "theme-dune-sand", "theme-dune-sand-light", "theme-custom"); document.documentElement.classList.add("theme-dune-sand-light"); localStorage.setItem("openmate-theme", "dune-sand-light"); }, keywords: ["dune-sand", "light", "沙丘", "浅色", "theme"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
    { id: "theme-custom", label: "自定义主题", labelEn: "Custom Theme", labelJa: "カスタムテーマ", icon: <Palette size={16} />, action: () => { document.documentElement.classList.remove("light", "dark", "theme-purple", "theme-deep-abyss", "theme-cream-mocha", "theme-nord-night", "theme-obsidian-cyan", "theme-twilight-violet", "theme-dune-sand", "theme-custom"); document.documentElement.classList.add("dark", "theme-custom"); localStorage.setItem("openmate-theme", "custom"); }, keywords: ["custom", "自定义", "主题", "theme"], group: "系统操作", groupEn: "System Actions", groupJa: "システムアクション" },
  ], [router]);

  // Get display label based on language
  const getLabel = useCallback((cmd: CommandItem) => {
    if (lang === "zh") return cmd.label;
    if (lang === "ja") return cmd.labelJa;
    return cmd.labelEn;
  }, [lang]);

  const getGroup = useCallback((cmd: CommandItem) => {
    if (lang === "zh") return cmd.group;
    if (lang === "ja") return cmd.groupJa;
    return cmd.groupEn;
  }, [lang]);

  // Filter commands
  const filtered = useMemo(() => {
    if (!query.trim()) return commands;
    const q = query.toLowerCase();
    return commands.filter(
      (cmd) =>
        cmd.label.toLowerCase().includes(q) ||
        cmd.labelEn.toLowerCase().includes(q) ||
        cmd.labelJa.toLowerCase().includes(q) ||
        cmd.keywords.some((k) => k.includes(q)),
    );
  }, [query, commands]);

  // Build display list: recent first (when no query), then filtered
  const displayList = useMemo(() => {
    if (query.trim()) return filtered;
    const recentIds = getRecentIds();
    const recentCmds = recentIds
      .map((id) => commands.find((c) => c.id === id))
      .filter(Boolean) as CommandItem[];
    const recentFiltered = recentCmds.filter((c) => filtered.includes(c));
    const rest = filtered.filter((c) => !recentFiltered.includes(c));
    return [...recentFiltered, ...rest];
  }, [query, filtered, commands]);

  // Group display list
  const grouped = useMemo(() => {
    if (!query.trim()) {
      // Show recent group first when no query
      const recentIds = getRecentIds();
      const recentCmds = recentIds
        .map((id) => commands.find((c) => c.id === id))
        .filter((c): c is CommandItem => !!c && displayList.includes(c));
      const rest = displayList.filter((c) => !recentCmds.includes(c));
      const groups: Record<string, CommandItem[]> = {};
      if (recentCmds.length > 0) {
        const recentLabel = t("command.recent", "Recent");
        groups[recentLabel] = recentCmds;
      }
      for (const cmd of rest) {
        const g = getGroup(cmd);
        (groups[g] ??= []).push(cmd);
      }
      return groups;
    }
    return displayList.reduce<Record<string, CommandItem[]>>((acc, cmd) => {
      const g = getGroup(cmd);
      (acc[g] ??= []).push(cmd);
      return acc;
    }, {});
  }, [displayList, query, commands, getGroup, lang]);

  // Keyboard shortcut
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Focus input when opened
  useEffect(() => {
    if (open) {
      setQuery("");
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  // Scroll selected item into view
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const item = list.querySelector(`[data-idx="${selectedIndex}"]`) as HTMLElement | undefined;
    item?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  const executeCommand = useCallback(
    (cmd: CommandItem) => {
      pushRecent(cmd.id);
      setOpen(false);
      cmd.action();
    },
    [],
  );

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((i) => (i + 1) % displayList.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((i) => (i - 1 + displayList.length) % displayList.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const cmd = displayList[selectedIndex];
      if (cmd) executeCommand(cmd);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  if (!open) return null;

  const noResultsText = t("command.noMatch", "No matching commands");
  const navText = t("command.navigate", "Navigate");
  const execText = t("command.execute", "Execute");
  const closeText = t("command.close", "Close");
  const cmdCountText = t("command.commandCount", "commands");
  const placeholderText = t("command.searchCommands", "Search pages, commands...");

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center pt-[15vh]">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm"
        onClick={() => setOpen(false)}
      />

      {/* Panel */}
      <div className="relative z-50 w-full max-w-xl overflow-hidden rounded-xl border border-border bg-card shadow-2xl">
        {/* Input */}
        <div className="flex items-center gap-3 border-b border-border px-4 py-3">
          <Command size={16} className="shrink-0 text-muted-foreground" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
            onKeyDown={handleKeyDown}
            placeholder={placeholderText}
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          <kbd className="shrink-0 select-none rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
            ESC
          </kbd>
        </div>

        {/* Command list */}
        <div ref={listRef} className="max-h-80 overflow-y-auto p-1.5">
          {displayList.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              {noResultsText}
            </div>
          ) : (
            Object.entries(grouped).map(([group, items]) => (
              <div key={group}>
                <div className="px-2 py-1.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  {group}
                </div>
                {items.map((cmd) => {
                  const idx = displayList.indexOf(cmd);
                  const isSelected = idx === selectedIndex;
                  return (
                    <button
                      key={cmd.id}
                      data-idx={idx}
                      onClick={() => executeCommand(cmd)}
                      onMouseEnter={() => setSelectedIndex(idx)}
                      className={cn(
                        "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                        isSelected
                          ? "bg-primary/12 text-primary"
                          : "text-muted-foreground hover:bg-accent/50",
                      )}
                    >
                      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-muted">
                        {cmd.icon}
                      </span>
                      <span className="flex-1 text-left">{getLabel(cmd)}</span>
                      {isSelected && (
                        <kbd className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                          Enter
                        </kbd>
                      )}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-border px-4 py-2">
          <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <kbd className="rounded border border-border px-1">↑</kbd>
              <kbd className="rounded border border-border px-1">↓</kbd>
              {navText}
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded border border-border px-1">↵</kbd>
              {execText}
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded border border-border px-1">Esc</kbd>
              {closeText}
            </span>
          </div>
          <span className="text-[10px] text-muted-foreground">
            {displayList.length} {cmdCountText}
          </span>
        </div>
      </div>
    </div>
  );
}
