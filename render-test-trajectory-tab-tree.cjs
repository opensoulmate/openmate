// /system trajectory tab第二消费者接树真实测试（2026-10-02，06:30轮遗留#3销账）
// 编译：tsc transpileModule（jsx: react-jsx, module: commonjs）→ require真实调用：
//   trajectory-tab.tsx 跨目录相对导入 "../../trajectory/span-tree-view|span-tree"
//   用 Node 真实模块解析验证（路径写错=require直接抛MODULE_NOT_FOUND，字符串
//   检查测不出这类错误）+ react-dom/server真实SSR渲染整tab。
// 覆盖：①编译+require冒烟（跨目录导入真实解析，非静态字符串）
//       ②format单一真源（本地副本已删+行为回归钉）
//       ③接线钉（写了≠接线了：fetchTrace数据源/selectSession消费/树渲染条件/
//         切换按钮回放互斥）④tab引用的trajectory.* i18n key全语言在场
//       ⑤TrajectoryTab SSR真实渲染不炸
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.tab-tree-'));
// 临时产物自清理（render-test-gene-keyset同款纪律：测试tmpdir不留垃圾）
process.on('exit', () => { try { fs.rmSync(outDir, { recursive: true, force: true }); } catch {} });

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

// @/ 别名stub（tab只消费cn/getApiBaseUrl/useIsMobile——与树接线无关的宿主依赖）
fs.mkdirSync(path.join(outDir, 'lib'), { recursive: true });
fs.mkdirSync(path.join(outDir, 'hooks'), { recursive: true });
fs.writeFileSync(path.join(outDir, 'lib', 'utils.js'),
  'exports.cn = (...a) => a.filter(Boolean).join(" ");');
fs.writeFileSync(path.join(outDir, 'lib', 'api-client.js'),
  'exports.getApiBaseUrl = () => "http://127.0.0.1:8090";');
fs.writeFileSync(path.join(outDir, 'hooks', 'use-mobile.js'),
  'exports.useIsMobile = () => false;');

const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...args) {
  if (typeof request === 'string' && request.startsWith('@/')) {
    request = path.join(outDir, request.slice(2)) + '.js';
  }
  return origResolve.call(this, request, ...args);
};

// 输出镜像源码相对布局：system/tabs/ → ../../trajectory/ 的相对解析真实成立
const treeFile = compile('src/app/(app)/trajectory/span-tree.ts', 'trajectory/span-tree.js');
compile('src/app/(app)/trajectory/span-tree-view.tsx', 'trajectory/span-tree-view.js');
// 跨会话搜索共享模块（2026-10-04）：tab的新import同款真实解析，缺编译=require抛MODULE_NOT_FOUND
compile('src/app/(app)/trajectory/cross-search.ts', 'trajectory/cross-search.js');
compile('src/app/(app)/trajectory/cross-search-view.tsx', 'trajectory/cross-search-view.js');
// 共享展示组件单一真源（2026-10-05共享化去重）：tab的新import同款真实解析
compile('src/app/(app)/trajectory/trajectory-shared.tsx', 'trajectory/trajectory-shared.js');
const tabFile = compile('src/app/(app)/system/tabs/trajectory-tab.tsx', 'system/tabs/trajectory-tab.js');

const { formatDuration, formatTokens } = require(treeFile);

const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const i18next = require('i18next');
const { initReactI18next } = require('react-i18next');

const zh = require('./src/locales/zh.json');
const en = require('./src/locales/en.json');
const ja = require('./src/locales/ja.json');

const tabSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/system/tabs/trajectory-tab.tsx'), 'utf8');

let failed = 0;
function assert(cond, name) {
  if (cond) { console.log('PASS:', name); }
  else { failed++; console.log('FAIL:', name); }
}

