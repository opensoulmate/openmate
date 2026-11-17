// UI DSL 渲染器 render-test（插件系统第一路 PoC 验收）
// 编译：tsc transpileModule（plugin-ui-dsl.ts 纯函数 + panel-renderer.tsx react-dom/server 真实 SSR）
// 覆盖：①normalizeDslPage 归一化/前向兼容 ②form/list/report 三面板宿主组件渲染（设计令牌皮肤）
//       ③结果注入（target_panel 绑定）④demo 插件真实 ui/home.json 渲染
//       ⑤接线钉（[plugin]/page.tsx DSL 兜底 + plugin-nav 导航合并 → 菜单长出来）
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.plugin-dsl-'));
process.on('exit', () => { try { fs.rmSync(outDir, { recursive: true, force: true }); } catch {} });

let failed = 0;
let passed = 0;
function assert(cond, name) {
  if (cond) { passed++; console.log('PASS:', name); }
  else { failed++; console.log('FAIL:', name); }
}

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

// ── 编译被测模块 + 依赖（@/ 别名指向 outDir）──
compile('src/lib/utils.ts', 'lib/utils.js');
compile('src/lib/plugin-ui-dsl.ts', 'lib/plugin-ui-dsl.js');
compile('src/lib/plugin-nav.ts', 'lib/plugin-nav.js');
compile('src/components/ui/button.tsx', 'components/ui/button.js');
compile('src/components/ui/input.tsx', 'components/ui/input.js');
const rendererFile = compile('src/components/plugin-ui/panel-renderer.tsx', 'components/plugin-ui/panel-renderer.js');

const { normalizeDslPage } = require(path.join(outDir, 'lib/plugin-ui-dsl.js'));
const { mergePluginNavItems } = require(path.join(outDir, 'lib/plugin-nav.js'));
const { PanelRenderer } = require(rendererFile);

const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const demoDsl = JSON.parse(fs.readFileSync(path.join(ROOT, 'plugins/demo-greeter/ui/home.json'), 'utf8'));

// ── ① normalizeDslPage 归一化/前向兼容 ──
{
  const page = normalizeDslPage(demoDsl);
  assert(page !== null && page.panels.length === 3, 'demo DSL 归一化出 3 面板');
  assert(page.panels.map((p) => p.type).join(',') === 'form,report,list', '面板类型序列 form,report,list');
  const form = page.panels[0];
  assert(form.submit.tool === 'demo-greeter__greet' && form.submit.target_panel === 'greet-result', '表单提交绑定工具+目标面板');

  assert(normalizeDslPage({ version: 2, panels: [] }) === null, 'version≠1 返回 null');
  assert(normalizeDslPage(null) === null, '非对象返回 null');
  const filtered = normalizeDslPage({
    version: 1,
    page: { title: 'P' },
    panels: [
      { type: 'carousel', id: 'x' },
      { type: 'report', id: 'r1', title: 'R', sections: [{ kind: 'text', content: 'hi' }] },
    ],
  });
  assert(filtered !== null && filtered.panels.length === 1 && filtered.panels[0].id === 'r1', '未知面板类型丢弃不整页失败');
  const fields = normalizeDslPage({
    version: 1, page: { title: 'P' },
    panels: [{ type: 'form', id: 'f', title: 'F', fields: [{ name: 'a', label: 'A' }, { widget: 'text' }], submit: { tool: 'p__t' } }],
  }).panels[0].fields;
  assert(fields.length === 1 && fields[0].widget === 'text' && fields[0].label === 'A', '非法字段丢弃+label 兜底 name');

  // auto_refresh_seconds（实时运行面板）：≥2 生效，非法/缺省归 0
  const refresh = normalizeDslPage({
    version: 1, page: { title: 'P' },
    panels: [
      { type: 'list', id: 'l1', title: 'L', columns: [{ key: 'a', label: 'A' }], auto_refresh_seconds: 10 },
      { type: 'list', id: 'l2', title: 'L', columns: [{ key: 'a', label: 'A' }], auto_refresh_seconds: 1 },
    ],
  }).panels;
  assert(refresh[0].auto_refresh_seconds === 10, 'list 面板 auto_refresh_seconds=10 保留');
  assert(refresh[1].auto_refresh_seconds === 0, 'auto_refresh_seconds<2 归 0（不轮询）');

  // flow-console 宿主运行面板真实 DSL 可归一化（list/report 混排 + 实时列表）
  const consoleDsl = JSON.parse(fs.readFileSync(path.join(ROOT, 'plugins/flow-console/ui/home.json'), 'utf8'));
  const consolePage = normalizeDslPage(consoleDsl);
  assert(consolePage !== null && consolePage.panels.length === 8, 'flow-console 归一化出 8 面板');
  assert(consolePage.panels.filter((p) => p.type === 'list').length === 2
    && consolePage.panels.filter((p) => p.type === 'report').length === 2
    && consolePage.panels.filter((p) => p.type === 'form').length === 4,
  'flow-console 面板构成 4 form + 2 list + 2 report');
}

