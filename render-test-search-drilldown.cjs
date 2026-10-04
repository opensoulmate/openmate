// 搜索→树钻取整合 render-test（2026-10-05，"树内搜索与扁平搜索整合"销账）
// 编译：tsc transpileModule（span-tree.ts/cross-search.ts纯函数 + span-tree-view.tsx
//       react-dom/server真实SSR + 真实i18next）。
// 覆盖：①treeFilterFromSearch适配（trim/类型集/status精确值/errorsOnly恒false）
//       ②statuses精确语义（error≠errorsOnly放宽档不冒充/空status归一ok/多值/AND）
//       ③filterIsActive/sameFilter ④SSR携带面（initialFilter预填输入框/来自搜索
//       chip/status chip/剪树/摘要）⑤无携带=既有形态零变化 ⑥两消费者接线钉
//       ⑦三语i18n键+en零CJK ⑧既有契约回归钉（q=反模式/键在场）
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.search-drilldown-'));
process.on('exit', () => { try { fs.rmSync(outDir, { recursive: true, force: true }); } catch {} });

try {
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
  const csFile = compile('src/app/(app)/trajectory/cross-search.ts', 'trajectory/cross-search.js');
  const viewFile = compile('src/app/(app)/trajectory/span-tree-view.tsx', 'trajectory/span-tree-view.js');

  const {
    nodeMatches, filterTree, filterIsActive, sameFilter,
  } = require(treeFile);
  const { treeFilterFromSearch, buildSearchQueryString } = require(csFile);
  const { SpanTreeView, SpanTreeBody } = require(viewFile);

  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const i18next = require('i18next');
  const { initReactI18next } = require('react-i18next');

  const zh = require('./src/locales/zh.json');
  const en = require('./src/locales/en.json');
  const ja = require('./src/locales/ja.json');

  const viewSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/span-tree-view.tsx'), 'utf8');
  const treeSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/span-tree.ts'), 'utf8');
  const csSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/cross-search.ts'), 'utf8');
  const trajSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/trajectory-client.tsx'), 'utf8');
  const tabSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/system/tabs/trajectory-tab.tsx'), 'utf8');

  let failed = 0;
  let passed = 0;
  function assert(cond, name) {
    if (cond) { passed++; console.log('PASS:', name); }
    else { failed++; console.log('FAIL:', name); }
  }

  // ── 样本树：error/warn/ok/空status四形态 ──
  function node(id, event_type, over = {}) {
    return {
      id, event_type, agent_id: 'agent', content: `${event_type} ${id}`,
      status: 'ok', token_usage: 10, duration_ms: 100, created_at: '2026-10-05T00:00:00Z',
      parent_event_id: null, children: [], depth: 0,
      subtree_tokens: 10, subtree_duration_ms: 100, subtree_events: 1,
      ...over,
    };
  }
  const errN = node('e1', 'tool_call', { status: 'error', content: 'boom error tool' });
  const warnN = node('w1', 'tool_call', { status: 'warn', content: 'warned tool' });
  const okN = node('o1', 'llm_call', { status: 'ok', content: 'fine llm' });
  const emptyN = node('n1', 'llm_call', { status: '', content: 'empty status llm' });
  const rootN = node('r1', 'round', {
    children: [errN, warnN, okN, emptyN], content: 'round root',
    subtree_events: 5, subtree_tokens: 50, subtree_duration_ms: 500,
  });
  const ROOTS = [rootN];

  // ── ① treeFilterFromSearch 适配 ──
  const f1 = treeFilterFromSearch({ query: ' k ', selectedTypes: new Set(['tool_call', 'llm_call']), status: 'error' });
  assert(f1.query === 'k', '①query trim');
  assert(f1.types.size === 2 && f1.types.has('tool_call') && f1.types.has('llm_call'), '①types保留全部勾选（Set复制）');
  assert(f1.statuses.size === 1 && f1.statuses.has('error'), '①status=error→statuses精确值恰一档');
  assert(f1.errorsOnly === false, '①errorsOnly恒false（错误chip是独立用户动作，不替用户预设）');
  const f2 = treeFilterFromSearch({ query: '', selectedTypes: [], status: '' });
  assert(f2.statuses.size === 0 && f2.types.size === 0 && f2.query === '', '①空条件=不过滤（statuses空集不设防）');
  const srcTypes = new Set(['tool_call']);
  const f3 = treeFilterFromSearch({ query: 'x', selectedTypes: srcTypes, status: 'ok' });
  srcTypes.add('llm_call');
  assert(f3.types.size === 1 && !f3.types.has('llm_call'), '①types是副本（外部改动不污染携带面）');
  assert(f3.statuses.has('ok') && !f3.statuses.has('error'), '①status=ok只带ok档');

  // ── ② statuses精确语义（不冒充——与errorsOnly放宽档的有意不对称）──
  assert(nodeMatches(errN, { query: '', statuses: new Set(['error']) }) === true, '②statuses=error命中error节点');
  assert(nodeMatches(warnN, { query: '', statuses: new Set(['error']) }) === false,
    '②warn不冒充error（精确档——放宽档会误命中，见下条对照）');
  assert(nodeMatches(warnN, { query: '', errorsOnly: true }) === true,
    '②对照：errorsOnly=status≠ok放宽档含warn（两档语义差异钉死）');
  assert(nodeMatches(okN, { query: '', statuses: new Set(['ok']) }) === true, '②statuses=ok命中ok节点');
  assert(nodeMatches(emptyN, { query: '', statuses: new Set(['ok']) }) === true,
    '②空status归一ok后匹配（与errorsOnly同口径）');
  assert(nodeMatches(emptyN, { query: '', errorsOnly: true }) === false, '②空status归一ok（errorsOnly不误伤）');
  assert(nodeMatches(warnN, { query: '', statuses: new Set(['error', 'warn']) }) === true, '②多值白名单OR');
  assert(nodeMatches(errN, { query: '', statuses: new Set() }) === true, '②空集=不过滤');
  assert(nodeMatches(errN, { query: 'zzz', statuses: new Set(['error']) }) === false, '②文本×status AND');
  assert(nodeMatches(errN, { query: '', types: new Set(['llm_call']), statuses: new Set(['error']) }) === false,
    '②类型×status AND');

  // ── ③ filterIsActive / sameFilter ──
  assert(filterIsActive({ query: '', types: new Set(), errorsOnly: false, statuses: new Set() }) === false,
    '③全空=不生效');
  assert(filterIsActive({ query: '  ', types: new Set(), errorsOnly: false, statuses: new Set() }) === false,
    '③纯空白query=不生效');
  assert(filterIsActive({ query: '', statuses: new Set(['error']) }) === true, '③statuses非空=生效');
  assert(filterIsActive({ query: '', types: new Set(['tool_call']) }) === true, '③types非空=生效');
  const base = { query: ' k ', types: new Set(['a', 'b']), errorsOnly: false, statuses: new Set(['error']) };
  assert(sameFilter(base, { query: 'k', types: new Set(['b', 'a']), errorsOnly: false, statuses: new Set(['error']) }) === true,
    '③同条件（query trim等价+Set序无关）=true');
  assert(sameFilter(base, { query: 'k', types: new Set(['b', 'a']), errorsOnly: false, statuses: new Set(['ok']) }) === false,
    '③status不同=false');
  assert(sameFilter(base, { query: 'k', types: new Set(['b', 'a']), errorsOnly: true, statuses: new Set(['error']) }) === false,
    '③errorsOnly不同=false');
  assert(sameFilter(base, { query: 'other', types: new Set(['b', 'a']), errorsOnly: false, statuses: new Set(['error']) }) === false,
    '③query不同=false');

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

    // ── ④ SSR携带面（SpanTreeView initialFilter真实渲染）──
    const carried = treeFilterFromSearch({ query: 'boom', selectedTypes: new Set(['tool_call']), status: 'error' });
    const htmlCarry = renderToStaticMarkup(React.createElement(SpanTreeView, {
      roots: ROOTS,
      initialFilter: carried,
      getEventColor: () => '#3b82f6',
      getEventIcon: () => 'span',
    }));
    assert(htmlCarry.includes('value="boom"'), '④initialFilter预填树内搜索输入框（value=boom）');
    assert(htmlCarry.includes(zh.trajectory.fromSearch), '④"来自搜索"来源chip渲染（携带面可见）');
    // title属性里引号被HTML转义（&quot;）——归一后再比
    const htmlCarryNorm = htmlCarry.replace(/&quot;/g, '"').replace(/&#x27;/g, "'");
    assert(htmlCarryNorm.includes(zh.trajectory.fromSearchHint), '④来源chip tooltip（清除指引）');
    assert(htmlCarry.includes(zh.trajectory.statusError), '④status精确值chip渲染（本地化label）');
    // 命中行内容=splitMatch分段渲染（'boom'命中段+后缀段各自成span）——
    // 后缀段在HTML里连续，用后缀实锤命中行在场
    assert(htmlCarry.includes(' error tool'), '④命中行保留（内容预览后缀可见）');
    assert(!htmlCarry.includes('warned tool') && !htmlCarry.includes('fine llm'),
      '④warn/ok兄弟剪掉（statuses=error精确档——warn不冒充）');
    assert(htmlCarry.includes('1 条结果') && htmlCarry.includes('boom'), '④摘要行带命中计数+查询词');
    assert(htmlCarry.includes(zh.trajectory.statusError), '④摘要生效条件含status（携带面不静默）');

    // status=ok携带：空status节点归一ok也保留
    const htmlOk = renderToStaticMarkup(React.createElement(SpanTreeView, {
      roots: ROOTS,
      initialFilter: treeFilterFromSearch({ query: '', selectedTypes: new Set(), status: 'ok' }),
      getEventColor: () => '#3b82f6',
      getEventIcon: () => 'span',
    }));
    assert(htmlOk.includes('fine llm') && htmlOk.includes('empty status llm'),
      '④status=ok：ok+空status节点都保留（归一ok）');
    assert(!htmlOk.includes('boom error tool') && !htmlOk.includes('warned tool'),
      '④status=ok：error/warn剪掉');
    assert(htmlOk.includes(zh.trajectory.statusOk), '④status=ok chip本地化（statusOk复用键）');

    // ── ⑤ 无携带=既有形态零变化（回归钉）──
    const htmlPlain = renderToStaticMarkup(React.createElement(SpanTreeView, {
      roots: ROOTS,
      getEventColor: () => '#3b82f6',
      getEventIcon: () => 'span',
    }));
    assert(!htmlPlain.includes(zh.trajectory.fromSearch), '⑤无携带不出"来自搜索"chip（列表入口形态不变）');
    assert(htmlPlain.includes('boom error tool') && htmlPlain.includes('warned tool') && htmlPlain.includes('fine llm'),
      '⑤无携带不过滤（全树可见）');
    assert(!htmlPlain.includes(zh.trajectory.statusError),
      '⑤无携带无status精确chip（statusError仅来自携带面）');
    const htmlNullFilter = renderToStaticMarkup(React.createElement(SpanTreeView, {
      roots: ROOTS,
      initialFilter: null,
      getEventColor: () => '#3b82f6',
      getEventIcon: () => 'span',
    }));
    assert(!htmlNullFilter.includes(zh.trajectory.fromSearch), '⑤initialFilter=null同无携带');

    // SpanTreeBody statuses过滤摘要（生效条件可见）
    const htmlBody = renderToStaticMarkup(React.createElement(SpanTreeBody, {
      roots: ROOTS,
      filter: { query: '', types: new Set(), errorsOnly: false, statuses: new Set(['error']) },
      expandedIds: new Set(),
      onToggle: () => {},
      onFocus: () => {},
      getEventColor: () => '#3b82f6',
      getEventIcon: () => 'span',
    }));
    assert(htmlBody.includes(zh.trajectory.statusError) && htmlBody.includes(zh.trajectory.filtered.split('{{')[0]),
      '⑤摘要行含status生效条件（filtered复用键前缀+status本地化）');

    await i18next.changeLanguage('en');
    const htmlEn = renderToStaticMarkup(React.createElement(SpanTreeView, {
      roots: ROOTS,
      initialFilter: treeFilterFromSearch({ query: 'boom', selectedTypes: new Set(), status: 'error' }),
      getEventColor: () => '#3b82f6',
      getEventIcon: () => 'span',
    }));
    assert(!/[\u4e00-\u9fff\u3040-\u30ff]/.test(htmlEn), '④en SSR携带面全英文零CJK（含新chip文案）');
    await i18next.changeLanguage('zh');

    // ── ⑥ 两消费者接线钉（写了≠接线了）──
    assert(csSrc.includes('export function treeFilterFromSearch'), '⑥适配器定义在cross-search单一真源');
    assert(!/function treeFilterFromSearch/.test(viewSrc) && !/function treeFilterFromSearch/.test(trajSrc) &&
      !/function treeFilterFromSearch/.test(tabSrc), '⑥适配器无本地副本（防双实现漂移）');
    for (const [name, src] of [['trajectory-client', trajSrc], ['trajectory-tab', tabSrc]]) {
      assert(src.includes('initialFilter={carriedTreeFilter}'), `⑥${name}：SpanTreeView挂initialFilter`);
      assert(src.includes('selectSession(sid, searchCriteria ? treeFilterFromSearch(searchCriteria) : null)'),
        `⑥${name}：搜索组"打开会话"携带快照条件`);
      assert(src.includes('const selectSession = useCallback((id: string, carry: TreeFilter | null = null)'),
        `⑥${name}：selectSession带carry参数`);
      assert(src.includes('setCarriedTreeFilter(carry)'), `⑥${name}：carry落state`);
      assert(src.includes('setSearchCriteria({ query, selectedTypes: new Set(selectedTypes), status: searchStatus })'),
        `⑥${name}：doSearch快照搜索条件（不跟live输入走）`);
      assert(!src.includes('onOpen={selectSession}'), `⑥${name}：旧直连接线钉（onOpen不裸传selectSession）`);
    }
    assert(viewSrc.includes('filterToState(initialFilterRef.current)'), '⑥roots重置吃initialFilter（携带态恢复）');
    assert(viewSrc.includes('sameFilter(filter, initialFilter)') && viewSrc.includes('filterIsActive(filter)'),
      '⑥"来自搜索"chip判定=sameFilter+filterIsActive单一真源');
    assert(viewSrc.includes('setStatusFilter(new Set<string>())'), '⑥clearFilters清status（全部chip全清）');
    assert(viewSrc.includes('statusLabelKey(s) ?? s'), '⑥status label未映射直出机器原值（未知不猜）');
    assert(treeSrc.includes('filter.statuses.has(nodeStatus(node))'), '⑥statuses判定在nodeMatches真路径');
    assert(treeSrc.includes('function filterIsActive') && treeSrc.includes('function sameFilter'),
      '⑥filterIsActive/sameFilter定义在span-tree单一真源');
    assert(viewSrc.includes('filterIsActive(filter)') && !/filterActive = q !== ""/.test(viewSrc),
      '⑥SpanTreeBody过滤生效判定改吃filterIsActive（旧内联表达式归零）');

    // ── ⑦ i18n键三语 + en零CJK ──
    for (const [ns, k] of [['trajectory', 'fromSearch'], ['trajectory', 'fromSearchHint']]) {
      assert([zh, en, ja].every((l) => typeof l[ns]?.[k] === 'string' && l[ns][k].length > 0),
        `⑦${ns}.${k} present in zh/en/ja`);
    }
    assert(!/[\u4e00-\u9fff\u3040-\u30ff]/.test(en.trajectory.fromSearch + en.trajectory.fromSearchHint),
      '⑦en新键零CJK');
    for (const k of ['statusFilter', 'statusAll', 'statusError', 'statusOk', 'openSession']) {
      assert([zh, en, ja].every((l) => typeof l.trajectory?.[k] === 'string' && l.trajectory[k].length > 0),
        `⑦trajectory.${k} 三语在场（statusLabelKey消费键防删除钉）`);
    }

    // ── ⑧ 既有契约回归钉 ──
    const qs = buildSearchQueryString({ query: 'k', selectedTypes: new Set(), status: 'error' });
    assert(qs === 'keyword=k&status=error' && !/(?:^|&)q=/.test(qs), '⑧buildSearchQueryString契约不变（q=反模式归零）');
    assert(viewSrc.includes('import { statusLabelKey } from "./cross-search"'),
      '⑧statusLabelKey跨模块import（不在view内复制映射）');

    console.log(failed === 0 ? `\nALL PASS (${passed})` : `\n${failed} FAILED / ${passed} passed`);
    fs.rmSync(outDir, { recursive: true, force: true });
    process.exit(failed === 0 ? 0 : 1);
  }

  main().catch((e) => { console.error(e); process.exit(1); });
} catch (e) {
  console.error(e);
  fs.rmSync(outDir, { recursive: true, force: true });
  process.exit(1);
}
