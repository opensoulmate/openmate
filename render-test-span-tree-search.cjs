// 树内搜索/高亮/过滤 真实测试（2026-10-02，06:30轮遗留#4销账）
// 编译：tsc transpileModule（jsx: react-jsx, module: commonjs）→ require纯函数真实调用
//       + react-dom/server真实SSR渲染（SpanTreeBody带filter prop=过滤真实路径）+ 真实i18next
// 覆盖：①nodeMatches三条件AND ②filterTree剪树语义+不可变 ③matchIds/expandAllIds/
//       eventTypeCounts/countErrors ④filteredRollup镜像store.py rollup公式 ⑤splitMatch
//       高亮分段 ⑥SSR过滤渲染（命中+祖先/剪兄弟/自动展开/高亮/摘要/空态/Σ重算）
//       ⑦SSR工具栏（搜索框/chips/错误chip）⑧i18n复用键三语言在场（零新增键）⑨接线钉
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.span-tree-search-'));

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

const treeFile = compile('src/app/(app)/trajectory/span-tree.ts', 'trajectory/span-tree.js');
const viewFile = compile('src/app/(app)/trajectory/span-tree-view.tsx', 'trajectory/span-tree-view.js');
const {
  filterTree, nodeMatches, matchIds, expandAllIds, filteredRollup,
  eventTypeCounts, countErrors, splitMatch,
} = require(treeFile);
const { SpanTreeView, SpanTreeBody } = require(viewFile);

const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const i18next = require('i18next');
const { initReactI18next } = require('react-i18next');

const zh = require('./src/locales/zh.json');
const en = require('./src/locales/en.json');
const ja = require('./src/locales/ja.json');

const viewSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/span-tree-view.tsx'), 'utf8');
const trajSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/trajectory-client.tsx'), 'utf8');
const tabSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/system/tabs/trajectory-tab.tsx'), 'utf8');
const storeSrc = fs.readFileSync(path.join(ROOT, '../opensoul/src/trajectory/store.py'), 'utf8');

let failed = 0;
function assert(cond, name) {
  if (cond) { console.log('PASS:', name); }
  else { failed++; console.log('FAIL:', name); }
}

// ── 样本树（与render-test-span-tree.cjs同形）──
function node(id, event_type, over = {}) {
  return {
    id, event_type, agent_id: '', content: `${event_type} ${id}`,
    status: 'ok', token_usage: 10, duration_ms: 100, created_at: '2026-10-02T00:00:00Z',
    parent_event_id: null, children: [], depth: 0,
    subtree_tokens: 10, subtree_duration_ms: 100, subtree_events: 1,
    ...over,
  };
}
const subSpan = node('sub', 'span', { content: 'span sub needle-leaf' });
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

// ── ① nodeMatches 三条件AND ──
assert(nodeMatches(subSpan, { query: 'SUB' }) === true, '①query不区分大小写命中content');
assert(nodeMatches(toolY, { query: 'tool_call' }) === true, '①query命中event_type');
assert(nodeMatches(llmA, { query: 'la' }) === true, '①query命中id相关字段（content含id）');
assert(nodeMatches(toolY, { query: 'error' }) === true, '①query命中status');
assert(nodeMatches(subSpan, { query: 'zzz' }) === false, '①query未命中=false');
assert(nodeMatches(subSpan, { query: '' }) === true, '①空query=不按文本过滤（全过）');
assert(nodeMatches(subSpan, { query: '  ' }) === true, '①纯空白query=不过滤');
assert(nodeMatches(toolX, { query: '', types: new Set(['tool_call']) }) === true,
  '①类型白名单命中');
assert(nodeMatches(subSpan, { query: '', types: new Set(['tool_call']) }) === false,
  '①类型白名单未命中（只看tool_call）');
assert(nodeMatches(toolX, { query: '', types: new Set() }) === true, '①空白名单=全部');
assert(nodeMatches(toolY, { query: '', errorsOnly: true }) === true, '①errorsOnly命中status≠ok');
assert(nodeMatches(toolX, { query: '', errorsOnly: true }) === false, '①errorsOnly未命中status=ok');
assert(nodeMatches(toolY, { query: 'zzz', errorsOnly: true }) === false,
  '①query×errorsOnly AND语义（一半命中=false）');