// ── ②③④ 三面板真实渲染（demo ui/home.json）──
{
  const calls = [];
  const toolCall = async (tool, args) => {
    calls.push({ tool, args });
    if (tool === 'demo-greeter__greet_log') return { items: [], count: 0 };
    return { greeting: `${args.name}，您好。`, name: args.name, style: args.style };
  };
  const page = normalizeDslPage(demoDsl);
  const html = renderToStaticMarkup(
    React.createElement(PanelRenderer, {
      page,
      toolCall,
      initialData: {
        'greet-log': { items: [{ time: '2026-11-17 09:00:00', name: '小明', style: 'casual', greeting: '嗨，小明！今天也要加油鸭～' }] },
      },
    })
  );

  // form：字段/必填标记/select 选项/提交按钮（宿主组件 Input/Button 皮肤类在场）
  assert(html.includes('问候对象') && html.includes('placeholder="输入姓名"'), 'form 文本字段+placeholder 渲染');
  assert(html.includes('*'), 'form 必填标记渲染');
  assert(html.includes('<option value="formal"') && html.includes('>正式</option>'), 'form select 选项渲染');
  assert(html.includes('<option value="casual"') && html.includes('>随意</option>'), 'form select 全部选项渲染');
  assert(html.includes('>生成</button>'), 'form 提交按钮渲染');
  assert(html.includes('data-slot="input"'), '宿主 Input 组件（设计令牌皮肤）在场');

  // list：initialData 行渲染（SSR 不跑 useEffect，走注入路径）
  assert(html.includes('嗨，小明！今天也要加油鸭～'), 'list 行内容渲染');
  assert(html.includes('<th'), 'list 表头渲染');

  // report：声明的初始 kv 渲染
  assert(html.includes('最新问候') && html.includes('尚未生成'), 'report kv 面板渲染');
}

// ── ③ 结果注入（target_panel 绑定，report 以结果对象出 kv）──
{
  const page = normalizeDslPage(demoDsl);
  const html = renderToStaticMarkup(
    React.createElement(PanelRenderer, {
      page,
      toolCall: async () => ({}),
      initialData: { 'greet-result': { greeting: '验收，您好。', name: '验收' } },
    })
  );
  assert(html.includes('验收，您好。') && html.includes('greeting'), '结果注入目标 report 面板（kv 展示）');
}

// ── ⑤ 接线钉：DSL 兜底 + 导航合并（菜单长出来）──
{
  const pageSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/plugins/[plugin]/page.tsx'), 'utf8');
  assert(pageSrc.includes('import { PluginDslView } from "@/components/plugin-ui/panel-renderer"'), '接线钉：[plugin]/page.tsx 导入 PluginDslView');
  assert(pageSrc.includes('<PluginDslView pluginId={pluginName} />'), '接线钉：未映射插件走 DSL 渲染兜底');
  assert(pageSrc.includes('PLUGIN_PAGES'), '接线钉：既有静态映射保留（增量改码）');

  const base = [
    { href: '/chat', label: '对话', icon: {} },
    { href: '/workflow', label: '流程', icon: {} },
  ];
  const demoNav = { href: '/plugins/demo-greeter', label: '问候演示', icon: 'Sparkles', pluginId: 'demo-greeter', position: 'end' };
  const merged = mergePluginNavItems(base, [demoNav]);
  assert(merged.length === 3 && merged[2].href === '/plugins/demo-greeter', '导航合并：position=end 追加到末尾');
  const afterNav = { ...demoNav, position: 'after:chat' };
  const merged2 = mergePluginNavItems(base, [afterNav]);
  assert(merged2[1].href === '/plugins/demo-greeter' && merged2[0].href === '/chat', '导航合并：after:<href> 插入目标后');
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
