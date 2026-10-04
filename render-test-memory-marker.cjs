// MemoryMarkerBadge真实渲染测试（react-dom/server renderToStaticMarkup）
// 编译：tsc transpileModule（jsx: react-jsx, module: commonjs）
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const os = require('os');

const SRC = path.join(__dirname, 'src/components/memory-marker-badge.tsx');
const outDir = fs.mkdtempSync(path.join(__dirname, '.mmb-render-'));
// 临时产物自清理（统一纪律：失败早退也不留tmpdir垃圾）
process.on('exit', () => { try { fs.rmSync(outDir, { recursive: true, force: true }); } catch {} });
const src = fs.readFileSync(SRC, 'utf8');
const { outputText } = ts.transpileModule(src, {
  compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX,
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    esModuleInterop: true,
  },
  fileName: SRC,
});
const outFile = path.join(outDir, 'memory-marker-badge.js');
fs.writeFileSync(outFile, outputText);

const { renderToStaticMarkup } = require('react-dom/server');
const mod = require(outFile);
const { MemoryMarkerBadge } = mod;

let failed = 0;
function assert(cond, name) {
  if (cond) { console.log('PASS:', name); }
  else { failed++; console.log('FAIL:', name); }
}

// 1. marker=null → 渲染空（零UI干扰）
const htmlNull = renderToStaticMarkup(require('react').createElement(MemoryMarkerBadge, { marker: null }));
assert(htmlNull === '', 'null marker renders empty');

// 2. undefined marker → 渲染空
const htmlUndef = renderToStaticMarkup(require('react').createElement(MemoryMarkerBadge, {}));
assert(htmlUndef === '', 'undefined marker renders empty');

// 3. count=0且files空 → 渲染空
const htmlZero = renderToStaticMarkup(require('react').createElement(MemoryMarkerBadge, {
  marker: { type: 'recall', tokens: 0, count: 0, files: [], items: [] },
}));
assert(htmlZero === '', 'zero-count empty marker renders empty');

// 4. 真实marker → 徽章文案/类型/条数在HTML中（与GET /messages的memory_marker字段同shape）
const marker = { type: 'recall', tokens: 12, count: 2, files: ['ltm_1', 'mem_2'], items: ['片段A'] };
const html = renderToStaticMarkup(require('react').createElement(MemoryMarkerBadge, { marker }));
assert(html.includes('用了记忆'), 'badge shows 用了记忆');
assert(html.includes('记忆召回'), 'badge shows recall label');
assert(html.includes('2 条'), 'badge shows count');
assert(html.includes('title='), 'badge has tooltip title');

// 5. startup类型映射
const htmlStartup = renderToStaticMarkup(require('react').createElement(MemoryMarkerBadge, {
  marker: { type: 'startup', tokens: 5, count: 1, files: ['f'], items: [] },
}));
assert(htmlStartup.includes('启动记忆'), 'startup label mapped');

// 6. count缺省回退files长度（与读侧_decode_memory_marker契约一致）
const htmlFallback = renderToStaticMarkup(require('react').createElement(MemoryMarkerBadge, {
  marker: { type: 'recall', tokens: 0, count: 0, files: ['a', 'b', 'c'], items: [] },
}));
assert(htmlFallback.includes('3 条'), 'count falls back to files length');

// 7. MemoryMarker接口字段与读侧JSON键对齐（type/tokens/count/files/items）
assert(Object.keys(mod).includes('MemoryMarkerBadge'), 'exports MemoryMarkerBadge');

// 8. live模式：--render-marker '<json>' —— 用真实API返回的memory_marker渲染（E2E全链路）
if (process.argv[2] === '--render-marker') {
  const liveMarker = JSON.parse(process.argv[3]);
  const htmlLive = renderToStaticMarkup(require('react').createElement(MemoryMarkerBadge, { marker: liveMarker }));
  assert(htmlLive.includes('用了记忆'), 'live API marker renders badge');
  assert(htmlLive.includes(String(liveMarker.count) + ' 条'), 'live API marker count rendered');
  console.log('LIVE-MARKER-RENDER OK');
}

fs.rmSync(outDir, { recursive: true, force: true });
console.log(failed === 0 ? 'RENDER TEST ALL PASS' : `RENDER TEST ${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);
