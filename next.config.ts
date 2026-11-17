import type { NextConfig } from "next";

const soulApiUrl = process.env.SOUL_API_URL || "http://localhost:8090";

const nextConfig: NextConfig = {
  output: "standalone",
  devIndicators: false,
  allowedDevOrigins: ["192.168.31.82", "100.76.2.155", "172.17.0.1", "172.18.0.1"],
  turbopack: {
    root: '.', // 相对项目根（2026-09-21：去除硬编码绝对路径）
  },
  async rewrites() {
    return [
      {
        source: "/api/soul/:path*",
        destination: `${soulApiUrl}/:path*`,
      },
    ];
  },
  // 菜单/路由收敛（menu-audit.md P0）：死链修复 + 5 大 hub 吸收重复路由 + 无用器官删除，
  // 全程 301 重定向（归位不砍杀：旧地址仍可达，导航收敛后无断链）。
  async redirects() {
    return [
      // ── P0-① 8 条死链 → 正确归宿 ─────────────────────────────
      { source: "/will", destination: "/workflow", permanent: true },
      { source: "/mate", destination: "/chat", permanent: true },
      { source: "/groups", destination: "/ai-groups", permanent: true },
      { source: "/team", destination: "/ai-groups", permanent: true },
      { source: "/graph-builder", destination: "/graph", permanent: true },
      { source: "/diagnostics", destination: "/system", permanent: true },
      { source: "/benchmark", destination: "/system", permanent: true },
      { source: "/activity", destination: "/system", permanent: true },
      // ── P0-③ 5 大 hub 吸收 30 重复路由（301）────────────────────
      // 系统观测 → /system（概览/监控/拓扑/注册表/指标/架构）
      { source: "/monitoring", destination: "/system", permanent: true },
      { source: "/metrics", destination: "/system", permanent: true },
      { source: "/topology", destination: "/system", permanent: true },
      { source: "/registry", destination: "/system", permanent: true },
      { source: "/intelligence", destination: "/system", permanent: true },
      { source: "/ai-engine", destination: "/system", permanent: true },
      { source: "/vital", destination: "/system", permanent: true },
      { source: "/dashboard", destination: "/system", permanent: true },
      // 管理 → /admin（系统管理/权限/企业/Soma/Soul）
      { source: "/permission", destination: "/admin", permanent: true },
      { source: "/enterprise", destination: "/admin", permanent: true },
      { source: "/soma-admin", destination: "/admin", permanent: true },
      { source: "/soul", destination: "/admin", permanent: true },
      // 演进 → /evolution（目标/配置/概览/自愈/版本/轨迹/时间线）
      { source: "/healer", destination: "/evolution", permanent: true },
      { source: "/heredity", destination: "/evolution", permanent: true },
      { source: "/changelog", destination: "/evolution", permanent: true },
      { source: "/trajectory", destination: "/evolution", permanent: true },
      { source: "/timeline", destination: "/evolution", permanent: true },
      // 流程 → /workflow（流程/定时/流水线/运行观测）
      { source: "/cron/:path*", destination: "/workflow", permanent: true },
      { source: "/pipeline", destination: "/workflow", permanent: true },
      // 知识文档 → /knowledge + /docs
      { source: "/tags", destination: "/knowledge", permanent: true },
      { source: "/knowledge-requests", destination: "/knowledge", permanent: true },
      { source: "/dev-specs", destination: "/docs", permanent: true },
      { source: "/devlog", destination: "/docs", permanent: true },
      // 市场合一 → 能力市场（/skills）
      { source: "/marketplace", destination: "/skills", permanent: true },
      // ── 器官处置（用户两轮反例改判）───────────────────────────
      // nest（企业/多租户）、reflex（事件驱动快速应激层）、pulse（时间驱动后台自主循环）
      // 均为**战略核心/战略能力**，改判「留」：路由已恢复为活页（子系统观察面板），不再 301。
      // reflex=被动触发求快、pulse=主动轮询求持续；页面只是观察窗，功能实现在另一路。
      // 企业权限体系（多用户/RBAC/资源级权限/租户隔离/审计）排 P2 首位。
      // （无器官级删除；仅 will/mate 为死链，见上。）
    ];
  },
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "**.githubusercontent.com" },
      { protocol: "https", hostname: "avatars.githubusercontent.com" },
      { protocol: "https", hostname: "lh3.googleusercontent.com" },
    ],
  },
};

export default nextConfig;
