// trajectory span树视图真实测试（2026-10-02 P1，04:20轮遗留#5销账）
// 编译：tsc transpileModule（jsx: react-jsx, module: commonjs）→ require纯函数真实调用
//       + react-dom/server真实SSR渲染 + 真实i18next
// 覆盖：①span-tree纯函数（flattenVisible/展开折叠/默认展开/drill-down面包屑/
//       rollup容错）②formatDuration/formatTokens迁移回归钉（自trajectory-client
//       逐字迁移，输出不可变）③SpanTreeView SSR渲染（层级缩进/徽标/Σrollup/
//       hint）④三语言trajectory key集一致+en零CJK ⑤接线钉（写了≠接线了：
//       trajectory-client消费SpanTreeView+/trace数据源+单一真源无重复映射表）
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.span-tree-'));

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

const treeFile = compile('src/app/(app)/trajectory/span-tree.ts', 'trajectory/span-tree.js');
const viewFile = compile('src/app/(app)/trajectory/span-tree-view.tsx', 'trajectory/span-tree-view.js');
const {
  findNode, flattenVisible, toggleExpanded, defaultExpandedIds,
  breadcrumbPath, subtreeRollup, isExpandable, formatDuration, formatTokens,
} = require(treeFile);
const { SpanTreeView } = require(viewFile);

const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const i18next = require('i18next');
const { initReactI18next } = require('react-i18next');

const zh = require('./src/locales/zh.json');
const en = require('./src/locales/en.json');
const ja = require('./src/locales/ja.json');

const trajSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/trajectory-client.tsx'), 'utf8');
const viewSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/span-tree-view.tsx'), 'utf8');
const storeSrc = fs.readFileSync(path.join(ROOT, '../opensoul/src/trajectory/store.py'), 'utf8');

let failed = 0;
function assert(cond, name) {
  if (cond) { console.log('PASS:', name); }
  else { failed++; console.log('FAIL:', name); }
}

function keySet(obj, prefix = '') {
  const keys = [];
  for (const [k, v] of Object.entries(obj)) {
    const p = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object') keys.push(...keySet(v, p));
    else keys.push(p);
  }
  return keys.sort();
}

// ── 样本树（与get_trace_tree产物同形：children/depth/subtree_*）──
function node(id, event_type, over = {}) {
  return {
    id, event_type, agent_id: '', content: `${event_type} ${id}`,
    status: 'ok', token_usage: 10, duration_ms: 100, created_at: '2026-10-02T00:00:00Z',
    parent_event_id: null, children: [], depth: 0,
    subtree_tokens: 10, subtree_duration_ms: 100, subtree_events: 1,
    ...over,
  };
}
const subSpan = node('sub', 'span');
const toolX = node('tx', 'tool_call', {
  children: [subSpan],
  subtree_events: 2, subtree_tokens: 60, subtree_duration_ms: 400,
});
const toolY = node('ty', 'tool_call', { status: 'error' });
const llmA = node('la', 'llm_call', {
  children: [toolX, toolY],
  subtree_events: 4, subtree_tokens: 222, subtree_duration_ms: 1500,
});
const llmB = node('lb', 'llm_call');
const root1 = node('r1', 'user_input', {
  children: [llmA, llmB], content: 'user_input r1 hello',
  subtree_events: 6, subtree_tokens: 300, subtree_duration_ms: 2000,
});
const root2 = node('r2', 'user_input', { content: '' });
const ROOTS = [root1, root2];

// ── ① 纯函数行为 ──
assert(isExpandable(toolX) === true && isExpandable(toolY) === false, '①isExpandable按children判定');
assert(findNode(ROOTS, 'sub') === subSpan, '①findNode DFS命中深层节点');
assert(findNode(ROOTS, 'nope') === null, '①findNode不存在返回null');

const allOpen = new Set(['r1', 'la', 'tx', 'lb']);
const flatAll = flattenVisible(ROOTS, allOpen);
assert(flatAll.map(r => r.node.id).join(',') === 'r1,la,tx,sub,ty,lb,r2',
  '①flattenVisible全展开=pre-order含深层');
assert(flatAll.map(r => r.depth).join(',') === '0,1,2,3,2,1,0',
  '①depth=遍历深度（drill-down归零依据）');
