import { DocsClient } from "./docs-client";

/**
 * 文档中心页面入口
 * 替代原有的 /dev-specs 页面，提供统一的文档浏览体验
 */
export default function DocsPage() {
  return <DocsClient />;
}
