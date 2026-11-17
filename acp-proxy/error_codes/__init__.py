"""ErrorCode v1.0 全局统一错误码注册表 — 对齐 ErrorCode v1.0 规范。

四层域隔离：
- -326xx: RPC协议标准错误（全域共用）
- -320xx: ACP人机会话域
- -321xx: A2A多Agent协作域
- -327xx: MCP系统管控域

用法：
    from error_codes import ErrorCode, make_error
    error = make_error(ErrorCode.ACP_SESSION_NOT_FOUND, session_id="sess-xxx")
    # 返回标准错误结构：code/message/desc/retryable/data
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 错误码枚举注册表
# ---------------------------------------------------------------------------

class ErrorCode:
    """全局统一错误码常量注册表。

    按域分段，永不重叠。新增错误只能在对应域增量追加。
    """

    # ── RPC协议标准错误（-326xx）全域共用 ──
    RPC_INVALID_REQUEST = -32600         # JSON-RPC请求格式非法
    RPC_METHOD_NOT_FOUND = -32601        # 调用的method未定义
    RPC_INVALID_PARAMS = -32602          # 参数缺失/类型错误/超出范围
    RPC_INTERNAL_ERROR = -32603          # 服务未知内部报错

    # ── ACP人机会话域（-320xx）──
    # 会话生命周期
    ACP_AUTH_FAILED = -32001             # 鉴权失败
    ACP_SESSION_NOT_FOUND = -32002       # 会话不存在或已过期
    ACP_AGENT_CRASHED = -32003           # Agent进程异常
    ACP_APPROVAL_REJECTED = -32004       # 审批被拒绝
    ACP_SESSION_TIMEOUT = -32005         # 会话任务超时

    # 工具&技能执行
    ACP_TOOL_TIMEOUT = -32010            # 工具执行超时
    ACP_TOOL_PERMISSION_DENIED = -32011  # 工具权限不足
    ACP_HIGH_RISK_BLOCKED = -32012       # 高危操作被拦截

    # ── A2A多Agent协作域（-321xx）──
    # 任务调度
    A2A_TASK_NOT_FOUND = -32101          # 任务不存在
    A2A_DELEGATION_FAILED = -32102       # 任务委派失败
    A2A_SUBTASK_FAILED = -32103          # 子任务执行失败
    A2A_TASK_CANCELLED = -32104          # 任务已取消

    # Artifact同步
    A2A_ARTIFACT_SYNC_FAILED = -32120    # 工件同步失败
    A2A_ARTIFACT_NOT_FOUND = -32121      # 工件不存在
    A2A_ARTIFACT_PERMISSION_DENIED = -32122  # 工件权限拒绝

    # ── MCP系统管控域（-327xx）──
    # 进程&资源管控
    MCP_PROCESS_START_FAILED = -32701    # 进程启动失败
    MCP_PROCESS_RESTART_FAILED = -32702  # 进程重启失败
    MCP_RESOURCE_QUOTA_EXCEEDED = -32703 # 资源配额超限

    # 配置管控
    MCP_CONFIG_INVALID = -32710          # 配置格式非法
    MCP_CONFIG_HOT_RELOAD_FAILED = -32711  # 配置热更新失败
    MCP_CONFIG_READONLY = -32712         # 配置项只读禁止修改

    # ── LLM供应商域（-322xx）──（Pi #15 增量追加：供应商错误文案兼容层的归一目标。
    # 重试决策用本域结构化码（is_retryable），不用供应商文案正则——抄
    # piRetryErrorNormalizer 功能不抄机制，映射入口 error_codes/vendor_compat.py）
    LLM_RATE_LIMITED = -32201            # 限流/并发超限（可重试）
    LLM_TIMEOUT = -32202                 # 响应/读取超时（可重试）
    LLM_SERVER_ERROR = -32203            # 供应商服务端故障（可重试）
    LLM_NETWORK_ERROR = -32204           # 传输层网络故障（可重试）
    LLM_AUTH_FAILED = -32205             # API key 无效/凭证级失败（不可重试）
    LLM_QUOTA_EXCEEDED = -32206          # 额度/账单耗尽（不可重试，需人工）
    LLM_INVALID_REQUEST = -32207         # 请求级错误：参数非法/上下文超长（不可重试）
    LLM_EMPTY_OUTPUT = -32208            # HTTP 200 空输出（可重试）
    LLM_CONTENT_FILTERED = -32209        # 内容安全过滤拒绝（不可重试）
    LLM_UNKNOWN = -32299                 # 未识别的供应商错误（不可自动重试，人工看 desc）


# ---------------------------------------------------------------------------
# 标准错误结构
# ---------------------------------------------------------------------------

@dataclass
class OpenSoulError:
    """标准错误结构 — 所有报错必须使用该结构输出。

    字段说明：
    - code: 全局唯一数字错误码
    - message: 短文案（前端弹窗/展示）
    - desc: 详细技术描述（日志/排查/开发阅读）
    - retryable: 是否可自动重试
    - data: 错误上下文、非法字段、超时时间等
    """
    code: int
    message: str
    desc: str
    retryable: bool
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为JSON-RPC error结构。"""
        return {
            "code": self.code,
            "message": self.message,
            "desc": self.desc,
            "retryable": self.retryable,
            "data": self.data,
        }

    def to_jsonrpc_error(self) -> dict[str, Any]:
        """包装为JSON-RPC 2.0 error响应。"""
        return {"jsonrpc": "2.0", "id": None, "error": self.to_dict()}


