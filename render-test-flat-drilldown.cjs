// 扁平面携带过滤 render-test（2026-10-05，02:18轮遗留#2"携带过滤无处生效"销账）
// 编译：tsc transpileModule（span-tree.ts/cross-search.ts纯函数 + cross-search-view.tsx
//       react-dom/server真实SSR + 真实i18next）。
// 覆盖：①filterFlatEvents语义（恒等/剪枝/原始序号/真值计数/active不从命中率推算/
//       精确档status/四条件AND）②FlatFilterBar SSR（chips/命中计数/来源chip/
//       tooltip/"全部"清除/未生效=渲染null/未知status直出原值）③MatchedText
//       高亮（splitMatch单一真源）④两消费者接线钉（含旧形态归零anti-pin）
//       ⑤三语i18n键+en零CJK ⑥既有契约回归钉（携带链/单一真源不漂移）
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.flat-drilldown-'));
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
  const csvFile = compile('src/app/(app)/trajectory/cross-search-view.tsx', 'trajectory/cross-search-view.js');

  const { nodeMatches, filterIsActive, splitMatch } = require(treeFile);
  const { filterFlatEvents, treeFilterFromSearch } = require(csFile);
  const { FlatFilterBar, MatchedText } = require(csvFile);

  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const i18next = require('i18next');
  const { initReactI18next } = require('react-i18next');

  const zh = require('./src/locales/zh.json');
  const en = require('./src/locales/en.json');
  const ja = require('./src/locales/ja.json');

  const treeSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/span-tree.ts'), 'utf8');
  const csSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/cross-search.ts'), 'utf8');
  const csvSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/cross-search-view.tsx'), 'utf8');
  const sharedSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/trajectory-shared.tsx'), 'utf8');
  const trajSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/trajectory-client.tsx'), 'utf8');
  const tabSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/system/tabs/trajectory-tab.tsx'), 'utf8');

  let failed = 0;
  let passed = 0;
  function assert(cond, name) {
    if (cond) { passed++; console.log('PASS:', name); }
    else { failed++; console.log('FAIL:', name); }
  }

  // ── 样本扁平事件（TrajectoryEvent真实形状：id/session_id/metadata_json等字段在场）──
  function ev(id, over = {}) {
    return {
      id, session_id: 's1', parent_event_id: null,
      event_type: 'tool_call', agent_id: 'agent',
      content: `content ${id}`, metadata_json: null,
      token_usage: 10, duration_ms: 100, status: 'ok',
      created_at: '2026-10-05T00:00:00Z',
      ...over,
    };
  }
  const EVS = [
    ev('e1', { event_type: 'llm_call', content: 'fine llm' }),
    ev('e2', { status: 'error', content: 'boom error tool' }),
    ev('e3', { status: 'warn', content: 'warned tool' }),
    ev('e4', { status: '', content: 'empty status llm' }),
  ];

  // ── ① filterFlatEvents 语义 ──
  const rNull = filterFlatEvents(EVS, null);
  assert(rNull.active === false, '①filter=null→active=false');
  assert(rNull.matchCount === 4 && rNull.total === 4, '①filter=null→恒等计数（matchCount==total）');
  assert(rNull.visible.length === 4 && rNull.visible.every((v, i) => v.idx === i && v.event === EVS[i]),
    '①filter=null→全量+原始序号不重排（既有扁平形态零变化）');

  const rEmpty = filterFlatEvents(EVS, { query: '  ', types: new Set(), errorsOnly: false, statuses: new Set() });
  assert(rEmpty.active === false && rEmpty.visible.length === 4,
    '①空条件filter=恒等（filterIsActive同口径——不误剪）');

  const rQ = filterFlatEvents(EVS, { query: 'boom' });
  assert(rQ.active === true, '①query生效');
  const rQ2 = filterFlatEvents(EVS, { query: 'boom' });
  assert(JSON.stringify(rQ2.visible.map(v => v.idx)) === '[1]', '①命中剪枝+idx=原始序号（e2=idx1，不重排成0）');
  assert(rQ2.matchCount === 1 && rQ2.total === 4, '①matchCount=命中真值 / total=全量真值（不推算）');

  // active不从matchCount<total推算（全命中时仍=生效——"指标不许说谎"）
  const rAll = filterFlatEvents(EVS, { query: '', statuses: new Set(['error', 'warn', 'ok']) });
  assert(rAll.matchCount === 4 && rAll.total === 4 && rAll.active === true,
    '①全命中也active=true（生效判定=filterIsActive，不从命中率反推）');

  const rErr = filterFlatEvents(EVS, { query: '', statuses: new Set(['error']) });
  assert(JSON.stringify(rErr.visible.map(v => v.idx)) === '[1]', '①status=error精确档：只命中error（warn不冒充）');
  const rWarnOnly = filterFlatEvents(EVS, { query: '', errorsOnly: true });
  assert(JSON.stringify(rWarnOnly.visible.map(v => v.idx).sort()) === '[1,2]',
    '①errorsOnly放宽档含warn（与精确档语义差异钉死）');
  const rOk = filterFlatEvents(EVS, { query: '', statuses: new Set(['ok']) });
  assert(JSON.stringify(rOk.visible.map(v => v.idx).sort()) === '[0,3]',
    '①status=ok：ok+空status归一ok（e4 status=""按ok匹配）');

  const rAnd = filterFlatEvents(EVS, { query: 'tool', types: new Set(['tool_call']), statuses: new Set(['error']) });
  assert(JSON.stringify(rAnd.visible.map(v => v.idx)) === '[1]', '①文本×类型×status AND');
  const rAndMiss = filterFlatEvents(EVS, { query: 'tool', types: new Set(['llm_call']) });
  assert(rAndMiss.visible.length === 0 && rAndMiss.matchCount === 0 && rAndMiss.total === 4,
    '①零命中：visible=[]、matchCount=0、total不缩水（空态≠恒等）');
  assert(filterFlatEvents([], { query: 'x' }).total === 0, '①空列表total=0不炸');

  // ── ② FlatFilterBar SSR ──
  const noop = () => {};
  assert(renderToStaticMarkup(React.createElement(FlatFilterBar, {
    filter: null, fromSearch: false, matchCount: 0, total: 0, onClearAll: noop,
  })) === '', '②filter=null→渲染null（既有扁平列表形态零变化）');
  assert(renderToStaticMarkup(React.createElement(FlatFilterBar, {
    filter: { query: '', types: new Set(), errorsOnly: false, statuses: new Set() },
    fromSearch: true, matchCount: 3, total: 3, onClearAll: noop,
  })) === '', '②空条件filter→渲染null（不显示假过滤条）');

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

    const carried = treeFilterFromSearch({ query: 'boom', selectedTypes: new Set(['tool_call']), status: 'error' });
    const htmlBar = renderToStaticMarkup(React.createElement(FlatFilterBar, {
      filter: carried, fromSearch: true, matchCount: 1, total: 4, onClearAll: noop,
    }));
    assert(htmlBar.includes(zh.trajectory.fromSearch), '②"来自搜索"来源chip渲染');
    const htmlBarNorm = htmlBar.replace(/&quot;/g, '"').replace(/&#x27;/g, "'");
    assert(htmlBarNorm.includes(zh.trajectory.fromSearchHint), '②来源chip tooltip（清除指引；引号转义归一后比）');
    assert(htmlBar.includes('boom'), '②query条件chip渲染');
    assert(htmlBar.includes('tool_call'), '②type条件chip渲染');
    assert(htmlBar.includes(zh.trajectory.statusError), '②status条件chip本地化');
    // 命中计数=i18next插值真值（原文{{placeholder}}≠渲染文本——取t()渲染值比）
    const flatTxt = i18next.t('trajectory.flatMatches', { match: 1, total: 4 });
    assert(htmlBar.includes(flatTxt), `②命中计数"命中 1 / 共 4"（插值真值=${flatTxt}）`);
    assert(htmlBar.includes(zh.common.all), '②"全部"清除按钮渲染（common.all复用键）');

    const htmlNoFrom = renderToStaticMarkup(React.createElement(FlatFilterBar, {
      filter: carried, fromSearch: false, matchCount: 1, total: 4, onClearAll: noop,
    }));
    assert(!htmlNoFrom.includes(zh.trajectory.fromSearch), '②fromSearch=false不亮来源chip（不冒充"来自搜索"）');
    assert(htmlNoFrom.includes(flatTxt), '②fromSearch=false过滤条其余部分照常');

    const htmlUnknown = renderToStaticMarkup(React.createElement(FlatFilterBar, {
      filter: { query: '', types: new Set(), errorsOnly: true, statuses: new Set(['custom_x']) },
      fromSearch: false, matchCount: 0, total: 2, onClearAll: noop,
    }));
    assert(htmlUnknown.includes('custom_x'), '②未映射status直出机器原值（未知不猜）');
    assert(htmlUnknown.includes(zh.common.error), '②errorsOnly条件chip渲染');

    // ── ③ MatchedText 高亮 ──
    const htmlHit = renderToStaticMarkup(React.createElement(MatchedText, { text: 'hello boom world', query: 'boom' }));
    assert(htmlHit.includes('hsl(var(--primary) / 0.28)'), '③命中段高亮样式（splitMatch单一真源）');
    assert(htmlHit.replace(/<[^>]+>/g, '') === 'hello boom world', '③段text拼回=原文（splitMatch契约）');
    const htmlPlain = renderToStaticMarkup(React.createElement(MatchedText, { text: 'hello boom world', query: '' }));
    assert(!htmlPlain.includes('hsl(var(--primary) / 0.28)'), '③空查询=单段不高亮');
    assert(htmlPlain.replace(/<[^>]+>/g, '') === 'hello boom world', '③空查询原文逐字不变');
    const htmlCase = renderToStaticMarkup(React.createElement(MatchedText, { text: 'Hello BOOM', query: 'boom' }));
    assert(htmlCase.replace(/<[^>]+>/g, '') === 'Hello BOOM' && htmlCase.includes('hsl(var(--primary) / 0.28)'),
      '③大小写不敏感命中（保留原文大小写）');

    // ── ④ 两消费者接线钉（写了≠接线了）──
    assert(csSrc.includes('export function filterFlatEvents'), '④filterFlatEvents定义在cross-search单一真源');
    assert(csSrc.includes('nodeMatches(event, filter') && csSrc.includes('filterIsActive(filter)'),
      '④filterFlatEvents吃span-tree.nodeMatches/filterIsActive（匹配逻辑不复制）');
    assert(treeSrc.includes('export interface FilterableEvent'), '④FilterableEvent最小形状在span-tree单一真源');
    assert(!/function nodeMatches/.test(csSrc) && !/function nodeMatches/.test(csvSrc) &&
      !/function nodeMatches/.test(trajSrc) && !/function nodeMatches/.test(tabSrc),
      '④nodeMatches无本地副本（防双实现漂移）');
    assert(csvSrc.includes('export function FlatFilterBar') && csvSrc.includes('export function MatchedText'),
      '④FlatFilterBar/MatchedText定义在cross-search-view两消费者单一真源');
    assert(csvSrc.includes('splitMatch(text || "", query || "")') && csvSrc.includes('filterIsActive(filter)'),
      '④共享组件吃splitMatch/filterIsActive单一真源');

    for (const [name, src] of [['trajectory-client', trajSrc], ['trajectory-tab', tabSrc]]) {
      assert(src.includes('filterFlatEvents(displayEvents, carriedTreeFilter)'),
        `④${name}：扁平列表吃filterFlatEvents（携带过滤真路径）`);
      assert(src.includes('const flatFilterActive = flatFiltered.active'),
        `④${name}：生效判定吃filterFlatEvents.active（不从命中率推算）`);
      assert(src.includes('<FlatFilterBar') && src.includes('onClearAll={() => setCarriedTreeFilter(null)}'),
        `④${name}：过滤条挂载+"全部"清除落carriedTreeFilter`);
      assert(src.includes('flatFiltered.visible.map(({ event: ev, idx })'),
        `④${name}：渲染吃filtered.visible（原始序号idx透传）`);
      assert(src.includes('highlightQuery={flatFilterActive ? (carriedTreeFilter?.query || "") : ""}'),
        `④${name}：命中高亮只在过滤生效时开（无携带=原文）`);
      assert(/from "[^"]*trajectory-shared"/.test(src) && src.includes('<EventCard'),
        `④${name}：EventCard经trajectory-shared import（SearchBar/EventCard共享化单一真源）`);
      assert(src.includes('filterFlatEvents, groupEventsBySession'),
        `④${name}：filterFlatEvents经cross-search import（无本地副本）`);
      assert(src.includes('FlatFilterBar }'),
        `④${name}：FlatFilterBar经cross-search-view import`);
      // 旧扁平直渲形态归零（携带过滤不生效的旧接线不许残留）
      assert(!src.includes('{displayEvents.map((ev, idx) => ('),
        `④${name}：旧displayEvents直渲归零（扁平分支改吃filterFlatEvents）`);
      assert(!/function (filterFlatEvents|FlatFilterBar|MatchedText)/.test(src),
        `④${name}：三者无本地副本`);
      // 既有携带链回归钉（上轮契约逐字不变）
      assert(src.includes('initialFilter={carriedTreeFilter}'), `④${name}：树面initialFilter携带照旧`);
      assert(src.includes('selectSession(sid, searchCriteria ? treeFilterFromSearch(searchCriteria) : null)'),
        `④${name}：搜索组"打开会话"携带快照条件照旧`);
    }
    // EventCard高亮接线随共享化迁入trajectory-shared单一真源（消费面判据逐字不变）
    assert(sharedSrc.includes('highlightQuery?: string') && sharedSrc.includes('<MatchedText text={event.content} query={highlightQuery} />'),
      '④trajectory-shared：EventCard高亮接线（MatchedText消费）');
    assert(/import \{[^}]*MatchedText[^}]*\} from "\.\/cross-search-view"/.test(sharedSrc),
      '④trajectory-shared：MatchedText经cross-search-view import（无本地副本）');

    // ── ⑤ i18n键三语 + en零CJK ──
    for (const [ns, k] of [['trajectory', 'flatMatches']]) {
      assert([zh, en, ja].every((l) => typeof l[ns]?.[k] === 'string' && l[ns][k].length > 0),
        `⑤${ns}.${k} present in zh/en/ja`);
    }
    assert(!/[\u4e00-\u9fff\u3040-\u30ff]/.test(en.trajectory.flatMatches), '⑤en flatMatches零CJK');
    for (const k of ['fromSearch', 'fromSearchHint', 'statusError', 'noMatchingEvents']) {
      assert([zh, en, ja].every((l) => typeof l.trajectory?.[k] === 'string' && l.trajectory[k].length > 0),
        `⑤trajectory.${k} 三语在场（消费键防删除钉）`);
    }
    assert([zh, en, ja].every((l) => typeof l.common?.all === 'string' && l.common.all.length > 0),
      '⑤common.all 三语在场（"全部"清除按钮消费键防删除钉）');

    await i18next.changeLanguage('en');
    const htmlEn = renderToStaticMarkup(React.createElement(FlatFilterBar, {
      filter: carried, fromSearch: true, matchCount: 1, total: 4, onClearAll: noop,
    }));
    assert(!/[\u4e00-\u9fff\u3040-\u30ff]/.test(htmlEn), '⑤en FlatFilterBar全英文零CJK');
    await i18next.changeLanguage('zh');

    // ── ⑥ 既有契约回归钉（单一真源/两面一致不漂移）──
    assert(treeSrc.includes('export function nodeMatches') && treeSrc.includes('function nodeStatus(node: FilterableEvent)'),
      '⑥nodeMatches/nodeStatus签名放宽到FilterableEvent（扁平面复用的入口）');
    const sameErr = nodeMatches(EVS[1], { query: '', statuses: new Set(['error']) });
    const flatErr = filterFlatEvents(EVS, { query: '', statuses: new Set(['error']) });
    assert(sameErr === true && flatErr.visible.length === 1 && flatErr.visible[0].event === EVS[1],
      '⑥nodeMatches与filterFlatEvents同判据（两面一致，同一查询同一结果）');
    assert(splitMatch('a boom b', 'boom').length === 3, '⑥splitMatch契约不变（三段）');
    assert(filterIsActive(carried) === true && filterIsActive({ query: '', types: new Set(), errorsOnly: false, statuses: new Set() }) === false,
      '⑥filterIsActive契约不变');
    const t1 = treeFilterFromSearch({ query: ' k ', selectedTypes: new Set(['a']), status: 'error' });
    assert(t1.query === 'k' && t1.statuses.has('error') && t1.errorsOnly === false,
      '⑥treeFilterFromSearch契约不变（trim/status精确值/errorsOnly恒false）');

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
