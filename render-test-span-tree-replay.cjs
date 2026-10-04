// 回放×树协同真实测试（2026-10-04，"回放与树互斥"遗留销账）
// 编译：tsc transpileModule（jsx: react-jsx, module: commonjs）→ require纯函数真实调用
//       + react-dom/server真实SSR渲染 + 真实i18next
// 覆盖：①clipToIds/countSpans纯函数（渐进揭示/祖先上下文/不改原树/空集）
//       ②Σrollup裁剪态重算（subtree_*是全树汇总=裁后假数守卫）
//       ③SpanTreeView SSR：revealedIds渐进揭示+摘要行+回放×过滤组合
//       ④i18n replayRevealed三语言在场+en零CJK
//       ⑤接线钉（写了≠接线了：trajectory-client+system tab双消费者×纯函数单一真源）
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
  clipToIds, countSpans, filterTree, filteredRollup, subtreeRollup,
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
const tabSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/system/tabs/trajectory-tab.tsx'), 'utf8');
const viewSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/span-tree-view.tsx'), 'utf8');

let failed = 0;
function assert(cond, name) {
  if (cond) { console.log('PASS:', name); }
  else { failed++; console.log('FAIL:', name); }
}

// ── 样本树（与get_trace_tree产物同形）：7 span ──
function node(id, event_type, over = {}) {
  return {
    id, event_type, agent_id: '', content: `${event_type} ${id}`,
    status: 'ok', token_usage: 10, duration_ms: 100, created_at: '2026-10-04T00:00:00Z',
    parent_event_id: null, children: [], depth: 0,
    subtree_tokens: 10, subtree_duration_ms: 100, subtree_events: 1,
    ...over,
  };
}
function sampleTree() {
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
  const root2 = node('r2', 'user_input', { content: 'user_input r2' });
  return [root1, root2];
}

function idsOf(roots) {
  const out = [];
  const walk = (ns) => { for (const n of ns || []) { out.push(n.id); walk(n.children); } };
  walk(roots);
  return out;
}