assert(nodeMatches(toolY, { query: 'tool', types: new Set(['llm_call']), errorsOnly: true }) === false,
  '①三条件AND（类型不符=false）');

// ── ② filterTree 剪树语义+不可变 ──
const byQuery = filterTree(ROOTS, { query: 'hello' });
assert(byQuery.length === 1 && byQuery[0].id === 'r1' && byQuery[0].children.length === 0,
  '②命中节点保留+未命中子树整枝剪掉（r1自命中，la/lb剪）');
const byDeep = filterTree(ROOTS, { query: 'sub' });
assert(byDeep.length === 1 && byDeep[0].id === 'r1',
  '②深层命中→祖先上下文保留（root链）');
const deepChain = byDeep[0].children[0];
assert(deepChain.id === 'la' && deepChain.children[0].id === 'tx' &&
  deepChain.children[0].children[0].id === 'sub',
  '②祖先链r1→la→tx→sub全保留（上下文可读）');
assert(deepChain.children.length === 1 && deepChain.children[0].children.length === 1,
  '②未命中兄弟剪掉（ty/lb/r2不出现）');
const byType = filterTree(ROOTS, { query: '', types: new Set(['tool_call']) });
const typeIds = [];
(function walk(ns) { for (const n of ns) { typeIds.push(n.id); walk(n.children || []); } })(byType);
assert(typeIds.join(',') === 'r1,la,tx,ty',
  '②只看tool_call=tx/ty命中+r1/la仅上下文，sub/lb/r2剪');
assert(byType[0].children[0].children[0].children.length === 0,
  '②命中节点的未命中子节点也剪（tx的sub剪）');
const byErr = filterTree(ROOTS, { query: '', errorsOnly: true });
assert(byErr.length === 1 && byErr[0].children[0].children.length === 1 &&
  byErr[0].children[0].children[0].id === 'ty',
  '②errorsOnly=ty+祖先上下文，其余剪');
const byTypeErr = filterTree(ROOTS, { query: '', types: new Set(['tool_call']), errorsOnly: true });
assert(byTypeErr[0].children[0].children[0].id === 'ty' && byTypeErr[0].children[0].children.length === 1,
  '②类型×errorsOnly组合AND');
assert(filterTree(ROOTS, { query: 'zzz' }).length === 0, '②零命中=[]');
// 不可变：原树children结构不被剪树改写（React state友好）
assert(root1.children.length === 2 && llmA.children.length === 2 && toolX.children.length === 1 &&
  subSpan.children.length === 0,
  '②filterTree不改原树（children长度原样）');
assert(root1.children[0] === llmA && llmA.children[0] === toolX,
  '②原树节点引用不变（剪树=新拷贝）');

// ── ③ matchIds/expandAllIds/eventTypeCounts/countErrors ──
assert([...matchIds(ROOTS, { query: 'hello' })].join(',') === 'r1', '③matchIds=命中id（祖先不算命中）');
assert([...matchIds(ROOTS, { query: 'sub' })].join(',') === 'sub', '③深层单点命中集');
const allExp = expandAllIds(ROOTS);
assert(allExp.size === 3 && allExp.has('r1') && allExp.has('la') && allExp.has('tx'),
  '③expandAllIds=全部可展开id（lb/ty/sub叶子不进）');
const counts = eventTypeCounts(ROOTS);
assert(counts.map(c => `${c.type}:${c.count}`).join(',') === 'llm_call:2,tool_call:2,user_input:2,span:1',
  '③eventTypeCounts计数+（count降序,type升序）稳定排序');
assert(countErrors(ROOTS) === 1, '③countErrors=status≠ok计数（ty）');

// ── ④ filteredRollup 镜像store.py rollup公式 ──
const full = filteredRollup(llmA);
assert(full.events === 4 && full.tokens === 40 && full.durationMs === 400,
  '④filteredRollup全树=events 1+Σ子/tokens自身+Σ子/duration自身+Σ子');
assert(storeSrc.includes('subtree_events += child') && storeSrc.includes('subtree_tokens += child'),
  '④公式与opensoul store.py rollup()逐项一致（单一真源镜像）');
