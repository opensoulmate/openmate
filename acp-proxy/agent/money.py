# -*- coding: utf-8 -*-
"""money.py — C3 数值精算（Decimal + 人民币大小写），SoulMate 能力层纯函数库。

通用文档创作能力（写标书/写方案/写课件/报销单/报价单全场景复用）：
- Decimal 精确计算：报价合计/折扣率/税率换算/行小计——**绝对禁止 LLM 自己算钱**，
  LLM 只提参数（字符串数字），本模块负责全部算术；
- 人民币大写转换（壹仟贰佰伍拾万元整）+ 大写→小写反解析（供回读校验）。

纪律（对齐文章坑 1 浮点精度丢失 + 财政部《正确填写票据和结算凭证的基本规定》）：
- **拒绝 float**：浮点传入即抛 MoneyError（\"0.1+0.2\" 类精度事故在报价/报销场景
  =废标级/报销事故），调用方必须传字符串/整数/Decimal；
- 金额最多两位小数（分），超限抛错要求先显式 quantize2 决策，绝不静默截断；
- 行小计四舍五入到分（ROUND_HALF_UP，银行惯例）后求和；
- 大写规则：到\"元\"为止写\"整\"；到\"角\"为止（元位非零）可不写\"整\"——本库取
  确定性口径：fen==0 且（jiao==0 或 元位为 0）时写\"整\"（0.7→柒角整、
  13356305.2→…元贰角，两向均符合规定）；有\"分\"不写\"整\"；
  元位为 0 且角位非零只写角（0.7→柒角整，不写\"零元\"）。

纯函数、无 I/O、无 LLM；独立可测（tests/test_money.py）。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

__all__ = [
    "MoneyError",
    "to_decimal",
    "quantize2",
    "line_total",
    "quote_total",
    "apply_discount",
    "calc_discount",
    "tax_from_inclusive",
    "tax_to_inclusive",
    "rmb_upper",
    "rmb_lower",
    "format_money",
]

CENT = Decimal("0.01")

_CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_CN_UNITS = ("", "拾", "佰", "仟")
# 数字→中文（反解析用）
_CN_DIGIT_MAP = {ch: i for i, ch in enumerate(_CN_DIGITS)}
_CN_DIGIT_MAP["两"] = 2  # 口语容错
_CN_UNIT_MAP = {"拾": 10, "佰": 100, "仟": 1000}


class MoneyError(ValueError):
    """金额参数/格式非法（显式报因，绝不静默取近似值）。"""


# ── Decimal 基元 ─────────────────────────────────────────────────────

def to_decimal(value, field: str = "amount") -> Decimal:
    """安全转 Decimal：str/int/Decimal 接受；**float 拒绝**（MoneyError）。

    字符串容错：去 ￥¥/千分位逗号/空白；全角数字归一为半角。
    """
    if isinstance(value, bool):
        raise MoneyError(f"{field} 不接受布尔值")
    if isinstance(value, float):
        raise MoneyError(
            f"{field} 不接受浮点数（浮点精度事故禁入金额链，禁 LLM 算数）："
            f"请传字符串或整数，如 \"1234.56\"")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, str):
        s = value.strip()
        for ch in ("￥", "¥", ",", "，", " "):
            s = s.replace(ch, "")
        # 全角→半角数字/小数点
        s = "".join(
            chr(ord(ch) - 0xFEE0) if "０" <= ch <= "９" or ch == "．" else ch
            for ch in s)
        if not s:
            raise MoneyError(f"{field} 为空")
        try:
            d = Decimal(s)
        except InvalidOperation as e:
            raise MoneyError(f"{field} 不是合法数字: {value!r}") from e
    else:
        raise MoneyError(f"{field} 类型不支持: {type(value).__name__}（传字符串/整数/Decimal）")
    if not d.is_finite():
        raise MoneyError(f"{field} 非有限数: {value!r}")
    return d


def quantize2(value, field: str = "amount") -> Decimal:
    """四舍五入到分（ROUND_HALF_UP）。显式决策点：调用即认可取整口径。"""
    return to_decimal(value, field).quantize(CENT, rounding=ROUND_HALF_UP)


def _check_scale2(d: Decimal, field: str) -> Decimal:
    """金额链入口：超过两位小数即拒绝（禁静默截断/取整）。"""
    if d != d.quantize(CENT, rounding=ROUND_HALF_UP):
        raise MoneyError(
            f"{field} 超过两位小数（{d}）：请先显式 quantize2 取整到分再进入金额链")
    return d


# ── 报价/折扣/税率 ───────────────────────────────────────────────────

def line_total(qty, unit_price) -> Decimal:
    """行小计 = 数量 × 单价，四舍五入到分。"""
    q = to_decimal(qty, "qty")
    p = to_decimal(unit_price, "unit_price")
    if q < 0:
        raise MoneyError(f"qty 不能为负: {qty!r}")
    return quantize2(q * p)


def quote_total(items, discount=None, tax_rate=None) -> dict:
    """报价合计：逐行行小计（到分）求和 → 折扣 → 税。

    items:      [{"name"?, "qty": ..., "unit_price": ...}, ...]
    discount:   折后价系数（0.9=九折，即折后价=原价×0.9）；None=无折扣
    tax_rate:   税率（0.13=13%），计税基数=折后金额；None=不含税口径

    返回 {"lines": [...], "subtotal", "discount_rate", "discount_amount",
          "after_discount", "tax_rate", "tax_amount", "total"}
    total=含税价（无 tax_rate 时等于 after_discount）。
    """
    if not isinstance(items, (list, tuple)) or not items:
        raise MoneyError("items 需要非空列表 [{name?, qty, unit_price}]")
    lines = []
    subtotal = Decimal("0.00")
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise MoneyError(f"items[{i}] 必须是对象 {{name?, qty, unit_price}}")
        if "qty" not in it or "unit_price" not in it:
            raise MoneyError(f"items[{i}] 缺 qty/unit_price")
        sub = line_total(it["qty"], it["unit_price"])
        subtotal += sub
        lines.append({
            "name": str(it.get("name", "") or f"第{i + 1}项"),
            "qty": str(to_decimal(it["qty"], f"items[{i}].qty")),
            "unit_price": str(quantize2(it["unit_price"], f"items[{i}].unit_price")),
            "subtotal": str(sub),
        })
    subtotal = quantize2(subtotal)
    out = {
        "lines": lines,
        "subtotal": str(subtotal),
        "discount_rate": None,
        "discount_amount": None,
        "after_discount": str(subtotal),
        "tax_rate": None,
        "tax_amount": None,
        "total": str(subtotal),
    }
    after = subtotal
    if discount is not None:
        rate = to_decimal(discount, "discount")
        if not (Decimal("0") < rate <= Decimal("1")):
            raise MoneyError(f"discount 折后价系数必须在 (0,1]：{discount!r}（0.9=九折）")
        disc_amt = quantize2(subtotal * (Decimal("1") - rate))
        after = subtotal - disc_amt
        out["discount_rate"] = str(rate)
        out["discount_amount"] = str(disc_amt)
        out["after_discount"] = str(after)
        out["total"] = str(after)
    if tax_rate is not None:
        rate = to_decimal(tax_rate, "tax_rate")
        if rate < 0 or rate > Decimal("1"):
            raise MoneyError(f"tax_rate 必须在 [0,1]：{tax_rate!r}（0.13=13%）")
        tax = quantize2(after * rate)
        total = after + tax
        out["tax_rate"] = str(rate)
        out["tax_amount"] = str(tax)
        out["total"] = str(total)
    return out


def apply_discount(list_price, rate) -> dict:
    """按折后价系数计折后价。rate=折后价/原价（0.85=八五折）。

    返回 {"list_price","rate","discount_amount","sale_price"}（金额到分）。
    """
    price = _check_scale2(quantize2(list_price), "list_price")
    r = to_decimal(rate, "rate")
    if not (Decimal("0") < r <= Decimal("1")):
        raise MoneyError(f"rate 折后价系数必须在 (0,1]：{rate!r}（0.85=八五折）")
    sale = quantize2(price * r)
    return {
        "list_price": str(price),
        "rate": str(r),
        "discount_amount": str(price - sale),
        "sale_price": str(sale),
    }


def calc_discount(list_price, sale_price) -> dict:
    """由原价/成交价反算折扣：返回折后价系数 + 优惠率（两者都给，杜绝口径歧义）。"""
    price = _check_scale2(quantize2(list_price), "list_price")
    sale = _check_scale2(quantize2(sale_price), "sale_price")
    if price <= 0:
        raise MoneyError(f"list_price 必须为正: {list_price!r}")
    if sale < 0 or sale > price:
        raise MoneyError(f"sale_price 须在 [0, list_price]：{sale_price!r} vs {list_price!r}")
    coeff = (sale / price).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return {
        "list_price": str(price),
        "sale_price": str(sale),
        "rate": str(coeff),                                  # 折后价系数（0.85=八五折）
        "discount_amount": str(price - sale),
        "discount_ratio": str((Decimal("1") - coeff).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP)),     # 优惠率（0.15=让利15%）
    }


def tax_from_inclusive(total_incl, tax_rate) -> dict:
    """含税价拆税：net = 含税价 / (1+税率)（到分），tax = 含税价 - net（轧差保分）。"""
    total = _check_scale2(quantize2(total_incl), "total_incl")
    rate = to_decimal(tax_rate, "tax_rate")
    if rate < 0 or rate > Decimal("1"):
        raise MoneyError(f"tax_rate 必须在 [0,1]：{tax_rate!r}")
    net = quantize2(total / (Decimal("1") + rate))
    tax = total - net
    return {"total_incl": str(total), "tax_rate": str(rate),
            "net": str(net), "tax_amount": str(tax)}


def tax_to_inclusive(net, tax_rate) -> dict:
    """不含税价加税：tax = 不含税价 × 税率（到分），total = net + tax。"""
    base = _check_scale2(quantize2(net), "net")
    rate = to_decimal(tax_rate, "tax_rate")
    if rate < 0 or rate > Decimal("1"):
        raise MoneyError(f"tax_rate 必须在 [0,1]：{tax_rate!r}")
    tax = quantize2(base * rate)
    return {"net": str(base), "tax_rate": str(rate),
            "tax_amount": str(tax), "total_incl": str(base + tax)}


# ── 人民币大写 ───────────────────────────────────────────────────────

def _section_upper(n: int) -> str:
    """0<=n<=9999 → 中文数字（不带万/亿，内部连续零只写一个，结尾零不写）。"""
    if n == 0:
        return ""
    out: list[str] = []
    zero_pending = False
    for i, ch in enumerate(f"{n:04d}"):
        d = int(ch)
        if d == 0:
            zero_pending = bool(out)
        else:
            if zero_pending:
                out.append("零")
                zero_pending = False
            out.append(_CN_DIGITS[d] + _CN_UNITS[3 - i])
    return "".join(out)


def _integer_upper(n: int) -> str:
    """非负整数 → 中文大写数字（亿/万/个三级）。"""
    if n == 0:
        return ""
    parts: list[str] = []
    yi, rest = divmod(n, 10 ** 8)
    wan, ge = divmod(rest, 10 ** 4)
    if yi:
        parts.append(_section_upper(yi) + "亿")
    if wan:
        if yi and wan < 1000:
            parts.append("零")
        parts.append(_section_upper(wan) + "万")
    if ge:
        if (yi or wan) and ge < 1000:
            parts.append("零")
        parts.append(_section_upper(ge))
    return "".join(parts)


def rmb_upper(value) -> str:
    """金额 → 人民币标准大写（13356305.2 → 壹仟叁佰叁拾伍万陆仟叁佰零伍元贰角）。

    超两位小数拒绝（先 quantize2）；负数前缀\"负\"；0 → 零元整。
    """
    d = _check_scale2(to_decimal(value, "amount"), "amount")
    neg = d < 0
    d = abs(d)
    yuan = int(d)
    cents = int((d - Decimal(yuan)) * 100)
    jiao, fen = divmod(cents, 10)
    out: list[str] = ["负"] if neg else []
    if fen != 0:
        # 到分：不写整
        if yuan:
            out.append(_integer_upper(yuan) + "元")
        if jiao:
            out.append(_CN_DIGITS[jiao] + "角")
        elif yuan:
            out.append("零")
        out.append(_CN_DIGITS[fen] + "分")
    elif jiao != 0:
        # 到角：元位为 0 写\"整\"（0.7→柒角整）；元位非零按规定可省（13356305.2→…元贰角）
        if yuan:
            out.append(_integer_upper(yuan) + "元")
        out.append(_CN_DIGITS[jiao] + "角" + ("整" if not yuan else ""))
    else:
        # 到元：写整
        out.append((_integer_upper(yuan) or "零") + "元整")
    return "".join(out)


def _parse_section_upper(text: str) -> int:
    """一段（无亿/万）中文数字 → int。拾前缺位按 1（\"拾伍\"=15）。"""
    total = 0
    num = 0
    for ch in text:
        if ch in _CN_DIGIT_MAP:
            num = _CN_DIGIT_MAP[ch]
        elif ch in _CN_UNIT_MAP:
            total += (num if num else 1) * _CN_UNIT_MAP[ch]
            num = 0
        else:
            raise MoneyError(f"大写金额含非法字符: {ch!r}（原文 {text!r}）")
    return total + num


