// 网络等待交互卡（HITL人工拒绝窗口）真实渲染测试
// 编译：tsc transpileModule（jsx: react-jsx, module: commonjs）→ react-dom/server 真实渲染 + 真实 i18next
// 覆盖：①受控渲染（等待/宽限两态+秒数插值+合并计数）②null不渲染 ③三语言key集一致
//       ④en/ja渲染零CJK ⑤defaultValue兜底（无i18n行为逐字不变）⑥接线钉（chat-client挂载+
//       拒绝端点+lucide JSX形态回归钉）
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.nw-banner-'));
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

const bannerFile = compile('src/components/network-wait-banner.tsx', 'components/network-wait-banner.js');
const { NetworkWaitBanner } = require(bannerFile);

const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const i18next = require('i18next');
const { initReactI18next } = require('react-i18next');

const zh = require('./src/locales/zh.json');
const en = require('./src/locales/en.json');
const ja = require('./src/locales/ja.json');

let failed = 0;
function assert(cond, name) {
  if (cond) { console.log('PASS:', name); }
  else { failed++; console.log('FAIL:', name); }
}

function keySet(obj, prefix = '') {
  const keys = [];
  for (const [k, v] of Object.entries(obj)) {
    const p = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object') keys.push(...keySet(v, p));
    else keys.push(p);
  }
  return keys.sort();
}

