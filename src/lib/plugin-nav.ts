/**
 * 插件导航管理器 — 自动从plugin.json读取导航配置
 * 
 * 插件在plugin.json中声明nav，系统自动注册到底部导航栏
 * 插件卸载时自动移除导航项
 */

import { BUILTIN_DOMAINS } from "@/lib/menu-domains";

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

// ═══════════════════════════════════════════════════════════════════
// 菜单贡献（docs/plugin-spec.md §菜单契约）— L1 功能域 + L2 清单，动态生成
//
// 用户定稿（2026-11-19）："插件安装之后生成一级菜单及二级菜单"——manifest 声明
// L1+L2，安装动作触发生效（运行时渲染进导航），卸载即移除；**禁手写死菜单项**。
// 同一插件多二级项按 manifest 顺序渲染；join=并入既有功能域（目标缺席回退独立一级位）。
// ═══════════════════════════════════════════════════════════════════

export interface RawMenuLabel {
  label: string;
  label_i18n?: Record<string, string>;
}

export interface PluginMenuItem {
  id: string;
  label: string;
  href: string;
  /** 工作流可调用单元（tools[].name；安装后进流程工具面） */
  functionId?: string;
  position: string;
}

export interface PluginMenu {
  pluginId: string;
  label: string;
  icon: string;
  home: string;
  position: string;
  /** 并入既有功能域（内置域 id 或插件 id）；目标缺席回退独立一级位 */
  join?: string;
  entries: PluginMenuItem[];
}

/** 多语言标签解析（label_i18n[lang] 覆盖 label，强制 i18n 约束） */
export function menuLabel(
  label: string,
  labelI18n: Record<string, string> | undefined,
  lang: string,
): string {
  const short = (lang || "").split("-")[0];
  return (labelI18n && (labelI18n[short] || labelI18n[lang])) || label;
}

function deriveFromShape(
  id: string,
  frontend: {
    menu?: Record<string, unknown>;
    nav?: Record<string, unknown>;
    pages?: Array<Record<string, unknown>>;
  },
  name: string,
  lang: string,
): PluginMenu | null {
  const menu = frontend.menu as
    | {
        label?: string;
        label_i18n?: Record<string, string>;
        icon?: string;
        position?: string;
        join?: string;
        entries?: Array<{
          id?: string;
          label?: string;
          label_i18n?: Record<string, string>;
          href?: string;
          function?: string;
          position?: string;
        }>;
      }
    | undefined;
  if (menu && menu.label && Array.isArray(menu.entries) && menu.entries.length > 0) {
    return {
      pluginId: id,
      label: menuLabel(menu.label, menu.label_i18n, lang),
      icon: menu.icon || "Puzzle",
      home: `/plugins/${id}`,
      position: menu.position || "end",
      join: menu.join || undefined,
      entries: menu.entries.map((e, index) => ({
        id: e.id || String(index),
        label: menuLabel(e.label || e.id || "", e.label_i18n, lang),
        href: e.href || `/plugins/${id}`,
        functionId: e.function || undefined,
        position: e.position || "end",
      })),
    };
  }
  // legacy 兼容（plugin.json 旧契约）：nav → L1，pages → L2（按清单顺序）
  const nav = frontend.nav as
    | { label?: string; icon?: string; href?: string; position?: string }
    | undefined;
  const pages = frontend.pages || [];
  if (!nav && pages.length === 0) return null;
  const label = menuLabel(nav?.label || name, undefined, lang);
  const entries: PluginMenuItem[] =
    pages.length > 0
      ? pages.map((p, index) => ({
          id: String(p.id ?? index),
          label: String(p.title ?? p.id ?? ""),
          href: `/plugins/${id}/${p.id}`,
          position: "end",
        }))
      : [{ id: "root", label, href: nav?.href || `/plugins/${id}`, position: "end" }];
  return {
    pluginId: id,
    label,
    icon: nav?.icon || "Puzzle",
    home: nav?.href || `/plugins/${id}`,
    position: nav?.position || "end",
    entries,
  };
}

