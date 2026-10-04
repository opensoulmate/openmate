// 轨迹共享组件单一真源 render-test（2026-10-05，共享化去重专项轮——多轮挂账
// "两消费者近重复文件去重（SearchBar/EventCard共享化）"销账）
// 编译：tsc transpileModule（trajectory-shared.tsx + 其依赖 span-tree/cross-search/
//       cross-search-view）+ react-dom/server真实SSR + 真实i18next。
// 覆盖：①require冒烟+导出面（8值导出；EVENT_*映射不外泄）②行为真值（映射/
//       timeAgo）③五组件SSR真实渲染（EventCard高亮/展开/metadata、SearchBar、
//       SessionCard、StatsBar双态、ReplayControls）④防重复钉（两消费者本地副本
//       归零+import接线+渲染照旧）⑤i18n键三语+en零CJK ⑥tmpdir自清理纪律统一
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.shared-cards-'));
// 临时产物自清理（统一纪律：失败早退也不留tmpdir垃圾）
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

const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...args) {
  if (typeof request === 'string' && request.startsWith('@/')) {
    request = path.join(outDir, request.slice(2)) + '.js';
  }
  return origResolve.call(this, request, ...args);
};

// 依赖镜像真实相对布局（shared的 ./span-tree ./cross-search ./cross-search-view）
compile('src/app/(app)/trajectory/span-tree.ts', 'trajectory/span-tree.js');
compile('src/app/(app)/trajectory/cross-search.ts', 'trajectory/cross-search.js');
compile('src/app/(app)/trajectory/cross-search-view.tsx', 'trajectory/cross-search-view.js');
const sharedFile = compile('src/app/(app)/trajectory/trajectory-shared.tsx', 'trajectory/trajectory-shared.js');

const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const i18next = require('i18next');
const { initReactI18next } = require('react-i18next');

const zh = require('./src/locales/zh.json');
const en = require('./src/locales/en.json');
const ja = require('./src/locales/ja.json');

const sharedSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/trajectory-shared.tsx'), 'utf8');
const trajSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/trajectory-client.tsx'), 'utf8');
const tabSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/system/tabs/trajectory-tab.tsx'), 'utf8');
const viewSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/span-tree-view.tsx'), 'utf8');

let failed = 0;
let passed = 0;
function assert(cond, name) {
  if (cond) { passed++; console.log('PASS:', name); }
  else { failed++; console.log('FAIL:', name); }
}

function ev(over = {}) {
  return {
    id: 'e1', session_id: 's1', parent_event_id: null,
    event_type: 'tool_call', agent_id: 'agent-x',
    content: 'content boom here', metadata_json: '{"k": 1}',
    token_usage: 12, duration_ms: 34, status: 'error',
    created_at: '2026-10-05T00:00:00Z',
    ...over,
  };
}

