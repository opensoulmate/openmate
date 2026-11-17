"""
输出格式验证器 — 借鉴Guardrails AI/Outlines/Instructor
核心思想：LLM输出必须符合预定义schema，自动验证+修复+重新生成
"""

import logging
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Callable
from enum import Enum

logger = logging.getLogger("acp-proxy.output-validator")


class ValidationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    repaired_output: Any = None
    original_output: Any = None

    def add_error(self, message: str, path: str = "", severity: ValidationSeverity = ValidationSeverity.ERROR):
        self.errors.append({"message": message, "path": path, "severity": severity.value})
        self.is_valid = False

    def add_warning(self, message: str, path: str = ""):
        self.warnings.append({"message": message, "path": path})


@dataclass
class SchemaField:
    name: str
    type: str  # "string", "number", "boolean", "array", "object"
    required: bool = True
    default: Any = None
    validator: Optional[Callable] = None
    description: str = ""


class OutputValidator:
    """输出格式验证器"""

    def __init__(self):
        self._stats = {"total_validations": 0, "passed": 0, "failed": 0, "repaired": 0}

    def validate_json(self, output: str, schema: dict) -> ValidationResult:
        """验证JSON输出"""
        self._stats["total_validations"] += 1
        result = ValidationResult(is_valid=True, original_output=output)

        # 1. 尝试解析JSON
        parsed = self._extract_json(output)
        if parsed is None:
            result.add_error("Output is not valid JSON", severity=ValidationSeverity.CRITICAL)
            self._stats["failed"] += 1
            return result

        # 2. 验证schema
        self._validate_against_schema(parsed, schema, result)

        # 3. 尝试修复
        if not result.is_valid:
            repaired = self._try_repair(parsed, schema)
            if repaired:
                result.repaired_output = repaired
                self._stats["repaired"] += 1
                logger.info(f"Output repaired: {len(result.errors)} errors fixed")

        if result.is_valid:
            self._stats["passed"] += 1
        else:
            self._stats["failed"] += 1

        return result

    def validate_tool_call(self, output: str, tool_schema: dict) -> ValidationResult:
        """验证工具调用输出"""
        self._stats["total_validations"] += 1
        result = ValidationResult(is_valid=True, original_output=output)

        # 提取工具调用
        tool_call = self._extract_tool_call(output)
        if not tool_call:
            result.add_error("No tool call found in output")
            self._stats["failed"] += 1
            return result

        # 验证工具名
        expected_name = tool_schema.get("name", "")
        if expected_name and tool_call.get("name") != expected_name:
            result.add_error(
                f"Tool name mismatch: expected {expected_name}, got {tool_call.get('name')}"
            )

        # 验证参数
        args_schema = tool_schema.get("parameters", {})
        if args_schema:
            self._validate_against_schema(
                tool_call.get("arguments", {}), args_schema, result
            )

        if result.is_valid:
            self._stats["passed"] += 1
        else:
            self._stats["failed"] += 1

        return result

    def _extract_json(self, text: str) -> Optional[Any]:
        """从文本中提取JSON"""
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试提取```json块
        json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试提取{...}或[...]
        for pattern in [r"\{.*\}", r"\[.*\]"]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    continue

        return None

    def _extract_tool_call(self, text: str) -> Optional[dict]:
        """提取工具调用"""
        # 常见格式: {"name": "...", "arguments": {...}}
        json_data = self._extract_json(text)
        if isinstance(json_data, dict):
            if "name" in json_data or "tool" in json_data or "function" in json_data:
                return {
                    "name": json_data.get("name") or json_data.get("tool") or json_data.get("function"),
                    "arguments": json_data.get("arguments") or json_data.get("args") or json_data.get("parameters") or {},
                }
        return None

    def _validate_against_schema(self, data: Any, schema: dict, result: ValidationResult, path: str = ""):
        """递归验证schema"""
        if not isinstance(schema, dict):
            return

        schema_type = schema.get("type", "")

        if schema_type == "object":
            if not isinstance(data, dict):
                result.add_error(f"Expected object at {path}, got {type(data).__name__}", path)
                return

            properties = schema.get("properties", {})
            required = schema.get("required", [])

            # 检查必填字段
            for req_field in required:
                if req_field not in data:
                    result.add_error(f"Missing required field: {req_field}", f"{path}.{req_field}")

            # 验证每个字段
            for field_name, field_schema in properties.items():
                if field_name in data:
                    self._validate_against_schema(
                        data[field_name], field_schema, result, f"{path}.{field_name}"
                    )

        elif schema_type == "array":
            if not isinstance(data, list):
                result.add_error(f"Expected array at {path}, got {type(data).__name__}", path)
                return

            items_schema = schema.get("items", {})
            for i, item in enumerate(data):
                self._validate_against_schema(item, items_schema, result, f"{path}[{i}]")

        elif schema_type == "string":
            if not isinstance(data, str):
                result.add_error(f"Expected string at {path}, got {type(data).__name__}", path)

            # 枚举检查
            if "enum" in schema and data not in schema["enum"]:
                result.add_error(
                    f"Value '{data}' not in enum {schema['enum']} at {path}", path
                )

        elif schema_type == "number":
            if not isinstance(data, (int, float)):
                result.add_error(f"Expected number at {path}, got {type(data).__name__}", path)
            else:
                if "minimum" in schema and data < schema["minimum"]:
                    result.add_warning(f"Value {data} below minimum {schema['minimum']} at {path}", path)
                if "maximum" in schema and data > schema["maximum"]:
                    result.add_warning(f"Value {data} above maximum {schema['maximum']} at {path}", path)

        elif schema_type == "boolean":
            if not isinstance(data, bool):
                result.add_error(f"Expected boolean at {path}, got {type(data).__name__}", path)

    def _try_repair(self, data: Any, schema: dict) -> Optional[Any]:
        """尝试修复数据"""
        if not isinstance(data, dict) or not isinstance(schema, dict):
            return None

        repaired = dict(data)
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # 填充缺失的必填字段（使用默认值）
        for req_field in required:
            if req_field not in repaired:
                field_schema = properties.get(req_field, {})
                default = field_schema.get("default")
                if default is not None:
                    repaired[req_field] = default
                elif field_schema.get("type") == "string":
                    repaired[req_field] = ""
                elif field_schema.get("type") == "number":
                    repaired[req_field] = 0
                elif field_schema.get("type") == "boolean":
                    repaired[req_field] = False
                elif field_schema.get("type") == "array":
                    repaired[req_field] = []
                elif field_schema.get("type") == "object":
                    repaired[req_field] = {}

        return repaired

    def get_stats(self) -> dict:
        total = self._stats["total_validations"]
        return {
            **self._stats,
            "pass_rate": f"{self._stats['passed'] / max(total, 1) * 100:.1f}%",
            "repair_rate": f"{self._stats['repaired'] / max(self._stats['failed'], 1) * 100:.1f}%",
        }

    # ═══════════════════════════════════════════════════════════════
    # json-validation 预置 Schema 工具（Pi 取长补短 #11）
    # ═══════════════════════════════════════════════════════════════
    #
    # 来源：OpenBidKit piJsonValidationTool（Ajv 四阶段、预置 Schema、路径越界
    # 校验、错误回传模型）。SoulMate 落地在**既有** OutputValidator（解析→schema
    # 校验→修复三段已有）上补第四段与两件套：
    #   ① JSON 解析（_extract_json：裸 JSON/```json块/{}/[] 三形态）
    #   ② Schema 校验（_validate_against_schema：object/array/string/number/
    #      boolean + required/enum/min/max，既有）
    #   ③ 路径越界校验（check_path_bounds：数据里的文件路径不得逃出 base_path
    #      ——防产物里写穿工作区的路径；无 base_path 显式跳过可见）
    #   ④ 结构化错误回传（_artifact_report：归一成 repair_loop.step 的
    #      验收输入形态 {ok, blocking, issues}——验收→修复天然对接，#3 成环）
    # 预置 Schema：register_preset/get_preset（Schema 映射走插件任务配置下发）。

    def register_preset(self, name: str, schema: dict) -> dict:
        """登记预置 Schema（插件任务配置下发的映射面）。"""
        name = str(name or "").strip()
        if not name:
            raise ValueError("preset 名必填")
        if not isinstance(schema, dict):
            raise ValueError("preset schema 需为对象")
        SCHEMA_PRESETS[name] = dict(schema)
        return {"registered": name, "total": len(SCHEMA_PRESETS)}

    def get_preset(self, name: str) -> dict | None:
        return SCHEMA_PRESETS.get(str(name or "").strip())

    def list_presets(self) -> list[str]:
        return sorted(SCHEMA_PRESETS)

    def validate_artifact(self, output, schema: dict | None = None,
                          preset: str = "", base_path: str = "") -> dict:
        """四阶段校验主入口 → repair_loop 验收输入形态。

        {"ok", "blocking": [id...], "issues": [{id, severity, detail}...],
         "stage", "data", "repaired", "presets_used", "path_bounds": {...}}

        severity 口径（对接 repair_loop.step）：blocking=不过（解析失败/schema
        错误/路径越界），minor=建议（min/max 警告类）。
        """
        # 预置 Schema 解析（插件任务配置映射）
        preset_name = ""
        if schema is None and preset:
            schema = self.get_preset(preset)
            preset_name = str(preset)
            if schema is None:
                return _artifact_report(False, issues=[{
                    "id": "stage1-preset", "severity": "blocking",
                    "detail": f"预置 Schema 不存在: {preset!r}（可用: {self.list_presets()}）",
                }], stage="preset")
        if schema is None:
            return _artifact_report(False, issues=[{
                "id": "stage1-schema", "severity": "blocking",
                "detail": "缺 schema（传 schema 对象或 preset 预置名）",
            }], stage="preset")

        # ① JSON 解析
        parsed = output if not isinstance(output, str) else self._extract_json(output)
        if parsed is None:
            return _artifact_report(False, issues=[{
                "id": "stage1-parse", "severity": "blocking",
                "detail": "JSON 解析失败（非合法 JSON，也未在文本中找到 {...}/[...] 或 ```json 块）",
            }], stage="parse", presets_used=preset_name)

        # ② Schema 校验（既有递归校验）
        vres = ValidationResult(is_valid=True, original_output=parsed)
        self._validate_against_schema(parsed, schema, vres)
        self._stats["total_validations"] += 1
        issues = []
        for e in vres.errors:
            issues.append({"id": f"stage2-{len(issues) + 1}", "severity": "blocking",
                           "detail": f"{e['message']}（at {e['path'] or 'root'}）"})
        for w in vres.warnings:
            issues.append({"id": f"stage2-w{len(issues) + 1}", "severity": "minor",
                           "detail": f"{w['message']}（at {w['path'] or 'root'}）"})

        # ③ 路径越界校验（数据内文件路径不得逃出 base_path）
        bounds = self.check_path_bounds(parsed, base_path)
        for b in bounds["violations"]:
            issues.append({"id": f"stage3-{len(issues) + 1}", "severity": "blocking",
                           "detail": f"路径越界: {b['value']}（at {b['path']}）——"
                                     f"不得逃出 {bounds['base_path']}"})

        # ④ 结构化错误回传（repair_loop 验收输入形态）
        return _artifact_report(
            not any(i["severity"] == "blocking" for i in issues),
            issues=issues, stage="done", data=parsed,
            presets_used=preset_name, path_bounds=bounds)

    @staticmethod
    def check_path_bounds(data: Any, base_path: str, _path: str = "") -> dict:
        """路径越界校验（第三阶段）：数据里的文件路径不得逃出 base_path。

        只校验**路径形态**字符串（/ 开头、./ ../ 开头、盘符、含 ../），散文
        文本零误伤；无 base_path=显式跳过（skipped=True 可见，不静默）。
        """
        base = str(base_path or "").strip()
        report = {"base_path": base, "skipped": not base, "violations": []}
        if not base:
            return report
        base_res = Path(base).expanduser().resolve()

        def _walk(node, p):
            if isinstance(node, dict):
                for k, v in node.items():
                    _walk(v, f"{p}.{k}" if p else str(k))
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    _walk(v, f"{p}[{i}]")
            elif isinstance(node, str):
                if not _looks_like_path(node):
                    return
                cand = Path(node).expanduser()
                resolved = (base_res / cand).resolve() if not cand.is_absolute() \
                    else cand.resolve()
                if resolved != base_res and base_res not in resolved.parents:
                    report["violations"].append(
                        {"value": node, "path": p or "root",
                         "resolved": str(resolved)})

        _walk(data, _path)
        return report


# 预置 Schema 注册表（Schema 映射走插件任务配置——模块级单点）
SCHEMA_PRESETS: dict[str, dict] = {}


def _looks_like_path(s: str) -> bool:
    """路径形态判定（保守：散文零误伤）。"""
    s = str(s)
    return (s.startswith(("/", "./", "../", "~", ".\\", "..\\", "\\\\"))
            or (len(s) > 2 and s[1] == ":" and s[2] in ("/", "\\"))
            or "../" in s or "..\\" in s)


def _artifact_report(ok: bool, issues: list, stage: str = "done",
                     data=None, repaired=None, presets_used: str = "",
                     path_bounds: dict | None = None) -> dict:
    """四阶段报告 → repair_loop.step 验收输入形态（天然对接 #3）。"""
    blocking = [i["id"] for i in issues if i.get("severity") == "blocking"]
    return {
        "ok": bool(ok) and not blocking,
        "blocking": blocking,
        "issues": issues,
        "stage": stage,
        "data": data,
        "repaired": repaired,
        "presets_used": presets_used,
        "path_bounds": path_bounds or {"base_path": "", "skipped": True,
                                       "violations": []},
    }
