"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import { useTranslation } from "react-i18next";
import {
  RefreshCw, Maximize2, Minimize2, Activity, RotateCcw,
  CheckCircle, XCircle, Loader2, Clock, PanelRightOpen, PanelRightClose,
} from "lucide-react";
import Link from "next/link";
import type { TFunction } from "i18next";
import { useIsMobile } from "@/hooks/use-mobile";
import { PageLayout } from '@/components/page-layout';

interface OrganStatus {
  key: string;
  label: string;
  emoji: string;
  category: string;
  status: "ok" | "error" | "loading";
  responseTime?: number;
}

// Organ layout: position each organ anatomically on the body
// Coordinates are percentages relative to the SVG viewBox
// healthKey: /api/health/all 器官键映射（soma 后端键是 soma-connector）
// self: 产品自述条目（Mate=OpenMate 进程本体，无器官功能页）
const ORGAN_LAYOUT: Array<{
  key: string;
  label: string;
  emoji: string;
  category: string;
  x: number;
  y: number;
  href: string;
  system: string;
  healthKey?: string;
  self?: boolean;
}> = [
  // Brain area (head)
  { key: "soul", label: "灵魂", emoji: "🧠", category: "core", x: 50, y: 6, href: "/dashboard", system: "brain" },
  { key: "cortex", label: "皮层", emoji: "🧩", category: "core", x: 38, y: 10, href: "/cortex", system: "brain" },
  { key: "hippo", label: "海马体", emoji: "🧠", category: "organ", x: 62, y: 10, href: "/hippo", system: "brain" },
  { key: "mind", label: "心智", emoji: "💭", category: "organ", x: 50, y: 13, href: "/mind", system: "brain" },
  { key: "reflex", label: "反射", emoji: "⚡", category: "organ", x: 30, y: 13, href: "/reflex", system: "brain" },

  // Eyes/Sense (face)
  { key: "sense", label: "感官", emoji: "👁", category: "service", x: 42, y: 17, href: "/sense", system: "sensory" },
  { key: "vision", label: "视觉", emoji: "🎨", category: "organ", x: 58, y: 17, href: "/vision", system: "sensory" },
  { key: "capture", label: "捕获", emoji: "📸", category: "service", x: 35, y: 19, href: "/capture", system: "sensory" },

  // Voice (throat)
  { key: "voice", label: "声带", emoji: "🎤", category: "organ", x: 50, y: 22, href: "/voice", system: "sensory" },
  { key: "echo", label: "回声", emoji: "🔊", category: "service", x: 40, y: 24, href: "/echo", system: "sensory" },

  // Chest area
  { key: "vital", label: "体征", emoji: "📊", category: "service", x: 45, y: 32, href: "/vital", system: "circulatory" },
  { key: "pulse", label: "脉搏", emoji: "💓", category: "organ", x: 55, y: 30, href: "/pulse", system: "circulatory" },
  { key: "immune", label: "免疫", emoji: "🛡", category: "service", x: 38, y: 36, href: "/immune", system: "immune" },

  // Arms (Nerve/Will)
  { key: "nerve", label: "神经", emoji: "⚡", category: "service", x: 20, y: 32, href: "/nerve", system: "nervous" },
  { key: "will", label: "意志", emoji: "✨", category: "core", x: 80, y: 32, href: "/will", system: "nervous" },

  // Core (torso center)
  { key: "gland", label: "腺体", emoji: "🧪", category: "service", x: 50, y: 40, href: "/gland", system: "endocrine" },
  { key: "heredity", label: "遗传链", emoji: "🔗", category: "organ", x: 58, y: 38, href: "/heredity", system: "endocrine" },

  // Vein (vascular system - spread across body)
  { key: "vein", label: "血管", emoji: "🩸", category: "core", x: 30, y: 40, href: "/vein", system: "circulatory" },

  // Soma/Mate（核心 8 补齐：躯体采集层 + 共生伙伴本体）
  { key: "soma", label: "躯体", emoji: "🤖", category: "core", x: 66, y: 43, href: "/soma", system: "motor", healthKey: "soma-connector" },
  { key: "mate", label: "伙伴", emoji: "🤝", category: "core", x: 65, y: 25, href: "", system: "brain", self: true },

  // Hands (Limb/Link)
  { key: "limb", label: "四肢", emoji: "💪", category: "organ", x: 15, y: 44, href: "/limb", system: "motor" },
  { key: "link", label: "突触", emoji: "🔗", category: "service", x: 85, y: 44, href: "/link", system: "motor" },

  // Abdomen
  { key: "marrow", label: "骨髓", emoji: "🦴", category: "service", x: 42, y: 48, href: "/marrow", system: "immune" },
  { key: "gene", label: "基因", emoji: "🧬", category: "service", x: 58, y: 48, href: "/gene", system: "endocrine" },

  // Lower torso
  { key: "nest", label: "巢穴", emoji: "🏠", category: "organ", x: 50, y: 55, href: "/nest", system: "isolation" },
  { key: "mirror", label: "镜像", emoji: "🪞", category: "service", x: 38, y: 58, href: "/mirror", system: "isolation" },

  // Legs (Foundation)
  { key: "trajectory", label: "轨迹", emoji: "📊", category: "system", x: 42, y: 68, href: "/trajectory", system: "foundation" },
  { key: "mcp", label: "MCP", emoji: "🔌", category: "system", x: 58, y: 68, href: "/mcp", system: "foundation" },

  // Feet (Infrastructure)
  { key: "learn", label: "Learn", emoji: "📚", category: "system", x: 45, y: 80, href: "/learn", system: "foundation" },
  { key: "diagnostics", label: "Diagnostics", emoji: "🩺", category: "system", x: 55, y: 80, href: "/monitoring", system: "foundation" },
];

