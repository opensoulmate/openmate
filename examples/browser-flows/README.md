# 浏览器录制流程样板（【样板】不与真流程混排）

这里的浏览器录制配方（browser_flows）是**能力演示样板**，已从运行时
`acp-proxy/data/browser_flows/` 归位到本目录——运行时录制目录只放真录的业务流程。

- `flow-5d12b83b.json` —— 【样板】「录入订单」：x.test 假站表单录制（fill 含 password
  占位），演示录制配方的 goto/click/fill 形状
- `flow-d455e155.json` / `.skill.md` —— 【样板】「立项系统」：dhzh.dhccjy.com 菜单
  导航录制（5 步，无业务数据），演示「录制完成自动 skill 化」

想试运行样板：把对应 `.json`（及 `.skill.md`）复制回 `acp-proxy/data/browser_flows/`
即可在浏览器流程面出现；用完删掉。真实业务流程请录真配方，别改样板充数。
