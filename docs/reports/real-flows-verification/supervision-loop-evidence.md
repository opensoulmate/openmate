# 监督闭环真流程实证（自报≠verified）

真引擎实跑输出（verify_supervision_loop.py，全真件：FlowEngine+CollabRuntime+WorkOrderStore 落盘；仅工具/A2A 通道为脚本化桩）。

```text
## ① 全链实证：自报完成 → 未验证 → 内容级探针验收 → verified

- 自报交付（soulmate-a，文本自称 VERIFIED）→ 探针瞬间: confidence=reported done verified=False 未验证清单=['work']
- 探针验收后: confidence=verified verified=True verified_by=probe:p__probe claim_source=evidence
- 证据: probe=p__probe {"pass": true, "content": "报告 3 节齐全，数字对账一致"} when=$.check.pass == true → true
- 实例判档: verified
- 事件链: claim-capped → approval-approved → verified → run-ok

## ② 伪完成被拒实测（拒收留痕，节点保持 done·unverified）

- 自审：验收者=执行者 → 拒收: 验收翻档被拒（self_referential_evidence）: 验收者=执行者（soulmate-a），自审拒绝翻档（子代理自报须抽查）
- 无证据（fail-closed） → 拒收: 验收翻档被拒（no_evidence）: 无验收证据，拒绝翻档（报成功没用，要内容级探针/验收证据）
- 留痕: settle_rejects=2 verified=False 判档=done·unverified
- 拒收日志: ['拒翻档 [self_referential_evidence] 验收者=执行者（', '拒翻档 [no_evidence] 无验收证据，拒绝翻档（报成功没用，要内容级探']

## ③ 并行写域重叠拒载实测

- 拒载: <flow> parallel 节点 'fan' 并行分支写域重叠: b1↔b2 共享写域 ['src/app.py']（并发互踩静态拒载：加顺序依赖或拆开 scope）

（全真件落盘目录: /home/climbing/.hermes/cache/scratch/supervision-loop-qzquhxjx）
```

配套自动化实证（每步 pytest 过）：

- acp-proxy/tests/test_flow_verify_dual_axis.py — 全链/伪完成拒收/settle 修复轮重规划/清单/工单双轴/看板 API（13 项）
- acp-proxy/tests/test_flow_write_scope.py — shared_scope_pairs 路径语义 + 拒载（5 项）
- acp-proxy/tests/test_flow_agent_node_gaps.py — INPUT_REQUIRED 转人工/任务级断点恢复/换人重派/派发失败本地降级（9 项）
- render-test-tasks-inbox.cjs — /tasks 看板双轴黄绿 SSR 断言（27/27）
