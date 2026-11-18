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
  // 菜单收敛已整体回滚（用户令：恢复收敛前原样菜单与全部入口，宁可乱但全）。
  // 原 5 大 hub 吸收的 30 条 301 已撤——旧路由/旧组件已全部恢复为活页。
  // 仅保留 7 条死链修复（原为实测 404，功能归宿不变：伙伴→对话等）。
  // /will 已补建真页面（flow_engine 观察窗，核心件回归，见 menu-audit 附录 C 改判），
  // /mate 不建页面（Mate=OpenMate 进程本体非器官页），301 保留指向对话主入口。
  async redirects() {
    return [
      // ── 7 条死链 → 正确归宿（原 404，点击可达真实功能页）────────────
      { source: "/mate", destination: "/chat", permanent: true },
      { source: "/groups", destination: "/ai-groups", permanent: true },
      { source: "/team", destination: "/ai-groups", permanent: true },
      { source: "/graph-builder", destination: "/graph", permanent: true },
      { source: "/diagnostics", destination: "/system", permanent: true },
      { source: "/benchmark", destination: "/system", permanent: true },
      { source: "/activity", destination: "/system", permanent: true },
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