const SYSTEM_COLORS: Record<string, { fill: string; stroke: string; glow: string }> = {
  brain: { fill: "#8b5cf6", stroke: "#7c3aed", glow: "rgba(139,92,246,0.4)" },
  sensory: { fill: "#f59e0b", stroke: "#d97706", glow: "rgba(245,158,11,0.4)" },
  nervous: { fill: "#3b82f6", stroke: "#2563eb", glow: "rgba(59,130,246,0.4)" },
  circulatory: { fill: "#ef4444", stroke: "#dc2626", glow: "rgba(239,68,68,0.4)" },
  immune: { fill: "#10b981", stroke: "#059669", glow: "rgba(16,185,129,0.4)" },
  endocrine: { fill: "#ec4899", stroke: "#db2777", glow: "rgba(236,72,153,0.4)" },
  motor: { fill: "#f97316", stroke: "#ea580c", glow: "rgba(249,115,22,0.4)" },
  isolation: { fill: "#6366f1", stroke: "#4f46e5", glow: "rgba(99,102,241,0.4)" },
  foundation: { fill: "#64748b", stroke: "#475569", glow: "rgba(100,116,139,0.4)" },
};

// SVG body outline paths
const BODY_OUTLINE = `
  M 50 2
  C 44 2 40 6 40 10
  C 40 16 44 18 50 18
  C 56 18 60 16 60 10
  C 60 6 56 2 50 2
  Z
`;

const TORSO_OUTLINE = `
  M 35 20
  C 30 22 25 28 22 35
  L 18 42
  L 15 50
  L 20 50
  L 25 45
  L 30 55
  L 35 70
  L 38 85
  L 42 95
  L 48 95
  L 48 85
  L 50 70
  L 52 85
  L 52 95
  L 58 95
  L 62 85
  L 65 70
  L 70 55
  L 75 45
  L 80 50
  L 85 50
  L 82 42
  L 78 35
  L 75 28
  L 70 22
  L 65 20
  Z
`;

