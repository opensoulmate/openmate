// 技能卡来源chip+分类本地化render-test（"前端未展示source列"+"分类名chip三语本地化"销账的前端契约钉）
// 编译：tsc transpileModule（纯函数文件skill-display.ts，span-tree.ts/skill-list-url.ts同例）。
// 覆盖：①来源i18n key映射4值 ②"未知不猜"纪律（未映射来源/分类→null直出原值）
//       ③来源chip色调4档+未映射虚线档（不冒充unknown灰） ④分类key映射6值
//       ⑤labelOrRaw回退三态（null key/译文缺失/正常译文） ⑥接线钉（gene-client真实
//       消费点） ⑦三语locale键值齐全+en零CJK。
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.gene-source-'));

function compile(relSrc, relOut) {
  const src = fs.readFileSync(path.join(ROOT, relSrc), 'utf8');
  const { outputText } = ts.transpileModule(src, {
    compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
    fileName: relSrc,
  });
  const outFile = path.join(outDir, relOut);
  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  fs.writeFileSync(outFile, outputText);
  return outFile;
}

const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...args) {
  if (typeof request === 'string' && request.startsWith('@/')) {
    request = path.join(outDir, request.slice(2)) + '.js';
  }
  return origResolve.call(this, request, ...args);
};

const displayFile = compile('src/app/(app)/gene/skill-display.ts', 'gene/skill-display.js');
const { sourceLabelKey, sourceTone, categoryLabelKey, labelOrRaw, KNOWN_SOURCES, KNOWN_CATEGORIES } = require(displayFile);

let failed = 0;
function assert(cond, name) {
  if (cond) { console.log('PASS:', name); }
  else { failed++; console.log('FAIL:', name); }
}

// ── ① 来源i18n key映射（后端SKILL_SOURCES+SOURCE_UNKNOWN全集） ──────
assert(sourceLabelKey('extract') === 'gene.source.extract', 'extract→gene.source.extract');
assert(sourceLabelKey('seed') === 'gene.source.seed', 'seed→gene.source.seed');
assert(sourceLabelKey('test') === 'gene.source.test', 'test→gene.source.test');
assert(sourceLabelKey('unknown') === 'gene.source.unknown', 'unknown→gene.source.unknown');

// ── ② "未知不猜"：未映射来源/分类返回null（调用方直出原值，不冒充已知档） ──
assert(sourceLabelKey('wire解码测试-乱写') === null, '未映射来源→null（不猜）');
assert(sourceLabelKey('') === null, '空来源→null');
assert(categoryLabelKey('自定义分类') === null, '未映射分类→null（不猜）');
assert(categoryLabelKey('') === null, '空分类→null');

// ── ③ 来源chip色调：4已知档互不相同 + 未映射档与unknown档可区分 ──
const tones = ['extract', 'seed', 'test', 'unknown'].map(sourceTone);
assert(new Set(tones).size === 4, '4种来源色调互不相同');
const unknownTone = sourceTone('unknown');
const unmappedTone = sourceTone('未映射值');
assert(unknownTone !== unmappedTone, '未映射值色调≠unknown档（不冒充已知档）');
assert(unmappedTone.includes('border-dashed'), '未映射值=虚线边框档（视觉可区分）');
assert(unknownTone.includes('text-muted-foreground') && !unknownTone.includes('border-dashed'), 'unknown档=灰无虚线');

// ── ④ 分类key映射6值（facet已知全集） ──
for (const cat of KNOWN_CATEGORIES) {
  assert(categoryLabelKey(cat) === `gene.categories.${cat}`, `分类${cat}→gene.categories.${cat}`);
}
assert(KNOWN_CATEGORIES.length === 6, 'facet已知分类恰6类');
assert(KNOWN_SOURCES.length === 4, '已知来源恰4值（SKILL_SOURCES 3+unknown）');

// ── ⑤ labelOrRaw回退三态 ──
const tFull = (k) => ({ 'gene.source.extract': '学习提炼', 'gene.categories.coding': '编程' })[k] || k;
assert(labelOrRaw(tFull, null, 'raw值') === 'raw值', 'key=null→直出原值');
assert(labelOrRaw(tFull, 'gene.source.missing', 'raw值') === 'raw值', '译文缺失（t原样返回key）→直出原值');
assert(labelOrRaw(tFull, 'gene.source.extract', 'extract') === '学习提炼', '正常译文→译文');
assert(labelOrRaw(tFull, 'gene.categories.coding', 'coding') === '编程', '分类译文→译文');

// ── ⑥ 接线钉（gene-client.tsx真实消费点，防死代码） ──
const clientSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/gene/gene-client.tsx'), 'utf8');
assert(clientSrc.includes('from "./skill-display"'), 'gene-client import skill-display');
assert(clientSrc.includes('sourceTone(skill.source || "")'), '卡片行来源chip吃sourceTone');
assert(clientSrc.includes('labelOrRaw(t, sourceLabelKey(skill.source || "")'), '卡片行来源label吃sourceLabelKey+labelOrRaw');
assert(clientSrc.includes('labelOrRaw(t, categoryLabelKey(skill.category), skill.category)'), '卡片行分类label本地化');
assert(clientSrc.includes('labelOrRaw(t, categoryLabelKey(c.category), c.category)'), '过滤chip分类label本地化');
assert(clientSrc.includes('{t("gene.sourceKind")}: <span className="font-mono">{skill.source || "—"}'), '展开行来源类型直出机器值（审计）');
assert(clientSrc.includes('source: string'), 'LearnedSkill接口带source字段（吃后端口径归一列）');

// ── ⑦ 三语locale键值齐全 + en零CJK ──
const cjk = /[\u4e00-\u9fff\u3040-\u30ff]/;
for (const lang of ['zh', 'en', 'ja']) {
  const locale = JSON.parse(fs.readFileSync(path.join(ROOT, `src/locales/${lang}.json`), 'utf8'));
  const g = locale.gene;
  assert(typeof g.sourceKind === 'string' && g.sourceKind.length > 0, `${lang}: gene.sourceKind存在`);
  for (const s of KNOWN_SOURCES) {
    assert(typeof g.source?.[s] === 'string' && g.source[s].length > 0, `${lang}: gene.source.${s}存在`);
  }
  for (const c of KNOWN_CATEGORIES) {
    assert(typeof g.categories?.[c] === 'string' && g.categories[c].length > 0, `${lang}: gene.categories.${c}存在`);
  }
  if (lang === 'en') {
    const enVals = [g.sourceKind, ...Object.values(g.source || {}), ...Object.values(g.categories || {})];
    assert(enVals.every(v => typeof v === 'string' && !cjk.test(v)), 'en译文零CJK');
  }
  if (lang === 'zh' || lang === 'ja') {
    // zh/ja的unknown档必须与英文区分开（是真翻译不是占位）
    assert(g.source.unknown !== 'Legacy unknown', `${lang}: unknown档已本地化`);
  }
}

console.log(failed === 0 ? 'ALL PASS' : `FAILED: ${failed}`);
// 清理transpile临时产物（06:30轮自清理惯例，不留.gene-source-*垃圾）
fs.rmSync(outDir, { recursive: true, force: true });
process.exit(failed === 0 ? 0 : 1);