async function main() {
  // ── ① 编译+require冒烟（跨目录相对导入真实解析）──
  let tabMod = null, tabErr = null;
  try { tabMod = require(tabFile); } catch (e) { tabErr = e; }
  assert(tabErr === null && typeof tabMod?.TrajectoryTab === 'function',
    `①require编译后trajectory-tab成功且导出TrajectoryTab${tabErr ? '（' + tabErr.message + '）' : ''}`);
  // tab真的加载了真实span-tree-view/span-tree模块（require.cache实锤，非stub）
  const cached = Object.keys(require.cache);
  assert(cached.some(k => k === path.resolve(path.join(outDir, 'trajectory', 'span-tree-view.js'))),
    '①tab的../../trajectory/span-tree-view导入解析到真实编译模块（require.cache实锤）');
  assert(cached.some(k => k === path.resolve(treeFile)),
    '①tab的../../trajectory/span-tree导入解析到真实编译模块（require.cache实锤）');
  assert(cached.some(k => k === path.resolve(path.join(outDir, 'trajectory', 'trajectory-shared.js'))),
    '①tab的../../trajectory/trajectory-shared导入解析到真实编译模块（require.cache实锤）');

  // ── ② format单一真源（本地副本已删+行为回归钉）──
  assert(!/function formatDuration/.test(tabSrc) && !/function formatTokens/.test(tabSrc),
    '②trajectory-tab本地format定义已删（单一真源=span-tree.ts）');
  assert(tabSrc.includes('formatDuration, formatTokens, type SpanNode, type TreeFilter } from "../../trajectory/span-tree"'),
    '②trajectory-tab从span-tree import format（迁移接线；TreeFilter类型同源）');
  // 行为：tab经同一模块实例取到的format输出逐字（6档回归钉）
  assert(formatDuration(500) === '500ms' && formatDuration(1500) === '1.5s' && formatDuration(90000) === '2m 30s',
    '②formatDuration 3档输出逐字（tab消费的单一真源实例）');
  assert(formatTokens(999) === '999' && formatTokens(1500) === '1.5k' && formatTokens(2500000) === '2.5M',
    '②formatTokens 3档输出逐字（tab消费的单一真源实例）');

  // ── ③ 接线钉（写了≠接线了）──
  assert(tabSrc.includes('import { SpanTreeView } from "../../trajectory/span-tree-view"'),
    '③tab import SpanTreeView（相对路径=跨目录接线）');
  assert(tabSrc.includes('sessions/${sessionId}/trace'),
    '③fetchTrace数据源=/trace（get_trace_tree单一真源，注释提及不算）');
  assert(/const fetchTrace = useCallback/.test(tabSrc),
    '③fetchTrace定义在场（定义钉非注释）');
  assert(tabSrc.includes('fetchTrace(id)') && tabSrc.includes('}, [fetchEvents, fetchTrace])'),
    '③selectSession消费fetchTrace（真实数据路径+依赖数组接线）');
  assert(tabSrc.includes('setTraceRoots(null)'),
    '③selectSession清上一棵树（防切换会话闪现旧树）');
  assert(tabSrc.includes('view === "detail" && treeMode && traceRoots && traceRoots.length > 0') &&
    !tabSrc.includes('treeMode && !replayMode'),
    '③树渲染条件=detail+treeMode+数据在场守卫（回放×树协同：互斥已解除）');
  assert(tabSrc.includes('<SpanTreeView') && tabSrc.includes('getEventColor={getEventColor}') &&
    tabSrc.includes('getEventIcon={getEventIcon}') && tabSrc.includes('key={selectedSession}'),
    '③SpanTreeView挂载+图标/配色props注入+key随会话重置');
  assert(tabSrc.includes('revealedIds={revealedSpanIds}') && tabSrc.includes('const revealedSpanIds = replayMode'),
    '③回放渐进揭示接线：revealedSpanIds计算→revealedIds prop消费');
  assert(/onClick=\{\(\) => \{ setTreeMode\(!treeMode\) \}\}/.test(tabSrc) &&
    !/setReplayMode\(false\); setReplayPlaying\(false\); setTreeMode/.test(tabSrc),
    '③切换按钮接线=纯视图切换（切树不再关回放，字符串逐字）');
  assert(/const \[treeMode, setTreeMode\] = useState\(true\)/.test(tabSrc) &&
    /const \[traceRoots, setTraceRoots\] = useState<SpanNode\[\] \| null>\(null\)/.test(tabSrc),
    '③treeMode默认开（观察面优先）+traceRoots状态定义在场');
  assert(tabSrc.includes('setTraceRoots(d.roots || [])'),
    '③fetchTrace落状态=d.roots（服务端建树产物原样消费）');

  // ── ④ tab引用的trajectory.* i18n key全语言在场 ──
  const used = [...new Set([...tabSrc.matchAll(/t\("(trajectory\.[A-Za-z0-9_.]+)"/g)].map(m => m[1]))];
  assert(used.length > 10, `④tab引用trajectory key抽取（${used.length}个）`);
  const missing = [];
  for (const full of used) {
    const key = full.slice('trajectory.'.length);
    for (const [lang, res] of [['zh', zh], ['en', en], ['ja', ja]]) {
      if (typeof res.trajectory?.[key] !== 'string' || res.trajectory[key].length === 0) missing.push(`${full}@${lang}`);
    }
  }
  assert(missing.length === 0, `④tab引用的trajectory key全3语言在场${missing.length ? '缺:' + missing.join(',') : ''}`);
  assert(used.includes('trajectory.treeView') && used.includes('trajectory.listView'),
    '④新按钮消费treeView/listView键（i18n接线）');

  // ── ⑤ TrajectoryTab SSR真实渲染（整tab编译产物执行不炸）──
  await i18next.use(initReactI18next).init({
    lng: 'zh',
    resources: {
      zh: { translation: zh },
      en: { translation: en },
      ja: { translation: ja },
    },
    interpolation: { escapeValue: false },
  });
  global.window = global.window || {
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    addEventListener() {}, removeEventListener() {},
  };
  let html = '', renderErr = null;
  try { html = renderToStaticMarkup(React.createElement(tabMod.TrajectoryTab)); }
  catch (e) { renderErr = e; }
  assert(renderErr === null && html.length > 200,
    `⑤TrajectoryTab SSR渲染不炸（${html.length}字节）${renderErr ? '（' + renderErr.message + '）' : ''}`);
  assert(html.includes(zh.trajectory.searchEventsPlaceholder) && html.includes(zh.trajectory.refresh),
    '⑤SSR: 列表态真实文案渲染（搜索占位+刷新，i18n链路通）');

  console.log(failed === 0 ? '\nALL PASS' : `\n${failed} FAILED`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch(e => { console.error(e); process.exit(1); });
