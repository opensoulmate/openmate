# -*- coding: utf-8 -*-
"""writing_tools.py — SoulMate 写作能力层工具组（C1/C2/C3/C4 触发面）。

工具形状 = MCP 工具契约（function-calling），对齐 download_tools/plugin_tools
分发先例：`tool_defs()` 进 LLM 工具表，`run(func_name, args, session_id)` 主循环
与 code-mode 批内共用。四件套（**通用文档创作能力**，写标书/写方案/写课件/写
合同/报销报价全场景复用，非投标专用）：

- fact_lock    C1 全局事实锁：build（结构化事实表→锁工件+prompt 注入块）/
               check（生成后跨章节一致性校验，冲突即报+定位章节行号）/ prompt
- doc_blank    C2 留白协议：register/list（待补清单）/ scan（占位残留确定性
               扫描）/ attach_comments（缺口落 Word 批注）/ protocol（写作纪律）
- money_calc   C3 Decimal 精算：quote_total（报价合计）/ discount（折扣率双向）/
               tax（税率换算）/ to_upper|to_lower（人民币大小写）——**禁 LLM 算数**
- docx_compile C4 OpenXML 确定性编译：compile（AST→docx：三线表/跨页续表头/
               页眉页脚分节/修订留痕 w:ins/w:del+来源血缘）/ verify_revisions
               （拒绝全部修订≡原文自校验）

纪律：参数非法/依赖缺失显式报因禁静默；金额只走 agent/money.py（拒绝 float）；
编译产物落 ~/.hermes/soulmate/documents/（filename 只许文件名），返回 MEDIA: 路径。
执行体：agent/fact_lock.py / doc_blank.py / money.py / docx_compile.py（纯函数
核心，独立可测），本模块只做参数面+结果呈现（分层铁律）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from . import doc_blank, fact_lock, money
from agent.docx_compile import compile_docx, revision_views, verify_reject_all

logger = logging.getLogger("agent.writing_tools")

DEFAULT_OUT_DIR = os.path.expanduser("~/.hermes/soulmate/documents")
_SAFE_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff.\-]*$")

MONEY_ACTIONS = ("quote_total", "discount", "tax", "to_upper", "to_lower")
BLANK_ACTIONS = ("register", "list", "scan", "attach_comments", "protocol")
LOCK_ACTIONS = ("build", "check", "prompt")
COMPILE_ACTIONS = ("compile", "verify_revisions")

# 工具名（soulmate_agent 接线用）
TOOL_NAMES = ("fact_lock", "doc_blank", "money_calc", "docx_compile")


def tool_defs() -> list[dict]:
    """四件套 function-calling 工具定义（MCP 工具契约形状）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "fact_lock",
                "description": (
                    "全局事实锁（长文前后一致性）：写任何多章节长文（方案/可研/"
                    "合同/课件/标书）前，先把已确认事实（工期/质保/人数/金额/"
                    "系统全称等）建成只读事实锁并注入写作提示；成稿后跑一致性"
                    "校验器逐章核对，冲突即点名（章节+行号+原文）。action=build "
                    "建锁（facts 列表）/ prompt 取注入块 / check 跨章节校验"
                    "（传 chapters=[{id,title,text}]）。承诺类数字只出自事实锁——"
                    "锁值出现且一致才许交付。"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": list(LOCK_ACTIONS)},
                        "facts": {
                            "type": "array", "items": {"type": "object"},
                            "description": (
                                "build 用：[{key, value, scope, source, "
                                "confirmed_by, aliases?}]（后三者必填：范围/来源/"
                                "确认人——人工确认留痕）"),
                        },
                        "lock": {"type": "object",
                                 "description": "check/prompt 用：build 产出的锁工件"},
                        "chapters": {
                            "type": "array", "items": {"type": "object"},
                            "description": (
                                "check 用：[{id, title, text}]，按文档顺序的章节文本"),
                        },
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "doc_blank",
                "description": (
                    "留白协议（缺资料不编造）：证据不足的槽位登记待补，禁行业"
                    "惯例/记忆补全；缺口正规落点=Word 批注+文末待补清单（正文禁"
                    "【待补】占位文字）。action=register 登记槽位 / list 待补清单"
                    " / scan 占位残留扫描（交付前自查）/ attach_comments 把缺口挂"
                    "成 Word 批注（comments=[{anchor,text}] 锚定原文）/ protocol "
                    "取写作纪律注入块。"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": list(BLANK_ACTIONS)},
                        "description": {"type": "string",
                                        "description": "register：缺什么（如 ISO9001 证书编号）"},
                        "chapter": {"type": "string", "description": "register：所在章节/位置"},
                        "reason": {"type": "string", "description": "register：缺因（默认'资料缺，禁编造'）"},
                        "slot_id": {"type": "string", "description": "register 可选自定义 ID"},
                        "text": {"type": "string", "description": "scan：待扫描正文"},
                        "docx_path": {"type": "string",
                                      "description": "attach_comments：目标 docx 绝对路径"},
                        "comments": {
                            "type": "array", "items": {"type": "object"},
                            "description": (
                                "attach_comments：[{anchor|paragraph_index, text}]"),
                        },
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "money_calc",
                "description": (
                    "Decimal 数值精算（报价/折扣/税率/人民币大小写）——绝对禁止"
                    "LLM 自己算钱：只传参数（字符串数字），本工具出精确结果。"
                    "action=quote_total 报价合计（items=[{name,qty,unit_price}]，"
                    "可选 discount=折后价系数 0.9=九折、tax_rate=0.13）/ discount "
                    "折扣（传 rate=系数正算，或传 sale_price 反算折扣率）/ tax 税率"
                    "换算（mode=from_inclusive 含税拆税 | to_inclusive 加税）/ "
                    "to_upper 人民币大写（壹仟贰佰伍拾万元整）/ to_lower 大写反解析。"
                    "金额只收字符串/整数（拒绝浮点），超两位小数显式报错。"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": list(MONEY_ACTIONS)},
                        "items": {"type": "array", "items": {"type": "object"},
                                  "description": "quote_total：[{name?, qty, unit_price}]"},
                        "discount": {"type": "string",
                                     "description": "quote_total：折后价系数（0.9=九折）"},
                        "tax_rate": {"type": "string", "description": "quote_total/tax：税率（0.13=13%）"},
                        "list_price": {"type": "string", "description": "discount：原价"},
                        "rate": {"type": "string", "description": "discount：折后价系数（正算）"},
                        "sale_price": {"type": "string", "description": "discount：成交价（反算折扣率）"},
                        "mode": {"type": "string",
                                 "enum": ["from_inclusive", "to_inclusive"],
                                 "description": "tax：含税拆税 / 不含税加税"},
                        "total_incl": {"type": "string", "description": "tax from_inclusive：含税价"},
                        "net": {"type": "string", "description": "tax to_inclusive：不含税价"},
                        "amount": {"type": "string", "description": "to_upper：金额"},
                        "upper_text": {"type": "string", "description": "to_lower：大写金额"},
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "docx_compile",
                "description": (
                    "OpenXML 确定性编译（AST→docx 深排版，排版不猜）：三线表/"
                    "跨页续表头/页眉页脚分节/修订留痕（w:ins/w:del+来源血缘批注+"
                    "provenance 侧车）。action=compile 编译（spec=AST，含 "
                    "sections/三线表/segments 修订段）/ verify_revisions 修订双视图"
                    "校验（拒绝全部修订≡原文）。spec 形状见 tool 文档：{title?, "
                    "sections:[{header?, footer?{text,page_number}, landscape?, "
                    "content:[...]}]}，块型 heading/paragraph/bullet/table/pagebreak，"
                    "table 支持 style=three_line、caption、header_repeat；paragraph "
                    "支持 segments=[{text, mode: normal|ins|del, author?, date?, "
                    "source?}]。产物落文档目录并返回 MEDIA: 路径。"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": list(COMPILE_ACTIONS)},
                        "spec": {"type": "object", "description": "compile：文档 AST"},
                        "filename": {"type": "string",
                                     "description": "compile：输出文件名（不含路径，.docx 可省）"},
                        "docx_path": {"type": "string",
                                      "description": "verify_revisions：目标 docx 绝对路径"},
                        "original_text": {"type": "string",
                                          "description": "verify_revisions 可选：原文（对照校验）"},
                    },
                    "required": ["action"],
                },
            },
        },
    ]