async function main() {
  // ── ① require冒烟 + 导出面 ──
  let mod = null, err = null;
  try { mod = require(sharedFile); } catch (e) { err = e; }
  assert(err === null, `①require编译后trajectory-shared成功${err ? '（' + err.message + '）' : ''}`);
  for (const fn of ['StatsBar', 'SearchBar', 'SessionCard', 'EventCard', 'ReplayControls', 'getEventColor', 'getEventIcon', 'timeAgo']) {
    assert(typeof mod?.[fn] === 'function', `①导出${fn}为函数`);
  }
  assert(!('EVENT_ICONS' in (mod || {})) && !('EVENT_COLORS' in (mod || {})),
    '①EVENT_ICONS/EVENT_COLORS不外泄（唯一出口=getEventColor/getEventIcon）');

  // ── ② 行为真值（映射表语义逐字不漂移）──
  assert(mod.getEventColor('tool_call') === '#f59e0b', '②getEventColor(tool_call)=#f59e0b');
  assert(mod.getEventColor('error') === '#ef4444', '②getEventColor(error)=#ef4444');
  assert(mod.getEventColor('totally-unknown') === '#6b7280', '②未知类型回落灰（不冒充已知色）');
  assert(mod.getEventIcon('tool_call') === require('lucide-react').Wrench, '②getEventIcon(tool_call)=Wrench');
  assert(mod.getEventIcon('nope') === require('lucide-react').Activity, '②未知类型回落Activity');
  assert(mod.timeAgo(new Date().toISOString()) === 'just now', '②timeAgo(<60s)=just now');
  assert(mod.timeAgo(new Date(Date.now() - 90 * 1000).toISOString()) === '1m ago', '②timeAgo(90s)=1m ago');
  assert(mod.timeAgo(new Date(Date.now() - 3 * 3600 * 1000).toISOString()) === '3h ago', '②timeAgo(3h)=3h ago');

  await i18next.use(initReactI18next).init({
    lng: 'zh',
    resources: { zh: { translation: zh }, en: { translation: en }, ja: { translation: ja } },
    interpolation: { escapeValue: false },
  });
  const h = (el) => renderToStaticMarkup(el);

  // ── ③ SSR真实渲染 ──
  // EventCard：高亮+展开+metadata+status chip（content>300字符触发展开按钮）
  const longEv = ev({ content: 'boom ' + 'x'.repeat(320) });
  const card = h(React.createElement(mod.EventCard, { event: longEv, idx: 0, total: 4, highlightQuery: 'boom' }));
  assert(card.includes('#1/4'), '③EventCard编号#idx+1/total');
  assert(card.includes('tool_call') && card.includes('agent-x'), '③EventCard类型chip+agent_id');
  assert(card.includes('error'), '③EventCard status≠ok chip在场');
  assert(card.includes('background:hsl(var(--primary) / 0.28)'), '③EventCard命中片段高亮（MatchedText消费）');
  assert(card.includes('boom') && card.includes('x'.repeat(50)),
    '③高亮后全文可拼回（splitMatch分段不丢字）');
  assert(card.includes(zh.trajectory.expand), '③长文（>300字符）→展开按钮');
  assert(card.includes(zh.trajectory.metadata), '③metadata_json在场→metadata复制按钮');
  // EventCard：空查询=原文不高亮（与splitMatch契约一致）
  const cardPlain = h(React.createElement(mod.EventCard, { event: ev({ metadata_json: null }), idx: 2, total: 4 }));
  assert(!cardPlain.includes('background:hsl(var(--primary) / 0.28)'), '③空查询不高亮（原文逐字）');
  assert(cardPlain.includes('content boom here'), '③空查询原文完整渲染');
  assert(!cardPlain.includes(zh.trajectory.metadata), '③无metadata_json→无metadata按钮');
  // SearchBar
  const bar = h(React.createElement(mod.SearchBar, {
    query: 'kw', onQueryChange: () => {}, eventTypes: [{ value: 'tool_call', name: 'Tool' }],
    selectedTypes: new Set(['tool_call']), onTypeToggle: () => {}, onSearch: () => {},
    status: 'error', onStatusChange: () => {},
  }));
  assert(bar.includes(zh.trajectory.searchEventsPlaceholder), '③SearchBar输入框placeholder（i18n链路通）');
  assert(bar.includes(zh.trajectory.search), '③SearchBar搜索按钮');
  assert(bar.includes(zh.trajectory.filters.replace('{{count}}', '1')),
    '③SearchBar已选类型→filters(count)插值真值（原文{{count}}≠渲染文本）');
  assert(bar.includes('value="kw"'), '③SearchBar受控query');
  // SessionCard
  const sess = h(React.createElement(mod.SessionCard, {
    session: { id: 'sess-12345678', agent_id: 'a', task_description: '任务描述X', status: 'running',
      total_events: 7, total_tokens: 3000, created_at: new Date().toISOString(), ended_at: null },
    selected: true, onSelect: () => {},
  }));
  assert(sess.includes('任务描述X'), '③SessionCard任务描述');
  assert(sess.includes('running') && sess.includes(zh.trajectory.events), '③SessionCard状态chip+事件数');
  assert(sess.includes(zh.trajectory.tok), '③SessionCard token数（>0才渲染）');
  // StatsBar：loading脉冲 vs 数据卡
  const statsLoading = h(React.createElement(mod.StatsBar, { stats: null, loading: true }));
  assert((statsLoading.match(/animation:pulse/g) || []).length === 4, '③StatsBar loading=4脉冲占位');
  const stats = h(React.createElement(mod.StatsBar, {
    stats: { total_sessions: 3, running_sessions: 1, total_events: 9, total_tokens: 12345 }, loading: false,
  }));
  assert(stats.includes('3') && stats.includes('9') && stats.includes(zh.trajectory.sessions), '③StatsBar数据卡渲染真值');
  // ReplayControls
  const replay = h(React.createElement(mod.ReplayControls, {
    events: [ev(), ev({ id: 'e2' })], currentIdx: 0, onStep: () => {},
    playing: false, onTogglePlay: () => {}, speed: 1000, onSpeedChange: () => {},
  }));
  assert(replay.includes('0.5x') && replay.includes('5x'), '③ReplayControls速度档位');
  assert(replay.includes('1/2'), '③ReplayControls进度计数');
  assert(replay.includes(zh.trajectory.play), '③ReplayControls播放按钮（未播放态）');

  // ── ④ 防重复钉（写了≠接线了：定义唯一+消费照旧）──
  for (const [name, src] of [['trajectory-client', trajSrc], ['trajectory-tab', tabSrc]]) {
    for (const sym of ['function EventCard', 'function SearchBar', 'function SessionCard', 'function StatsBar',
      'function ReplayControls', 'function getEventColor', 'function getEventIcon', 'function timeAgo',
      'interface TrajectoryEvent', 'interface TrajectoryStats', 'interface TrajectorySession', 'interface EventType',
      'const EVENT_ICONS', 'const EVENT_COLORS']) {
      assert(!src.includes(sym), `④${name}：本地副本归零（${sym}）`);
    }
    assert(/import \{[\s\S]*?\} from "[^"]*trajectory-shared"/.test(src),
      `④${name}：经trajectory-shared import（防双实现漂移）`);
    for (const comp of ['<StatsBar', '<SearchBar', '<SessionCard', '<EventCard', '<ReplayControls']) {
      assert(src.includes(comp), `④${name}：${comp.slice(1)}渲染接线照旧（定义搬走消费不丢）`);
    }
    assert(src.includes('getEventColor={getEventColor}') && src.includes('getEventIcon={getEventIcon}'),
      `④${name}：SpanTreeView映射props注入照旧`);
  }
  assert(trajSrc.includes('} from "./trajectory-shared"'), '④trajectory-client相对路径./trajectory-shared');
  assert(tabSrc.includes('} from "../../trajectory/trajectory-shared"'), '④trajectory-tab跨目录路径../../trajectory/trajectory-shared');
  for (const sym of ['export function EventCard', 'export function SearchBar', 'export function SessionCard',
    'export function StatsBar', 'export function ReplayControls', 'export function getEventColor',
    'export function getEventIcon', 'export interface TrajectoryEvent', 'export interface TrajectoryStats',
    'export interface TrajectorySession', 'export interface EventType']) {
    assert(sharedSrc.includes(sym), `④单一真源：${sym} 在trajectory-shared`);
  }
  assert(sharedSrc.includes('const EVENT_ICONS') && sharedSrc.includes('const EVENT_COLORS'),
    '④映射表定义在trajectory-shared（唯一一份）');
  assert(!/const EVENT_(ICONS|COLORS)/.test(viewSrc), '④span-tree-view不复制映射表（props注入契约）');
  // 区块字节级迁移佐证：共享模块内仍是逐字同一套样式锚点
  assert(sharedSrc.includes('<MatchedText text={event.content} query={highlightQuery} />') &&
    sharedSrc.includes('<StatusFilterChips value={status} onChange={onStatusChange} />'),
    '④区块迁移=逐字搬移（MatchedText/StatusFilterChips挂载点原样）');

  // ── ⑤ i18n键三语 + en零CJK ──
  const keys = ['collapse', 'events', 'expand', 'filter', 'filters', 'metadata', 'pause', 'play',
    'running', 'search', 'searchEventsPlaceholder', 'sessions', 'tok', 'tokens'];
  const missing = [];
  for (const k of keys) {
    for (const [lang, res] of [['zh', zh], ['en', en], ['ja', ja]]) {
      if (typeof res.trajectory?.[k] !== 'string' || res.trajectory[k].length === 0) missing.push(`${k}@${lang}`);
    }
  }
  assert(missing.length === 0, `⑤共享组件消费的trajectory键三语在场${missing.length ? '缺:' + missing.join(',') : ''}`);
  assert(!/[\u4e00-\u9fff\u3040-\u30ff]/.test(keys.map(k => en.trajectory[k]).join('')), '⑤en键零CJK');

  // ── ⑥ tmpdir自清理纪律统一（上轮遗留"逐个补"本轮收口）──
  const allTests = fs.readdirSync(ROOT).filter(f => /^render-test-.*\.cjs$/.test(f));
  const dirty = allTests.filter(f => {
    const t = fs.readFileSync(path.join(ROOT, f), 'utf8');
    return t.includes('mkdtempSync') && !t.includes("process.on('exit'");
  });
  assert(dirty.length === 0, `⑥全部${allTests.length}套render-test tmpdir接exit自清理${dirty.length ? '缺:' + dirty.join(',') : ''}`);

  console.log(failed === 0 ? `\nALL PASS (${passed})` : `\n${failed} FAILED / ${passed} passed`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(1); });
