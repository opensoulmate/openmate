"""LLM推理层 — 流式SSE调用OpenAI兼容API

支持Ollama、通义千问、OpenAI等兼容接口的流式和非流式调用。
自动注入系统提示词（SOUL规则），管理上下文窗口裁剪。
集成ModelRouter智能路由，根据任务复杂度自动选择最优模型。
"""

import asyncio
import json
import logging
from utils.token_manager import get_max_output_tokens
from utils.llm_config import describe, resolve_agent_llm_config
# 输出防护策略单一真源=utils.llm_safety（2026-09-28接线）：此前Aider续写/
# Gemini CLI omission防护只存在于llm_gateway/llm_safety死路径，chat()真实传输
# 裸奔（finish_reason=length静默截断、omission占位符照单全收）
from utils.llm_safety import (
    MAX_CONTINUATION,
    TurnSendBudget,
    build_continuation_messages,
    check_finish_reason,
    check_omission,
)
# LLM重试策略单一真源=utils.llm_retry（2026-09-28接线，kilocode retry.ts移植）：
# Retry-After三格式+5xx无条件重试+指数退避封顶30s。接线前chat()/chat_stream()
# 零HTTP重试（瞬时429/5xx直接"[LLM错误: …]"掐断）、with_tools固定退避不听头
# 且把400当可重试却漏501/504等其余5xx
from utils.llm_retry import (
    classify_failure,
    MAX_ATTEMPTS,
    backoff_ms,
    is_retryable_status,
    is_transient_error,
    plan_error_retry,
    plan_status_retry,
)
# 模型降级有序链=utils.model_fallback（CowAgent chat_fallback移植，2026-09-30
# 三路会话面接线）：primary失败→备胎link——429+有备胎立即切（switch_now不干等）、
# 切换进来的link只给1发（link_retries=0）、走完环绕第二遍、sticky停在应答link、
# 链耗尽报错列出所有试过的模型。opt-in：LLM_FALLBACK_CHAIN未配置=空链=行为与
# 接线前逐字一致
from utils.model_fallback import FallbackChain, FallbackLink, load_fallback_chain
from utils.stall_guard import send_with_stall_guard

# ── 传输故障可观测（路线图10.1）：分类入tool_failures.db ──────────────────
_llm_fail_log = None


def record_llm_failure(path: str, source, action: str, attempt: int,
                       outcome: str, detail: str = "") -> None:
    """传输故障分类落 tool_failures.db（10.1：传输故障分类可观测）。

    kind=utils.llm_retry.classify_failure 单一真源（rate_limit/server_error/
    protocol_error/read_timeout/connect_error/network_error/client_error…）。
    旁路纪律（同prompt_prefetch/experience_sink）：只记账，绝不冒泡、绝不阻塞
    LLM 链路；action=llm_retry（重试已排程）/llm_give_up（放弃，失败必须可见）。
    """
    global _llm_fail_log
    try:
        # 测试期卫生：不污染真实 tool_failures.db（要观测的测试显式设
        # TOOL_FAILURE_DB_PATH 到临时库即可正常记账）
        if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("TOOL_FAILURE_DB_PATH"):
            return
        if _llm_fail_log is None:
            from agent.tool_recovery import ToolRecoveryManager
            _llm_fail_log = ToolRecoveryManager()
        _llm_fail_log.record(
            session_id="", tool_name=f"llm:{path}",
            kind=classify_failure(source),
            error=detail or str(source),
            action=action, attempt=int(attempt), outcome=outcome,
        )
    except Exception:
        pass


# 网络离线三态=utils.network_state（网络恢复专项，kilocode SessionNetwork移植）：
# 传输错误先过离线判定——全局断网则park等待恢复（3URL竞速探测+3s轮询+10s宽限
# 自动续跑，有界park），恢复后继续原有重试；端点级故障/非断网走原有退避不变
from utils import network_state
import os
from typing import AsyncGenerator, Optional, TYPE_CHECKING

import httpx

# 避免循环导入: TYPE_CHECKING下导入类型，运行时用字符串
if TYPE_CHECKING:
    from model_router import ModelRouter

logger = logging.getLogger("acp-agent.llm")


class LLMNonRetryableError(RuntimeError):
    """HTTP状态明确不可重试（401/403/404等凭证/请求级错误）：
    fail-fast直接可见失败，不进入重试环烧掉3次调用（mem0：失败必须可见）"""

# 默认系统提示词 — 包含SOUL强制注释规则和迭代复盘规则
DEFAULT_SYSTEM_PROMPT = """你是OpenMate内置Vibe Coding Agent，一个专业的AI编程助手。

## 最高优先级：强制注释铁律
1. 任何代码输出，哪怕仅一行，必须附带注释
2. 注释说明业务目的和作用，不写重复代码字面含义的无效注释
3. 函数/类/接口必须有头部文档注释（Docstring/TSDoc）
4. 修改旧代码时同步更新注释，删除代码时清理失效注释

## 文件操作规范
- 优先使用增量diff，不要输出完整文件
- 修改前先读取原始文件
- 每次变更生成unified diff格式

## 迭代复盘规则
每次任务完成后，自动输出复盘：
1. 修改了哪些文件，每个文件做了什么改动
2. 核心新增逻辑的关键代码片段
3. 新增依赖和配置
4. 数据流：输入→输出
5. 未来容易出什么bug，排查看哪些日志"""


