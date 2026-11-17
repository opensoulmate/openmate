/**
 * 插件导航管理器 — 自动从plugin.json读取导航配置
 * 
 * 插件在plugin.json中声明nav，系统自动注册到底部导航栏
 * 插件卸载时自动移除导航项
 */

export interface PluginNavConfig {
  label: string;
  icon: string;
  position?: string; // "after:dev-specs" | "before:chat" | "end"
}

export interface PluginNavItem {
  href: string;
  label: string;
  icon: string;
  pluginId: string;
  position: string;
}

// 缓存已加载的插件导航项
let cachedNavItems: PluginNavItem[] = [];

/**
 * 获取API基地址
 */
function getApiBase(): string {
  if (typeof window === 'undefined') return '';
  const hostname = window.location.hostname;
  return `http://${hostname}:8092`;
}

/**
 * 从后端获取所有已加载插件的导航配置
 */
/** 清单页规格（to_frontend_manifest frontend.pages 消费形状：dsl/html 页型） */
export interface PluginPageSpec {
  id: string;
  title: string;
  dsl?: string | null;
  html?: string | null;
}

/**
 * 纯函数：插件 → 菜单项（清单驱动，动态插件页宿主导航长出）。
 * - 有页清单（frontend.pages）→ **逐页长出**一项（DSL 渲染页与 HTML 面板页
 *   同形状：href=/plugins/{id}/{pageId}）；
 * - 无页清单 → 回落插件级 nav 项（legacy plugin.json 契约不变）；
 * - 插件不在清单（未装/已卸载）或未启用 → []（菜单即收走）。
 */
export function derivePluginNavItems(plugin: {
  status?: string;
  manifest?: { id?: string; frontend?: { nav?: PluginNavConfig; pages?: PluginPageSpec[] } };
}): PluginNavItem[] {
  const status = plugin?.status;
  if (status !== 'loaded' && status !== 'enabled') return [];
  const id = plugin?.manifest?.id;
  if (!id) return [];
  const nav = plugin?.manifest?.frontend?.nav;
  const pages = plugin?.manifest?.frontend?.pages;
  const base = {
    icon: nav?.icon || 'Puzzle',
    pluginId: id,
    position: nav?.position || 'end',
  };
  if (Array.isArray(pages) && pages.length > 0) {
    return pages.map((p) => ({
      href: `/plugins/${id}/${p.id}`,
      label: p.title || p.id,
      ...base,
    }));
  }
  if (!nav) return [];
  return [{
    href: `/plugins/${id}`,
    label: nav.label,
    ...base,
  }];
}

export async function fetchPluginNavItems(): Promise<PluginNavItem[]> {
  try {
    const res = await fetch(`${getApiBase()}/plugins`);
    if (!res.ok) return [];
    
    const data = await res.json();
    const plugins = data.plugins || [];
    
    const navItems: PluginNavItem[] = [];
    
    for (const plugin of plugins) {
      // 动态加载的插件：菜单从清单长出（页级：DSL/HTML 页同形状；卸载即收走）
      navItems.push(...derivePluginNavItems(plugin));
    }
    
    cachedNavItems = navItems;
    return navItems;
  } catch {
    return cachedNavItems;
  }
}

/**
 * 获取缓存的插件导航项（同步）
 */
export function getCachedPluginNavItems(): PluginNavItem[] {
  return cachedNavItems;
}

/**
 * 将插件导航项插入到导航列表的正确位置
 * 
 * @param baseItems 基础导航项列表
 * @param pluginItems 插件导航项列表
 * @returns 合并后的导航项列表
 */
export function mergePluginNavItems(
  baseItems: Array<{ href: string; label: string; icon: any }>,
  pluginItems: PluginNavItem[]
): Array<{ href: string; label: string; icon: any }> {
  if (pluginItems.length === 0) {
    return baseItems;
  }
  
  const result = [...baseItems];
  
  // 按position分组
  const beforeItems = pluginItems.filter(p => p.position === 'start');
  const afterItems = pluginItems.filter(p => p.position.startsWith('after:'));
  const endItems = pluginItems.filter(p => p.position === 'end' || !p.position);
  
  // 插入到开头
  for (const item of beforeItems.reverse()) {
    result.unshift({
      href: item.href,
      label: item.label,
      icon: item.icon, // 返回图标名称，由bottom-nav.tsx处理映射
    });
  }
  
  // 插入到指定位置之后
  for (const item of afterItems) {
    const targetHref = item.position.replace('after:', '');
    const targetIndex = result.findIndex(r => r.href === `/${targetHref}`);
    if (targetIndex >= 0) {
      result.splice(targetIndex + 1, 0, {
        href: item.href,
        label: item.label,
        icon: item.icon,
      });
    } else {
      // 找不到目标位置，添加到末尾
      result.push({
        href: item.href,
        label: item.label,
        icon: item.icon,
      });
    }
  }
  
  // 插入到末尾
  for (const item of endItems) {
    result.push({
      href: item.href,
      label: item.label,
      icon: item.icon,
    });
  }
  
  return result;
}