assert(flatAll[2].hasChildren === true && flatAll[2].expanded === true, '①可展开且已展开行标记');
assert(flatAll[3].hasChildren === false && flatAll[3].expanded === false, '①叶子行无展开标记');

const flatDefault = flattenVisible(ROOTS, defaultExpandedIds(ROOTS));
assert(flatDefault.map(r => r.node.id).join(',') === 'r1,la,tx,ty,lb,r2',
  '①默认展开深度<2，tool_call层的sub折叠不出现');
assert(flatDefault.find(r => r.node.id === 'tx').expanded === false,
  '①默认深度2的可展开节点=折叠态');

const toggled = toggleExpanded(defaultExpandedIds(ROOTS), 'tx');
assert(toggled.has('tx') && flattenVisible(ROOTS, toggled).some(r => r.node.id === 'sub'),
  '①toggleExpanded展开后子节点出现');
assert(!toggleExpanded(toggled, 'tx').has('tx'), '①toggleExpanded再点=折叠');
assert(defaultExpandedIds(ROOTS).has('tx') === false, '①toggleExpanded不改原Set（React state友好）');

const crumb = breadcrumbPath(ROOTS, 'sub');
assert(crumb.map(n => n.id).join(',') === 'r1,la,tx,sub', '①breadcrumbPath=root→focus全路径');
assert(breadcrumbPath(ROOTS, 'r1').length === 1, '①面包屑聚焦root=单段');
assert(Array.isArray(breadcrumbPath(ROOTS, 'ghost')) && breadcrumbPath(ROOTS, 'ghost').length === 0,
  '①面包屑未知节点=[]（调用方回落全树不炸）');

const roll1 = subtreeRollup(llmA);
assert(roll1.events === 4 && roll1.tokens === 222 && roll1.durationMs === 1500,
  '①rollup优先取get_trace_tree的subtree_*汇总');
const bare = node('bare', 'tool_call', { token_usage: 42, duration_ms: 77 });
delete bare.subtree_tokens; delete bare.subtree_duration_ms; delete bare.subtree_events;
const roll2 = subtreeRollup(bare);
assert(roll2.events === 1 && roll2.tokens === 42 && roll2.durationMs === 77,
  '①rollup缺subtree_*回退自身值（旧数据不显示NaN）');

// ── ② format迁移回归钉（自trajectory-client逐字迁移，输出不可变）──
assert(formatDuration(500) === '500ms', '②formatDuration ms档逐字');
assert(formatDuration(1500) === '1.5s', '②formatDuration 秒档逐字');
assert(formatDuration(90000) === '2m 30s', '②formatDuration 分秒档逐字');
assert(formatTokens(999) === '999', '②formatTokens 原样档逐字');
assert(formatTokens(1500) === '1.5k', '②formatTokens k档逐字');
assert(formatTokens(2500000) === '2.5M', '②formatTokens M档逐字');
assert(!/function formatDuration/.test(trajSrc) && !/function formatTokens/.test(trajSrc),
  '②trajectory-client本地format定义已删（单一真源=span-tree.ts）');
assert(trajSrc.includes('formatDuration, formatTokens, type SpanNode'),
  '②trajectory-client从span-tree import format（迁移接线）');

// ── ③ SSR真实渲染 ──
function renderTree(props) {
  return renderToStaticMarkup(React.createElement(SpanTreeView, {
    roots: ROOTS,
    getEventColor: () => '#3b82f6',
    getEventIcon: () => 'span',
    ...props,
  }));
}