class LLMEngine:
    """LLM推理层 — 流式SSE调用OpenAI兼容API

    支持ModelRouter智能路由: 如果传入router，会根据每次请求的prompt
    动态选择最优模型（如auto模式下简单任务用便宜模型，复杂任务用强模型）。
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        system_prompt: str = "",
        model_router: Optional["ModelRouter"] = None,
        agent_id: str = "",
    ):
        """初始化LLM引擎，从参数或环境变量读取配置

        Args:
            api_key: API密钥（可选，优先级最高）
            base_url: API基础URL（可选，优先级最高）
            model: 模型名称（可选，优先级最高）
            system_prompt: 系统提示词（可选）
            model_router: ModelRouter实例（可选），启用后每次chat根据prompt选择模型
            agent_id: agent标识符（可选），配合router做agent级模型覆盖
        """
        # LLM配置收口单一真源（2026-09-27）：显式参数 > 宿主注入OPENAI_* >
        # resolve_llm_config静态真源 > 裸机本地Ollama兜底——契约见
        # utils/llm_config.resolve_agent_llm_config docstring。旧实现此处自扫
        # LLM_*elseOPENAI_* + 硬编码模型名兜底，与全系统单一真源双源漂移
        # （此处deepseek-r1:latest vs start/wakeup的qwen3:8b），已销账。
        _cfg = resolve_agent_llm_config(api_key=api_key, base_url=base_url, model=model)
        self.api_key = _cfg["api_key"]
        self.base_url = _cfg["base_url"]
        self.model = _cfg["model"]
        # P0-4可观测：key从哪来（param/openai_env/file/env/""），key原文绝不进日志
        self.key_source = _cfg["key_source"]
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

        # ModelRouter智能路由（可选）
        self.model_router = model_router
        self.agent_id = agent_id

        # 路由模式: 默认auto，可通过环境变量覆盖
        self.route_mode = os.environ.get("MODEL_ROUTER_MODE", "auto")

        # P1 provider usage回填：最近一次LLM调用的provider权威usage（chat_stream_with_tools
        # / chat写入；token归因estimate→真实校准的信号源，无usage时为None）
        self.last_usage: Optional[dict] = None
        # OpenAI标准流式usage保障：stream_options.include_usage（标准规定流式默认不带
        # usage，除非显式请求）。部分OpenAI兼容server不认该字段会400——首次报错后
        # 置False永久回退，探测只发生一次，不影响后续请求
        self._stream_options_ok = True

        logger.info(f"LLM Engine init: {describe(_cfg)}, router={'enabled' if model_router else 'disabled'}")

    def _make_client(self) -> httpx.AsyncClient:
        """创建新的HTTP客户端实例

        不复用连接池，避免httpx AsyncClient在websockets serve循环内
        因内部anyio task group状态冲突导致流式读取死锁。
        """
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            timeout=httpx.Timeout(connect=10, read=120, write=10, pool=10),
        )

    def _resolve_model(self, messages: list[dict]) -> tuple[str, str, str]:
        """通过ModelRouter解析模型 — 返回 (model_name, base_url, api_key)

        如果没有router或router选择失败，回退到原有固定配置。
        从messages中提取最后一条用户消息作为prompt用于复杂度评估。

        Args:
            messages: 对话历史消息

        Returns:
            (model_name, base_url, api_key) 三元组
        """
        if not self.model_router:
            # 没有router，使用原有固定配置
            return self.model, self.base_url, self.api_key

        # 提取最后一条用户消息作为prompt（用于复杂度评估）
        prompt = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                prompt = msg.get("content", "")
                break

        try:
            selection = self.model_router.select_model(
                prompt=prompt,
                mode=self.route_mode,
                agent_id=self.agent_id,
            )
            return selection.model, selection.base_url, selection.api_key
        except Exception as e:
            logger.warning("ModelRouter选择失败，回退到固定配置: %s", e)
            return self.model, self.base_url, self.api_key

    @staticmethod
    def _apply_link(client: httpx.AsyncClient, payload: dict, link: Optional[FallbackLink]) -> None:
        """切换降级链link：就地改写client端点/鉴权+payload模型名（CowAgent use_fallback）。

        client.base_url/headers为httpx公开可写属性（0.28.1实测setter可用）；鉴权
        **整体替换**防primary的key泄漏给备胎provider。chat()的async with包在外层，
        不重建client只改端点。
        """
        if link is None:
            # try_advance成功后chain.current必非None（编程不变量）——失败必须可见
            raise RuntimeError("model_fallback: try_advance成功后chain.current为None（内部不一致）")
        client.base_url = link.base_url
        client.headers = link.headers()
        payload["model"] = link.model

    async def chat_stream(
        self, messages: list[dict], cancel_event: Optional[asyncio.Event] = None,
        system_prompt: Optional[str] = None, _continuation_depth: int = 0,
        _retry_count: int = 0, _chain: Optional[FallbackChain] = None,
        _budget: Optional[TurnSendBudget] = None,
    ) -> AsyncGenerator[str, None]:
        """流式输出LLM响应 — 每个yield是一个纯文本delta片段

        如果启用了ModelRouter，每次请求会根据prompt动态选择最优模型。

        输出防护（P2流式续写，2026-09-28接线，与chat()同一策略单一真源
        utils.llm_safety）：finish_reason=length纯文本截断时按段自动续写
        （Aider方案，各段流式无缝拼接，最多MAX_CONTINUATION次），次数用尽/
        空段不续写→显式截断标记（AIHawk"截断必须显式标记"，不静默半截收尾）。
        已取消（cancel_event）不续写不标记。

        HTTP重试（utils.llm_retry单一真源，kilocode retry.ts）：429/408/5xx可重试
        （有头听头Retry-After三格式），瞬时传输错误在本attempt零输出时可重试
        （已流出内容重试=双份回复，不重试走原失败契约），400/401等fail-fast。
        接线前chat_stream零HTTP重试，瞬时限流直接"[LLM错误: …]"掐断真实回复。

        续写×重试联合封顶（utils.llm_safety.TurnSendBudget单一真源，2026-10-01）：
        续写（最多MAX_CONTINUATION+1段）与HTTP重试（每段MAX_ATTEMPTS发）共享同一
        逻辑回合发送预算（MAX_TURN_SENDS发硬顶），余额用尽=显式失败/显式截断标记，
        不静默不多发（此前理论12发/回合无联合上界）。

        Args:
            messages: 对话历史（不含system提示词，会自动注入）
            cancel_event: 取消信号，设置后停止流式输出
            system_prompt: 自定义system提示词，None时使用默认
            _continuation_depth: 续写递归深度（内部参数，外部调用方勿传）
            _retry_count: HTTP重试递归计数（内部参数，外部调用方勿传）
            _budget: 逻辑回合发送预算（内部参数，外部调用方勿传）
        """
        # 通过router解析模型（无router时返回固定配置）
        resolved_model, resolved_base_url, resolved_api_key = self._resolve_model(messages)

        # 模型降级有序链（CowAgent chat_fallback，utils.model_fallback，2026-09-30
        # 接线）：opt-in（LLM_FALLBACK_CHAIN未设=空链=行为不变）；sticky——链切换过
        # 则本run停在应答link（chain.current），重试/续写递归携带同一链不弹回primary
        if _chain is None:
            chain = load_fallback_chain(primary_model=resolved_model)
        else:
            chain = _chain
        if chain.current is not None:
            resolved_model = chain.current.model
            resolved_base_url = chain.current.base_url
            resolved_api_key = chain.current.api_key

        # 续写×重试联合发送预算（utils.llm_safety.TurnSendBudget单一真源）：
        # 随重试/续写/降级链切换递归线程传递，一次调用=一个逻辑回合=一个预算
        budget = _budget if _budget is not None else TurnSendBudget()

        # 使用解析后的配置创建客户端
        client = httpx.AsyncClient(
            base_url=resolved_base_url,
            headers={"Authorization": f"Bearer {resolved_api_key}"} if resolved_api_key else {},
            timeout=httpx.Timeout(connect=10, read=120, write=10, pool=10),
        )
        payload = {
            "model": resolved_model,
            "messages": self._build_messages(messages, system_prompt),
            "stream": True,
            "temperature": 0.7,
            "max_tokens": get_max_output_tokens(),
        }
        # OpenAI标准：流式usage默认不发，stream_options.include_usage显式请求
        if self._stream_options_ok:
            payload["stream_options"] = {"include_usage": True}
        # P2流式续写状态：本段文本累积（段语义，只放本段防重复入history）
        # + 观察到的finish_reason（check_finish_reason单一真源归一）
        _segment_parts: list[str] = []
        _last_finish: str | None = None
        try:
            async with client:
                if not budget.spend():
                    logger.warning("[LLM] 续写×重试联合发送预算已用尽，不再发送")
                    yield "\n[LLM错误: 本回合HTTP发送预算已用尽（续写×重试联合封顶）]"
                    return
                req = client.build_request("POST", "/chat/completions", json=payload)
                response = await client.send(req, stream=True)
                try:
                    if response.status_code != 200:
                        body = await response.aread()
                        _err = body.decode()[:200]
                        if "stream_options" in _err and self._stream_options_ok:
                            # provider不支持stream_options：永久回退并重试一次
                            # （flag已置False，递归不会二次进入本分支）
                            self._stream_options_ok = False
                            logger.warning("[LLM] provider不支持stream_options，已回退并重试")
                            async for _c in self.chat_stream(
                                messages, cancel_event, system_prompt,
                                _continuation_depth=_continuation_depth,
                                _retry_count=_retry_count,
                                _chain=chain, _budget=budget,
                            ):
                                yield _c
                            return
                        # 重试决策合并单一真源=plan_status_retry（2026-09-29）：三路
                        # 重试环此前三处内联组合「可重试∧次数→backoff→sleep」，现收口
                        # utils.llm_retry单点；None=不重试
                        _wait = plan_status_retry(
                            response.status_code, _retry_count,
                            getattr(response, "headers", None),
                        )
                        # 降级链切换决策单点=chain.try_advance（CowAgent，2026-09-30）：
                        # 429+有备胎立即切（switch_now不干等Retry-After）/重试已放弃换
                        # link再试；切换进来的link只给1发（_retry_count=MAX_ATTEMPTS-1，
                        # plan_*预算见底=首败即换，CowAgent link_retries=0）
                        _summary = f"LLM HTTP {response.status_code}: {_err}"
                        if budget.remaining > 0 and chain.try_advance(
                            _summary, retry_planned=(_wait is not None),
                            status=response.status_code,
                        ):
                            async for _c in self.chat_stream(
                                messages, cancel_event, system_prompt,
                                _continuation_depth=_continuation_depth,
                                _retry_count=MAX_ATTEMPTS - 1,
                                _chain=chain, _budget=budget,
                            ):
                                yield _c
                            return
                        if _wait is not None and budget.remaining > 0:
                            # 429/408/5xx可重试（utils.llm_retry单一真源，kilocode）：
                            # 有头听头（Retry-After三格式），无头指数退避——接线前
                            # chat_stream零HTTP重试，瞬时限流直接"[LLM错误: …]"掐断
                            # 真实回复（soulmate_agent.py:1762主路径）
                            logger.warning(
                                f"[LLM] API {response.status_code}，{_wait:.1f}s后重试 "
                                f"({_retry_count+1}/{MAX_ATTEMPTS})"
                            )
                            record_llm_failure("chat_stream", response.status_code, "llm_retry", _retry_count + 1, "retry_scheduled", _summary)
                            await asyncio.sleep(_wait)
                            async for _c in self.chat_stream(
                                messages, cancel_event, system_prompt,
                                _continuation_depth=_continuation_depth,
                                _retry_count=_retry_count + 1,
                                _chain=chain, _budget=budget,
                            ):
                                yield _c
                            return
                        record_llm_failure("chat_stream", response.status_code, "llm_give_up", _retry_count + 1, "failed", _summary)
                        raise RuntimeError(
                            chain.fail(_summary, f"LLM API error {response.status_code}: {_err}")
                        )
                    buffer = ""
                    latest_usage = None
                    stream_finished = False
                    _got_done = False
                    async for chunk in response.aiter_bytes():
                        if cancel_event and cancel_event.is_set():
                            logger.info("LLM stream cancelled by event")
                            return
                        buffer += chunk.decode("utf-8", errors="replace")
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                # 流正常收尾：跳出到统一收尾块（usage回填+续写判定）
                                _got_done = True
                                break
                            try:
                                obj = json.loads(data_str)
                                # usage捕获必须在choices判空之前——OpenAI标准流式形态
                                # usage在choices:[]的尾部chunk里
                                _obj_usage = obj.get("usage")
                                if isinstance(_obj_usage, dict) and _obj_usage:
                                    latest_usage = _obj_usage
                                if stream_finished:
                                    # tail模式（finish_reason已处理）：只认usage/[DONE]
                                    continue
                                choices = obj.get("choices", [])
                                if not choices:
                                    continue
                                delta = choices[0].get("delta", {})
                                if delta.get("reasoning_content"):
                                    continue
                                content = delta.get("content")
                                if content:
                                    logger.debug(f"[LLM RAW] content='{content}'")
                                    _segment_parts.append(content)
                                    yield content
                                _fr_raw = choices[0].get("finish_reason")
                                if _fr_raw:
                                    # check_finish_reason单一真源归一（length/stop/unknown）
                                    _last_finish = check_finish_reason(choices[0])
                                if choices[0].get("finish_reason") in ("stop", "tool_calls", "length"):
                                    # 不直接return：finish_reason之后provider可能还有
                                    # trailing usage-only chunk（OpenAI标准usage位置）。
                                    # 单遍消费：httpx流不可二次aiter_bytes（StreamConsumed），
                                    # 同一iterator继续读到[DONE]/流耗尽再结束
                                    stream_finished = True
                            except json.JSONDecodeError:
                                continue
                        if _got_done:
                            break
                    # 流结束统一收尾（[DONE]与流耗尽同路径）：
                    # P1 provider usage回填：plain路径同样回填last_usage
                    # （此前chat_stream全程不消费usage，plain路径校准样本为零）；
                    # P2纯文本截断续写：finish_reason=length→按段续写无缝拼流，
                    # 次数用尽/空段→显式截断标记（不静默半截收尾），已取消不处理
                    self.last_usage = latest_usage
                    _segment_text = "".join(_segment_parts)
                    if _last_finish == "length" and not (cancel_event and cancel_event.is_set()):
                        if (_segment_text.strip() and _continuation_depth < MAX_CONTINUATION
                                and budget.remaining > 0):
                            logger.warning(
                                f"[LLM] 流式finish_reason=length截断，自动续写 "
                                f"(attempt {_continuation_depth+1}/{MAX_CONTINUATION})"
                            )
                            async for _c in self.chat_stream(
                                build_continuation_messages(messages, _segment_text),
                                cancel_event, system_prompt,
                                _continuation_depth=_continuation_depth + 1,
                                _chain=chain, _budget=budget,
                            ):
                                yield _c
                        elif _segment_text.strip() and _continuation_depth < MAX_CONTINUATION:
                            # 续写×重试联合发送预算用尽（utils.llm_safety.TurnSendBudget）：
                            # 显式截断标记，不静默半截收尾（AIHawk"截断必须显式标记"）
                            logger.warning("[LLM] 续写×重试联合发送预算已用尽，输出可能仍不完整")
                            yield "\n\n[输出因max_tokens截断，本回合发送预算已用尽]"
                        else:
                            logger.warning("[LLM] 流式续写次数用尽/空段，输出可能仍不完整")
                            yield "\n\n[输出因max_tokens截断，续写次数已用尽]"
                    return
                finally:
                    await response.aclose()
        except httpx.TransportError as e:
            # 网络离线三态（utils.network_state，kilocode SessionNetwork）：
            # 全局断连→park等恢复（3s轮询探测+10s宽限自动续跑，有界）；恢复/非离线
            # 走下方原有重试记账，aborted也回落原失败契约——原有语义不变只加等待
            await network_state.maybe_park(e, cancel_event)
            # 瞬时传输错误重试决策合并单一真源=plan_error_retry（2026-09-29）：
            # 仅当本attempt尚无任何输出（has_output=_segment_parts非空）——已流出
            # 内容再重试=同一回复发两遍；None=不重试/次数耗尽保持原失败契约
            # （ReadTimeout→"[LLM响应超时]"、其余→"[LLM错误: …]"），失败必须可见
            _wait = plan_error_retry(e, _retry_count, has_output=bool(_segment_parts))
            # 降级链切换决策单点（CowAgent，2026-09-30）：瞬时错误先同link重试，
            # 重试已放弃才换link；has_output=True永不切换（防双份回复）
            _summary = f"{e.__class__.__name__}: {e}"
            if budget.remaining > 0 and chain.try_advance(
                _summary, retry_planned=(_wait is not None),
                has_output=bool(_segment_parts),
            ):
                async for _c in self.chat_stream(
                    messages, cancel_event, system_prompt,
                    _continuation_depth=_continuation_depth,
                    _retry_count=MAX_ATTEMPTS - 1,
                    _chain=chain, _budget=budget,
                ):
                    yield _c
                return
            if _wait is not None and budget.remaining > 0:
                logger.warning(
                    f"[LLM] 传输错误{e.__class__.__name__}，{_wait:.1f}s后重试 "
                    f"({_retry_count+1}/{MAX_ATTEMPTS})"
                )
                record_llm_failure("chat_stream", e, "llm_retry", _retry_count + 1, "retry_scheduled", _summary)
                await asyncio.sleep(_wait)
                async for _c in self.chat_stream(
                    messages, cancel_event, system_prompt,
                    _continuation_depth=_continuation_depth,
                    _retry_count=_retry_count + 1,
                    _chain=chain, _budget=budget,
                ):
                    yield _c
                return
            # 最终失败：单link=原契约逐字不变（"[LLM响应超时]"/"[LLM错误: …]"）；
            # 多link试过=chain.fail列出全部（CowAgent语义3），失败必须可见
            record_llm_failure("chat_stream", e, "llm_give_up", _retry_count + 1, "failed", _summary)
            _fail_msg = chain.fail(_summary, str(e))
            if len(chain.spent) > 1:
                logger.error(f"LLM stream error (降级链耗尽): {_fail_msg}", exc_info=True)
                yield f"\n[LLM错误: {_fail_msg}]"
            elif isinstance(e, httpx.ReadTimeout):
                logger.warning("LLM stream read timeout")
                yield "\n[LLM响应超时]"
            else:
                logger.error(f"LLM stream error: {e}", exc_info=True)
                yield f"\n[LLM错误: {e}]"
        except Exception as e:
            logger.error(f"LLM stream error: {e}", exc_info=True)
            yield f"\n[LLM错误: {e}]"

    async def chat_stream_with_tools(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        cancel_event: Optional[asyncio.Event] = None,
        system_prompt: Optional[str] = None,
        _continuation_depth: int = 0,
        _chain: Optional[FallbackChain] = None,
        _retry_count: int = 0,
        _budget: Optional[TurnSendBudget] = None,
    ) -> AsyncGenerator[str | dict, None]:
        """流式输出LLM响应 — 支持function calling工具调用

        如果启用了ModelRouter，每次请求会根据prompt动态选择最优模型。

        与chat_stream的区别：解析SSE中的tool_calls增量数据，
        按index累积arguments字符串，最终yield完整的tool_calls列表。

        输出防护（P2流式续写，2026-09-28接线，与chat()同一策略单一真源
        utils.llm_safety）：**纯文本**finish_reason=length截断时按段自动续写
        （各段流式无缝拼接，最多MAX_CONTINUATION次；续写段不发射中间usage，
        防消费方对同一逻辑回合双重backfill）。tool_calls截断仍走_truncated标记
        调用方决策（不递归，契约不变）。次数用尽/空段→显式截断标记。

        Args:
            messages: 对话历史（不含system提示词，会自动注入）
            tools: OpenAI function calling格式的工具定义列表
            cancel_event: 取消信号，设置后停止流式输出
            _continuation_depth: 续写递归深度（内部参数，外部调用方勿传）

        Yields:
            str: 普通文本delta片段
            dict: {"usage": {...}} provider权威usage（P1回填，tool_calls之前发射——
                消费方收到tool_calls会break出async for，后到chunk被丢弃，usage必须先行）
            dict: {"tool_calls": [...]} 完整的工具调用列表（仅在LLM请求调用工具时；
                finish_reason=length截断时附"_truncated": True标记）
        """
        # 通过router解析模型（无router时返回固定配置）
        resolved_model, resolved_base_url, resolved_api_key = self._resolve_model(messages)

        # 模型降级有序链（CowAgent chat_fallback，utils.model_fallback，2026-09-30
        # 接线）：opt-in（LLM_FALLBACK_CHAIN未设=空链=行为不变）；sticky——链切换过
        # 则本run停在应答link（chain.current），重试/续写递归携带同一链不弹回primary
        if _chain is None:
            chain = load_fallback_chain(primary_model=resolved_model)
        else:
            chain = _chain
        if chain.current is not None:
            resolved_model = chain.current.model
            resolved_base_url = chain.current.base_url
            resolved_api_key = chain.current.api_key

        # 续写×重试联合发送预算（utils.llm_safety.TurnSendBudget单一真源）：
        # 随重试/续写/降级链切换递归线程传递，一次调用=一个逻辑回合=一个预算
        budget = _budget if _budget is not None else TurnSendBudget()

        # 使用解析后的配置创建客户端
        client = httpx.AsyncClient(
            base_url=resolved_base_url,
            headers={"Authorization": f"Bearer {resolved_api_key}"} if resolved_api_key else {},
            timeout=httpx.Timeout(connect=10, read=120, write=10, pool=10),
        )
        payload = {
            "model": resolved_model,
            "messages": self._build_messages(messages, system_prompt),
            "stream": True,
            "temperature": 0.7,
            "max_tokens": get_max_output_tokens(),
        }
        # OpenAI标准：流式usage默认不发，stream_options.include_usage显式请求
        # （usage回填/校准样本积累的结构性前提；不支持的server报错后永久回退）
        if self._stream_options_ok:
            payload["stream_options"] = {"include_usage": True}
        # 如果提供了工具定义，加入payload
        if tools:
            payload["tools"] = tools
            logger.info(f"[LLM] Sending {len(tools)} tools to {resolved_model}")
        else:
            logger.info(f"[LLM] No tools provided to {resolved_model}")

        # 按index累积tool_calls的arguments（SSE中arguments是增量拼接的）
        accumulated_tool_calls: dict[int, dict] = {}  # index → {id, type, function: {name, arguments}}
        # P2流式续写状态：本段文本累积（段语义）+ 观察到的finish_reason
        _segment_parts: list[str] = []
        _last_finish: str | None = None

        for _retry in range(MAX_ATTEMPTS):
            try:
                # 每次attempt重建client：httpx AsyncClient在async with退出时aclose，
                # 关闭后不可复用（旧代码retry复用已关闭client，二次请求必然失败——
                # "重试"从未真实生效的隐性死路径）
                client = httpx.AsyncClient(
                    base_url=resolved_base_url,
                    headers={"Authorization": f"Bearer {resolved_api_key}"} if resolved_api_key else {},
                    timeout=httpx.Timeout(connect=10, read=120, write=10, pool=10),
                )
                async with client:
                    if not budget.spend():
                        logger.warning("[LLM] 续写×重试联合发送预算已用尽，不再发送")
                        yield "\n[LLM错误: 本回合HTTP发送预算已用尽（续写×重试联合封顶）]"
                        return
                    req = client.build_request("POST", "/chat/completions", json=payload)
                    response = await client.send(req, stream=True)
                    try:
                        if response.status_code != 200:
                            body = await response.aread()
                            error_msg = body.decode()[:300]
                            if "stream_options" in error_msg and self._stream_options_ok:
                                # provider不支持stream_options：永久回退，下一次retry不再携带
                                self._stream_options_ok = False
                                payload.pop("stream_options", None)
                                logger.warning("[LLM] provider不支持stream_options，已回退重试（usage依赖provider默认行为）")
                                continue  # 回到for _retry重试（已去掉不支持的字段）
                            if not is_retryable_status(response.status_code):
                                # 凭证/请求级错误（400/401/403/404等）重试不会自愈：
                                # fail-fast可见失败，不烧3次调用（外层except区分处理）。
                                # 429/408/5xx可重试（utils.llm_retry单一真源，kilocode：
                                # 5xx无条件重试"transient server failures"——旧硬编码
                                # (400,429,500,502,503)把400当可重试却漏501/504等）。
                                # 降级链（CowAgent，2026-09-30）：不可重试状态也换link
                                # （任何link失败都换，llm_transport"402不可重试也换link"
                                # 同款），链上没备胎才fail-fast
                                _summary = f"LLM HTTP {response.status_code}: {error_msg}"
                                if budget.remaining > 0 and chain.try_advance(_summary, retry_planned=False, status=response.status_code):
                                    async for _c in self.chat_stream_with_tools(
                                        messages, tools, cancel_event, system_prompt,
                                        _continuation_depth=_continuation_depth,
                                        _chain=chain, _budget=budget, _retry_count=MAX_ATTEMPTS - 1,
                                    ):
                                        yield _c
                                    return
                                raise LLMNonRetryableError(
                                    chain.fail(_summary, f"LLM API error {response.status_code}: {error_msg}")
                                )
                            # 重试决策合并单一真源=plan_status_retry（2026-09-29）：
                            # 可重试状态下算"等多久后重试"，None=次数耗尽走失败契约。
                            # attempt=_retry_count+_retry：降级链切换进来的link只给1发
                            # （CowAgent link_retries=0，切换递归传_retry_count=
                            # MAX_ATTEMPTS-1=预算见底，首败即换不重试）
                            _wait = plan_status_retry(
                                response.status_code, _retry_count + _retry,
                                getattr(response, "headers", None),
                            )
                            # 降级链切换决策单点（CowAgent，2026-09-30）：429+有备胎
                            # 立即切（switch_now不干等）/重试已放弃换link
                            _summary = f"LLM HTTP {response.status_code}: {error_msg}"
                            if budget.remaining > 0 and chain.try_advance(
                                _summary, retry_planned=(_wait is not None),
                                status=response.status_code,
                            ):
                                async for _c in self.chat_stream_with_tools(
                                    messages, tools, cancel_event, system_prompt,
                                    _continuation_depth=_continuation_depth,
                                    _chain=chain, _budget=budget, _retry_count=MAX_ATTEMPTS - 1,
                                ):
                                    yield _c
                                return
                            if _wait is not None and budget.remaining > 0:
                                # 有头听头：Retry-After三格式（ms头/秒数/HTTP date），
                                # 无头才指数退避2s×2^n封顶30s（旧固定2**_retry不听头）
                                logger.warning(
                                    f"[LLM] API {response.status_code}，{_wait:.1f}s后重试 "
                                    f"({_retry+1}/{MAX_ATTEMPTS}): {error_msg[:100]}"
                                )
                                record_llm_failure("chat_stream_with_tools", response.status_code, "llm_retry", _retry_count + _retry + 1, "retry_scheduled", _summary)
                                await asyncio.sleep(_wait)
                                # 修复：此前误用break——直接退出重试循环且零yield，
                                # 消费方拿到空响应（"瞬时限流→静默空白"死路径）
                                continue  # 回到for _retry重试
                            record_llm_failure("chat_stream_with_tools", response.status_code, "llm_give_up", _retry_count + _retry + 1, "failed", _summary)
                            raise RuntimeError(
                                chain.fail(_summary, f"LLM API error {response.status_code}: {error_msg}")
                            )
                        buffer = ""
                        _stream_finished = False
                        _final_tc_payload = None
                        latest_usage = None
                        _got_done = False
                        async for chunk in response.aiter_bytes():
                            if cancel_event and cancel_event.is_set():
                                logger.info("LLM stream cancelled by event")
                                return
                            buffer += chunk.decode("utf-8", errors="replace")
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                line = line.strip()
                                if not line.startswith("data: "):
                                    continue
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    # 流正常收尾：跳出到统一收尾块（与流耗尽同路径，
                                    # 单遍消费不二次aiter_bytes）
                                    _got_done = True
                                    break
                                try:
                                    obj = json.loads(data_str)
                                    # P1 provider usage回填：usage捕获必须在choices判空之前
                                    # ——OpenAI标准流式形态usage在choices:[]的尾部chunk里，
                                    # 上轮opensoul侧live实测token-plan亦如此（usage-only chunk）
                                    _obj_usage = obj.get("usage")
                                    if isinstance(_obj_usage, dict) and _obj_usage:
                                        latest_usage = _obj_usage
                                    if _stream_finished:
                                        # tail模式（finish_reason已处理）：只认usage/[DONE]，
                                        # 不再产出content/tool_calls（单遍消费，不二次aiter_bytes）
                                        continue
                                    choices = obj.get("choices", [])
                                    if not choices:
                                        continue
                                    choice = choices[0]
                                    delta = choice.get("delta", {})
                                    if delta.get("reasoning_content"):
                                        continue
                                    content = delta.get("content")
                                    if content:
                                        logger.debug(f"[LLM RAW] content='{content}'")
                                        _segment_parts.append(content)
                                        yield content
                                    _fr_raw = choice.get("finish_reason")
                                    if _fr_raw:
                                        # check_finish_reason单一真源归一
                                        _last_finish = check_finish_reason(choice)
                                    tool_calls_delta = delta.get("tool_calls") or []
                                    for tc_delta in tool_calls_delta:
                                        idx = tc_delta.get("index", 0)
                                        if idx not in accumulated_tool_calls:
                                            accumulated_tool_calls[idx] = {
                                                "id": tc_delta.get("id", ""),
                                                "type": tc_delta.get("type", "function"),
                                                "function": {
                                                    "name": "",
                                                    "arguments": "",
                                                },
                                            }
                                        if tc_delta.get("id"):
                                            accumulated_tool_calls[idx]["id"] = tc_delta["id"]
                                        func_delta = tc_delta.get("function", {})
                                        if func_delta.get("name"):
                                            accumulated_tool_calls[idx]["function"]["name"] += func_delta["name"]
                                        if func_delta.get("arguments"):
                                            accumulated_tool_calls[idx]["function"]["arguments"] += func_delta["arguments"]
                                            logger.debug(f"[LLM STREAM] tc[{idx}] args += {len(func_delta['arguments'])} chars, total={len(accumulated_tool_calls[idx]['function']['arguments'])}")
                                    if choice.get("finish_reason") in ("stop", "tool_calls", "length"):
                                        if accumulated_tool_calls:
                                            # 检查是否有不完整的tool_calls（finish_reason=length导致截断）
                                            incomplete = False
                                            for _i, _tc in accumulated_tool_calls.items():
                                                _args = _tc["function"]["arguments"]
                                                if not _args:
                                                    logger.warning(f"[LLM] tool_call args为空: name={_tc['function']['name']}")
                                                    incomplete = True
                                                else:
                                                    try:
                                                        json.loads(_args)
                                                    except json.JSONDecodeError:
                                                        logger.warning(f"[LLM] tool_call args不完整(截断): name={_tc['function']['name']}, len={len(_args)}")
                                                        incomplete = True
                                            _tc_list = [
                                                accumulated_tool_calls[i]
                                                for i in sorted(accumulated_tool_calls.keys())
                                            ]
                                            if incomplete and choice.get("finish_reason") == "length":
                                                # max_tokens导致截断：不递归调用，带_truncated标记返回，
                                                # 调用方按标记决定重试策略（提示模型用execute_code
                                                # 写完整文件，不要用write_file）
                                                logger.info(f"[LLM] finish_reason=length, tool_calls不完整, 返回_truncated标记")
                                                _final_tc_payload = {"tool_calls": _tc_list, "_truncated": True}
                                            else:
                                                _final_tc_payload = {"tool_calls": _tc_list}
                                        # 不在此处直接return：finish_reason之后provider可能还有
                                        # trailing usage-only chunk（OpenAI标准流式usage所在位置）。
                                        # 单遍消费：设tail模式后同一aiter_bytes iterator继续读到
                                        # [DONE]/流耗尽——httpx流不可二次迭代（StreamConsumed），
                                        # 旧"break后二次排空"分支对真实httpx是死代码（live 0/5复现，
                                        # usage在finish_reason之后=结构性丢失→backfill零样本）
                                        _stream_finished = True
                                        continue  # tail模式：继续解析缓冲区/后续字节，只认usage/[DONE]
                                except json.JSONDecodeError:
                                    continue
                            if _got_done:
                                break
                        # 流结束统一收尾（[DONE]与流耗尽同路径，非标准provider无
                        # finish_reason/[DONE]同样覆盖）。发射顺序：usage先于tool_calls
                        # ——消费方（soulmate_agent）收到tool_calls后break出async for，
                        # 后到chunk会被丢弃。P2纯文本截断续写判定必须在usage发射之前：
                        # 续写不发射中间段usage（防消费方对同一逻辑回合双重backfill），
                        # 最终段usage随递归自然发射
                        self.last_usage = latest_usage
                        if (_final_tc_payload is None and not accumulated_tool_calls
                                and _last_finish == "length"
                                and not (cancel_event and cancel_event.is_set())):
                            _seg = "".join(_segment_parts)
                            if (_seg.strip() and _continuation_depth < MAX_CONTINUATION
                                    and budget.remaining > 0):
                                logger.warning(
                                    f"[LLM] 流式finish_reason=length纯文本截断，自动续写 "
                                    f"(attempt {_continuation_depth+1}/{MAX_CONTINUATION})"
                                )
                                async for _c in self.chat_stream_with_tools(
                                    build_continuation_messages(messages, _seg),
                                    tools, cancel_event, system_prompt,
                                    _continuation_depth=_continuation_depth + 1,
                                    _chain=chain, _budget=budget,
                                ):
                                    yield _c
                                return
                            if _seg.strip() and _continuation_depth < MAX_CONTINUATION:
                                # 续写×重试联合发送预算用尽（utils.llm_safety.TurnSendBudget）：
                                # 显式截断标记，不静默半截收尾（AIHawk"截断必须显式标记"）
                                logger.warning("[LLM] 续写×重试联合发送预算已用尽，输出可能仍不完整")
                                yield "\n\n[输出因max_tokens截断，本回合发送预算已用尽]"
                            else:
                                logger.warning("[LLM] 流式续写次数用尽/空段，输出可能仍不完整")
                                yield "\n\n[输出因max_tokens截断，续写次数已用尽]"
                        if latest_usage:
                            yield {"usage": latest_usage}
                        if _final_tc_payload is not None:
                            yield _final_tc_payload
                        elif accumulated_tool_calls:
                            yield {"tool_calls": [
                                accumulated_tool_calls[i]
                                for i in sorted(accumulated_tool_calls.keys())
                            ]}
                        return
                    finally:
                        await response.aclose()
                break  # 成功，退出重试循环
            except httpx.TransportError as e:
                # 网络离线三态（utils.network_state，kilocode SessionNetwork）：
                # 全局断连→park等恢复（3s轮询探测+10s宽限自动续跑，有界）；恢复/
                # 非离线走下方原有重试记账，aborted回落原失败契约——语义不变只加等待
                await network_state.maybe_park(e, cancel_event)
                # 瞬时传输错误重试决策合并单一真源=plan_error_retry（2026-09-29）：
                # 仅当本attempt零输出（文本与tool_calls均未累积，has_output）——已流出/
                # 已累积再重试=双份回复或tool_calls状态串attempt污染；None=保持原失败契约。
                # attempt=_retry_count+_retry：切换进来的link只给1发（CowAgent）
                _has_out = bool(_segment_parts) or bool(accumulated_tool_calls)
                _wait = plan_error_retry(
                    e, _retry_count + _retry,
                    has_output=_has_out,
                )
                # 降级链切换决策单点（CowAgent，2026-09-30）：瞬时错误先同link重试，
                # 重试已放弃才换link；has_output=True永不切换（防双份回复/状态污染）
                _summary = f"{e.__class__.__name__}: {e}"
                if budget.remaining > 0 and chain.try_advance(
                    _summary, retry_planned=(_wait is not None), has_output=_has_out,
                ):
                    async for _c in self.chat_stream_with_tools(
                        messages, tools, cancel_event, system_prompt,
                        _continuation_depth=_continuation_depth,
                        _chain=chain, _budget=budget, _retry_count=MAX_ATTEMPTS - 1,
                    ):
                        yield _c
                    return
                if _wait is not None and budget.remaining > 0:
                    logger.warning(
                        f"[LLM] 传输错误{e.__class__.__name__}，{_wait:.1f}s后重试 "
                        f"({_retry+1}/{MAX_ATTEMPTS})"
                    )
                    record_llm_failure("chat_stream_with_tools", e, "llm_retry", _retry + 1, "retry_scheduled", _summary)
                    await asyncio.sleep(_wait)
                    continue
                # 最终失败：单link=原契约逐字不变；多link试过=列出全部（CowAgent语义3）
                record_llm_failure("chat_stream_with_tools", e, "llm_give_up", _retry + 1, "failed", _summary)
                _fail_msg = chain.fail(_summary, str(e))
                if len(chain.spent) > 1:
                    logger.error(f"LLM stream error (降级链耗尽): {_fail_msg}", exc_info=True)
                    yield f"\n[LLM错误: {_fail_msg}]"
                elif isinstance(e, httpx.ReadTimeout):
                    logger.warning("LLM stream read timeout")
                    yield "\n[LLM响应超时]"
                else:
                    logger.error(f"LLM stream error: {e}", exc_info=True)
                    yield f"\n[LLM错误: {e}]"
                return
            except LLMNonRetryableError as e:
                # 401/403/404等：重试不会自愈，直接可见失败（fail-fast）
                logger.error(f"LLM non-retryable error: {e}")
                record_llm_failure("chat_stream_with_tools", e, "llm_give_up", 1, "failed")
                yield f"\n[LLM错误: {e}]"
                return
            except RuntimeError as e:
                # 旧"LLM API error"catch-retry二次重试分支已删（2026-09-30，前轮遗留
                # 评估销账）：实际dead path——RuntimeError只在plan_*放弃时raise，此时
                # _retry已到预算末尾，_retry<MAX_ATTEMPTS-1恒False；且降级链bias下若
                # 误触发会对已失败link反复重发，绕过"切换进来的link只给1发"。失败可见
                # 契约=下方log+yield（mem0：失败必须可见，不静默吞不盲目重发）
                logger.error(f"LLM stream error: {e}", exc_info=True)
                yield f"\n[LLM错误: {e}]"
                return
            except Exception as e:
                logger.error(f"LLM stream error: {e}", exc_info=True)
                yield f"\n[LLM错误: {e}]"

    async def chat(
        self, messages: list[dict], _budget: Optional[TurnSendBudget] = None,
    ) -> str:
        """非流式完整输出 — 等待完整响应后返回（带输出防护）

        如果启用了ModelRouter，会根据prompt动态选择最优模型。

        输出防护（2026-09-28接线，策略单一真源=utils.llm_safety，此前只存在于
        llm_gateway/llm_safety死路径、本真实传输裸奔）：
        1. finish_reason=length自动续写（Aider方案，最多MAX_CONTINUATION次，
           各段拼接返回——此前静默截断，judge JSON/计划/摘要半截即废）
        2. omission占位符检测+整体重新生成（Gemini CLI方案）
        续写×重试联合封顶（utils.llm_safety.TurnSendBudget单一真源，2026-10-01）：
        续写/重发轮与HTTP重试共享同一逻辑回合发送预算（MAX_TURN_SENDS发硬顶），
        余额用尽=返回已累积输出（log显式警告）或可见失败，不静默不多发。
        失败契约不变：传输/HTTP错误返回"[LLM错误: …]"（失败必须可见，不吞）。
        """
        # 通过router解析模型（无router时返回固定配置）
        resolved_model, resolved_base_url, resolved_api_key = self._resolve_model(messages)

        # 模型降级有序链（CowAgent chat_fallback，utils.model_fallback，2026-09-30
        # 接线）：opt-in（LLM_FALLBACK_CHAIN未设=空链=行为不变）；续写轮共享同一链
        # =sticky（切到备胎后续写轮停在应答link，不弹回primary）
        chain = load_fallback_chain(primary_model=resolved_model)

        # 续写×重试联合发送预算（utils.llm_safety.TurnSendBudget单一真源）：
        # chat()不递归，续写/重发轮+HTTP重试+降级链walk共享本预算
        budget = _budget if _budget is not None else TurnSendBudget()

        client = httpx.AsyncClient(
            base_url=resolved_base_url,
            headers={"Authorization": f"Bearer {resolved_api_key}"} if resolved_api_key else {},
            timeout=httpx.Timeout(connect=10, read=120, write=10, pool=10),
        )
        current_history = list(messages)  # 不含system（每次请求由_build_messages统一注入）
        full_content = ""
        try:
            async with client:
                for attempt in range(MAX_CONTINUATION + 1):
                    payload = {
                        # sticky（CowAgent use_fallback）：链切换过的续写轮停在应答link
                        "model": chain.current.model if chain.current is not None else resolved_model,
                        "messages": self._build_messages(current_history),
                        "stream": False,
                        "temperature": 0.7,
                        "max_tokens": get_max_output_tokens(),
                    }
                    # HTTP重试（策略单一真源utils.llm_retry，kilocode retry.ts）：
                    # 429/408/5xx听Retry-After可重试、瞬时网络错误重试、400/401/402等
                    # 请求/凭证级fail-fast——接线前chat()零HTTP重试，瞬时429/5xx直接
                    # "[LLM错误: …]"整体失败（judge/计划/摘要/标题全走本方法）
                    resp = None
                    # 降级链walk（CowAgent，2026-09-30）：内层for=单link重试环，
                    # 失败且链上有备胎→try_advance换link重发（切换进来的link只给1发，
                    # plan_*按attempt偏移消费同一预算语义）；无链时try_advance恒False
                    # =while只走一遍、行为与接线前逐字一致
                    _is_start_link = True
                    while True:
                        _link_ok = False
                        for _http_retry in range(MAX_ATTEMPTS):
                            try:
                                if not budget.spend():
                                    logger.warning("[LLM] 续写×重试联合发送预算已用尽，不再发送")
                                    raise RuntimeError(
                                        "本回合HTTP发送预算已用尽（续写×重试联合封顶）"
                                    )
                                # 无流停顿检测（12.4①，utils.stall_guard）：2026-10-11
                                # 事故实证——本调用上游挂死 285s 零字节而 httpx read=120
                                # 超时未开火（websockets serve 循环超时盲区），独立看门狗
                                # 按字节间隙计时中止本次尝试→上层重试环照常重试（8s 可恢复）
                                resp = await send_with_stall_guard(
                                    client, "POST", "/chat/completions", json=payload
                                )
                            except httpx.TransportError as e:
                                # 网络离线三态（utils.network_state，kilocode SessionNetwork）：
                                # 全局断连→park等恢复（3s竞速探测+10s宽限自动续跑，有界）；
                                # 恢复/非离线走下方原有重试记账（chat()无cancel_event）
                                await network_state.maybe_park(e)
                                # 瞬时传输错误重试决策合并单一真源=plan_error_retry
                                # （2026-09-29）：chat()非流式无中途输出，has_output恒False
                                _wait = plan_error_retry(
                                    e, _http_retry if _is_start_link else MAX_ATTEMPTS - 1
                                )
                                if budget.remaining > 0 and chain.try_advance(
                                    f"{e.__class__.__name__}: {e}",
                                    retry_planned=(_wait is not None),
                                ):
                                    self._apply_link(client, payload, chain.current)
                                    break  # 换link重发（外层while）
                                if _wait is not None and budget.remaining > 0:
                                    logger.warning(
                                        f"[LLM] 传输错误{e.__class__.__name__}，"
                                        f"{_wait:.1f}s后重试 ({_http_retry+1}/{MAX_ATTEMPTS})"
                                    )
                                    record_llm_failure("chat", e, "llm_retry", _http_retry + 1, "retry_scheduled", f"{e.__class__.__name__}: {e}")
                                    await asyncio.sleep(_wait)
                                    continue
                                record_llm_failure("chat", e, "llm_give_up", _http_retry + 1, "failed", f"{e.__class__.__name__}: {e}")
                                raise RuntimeError(
                                    chain.fail(f"{e.__class__.__name__}: {e}", str(e))
                                ) from e
                            if resp.status_code == 200:
                                _link_ok = True
                                break
                            # 重试决策合并单一真源=plan_status_retry（2026-09-29）：可重试∧
                            # 未耗尽→等多久后重试；None=不重试/次数耗尽走失败契约
                            _wait = plan_status_retry(
                                resp.status_code,
                                _http_retry if _is_start_link else MAX_ATTEMPTS - 1,
                                getattr(resp, "headers", None),
                            )
                            _summary = f"LLM HTTP {resp.status_code}: {resp.text[:200]}"
                            if budget.remaining > 0 and chain.try_advance(
                                _summary, retry_planned=(_wait is not None),
                                status=resp.status_code,
                            ):
                                self._apply_link(client, payload, chain.current)
                                break  # 换link重发（外层while）
                            if _wait is None or budget.remaining <= 0:
                                record_llm_failure("chat", resp.status_code, "llm_give_up", _http_retry + 1, "failed", _summary)
                                raise RuntimeError(
                                    chain.fail(
                                        _summary,
                                        f"LLM API error {resp.status_code}: {resp.text[:200]}",
                                    )
                                )
                            # 有头听头：Retry-After三格式（ms头/秒数/HTTP date），无头才指数退避
                            logger.warning(
                                f"[LLM] API {resp.status_code}，{_wait:.1f}s后重试 "
                                f"({_http_retry+1}/{MAX_ATTEMPTS})"
                            )
                            record_llm_failure("chat", resp.status_code, "llm_retry", _http_retry + 1, "retry_scheduled", _summary)
                            await asyncio.sleep(_wait)
                        if _link_ok:
                            break
                        _is_start_link = False
                    if resp is None:  # 防御性收窄：循环break时resp必已赋值
                        raise RuntimeError("LLM API error: 重试环退出但无响应")
                    data = resp.json()
                    # P1 provider usage回填：非流式响应的权威usage同样写入last_usage
                    _nusage = data.get("usage")
                    if isinstance(_nusage, dict) and _nusage:
                        self.last_usage = _nusage
                    choice = data.get("choices", [{}])[0]
                    segment = (choice.get("message", {}) or {}).get("content") or ""
                    full_content += segment

                    # 防护1：omission占位符→整体重新生成（Gemini CLI方案）
                    omission = check_omission(full_content)
                    if omission:
                        if budget.remaining <= 0:
                            # 续写×重试联合发送预算用尽：保留已累积输出（log显式警告）
                            logger.warning("[LLM] omission重发×续写联合发送预算已用尽，返回已累积输出（可能仍不完整）")
                            break
                        logger.warning(
                            f"[LLM] {omission}，整体重新生成 "
                            f"(attempt {attempt+1}/{MAX_CONTINUATION+1})"
                        )
                        current_history = build_continuation_messages(
                            current_history, segment, error_hint=omission
                        )
                        full_content = ""  # 重置：要求从头完整重出
                        continue

                    # 防护2：finish_reason=length→续写（Aider方案）
                    if check_finish_reason(choice) == "length":
                        if budget.remaining <= 0:
                            # 续写×重试联合发送预算用尽：保留已累积输出（log显式警告）
                            logger.warning("[LLM] 续写×重试联合发送预算已用尽，返回已累积输出（可能仍不完整）")
                            break
                        logger.warning(
                            f"[LLM] finish_reason=length截断，自动续写 "
                            f"(attempt {attempt+1}/{MAX_CONTINUATION+1})"
                        )
                        current_history = build_continuation_messages(current_history, segment)
                        continue

                    break  # 正常结束（stop/未知finish_reason按结束处理）
                else:
                    logger.warning("[LLM] 续写/重发次数用尽，返回已累积输出（可能仍不完整）")
        except Exception as e:
            logger.error(f"LLM chat error: {e}", exc_info=True)
            return f"[LLM错误: {e}]"
        return full_content

    def _build_messages(self, history: list[dict], system_prompt: str = None) -> list[dict]:
        """构建OpenAI格式消息数组 — 注入system提示词

        Args:
            history: 会话历史消息
            system_prompt: 自定义system prompt，None时使用默认
        """
        result = [{"role": "system", "content": system_prompt or self.system_prompt}]
        result.extend(history)
        # Debug: log message roles and content preview
        for i, m in enumerate(result):
            role = m.get("role", "?")
            content = m.get("content", "")
            tc = m.get("tool_calls")
            preview = str(content)[:100] if content else "None"
            logger.info(f"[MSG {i}] role={role} content={preview} tool_calls={'yes' if tc else 'no'}")
        return result

    def _truncate_context(self, messages: list[dict], max_tokens: int = 32000) -> list[dict]:
        """上下文窗口裁剪 — 保留system+最近10轮，中间做摘要

        简化估算：中文1字≈2token，英文1词≈1.3token
        """
        if not messages:
            return messages

        # 估算总token数
        total_chars = sum(len(m.get("content", "")) for m in messages)
        estimated_tokens = total_chars * 1.5  # 粗略估算

        if estimated_tokens <= max_tokens:
            return messages

        # 保留最近10条消息
        keep_count = 10
        if len(messages) <= keep_count:
            return messages

        old_messages = messages[:-keep_count]
        recent_messages = messages[-keep_count:]

        # 将旧消息压缩为一条摘要
        summary_parts = []
        for m in old_messages:
            role = m.get("role", "unknown")
            content = m.get("content", "")[:100]
            summary_parts.append(f"[{role}]: {content}...")
        summary = "之前的对话摘要：\n" + "\n".join(summary_parts)

        return [{"role": "system", "content": summary}] + recent_messages

    async def close(self):
        """关闭引擎（客户端已不缓存，无需显式关闭）"""
        pass
