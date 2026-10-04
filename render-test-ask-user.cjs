// P1交互式提问卡（多问题/多选）真实渲染测试
// 编译：tsc transpileModule（jsx: react-jsx, module: commonjs）→ react-dom/server 真实渲染 + 真实 i18next
// 覆盖：①composeAskAnswers纯函数作答格式（与后端clarify契约一致）②多问题卡SSR渲染（问题块/
//       多选标签/自定义输入/提交按钮）③单问题旧形态渲染逐字不变（回归钉）④三语言askUser key集
//       一致 ⑤en渲染零CJK ⑥defaultValue兜底 ⑦接线钉（chat-client解析+挂载+后端payload契约）
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const Module = require('module');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.ask-card-'));
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

const menuFile = compile('src/components/task-choice-menu.tsx', 'components/task-choice-menu.js');
const { TaskChoiceMenu, composeAskAnswers } = require(menuFile);

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

const QUESTIONS = [
  {
    id: 'env', question: '部署环境',
    options: [{ id: 'prod', label: '生产' }, { id: 'staging', label: '预发', description: '灰度' }],
    multi: true, allow_custom: false,
  },
  {
    id: 'db', question: '数据库',
    options: [{ id: 'pg', label: 'PostgreSQL' }, { id: 'my', label: 'MySQL' }],
    multi: false, allow_custom: true,
  },
];

const noop = () => {};

function renderMenu(props) {
  return renderToStaticMarkup(React.createElement(TaskChoiceMenu, {
    question: '部署确认', options: [], onSelect: noop, onCustomSubmit: noop, onSubmitAnswers: noop,
    ...props,
  }));
}

