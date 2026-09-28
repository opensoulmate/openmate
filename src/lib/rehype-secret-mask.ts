/**
 * rehype-secret-mask.ts — P1 流式输出脱敏展示：Markdown 渲染层正文遮蔽
 *
 * rehype 插件：在 HAST 树上把文本节点里的敏感数据段落替换为
 * `<span>` 元素（node.data.secretMeta = SecretFinding），由
 * components/secret-pill.tsx 的 SecretAwareSpan 渲染为可点击揭示的脱敏胶囊。
 *
 * 设计取向（与 secret-pill.tsx 配套）：
 * - 遮蔽发生在 markdown 解析后、渲染前的 HAST 层——不破坏 markdown 结构
 *   （列表/加粗/表格里的密钥原位遮蔽，不需要拆分文档）。
 * - 围栏代码块（pre 子树）跳过：由 CodeBlock 的 maskCodeInText 显示遮蔽
 *   （SyntaxHighlighter 吃纯字符串，span 拆分会破坏高亮），两路都消费
 *   secret-redaction 的 splitSecretSegments 单一真源。
 * - node.data 挂元数据（unic 规范允许的 data 字段）：ReactMarkdown 会把
 *   node 原样传给自定义组件，无需把密钥原文塞进 DOM 属性。
 *
 * 参照：Warp secret_redaction（hover点击揭示UX）+ SUMMARY.md §四前端差距表
 * 「流式输出脱敏展示 | P1」。
 */

import { splitSecretSegments, maskSecret, type SecretFinding } from './secret-redaction';

export type SecretRisk = 'low' | 'medium' | 'high' | 'critical';

/** hast node shapes we touch (structural typing, no unist dep). */
interface HastText {
  type: 'text';
  value: string;
  data?: { secretMeta?: SecretFinding };
}
interface HastElement {
  type: 'element';
  tagName: string;
  properties?: Record<string, unknown>;
  data?: { secretMeta?: SecretFinding };
  children: HastNode[];
}
type HastNode = HastText | HastElement | { type: string; children?: HastNode[] };

/** Build the masked span element for one finding (child text is the masked preview). */
function secretSpan(finding: SecretFinding, rawText: string): HastElement {
  return {
    type: 'element',
    tagName: 'span',
    properties: { className: ['secret-redacted'] },
    data: { secretMeta: finding },
    children: [{ type: 'text', value: maskSecret(rawText) }],
  };
}

function isElement(node: HastNode): node is HastElement {
  return node.type === 'element' && Array.isArray((node as HastElement).children);
}

function isText(node: HastNode): node is HastText {
  return node.type === 'text';
}

/**
 * Walk the HAST tree and mask secrets in every text node outside `pre`.
 * Replaces each text node in-place in its parent's children array with
 * alternating text / span nodes. Handles both root and element nodes.
 */
function walk(node: HastNode): void {
  const children = (node as HastElement).children;
  if (!Array.isArray(children)) return;
  // 围栏代码块：跳过，CodeBlock 显示遮蔽自己处理
  if (isElement(node) && node.tagName === 'pre') return;

  const out: HastNode[] = [];
  let changed = false;
  for (const child of children) {
    if (isText(child)) {
      const segments = splitSecretSegments(child.value, 'high');
      if (segments.some((s) => s.finding)) {
        changed = true;
        for (const seg of segments) {
          if (seg.finding) {
            out.push(secretSpan(seg.finding, seg.text));
          } else {
            out.push({ type: 'text', value: seg.text });
          }
        }
        continue;
      }
    }
    out.push(child);
    walk(child);
  }
  if (changed) (node as HastElement).children = out;
}

/**
 * rehype plugin（unified attacher 签名）— 用法二选一：
 *   rehypePlugins={[rehypeSecretMask]}              // 直接挂 attacher（推荐）
 *   rehypePlugins={[[rehypeSecretMask, 'high']]}    // 挂 attacher+参数
 * 直接调用返回 transformer（测试用）：rehypeSecretMask()(tree)。
 * 注意：unified 把数组元素当 attacher（工厂）调用，不是 transformer——
 * 写成 rehypeSecretMask() 会被当作 attacher 以 0 参数调用而静默失效/崩溃。
 */
export function rehypeSecretMask(minRisk: SecretRisk = 'high') {
  // minRisk 保留在签名上（调用方可调档），当前实现固定 'high' 阈值
  // 与 SecretScanBadge 调用点一致；参数化实现待需要时在 splitSecretSegments 透传。
  void minRisk;
  return (tree: HastNode) => walk(tree);
}