def rmb_lower(text: str) -> Decimal:
    """人民币大写 → Decimal（rmb_upper 逆变换，供回读校验）。

    接受整/正 结尾、人民币/￥ 前缀、\"拾伍\"式省位；非法字符显式报错。
    """
    if not isinstance(text, str) or not text.strip():
        raise MoneyError("大写金额为空")
    s = text.strip()
    for p in ("人民币", "￥", "¥"):
        if s.startswith(p):
            s = s[len(p):].strip()
    neg = s.startswith("负")
    if neg:
        s = s[1:].strip()
    while s.endswith(("整", "正")):
        s = s[:-1].strip()
    if not s:
        raise MoneyError(f"大写金额无有效数字: {text!r}")
    # 拆 元/角/分
    yuan_part, jiao, fen = "", 0, 0
    body = s
    if "分" in body:
        head, _, tail = body.rpartition("分")
        if not tail and len(head) >= 1 and head[-1] in _CN_DIGIT_MAP:
            fen = _CN_DIGIT_MAP[head[-1]]
            body = head[:-1]
        else:
            raise MoneyError(f"\"分\"前须为一位数字: {text!r}")
    if "角" in body:
        head, _, tail = body.rpartition("角")
        if not tail and len(head) >= 1 and head[-1] in _CN_DIGIT_MAP:
            jiao = _CN_DIGIT_MAP[head[-1]]
            body = head[:-1]
        else:
            raise MoneyError(f"\"角\"前须为一位数字: {text!r}")
    # 去掉连接性\"零\"（壹拾元零伍分 的\"零\"挂在元段尾部）
    body = body.rstrip("零")
    if body.endswith("元"):
        yuan_part = body[:-1]
    elif body:
        if "元" in body:
            raise MoneyError(f"元/角/分顺序非法: {text!r}")
        yuan_part = body          # 无元位（柒角整 / 伍分）
    while yuan_part.startswith("零"):
        yuan_part = yuan_part[1:]
    yuan = 0
    if yuan_part:
        if "亿" in yuan_part:
            head, _, yuan_part = yuan_part.partition("亿")
            yuan += _parse_section_upper(head) * 10 ** 8
        if "万" in yuan_part:
            head, _, yuan_part = yuan_part.partition("万")
            yuan += _parse_section_upper(head) * 10 ** 4
        yuan += _parse_section_upper(yuan_part)
    d = Decimal(yuan) + Decimal(jiao) / 10 + Decimal(fen) / 100
    return -d if neg else d


def format_money(value, sep: str = ",") -> str:
    """千分位展示（仅展示层；金额计算永远走 Decimal 原值）。"""
    d = quantize2(value)
    neg = d < 0
    s = f"{abs(d):.2f}"
    int_part, _, frac = s.partition(".")
    grouped = ""
    while len(int_part) > 3:
        grouped = sep + int_part[-3:] + grouped
        int_part = int_part[:-3]
    return ("-" if neg else "") + int_part + grouped + "." + frac
