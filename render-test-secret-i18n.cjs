// secret redaction UI i18n 收口 真实渲染测试（react-dom/server renderToStaticMarkup + 真实 i18next）
// 编译：tsc transpileModule（jsx: react-jsx, module: commonjs）→ 临时目录镜像 src/{lib,components}
// 覆盖：①三语言 key 集一致性 ②20个pattern类型×3语言全覆盖 ③真实渲染逐语言断言（zh/en/ja）
//       ④en/ja渲染HTML零中文字面量（i18n收口核心断言）⑤SecretScanBadge计数文案插值 ⑥接线钉
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.ssr-i18n-'));

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

// '@/…' 路径别名 → 临时目录编译产物（secret-scan-badge.tsx 用 @/lib 导入）
const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...args) {
  if (typeof request === 'string' && request.startsWith('@/')) {
    request = path.join(outDir, request.slice(2)) + '.js';
  }
  return origResolve.call(this, request, ...args);
};

compile('src/lib/secret-redaction.ts', 'lib/secret-redaction.js');
const pillFile = compile('src/components/secret-pill.tsx', 'components/secret-pill.js');
const badgeFile = compile('src/components/secret-scan-badge.tsx', 'components/secret-scan-badge.js');

const redaction = require(path.join(outDir, 'lib/secret-redaction.js'));
const { SecretPill } = require(pillFile);
const { SecretScanBadge } = require(badgeFile);

const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const i18next = require('i18next');
const { initReactI18next } = require('react-i18next');

const zh = require('./src/locales/zh.json');
const en = require('./src/locales/en.json');
const ja = require('./src/locales/ja.json');

