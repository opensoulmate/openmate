// 动态插件页宿主 render-test（Sprint C step ④：清单驱动页面级路由宿主 + 导航长出）
// 编译：tsc transpileModule + react-dom/server 真实 SSR。
// 覆盖：①derivePluginNavItems（装插件→逐页长出 DSL/HTML 页、卸载→收走、legacy 回落）
//       ②selectActivePage/isHtmlPage 纯函数 ③PluginDslView SSR 结构
//       ④接线钉（清单驱动 fetch /manifest、html 分支走 HtmlPanelFrame、
//       htmlRpc 走 /html-rpc 同一审批闸、页级路由 [plugin]/[page] 存在）
//       ⑤tmpdir 自清理纪律统一
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.plugin-page-host-'));
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

compile('src/lib/utils.ts', 'lib/utils.js');
compile('src/lib/plugin-ui-dsl.ts', 'lib/plugin-ui-dsl.js');
compile('src/components/ui/button.tsx', 'components/ui/button.js');
compile('src/components/ui/input.tsx', 'components/ui/input.js');
compile('src/components/plugin-ui/html-panel-frame.tsx', 'components/plugin-ui/html-panel-frame.js');
const navFile = compile('src/lib/plugin-nav.ts', 'lib/plugin-nav.js');
const rendererFile = compile('src/components/plugin-ui/panel-renderer.tsx', 'components/plugin-ui/panel-renderer.js');

const React = require('react');
const { renderToString } = require('react-dom/server');
const navMod = require(navFile);
const rendererMod = require(rendererFile);

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

// ── ① derivePluginNavItems：装即长、卸即收 ──────────────────────
const derive = navMod.derivePluginNavItems;
const yamlPlugin = {
  status: 'enabled',
  manifest: {
    id: 'html-panel-demo',
    frontend: {
      nav: { label: 'HTML面板样板', icon: 'Puzzle', href: '/plugins/html-panel-demo', position: 'end' },
      pages: [
        { id: 'panel', title: '面板', html: 'html/panel.html' },
        { id: 'home', title: '工作台', dsl: 'ui/home.json' },
      ],
    },
  },
};

const items = derive(yamlPlugin);
check('nav: 装插件→逐页长出（DSL+HTML 页同形状）', items.length === 2 &&
  items[0].href === '/plugins/html-panel-demo/panel' && items[0].label === '面板' &&
  items[1].href === '/plugins/html-panel-demo/home' && items[1].label === '工作台',
  JSON.stringify(items));
check('nav: icon/pluginId/position 继承插件 nav 配置', items.every(i =>
  i.icon === 'Puzzle' && i.pluginId === 'html-panel-demo' && i.position === 'end'), '继承项');

check('nav: 卸载即收走（不在清单→[]）', derive({ status: 'missing' }).length === 0 &&
  derive({}).length === 0, '未装/缺状态');
check('nav: 未启用→[]', derive({ status: 'disabled', manifest: { id: 'x', frontend: { nav: { label: 'x' } } } }).length === 0, 'disabled');

const legacy = { status: 'loaded', manifest: { id: 'legacy-p', frontend: { nav: { label: '旧插件', icon: 'Star', position: 'end' } } } };
const legacyItems = derive(legacy);
check('nav: 无页清单回落插件级项（legacy 契约不变）', legacyItems.length === 1 &&
  legacyItems[0].href === '/plugins/legacy-p' && legacyItems[0].label === '旧插件', JSON.stringify(legacyItems));

const noNav = { status: 'loaded', manifest: { id: 'no-nav', frontend: {} } };
check('nav: 无 nav 无页→[]（icon/position 兜底不误长）', derive(noNav).length === 0, '无 nav');
const pagesNoNav = { status: 'loaded', manifest: { id: 'p-only', frontend: { pages: [{ id: 'home', title: 'Home', dsl: 'a.json' }] } } };
const pOnly = derive(pagesNoNav);
check('nav: 有页无 nav→页项长出（默认 Puzzle/end）', pOnly.length === 1 &&
  pOnly[0].icon === 'Puzzle' && pOnly[0].position === 'end', JSON.stringify(pOnly));

// ── ② selectActivePage / isHtmlPage ─────────────────────────────
const { selectActivePage, isHtmlPage } = rendererMod;
check('select: 命中目标页', selectActivePage(yamlPlugin.manifest.frontend.pages, 'home').id === 'home', '命中');
check('select: 未命中回落首页', selectActivePage(yamlPlugin.manifest.frontend.pages, 'ghost').id === 'panel', '回落');
check('select: 空清单→null', selectActivePage([], 'home') === null, '空');
check('kind: html 页判定', isHtmlPage({ id: 'a', title: 'A', html: 'h/a.html' }) === true, 'html');
check('kind: dsl 优先于 html（声明式优先）', isHtmlPage({ id: 'a', title: 'A', dsl: 'x.json', html: 'h/a.html' }) === false, 'dsl 优先');
check('kind: 双无→非 html（走 missing 语义）', isHtmlPage({ id: 'a', title: 'A' }) === false, '双无');

// ── ③ SSR 结构（清单加载中态 + 页签/iframe 走客户态）────────────
const html = renderToString(React.createElement(rendererMod.PluginDslView, {
  pluginId: 'html-panel-demo',
}));
check('ssr: 清单加载中态', html.includes('加载插件页面'), 'loading');

// ── ④ 接线钉 ────────────────────────────────────────────────────
const src = fs.readFileSync(path.join(ROOT, 'src/components/plugin-ui/panel-renderer.tsx'), 'utf8');
check('pin: 清单驱动 fetch /manifest', src.includes('/plugins/${pluginId}/manifest'), 'manifest fetch');
check('pin: html 分支走 HtmlPanelFrame', src.includes('<HtmlPanelFrame') && src.includes('htmlPage.html as string'), 'html 分支');
check('pin: htmlRpc 走 /html-rpc', src.includes('/plugins/${pluginId}/html-rpc'), 'html-rpc');
check('pin: htmlRpc 审批闸确认后带 approved 重发', src.includes('approval_required') && src.includes('res = await call(true)'), '审批闸');
check('pin: legacy 回落（dslId = activePage?.id ?? pageId）', src.includes('activePage?.id ?? pageId'), 'legacy 回落');

const subRouteSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/plugins/[plugin]/[page]/page.tsx'), 'utf8');
check('pin: 页级路由 [plugin]/[page] 接 PluginDslView', subRouteSrc.includes('<PluginDslView pluginId={pluginName} pageId={pageId} />'), '页级路由');

const mainRouteSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/plugins/[plugin]/page.tsx'), 'utf8');
check('pin: [plugin] 主路由静态映射+DSL 兜底保留（增量改码）',
  mainRouteSrc.includes('PLUGIN_PAGES') && mainRouteSrc.includes('<PluginDslView pluginId={pluginName} />'), '主路由');

console.log(`\n${checks.filter(c => c.ok).length}/${checks.length} checks passed`);
