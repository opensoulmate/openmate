// 跨会话搜索render-test（P0 q/keyword哑火修复 + "哪个session有error的tool_call"分组契约钉）
// 编译：tsc transpileModule（纯函数文件cross-search.ts，span-tree.ts/skill-list-url.ts同例）。
// 覆盖：①buildSearchQueryString参数名钉（keyword canonical、q反模式归零）
//       ②event_type单选/多选不带/status过滤/trim ③groupEventsBySession分组计数+组序
//       ④statusLabelKey映射+未映射null（"未知不猜"）⑤两消费者接线钉（trajectory-client
//       与system/tabs/trajectory-tab镜像双侧）⑥三语i18n键齐全+en零CJK
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.cross-search-'));
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

  // cross-search.js运行时value-import './span-tree'（filterFlatEvents吃
  // nodeMatches单一真源）——span-tree.js须一并编译进outDir
  compile('src/app/(app)/trajectory/span-tree.ts', 'trajectory/span-tree.js');
  const csFile = compile('src/app/(app)/trajectory/cross-search.ts', 'trajectory/cross-search.js');
  const {
    buildSearchQueryString,
    groupEventsBySession,
    statusLabelKey,
    SEARCH_STATUS_OPTIONS,
  } = require(csFile);

  let failed = 0;
  let passed = 0;
  function assert(cond, name) {
    if (cond) { passed++; console.log('PASS:', name); }
    else { failed++; console.log('FAIL:', name); }
  }

  // ── ① buildSearchQueryString：keyword canonical（P0回归钉） ──────────
  const qs1 = buildSearchQueryString({ query: 'hello world', selectedTypes: new Set(), status: '' });
  assert(qs1 === 'keyword=hello+world', `文本搜索走keyword参数（实际:${qs1}）`);
  assert(!/(?:^|&)q=/.test(qs1), 'q=反模式归零（曾致文本搜索哑火）');

  // ── ② trim/空查询/类型/状态 ─────────────────────────────────────────
  const qs2 = buildSearchQueryString({ query: '   ', selectedTypes: new Set(), status: '' });
  assert(qs2 === '', '空白查询=空串（不发keyword）');
  const qs3 = buildSearchQueryString({ query: ' k ', selectedTypes: new Set(['tool_call']), status: 'error' });
  assert(qs3 === 'keyword=k&event_type=tool_call&status=error', `trim+单类型+status（实际:${qs3}）`);
  const qs4 = buildSearchQueryString({ query: 'k', selectedTypes: new Set(['a', 'b']), status: '' });
  assert(qs4 === 'keyword=k', '多类型不带event_type（保持既有单选语义）');
  const qs5 = buildSearchQueryString({ query: '', selectedTypes: new Set(), status: 'error' });
  assert(qs5 === 'status=error', '纯status过滤（无文本也发搜索——"仅错误"入口）');
  const qs6 = buildSearchQueryString({ query: 'k', selectedTypes: ['tool_call'], status: '' });
  assert(qs6 === 'keyword=k&event_type=tool_call', 'selectedTypes数组形态同效（Set|array兼容）');

  // ── ③ groupEventsBySession ─────────────────────────────────────────
  const ev = (sid, at) => ({ id: `${sid}-${at}`, session_id: sid, created_at: at });
  const groups = groupEventsBySession([
    ev('s1', '2026-10-04T10:00:00'),
    ev('s2', '2026-10-04T11:00:00'),
    ev('s1', '2026-10-04T12:00:00'),
    ev('s3', '2026-10-04T09:00:00'),
  ]);
  assert(groups.length === 3, '按session分3组');
  assert(groups[0].session_id === 's1' && groups[1].session_id === 's2' && groups[2].session_id === 's3',
    '组序=最近命中降序（s1最新12:00居首）');
  const g1 = groups.find(g => g.session_id === 's1');
  assert(g1.visible_count === 2 && g1.events.length === 2, '组内计数=可见条数（不推算total）');
  assert(g1.latest_at === '2026-10-04T12:00:00', 'latest_at=组内最大created_at');
  assert(g1.events[0].created_at === '2026-10-04T10:00:00', '事件保持原序（不重排）');
  assert(groupEventsBySession([]).length === 0, '空命中=空组');

  // ── ④ statusLabelKey："未知不猜" ───────────────────────────────────
  assert(statusLabelKey('') === 'trajectory.statusAll', '空→statusAll');
  assert(statusLabelKey('error') === 'trajectory.statusError', 'error→statusError');
  assert(statusLabelKey('ok') === 'trajectory.statusOk', 'ok→statusOk');
  assert(statusLabelKey('timeout') === null, '未映射状态→null（不冒充已知档）');
  assert(SEARCH_STATUS_OPTIONS.join(',') === ',error,ok', '状态选项=全部/仅错误/仅成功');

  // ── ⑤ 两消费者接线钉（镜像双侧同步） ───────────────────────────────
  for (const [label, file] of [
    ['trajectory-client', 'src/app/(app)/trajectory/trajectory-client.tsx'],
    ['trajectory-tab', 'src/app/(app)/system/tabs/trajectory-tab.tsx'],
  ]) {
    const src = fs.readFileSync(path.join(ROOT, file), 'utf8');
    assert(/from "[^"]*cross-search"/.test(src), `${label}: import cross-search`);
    assert(/from "[^"]*cross-search-view"/.test(src), `${label}: import cross-search-view`);
    assert(src.includes('buildSearchQueryString({ query, selectedTypes, status: searchStatus })'),
      `${label}: doSearch吃buildSearchQueryString`);
    assert(src.includes('groupEventsBySession(searchResults)'), `${label}: 分组吃groupEventsBySession`);
    assert(src.includes('/api/trajectory/search/sessions?'), `${label}: fetch分组端点`);
    assert(src.includes('<StatusFilterChips value={status} onChange={onStatusChange} />'),
      `${label}: SearchBar挂StatusFilterChips`);
    assert(src.includes('<SessionGroupHeader'), `${label}: 搜索结果挂SessionGroupHeader`);
    assert(src.includes('onOpen={sid =>') &&
      src.includes('selectSession(sid, searchCriteria ? treeFilterFromSearch(searchCriteria) : null)'),
      `${label}: 组头打开会话接selectSession并携带搜索条件（搜索→树钻取整合）`);
    assert(!src.includes('onOpen={selectSession}'), `${label}: 旧裸selectSession直连归零（携带面接线钉）`);
    assert(!src.includes('params.set("q"'), `${label}: q=反模式归零（回归钉）`);
  }

  // cross-search-view组件面
  const viewSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/trajectory/cross-search-view.tsx'), 'utf8');
  assert(viewSrc.includes('export function StatusFilterChips'), 'cross-search-view: StatusFilterChips导出');
  assert(viewSrc.includes('export function SessionGroupHeader'), 'cross-search-view: SessionGroupHeader导出');
  assert(viewSrc.includes('meta ? meta.match_count : visibleCount'), '组头计数=服务端真值优先（不推算）');
  assert(viewSrc.includes('t("trajectory.openSession")'), '组头打开会话按钮接i18n');
  assert(viewSrc.includes('sample.sample_truncated'), '样本截断标记被展示层消费');

  // ── ⑥ 三语i18n键齐全 + en零CJK ────────────────────────────────────
  const NEED = ['statusFilter', 'statusAll', 'statusError', 'statusOk', 'sessionMatches', 'matchSessions', 'openSession'];
  const cjk = /[\u4e00-\u9fff\u3040-\u30ff]/;
  for (const lang of ['zh', 'en', 'ja']) {
    const loc = JSON.parse(fs.readFileSync(path.join(ROOT, `src/locales/${lang}.json`), 'utf8'));
    const traj = loc.trajectory || {};
    for (const k of NEED) {
      assert(typeof traj[k] === 'string' && traj[k].length > 0, `${lang}: trajectory.${k}存在`);
    }
    if (lang === 'en') {
      assert(NEED.every(k => !cjk.test(traj[k])), 'en新增键零CJK');
    }
    if (lang === 'zh') {
      assert(traj.statusError === '仅错误' && traj.openSession === '打开会话', 'zh真本地化（非英文直出）');
    }
    if (lang === 'ja') {
      assert(traj.statusError === 'エラーのみ' && traj.openSession === 'セッションを開く', 'ja真本地化');
    }
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
} finally {
  fs.rmSync(outDir, { recursive: true, force: true });
}