/** 纯函数：插件清单 → 菜单贡献（L1+L2）。无菜单声明返回 null（如纯工具插件）。 */
export function derivePluginMenu(
  plugin: {
    status?: string;
    manifest?: {
      id?: string;
      name?: string;
      frontend?: {
        menu?: Record<string, unknown>;
        nav?: Record<string, unknown>;
        pages?: Array<Record<string, unknown>>;
      };
    };
  },
  lang: string = "zh",
): PluginMenu | null {
  const status = plugin?.status;
  if (status !== "loaded" && status !== "enabled") return null;
  const id = plugin?.manifest?.id;
  if (!id) return null;
  return deriveFromShape(id, plugin?.manifest?.frontend || {}, plugin?.manifest?.name || id, lang);
}

let cachedMenus: PluginMenu[] = [];

/** 拉取全部已安装插件的菜单贡献（清单驱动；卸载即从 /plugins 消失=菜单收走） */
export async function fetchPluginMenus(lang: string = "zh"): Promise<PluginMenu[]> {
  try {
    const res = await fetch(`${getApiBase()}/plugins`);
    if (!res.ok) return cachedMenus;
    const data = await res.json();
    const menus: PluginMenu[] = [];
    for (const plugin of data.plugins || []) {
      const menu = derivePluginMenu(plugin, lang);
      if (!menu) continue;
      // 双清单去重：同 id 优先带完整 entries 的（plugin.yaml ui.menu > legacy nav）
      const existingIndex = menus.findIndex((m) => m.pluginId === menu.pluginId);
      if (existingIndex >= 0) {
        if (menu.entries.length >= menus[existingIndex].entries.length) {
          menus[existingIndex] = menu;
        }
        continue;
      }
      menus.push(menu);
    }
    cachedMenus = menus;
    return menus;
  } catch {
    return cachedMenus;
  }
}

export function getCachedPluginMenus(): PluginMenu[] {
  return cachedMenus;
}

export interface JoinBucket {
  /** position=start 条目（渲染在目标域二级清单最前） */
  start: PluginMenuItem[];
  /** position=end 条目（渲染在目标域二级清单最后） */
  end: PluginMenuItem[];
}

export interface ResolvedPluginMenus {
  /** 独立一级位（无 join 或 join 目标缺席时回退——同一功能只在一个层级出现一次） */
  own: PluginMenu[];
  /** 并入条目：目标域 id（内置域 id 或 plugin:<插件 id>）→ 二级条目 */
  joinEntries: Map<string, JoinBucket>;
}

/**
 * join 解析：
 * - 目标=内置域 id → 并入该域二级（内置域恒在）；
 * - 目标=插件 id 且已安装 → 并入该插件域二级（键 plugin:<id>）；
 * - 目标缺席 → 回退独立一级位（如 bidding 未安装时「商机管理」自立）。
 */
export function resolvePluginMenus(menus: PluginMenu[]): ResolvedPluginMenus {
  const own: PluginMenu[] = [];
  const joinEntries = new Map<string, JoinBucket>();
  const builtinIds = new Set(BUILTIN_DOMAINS.map((d) => d.id));
  const byId = new Map(menus.map((m) => [m.pluginId, m]));
  const bucket = (key: string): JoinBucket => {
    let b = joinEntries.get(key);
    if (!b) {
      b = { start: [], end: [] };
      joinEntries.set(key, b);
    }
    return b;
  };
  for (const menu of menus) {
    let targetKey: string | null = null;
    if (menu.join) {
      if (builtinIds.has(menu.join)) {
        targetKey = menu.join;
      } else if (byId.has(menu.join) && menu.join !== menu.pluginId) {
        targetKey = `plugin:${menu.join}`;
      }
    }
    if (targetKey) {
      const b = bucket(targetKey);
      b.start.push(...menu.entries.filter((e) => e.position === "start"));
      b.end.push(...menu.entries.filter((e) => e.position !== "start"));
    } else {
      own.push(menu);
    }
  }
  return { own, joinEntries };
}
