/**
 * 主题系统 — 支持 7 套主题（6 标准 + 1 自定义），每套含暗色/浅色版本
 * 主题通过 CSS 类名切换，变量定义在 globals.css 中
 */
import i18n from "./i18n";

/** 主题 ID 联合类型 —— 包含所有暗色主题、浅色主题和自定义主题 */
export type ThemeId =
  | "dark"
  | "light"
  | "purple"
  | "deep-abyss"
  | "deep-abyss-light"
  | "cream-mocha"
  | "cream-mocha-light"
  | "nord-night"
  | "nord-night-light"
  | "obsidian-cyan"
  | "obsidian-cyan-light"
  | "twilight-violet"
  | "twilight-violet-light"
  | "dune-sand"
  | "dune-sand-light"
  | "custom";

/** localStorage 存储键名 */
const STORAGE_KEY = "openmate-theme";

/** 当前主题是否为亮色（light / *-light）——供画布 colorMode 等第三方库跟随 */
export function isLightTheme(theme: ThemeId): boolean {
  return theme === "light" || theme.endsWith("-light");
}

/** 所有可用主题列表 —— labelKey 对应 i18n 翻译键，color 为预览色 */
export const themes: { id: ThemeId; labelKey: string; color: string }[] = [
  { id: "dark", labelKey: "theme.dark", color: "#6366f1" },
  { id: "light", labelKey: "theme.light", color: "#818cf8" },
  { id: "purple", labelKey: "theme.purple", color: "#a855f7" },
  // ── 7 套新主题 ──
  { id: "deep-abyss", labelKey: "theme.deepAbyss", color: "#7aa2f7" },
  { id: "deep-abyss-light", labelKey: "theme.deepAbyssLight", color: "#2b5cb8" },
  { id: "cream-mocha", labelKey: "theme.creamMocha", color: "#89b4fa" },
  { id: "cream-mocha-light", labelKey: "theme.creamMochaLight", color: "#3e7fb6" },
  { id: "nord-night", labelKey: "theme.nordNight", color: "#81a1c1" },
  { id: "nord-night-light", labelKey: "theme.nordNightLight", color: "#5e81ac" },
  { id: "obsidian-cyan", labelKey: "theme.obsidianCyan", color: "#5ccfe6" },
  { id: "obsidian-cyan-light", labelKey: "theme.obsidianCyanLight", color: "#0d96b0" },
  { id: "twilight-violet", labelKey: "theme.twilightViolet", color: "#c4a7e7" },
  { id: "twilight-violet-light", labelKey: "theme.twilightVioletLight", color: "#8c68b8" },
  { id: "dune-sand", labelKey: "theme.duneSand", color: "#e69875" },
  { id: "dune-sand-light", labelKey: "theme.duneSandLight", color: "#c96c43" },
  { id: "custom", labelKey: "theme.custom", color: "#89b4fa" },
];

/** 获取带翻译标签的主题列表 */
export function getThemes() {
  return themes.map((t) => ({ ...t, label: i18n.t(t.labelKey) }));
}

/**
 * 从 localStorage 读取已存储的主题 ID
 * 如果存储值不在合法 ThemeId 列表中，回退到 "dark"
 */
export function getStoredTheme(): ThemeId {
  if (typeof window === "undefined") return "dark";
  const stored = localStorage.getItem(STORAGE_KEY);
  // 验证存储值是否为合法 ThemeId
  const validIds = themes.map((t) => t.id);
  if (stored && validIds.includes(stored as ThemeId)) {
    return stored as ThemeId;
  }
  return "dark";
}

/**
 * 应用主题到 document.documentElement
 * - 暗色主题：添加 "dark" + "theme-xxx" 类
 * - 浅色主题：只添加主题类（如 theme-deep-abyss-light）
 * - 系统主题（dark/light/purple）：保持原有逻辑
 */
export function applyTheme(theme: ThemeId) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;

  // 移除所有已知的主题类（防止残留）
  const allThemeClasses = [
    "light", "dark", "theme-purple",
    "theme-deep-abyss", "theme-deep-abyss-light",
    "theme-cream-mocha", "theme-cream-mocha-light",
    "theme-nord-night", "theme-nord-night-light",
    "theme-obsidian-cyan", "theme-obsidian-cyan-light",
    "theme-twilight-violet", "theme-twilight-violet-light",
    "theme-dune-sand", "theme-dune-sand-light",
    "theme-custom",
  ];
  root.classList.remove(...allThemeClasses);
  root.setAttribute("data-theme", theme);

  // ── 系统主题（保持原有逻辑）──
  if (theme === "light") {
    root.classList.add("light");
  } else if (theme === "purple") {
    root.classList.add("dark", "theme-purple");
  }
  // ── 暗色版新主题：需要同时添加 "dark" 类 + 主题类 ──
  else if (
    theme === "deep-abyss" ||
    theme === "cream-mocha" ||
    theme === "nord-night" ||
    theme === "obsidian-cyan" ||
    theme === "twilight-violet" ||
    theme === "dune-sand" ||
    theme === "custom"
  ) {
    root.classList.add("dark", `theme-${theme}`);
  }
  // ── 浅色版新主题：只添加主题类（不加 dark）──
  else if (theme.endsWith("-light")) {
    root.classList.add(`theme-${theme}`);
  }
  // ── 默认深色 ──
  else {
    root.classList.add("dark");
  }

  // 桌面版壳联动：标题栏明暗跟随主题（浏览器无此桥=静默跳过）
  const isLight = theme === "light" || theme.endsWith("-light");
  (window as any).openmateDesktop?.setTheme?.(isLight ? "light" : "dark");
}

/** 持久化主题到 localStorage 并立即应用 */
export function persistTheme(theme: ThemeId) {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEY, theme);
  applyTheme(theme);
}
