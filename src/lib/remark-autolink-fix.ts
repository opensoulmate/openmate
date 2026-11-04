/**
 * remark-autolink-fix —— 裸域名自动链接的全角尾标点修复
 *
 * 根因：remark-gfm 的 autolink literal 只剔半角尾标点（.,;:!?），
 * 中文全角标点（。）、）等被吞进 URL——"（主站 www.baidu.com）。"
 * 的 href 变成 http://www.baidu.com）。 跳转端再规范化全角字符后
 * 得到 http://www.baidu.com)./ 之类的坏地址。
 *
 * 修法（语义正确）：剥掉 URL 与链接文本尾部的 CJK 标点，把标点
 * 还原为链接后的正文节点——句子标点归正文，链接只留纯 URL。
 */
import type { Root } from 'mdast';

const CJK_TRAILING = /[。．，、；：？！）】」』》”’]+$/;

function walk(node: any): void {
  if (!node || !Array.isArray(node.children)) return;
  for (let i = 0; i < node.children.length; i++) {
    const child = node.children[i];
    if (child && child.type === 'link' && typeof child.url === 'string' && /^https?:/i.test(child.url)) {
      const punct = child.url.match(CJK_TRAILING)?.[0];
      if (punct) {
        child.url = child.url.replace(CJK_TRAILING, '');
        // 链接显示文本若同款吞了标点，一并剥掉
        const kids = child.children || [];
        const last = kids[kids.length - 1];
        if (last && last.type === 'text' && typeof last.value === 'string') {
          last.value = last.value.replace(CJK_TRAILING, '');
          if (!last.value) kids.pop();
        }
        // 标点还原为正文（紧随链接之后）
        node.children.splice(i + 1, 0, { type: 'text', value: punct });
        i++;
      }
    }
    walk(child);
  }
}

export function remarkAutolinkFix() {
  return (tree: Root) => walk(tree);
}