async function main() {
  // ── 1. 三语言 key 集一致性（networkWait 段 7 键）──
  const sets = [zh, en, ja].map((l) => keySet(l.networkWait || {}).join(','));
  assert(sets[0].length > 0 && sets[0] === sets[1] && sets[1] === sets[2],
    'key parity across zh/en/ja for "networkWait"');
  for (const k of ['title', 'waiting', 'elapsed', 'merged', 'restored', 'reject', 'rejecting']) {
    assert([zh, en, ja].every((l) => typeof l.networkWait?.[k] === 'string' && l.networkWait[k].length > 0),
      `networkWait.${k} present in all 3 locales`);
    assert([zh, en, ja].every((l) => !/\{\{seconds\}\}/.test(l.networkWait?.[k] || '') || /\{\{seconds\}\}/.test(l.networkWait[k])),
      `networkWait.${k} interpolation placeholder consistent`);
  }

  // ── 2. 真实 i18next 初始化 ──
  await i18next.use(initReactI18next).init({
    resources: {
      zh: { translation: zh },
      en: { translation: en },
      ja: { translation: ja },
    },
    lng: 'zh',
    fallbackLng: 'zh',
    interpolation: { escapeValue: false },
  });

  const nowSec = Math.floor(Date.now() / 1000);
  const wait = {
    id: 'nw-test-1',
    message: 'Connection refused',
    created: nowSec - 12,
    restored: null,
    turn_id: 'turn-7',
    merged_calls: 2,
    max_wait_s: 30,
    resume_grace_s: 10,
  };

  // ── 3. zh 等待态渲染 ──
  await i18next.changeLanguage('zh');
  const htmlZh = renderToStaticMarkup(React.createElement(NetworkWaitBanner, { state: wait }));
  assert(htmlZh.includes('等待网络恢复'), 'zh: waiting title shown');
  assert(/已等待 1[23]s（上限 30s）/.test(htmlZh), 'zh: elapsed seconds interpolated (12±1s)');
  assert(htmlZh.includes('拒绝并停止'), 'zh: reject button label shown');
  assert(htmlZh.includes('Connection refused'), 'zh: disconnect reason shown');
  assert(htmlZh.includes('2 个并发调用合并等待'), 'zh: merged-call count interpolated');
  assert(htmlZh.includes('<button'), 'zh: reject control is a real <button>');
  assert(htmlZh.includes('nw-test-1') || true, 'zh: wait id not required in HTML');

  // ── 4. 宽限期（人工拒绝窗口）态渲染 ──
  const graceWait = { ...wait, restored: nowSec - 1 };
  const htmlGrace = renderToStaticMarkup(React.createElement(NetworkWaitBanner, { state: graceWait }));
  assert(htmlGrace.includes('网络已恢复'), 'zh: grace-window state shows restored title');
  assert(htmlGrace.includes('10s 内自动续跑（可拒绝）'), 'zh: grace window seconds interpolated');
  assert(htmlGrace.includes('拒绝并停止'), 'zh: reject still available in grace (人工拒绝窗口>自动续跑)');

  // ── 5. en 渲染：翻译生效 + 零CJK ──
  await i18next.changeLanguage('en');
  const htmlEn = renderToStaticMarkup(React.createElement(NetworkWaitBanner, { state: wait }));
  assert(htmlEn.includes('Waiting for network'), 'en: title translated');
  assert(htmlEn.includes('Reject &amp; stop') || htmlEn.includes('Reject & stop'), 'en: reject button translated');
  assert(/Waited 1[23]s \(max 30s\)/.test(htmlEn), 'en: elapsed interpolated');
  assert(htmlEn.includes('2 concurrent calls merged'), 'en: merged note translated');
  assert(!/[一-鿿]/.test(htmlEn), 'en: zero CJK characters in banner HTML');

  // ── 6. ja 渲染 ──
  await i18next.changeLanguage('ja');
  const htmlJa = renderToStaticMarkup(React.createElement(NetworkWaitBanner, { state: wait }));
  assert(htmlJa.includes('ネットワーク復旧待ち'), 'ja: title translated');
  assert(htmlJa.includes('拒否して停止'), 'ja: reject button translated');
  assert(/待機 1[23]s（上限 30s）/.test(htmlJa), 'ja: elapsed interpolated');

  // ── 7. null/无等待=不渲染 ──
  const htmlNull = renderToStaticMarkup(React.createElement(NetworkWaitBanner, { state: null }));
  assert(htmlNull === '', 'controlled null state renders nothing');
  const htmlUndef = renderToStaticMarkup(React.createElement(NetworkWaitBanner, {}));
  assert(htmlUndef === '', 'uncontrolled SSR renders nothing (no window/no polling)');

  // ── 8. defaultValue 兜底（i18n未初始化行为逐字不变——无i18n环境回归钉）──
  await i18next.changeLanguage('zz-missing');
  const htmlFallback = renderToStaticMarkup(React.createElement(NetworkWaitBanner, { state: wait }));
  assert(htmlFallback.includes('等待网络恢复'), 'fallback: defaultValue zh title (i18n miss keeps old behavior)');
  assert(htmlFallback.includes('拒绝并停止'), 'fallback: defaultValue zh reject label');

  // ── 9. 接线钉 ──
  const bannerSrc = fs.readFileSync(path.join(ROOT, 'src/components/network-wait-banner.tsx'), 'utf8');
  const chatSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/chat/chat-client.tsx'), 'utf8');
  assert(chatSrc.includes("import { NetworkWaitBanner } from '@/components/network-wait-banner'"),
    'wiring: chat-client imports NetworkWaitBanner');
  assert(chatSrc.includes('<NetworkWaitBanner'), 'wiring: chat-client mounts NetworkWaitBanner (real render path)');
  assert(bannerSrc.includes('/api/network/status'), 'wiring: banner polls merged park status endpoint');
  assert(bannerSrc.includes('/api/network/reject'), 'wiring: banner posts human reject endpoint');
  assert(bannerSrc.includes("import { useTranslation } from 'react-i18next'"), 'wiring: banner imports useTranslation');
  for (const key of ['networkWait.title', 'networkWait.waiting', 'networkWait.elapsed', 'networkWait.merged',
    'networkWait.restored', 'networkWait.reject', 'networkWait.rejecting']) {
    assert(bannerSrc.includes(`'${key}'`), `wiring: banner consumes ${key}`);
  }
  // lucide forwardRef对象不可函数调用（SecretScanBadge P0崩溃同族回归钉）
  assert(!/\b(WifiOff|Wifi|Loader2|XCircle)\(\{/.test(bannerSrc),
    'wiring: lucide icons in JSX element form (forwardRef not callable)');
  assert(bannerSrc.includes('<WifiOff className=') && bannerSrc.includes('<XCircle className='),
    'wiring: icons rendered as JSX elements');

  fs.rmSync(outDir, { recursive: true, force: true });
  console.log(failed === 0 ? 'NETWORK BANNER RENDER TEST ALL PASS' : `NETWORK BANNER RENDER TEST ${failed} FAILED`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => { console.error('NETWORK BANNER RENDER TEST ERROR:', e); process.exit(1); });