# ---------------------------------------------------------------------------
# 错误码 → 默认文案映射表
# ---------------------------------------------------------------------------

_ERROR_REGISTRY: dict[int, dict[str, Any]] = {
    # RPC标准
    ErrorCode.RPC_INVALID_REQUEST: {"message": "无效请求", "desc": "JSON-RPC请求格式非法", "retryable": False},
    ErrorCode.RPC_METHOD_NOT_FOUND: {"message": "方法不存在", "desc": "调用的method未定义", "retryable": False},
    ErrorCode.RPC_INVALID_PARAMS: {"message": "参数非法", "desc": "请求参数缺失或格式不合法", "retryable": False},
    ErrorCode.RPC_INTERNAL_ERROR: {"message": "服务内部异常", "desc": "服务未知内部报错", "retryable": True},

    # ACP会话
    ErrorCode.ACP_AUTH_FAILED: {"message": "鉴权失败", "desc": "会话Token/身份校验不通过", "retryable": False},
    ErrorCode.ACP_SESSION_NOT_FOUND: {"message": "会话不存在或已过期", "desc": "session_id无效、已关闭或超时销毁", "retryable": False},
    ErrorCode.ACP_AGENT_CRASHED: {"message": "Agent进程异常", "desc": "会话绑定Agent崩溃、断开或未启动", "retryable": True},
    ErrorCode.ACP_APPROVAL_REJECTED: {"message": "审批被拒绝", "desc": "人工审批拒绝，任务终止", "retryable": False},
    ErrorCode.ACP_SESSION_TIMEOUT: {"message": "会话任务超时", "desc": "会话长时间无响应、任务超时终止", "retryable": False},

    # ACP工具
    ErrorCode.ACP_TOOL_TIMEOUT: {"message": "工具执行超时", "desc": "Tool运行超出timeout_ms", "retryable": True},
    ErrorCode.ACP_TOOL_PERMISSION_DENIED: {"message": "工具权限不足", "desc": "permission_scope不匹配", "retryable": False},
    ErrorCode.ACP_HIGH_RISK_BLOCKED: {"message": "高危操作被拦截", "desc": "未审批直接调用高危工具被网关拦截", "retryable": False},

    # A2A任务
    ErrorCode.A2A_TASK_NOT_FOUND: {"message": "任务不存在", "desc": "task_id不存在或已结束", "retryable": False},
    ErrorCode.A2A_DELEGATION_FAILED: {"message": "任务委派失败", "desc": "目标Agent离线或未就绪", "retryable": True},
    ErrorCode.A2A_SUBTASK_FAILED: {"message": "子任务执行失败", "desc": "子Agent任务异常终止", "retryable": False},
    ErrorCode.A2A_TASK_CANCELLED: {"message": "任务已取消", "desc": "任务被主动终止", "retryable": False},

    # A2A工件
    ErrorCode.A2A_ARTIFACT_SYNC_FAILED: {"message": "工件同步失败", "desc": "A2A artifact跨Agent同步失败", "retryable": True},
    ErrorCode.A2A_ARTIFACT_NOT_FOUND: {"message": "工件不存在", "desc": "artifact_id无效或已删除", "retryable": False},
    ErrorCode.A2A_ARTIFACT_PERMISSION_DENIED: {"message": "工件权限拒绝", "desc": "无权限读取跨Agent产物", "retryable": False},

    # MCP进程
    ErrorCode.MCP_PROCESS_START_FAILED: {"message": "进程启动失败", "desc": "Agent进程拉起失败", "retryable": True},
    ErrorCode.MCP_PROCESS_RESTART_FAILED: {"message": "进程重启失败", "desc": "热重启/重启流程异常", "retryable": True},
    ErrorCode.MCP_RESOURCE_QUOTA_EXCEEDED: {"message": "资源配额超限", "desc": "内存/磁盘/并发数超限", "retryable": False},

    # MCP配置
    ErrorCode.MCP_CONFIG_INVALID: {"message": "配置格式非法", "desc": "配置JSON/YAML解析失败", "retryable": False},
    ErrorCode.MCP_CONFIG_HOT_RELOAD_FAILED: {"message": "配置热更新失败", "desc": "动态配置加载不生效", "retryable": True},
    ErrorCode.MCP_CONFIG_READONLY: {"message": "配置项只读禁止修改", "desc": "系统核心配置禁止变更", "retryable": False},

    # LLM供应商（Pi #15：retryable 即结构化重试判据——供应商文案只在
    # vendor_compat 归一时消费一次，之后决策只看码）
    ErrorCode.LLM_RATE_LIMITED: {"message": "模型限流", "desc": "供应商限流/并发超限，可退避重试", "retryable": True},
    ErrorCode.LLM_TIMEOUT: {"message": "模型超时", "desc": "供应商响应/读取超时，可重试", "retryable": True},
    ErrorCode.LLM_SERVER_ERROR: {"message": "模型服务故障", "desc": "供应商服务端 5xx/过载，可重试", "retryable": True},
    ErrorCode.LLM_NETWORK_ERROR: {"message": "模型网络故障", "desc": "到供应商的传输层故障（连接/读/协议），可重试", "retryable": True},
    ErrorCode.LLM_AUTH_FAILED: {"message": "模型鉴权失败", "desc": "API key 无效/被撤销——凭证级，重试无益", "retryable": False},
    ErrorCode.LLM_QUOTA_EXCEEDED: {"message": "模型额度耗尽", "desc": "账单/配额不足——需人工充值或换端点", "retryable": False},
    ErrorCode.LLM_INVALID_REQUEST: {"message": "模型请求非法", "desc": "参数非法/上下文超长等请求级错误——改请求后才能成功", "retryable": False},
    ErrorCode.LLM_EMPTY_OUTPUT: {"message": "模型空输出", "desc": "HTTP 200 但输出为空——独立失败形态，可重试", "retryable": True},
    ErrorCode.LLM_CONTENT_FILTERED: {"message": "内容被过滤", "desc": "供应商内容安全策略拒绝——重试无益", "retryable": False},
    ErrorCode.LLM_UNKNOWN: {"message": "模型未知错误", "desc": "未识别的供应商错误——人工检查 desc/data 后处理", "retryable": False},
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def make_error(code: int, **extra_data: Any) -> OpenSoulError:
    """根据错误码创建标准错误对象。

    用法：
        error = make_error(ErrorCode.ACP_SESSION_NOT_FOUND, session_id="sess-xxx")
    """
    info = _ERROR_REGISTRY.get(code, {
        "message": f"未知错误({code})",
        "desc": "未注册的错误码",
        "retryable": False,
    })
    return OpenSoulError(
        code=code,
        message=info["message"],
        desc=info["desc"],
        retryable=info["retryable"],
        data=extra_data if extra_data else {},
    )


def is_retryable(code: int) -> bool:
    """查询错误码是否可重试。"""
    info = _ERROR_REGISTRY.get(code)
    return info["retryable"] if info else False


def get_error_info(code: int) -> dict[str, Any] | None:
    """查询错误码注册信息。"""
    return _ERROR_REGISTRY.get(code)


def list_error_codes(domain_prefix: int = 0) -> list[dict[str, Any]]:
    """列出错误码，可按域前缀过滤。"""
    result = []
    for code, info in _ERROR_REGISTRY.items():
        if domain_prefix and not str(code).startswith(str(domain_prefix)):
            continue
        result.append({"code": code, **info})
    return result
