"""C3 数值精算（money.py）：Decimal 报价/折扣/税率/人民币大小写。

覆盖：float 拒绝（禁 LLM 算数入金额链）/ 超两位小数拒绝 / 行小计到分 /
报价合计+折扣+税全链 / 折扣正反算 / 含税拆税与加税 / 大写边界
（路线图口径 13356305.2→壹仟叁佰叁拾伍万陆仟叁佰零伍元贰角；0.7→柒角整）
/ 大写↔小写往返 / 千分位展示。
"""

from decimal import Decimal

import pytest

from agent.money import (
    MoneyError, apply_discount, calc_discount, format_money, line_total,
    quote_total, quantize2, rmb_lower, rmb_upper, tax_from_inclusive,
    tax_to_inclusive, to_decimal,
)


# ── Decimal 基元：禁 float / 禁超分 ──────────────────────────────────

def test_to_decimal_rejects_float():
    with pytest.raises(MoneyError, match="浮点"):
        to_decimal(0.1)
    with pytest.raises(MoneyError):
        to_decimal(True)


def test_to_decimal_accepts_str_int_decimal_and_normalizes():
    assert to_decimal("1234.56") == Decimal("1234.56")
    assert to_decimal("￥1,234.56") == Decimal("1234.56")
    assert to_decimal("１２３．４５") == Decimal("123.45")   # 全角归一
    assert to_decimal(7) == Decimal("7")
    assert to_decimal(Decimal("0.30")) == Decimal("0.30")
    with pytest.raises(MoneyError, match="不是合法数字"):
        to_decimal("十二")


def test_more_than_two_decimals_rejected():
    with pytest.raises(MoneyError, match="两位小数"):
        rmb_upper("1.005")
    assert quantize2("1.005") == Decimal("1.01")      # 显式取整决策点


# ── 报价/折扣/税率 ──────────────────────────────────────────────────

def test_line_total_rounds_to_cent():
    assert line_total("3", "1999.99") == Decimal("5999.97")
    assert line_total("3", "0.333") == Decimal("1.00")   # 0.999→1.00 到分


def test_quote_total_full_chain():
    r = quote_total(
        [{"name": "甲", "qty": "3", "unit_price": "1999.99"},
         {"name": "乙", "qty": "2", "unit_price": "2500"}],
        discount="0.9", tax_rate="0.13")
    assert r["subtotal"] == "10999.97"
    assert r["discount_amount"] == "1100.00"          # 10999.97×0.1→1100.00（进位到分）
    assert r["after_discount"] == "9899.97"
    assert r["tax_amount"] == "1287.00"               # 9899.97×0.13→1287.00
    assert r["total"] == "11186.97"
    assert len(r["lines"]) == 2 and r["lines"][0]["subtotal"] == "5999.97"


def test_quote_total_validation():
    with pytest.raises(MoneyError):
        quote_total([])
    with pytest.raises(MoneyError):
        quote_total([{"qty": "1"}])                     # 缺 unit_price
    with pytest.raises(MoneyError):
        quote_total([{"qty": "1", "unit_price": "1"}], discount="1.5")
    with pytest.raises(MoneyError):
        quote_total([{"qty": "1", "unit_price": "1"}], tax_rate="2")


def test_discount_forward_and_reverse():
    fwd = apply_discount("1000", "0.85")
    assert fwd["sale_price"] == "850.00" and fwd["discount_amount"] == "150.00"
    rev = calc_discount("1000", "850")
    assert rev["rate"] == "0.8500" and rev["discount_ratio"] == "0.1500"
    with pytest.raises(MoneyError):
        apply_discount("1000", "0")
    with pytest.raises(MoneyError):
        calc_discount("1000", "1500")                   # 成交价超原价


def test_tax_bidirectional():
    r = tax_from_inclusive("11300", "0.13")
    assert r["net"] == "10000.00" and r["tax_amount"] == "1300.00"
    r2 = tax_to_inclusive("10000", "0.13")
    assert r2["tax_amount"] == "1300.00" and r2["total_incl"] == "11300.00"
    # 轧差保分：拆税再加税不漂移
    r3 = tax_from_inclusive("99.99", "0.06")
    assert Decimal(r3["net"]) + Decimal(r3["tax_amount"]) == Decimal("99.99")


# ── 人民币大写（路线图口径向量）────────────────────────────────────

@pytest.mark.parametrize("amount,expected", [
    ("13356305.2", "壹仟叁佰叁拾伍万陆仟叁佰零伍元贰角"),   # 路线图单测向量
    ("0.7", "柒角整"),                                     # 路线图单测向量
    ("0", "零元整"),
    ("10.05", "壹拾元零伍分"),
    ("1004", "壹仟零肆元整"),
    ("100000000", "壹亿元整"),
    ("20", "贰拾元整"),
    ("100500.3", "壹拾万零伍佰元叁角"),
    ("100200300.45", "壹亿零贰拾万零叁佰元肆角伍分"),
    ("0.05", "伍分"),
    ("100000005", "壹亿零伍元整"),
    ("-0.5", "负伍角整"),
    ("1234567890.12", "壹拾贰亿叁仟肆佰伍拾陆万柒仟捌佰玖拾元壹角贰分"),
])
def test_rmb_upper_vectors(amount, expected):
    assert rmb_upper(amount) == expected


@pytest.mark.parametrize("amount", [
    "13356305.2", "0.7", "0", "10.05", "1004", "100000000", "-0.5",
    "100500.3", "100200300.45", "0.05", "100000005", "20",
])
def test_rmb_lower_roundtrip(amount):
    """大写↔小写往返一致（回读校验口径）。"""
    upper = rmb_upper(amount)
    assert rmb_lower(upper) == Decimal(amount)


def test_rmb_lower_variants():
    assert rmb_lower("人民币壹拾伍元整") == Decimal("15")
    assert rmb_lower("拾伍元") == Decimal("15")           # 省位容错
    assert rmb_lower("柒角正") == Decimal("0.7")
    with pytest.raises(MoneyError):
        rmb_lower("壹拾X元整")                            # 非法字符显式报错
    with pytest.raises(MoneyError):
        rmb_lower("")


def test_format_money():
    assert format_money("12345678.9") == "12,345,678.90"
    assert format_money("-1234.5") == "-1,234.50"
