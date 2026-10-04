// 技能库列表URL单一纯函数（keyset翻页契约面，render-test-gene-keyset.cjs钉住）。
// 与后端GET /api/gene/skill的双模分页契约对齐（src/api/gene.py list_learned_skills）：
// - cursor=null=首屏（不带cursor参数）；否则带keyset游标续页（encodeURIComponent
//   ——游标含冒号/点号，不转义会破坏query解析）；
// - category=""=全部不带参（faceted-search过滤参数）；
// - **不再有offset**——"offset=已加载条数"的翻页面对活表（学习链每回合可落新技能、
//   record_usage改变排序键）会重复/漏行；keyset游标锚定上页末行排序键，页边界
//   不随翻页间隙的增删移位（dev-reports挂账"分页offset稳定性（可改keyset）"销账）。
//
// 独立成纯函数文件（span-tree.ts同例）：URL契约不进组件副作用面，render-test可
// 直接require transpile产物逐字钉URL形状。

export function skillsListUrl(apiBase: string, cursor: string | null, category: string): string {
  const catParam = category ? `&category=${encodeURIComponent(category)}` : ""
  const cursorParam = cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""
  return `${apiBase}/api/gene/skill?limit=100${cursorParam}${catParam}`
}