// ── 器官关系（25 组件三层架构真实关系；出处：knowledge-brain/docs/open-soma-architecture.md
//    分层架构图 + 关键边界区分表 + 组件职能表，勿编造）────────────────────────
// dir: oneway = 上下游数据/控制流（箭头指向 to）；twoway = 层内协作/双向关系
const RELATIONS: Array<{ id: string; from: string; to: string; dir: "oneway" | "twoway" }> = [
  // 核心 8 主链（分层架构图：Mate ↓ Will ↓ 感知/采集层 ↓ 总线层 ↓ 大脑层）
  { id: "mate-will", from: "mate", to: "will", dir: "oneway" },
  { id: "mate-soul", from: "mate", to: "soul", dir: "twoway" },
  { id: "soma-sense", from: "soma", to: "sense", dir: "oneway" },
  { id: "sense-nerve", from: "sense", to: "nerve", dir: "oneway" },
  { id: "nerve-soma", from: "nerve", to: "soma", dir: "oneway" },
  { id: "nerve-soul", from: "nerve", to: "soul", dir: "oneway" },
  { id: "vein-soul", from: "vein", to: "soul", dir: "oneway" },
  { id: "cortex-soul", from: "cortex", to: "soul", dir: "twoway" },
  { id: "will-soma", from: "will", to: "soma", dir: "oneway" },
  { id: "link-limb", from: "link", to: "limb", dir: "oneway" },
  // 应激/时序协同（Pulse=时间驱动循环、Reflex=事件驱动快速应激 ↔ Will 业务流）
  { id: "pulse-will", from: "pulse", to: "will", dir: "twoway" },
  { id: "reflex-nerve", from: "reflex", to: "nerve", dir: "twoway" },
  { id: "reflex-will", from: "reflex", to: "will", dir: "twoway" },
  // 底座支撑（组件职能表）
  { id: "gene-will", from: "gene", to: "will", dir: "oneway" },
  { id: "gland-cortex", from: "gland", to: "cortex", dir: "oneway" },
  { id: "hippo-soul", from: "hippo", to: "soul", dir: "twoway" },
  { id: "mirror-will", from: "mirror", to: "will", dir: "oneway" },
];

// 拖动位置持久化（localStorage；重置按钮恢复解剖默认位）
const POSITIONS_KEY = "openmate-body-map-positions";
type PosMap = Record<string, { x: number; y: number }>;
const DEFAULT_POSITIONS: PosMap = Object.fromEntries(
  ORGAN_LAYOUT.map((o) => [o.key, { x: o.x, y: o.y }]),
);

function OrganNode({
  organ,
  status,
  pos,
  dim,
  active,
  onClick,
  onPointerDown,
  onHover,
}: {
  organ: (typeof ORGAN_LAYOUT)[0];
  status: OrganStatus;
  pos: { x: number; y: number };
  dim: boolean;
  active: boolean;
  onClick: () => void;
  onPointerDown: (e: React.PointerEvent) => void;
  onHover: (key: string | null) => void;
}) {
  const { t } = useTranslation();
  const colors = SYSTEM_COLORS[organ.system] || SYSTEM_COLORS.foundation;
  const isError = status.status === "error";
  const isLoading = status.status === "loading";
  const isOk = status.status === "ok";
  const label = t(`bodyMap.organs.${organ.key}`, organ.label);

  return (
    <g
      onClick={onClick}
      onPointerDown={onPointerDown}
      onMouseEnter={() => onHover(organ.key)}
      onMouseLeave={() => onHover(null)}
      className="cursor-pointer transition-all duration-300"
      style={{
        opacity: dim ? 0.22 : 1,
        filter: isOk ? `drop-shadow(0 0 6px ${colors.glow})` : isError ? "drop-shadow(0 0 6px rgba(239,68,68,0.6))" : undefined,
        touchAction: "none",
      }}
    >
      {/* Pulse ring for ok status */}
      {isOk && !active && (
        <circle
          cx={pos.x}
          cy={pos.y}
          r="4.5"
          fill="none"
          stroke={colors.fill}
          strokeWidth="0.3"
          opacity="0.4"
        >
          <animate attributeName="r" from="4.5" to="8" dur="2s" repeatCount="indefinite" />
          <animate attributeName="opacity" from="0.4" to="0" dur="2s" repeatCount="indefinite" />
        </circle>
      )}

      {/* 悬停/选中高亮环（关联邻居可视） */}
      {active && (
        <circle cx={pos.x} cy={pos.y} r="6.2" fill="none" stroke={colors.fill} strokeWidth="0.55" opacity={0.9} />
      )}

      {/* Main circle */}
      <circle
        cx={pos.x}
        cy={pos.y}
        r="4"
        fill={isError ? "#ef4444" : isLoading ? "#6b7280" : colors.fill}
        stroke={isError ? "#dc2626" : colors.stroke}
        strokeWidth="0.5"
        opacity={isLoading ? 0.5 : 1}
      />

      {/* Status indicator */}
      {isOk && (
        <circle cx={pos.x + 3} cy={pos.y - 3} r="1.2" fill="#22c55e" stroke="#16a34a" strokeWidth="0.3" />
      )}
      {isError && (
        <circle cx={pos.x + 3} cy={pos.y - 3} r="1.2" fill="#ef4444" stroke="#dc2626" strokeWidth="0.3" />
      )}

      {/* Emoji */}
      <text
        x={pos.x}
        y={pos.y + 1.2}
        textAnchor="middle"
        fontSize="4"
        className="select-none pointer-events-none"
      >
        {organ.emoji}
      </text>

      {/* Label */}
      <text
        x={pos.x}
        y={pos.y + 7}
        textAnchor="middle"
        fontSize="2.2"
        fill="currentColor"
        fontWeight="500"
        className="select-none pointer-events-none"
      >
        {label}
      </text>
    </g>
  );
}

