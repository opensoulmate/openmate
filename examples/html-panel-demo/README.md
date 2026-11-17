# html-panel-demo（层② HTML 面板样板，不进运行时）

Sprint C 验收样板：**按钮 → 调样板 MCP 工具的最小 HTML 面板**。

- 面板内容：`html/panel.html`（静态只读服务 `GET /plugins/html-panel-demo/html/panel.html`）。
- 动作调用：`POST /plugins/html-panel-demo/html-rpc`（postMessage RPC 形状
  `{id, method, params}` → `{id, result|error}`；嵌 iframe 壳后改走
  `window.parent.postMessage`，形状不变）。
- 白名单：只可调 `plugin.yaml` 声明的工具（`add`/`reset`）+ `ui.*` 事件
  （`ui.toast`/`ui.refresh`/`ui.store.get`/`ui.store.set`）；未声明 method 必拒
  （`method_not_allowed`）。契约面：`GET /plugins/html-panel-demo/html-methods`。
- 审批闸：`reset` 声明 `approval: always`——未批调用返回 `approval_required`，
  前端壳弹审批确认后带 `approved: true` 重发（与 DSL 面同一审批闸）。
