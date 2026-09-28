// P1流式输出脱敏展示 真实渲染测试（react-dom/server renderToStaticMarkup）
// 编译：tsc transpileModule（jsx: react-jsx, module: commonjs）→ 临时目录镜像 src/{lib,components}
// 覆盖：splitSecretSegments分段 / maskCodeInText代码遮蔽 / rehype插件HAST包装 /
//       SecretPill遮蔽渲染（原始密钥绝不入HTML）/ SecretAwareSpan透传 /
//       ReactMarkdown端到端（真实markdown→遮蔽HTML）
const ts = require('typescript');
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const outDir = fs.mkdtempSync(path.join(ROOT, '.ssr-render-'));

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

compile('src/lib/secret-redaction.ts', 'lib/secret-redaction.js');
compile('src/lib/rehype-secret-mask.ts', 'lib/rehype-secret-mask.js');
const pillFile = compile('src/components/secret-pill.tsx', 'components/secret-pill.js');

const redaction = require(path.join(outDir, 'lib/secret-redaction.js'));
const { rehypeSecretMask } = require(path.join(outDir, 'lib/rehype-secret-mask.js'));
const { SecretPill, SecretAwareSpan } = require(pillFile);

const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const KEY = 'sk-' + 'a'.repeat(48); // openai_api_key
const JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U';
const EMAIL = 'user@example.com';

let failed = 0;
function assert(cond, name) {
  if (cond) { console.log('PASS:', name); }
  else { failed++; console.log('FAIL:', name); }
}