async function main() {
  await i18next.use(initReactI18next).init({
    lng: 'zh',
    resources: {
      zh: { translation: zh },
      en: { translation: en },
      ja: { translation: ja },
    },
    interpolation: { escapeValue: false },
  });
  await i18next.changeLanguage('zh');

  const html = renderTree();
  assert(html.includes('user_input') && html.includes('llm_call') && html.includes('tool_call'),
    '③zh SSR: 三层event_type徽标渲染');
  assert(html.includes('margin-left:20px') && html.includes('margin-left:40px'),
    '③SSR: 深度缩进（1/2层margin递增）');
  assert(html.includes('Σ3 事件') === false && html.includes('Σ2 事件'),
    '③SSR: 折叠子树Σrollup徽标（tx折叠=subtree_events 2；la展开不显示Σ）');
  assert(html.includes('222 token'), '③SSR: rollup token汇总渲染');
  assert(html.includes(zh.trajectory.treeHint), '③SSR: 未聚焦态hint文案渲染');
  assert(html.includes('error'), '③SSR: status≠ok节点错误chip渲染');
  assert(html.includes('user_input r1 hello'),
    '③SSR: content预览渲染');
  assert(!html.includes(zh.trajectory.allSpans), '③SSR: 未聚焦时无面包屑');

  // en渲染（组件内文案零CJK——节点content用ASCII样本）
  await i18next.changeLanguage('en');
  const htmlEn = renderTree({
    roots: [node('r1', 'user_input', { children: [node('la', 'llm_call')], content: 'hello world' })],
  });
  assert(!/[\u4e00-\u9fff]/.test(htmlEn), '③en SSR: 组件文案零CJK');

  // ── ④ i18n key集（trajectory三语言一致+新4键在场+en零CJK）──
  const sets = [zh, en, ja].map((l) => keySet(l.trajectory || {}).join(','));
  assert(sets[0].length > 0 && sets[0] === sets[1] && sets[1] === sets[2],
    '④key parity across zh/en/ja for "trajectory"（58旧键+4新键不缺）');
  for (const k of ['treeView', 'listView', 'treeHint', 'allSpans']) {
    assert([zh, en, ja].every((l) => typeof l.trajectory?.[k] === 'string' && l.trajectory[k].length > 0),
      `④trajectory.${k} present in all 3 locales`);
  }
  assert(!/[\u4e00-\u9fff]/.test(en.trajectory.treeHint + en.trajectory.treeView + en.trajectory.allSpans + en.trajectory.listView),
    '④en新4键零CJK');

  // ── ⑤ 接线钉（写了≠接线了）──
  assert(trajSrc.includes('import { SpanTreeView } from "./span-tree-view"'),
    '⑤trajectory-client import SpanTreeView');
  assert(trajSrc.includes('sessions/${sessionId}/trace'),
    '⑤fetchTrace数据源=/trace（get_trace_tree单一真源）');
  assert(trajSrc.includes('fetchTrace(id)') && trajSrc.includes('const fetchTrace = useCallback'),
    '⑤selectSession消费fetchTrace（真实数据路径）');
  assert(trajSrc.includes('<SpanTreeView') && trajSrc.includes('getEventColor={getEventColor}') &&
    trajSrc.includes('getEventIcon={getEventIcon}'),
    '⑤SpanTreeView挂载+图标/配色props注入（映射表单一真源=trajectory-client）');
  assert(trajSrc.includes('treeMode && traceRoots') && !trajSrc.includes('treeMode && !replayMode'),
    '⑤树渲染条件=数据在场守卫+回放互斥已解除（回放×树协同）');
  assert(trajSrc.includes('revealedIds={revealedSpanIds}') && trajSrc.includes('const revealedSpanIds = replayMode'),
    '⑤回放渐进揭示接线：revealedSpanIds计算→revealedIds prop消费');
  assert(trajSrc.includes('setTreeMode(!treeMode)'),
    '⑤工具栏树/列表切换接线');
  assert(viewSrc.includes('from "./span-tree"') && viewSrc.includes('flattenVisible(') &&
    viewSrc.includes('breadcrumbPath(') && viewSrc.includes('subtreeRollup('),
    '⑤SpanTreeView消费纯函数单一真源（非复制实现）');
  assert(!/const EVENT_COLORS/.test(viewSrc) && !/const EVENT_ICONS/.test(viewSrc),
    '⑤span-tree-view不定义图标/配色映射表（注释提及不算，防双实现漂移）');
  assert(viewSrc.includes('setFocusId') && viewSrc.includes('onToggle'),
    '⑤drill-down/展开交互接线在组件内');
  assert(storeSrc.includes('subtree_tokens') && storeSrc.includes('def get_trace_tree'),
    '⑤后端rollup单一真源=opensoul get_trace_tree（前端零重复建树）');

  console.log(failed === 0 ? '\nALL PASS' : `\n${failed} FAILED`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch(e => { console.error(e); process.exit(1); });
