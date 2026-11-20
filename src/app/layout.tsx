import type { Metadata, Viewport } from "next";
import "./globals.css";
import "@/styles/sidebar.css";
import I18nProvider from "@/components/i18n-provider";
import SplashGate from "@/components/splash-gate";
import VisualViewportSync from "@/components/visual-viewport-sync";

export const viewport: Viewport = {
  themeColor: "#7c3aed",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  // 移动端键盘遮挡根修①：Chrome 108+ 键盘弹出时缩布局视口（而非悬浮遮挡），
  // flow 布局的聊天输入栏自动顶到键盘上方；iOS 不认此 meta，由 VisualViewportSync 兜底
  interactiveWidget: "resizes-content",
};

export const metadata: Metadata = {
  manifest: "/manifest.json",
  title: "OpenMate｜开放协同的AI智能伙伴",
  description: "An open AI companion platform with pluggable skill extensions",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "OpenMate",
  },
};

// Inline script to prevent FOUC on theme load
// 在 HTML 渲染前根据 localStorage 主题值设置 class，避免闪烁
const themeInitScript = `
(function() {
  try {
    var t = localStorage.getItem('openmate-theme') || 'dark';
    var r = document.documentElement;
    r.setAttribute('data-theme', t);
    // 系统主题
    if (t === 'light') { r.classList.add('light'); }
    else if (t === 'purple') { r.classList.add('dark', 'theme-purple'); }
    // 暗色版新主题：dark + theme-xxx
    else if (t === 'deep-abyss') { r.classList.add('dark', 'theme-deep-abyss'); }
    else if (t === 'cream-mocha') { r.classList.add('dark', 'theme-cream-mocha'); }
    else if (t === 'nord-night') { r.classList.add('dark', 'theme-nord-night'); }
    else if (t === 'obsidian-cyan') { r.classList.add('dark', 'theme-obsidian-cyan'); }
    else if (t === 'twilight-violet') { r.classList.add('dark', 'theme-twilight-violet'); }
    else if (t === 'dune-sand') { r.classList.add('dark', 'theme-dune-sand'); }
    else if (t === 'custom') { r.classList.add('dark', 'theme-custom'); }
    // Apply custom theme colors from localStorage
    if (t === 'custom') {
      try {
        var cc = JSON.parse(localStorage.getItem('openmate-custom-colors') || '{}');
        var defs = [['bg','--custom-bg','#1e1e2e'],['fg','--custom-fg','#cdd6f4'],['card','--custom-card','#26273a'],['accent','--custom-accent','#89b4fa'],['secondary','--custom-secondary','#313244'],['border','--custom-border','#313244'],['sidebar','--custom-sidebar','#181825'],['danger','--custom-danger','#f38ba8'],['success','--custom-success','#a6e3a1']];
        for (var i = 0; i < defs.length; i++) { r.style.setProperty(defs[i][1], cc[defs[i][0]] || defs[i][2]); }
      } catch(e) {}
    }
    // 浅色版新主题：只加 theme-xxx-light（不加 dark）
    else if (t === 'deep-abyss-light') { r.classList.add('theme-deep-abyss-light'); }
    else if (t === 'cream-mocha-light') { r.classList.add('theme-cream-mocha-light'); }
    else if (t === 'nord-night-light') { r.classList.add('theme-nord-night-light'); }
    else if (t === 'obsidian-cyan-light') { r.classList.add('theme-obsidian-cyan-light'); }
    else if (t === 'twilight-violet-light') { r.classList.add('theme-twilight-violet-light'); }
    else if (t === 'dune-sand-light') { r.classList.add('theme-dune-sand-light'); }
    // 默认深色
    else { r.classList.add('dark'); }
  } catch(e) {}
})();
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      {/* h-[var(--app-height,...)]：移动端键盘弹出时 VisualViewportSync 把 --app-height 钉到
          可视高度，body 跟着缩，全站 in-flow 输入面（消息输入/表单）统一顶到键盘上方；
          无键盘时 var 缺省=100vh 与原 h-screen 完全一致（桌面零回归） */}
      <body className="h-[var(--app-height,100vh)] overflow-hidden bg-background text-foreground antialiased">
        <I18nProvider><SplashGate />{children}</I18nProvider>
        <VisualViewportSync />
      <script dangerouslySetInnerHTML={{ __html: "if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js')}" }} />
      </body>
    </html>
  );
}