async function main() {
  // ── 1. splitSecretSegments ──
  const segsNone = redaction.splitSecretSegments('普通文本没有任何密钥', 'high');
  assert(segsNone.length === 1 && !segsNone[0].finding, 'no secret → single text segment');
  const segs = redaction.splitSecretSegments(`前缀 ${KEY} 后缀`, 'high');
  assert(segs.length === 3, 'secret → 3 segments');
  assert(segs[1].finding && segs[1].finding.type === 'openai_api_key', 'middle segment is the finding');
  assert(segs[1].text === KEY, 'finding segment holds full secret');
  assert(segs[0].text === '前缀 ' && segs[2].text === ' 后缀', 'plain segments keep surrounding text');
  const segsRisk = redaction.splitSecretSegments(`mail ${EMAIL} end`, 'high');
  assert(segsRisk.length === 1 && !segsRisk[0].finding, 'low-risk email NOT masked at minRisk=high');
  const segsJwt = redaction.splitSecretSegments(`jwt ${JWT} x`, 'high');
  assert(segsJwt.length === 3 && segsJwt[1].finding && segsJwt[1].finding.type === 'jwt', 'high-risk JWT masked');
  const segsMulti = redaction.splitSecretSegments(`a ${KEY} b ${JWT} c`, 'high');
  assert(segsMulti.length === 5 && segsMulti[1].finding && segsMulti[3].finding, 'two secrets → 5 segments');

  // ── 2. maskCodeInText ──
  const maskedCode = redaction.maskCodeInText(`export const k = "${KEY}";`, 'high');
  assert(!maskedCode.includes(KEY), 'maskCodeInText removes full secret');
  assert(maskedCode.includes('export const k'), 'maskCodeInText keeps surrounding code');
  assert(maskedCode.includes('••••'), 'maskCodeInText shows masked preview');

  // ── 3. rehypeSecretMask HAST 变换 ──
  const plugin = rehypeSecretMask();
  const tree = {
    type: 'root',
    children: [{
      type: 'element', tagName: 'p', properties: {}, children: [
        { type: 'text', value: `你的密钥是 ${KEY} 请保存` },
      ],
    }],
  };
  plugin(tree);
  const p = tree.children[0];
  assert(p.children.length === 3, 'HAST: text split into 3 nodes');
  const span = p.children[1];
  assert(span.type === 'element' && span.tagName === 'span', 'HAST: secret wrapped in span');
  assert(span.data && span.data.secretMeta && span.data.secretMeta.matched === KEY, 'HAST: node.data.secretMeta carries finding');
  assert(!span.children[0].value.includes(KEY), 'HAST: span child text is masked');
  // pre 子树跳过（围栏代码走 CodeBlock 显示遮蔽）
  const treePre = {
    type: 'root',
    children: [{
      type: 'element', tagName: 'pre', properties: {}, children: [
        { type: 'element', tagName: 'code', properties: {}, children: [{ type: 'text', value: KEY }] },
      ],
    }],
  };
  plugin(treePre);
  const inner = treePre.children[0].children[0].children[0];
  assert(inner.type === 'text' && inner.value === KEY, 'HAST: pre subtree untouched');
  // 无敏感数据的树不被改动
  const treePlain = {
    type: 'root',
    children: [{ type: 'element', tagName: 'p', properties: {}, children: [{ type: 'text', value: 'hello **world**' }] }],
  };
  plugin(treePlain);
  assert(treePlain.children[0].children.length === 1, 'HAST: plain tree unchanged');

  // ── 4. SecretPill 遮蔽渲染（核心安全断言：默认HTML绝不含原文）──
  const meta = { type: 'openai_api_key', label: 'OpenAI API Key', risk: 'critical', start: 0, end: KEY.length, matched: KEY };
  const htmlPill = renderToStaticMarkup(React.createElement(SecretPill, { meta }));
  assert(!htmlPill.includes(KEY), 'SecretPill: raw secret NOT in default HTML');
  assert(htmlPill.includes(KEY.slice(0, 4)) && htmlPill.includes(KEY.slice(-4)), 'SecretPill: masked preview (first4/last4) shown');
  assert(htmlPill.includes('OpenAI API Key'), 'SecretPill: type label shown');
  assert(htmlPill.includes('显示敏感数据'), 'SecretPill: reveal button present');
  assert(htmlPill.includes('title='), 'SecretPill: tooltip title present');
  // 揭示切换逻辑在源码中（点击交互无法用 renderToStaticMarkup 验证，源码断言兜底）
  const pillSrc = fs.readFileSync(path.join(ROOT, 'src/components/secret-pill.tsx'), 'utf8');
  assert(pillSrc.includes('setRevealed(true)') && pillSrc.includes('setRevealed(false)'), 'SecretPill: reveal/hide toggles wired');

  // ── 5. SecretAwareSpan：普通span透传 / secretMeta→pill ──
  const htmlSpan = renderToStaticMarkup(React.createElement(SecretAwareSpan, { node: {}, className: 'x', children: 'plain' }));
  assert(htmlSpan.includes('plain') && htmlSpan.includes('class="x"'), 'SecretAwareSpan: plain span passthrough');
  const htmlMasked = renderToStaticMarkup(React.createElement(SecretAwareSpan, {
    node: { data: { secretMeta: meta } }, children: KEY,
  }));
  assert(!htmlMasked.includes(KEY), 'SecretAwareSpan: secretMeta → pill without raw secret');

  // ── 6. ReactMarkdown 端到端：真实 markdown → 遮蔽HTML ──
  const ReactMarkdown = (await import('react-markdown')).default;
  const remarkGfm = (await import('remark-gfm')).default;
  const md = `你的密钥是 ${KEY} 请保存\n\n- 列表项里的 ${JWT}\n\n普通段落无敏感数据`;
  const htmlMd = renderToStaticMarkup(React.createElement(ReactMarkdown, {
    remarkPlugins: [remarkGfm],
    rehypePlugins: [rehypeSecretMask],
    components: { span: SecretAwareSpan },
  }, md));
  assert(!htmlMd.includes(KEY), 'E2E markdown: raw key NOT in HTML');
  assert(!htmlMd.includes(JWT), 'E2E markdown: raw JWT NOT in HTML');
  assert(htmlMd.includes('普通段落无敏感数据'), 'E2E markdown: plain text intact');
  assert(htmlMd.includes('你的密钥是') && htmlMd.includes('请保存'), 'E2E markdown: surrounding prose intact');
  assert(htmlMd.includes('显示敏感数据'), 'E2E markdown: reveal buttons rendered');

  // 围栏代码块端到端：pre 不被插件动（CodeBlock 自己遮蔽，此处断言原文仍走pre路径）
  const mdCode = "```bash\nexport KEY=" + KEY + "\n```";
  const htmlCode = renderToStaticMarkup(React.createElement(ReactMarkdown, {
    rehypePlugins: [rehypeSecretMask],
    components: { span: SecretAwareSpan },
  }, mdCode));
  assert(htmlCode.includes(KEY), 'E2E: fenced code untouched by plugin (CodeBlock masks at display)');

  // ── 7. 接线钉：markdown-content.tsx 真实接线在场（防 attacher 语义回退）──
  const mdSrc = fs.readFileSync(path.join(ROOT, 'src/components/markdown-content.tsx'), 'utf8');
  assert(mdSrc.includes('rehypePlugins={[rehypeSecretMask]}'), 'wiring: rehypePlugins uses attacher form');
  assert(!mdSrc.includes('rehypeSecretMask()'), 'wiring: broken transformer-as-attacher form absent');
  assert(mdSrc.includes('span: SecretAwareSpan'), 'wiring: components.span override present');
  assert(mdSrc.includes('maskCodeInText(code, \'high\')'), 'wiring: CodeBlock masking present');
  assert(mdSrc.includes('{displayCode}'), 'wiring: SyntaxHighlighter renders displayCode');

  fs.rmSync(outDir, { recursive: true, force: true });
  console.log(failed === 0 ? 'RENDER TEST ALL PASS' : `RENDER TEST ${failed} FAILED`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => { console.error('RENDER TEST ERROR:', e); process.exit(1); });