const KEY = 'sk-' + 'a'.repeat(48); // openai_api_key

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
  // ── 1. 三语言 key 集一致性 ──
  for (const section of ['secretRedaction', 'markdown']) {
    const sets = [zh, en, ja].map((l) => keySet(l[section] || {}).join(','));
    assert(sets[0].length > 0 && sets[0] === sets[1] && sets[1] === sets[2],
      `key parity across zh/en/ja for "${section}"`);
  }
  for (const k of ['revealSecrets', 'maskSecrets', 'revealSecretsTip', 'maskSecretsTip']) {
    assert([zh, en, ja].every((l) => typeof l.markdown[k] === 'string' && l.markdown[k].length > 0),
      `markdown.${k} present in all 3 locales`);
  }

  // ── 2. pattern 类型全覆盖（从 lib 源码提取 PATTERNS 键，逐一核对三语言）──
  const libSrc = fs.readFileSync(path.join(ROOT, 'src/lib/secret-redaction.ts'), 'utf8');
  const patternTypes = [...libSrc.matchAll(/^  ([a-z_]+): \{$/gm)].map((m) => m[1]);
  assert(patternTypes.length === 20, `20 pattern types extracted from lib (got ${patternTypes.length})`);
  for (const lang of ['zh', 'en', 'ja']) {
    const missing = patternTypes.filter((ty) => !(ty in (lang === 'zh' ? zh : lang === 'en' ? en : ja).secretRedaction.patterns));
    assert(missing.length === 0, `all pattern labels present in ${lang}${missing.length ? ' missing: ' + missing : ''}`);
  }

  // ── 3. 真实 i18next 初始化（默认导出单例，react-i18next useTranslation 无 Provider 时消费它）──
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
  const i18n = i18next;

  const meta = { type: 'openai_api_key', label: 'OpenAI API Key', risk: 'critical', start: 0, end: KEY.length, matched: KEY };

  // ── 4. 逐语言真实渲染：SecretPill ──
  await i18n.changeLanguage('zh');
  const htmlZh = renderToStaticMarkup(React.createElement(SecretPill, { meta }));
  assert(htmlZh.includes('显示敏感数据'), 'zh: aria reveal label shown (pre-i18n behavior preserved)');
  assert(htmlZh.includes('点击显示原文'), 'zh: tooltip translated');
  assert(!htmlZh.includes(KEY), 'zh: raw secret still NOT in HTML (safety preserved)');

  await i18n.changeLanguage('en');
  const htmlEn = renderToStaticMarkup(React.createElement(SecretPill, { meta }));
  assert(htmlEn.includes('Show sensitive data'), 'en: aria reveal label translated');
  assert(htmlEn.includes('click to show'), 'en: tooltip translated');
  assert(htmlEn.includes('OpenAI API Key'), 'en: pattern label shown');
  assert(!htmlEn.includes('显示敏感数据') && !htmlEn.includes('点击'), 'en: zero Chinese UI literals in pill HTML');
  assert(!/[\u4e00-\u9fff]/.test(htmlEn), 'en: no CJK characters at all in pill HTML');
  assert(!htmlEn.includes(KEY), 'en: raw secret NOT in HTML');

  await i18n.changeLanguage('ja');
  const htmlJa = renderToStaticMarkup(React.createElement(SecretPill, { meta }));
  assert(htmlJa.includes('機密データを表示'), 'ja: aria reveal label translated');
  assert(htmlJa.includes('クリックで表示'), 'ja: tooltip translated');
  assert(!htmlJa.includes('显示敏感数据'), 'ja: no zh literals in pill HTML');

  // ── 5. 逐语言真实渲染：SecretScanBadge（计数插值 + 展开面板）──
  for (const [lang, countStr, header, noStr] of [
    ['zh', '1 项敏感数据', '安全扫描 — 敏感数据检测', null],
    ['en', '1 sensitive items', 'Security scan — sensitive data detection', '项敏感数据'],
    ['ja', '機密データ 1 件', 'セキュリティスキャン — 機密データ検出', '项敏感数据'],
  ]) {
    await i18n.changeLanguage(lang);
    const html = renderToStaticMarkup(React.createElement(SecretScanBadge, { text: `prefix ${KEY} suffix` }));
    assert(html.includes(countStr), `${lang}: badge count interpolated ("${countStr}")`);
    assert(!html.includes(KEY), `${lang}: raw secret NOT in badge HTML`);
    if (noStr) assert(!html.includes(noStr), `${lang}: zh count literal absent in badge HTML`);
    // 展开态（state 默认 collapsed，静态渲染只能覆盖触发条；面板文案走同一批 key，经 key 一致性断言覆盖）
    assert(html.includes('title='), `${lang}: badge tooltip attribute present`);
    void header;
  }

  // ── 6. 接线钉：组件源码确实消费 i18n key（防回退硬编码）──
  const badgeSrc = fs.readFileSync(path.join(ROOT, 'src/components/secret-scan-badge.tsx'), 'utf8');
  const pillSrc = fs.readFileSync(path.join(ROOT, 'src/components/secret-pill.tsx'), 'utf8');
  const mdSrc = fs.readFileSync(path.join(ROOT, 'src/components/markdown-content.tsx'), 'utf8');
  assert(badgeSrc.includes("import { useTranslation } from 'react-i18next'"), 'wiring: secret-scan-badge imports useTranslation');
  assert(pillSrc.includes("import { useTranslation } from 'react-i18next'"), 'wiring: secret-pill imports useTranslation');
  for (const key of ['secretRedaction.badge.title', 'secretRedaction.badge.count', 'secretRedaction.badge.header',
    'secretRedaction.badge.note', 'secretRedaction.badge.show', 'secretRedaction.badge.hide', 'secretRedaction.inline.tip']) {
    assert(badgeSrc.includes(`'${key}'`), `wiring: secret-scan-badge consumes ${key}`);
  }
  for (const key of ['secretRedaction.pill.showTip', 'secretRedaction.pill.hideTip',
    'secretRedaction.pill.ariaShow', 'secretRedaction.pill.ariaHide']) {
    assert(pillSrc.includes(`'${key}'`), `wiring: secret-pill consumes ${key}`);
  }
  for (const key of ['markdown.revealSecrets', 'markdown.maskSecrets', 'markdown.revealSecretsTip', 'markdown.maskSecretsTip']) {
    assert(mdSrc.includes(`'${key}'`), `wiring: markdown-content consumes ${key}`);
  }
  assert(!badgeSrc.includes('>项敏感数据<') && !pillSrc.includes('aria-label="显示敏感数据"'),
    'wiring: old hardcoded UI literals gone from JSX (kept only as defaultValue fallback)');
  // P0崩溃回归钉：lucide图标是forwardRef对象不可函数调用（riskIcon({...})形态=渲染即崩）
  assert(!/\b[A-Za-z]+Icon\(\{ *className/.test(badgeSrc) && !/\b(Eye|EyeOff|Shield|ShieldCheck|ShieldAlert|ChevronUp|ChevronDown)\(\{ *className/.test(badgeSrc),
    'wiring: lucide icon call-form gone (must be JSX elements — forwardRef not callable)');
  assert(badgeSrc.includes('<RiskIcon className=') && badgeSrc.includes('<ShieldCheck className='),
    'wiring: icons rendered as JSX elements');

  fs.rmSync(outDir, { recursive: true, force: true });
  console.log(failed === 0 ? 'I18N RENDER TEST ALL PASS' : `I18N RENDER TEST ${failed} FAILED`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => { console.error('I18N RENDER TEST ERROR:', e); process.exit(1); });
