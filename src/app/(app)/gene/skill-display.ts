// 技能卡信息列显示映射（来源chip + 分类名本地化）——纯函数模块（skill-list-url.ts同例）
//
// 来源口径与后端skill_learner.py的SKILL_SOURCES/SOURCE_UNKNOWN（:93-94）单一真源对齐：
//   extract = 生产学习链提炼（真实学习行）
//   seed    = 标准技能种子落库（seed_standard_skills）
//   test    = 测试/E2E脚本显式标记
//   unknown = 来源列上线前存量行（迁移回填，不猜来源）
//
// 显示原则（"未知不猜"纪律，与后端同门）：未映射的来源/分类值返回null由调用方
// 直出原值——后端将来新增来源值/分类值时前端不冒充"未知/通用"，看到什么显示什么。

/** 后端SKILL_SOURCES+SOURCE_UNKNOWN的已知取值（skill_learner.py:93-94单一真源） */
export const KNOWN_SOURCES = ["extract", "seed", "test", "unknown"] as const

/** facet分类已知取值（_CATEGORY_NORM_SQL归一后的6类，与category_counts同源） */
export const KNOWN_CATEGORIES = [
  "general",
  "coding",
  "file_operations",
  "web_operations",
  "agent_standard",
  "system_operations",
] as const

/** 来源值→i18n key（gene.source.*）；未映射返回null（调用方直出原值，不猜） */
export function sourceLabelKey(source: string): string | null {
  return (KNOWN_SOURCES as readonly string[]).includes(source) ? `gene.source.${source}` : null
}

/**
 * 来源chip色调（语义分级，低饱和）：
 * extract=绿（真实学习）/ seed=蓝（可重播种）/ test=玫红（测试垃圾可整批清）/
 * unknown=灰（存量未知）/ 未映射=灰+虚线边框（与unknown视觉可区分，不冒充已知档）
 */
export function sourceTone(source: string): string {
  switch (source) {
    case "extract":
      return "bg-emerald-500/15 text-emerald-400"
    case "seed":
      return "bg-sky-500/15 text-sky-400"
    case "test":
      return "bg-rose-500/15 text-rose-400"
    case "unknown":
      return "bg-muted/40 text-muted-foreground"
    default:
      return "bg-muted/40 text-muted-foreground border border-dashed border-border"
  }
}

/** 分类值→i18n key（gene.categories.*）；未映射返回null（调用方直出原值） */
export function categoryLabelKey(category: string): string | null {
  return (KNOWN_CATEGORIES as readonly string[]).includes(category)
    ? `gene.categories.${category}`
    : null
}

/**
 * i18n回退取值：key未映射（sourceLabelKey/categoryLabelKey返回null）或译文缺失
 * （t原样返回key）→直出raw原值。未映射值不冒充翻译——比错译诚实。
 */
export function labelOrRaw(t: (key: string) => string, key: string | null, raw: string): string {
  if (!key) return raw
  const translated = t(key)
  return !translated || translated === key ? raw : translated
}