async function main() {
  // ── 1. composeAskAnswers 纯函数（作答格式=后端clarify工具描述契约）──
  const text1 = composeAskAnswers(QUESTIONS, { env: ['prod', 'staging'], db: ['pg'] }, {});
  assert(text1 === '部署环境：生产、预发\n数据库：PostgreSQL',
    'compose: multi labels joined by 、, one line per question');
  const text2 = composeAskAnswers(QUESTIONS, { env: ['prod'], db: [] }, { db: 'MariaDB' });
  assert(text2 === '部署环境：生产\n数据库：（自定义）MariaDB',
    'compose: custom-only answer gets （自定义）prefix');
  const text3 = composeAskAnswers(QUESTIONS, { env: ['prod'], db: ['pg'] }, { db: 'extra' });
  assert(text3 === '部署环境：生产\n数据库：PostgreSQL、（自定义）extra',
    'compose: labels + custom both kept');
  const text4 = composeAskAnswers(QUESTIONS, {}, {});
  assert(text4 === '部署环境：\n数据库：', 'compose: unanswered question keeps empty answer');
  assert(composeAskAnswers(QUESTIONS, { env: ['no-such-id'] }, {}) === '部署环境：no-such-id\n数据库：',
    'compose: unknown id falls back to id (no crash)');

  // ── 2. 真实 i18next 初始化 ──
  await i18next.use(initReactI18next).init({
    lng: 'zh',
    resources: {
      zh: { translation: zh },
      en: { translation: en },
      ja: { translation: ja },
    },
    interpolation: { escapeValue: false },
  });

  // ── 3. askUser 三语言 key 集一致性（5 键）──
  const sets = [zh, en, ja].map((l) => keySet(l.askUser || {}).join(','));
  assert(sets[0].length > 0 && sets[0] === sets[1] && sets[1] === sets[2],
    'key parity across zh/en/ja for "askUser"');
  for (const k of ['multiHint', 'multiTag', 'submit', 'submitted', 'customPlaceholder']) {
    assert([zh, en, ja].every((l) => typeof l.askUser?.[k] === 'string' && l.askUser[k].length > 0),
      `askUser.${k} present in all 3 locales`);
  }

  // ── 4. zh 多问题卡 SSR 渲染 ──
  await i18next.changeLanguage('zh');
  const htmlZh = renderMenu({ questions: QUESTIONS });
  assert(htmlZh.includes('部署确认'), 'zh: card title rendered');
  assert(htmlZh.includes('部署环境') && htmlZh.includes('数据库'), 'zh: both questions rendered');
  assert(htmlZh.includes('可多选'), 'zh: multi tag on multi question');
  assert((htmlZh.match(/可多选/g) || []).length === 1, 'zh: multi tag only on multi question (not single)');
  assert(htmlZh.includes('生产') && htmlZh.includes('预发') && htmlZh.includes('PostgreSQL') && htmlZh.includes('MySQL'),
    'zh: all option labels rendered');
  assert(htmlZh.includes('灰度'), 'zh: option description as title attr');
  assert(htmlZh.includes('提交全部作答'), 'zh: submit button rendered');
  assert(htmlZh.includes('或输入自定义内容...'), 'zh: custom input only where allow_custom (rendered)');
  assert((htmlZh.match(/或输入自定义内容/g) || []).length === 1,
    'zh: custom input exactly once (only db question has allow_custom)');
  assert(htmlZh.includes('<button'), 'zh: real buttons (options + submit)');
  assert(!htmlZh.includes('已提交作答'), 'zh: not submitted initially');

  // ── 5. en 全翻译 + 零 CJK（ASCII 数据面）──
  await i18next.changeLanguage('en');
  const EN_Q = [
    { id: 'e', question: 'Deploy env', options: [{ id: 'p', label: 'Prod' }, { id: 's', label: 'Staging' }], multi: true },
    { id: 'd', question: 'Database', options: [{ id: 'pg', label: 'PostgreSQL' }, { id: 'm', label: 'MySQL' }] },
  ];
  const htmlEn = renderMenu({ question: 'Deploy confirm', questions: EN_Q });
  assert(htmlEn.includes('Answer all questions, then submit once'), 'en: multiHint translated');
  assert(htmlEn.includes('Multi-select'), 'en: multiTag translated');
  assert(htmlEn.includes('Submit answers'), 'en: submit translated');
  assert(!/[\u4e00-\u9fff]/.test(htmlEn), 'en: rendered card contains zero CJK');

  // ── 6. ja 翻染 ──
  await i18next.changeLanguage('ja');
  const htmlJa = renderMenu({ question: 'Deploy confirm', questions: EN_Q });
  assert(htmlJa.includes('すべての質問に答えてから一括送信してください'), 'ja: multiHint translated');
  assert(htmlJa.includes('複数選択可'), 'ja: multiTag translated');

  // ── 7. defaultValue 兜底（i18n miss 行为逐字不变）──
  await i18next.changeLanguage('zz-missing');
  const htmlFallback = renderMenu({ questions: QUESTIONS });
  assert(htmlFallback.includes('多个问题请一次作答后提交'), 'fallback: defaultValue zh multiHint');
  assert(htmlFallback.includes('提交全部作答'), 'fallback: defaultValue zh submit');

  // ── 8. 单问题旧形态渲染逐字不变（回归钉）──
  const htmlLegacy = renderMenu({
    question: '选方向', options: [{ id: 'a', label: '选项A', description: 'descA' }, { id: 'b', label: '选项B' }],
  });
  assert(htmlLegacy.includes('选方向'), 'legacy: question rendered');
  assert(htmlLegacy.includes('选项A') && htmlLegacy.includes('选项B'), 'legacy: option labels rendered');
  assert(htmlLegacy.includes('或输入自定义内容'), 'legacy: custom input rendered');
  assert(htmlLegacy.includes('1') && htmlLegacy.includes('2'), 'legacy: numbered options kept');
  assert(!htmlLegacy.includes('提交全部作答'), 'legacy: no submit button (old click-to-select UX)');
  assert(!htmlLegacy.includes('可多选'), 'legacy: no multi tag');
  // 旧形态在 questions 缺省/空时同样走旧渲染
  const htmlLegacyQ = renderMenu({
    question: '选方向', options: [{ id: 'a', label: '选项A' }], questions: [],
  });
  assert(htmlLegacyQ.includes('选项A') && !htmlLegacyQ.includes('提交全部作答'),
    'legacy: empty questions array still renders old menu');

  // ── 9. 接线钉（防脱敏/防漂移）──
  const menuSrc = fs.readFileSync(path.join(ROOT, 'src/components/task-choice-menu.tsx'), 'utf8');
  assert(menuSrc.includes('export function composeAskAnswers'), 'wiring: composeAskAnswers exported (single source)');
  assert(menuSrc.includes('onSubmitAnswers(composeAskAnswers(questions, answers, customTexts))'),
    'wiring: submit uses composeAskAnswers (no second formatter)');
  assert(menuSrc.includes('import { useTranslation } from "react-i18next"'), 'wiring: menu imports useTranslation');

  const chatSrc = fs.readFileSync(path.join(ROOT, 'src/app/(app)/chat/chat-client.tsx'), 'utf8');
  assert(chatSrc.includes("parsed.type === 'choice' && (parsed.choices || parsed.questions)"),
    'wiring: chat-client parses questions payload');
  assert(chatSrc.includes('questions={p.questions}'), 'wiring: chat-client passes questions to TaskChoiceMenu');
  assert(chatSrc.includes('onSubmitAnswers='), 'wiring: chat-client wires onSubmitAnswers');
  assert(chatSrc.includes("if (send) send(text)"), 'wiring: answer goes through __openmate_send__ channel');

  const pySrc = fs.readFileSync(path.join(ROOT, 'acp-proxy/agent/soulmate_agent.py'), 'utf8');
  assert(pySrc.includes('"questions": {'), 'wiring: clarify tool schema has questions');
  assert(pySrc.includes('await self._handle_clarify(session_id, func_args)'),
    'wiring: tool loop dispatches to _handle_clarify');
  assert(pySrc.includes('每行『问题：选项A、选项B』'),
    'contract: backend tool description documents the answer line format');
  assert(pySrc.includes('"questions": q_items'), 'contract: multi payload shape');
  assert(pySrc.includes('CLARIFY_WAIT_TIMEOUT_S'), 'contract: wait timeout single source');

  console.log(failed === 0 ? '\nALL PASS' : `\n${failed} FAILED`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(1); });