export function BodyMapClient() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const [organStatuses, setOrganStatuses] = useState<Record<string, OrganStatus>>({});
  const [hoveredOrgan, setHoveredOrgan] = useState<string | null>(null);
  const [selectedOrgan, setSelectedOrgan] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const isMobile = useIsMobile();
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  // ── 拖动重排：位置状态 + localStorage 持久化 + 重置 ──
  const [positions, setPositions] = useState<PosMap>(DEFAULT_POSITIONS);
  const dragRef = useRef<{ key: string; moved: boolean } | null>(null);
  const justDraggedRef = useRef(false);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(POSITIONS_KEY);
      if (raw) {
        const saved = JSON.parse(raw) as PosMap;
        const merged: PosMap = { ...DEFAULT_POSITIONS };
        for (const [key, pos] of Object.entries(saved)) {
          if (key in DEFAULT_POSITIONS && pos && typeof pos.x === "number" && typeof pos.y === "number") {
            merged[key] = pos;
          }
        }
        setPositions(merged);
      }
    } catch { /* 缓存损坏时用默认布局 */ }
  }, []);

  const persistPositions = (pos: PosMap) => {
    try { localStorage.setItem(POSITIONS_KEY, JSON.stringify(pos)); } catch { /* 存储异常不阻断拖动 */ }
  };

  const resetPositions = () => {
    setPositions(DEFAULT_POSITIONS);
    try { localStorage.removeItem(POSITIONS_KEY); } catch { /* 忽略 */ }
  };

  const svgCoords = (clientX: number, clientY: number) => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return {
      x: Math.min(97, Math.max(3, ((clientX - rect.left) / rect.width) * 100)),
      y: Math.min(97, Math.max(3, ((clientY - rect.top) / rect.height) * 100)),
    };
  };

  const handleOrganPointerDown = (e: React.PointerEvent, key: string) => {
    e.preventDefault();
    dragRef.current = { key, moved: false };
    try {
      (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    } catch { /* 合成/异常指针无捕获对象：不影响拖动（move 事件照常冒泡到 svg） */ }
  };

  const handlePointerMove = (e: React.PointerEvent) => {
    if (!dragRef.current) return;
    const p = svgCoords(e.clientX, e.clientY);
    if (!p) return;
    dragRef.current.moved = true;
    justDraggedRef.current = true;
    const key = dragRef.current.key;
    setPositions((prev) => ({ ...prev, [key]: p }));
  };

  const handlePointerUp = () => {
    if (dragRef.current?.moved) {
      setPositions((prev) => { persistPositions(prev); return prev; });
    }
    dragRef.current = null;
    // 拖动结束短窗口屏蔽 click（防止松手误触详情）
    setTimeout(() => { justDraggedRef.current = false; }, 300);
  };

  const checkAllOrgans = useCallback(async () => {
    // Set all to loading
    const loading: Record<string, OrganStatus> = {};
    ORGAN_LAYOUT.forEach((o) => {
      loading[o.key] = { ...o, status: "loading" };
    });
    setOrganStatuses(loading);

    try {
      const res = await fetch(`${apiBase}/api/health/all`, { signal: AbortSignal.timeout(10000) });
      if (res.ok) {
        const data = await res.json();
        const organs = data.organs || {};
        const updated: Record<string, OrganStatus> = {};
        ORGAN_LAYOUT.forEach((o) => {
          const hk = o.healthKey ?? o.key;
          updated[o.key] = {
            ...o,
            status: o.self ? "ok" : organs[hk] === "ok" ? "ok" : "error",
          };
        });
        setOrganStatuses(updated);
      }
    } catch {
      const fallback: Record<string, OrganStatus> = {};
      ORGAN_LAYOUT.forEach((o) => {
        fallback[o.key] = { ...o, status: o.self ? "ok" : "error" };
      });
      setOrganStatuses(fallback);
    }
    setLastRefresh(new Date());
  }, [apiBase]);

  useEffect(() => {
    checkAllOrgans();
  }, [checkAllOrgans]);

  // Auto-refresh every 30s
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = setInterval(checkAllOrgans, 30000);
    return () => clearInterval(timer);
  }, [autoRefresh, checkAllOrgans]);

  const okCount = Object.values(organStatuses).filter((s) => s.status === "ok").length;
  const errorCount = Object.values(organStatuses).filter((s) => s.status === "error").length;
  const totalCount = ORGAN_LAYOUT.length;

  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen();
      setFullscreen(true);
    } else {
      document.exitFullscreen();
      setFullscreen(false);
    }
  };

  const selectedOrganData = selectedOrgan ? ORGAN_LAYOUT.find((o) => o.key === selectedOrgan) : null;
  const selectedStatus = selectedOrgan ? organStatuses[selectedOrgan] : null;

  // 悬停高亮：active 器官的关联邻居 + 关系连线（其余淡化）
  const activeKey = hoveredOrgan ?? selectedOrgan;
  const neighborKeys = new Set<string>();
  if (activeKey) {
    for (const rel of RELATIONS) {
      if (rel.from === activeKey) neighborKeys.add(rel.to);
      if (rel.to === activeKey) neighborKeys.add(rel.from);
    }
  }

  const organLabel = (key: string) => {
    const o = ORGAN_LAYOUT.find((x) => x.key === key);
    return o ? t(`bodyMap.organs.${o.key}`, o.label) : key;
  };

  const organRelations = (key: string) =>
    RELATIONS.filter((r) => r.from === key || r.to === key);

  return (
      <PageLayout title="身体地图">
        
    <div ref={containerRef} className="flex h-full flex-col overflow-hidden bg-background">
      {/* Header */}
      <div className="body-map-header flex items-center justify-between border-b border-border px-4 lg:px-6 py-3 lg:py-4">
        <div className="flex items-center gap-3">
          <Activity size={20} className="text-primary" />
          <h1 className="text-base lg:text-lg font-semibold">{t("bodyMap.title", "System Body Map")}</h1>
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
            {okCount}/{totalCount} {t("bodyMap.healthy", "Online")}
          </span>
          {errorCount > 0 && (
            <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-500">
              {errorCount} {t("bodyMap.errors", "Error")}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            <Clock size={12} className="inline mr-1" />
            {lastRefresh.toLocaleTimeString('zh-CN', { fractionalSecondDigits: 3 })}
          </span>
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={cn(
              "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors",
              autoRefresh
                ? "bg-green-500/10 text-green-500 border border-green-500/30"
                : "border border-border text-muted-foreground hover:bg-muted"
            )}
          >
            <div className={cn("w-1.5 h-1.5 rounded-full", autoRefresh ? "bg-green-500 animate-pulse" : "bg-muted-foreground")} />
            {t("bodyMap.auto", "Auto")}
          </button>
          <button
            onClick={checkAllOrgans}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted transition-colors"
          >
            <RefreshCw size={12} /> {t("bodyMap.refresh", "Refresh")}
          </button>
          <button
            onClick={resetPositions}
            title={t("bodyMap.resetLayout", "重置布局")}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted transition-colors"
          >
            <RotateCcw size={12} /> {t("bodyMap.resetLayout", "重置布局")}
          </button>
          <button
            onClick={toggleFullscreen}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted transition-colors"
          >
            {fullscreen ? <Minimize2 size={12} /> : <Maximize2 size={12} />}
          </button>
          <button
            onClick={() => setShowDetails(!showDetails)}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted transition-colors"
          >
            {showDetails ? <PanelRightClose size={12} /> : <PanelRightOpen size={12} />}
          </button>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden relative">
        {/* SVG Body Map */}
        <div className="body-map-content flex-1 flex items-center justify-center p-3 lg:p-6">
          <svg
            ref={svgRef}
            viewBox="0 0 100 100"
            className="w-full max-w-2xl h-auto"
            style={{ maxHeight: "100%", touchAction: "none" }}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerLeave={handlePointerUp}
          >
            {/* Background grid */}
            <defs>
              <pattern id="grid" width="5" height="5" patternUnits="userSpaceOnUse">
                <path d="M 5 0 L 0 0 0 5" fill="none" stroke="currentColor" strokeWidth="0.05" opacity="0.1" />
              </pattern>
              <radialGradient id="bodyGlow" cx="50%" cy="40%" r="50%">
                <stop offset="0%" stopColor="rgba(139,92,246,0.08)" />
                <stop offset="100%" stopColor="transparent" />
              </radialGradient>
              <marker id="organ-arrow" viewBox="0 0 6 6" markerWidth={4} markerHeight={4} refX={5.5} refY={3} orient="auto">
                <path d="M 0 0.5 L 6 3 L 0 5.5 z" fill="currentColor" opacity={0.55} />
              </marker>
              <marker id="organ-arrow-back" viewBox="0 0 6 6" markerWidth={4} markerHeight={4} refX={0.5} refY={3} orient="auto">
                <path d="M 6 0.5 L 0 3 L 6 5.5 z" fill="currentColor" opacity={0.55} />
              </marker>
            </defs>

            <rect width="100" height="100" fill="url(#grid)" />
            <rect width="100" height="100" fill="url(#bodyGlow)" />

            {/* Body outline (simplified) */}
            {/* Head */}
            <ellipse cx="50" cy="10" rx="10" ry="9" fill="none" stroke="currentColor" strokeWidth="0.2" opacity="0.15" />
            {/* Neck */}
            <rect x="47" y="18" width="6" height="4" fill="none" stroke="currentColor" strokeWidth="0.2" opacity="0.1" rx="1" />
            {/* Torso */}
            <path
              d="M 33 22 C 28 26 25 32 24 40 L 26 55 L 30 65 L 35 70 L 40 72 L 47 72 L 47 60 L 50 72 L 53 60 L 53 72 L 60 72 L 65 70 L 70 65 L 74 55 L 76 40 C 75 32 72 26 67 22 Z"
              fill="none"
              stroke="currentColor"
              strokeWidth="0.2"
              opacity="0.12"
            />
            {/* Left arm */}
            <path d="M 24 35 L 18 42 L 14 50 L 12 55" fill="none" stroke="currentColor" strokeWidth="0.2" opacity="0.1" />
            {/* Right arm */}
            <path d="M 76 35 L 82 42 L 86 50 L 88 55" fill="none" stroke="currentColor" strokeWidth="0.2" opacity="0.1" />
            {/* Left leg */}
            <path d="M 40 72 L 38 82 L 37 92 L 36 97" fill="none" stroke="currentColor" strokeWidth="0.2" opacity="0.1" />
            {/* Right leg */}
            <path d="M 60 72 L 62 82 L 63 92 L 64 97" fill="none" stroke="currentColor" strokeWidth="0.2" opacity="0.1" />

            {/* 关系连线（组件真实关系；拖动跟随；悬停高亮关联、其余淡化） */}
            {RELATIONS.map((rel) => {
              const a = positions[rel.from];
              const b = positions[rel.to];
              if (!a || !b) return null;
              const touched = activeKey === rel.from || activeKey === rel.to;
              const dimmed = !!activeKey && !touched;
              return (
                <line
                  key={rel.id}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke="currentColor"
                  strokeWidth={touched ? 0.45 : 0.15}
                  opacity={touched ? 0.9 : dimmed ? 0.05 : 0.18}
                  strokeDasharray={rel.dir === "twoway" ? "2 1" : undefined}
                  markerEnd="url(#organ-arrow)"
                  markerStart={rel.dir === "twoway" ? "url(#organ-arrow-back)" : undefined}
                />
              );
            })}

            {/* Organ nodes */}
            {ORGAN_LAYOUT.map((organ) => (
              <OrganNode
                key={organ.key}
                organ={organ}
                status={organStatuses[organ.key] || { ...organ, status: "loading" }}
                pos={positions[organ.key] || { x: organ.x, y: organ.y }}
                dim={!!activeKey && activeKey !== organ.key && !neighborKeys.has(organ.key)}
                active={activeKey === organ.key}
                onClick={() => {
                  if (justDraggedRef.current) return;
                  setSelectedOrgan(organ.key === selectedOrgan ? null : organ.key);
                }}
                onPointerDown={(e) => handleOrganPointerDown(e, organ.key)}
                onHover={setHoveredOrgan}
              />
            ))}
          </svg>
        </div>

        {/* 拖动提示 */}
        <div className="pointer-events-none absolute bottom-2 left-1/2 -translate-x-1/2 text-[10px] text-muted-foreground/70 bg-background/60 rounded-full px-3 py-1">
          {t("bodyMap.dragHint", "拖动器官可重排布局（自动记住）；悬停看关联邻居，点击看关系说明")}
        </div>

        {/* Side panel: sidebar-style sliding on mobile, inline on desktop */}
        {isMobile && showDetails && (
          <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setShowDetails(false)} aria-hidden="true" />
        )}
        {isMobile ? (
          showDetails && (
            <div
              className="absolute inset-y-0 right-0 z-10 h-full w-72 min-w-0 border-l border-border transition-[right] duration-200 ease-linear flex flex-col overflow-y-auto bg-card"
              style={{ right: 0 }}
            >
              <div className="p-4 space-y-4">
                <OrganDetailsBlock
                  selectedOrganData={selectedOrganData}
                  selectedStatus={selectedStatus}
                  organLabel={organLabel}
                  organRelations={organRelations}
                  t={t}
                />
                <OrganListBlock
                  statuses={organStatuses}
                  selected={selectedOrgan}
                  onSelect={setSelectedOrgan}
                  onHover={setHoveredOrgan}
                  organLabel={organLabel}
                  okCount={okCount}
                  totalCount={totalCount}
                  t={t}
                />
              </div>
            </div>
          )
        ) : (
          showDetails && (
            <div className="w-72 shrink-0 border-l border-border overflow-y-auto p-3 lg:p-4 space-y-4">
              <OrganDetailsBlock
                selectedOrganData={selectedOrganData}
                selectedStatus={selectedStatus}
                organLabel={organLabel}
                organRelations={organRelations}
                t={t}
              />
              <OrganListBlock
                statuses={organStatuses}
                selected={selectedOrgan}
                onSelect={setSelectedOrgan}
                onHover={setHoveredOrgan}
                organLabel={organLabel}
                okCount={okCount}
                totalCount={totalCount}
                t={t}
              />
            </div>
          )
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}

// ── 详情块：器官状态 + 关系说明（点击器官查看）+ Mate 产品自述 ──
function OrganDetailsBlock({
  selectedOrganData,
  selectedStatus,
  organLabel,
  organRelations,
  t,
}: {
  selectedOrganData: (typeof ORGAN_LAYOUT)[0] | null | undefined;
  selectedStatus: OrganStatus | null;
  organLabel: (key: string) => string;
  organRelations: (key: string) => typeof RELATIONS;
  t: TFunction;
}) {
  return (
    <>
      {/* System legend */}
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">{t("bodyMap.systemsTitle", "Systems")}</h3>
        <div className="space-y-1.5">
          {Object.entries(SYSTEM_COLORS).map(([system, colors]) => (
            <div key={system} className="flex items-center gap-2">
              <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: colors.fill }} />
              <span className="text-xs capitalize">{system}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Selected organ detail */}
      {selectedOrganData && selectedStatus && (
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-2xl">{selectedOrganData.emoji}</span>
            <div>
              <h3 className="font-semibold text-sm">{organLabel(selectedOrganData.key)}</h3>
              <p className="text-xs text-muted-foreground capitalize">{selectedOrganData.system} system</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {selectedStatus.status === "ok" ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-500">
                <CheckCircle size={10} /> {t("bodyMap.healthy", "Healthy")}
              </span>
            ) : selectedStatus.status === "error" ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-500">
                <XCircle size={10} /> {t("bodyMap.errors", "Error")}
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                <Loader2 size={10} className="animate-spin" /> {t("bodyMap.loading", "Checking...")}
              </span>
            )}
          </div>

          {/* 关系说明（点击器官查看：上下游数据流/层内协作） */}
          <div>
            <h4 className="text-xs font-semibold text-muted-foreground mb-1.5">{t("bodyMap.relations", "关联")}</h4>
            {organRelations(selectedOrganData.key).length === 0 ? (
              <p className="text-[11px] text-muted-foreground">{t("bodyMap.noRelations", "无既定关联记载")}</p>
            ) : (
              <div className="space-y-1.5">
                {organRelations(selectedOrganData.key).map((rel) => {
                  const other = rel.from === selectedOrganData.key ? rel.to : rel.from;
                  const outgoing = rel.from === selectedOrganData.key;
                  return (
                    <div key={rel.id} className="rounded-lg bg-muted/50 px-2.5 py-1.5 text-[11px] leading-relaxed">
                      <span className="font-medium">
                        {rel.dir === "twoway" ? "↔" : outgoing ? "→" : "←"} {organLabel(other)}
                      </span>
                      <p className="text-muted-foreground">{t(`bodyMap.rel.${rel.id}.note`, rel.id)}</p>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Mate=产品自述（无器官功能页）；其余器官 → 打开对应页 */}
          {selectedOrganData.self ? (
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              {t("bodyMap.mateAbout", "Mate = OpenMate 本体——三进程之一（Soul/Soma/Mate）的客户端进程，即你正在使用的这个统一客户端/共生伙伴入口。不设独立器官功能页（产品自述）。")}
            </p>
          ) : (
            <Link
              href={selectedOrganData.href}
              className="block w-full rounded-lg bg-primary px-3 py-2 text-center text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              {t("bodyMap.open", "Open")} {organLabel(selectedOrganData.key)} →
            </Link>
          )}
        </div>
      )}
    </>
  );
}

// ── 器官清单（点击选中看关系，hover 高亮图上邻居）──
function OrganListBlock({
  statuses,
  selected,
  onSelect,
  onHover,
  organLabel,
  okCount,
  totalCount,
  t,
}: {
  statuses: Record<string, OrganStatus>;
  selected: string | null;
  onSelect: (key: string | null) => void;
  onHover: (key: string | null) => void;
  organLabel: (key: string) => string;
  okCount: number;
  totalCount: number;
  t: TFunction;
}) {
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
        {t("bodyMap.allOrgans", "All Organs")} ({okCount}/{totalCount})
      </h3>
      <div className="space-y-1">
        {ORGAN_LAYOUT.map((organ) => {
          const status = statuses[organ.key];
          const row = (
            <div
              className={cn(
                "flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs transition-colors hover:bg-muted cursor-pointer",
                selected === organ.key && "bg-[rgba(124,58,237,0.12)] text-[#7c3aed]"
              )}
              onMouseEnter={() => onHover(organ.key)}
              onMouseLeave={() => onHover(null)}
            >
              <span>{organ.emoji}</span>
              <span className="flex-1 truncate">{organLabel(organ.key)}</span>
              {status?.status === "ok" && <CheckCircle size={12} className="text-emerald-500" />}
              {status?.status === "error" && <XCircle size={12} className="text-red-500" />}
              {status?.status === "loading" && <Loader2 size={12} className="animate-spin text-muted-foreground" />}
            </div>
          );
          return organ.href ? (
            <Link key={organ.key} href={organ.href} onClick={() => onSelect(organ.key)}>{row}</Link>
          ) : (
            <div key={organ.key} onClick={() => onSelect(selected === organ.key ? null : organ.key)}>{row}</div>
          );
        })}
      </div>
    </div>
  );
}
