# examples/ — 开发参考样板（不进运行时）

本目录的插件是**样板代码**，供插件/流程开发者参考抄写。运行时插件扫描只认
`plugins/` 目录，`examples/` 下的任何内容都**不会**被装载、不会出现在 `/plugins`
清单、不会向流程引擎注册流程（防测试数据再生）。

## demo-greeter — 插件机制 PoC 演示插件

一个插件的最小全形状样板，覆盖三件事（外加一条完整流程定义）：

- **菜单长出来**：`plugin.yaml` 的 `ui.nav`（导航注册）
- **页面渲染**：`ui/home.json`（UI DSL：form/list/report 面板，数据绑定走 MCP 工具）
- **工具可调**：`main.py` 的 `handle_greet` / `handle_greet_log` / `handle_build_report`
  （经 MCP 工具注册桥，UI 与对话共用；`build_report` 演示 approval=always 人工确认闸）
- **流程定义**：`flows/daily-report.yaml`（cron 触发 → 调工具（含重试/超时）→
  人工审批闸 → 出报告 → 收尾通知，可观测进 flow-console 运行台）
  + `flows/greet-branch.yaml`（**decision 判断分支**样板：when/then/else 分流，
  /will 观察窗「判断分支可视化」渲染的即此结构）

想跑它：把 `demo-greeter/` 整目录复制（或软链）到 `plugins/` 下重启服务即可；
用完删掉，`DELETE /flows/{flow_id}` 可清掉它注册的流程与实例。

设计说明见 `docs/plugin-system-design.md`。
