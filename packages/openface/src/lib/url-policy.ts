// workspace web-browser 标签 URL 准入判定 —— 单一真源（面板/壳桥/各页共用，禁手抄第二份）。
//
// 铁律（用户两次报障定档）：应用自身页面一律整页渲染，绝不进 workspace 面板/右列/iframe。
// 认证 UI（登录/注册/锁屏/过期接管/OAuth 回跳）更是禁中之禁——报障原话
// 「点退出后登录界面渲染进了右侧 workspace 面板」即本规则失守的症状。
// /api/file 文档端点例外：走 file-preview 标签（docx 渲染不触发下载对话框）。

const FILE_ENDPOINT_RE = /^\/api\/file(\?|$)/;

/** 是否「应用自身页面」URL（= 禁止进 workspace web-browser 标签，应整页跳转）。 */
export function isAppSelfWebUrl(raw: string | null | undefined): boolean {
  if (!raw || typeof window === 'undefined') return false;
  try {
    const u = new URL(raw, window.location.href);
    if (u.host !== window.location.host) return false; // 外链：照旧进面板
    return !FILE_ENDPOINT_RE.test(u.pathname + u.search); // 文件端点：file-preview 例外
  } catch {
    return false;
  }
}

/** 应用自身 URL → 整页路由路径（pathname+search+hash，规避协议/大小写误写差异）。 */
export function appSelfPath(raw: string): string {
  const u = new URL(raw, window.location.href);
  return u.pathname + u.search + u.hash;
}
