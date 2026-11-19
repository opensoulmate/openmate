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
// Coordinates are viewBox units on a 176x100 canvas（宽幅 fit-to-screen 布局；30 器官 7 行防碰撞网格，中英日三语标签零叠字）
// healthKey: /api/health/all 器官键映射（soma 后端键是 soma-connector）
// self: 产品自述条目（Mate=OpenMate 进程本体，无器官功能页）
const ORGAN_LAYOUT: Array<{
  key: string;
  label: string;
  tag: string; // 4 字功能说明（名字下方弱化小字；文案定稿，禁改写）
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
  { key: "soul", label: "灵魂", tag: "记忆中枢", emoji: "🧠", category: "core", x: 88, y: 5, href: "/dashboard", system: "brain" },
  { key: "cortex", label: "皮层", tag: "认知规划", emoji: "🧩", category: "core", x: 49, y: 5, href: "/cortex", system: "brain" },
  { key: "hippo", label: "海马体", tag: "记忆管理", emoji: "🧠", category: "organ", x: 127, y: 5, href: "/hippo", system: "brain" },
  { key: "mind", label: "心智", tag: "人格调节", emoji: "💭", category: "organ", x: 88, y: 19, href: "/mind", system: "brain" },
  { key: "reflex", label: "反射", tag: "高速应答", emoji: "⚡", category: "organ", x: 12, y: 5, href: "/reflex", system: "brain" },

  // Eyes/Sense (face)
  { key: "sense", label: "感官", tag: "感知解析", emoji: "👁", category: "service", x: 12, y: 19, href: "/sense", system: "sensory" },
  { key: "vision", label: "视觉", tag: "图表绘制", emoji: "🎨", category: "organ", x: 49, y: 19, href: "/vision", system: "sensory" },
  { key: "capture", label: "捕获", tag: "采集入库", emoji: "📸", category: "service", x: 127, y: 19, href: "/capture", system: "sensory" },

  // Voice (throat)
  { key: "voice", label: "声带", tag: "语音合成", emoji: "🎤", category: "organ", x: 67, y: 33, href: "/voice", system: "sensory" },
  { key: "echo", label: "回声", tag: "消息推送", emoji: "🔊", category: "service", x: 164, y: 19, href: "/echo", system: "sensory" },

  // Chest area
  { key: "vital", label: "体征", tag: "系统监控", emoji: "📊", category: "service", x: 49, y: 47, href: "/vital", system: "circulatory" },
  { key: "pulse", label: "脉搏", tag: "时序节拍", emoji: "💓", category: "organ", x: 109, y: 33, href: "/pulse", system: "circulatory" },
  { key: "immune", label: "免疫", tag: "安全防护", emoji: "🛡", category: "service", x: 12, y: 47, href: "/immune", system: "immune" },

  // Arms (Nerve/Will)
  { key: "nerve", label: "神经", tag: "事件总线", emoji: "⚡", category: "service", x: 21, y: 33, href: "/nerve", system: "nervous" },
  { key: "will", label: "意志", tag: "流程编排", emoji: "✨", category: "core", x: 155, y: 33, href: "/will", system: "nervous" },

  // Core (torso center)
  { key: "gland", label: "腺体", tag: "模型网关", emoji: "🧪", category: "service", x: 88, y: 47, href: "/gland", system: "endocrine" },
  { key: "heredity", label: "遗传链", tag: "版本演化", emoji: "🔗", category: "organ", x: 127, y: 47, href: "/heredity", system: "endocrine" },

  // Vein (vascular system - spread across body)
  { key: "vein", label: "血管", tag: "文件管理", emoji: "🩸", category: "core", x: 164, y: 47, href: "/vein", system: "circulatory" },

  // Soma/Mate（核心 8 补齐：躯体采集层 + 共生伙伴本体）
  { key: "soma", label: "躯体", tag: "外部连接", emoji: "🤖", category: "core", x: 67, y: 61, href: "/soma", system: "motor", healthKey: "soma-connector" },
  { key: "mate", label: "伙伴", tag: "客户端本体", emoji: "🤝", category: "core", x: 164, y: 5, href: "", system: "brain", self: true },

  // Hands (Limb/Link)
  { key: "limb", label: "四肢", tag: "RPA执行", emoji: "💪", category: "organ", x: 21, y: 61, href: "/limb", system: "motor" },
  { key: "link", label: "突触", tag: "双向集成", emoji: "🔗", category: "service", x: 155, y: 61, href: "/link", system: "motor" },

  // Abdomen
  { key: "marrow", label: "骨髓", tag: "备份恢复", emoji: "🦴", category: "service", x: 109, y: 61, href: "/marrow", system: "immune" },
  { key: "gene", label: "基因", tag: "预制模板", emoji: "🧬", category: "service", x: 109, y: 75, href: "/gene", system: "endocrine" },

  // Lower torso
  { key: "nest", label: "巢穴", tag: "租户隔离", emoji: "🏠", category: "organ", x: 67, y: 75, href: "/nest", system: "isolation" },
  { key: "mirror", label: "镜像", tag: "沙箱测试", emoji: "🪞", category: "service", x: 21, y: 75, href: "/mirror", system: "isolation" },

  // Legs (Foundation)
  { key: "trajectory", label: "轨迹", tag: "轨迹追踪", emoji: "📊", category: "system", x: 88, y: 89, href: "/trajectory", system: "foundation" },
  { key: "mcp", label: "MCP", tag: "工具接入", emoji: "🔌", category: "system", x: 155, y: 75, href: "/mcp", system: "foundation" },

  // Feet (Infrastructure)
  { key: "learn", label: "Learn", tag: "课程学习", emoji: "📚", category: "system", x: 44, y: 89, href: "/learn", system: "foundation" },
  { key: "diagnostics", label: "Diagnostics", tag: "系统诊断", emoji: "🩺", category: "system", x: 132, y: 89, href: "/monitoring", system: "foundation" },
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

// ── 器官关系 = 系统真实代码接线（每条边均有 file:line 调用证据，证据来源：
//    ① 前端→后端真实 HTTP 调用 ② opensoul/acp-proxy 模块间真实 import/调用
//    ③ 数据真实落盘流向 ④ 服务进程间真实链路；设计文档仅定义器官身份，无证据不画）──
// dir: oneway = 调用方向（箭头指向被调方）；twoway = 双向真实接线
const RELATIONS: Array<{ id: string; from: string; to: string; dir: "oneway" | "twoway"; note: string; evidence: string }> = [
  { id: "mate-soul", from: "mate", to: "soul", dir: "oneway", note: "Mate 客户端拉 Soul 健康总览/知识库接口", evidence: "openmate/src/app/(app)/soul/soul-client.tsx:66 fetch /api/health/all" },
  { id: "mate-cortex", from: "mate", to: "cortex", dir: "oneway", note: "Mate 调 Cortex 课程/推理规划接口", evidence: "openmate/src/app/(app)/cortex/cortex-client.tsx:180 fetch /api/cortex/plan" },
  { id: "mate-hippo", from: "mate", to: "hippo", dir: "oneway", note: "Mate 拉 Hippo 记忆健康接口", evidence: "openmate/src/app/(app)/hippo/hippo-client.tsx:120 fetch /api/hippo/health" },
  { id: "mate-mind", from: "mate", to: "mind", dir: "oneway", note: "Mate 拉 Mind 人格/情感接口", evidence: "openmate/src/app/(app)/mind/mind-client.tsx:71 fetch /api/mind/personalities" },
  { id: "mate-reflex", from: "mate", to: "reflex", dir: "oneway", note: "Mate 观察面板读 Reflex 事件/统计（acp-proxy :8092）", evidence: "openmate/src/app/(app)/reflex/reflex-client.tsx:94 fetch /api/reflex/stats" },
  { id: "mate-sense", from: "mate", to: "sense", dir: "oneway", note: "Mate 拉 Sense 感知健康接口", evidence: "openmate/src/app/(app)/sense/sense-client.tsx:91 fetch /api/sense/health" },
  { id: "mate-vision", from: "mate", to: "vision", dir: "oneway", note: "Mate 拉 Vision 图像生成记录", evidence: "openmate/src/app/(app)/vision/vision-workspace.tsx:55 fetch /api/vision/generations" },
  { id: "mate-capture", from: "mate", to: "capture", dir: "oneway", note: "Mate 拉 Capture 捕获列表", evidence: "openmate/src/app/(app)/capture/capture-client.tsx:83 fetch /api/capture/list" },
  { id: "mate-voice", from: "mate", to: "voice", dir: "oneway", note: "Mate 拉 Voice 合成统计", evidence: "openmate/src/app/(app)/voice/voice-client.tsx:105 fetch /api/voice/stats" },
  { id: "mate-echo", from: "mate", to: "echo", dir: "oneway", note: "Mate 拉 Echo 广播健康接口", evidence: "openmate/src/app/(app)/echo/echo-client.tsx:63 fetch /api/echo/health" },
  { id: "mate-vital", from: "mate", to: "vital", dir: "oneway", note: "Mate 拉 Vital 体征健康接口", evidence: "openmate/src/app/(app)/vital/vital-client.tsx:147 fetch /api/vital/health" },
  { id: "mate-pulse", from: "mate", to: "pulse", dir: "oneway", note: "Mate 观察面板读 Pulse 心跳状态（acp-proxy :8092）", evidence: "openmate/src/app/(app)/pulse/pulse-client.tsx:105 fetch /api/pulse/status" },
  { id: "mate-immune", from: "mate", to: "immune", dir: "oneway", note: "Mate 拉 Immune 防护健康接口", evidence: "openmate/src/app/(app)/immune/immune-client.tsx:89 fetch /api/immune/health" },
  { id: "mate-nerve", from: "mate", to: "nerve", dir: "oneway", note: "Mate 拉 Nerve 健康/事件/订阅接口", evidence: "openmate/src/app/(app)/nerve/nerve-client.tsx:90 fetch /api/nerve/health" },
  { id: "mate-will", from: "mate", to: "will", dir: "oneway", note: "Mate 直连 Will 工作流引擎（acp-proxy :8092 /flows）", evidence: "openmate/src/app/(app)/will/will-client.tsx:134 fetch ${acpBase}/flows" },
  { id: "mate-gland", from: "mate", to: "gland", dir: "oneway", note: "Mate 拉 Gland 模型调度健康接口", evidence: "openmate/src/app/(app)/gland/gland-client.tsx:83 fetch /api/gland/health" },
  { id: "mate-heredity", from: "mate", to: "heredity", dir: "oneway", note: "Mate 拉 Heredity 平台兼容接口", evidence: "openmate/src/app/(app)/heredity/heredity-client.tsx:103 fetch /api/heredity/platform" },
  { id: "mate-vein", from: "mate", to: "vein", dir: "oneway", note: "Mate 拉 Vein 文件版本/存储接口", evidence: "openmate/src/app/(app)/vein/vein-client.tsx:185 fetch /api/vein/files/…/versions" },
  { id: "mate-soma", from: "mate", to: "soma", dir: "oneway", note: "Mate 拉 Soma 连接器健康接口", evidence: "openmate/src/app/(app)/soma/soma-connector-client.tsx:120 fetch /api/soma/health" },
  { id: "mate-limb", from: "mate", to: "limb", dir: "oneway", note: "Mate 拉 Limb 任务/历史接口", evidence: "openmate/src/app/(app)/limb/limb-client.tsx:100 fetch /api/limb/health" },
  { id: "mate-link", from: "mate", to: "link", dir: "oneway", note: "Mate 拉 Link 连接器健康接口", evidence: "openmate/src/app/(app)/link/link-client.tsx:56 fetch /api/link/health" },
  { id: "mate-marrow", from: "mate", to: "marrow", dir: "oneway", note: "Mate 拉 Marrow 备份健康接口", evidence: "openmate/src/app/(app)/marrow/marrow-client.tsx:89 fetch /api/marrow/health" },
  { id: "mate-gene", from: "mate", to: "gene", dir: "oneway", note: "Mate 拉 Gene 模板健康接口", evidence: "openmate/src/app/(app)/gene/gene-client.tsx:145 fetch /api/gene/health" },
  { id: "mate-nest", from: "mate", to: "nest", dir: "oneway", note: "Mate 拉 Nest 租户/策略健康接口", evidence: "openmate/src/app/(app)/nest/nest-client.tsx:104 fetch /api/nest/health" },
  { id: "mate-mirror", from: "mate", to: "mirror", dir: "oneway", note: "Mate 拉 Mirror 沙箱健康接口", evidence: "openmate/src/app/(app)/mirror/mirror-client.tsx:59 fetch /api/mirror/health" },
  { id: "mate-trajectory", from: "mate", to: "trajectory", dir: "oneway", note: "Mate 拉 Trajectory 轨迹统计/会话", evidence: "openmate/src/app/(app)/trajectory/trajectory-client.tsx:85 fetch /api/trajectory/stats" },
  { id: "mate-mcp", from: "mate", to: "mcp", dir: "oneway", note: "Mate 拉 MCP 服务器/工具接口", evidence: "openmate/src/app/(app)/mcp/mcp-client.tsx:96 fetch /api/mcp/servers" },
  { id: "mate-learn", from: "mate", to: "learn", dir: "oneway", note: "Mate 调 Learn 课程生成接口", evidence: "openmate/src/app/(app)/learn/create/create-course-client.tsx:125 fetch /api/learn/courses/generate" },
  { id: "mate-diagnostics", from: "mate", to: "diagnostics", dir: "oneway", note: "Mate 触发 Diagnostics 全器官健康巡检", evidence: "openmate/src/app/(app)/monitoring/monitoring-client.tsx:779 fetch /api/diagnostics/check-all" },
  { id: "soul-nerve", from: "soul", to: "nerve", dir: "oneway", note: "Soul 大脑经 Nerve 事件桥发指令/事件", evidence: "opensoul/src/api/brain.py:401 from src.nerve.event_bridge import emit" },
  { id: "soma-nerve", from: "soma", to: "nerve", dir: "twoway", note: "Soma 经 Nerve 事件桥推事件；Soma 侧事件总线接线", evidence: "opensoul/src/api/soma_connector.py:20 push_event / openmate/acp-proxy/agent/soulmate_agent.py:97 event_bus" },
  { id: "echo-nerve", from: "echo", to: "nerve", dir: "oneway", note: "Echo 广播经 Nerve 事件桥分发", evidence: "opensoul/src/api/echo.py:9 from src.nerve.event_bridge import push_event" },
  { id: "immune-nerve", from: "immune", to: "nerve", dir: "oneway", note: "Immune 防护事件经 Nerve 事件桥上报", evidence: "opensoul/src/api/immune.py:18 from src.nerve.event_bridge import push_event" },
  { id: "link-nerve", from: "link", to: "nerve", dir: "oneway", note: "Link 集成事件经 Nerve 事件桥分发", evidence: "opensoul/src/api/link.py:7 from src.nerve.event_bridge import push_event" },
  { id: "marrow-nerve", from: "marrow", to: "nerve", dir: "oneway", note: "Marrow 备份/迁移事件经 Nerve 事件桥上报", evidence: "opensoul/src/api/marrow.py:11 from src.nerve.event_bridge import push_event" },
  { id: "vein-nerve", from: "vein", to: "nerve", dir: "oneway", note: "Vein 文件事件经 Nerve 事件桥分发", evidence: "opensoul/src/api/vein.py:9 from src.nerve.event_bridge import push_event" },
  { id: "mirror-nerve", from: "mirror", to: "nerve", dir: "oneway", note: "Mirror 沙箱事件经 Nerve 事件桥上报", evidence: "opensoul/src/api/mirror.py:11 from src.nerve.event_bridge import push_event" },
  { id: "vein-soul", from: "vein", to: "soul", dir: "oneway", note: "Vein 文件/资源写 Soul 存储", evidence: "opensoul/src/api/vein.py:10 from src.database.postgres import db_pool（文件库 data/vein/files，vein/file_store.py:56）" },
  { id: "soul-cortex", from: "soul", to: "cortex", dir: "twoway", note: "Soul 调 Cortex 多智能体协调；Cortex GraphRAG 读 Soul 知识库", evidence: "opensoul/src/api/brain.py:1006 MultiAgentCoordinator / opensoul/src/cortex/graphrag.py:14" },
  { id: "soul-hippo", from: "soul", to: "hippo", dir: "twoway", note: "Soul 记忆内核走 Hippo 存储管线；Hippo 回写 Soul 三库记忆", evidence: "opensoul/src/api/brain.py:111 memory_persist / opensoul/src/api/hippo.py:1266 services.memory_kb" },
  { id: "soul-mind", from: "soul", to: "mind", dir: "twoway", note: "Soul 情感决策读 Mind 情感分析；Mind 落 Soul 库", evidence: "opensoul/src/api/brain.py:191 EmotionDrivenDecision / opensoul/src/api/mind.py:10" },
  { id: "soul-learn", from: "soul", to: "learn", dir: "twoway", note: "Soul 思考读 Learn 经验记忆；Learn 落 Soul 库", evidence: "opensoul/src/api/brain.py:189 ExperienceMemory / opensoul/src/learn/experience.py:392" },
  { id: "soul-mirror", from: "soul", to: "mirror", dir: "twoway", note: "Soul 反思调 Mirror 元认知；Mirror 落 Soul 库", evidence: "opensoul/src/api/brain.py:297 metacognition / opensoul/src/mirror/metacognition.py:460" },
  { id: "soul-heredity", from: "soul", to: "heredity", dir: "twoway", note: "Soul 主控记录自进化；Heredity 落 Soul 库", evidence: "opensoul/src/api/brain.py:889 SelfEvolution / opensoul/src/heredity/self_evolution.py:478" },
  { id: "soul-trajectory", from: "soul", to: "trajectory", dir: "twoway", note: "Soul 检索命中记 Trajectory 轨迹；Trajectory 事件落 Soul 库", evidence: "opensoul/src/api/search.py:336 trajectory_store / opensoul/src/trajectory/store.py:11 db_pool" },
  { id: "soul-gene", from: "soul", to: "gene", dir: "twoway", note: "Soul 启动引导加载 Gene 模板引擎；Gene 模板库读 Soul 存储", evidence: "opensoul/src/system/bootstrap.py:245 TemplateEngine / opensoul/src/api/gene.py:17" },
  { id: "soul-vital", from: "soul", to: "vital", dir: "twoway", note: "Soul 服务启动挂 Vital 告警管理；Vital 采集器写 Soul 库", evidence: "opensoul/src/main.py:108 AlertManager / opensoul/src/vital/collector.py:198" },
  { id: "soul-nest", from: "soul", to: "nest", dir: "oneway", note: "Soul 租户绑定经 Nest 租户管理", evidence: "opensoul/src/services/tenant_binding.py:242 from src.nest.tenant import TenantManager" },
  { id: "mcp-soul", from: "mcp", to: "soul", dir: "oneway", note: "MCP Server 读 Soul 存储（Meilisearch/Postgres）", evidence: "opensoul/src/mcp/server.py:21 meili_client / db_pool" },
  { id: "hippo-mcp", from: "hippo", to: "mcp", dir: "oneway", note: "Hippo 会话授权经 MCP 会话门", evidence: "opensoul/src/api/hippo.py:15 from src.mcp.session_grants import default_gate" },
  { id: "hippo-will", from: "hippo", to: "will", dir: "oneway", note: "Hippo 记忆衰减后台任务进 Will 任务队列", evidence: "opensoul/src/api/hippo.py:850 from src.will.job_handlers / src.will.job_queue" },
  { id: "hippo-gland", from: "hippo", to: "gland", dir: "oneway", note: "Hippo 记忆蒸馏调 Gland 模型路由", evidence: "opensoul/src/hippo/dream_distiller.py:24 from src.gland.router import extract_chat_text" },
  { id: "hippo-immune", from: "hippo", to: "immune", dir: "oneway", note: "Hippo 记忆入库前经 Immune 内容审核脱敏", evidence: "opensoul/src/hippo/memory_redact.py:52 from src.immune.moderator import ContentModerator" },
  { id: "gland-cortex", from: "gland", to: "cortex", dir: "oneway", note: "Gland 模型路由复用 Cortex 重试策略", evidence: "opensoul/src/gland/router.py:224 from src.cortex.llm_retry import is_retryable_status" },
  { id: "gland-hippo", from: "gland", to: "hippo", dir: "oneway", note: "Gland 路由决策记 Hippo 决策日志", evidence: "opensoul/src/gland/route_policy.py:138 from src.hippo.decision_log import get_decision_log" },
  { id: "gland-immune", from: "gland", to: "immune", dir: "oneway", note: "Gland 模型输出经 Immune 内容审核", evidence: "opensoul/src/gland/router.py:36 from src.immune.moderator import ContentModerator" },
  { id: "sense-gland", from: "sense", to: "gland", dir: "oneway", note: "Sense OCR 推理经 Gland 模型路由", evidence: "opensoul/src/sense/ocr.py:203 from src.gland.router import TaskType" },
  { id: "sense-trajectory", from: "sense", to: "trajectory", dir: "oneway", note: "Sense 行为感知写 Trajectory 事件", evidence: "opensoul/src/sense/behavior.py:127 from src.trajectory.store import TrajectoryEvent" },
  { id: "trajectory-cortex", from: "trajectory", to: "cortex", dir: "oneway", note: "Trajectory 分支摘要用 Cortex token 归因", evidence: "opensoul/src/trajectory/branch_summary.py:61 from src.cortex.token_attribution import estimate_tokens" },
  { id: "trajectory-gland", from: "trajectory", to: "gland", dir: "oneway", note: "Trajectory 摘要生成调 Gland 模型路由", evidence: "opensoul/src/trajectory/branch_summary.py:589 from src.gland.router import ModelRouter" },
  { id: "will-nest", from: "will", to: "nest", dir: "oneway", note: "Will 流程检查点经 Nest 租户隔离", evidence: "opensoul/src/will/checkpoint.py:38 from src.nest.isolation import IsolationEngine" },
  { id: "will-learn", from: "will", to: "learn", dir: "oneway", note: "Will 任务执行收集 Learn 经验", evidence: "opensoul/src/will/job_handlers.py:89 from src.learn.experience_collector import ExperienceCollector" },
  { id: "will-heredity", from: "will", to: "heredity", dir: "oneway", note: "Will 主动行为触发 Heredity 进化周期", evidence: "opensoul/src/will/proactive_behaviors.py:187 from src.heredity.self_evolution import run_evolution_cycle" },
  { id: "will-soul", from: "will", to: "soul", dir: "oneway", note: "Will 工作流状态落 Soul 库", evidence: "opensoul/src/will/proactive_behaviors.py:38 from src.database.postgres import db_pool" },
  { id: "heredity-learn", from: "heredity", to: "learn", dir: "oneway", note: "Heredity 自进化读 Learn 长期学习", evidence: "opensoul/src/heredity/self_evolution.py:16 from src.learn.long_term import …" },
  { id: "learn-nest", from: "learn", to: "nest", dir: "oneway", note: "Learn 课程引擎经 Nest 租户隔离", evidence: "opensoul/src/learn/course_engine.py:15 from src.nest.isolation import IsolationEngine" },
  { id: "capture-soul", from: "capture", to: "soul", dir: "oneway", note: "Capture 捕获入库 Soul 知识库", evidence: "opensoul/src/api/capture.py:17 from src.database.postgres import db_pool（'stores them in the knowledge base'）" },
  { id: "capture-soma", from: "capture", to: "soma", dir: "oneway", note: "Soma 浏览器采集插件投递页面捕获", evidence: "openmate/browser-extension/src/background.ts:18 POST http://localhost:8090/api/capture" },
  { id: "immune-soul", from: "immune", to: "soul", dir: "oneway", note: "Immune 审计日志落 Soul 库", evidence: "opensoul/src/api/immune.py:19 from src.services.tenant_binding import bind_identity / db_pool" },
  { id: "diagnostics-soul", from: "diagnostics", to: "soul", dir: "oneway", note: "Diagnostics 健康探针巡检 Soul /api/health", evidence: "opensoul/src/api/diagnostics.py:16 器官健康注册表 → /api/health" },
  { id: "diagnostics-nerve", from: "diagnostics", to: "nerve", dir: "oneway", note: "Diagnostics 健康探针巡检 Nerve /api/nerve/health", evidence: "opensoul/src/api/diagnostics.py:18 → /api/nerve/health" },
  { id: "diagnostics-vital", from: "diagnostics", to: "vital", dir: "oneway", note: "Diagnostics 健康探针巡检 Vital /api/vital/health", evidence: "opensoul/src/api/diagnostics.py:23 → /api/vital/health" },
  { id: "vision-soul", from: "vision", to: "soul", dir: "oneway", note: "Vision 生成记录落 Soul 库", evidence: "opensoul/src/api/vision.py:11 from src.database.postgres import db_pool" },
  { id: "limb-soul", from: "limb", to: "soul", dir: "oneway", note: "Limb 任务执行记录落 Soul 库", evidence: "opensoul/src/api/limb.py:11 from src.services.tenant_binding import bind_identity / db_pool" },
  { id: "soma-reflex", from: "soma", to: "reflex", dir: "twoway", note: "Soma 协调核挂 Reflex 快速应激（MCP 调用捕获→应激）", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:187 from agent.reflex import get_reflex / :1096 _reflex_hub().on_mcp_call" },
  { id: "soma-pulse", from: "soma", to: "pulse", dir: "twoway", note: "Soma 协调核挂 Pulse 心跳守卫", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:188 from agent.pulse import … / :2351 _pulse_guard_provider" },
  { id: "soma-gland", from: "soma", to: "gland", dir: "oneway", note: "Soma 调 Gland 模型引擎 LLMEngine", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:44 from agent.llm_engine import LLMEngine" },
  { id: "soma-immune", from: "soma", to: "immune", dir: "oneway", note: "Soma 输出经 Immune 注入审计门/密钥脱敏", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:61-62 output_guard / infection_guard InfectionGate" },
  { id: "soma-echo", from: "soma", to: "echo", dir: "oneway", note: "Soma 回合记忆走 Echo 回声标记", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:71 from agent import memory_echo" },
  { id: "soma-gene", from: "soma", to: "gene", dir: "oneway", note: "Soma 接线 Gene 基因模板", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:74 from agent import gene_wire" },
  { id: "soma-hippo", from: "soma", to: "hippo", dir: "oneway", note: "Soma 持久记忆写 Hippo 记忆库（MEMORY.md）", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:82 PersistentMemory（落盘 ~/.hermes/soulmate/memory/MEMORY.md，persistent_memory.py:24）" },
  { id: "soma-marrow", from: "soma", to: "marrow", dir: "oneway", note: "Soma 会话文件进 Marrow 索引（index.db）", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:1926 _file_index.index_file（索引库 ~/.hermes/soulmate/file-index/index.db，file_index.py:45）" },
  { id: "soma-vein", from: "soma", to: "vein", dir: "oneway", note: "Soma 文件下发/下载走 Vein 数据通道", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:181 SendFileTools / :183 DownloadTools" },
  { id: "soma-limb", from: "soma", to: "limb", dir: "oneway", note: "Soma 挂 Limb 浏览器执行器（Playwright/CDP）", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:164 from agent.browser_tools import BrowserTools" },
  { id: "soma-voice", from: "soma", to: "voice", dir: "oneway", note: "Soma 挂 Voice 语音合成 TTSTools", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:179 from agent.tts_tools import TTSTools" },
  { id: "soma-vision", from: "soma", to: "vision", dir: "oneway", note: "Soma 挂 Vision 图像生成/编辑 ImageTools", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:180 from agent.image_tools import ImageTools" },
  { id: "soma-trajectory", from: "soma", to: "trajectory", dir: "oneway", note: "Soma 回合轨迹记录 TrajectoryRecorder", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:109 from agent.trajectory_recorder import TrajectoryRecorder" },
  { id: "soma-mcp", from: "soma", to: "mcp", dir: "oneway", note: "Soma MCP 资源接线 mcp_resources", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:64 from agent.mcp_resources import …" },
  { id: "soma-diagnostics", from: "soma", to: "diagnostics", dir: "oneway", note: "Soma 健康自检 HealthChecker", evidence: "openmate/acp-proxy/agent/soulmate_agent.py:100 from agent.health_checker import HealthChecker" },
  { id: "will-pulse", from: "will", to: "pulse", dir: "twoway", note: "Will 工单挂 Pulse 心跳推进/超时催办", evidence: "openmate/acp-proxy/plugin/flow_collab.py:1105-1114 attach_pulse → loop.set_task_overdue_handler" },
  { id: "will-reflex", from: "will", to: "reflex", dir: "oneway", note: "Will 工单异常进 Reflex 快速应激", evidence: "openmate/acp-proxy/plugin/flow_collab.py:1253 from agent.reflex import get_reflex" },
  { id: "reflex-pulse", from: "reflex", to: "pulse", dir: "twoway", note: "Pulse 自检发现旁路进 Reflex 应激（on_error）", evidence: "openmate/acp-proxy/routes/pulse.py:177-178 from agent.reflex import get_reflex → on_error" },
];

// 拖动位置持久化（localStorage；重置按钮恢复解剖默认位）
const POSITIONS_KEY = "openmate-body-map-positions-v2"; // v2=160x100 宽幅画布（旧 100x100 缓存不兼容）
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
      {/* 悬停原生提示：名字 + 4 字功能说明 */}
      <title>{organ.tag ? `${label} · ${organ.tag}` : label}</title>
      {/* Pulse ring for ok status */}
      {isOk && !active && (
        <circle
          cx={pos.x}
          cy={pos.y}
          r="5.2"
          fill="none"
          stroke={colors.fill}
          strokeWidth="0.3"
          opacity="0.4"
        >
          <animate attributeName="r" from="5.9" to="10" dur="2s" repeatCount="indefinite" />
          <animate attributeName="opacity" from="0.4" to="0" dur="2s" repeatCount="indefinite" />
        </circle>
      )}

      {/* 悬停/选中高亮环（关联邻居可视） */}
      {active && (
        <circle cx={pos.x} cy={pos.y} r="7.9" fill="none" stroke={colors.fill} strokeWidth="0.6" opacity={0.9} />
      )}

      {/* Main circle */}
      <circle
        cx={pos.x}
        cy={pos.y}
        r="5.0"
        fill={isError ? "#ef4444" : isLoading ? "#6b7280" : colors.fill}
        stroke={isError ? "#dc2626" : colors.stroke}
        strokeWidth="0.5"
        opacity={isLoading ? 0.5 : 1}
      />

      {/* Status indicator */}
      {isOk && (
        <circle cx={pos.x + 3.8} cy={pos.y - 3.8} r="1.5" fill="#22c55e" stroke="#16a34a" strokeWidth="0.3" />
      )}
      {isError && (
        <circle cx={pos.x + 3.8} cy={pos.y - 3.8} r="1.5" fill="#ef4444" stroke="#dc2626" strokeWidth="0.3" />
      )}

      {/* Emoji */}
      <text
        x={pos.x}
        y={pos.y + 1.7}
        textAnchor="middle"
        fontSize="5.4"
        className="select-none pointer-events-none"
      >
        {organ.emoji}
      </text>

      {/* Label（名字 + 名字后 4 字功能说明：弱化小字、与名字垂直居中对齐、整体居中于图标） */}
      <text
        x={pos.x}
        y={pos.y + 7.15}
        textAnchor="middle"
        fill="currentColor"
        fontWeight="500"
        className="select-none pointer-events-none"
      >
        <tspan fontSize="2.4">{label}</tspan>
        {organ.tag && (
          <tspan fontSize="2" dx="0.7" dy="-0.15" fontWeight="400" opacity={0.55}>
            {organ.tag}
          </tspan>
        )}
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

  // viewBox 176x100 fit-to-screen：preserveAspectRatio=meet 时按最小缩放比+居中偏移反算
  const svgCoords = (clientX: number, clientY: number) => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    const scale = Math.min(rect.width / 176, rect.height / 100);
    const offX = (rect.width - 176 * scale) / 2;
    const offY = (rect.height - 100 * scale) / 2;
    return {
      x: Math.min(172, Math.max(4, (clientX - rect.left - offX) / scale)),
      y: Math.min(96, Math.max(4, (clientY - rect.top - offY) / scale)),
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
            viewBox="0 0 176 100"
            className="h-full w-full"
            style={{ touchAction: "none" }}
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

            {/* Body outline (simplified) — x1.76 宽幅拉伸配 176x100 画布 */}
            <g transform="scale(1.76 1)">
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
            </g>

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
              <h3 className="font-semibold text-sm">
                {organLabel(selectedOrganData.key)}
                {selectedOrganData.tag && (
                  <span className="ml-1.5 text-xs font-normal text-muted-foreground/70">{selectedOrganData.tag}</span>
                )}
              </h3>
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
            <p className="mb-1.5 text-[9px] leading-relaxed text-muted-foreground/60">{t("bodyMap.wiringNote", "关系边=系统真实代码接线（每条附 file:line 调用证据）；设计文档仅定义器官身份，无证据的边不画")}</p>
            {organRelations(selectedOrganData.key).length === 0 ? (
              <p className="text-[11px] text-muted-foreground">{t("bodyMap.noRelations", "未接线（无真实代码接线证据）")}</p>
            ) : (
              <div className="space-y-1.5">
                {organRelations(selectedOrganData.key).map((rel) => {
                  const other = rel.from === selectedOrganData.key ? rel.to : rel.from;
                  const outgoing = rel.from === selectedOrganData.key;
                  const otherTag = ORGAN_LAYOUT.find((x) => x.key === other)?.tag;
                  return (
                    <div key={rel.id} className="rounded-lg bg-muted/50 px-2.5 py-1.5 text-[11px] leading-relaxed">
                      <span className="font-medium">
                        {rel.dir === "twoway" ? "↔" : outgoing ? "→" : "←"} {organLabel(other)}
                        {otherTag && <span className="ml-1.5 text-[9px] font-normal text-muted-foreground/60">{otherTag}</span>}
                      </span>
                      <p className="text-muted-foreground">{t(`bodyMap.rel.${rel.id}.note`, rel.note)}</p>
                      <p className="mt-0.5 font-mono text-[9px] leading-relaxed text-muted-foreground/70 break-all">{t("bodyMap.evidence", "证据")}: {rel.evidence}</p>
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
              <span className="flex-1 truncate">
                {organLabel(organ.key)}
                {organ.tag && <span className="ml-1.5 text-[10px] text-muted-foreground/60">{organ.tag}</span>}
              </span>
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
