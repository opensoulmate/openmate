// 任务收发台 render-test（协作三件套·任务收发台前端页）
// 编译：tsc transpileModule（tasks-client.tsx；page-layout/api-client 打桩防重依赖图）
//       + react-dom/server 真实 SSR。
// 覆盖：①纯函数（dueText 期限/超时、bucketLabel 三桶、kindLabel、resultText）
//       ②TaskTable SSR 结构（认领/心跳/交付/催办动作、催办×N 徽标、超时徽标、
//         交付回流展示、空桶文案）③TasksClient SSR 冒烟（三桶页签+预算策略看板）
//       ④接线钉（/collab 端点面、导航入口、i18n 三语）⑤tmpdir 自清理纪律统一
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.tasks-inbox-'));
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

// 重依赖打桩（PageLayout→openface/zustand、api-client）：本 render-test 只钉收发台自身
fs.mkdirSync(path.join(outDir, 'components'), { recursive: true });
fs.writeFileSync(path.join(outDir, 'components/page-layout.js'),
  "const React=require('react');exports.PageLayout=(p)=>React.createElement('div',{'data-testid':'page-layout'},p.title,React.createElement('div',null,p.children));");
fs.writeFileSync(path.join(outDir, 'components/task-triage-gate.js'),
  "const React=require('react');exports.TaskTriagePanel=()=>React.createElement('div',{'data-testid':'triage-stub'});");
fs.writeFileSync(path.join(outDir, 'lib-api-client-stub.js'),
  "exports.getApiBaseUrl=()=>'http://127.0.0.1:8092';");
fs.mkdirSync(path.join(outDir, 'lib'), { recursive: true });
fs.renameSync(path.join(outDir, 'lib-api-client-stub.js'), path.join(outDir, 'lib/api-client.js'));

const clientFile = compile('src/app/(app)/tasks/tasks-client.tsx', 'tasks/tasks-client.js');

const React = require('react');
const { renderToString } = require('react-dom/server');
const mod = require(clientFile);

const checks = [];
function check(name, ok, detail) {
  checks.push({ name, ok, detail });
  if (!ok) {
    console.error(`✗ ${name}: ${detail}`);
    process.exitCode = 1;
  } else {
    console.log(`✓ ${name}`);
  }
}

// ── ① 纯函数行为真值 ─────────────────────────────────────────────
const now = 1_000_000;
check('dueText: 未到期=剩', mod.dueText(now + 3600, now).includes('剩'), mod.dueText(now + 3600, now));
check('dueText: 过期=超时', mod.dueText(now - 7200, now).includes('超时'), mod.dueText(now - 7200, now));
check('dueText: 零期限=—', mod.dueText(0, now) === '—', mod.dueText(0, now));
check('bucketLabel: 三桶中文', mod.bucketLabel('open') === '在办' &&
  mod.bucketLabel('delivered') === '交付' && mod.bucketLabel('overdue') === '超时',
  [mod.bucketLabel('open'), mod.bucketLabel('delivered'), mod.bucketLabel('overdue')].join('/'));
check('kindLabel: agent/人工/审批', mod.kindLabel('agent') === 'Agent 任务' &&
  mod.kindLabel('human') === '人工任务' && mod.kindLabel('approval') === '审批', 'kinds');
check('resultText: 字符串原样/对象 JSON/空=—', mod.resultText('x') === 'x' &&
  mod.resultText({ a: 1 }) === '{"a":1}' && mod.resultText(null) === '—', 'resultText');

