// 层② HTML 面板 iframe 壳 render-test（Sprint C step ②）
// 编译：tsc transpileModule（html-panel-frame.tsx + 依赖 lib/utils）+ react-dom/server 真实 SSR。
// 覆盖：①parsePanelRpc 纯函数（形状纪律：只认 {id, method, params?}，防冒充/畸形消息不进桥）
//       ②buildThemeMessage 令牌注入形状 ③SSR 结构（sandbox="allow-scripts" 只给脚本
//       + src 走 html/ 静态服务 + data-slot）④接线钉（消息源校验/回帖形状/theme onLoad）
//       ⑤tmpdir 自清理纪律统一
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.html-panel-'));
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
const frameFile = compile('src/components/plugin-ui/html-panel-frame.tsx', 'plugin-ui/html-panel-frame.js');

const React = require('react');
const { renderToString } = require('react-dom/server');
const mod = require(frameFile);

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

// ── ① parsePanelRpc 形状纪律 ─────────────────────────────────────
const parse = mod.parsePanelRpc;
check('rpc: 合法请求解析', JSON.stringify(parse({ id: 1, method: 'add', params: { a: 1 } })) ===
  JSON.stringify({ id: 1, method: 'add', params: { a: 1 } }), '解析形状');
check('rpc: 缺 method 不进桥', parse({ id: 1 }) === null, '缺 method');
check('rpc: 缺 id 不进桥', parse({ method: 'add' }) === null, '缺 id');
check('rpc: 非对象不进桥', parse('hello') === null && parse(null) === null, '非对象');
check('rpc: params 非对象归一为 {}', JSON.stringify(parse({ id: 'x', method: 'm', params: [1, 2] }).params) === '{}', '数组 params');
check('rpc: 字符串 id 允许', parse({ id: 'req-1', method: 'm' }).id === 'req-1', '字符串 id');
check('rpc: theme 消息不进桥', parse({ type: 'theme', tokens: {} }) === null, 'theme 不进桥');

// ── ② theme 令牌注入形状 ─────────────────────────────────────────
check('theme: 注入消息形状', JSON.stringify(mod.buildThemeMessage({ '--bg': '#fff' })) ===
  JSON.stringify({ type: 'theme', tokens: { '--bg': '#fff' } }), 'theme 形状');

// ── ③ SSR 结构 ───────────────────────────────────────────────────
const html = renderToString(React.createElement(mod.HtmlPanelFrame, {
  pluginId: 'html-panel-demo',
  file: 'panel.html',
  rpc: async () => ({ result: {} }),
  themeTokens: { '--bg': '#fff' },
}));
check('ssr: sandbox 只给脚本（allow-scripts）', html.includes('sandbox="allow-scripts"') &&
  !html.includes('allow-same-origin'), 'sandbox 属性');
check('ssr: src 走 html/ 静态服务', html.includes('src="/plugins/html-panel-demo/html/panel.html"'), 'src');
check('ssr: data-slot 挂点', html.includes('data-slot="plugin-html-panel-frame"'), 'slot');

// ── ④ 接线钉 ─────────────────────────────────────────────────────
const src = fs.readFileSync(path.join(ROOT, 'src/components/plugin-ui/html-panel-frame.tsx'), 'utf8');
check('pin: 消息源校验防冒充', src.includes('event.source !== frame.contentWindow'), 'source 校验');
check('pin: 回帖 {id, result|error}', src.includes('{ id: req.id, ...(error ? { error } : { result: result ?? {} }) }'), '回帖形状');
check('pin: theme onLoad 注入', src.includes('onLoad={handleLoad}') && src.includes('buildThemeMessage(themeTokens)'), 'theme 注入');
check('pin: RPC 走宿主回调（同一审批闸面）', src.includes('await rpc(req.method, req.params ?? {})'), 'rpc 回调');

console.log(`\n${checks.filter(c => c.ok).length}/${checks.length} checks passed`);