function renderTree(props) {
  return renderToStaticMarkup(React.createElement(SpanTreeView, {
    roots: sampleTree(),
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

  // ── ① clipToIds/countSpans 纯函数 ──
  const roots = sampleTree();
  assert(countSpans(roots) === 7, '①countSpans=全树span数（7）');
  assert(countSpans([]) === 0, '①countSpans空树=0');

  const clipped = clipToIds(roots, new Set(['r1', 'la', 'tx']));
  assert(idsOf(clipped).join(',') === 'r1,la,tx',
    '①clipToIds渐进揭示=只留已揭示（未来span不出现）');
  assert(countSpans(clipped) === 3, '①裁后countSpans=已揭示数');

  const ctx = clipToIds(roots, new Set(['sub']));
  assert(idsOf(ctx).join(',') === 'r1,la,tx,sub',
    '①祖先上下文保留（只揭示深层节点时父链作上下文）');

  assert(idsOf(clipToIds(roots, new Set())).join(',') === '',
    '①空id集=空树（回放起点=什么都没发生）');
  assert(idsOf(clipToIds(roots, new Set(['ghost']))).join(',') === '',
    '①未知id不误留（无命中无子孙=剪掉）');

  // 不改原树（React state友好）
  assert(idsOf(roots).join(',') === 'r1,la,tx,sub,ty,lb,r2',
    '①clipToIds不改原树（原树7节点原样）');
  const txOrig = roots[0].children[0].children[0];
  assert(Array.isArray(txOrig.children) && txOrig.children.length === 1 && txOrig.children[0].id === 'sub',
    '①裁剪只动新树children（原节点children数组引用不被改写）');

  // ── ② Σrollup裁剪态重算（subtree_*裁后=假数守卫）──
  const laClipped = clipped[0].children[0];
  const recomputed = filteredRollup(laClipped);
  assert(recomputed.events === 2 && recomputed.tokens === 20,
    '②filteredRollup对裁后子树重算（la+tx=2事件/20token）');
  assert(subtreeRollup(laClipped).events === 4 && subtreeRollup(laClipped).tokens === 222,
    '②subtree_*仍是全树汇总（裁后不可直接用=重算依据）');

  // ── ③ SSR真实渲染 ──
  // 回放中（揭示r1/la/tx）：未来span不渲染+摘要行计数
  const htmlReplay = renderTree({ revealedIds: new Set(['r1', 'la', 'tx']) });
  assert(htmlReplay.includes('user_input r1 hello') && htmlReplay.includes('tool_call tx'),
    '③SSR回放：已揭示span渲染');
  assert(!htmlReplay.includes('span sub') && !htmlReplay.includes('tool_call ty') &&
    !htmlReplay.includes('user_input r2'),
    '③SSR回放：未来span（sub/ty/r2）不渲染=渐进揭示');
  assert(htmlReplay.includes('回放中：已揭示 3/7 个span'),
    '③SSR回放：摘要行"已揭示 3/7"（裁后重算/全树分母）');

  // 无revealedIds=全树+无摘要行（缺省行为逐字不变；默认展开深度<2，
  // 深层sub在折叠子树内不直出=既有缺省形态，不算渐进裁剪）
  const htmlFull = renderTree({});
  assert(htmlFull.includes('user_input r2') && htmlFull.includes('tool_call ty') && htmlFull.includes('tool_call tx'),
    '③SSR缺省revealedIds=全树渲染（默认展开层全在）');
  assert(!htmlFull.includes('回放中：已揭示'),
    '③SSR缺省无回放摘要行');

  // null显式=全揭示（调用方回放关=立即回全树）
  const htmlNull = renderTree({ revealedIds: null });
  assert(htmlNull.includes('user_input r2') && !htmlNull.includes('回放中：已揭示'),
    '③revealedIds=null=全揭示（回放退出即回全树）');

  // 回放×过滤组合（clip先行、filter后行）：揭示r1/la/tx/sub + query=sub
  const htmlCombo = renderTree({ revealedIds: new Set(['r1', 'la', 'tx', 'sub']), });
  assert(htmlCombo.includes('span sub') && !htmlCombo.includes('tool_call ty'),
    '③回放×树组合：揭示集含sub则sub在、ty不在');
  const comboFiltered = filterTree(clipToIds(sampleTree(), new Set(['r1', 'la', 'tx', 'sub'])), { query: 'sub' });
  assert(idsOf(comboFiltered).join(',') === 'r1,la,tx,sub',
    '③clip→filter链式：过滤在裁剪后的树上作用（祖先上下文双守恒）');

  // ── ④ i18n ──
  for (const [lang, res] of [['zh', zh], ['en', en], ['ja', ja]]) {
    assert(typeof res.trajectory?.replayRevealed === 'string' && res.trajectory.replayRevealed.length > 0,
      `④trajectory.replayRevealed present@${lang}`);
  }
  assert(zh.trajectory.replayRevealed.includes('{{shown}}') && zh.trajectory.replayRevealed.includes('{{total}}'),
    '④zh replayRevealed插值占位在场');
  assert(!/[\u4e00-\u9fff]/.test(en.trajectory.replayRevealed), '④en replayRevealed零CJK');

  // ── ⑤ 接线钉（写了≠接线了）──
  assert(viewSrc.includes('clipToIds(') && viewSrc.includes('countSpans('),
    '⑤span-tree-view消费clipToIds/countSpans（非复制实现）');
  assert(viewSrc.includes('revealedIds = null') && viewSrc.includes('revealedIds?: Set<string> | null'),
    '⑤revealedIds prop贯穿SpanTreeView→SpanTreeBody');
  assert(viewSrc.includes('t("trajectory.replayRevealed"'),
    '⑤摘要行消费i18n key（非硬编码裸文本）');
  assert(!viewSrc.includes('subtreeRollup(node) : filteredRollup'),
    '⑤rollup分支方向不倒装（裁后重算优先）');
  assert(trajSrc.includes('revealedIds={revealedSpanIds}') &&
    trajSrc.includes('const revealedSpanIds = replayMode') &&
    !trajSrc.includes('treeMode && !replayMode'),
    '⑤trajectory-client：回放id集→prop，互斥已解除');
  assert(tabSrc.includes('revealedIds={revealedSpanIds}') &&
    tabSrc.includes('const revealedSpanIds = replayMode') &&
    !tabSrc.includes('treeMode && !replayMode'),
    '⑤system tab第二消费者同款接线（双消费者不漂移）');
  assert(trajSrc.includes('events.slice(0, replayIdx + 1).map(ev => String(ev.id))') &&
    tabSrc.includes('events.slice(0, replayIdx + 1).map(ev => String(ev.id))'),
    '⑤已揭示id集=events[0..replayIdx]（与扁平回放同一进度源）');
  assert(/onClick=\{\(\) => \{ setTreeMode\(!treeMode\) \}\}/.test(trajSrc) &&
    /onClick=\{\(\) => \{ setTreeMode\(!treeMode\) \}\}/.test(tabSrc) &&
    !/setReplayMode\(false\); setReplayPlaying\(false\); setTreeMode/.test(trajSrc + tabSrc),
    '⑤切树按钮不再杀回放（双侧onClick纯切换）');

  console.log(failed === 0 ? '\nALL PASS' : `\n${failed} FAILED`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch(e => { console.error(e); process.exit(1); });