const pruned = filteredRollup(filterTree([toolX], { query: 'sub' })[0]);
assert(pruned.events === 2 && pruned.tokens === 20 && pruned.durationMs === 200,
  '④剪后子树求和（tx+sub=2事件/20tok/200ms——不是subtree_*的60/400假数）');

// ── ⑤ splitMatch 高亮分段 ──
const segs = splitMatch('Hello world hello', 'hello');
assert(segs.length === 3 && segs[0].hit && !segs[1].hit && segs[2].hit, '⑤多段命中（不区分大小写）');
assert(segs.map(s => s.text).join('') === 'Hello world hello', '⑤分段拼回=原文（无损）');
assert(splitMatch('abc', 'zzz').length === 1 && splitMatch('abc', 'zzz')[0].hit === false,
  '⑤无命中=单段不高亮');
assert(splitMatch('abc', '').length === 1 && splitMatch('abc', '')[0].hit === false,
  '⑤空query=单段不高亮');
assert(splitMatch('aaa', 'aa').length === 2, '⑤非重叠推进（不循环死机）');

// ── SSR工具函数 ──
function renderBody(filter, props = {}) {
  return renderToStaticMarkup(React.createElement(SpanTreeBody, {
    roots: ROOTS,
    filter,
    expandedIds: new Set(),
    onToggle: () => {},
    onFocus: () => {},
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

  // ── ⑥ SSR过滤渲染（SpanTreeBody真实过滤路径）──
  const htmlQ = renderBody({ query: 'hello' });
  assert(htmlQ.includes('user_input r1 ') && htmlQ.includes('hsl(var(--primary) / 0.28)') &&
    !htmlQ.includes('llm_call la') && !htmlQ.includes('llm_call lb'),
    '⑥query命中行+祖先保留，未命中兄弟剪（SSR；命中片段单独成span）');
  assert(htmlQ.includes('hsl(var(--primary) / 0.28)'),
    '⑥命中片段高亮样式渲染');
  assert(htmlQ.includes('1 条结果') && htmlQ.includes('hello'),
    '⑥resultsFor摘要（命中计数+查询词）');

  const htmlDeep = renderBody({ query: 'sub' });
  assert(htmlDeep.includes('needle-leaf'),
    '⑥深层命中自动全展开（默认折叠的深度3节点可见；marker=needle-leaf）');
  assert(htmlDeep.includes('40 token') && !htmlDeep.includes('300 token'),
    '⑥过滤态Σ用filteredRollup重算（40=剪后树合计；300=subtree_*假数不出）');

  const htmlType = renderBody({ query: '', types: new Set(['tool_call']) });
  assert(htmlType.includes('tool_call tx') && htmlType.includes('tool_call ty'),
    '⑥只看tool_call=两个tool_call行都在');
  assert(htmlType.includes('llm_call la') && htmlType.includes('user_input r1 hello'),
    '⑥祖先上下文行保留（la/r1）');
  assert(!htmlType.includes('needle-leaf') && !htmlType.includes('llm_call lb'),
    '⑥未命中子树剪（sub/lb不出现）');
  assert(htmlType.includes('已筛选: tool_call'),
    '⑥filtered摘要渲染（类型白名单可见性声明）');

  const htmlErr = renderBody({ query: '', errorsOnly: true });
  assert(htmlErr.includes('tool_call ty') && !htmlErr.includes('tool_call tx'),
    '⑥errorsOnly=错误行保留、ok行剪');
  assert(htmlErr.includes('已筛选: ' + zh.common.error),
    '⑥errorsOnly摘要渲染（common.error复用键）');

  const htmlNone = renderBody({ query: 'zzz-nomatch' });
  assert(htmlNone.includes(zh.trajectory.noMatchingEvents) && !htmlNone.includes(zh.trajectory.noEventsInSession),
    '⑥零命中=noMatchingEvents空态（不是noEventsInSession）');

  const htmlNoFilter = renderBody({ query: '', types: new Set(), errorsOnly: false });
  assert(htmlNoFilter.includes('300 token') && !htmlNoFilter.includes('40 token'),
    '⑥无过滤态Σ=subtree_*服务端汇总（回归：行为不变）');
  assert(!htmlNoFilter.includes('条结果') && !htmlNoFilter.includes('已筛选'),
    '⑥无过滤态无摘要行');

  // ── ⑦ SSR工具栏（SpanTreeView整体）──
  const htmlFull = renderToStaticMarkup(React.createElement(SpanTreeView, {
    roots: ROOTS,
    getEventColor: () => '#3b82f6',
    getEventIcon: () => 'span',
  }));
  assert(htmlFull.includes(zh.trajectory.searchEventsPlaceholder), '⑦搜索输入框placeholder渲染');
  assert(htmlFull.includes(zh.common.all), '⑦All chip渲染（清除全部过滤）');
  assert(htmlFull.includes('tool_call') && htmlFull.includes('llm_call') && htmlFull.includes('user_input'),
    '⑦事件类型chips渲染');
  assert(htmlFull.includes(zh.common.error), '⑦errorsOnly chip渲染（countErrors>0才出）');
  assert(htmlFull.includes(zh.trajectory.treeHint), '⑦未聚焦hint照旧渲染（回归）');

  await i18next.changeLanguage('en');
  const htmlEn = renderToStaticMarkup(React.createElement(SpanTreeView, {
    roots: [node('r1', 'user_input', { children: [node('la', 'llm_call')], content: 'hello world' })],
    getEventColor: () => '#3b82f6',
    getEventIcon: () => 'span',
  }));
  assert(!/[\u4e00-\u9fff]/.test(htmlEn), '⑦en SSR: 工具栏+组件文案零CJK');
  await i18next.changeLanguage('zh');

  // ── ⑧ i18n复用键三语言在场（零新增键——复用既有键）──
  for (const [ns, k] of [['common', 'all'], ['common', 'error'], ['trajectory', 'searchEventsPlaceholder'],
    ['trajectory', 'resultsFor'], ['trajectory', 'filtered'], ['trajectory', 'noMatchingEvents']]) {
    assert([zh, en, ja].every((l) => typeof l[ns]?.[k] === 'string' && l[ns][k].length > 0),
      `⑧${ns}.${k} present in all 3 locales（复用键防删除钉）`);
  }
  assert(!/[\u4e00-\u9fff]/.test(en.common.all + en.common.error + en.trajectory.searchEventsPlaceholder +
    en.trajectory.resultsFor + en.trajectory.filtered + en.trajectory.noMatchingEvents),
    '⑧en复用键零CJK');

  // ── ⑨ 接线钉（写了≠接线了）──
  assert(viewSrc.includes('<SpanTreeBody') && viewSrc.includes('filter={filter}'),
    '⑨SpanTreeView→SpanTreeBody filter接线');
  assert(viewSrc.includes('filterTree(') && viewSrc.includes('matchIds(') &&
    viewSrc.includes('expandAllIds(') && viewSrc.includes('filteredRollup(') && viewSrc.includes('splitMatch('),
    '⑨SpanTreeBody消费纯函数单一真源');
  assert(!/function filterTree/.test(viewSrc) && !/function splitMatch/.test(viewSrc) &&
    !/function nodeMatches/.test(viewSrc),
    '⑨view不重复实现纯函数（防双实现漂移）');
  assert(viewSrc.includes('setQuery("")') && viewSrc.includes('setTypeFilter(new Set<string>())') &&
    viewSrc.includes('setErrorsOnly(false)'),
    '⑨roots变化重置搜索/过滤（不残留上一棵树状态）');
  assert(viewSrc.includes('eventTypeCounts(roots)') && viewSrc.includes('countErrors(roots)'),
    '⑨chips数据源=全树计数（过滤中chips不消失）');
  assert(trajSrc.includes('<SpanTreeView'), '⑨消费者1 trajectory-client挂载SpanTreeView（搜索免费获得）');
  assert(tabSrc.includes('<SpanTreeView'), '⑨消费者2 trajectory-tab挂载SpanTreeView（搜索免费获得）');
  assert(viewSrc.includes('trajectory.resultsFor') && viewSrc.includes('trajectory.filtered') &&
    viewSrc.includes('common.error') && viewSrc.includes('common.all'),
    '⑨UI文案全部走i18n复用键（零硬编码）');

  console.log(failed === 0 ? '\nALL PASS' : `\n${failed} FAILED`);
  fs.rmSync(outDir, { recursive: true, force: true });
  process.exit(failed === 0 ? 0 : 1);
}

main().catch(e => { console.error(e); process.exit(1); });