// ── ② TaskTable SSR 结构 ─────────────────────────────────────────
const tasks = [
  {
    task_id: 'TO-1-a', kind: 'agent', status: 'working', bucket: 'open',
    title: '统计合计', instruction: '统计 3 7 11 的合计', form: '',
    role: 'engineer', assignee: 'alice', target_agent: 'soulmate',
    due_at: now + 1800, delivered_at: null, delivered_by: '', result: null,
    claimed_by: 'soulmate:soulmate', lease_expires_at: now + 600, a2a_task_id: 'a2a-9',
    reminders: 2, over_budget: true,
  },
  {
    task_id: 'TO-2-b', kind: 'human', status: 'delivered', bucket: 'delivered',
    title: '复核结论', instruction: '复核并交付', form: '验收意见',
    role: '', assignee: 'bob', target_agent: '',
    due_at: now + 3600, delivered_at: now, delivered_by: 'bob', result: '验收通过',
    claimed_by: 'bob', lease_expires_at: 0, a2a_task_id: '',
    reminders: 0, over_budget: false,
  },
];
const tableHtml = renderToString(React.createElement(mod.TaskTable, {
  tasks, now,
  onClaim: () => {}, onHeartbeat: () => {}, onDeliver: () => {}, onRemind: () => {},
  onVerify: () => {}, setVerifyId: () => {},
  verifyId: 'TO-2-b', verifyDraft: '',   // 展开验收框：钉内容级证据入口
}));
// react-dom 相邻文本节点插 <!-- --> 分隔——断言前抹平
const flat = tableHtml.replace(/<!-- -->/g, '');
check('table: 认领/心跳/交付/催办四动作', ['认领', '心跳', '交付', '催办'].every((w) => flat.includes(w)), '动作');
check('table: 催办×N 徽标', flat.includes('催办×2'), '催办徽标');
check('table: 超预算徽标', flat.includes('超预算'), '预算徽标');
check('table: 期限文本渲染', flat.includes('剩'), '期限');
check('table: 持锁与 a2a 可见', flat.includes('soulmate:soulmate') && flat.includes('a2a:a2a-9'), '锁可见');
check('table: 交付回流展示', flat.includes('验收通过') && flat.includes('交付（bob）'), '交付展示');
check('table: 角色与归属', flat.includes('@engineer') && flat.includes('alice'), '角色归属');
// ── 双轴（reported done ≠ verified）：黄=自报交付，绿=验收翻档 ──
check('table: done·unverified 黄标（自报封顶）', flat.includes('已交付·未验证 Code·reported done'), '黄标');
check('table: 未验证可验收入口', flat.includes('验收') && flat.includes('内容级验收证据'), '验收动作');
const verifiedTasks = [{ ...tasks[1], verified: true, verified_by: 'qa', verify_evidence: '抽查通过' }];
const verifiedHtml = renderToString(React.createElement(mod.TaskTable, {
  tasks: verifiedTasks, now, onVerify: () => {}, setVerifyId: () => {},
})).replace(/<!-- -->/g, '');
check('table: verified 绿标 + 验收者可见', verifiedHtml.includes('已验证 Test·verified') && verifiedHtml.includes('qa'), '绿标');
const emptyHtml = renderToString(React.createElement(mod.TaskTable, { tasks: [], now }));
check('table: 空桶文案', emptyHtml.includes('本桶暂无工单'), '空桶');

// ── ③ TasksClient SSR 冒烟 ───────────────────────────────────────
const clientHtml = renderToString(React.createElement(mod.TasksClient, {}));
check('client: 三桶页签', ['在办', '交付', '超时', '已关闭'].every((w) => clientHtml.includes(w)), '页签');
check('client: 收件人过滤入口', clientHtml.includes('收件人'), 'assignee 输入');
check('client: 预算策略看板', clientHtml.includes('预算策略'), '预算看板');

// ── ④ 接线钉 ─────────────────────────────────────────────────────
const src = fs.readFileSync(path.join(ROOT, 'src/app/(app)/tasks/tasks-client.tsx'), 'utf8');
check('pin: 收件箱清单走 /collab/tasks', src.includes('/collab/tasks?'), '列表端点');
check('pin: 认领/心跳/交付/催办端点', ['/claim', '/heartbeat', '/deliver', '/remind'].every((s) => src.includes(s)), '动作端点');
check('pin: 预算看板走 /collab/stats', src.includes('/collab/stats'), 'stats 端点');
const navSrc = fs.readFileSync(path.join(ROOT, 'src/components/organ-nav-sidebar.tsx'), 'utf8');
check('pin: 导航入口 /tasks', navSrc.includes('href: "/tasks"') && navSrc.includes('nav.tasks'), '导航');
for (const [lang, expect] of [['zh', '任务收发台'], ['en', 'Task Inbox'], ['ja', 'タスク受信箱']]) {
  const locale = JSON.parse(fs.readFileSync(path.join(ROOT, `src/locales/${lang}.json`), 'utf8'));
  check(`pin: i18n ${lang} nav.tasks`, locale.nav.tasks === expect, String(locale.nav.tasks));
}

console.log(`\n${checks.filter((c) => c.ok).length}/${checks.length} checks passed`);
