"use client";

/**
 * 插件页级路由 /plugins/{plugin}/{page} — 清单页逐页长出（DSL 渲染页与 HTML
 * 面板页同形状），由 PluginDslView 按清单页型分支渲染；菜单项由
 * plugin-nav.derivePluginNavItems 从同一份清单长出（装即长、卸即收）。
 */

import { useParams } from "next/navigation";

import { PluginDslView } from "@/components/plugin-ui/panel-renderer";

export default function PluginSubPage() {
  const params = useParams();
  const pluginName = params.plugin as string;
  const pageId = params.page as string;
  return <PluginDslView pluginId={pluginName} pageId={pageId} />;
}
