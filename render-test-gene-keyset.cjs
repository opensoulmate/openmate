// 技能库keyset翻页render-test（"分页offset稳定性（可改keyset）"销账的前端契约钉）
// 编译：tsc transpileModule（纯函数文件skill-list-url.ts，span-tree.ts同例）。
// 覆盖：①首屏URL（无cursor无offset）②游标续页URL（encodeURIComponent转义）③category
//       过滤参数④组合态⑤**offset反模式归零**（旧offset=已加载条数翻页不许回归）⑥接线钉
//       （gene-client：fetchSkills吃skillCursor、加载更多按钮带游标+空游标禁点、
//       next_cursor单一真源回填state）。
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.gene-keyset-'));

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

const urlFile = compile('src/app/(app)/gene/skill-list-url.ts', 'gene/skill-list-url.js');
const { skillsListUrl } = require(urlFile);

let failed = 0;
function assert(cond, name) {
  if (cond) { console.log('PASS:', name); }
  else { failed++; console.log('FAIL:', name); }
}

const BASE = 'http://127.0.0.1:8090';

// ── ① 首屏URL：无cursor、无offset ──────────────────────────────────
assert(
  skillsListUrl(BASE, null, '') === `${BASE}/api/gene/skill?limit=100`,
  '首屏URL=limit=100不带cursor/offset/category'
);

// ── ② 游标续页URL：encodeURIComponent（游标含冒号/点号必须转义） ──
const cursor = '1728000000.123456:1728000000.1:skill_abc';
const page2 = skillsListUrl(BASE, cursor, '');
assert(page2 === `${BASE}/api/gene/skill?limit=100&cursor=${encodeURIComponent(cursor)}`,
  '续页URL带cursor且encodeURIComponent转义');
assert(page2.includes(encodeURIComponent(cursor)) && !page2.includes(cursor),
  '游标裸串不出现在URL里（冒号已转义）');

// ── ③ category过滤参数 ───────────────────────────────────────────
assert(
  skillsListUrl(BASE, null, 'file_operations') ===
    `${BASE}/api/gene/skill?limit=100&category=file_operations`,
  'category过滤URL'
);

// ── ④ 组合态：游标×分类（顺序固定limit→cursor→category） ──────────
assert(
  skillsListUrl(BASE, cursor, 'general') ===
    `${BASE}/api/gene/skill?limit=100&cursor=${encodeURIComponent(cursor)}&category=general`,
  '游标+分类组合URL'
);

// ── ⑤ offset反模式归零：旧"offset=已加载条数"翻页不许回归 ─────────
assert(!skillsListUrl(BASE, cursor, '').includes('offset='), 'URL纯函数零offset');
assert(!skillsListUrl(BASE, null, 'x').includes('offset='), 'URL纯函数零offset(过滤态)');

// ── ⑥ 接线钉（gene-client真实渲染路径的源文本契约） ────────────────
const clientSrc = fs.readFileSync(
  path.join(ROOT, 'src/app/(app)/gene/gene-client.tsx'), 'utf8'
);
assert(clientSrc.includes('import { skillsListUrl } from "./skill-list-url"'),
  'gene-client import纯函数（非内联副本=单一真源）');
assert(clientSrc.includes('fetch(skillsListUrl(apiBase, cursor, skillCategory))'),
  'fetchSkills真实fetch走skillsListUrl');
assert(clientSrc.includes('onClick={() => fetchSkills(skillCursor, true)}'),
  '加载更多按钮=游标续页（不是offset=skills.length）');
assert(clientSrc.includes('disabled={skillLoadingMore || !skillCursor}'),
  '空游标禁点（部署错位时防止追加首屏=重复行）');
assert(clientSrc.includes('setSkillCursor(typeof data.next_cursor === "string" && data.next_cursor ? data.next_cursor : null)'),
  'next_cursor单一真源回填skillCursor state');
assert(clientSrc.includes('const [skillCursor, setSkillCursor] = useState<string | null>(null)'),
  'skillCursor state声明');
assert(!clientSrc.includes('offset=${offset}'), '旧offset=模板串已废（回归反钉）');
assert(!clientSrc.includes('fetchSkills(skills.length'), '旧"已加载条数当offset"已废（回归反钉）');

console.log(failed === 0 ? 'ALL PASS' : `FAILURES: ${failed}`);
// 清理transpile临时产物（不给工作区留.gene-keyset-*垃圾）
fs.rmSync(outDir, { recursive: true, force: true });
process.exit(failed === 0 ? 0 : 1);
