# evo 进化目标（用户经 SoulMate 对话写入，三车道每周期必读）

> 来源唯一：用户对 SoulMate 说「告诉evo：…」→ A2A set_goal → 此处。
> 车道消费规则：每周期开工先消费 `- [ ]` 未完成目标（最高优先级），完成打勾并注明 commit。

- [x] **[2026-10-23 03:32]** 【探针目标】回复正文不得混入执行旁白（完成度/决定类），需有回归测试 ← commit 3615691e：agent/execution_narration.py 生产者契约+NarrationSplitter 行级路由（旁白只进日志/轨迹，回复正文只留回答；fail-safe=识别不出归正文），自评轮接线+推理门判据同步；回归测试 13 例（tests/test_execution_narration.py，含真实 _run_llm_with_tools 路径三关）+全量 2932 绿（周期#669）
- [x] **[2026-10-23 07:55]** 【探针A2A桥】所有新增工具必须带真实路径回归测试 ← commit（周期#669）：新增工具真实路径回归测试参照形态落地——tests/test_evo_communicate_tool.py 5 例（最新工具 evo_communicate：真实工具循环路径→A2A 信封断言、回执原话透传不冒名、失败可见三态、五登记点接线）；规则固化=此后新增工具必须配真实路径回归测试（存量历史欠账不在本条，需补另立目标）