class WritingTools:
    """写作能力层工具组（C1/C2/C3/C4）。doc_blank 槽位登记簿按 session 持有。"""

    def __init__(self, out_dir: str = "", now=None):
        self.out_dir = Path(out_dir or DEFAULT_OUT_DIR)
        self._now = now or time.time
        self._registries: dict[str, doc_blank.BlankRegistry] = {}

    # ── 分发 ─────────────────────────────────────────────────────────

    def run(self, func_name: str, func_args: dict, session_id: str = "") -> str:
        func_name = str(func_name or "").strip()
        args = dict(func_args or {})
        try:
            if func_name == "fact_lock":
                return self._fact_lock(args)
            if func_name == "doc_blank":
                return self._doc_blank(args, session_id)
            if func_name == "money_calc":
                return self._money_calc(args)
            if func_name == "docx_compile":
                return self._docx_compile(args)
            return (f"❌ 未知写作工具: {func_name!r}。"
                    f"支持: {', '.join(TOOL_NAMES)}")
        except (ValueError, money.MoneyError) as e:
            return f"❌ 参数错误: {e}"
        except RuntimeError as e:
            return f"❌ 依赖不可用: {e}"
        except Exception as e:  # noqa: BLE001 — 显式报因禁静默
            logger.exception("writing tool failed: %s", func_name)
            return f"❌ 执行失败（{func_name}）: {type(e).__name__}: {e}"

    def _registry(self, session_id: str) -> doc_blank.BlankRegistry:
        sid = str(session_id or "default")
        reg = self._registries.get(sid)
        if reg is None:
            reg = doc_blank.BlankRegistry()
            self._registries[sid] = reg
            if len(self._registries) > 50:   # 有界：会话登记簿不无限膨胀
                self._registries.pop(next(iter(self._registries)))
        return reg

    # ── C1 fact_lock ─────────────────────────────────────────────────

    def _fact_lock(self, args: dict) -> str:
        action = str(args.get("action", "") or "").strip().lower()
        if action == "build":
            facts = args.get("facts")
            if not isinstance(facts, list) or not facts:
                return "❌ 参数错误: build 需要非空 facts 列表"
            try:
                lock = fact_lock.build_lock(facts)
            except ValueError as e:
                return f"❌ 参数错误: {e}"
            block = fact_lock.prompt_block(lock)
            return "\n".join([
                "✅ 事实锁已构建（只读，人工确认后注入写作提示顶部）",
                json.dumps(lock, ensure_ascii=False, indent=2),
                "",
                "── 写作提示注入块 ──",
                block,
            ])
        if action == "prompt":
            lock = args.get("lock")
            if not isinstance(lock, dict):
                return "❌ 参数错误: prompt 需要 lock（build 产出的锁工件）"
            try:
                return "✅ 事实锁注入块：\n" + fact_lock.prompt_block(lock)
            except (ValueError, AttributeError) as e:
                return f"❌ 参数错误: {e}"
        if action == "check":
            lock = args.get("lock")
            chapters = args.get("chapters")
            if not isinstance(lock, dict):
                return "❌ 参数错误: check 需要 lock（build 产出的锁工件）"
            if not isinstance(chapters, list) or not chapters:
                return "❌ 参数错误: check 需要非空 chapters 列表 [{id, title, text}]"
            try:
                result = fact_lock.check_document(lock, chapters)
            except ValueError as e:
                return f"❌ 参数错误: {e}"
            report = fact_lock.format_report(result)
            return "\n".join([
                report,
                "",
                json.dumps({"ok": result["ok"], "issues": result["issues"]},
                           ensure_ascii=False, indent=2),
            ])
        return f"❌ 参数错误: 未知 action={action!r}（支持 {'/'.join(LOCK_ACTIONS)}）"

    # ── C2 doc_blank ─────────────────────────────────────────────────

    def _doc_blank(self, args: dict, session_id: str) -> str:
        action = str(args.get("action", "") or "").strip().lower()
        reg = self._registry(session_id)
        if action == "register":
            try:
                slot = reg.register(
                    str(args.get("description", "") or ""),
                    chapter=str(args.get("chapter", "") or ""),
                    reason=str(args.get("reason", "") or ""),
                    slot_id=str(args.get("slot_id", "") or ""))
            except ValueError as e:
                return f"❌ 参数错误: {e}"
            return (f"✅ 已登记留白槽位 {slot.slot_id}：{slot.description}"
                    f"（{slot.chapter or '位置未定'}）——正文不编造，缺口落批注/待补清单")
        if action == "list":
            return "✅ 待补清单：\n" + reg.pending_markdown()
        if action == "scan":
            text = args.get("text")
            if not isinstance(text, str) or not text:
                return "❌ 参数错误: scan 需要非空 text"
            viol = doc_blank.scan_placeholders(text)
            if not viol:
                return "✅ 占位残留扫描通过：无【待补】/TBD/？？等占位文字"
            lines = [f"❌ 占位残留 {len(viol)} 处（正文禁占位文字，缺口应落 Word 批注）："]
            for v in viol:
                lines.append(f"  第{v['line']}行第{v['col']}列 [{v['kind']}] "
                             f"{v['matched']}｜{v['excerpt']}")
            return "\n".join(lines)
        if action == "attach_comments":
            docx_path = str(args.get("docx_path", "") or "")
            comments = args.get("comments")
            if not docx_path or not Path(docx_path).is_file():
                return f"❌ 参数错误: docx_path 不存在: {docx_path!r}"
            if not isinstance(comments, list) or not comments:
                return "❌ 参数错误: attach_comments 需要非空 comments 列表 [{anchor, text}]"
            try:
                res = doc_blank.attach_comments(docx_path, comments)
            except (ValueError, RuntimeError) as e:
                return f"❌ 参数错误: {e}"
            lines = []
            if res["attached"]:
                lines.append(f"✅ 已挂 Word 批注 {len(res['attached'])} 条 → {res['path']}")
                for a in res["attached"]:
                    lines.append(f"  {a['where']}：{a['text']}")
            else:
                lines.append("⚠️ 无批注挂载（文件未改动）")
            for f in res["failed"]:
                lines.append(f"  ❌ 第{f['index']}条失败：{f['error']}")
            return "\n".join(lines)
        if action == "protocol":
            return "✅ 留白协议写作纪律：\n" + doc_blank.protocol_prompt(reg)
        return f"❌ 参数错误: 未知 action={action!r}（支持 {'/'.join(BLANK_ACTIONS)}）"

    # ── C3 money_calc ────────────────────────────────────────────────

    def _money_calc(self, args: dict) -> str:
        action = str(args.get("action", "") or "").strip().lower()
        if action == "quote_total":
            try:
                r = money.quote_total(args.get("items"),
                                      discount=args.get("discount"),
                                      tax_rate=args.get("tax_rate"))
            except money.MoneyError as e:
                return f"❌ 参数错误: {e}"
            lines = ["✅ 报价精算（Decimal，行小计四舍五入到分）："]
            for ln in r["lines"]:
                lines.append(f"  {ln['name']}: {ln['qty']} × {ln['unit_price']} = {ln['subtotal']}")
            lines.append(f"  合计（未折）: {r['subtotal']}")
            if r["discount_rate"] is not None:
                lines.append(f"  折扣（系数 {r['discount_rate']}）: -{r['discount_amount']} → {r['after_discount']}")
            if r["tax_rate"] is not None:
                lines.append(f"  税（{r['tax_rate']}）: +{r['tax_amount']}")
            lines.append(f"  总计: {r['total']}")
            lines.append(f"  大写: 人民币{money.rmb_upper(r['total'])}")
            return "\n".join(lines)
        if action == "discount":
            try:
                if args.get("sale_price") is not None:
                    r = money.calc_discount(args.get("list_price"), args.get("sale_price"))
                    return (f"✅ 折扣反算：原价 {r['list_price']} → 成交价 {r['sale_price']}"
                            f"｜折后价系数 {r['rate']}"
                            f"｜优惠额 {r['discount_amount']}｜优惠率 {r['discount_ratio']}")
                r = money.apply_discount(args.get("list_price"), args.get("rate"))
                return (f"✅ 折扣正算：原价 {r['list_price']} × 系数 {r['rate']}"
                        f" = 折后价 {r['sale_price']}（优惠 {r['discount_amount']}）")
            except money.MoneyError as e:
                return f"❌ 参数错误: {e}"
        if action == "tax":
            mode = str(args.get("mode", "") or "").strip().lower()
            try:
                if mode == "from_inclusive":
                    r = money.tax_from_inclusive(args.get("total_incl"), args.get("tax_rate"))
                    return (f"✅ 含税拆税：含税 {r['total_incl']} = 不含税 {r['net']}"
                            f" + 税额 {r['tax_amount']}（税率 {r['tax_rate']}）")
                if mode == "to_inclusive":
                    r = money.tax_to_inclusive(args.get("net"), args.get("tax_rate"))
                    return (f"✅ 加税：不含税 {r['net']} + 税额 {r['tax_amount']}"
                            f" = 含税 {r['total_incl']}（税率 {r['tax_rate']}）")
            except money.MoneyError as e:
                return f"❌ 参数错误: {e}"
            return "❌ 参数错误: tax 需要 mode=from_inclusive|to_inclusive"
        if action == "to_upper":
            try:
                return f"✅ 人民币大写：{money.rmb_upper(args.get('amount'))}"
            except money.MoneyError as e:
                return f"❌ 参数错误: {e}"
        if action == "to_lower":
            try:
                d = money.rmb_lower(str(args.get("upper_text", "") or ""))
                return f"✅ 大写反解析：{d}（回读校验大写：{money.rmb_upper(d)}）"
            except money.MoneyError as e:
                return f"❌ 参数错误: {e}"
        return f"❌ 参数错误: 未知 action={action!r}（支持 {'/'.join(MONEY_ACTIONS)}）"

    # ── C4 docx_compile ──────────────────────────────────────────────

    def _docx_compile(self, args: dict) -> str:
        action = str(args.get("action", "") or "").strip().lower()
        if action == "compile":
            spec = args.get("spec")
            if not isinstance(spec, dict):
                return "❌ 参数错误: compile 需要 spec（文档 AST 对象，见工具说明）"
            try:
                out_path = self._resolve_out_path(args)
                res = compile_docx(spec, out_path)
            except ValueError as e:
                return f"❌ 参数错误: {e}"
            size = Path(res["path"]).stat().st_size
            lines = [
                f"✅ 文档已编译（docx 确定性排版）: {res['path']}（{size} 字节）",
                f"统计: {res['blocks']} 块，{res['tables']} 表，{res['sections']} 节，"
                f"{res['revisions']} 条修订，{res['trace_comments']} 条溯源批注",
            ]
            if res["reject_all_verified"] is not None:
                mark = "✅ 通过" if res["reject_all_verified"] else "❌ 未过"
                lines.append(f"拒绝全部修订≡原文自校验: {mark}")
            if res["provenance"]:
                lines.append(f"来源血缘侧车: {res['provenance']}")
            lines.append(f"MEDIA:{res['path']}")
            return "\n".join(lines)
        if action == "verify_revisions":
            docx_path = str(args.get("docx_path", "") or "")
            if not docx_path or not Path(docx_path).is_file():
                return f"❌ 参数错误: docx_path 不存在: {docx_path!r}"
            views = revision_views(docx_path)
            original_text = args.get("original_text")
            lines = [
                "✅ 修订双视图：",
                f"  拒绝全部修订（原文）: {views['original'][:200]}",
                f"  接受全部修订（终稿）: {views['revised'][:200]}",
            ]
            if isinstance(original_text, str) and original_text:
                ok = verify_reject_all(original_text, docx_path)
                lines.append("  与给定原文逐字对账: " + ("✅ 一致" if ok else "❌ 不一致"))
            return "\n".join(lines)
        return (f"❌ 参数错误: 未知 action={action!r}"
                f"（支持 {'/'.join(COMPILE_ACTIONS)}）")

    def _resolve_out_path(self, args: dict) -> Path:
        """输出路径纪律（docgen_tools 同款）：只许文件名，落 out_dir。"""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        name = str(args.get("filename", "") or "").strip()
        if not name:
            name = f"doc-{int(self._now() * 1000)}.docx"
        if not name.lower().endswith(".docx"):
            name = f"{name}.docx"
        if "/" in name or "\\" in name or not _SAFE_NAME_RE.match(name):
            if "/" in name or "\\" in name:
                raise ValueError(
                    f"⚠️ 输出目录受限：文档编译只能写入 {self.out_dir}，"
                    f"请求路径 {name!r} 越界。请改用 filename=<文件名>（不含路径"
                    f"分隔符）；若用户明文指定其他目标路径，生成后可用 terminal "
                    f"复制过去并在回复中说明产物实际路径")
            raise ValueError(f"filename 非法: {name!r}（只允许文件名本身）")
        out = (self.out_dir / name).resolve()
        if out.parent != self.out_dir.resolve():
            raise ValueError("输出路径越界（必须落在文档目录内）")
        return out
