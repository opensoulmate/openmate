"""路线图 7.4 图像生成/编辑 单元测试。

覆盖：生成面（纯色/渐变/文本卡真实产物回读）、编辑面（操作链真实产物回读）、
信息面、校验面（规模/颜色/操作/路径越界/扩展名）、原子落盘、字体接缝、
soulmate_agent 接线护栏 + __new__ 真实 dispatch 烟测。
Pillow 缺失时整单跳过（运行时 venv 实测有 Pillow）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from agent.image_generator import (  # noqa: E402
    MAX_OPS,
    MAX_SIDE,
    OUT_EXTS,
    ImageGenerator,
    ImageGeneratorError,
    _find_font_path,
    _parse_background,
    _parse_color,
    _parse_size,
    _resolve_target,
    get_image_generator,
)


def _reopened(path: Path):
    img = Image.open(path)
    img.load()
    return img


# ── 生成面（真实产物回读） ─────────────────────────────────────


def test_create_solid_real_product(tmp_path):
    gen = ImageGenerator()
    out = gen.create(width=200, height=100, background="#ff0000",
                     path=str(tmp_path / "solid.png"))
    assert "✅" in out and "MEDIA:" in out and "200x100" in out
    img = _reopened(tmp_path / "solid.png")
    assert img.size == (200, 100)
    assert img.getpixel((10, 10)) == (255, 0, 0)


def test_create_gradient_real_product(tmp_path):
    gen = ImageGenerator()
    out = gen.create(width=50, height=50, background="gradient:#000000:#ffffff",
                     path=str(tmp_path / "grad.png"))
    assert "✅" in out
    img = _reopened(tmp_path / "grad.png")
    top = img.getpixel((25, 0))
    bottom = img.getpixel((25, 49))
    assert top != bottom  # 渐变两端颜色确实不同
    assert sum(top) < sum(bottom)  # 上黑下白


def test_create_text_card_real_product(tmp_path):
    gen = ImageGenerator()
    out = gen.create(width=480, height=320, background="#1f2937",
                     title="测试标题标题标题标题标题标题标题标题",
                     body="这是一段正文内容，测试中文自动换行是否生效。ABC 123 xyz "
                          * 4,
                     path=str(tmp_path / "card.png"))
    assert "✅" in out
    img = _reopened(tmp_path / "card.png")
    assert img.size == (480, 320)
    # 文本已绘制：画布上存在非背景色像素
    bg = (31, 41, 55)
    pixels = [img.getpixel((x, y))
              for x in range(0, 480, 16) for y in range(0, 320, 16)]
    assert any(p != bg for p in pixels)


def test_create_auto_named_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SOULMATE_IMAGE_ROOTS", str(tmp_path))
    monkeypatch.setattr("agent.image_generator.Path.home", lambda: tmp_path)
    gen = ImageGenerator()
    out = gen.create(width=32, height=32)
    assert "✅" in out and "MEDIA:" in out
    # 自动命名落在 ~/.openmate/images/
    named = [p for p in (tmp_path / ".openmate" / "images").glob("img_*.png")]
    assert len(named) == 1


def test_create_no_ext_auto_appends_png(tmp_path):
    gen = ImageGenerator()
    out = gen.create(width=32, height=32, path=str(tmp_path / "noext"))
    assert "✅" in out and (tmp_path / "noext.png").is_file()


# ── 校验面（生成） ─────────────────────────────────────────────


def test_size_validation():
    for bad in ((0, 100), (100, -1), ("abc", 10)):
        with pytest.raises(ImageGeneratorError):
            _parse_size(*bad)
    with pytest.raises(ImageGeneratorError):  # 单边超限
        _parse_size(MAX_SIDE + 1, 4)


def test_create_bad_size_reports(tmp_path):
    gen = ImageGenerator()
    out = gen.create(width=0, height=10, path=str(tmp_path / "x.png"))
    assert out.startswith("[错误]") and "尺寸" in out
    assert not (tmp_path / "x.png").exists()  # 失败不落产物


def test_bad_color_and_gradient_rejected():
    with pytest.raises(ImageGeneratorError):
        _parse_color("不存在的颜色xyz")
    assert _parse_background("gradient:#000:#fff")[0] == "gradient"
    with pytest.raises(ImageGeneratorError):
        _parse_background("gradient:#000")  # 缺第二个颜色
    with pytest.raises(ImageGeneratorError):
        _parse_background("gradient:坏颜色:#fff")


def test_out_of_roots_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("SOULMATE_IMAGE_ROOTS", str(tmp_path))
    with pytest.raises(ImageGeneratorError):
        _resolve_target("/etc/evil.png", tmp_path, "x")


def test_bad_extension_rejected(tmp_path):
    with pytest.raises(ImageGeneratorError):
        _resolve_target(str(tmp_path / "x.gif"), tmp_path, "x")
    assert OUT_EXTS[".png"] == "PNG"


def test_explicit_font_missing_reports(tmp_path):
    gen = ImageGenerator()
    out = gen.create(width=100, height=100, title="标题",
                     font="/nonexistent/font.ttf", path=str(tmp_path / "f.png"))
    assert out.startswith("[错误]") and "字体" in out


def test_font_resolver_env_and_search(monkeypatch):
    monkeypatch.setattr("agent.image_generator._FONT_CANDIDATES", ())
    monkeypatch.delenv("SOULMATE_IMAGE_FONT", raising=False)
    assert _find_font_path(None) is None  # 全无候选 → None（画文本时显式报因）
    monkeypatch.setenv("SOULMATE_IMAGE_FONT", "/nonexistent/f.ttf")
    with pytest.raises(ImageGeneratorError):
        _find_font_path(None)


# ── 编辑面（真实产物回读） ─────────────────────────────────────


@pytest.fixture()
def src_image(tmp_path):
    p = tmp_path / "src.png"
    Image.new("RGB", (200, 100), (10, 200, 10)).save(p)
    return p


def test_edit_op_chain_real_product(tmp_path, src_image):
    gen = ImageGenerator()
    out = gen.edit(str(src_image), ops=[
        {"op": "crop", "x": 0, "y": 0, "width": 100, "height": 100},
        {"op": "resize", "width": 50, "height": 50},
        {"op": "rotate", "degrees": 90},
        {"op": "flip", "direction": "h"},
        {"op": "brightness", "factor": 1.2},
        {"op": "contrast", "factor": 0.9},
        {"op": "watermark", "text": "水印", "position": "center"},
    ], path=str(tmp_path / "out.png"))
    assert "✅" in out and "MEDIA:" in out and "操作 7 步" in out
    img = _reopened(tmp_path / "out.png")
    assert img.size == (50, 50)  # rotate expand=False默认True后90度swap？rotate expand=1 → 50x50 交换后仍 50x50
    assert img.format == "PNG"


def test_edit_convert_to_jpeg(tmp_path, src_image):
    gen = ImageGenerator()
    out = gen.edit(str(src_image),
                   ops=[{"op": "resize", "width": 40, "height": 20}],
                   path=str(tmp_path / "out.jpg"))
    assert "✅" in out and "JPEG" in out
    img = _reopened(tmp_path / "out.jpg")
    assert img.format == "JPEG" and img.size == (40, 20)


def test_edit_rotate_expand_swaps_dims(tmp_path, src_image):
    gen = ImageGenerator()
    out = gen.edit(str(src_image), ops=[{"op": "rotate", "degrees": 90}],
                   path=str(tmp_path / "rot.png"))
    assert "✅" in out
    img = _reopened(tmp_path / "rot.png")
    assert img.size == (100, 200)  # expand=True：200x100 → 100x200


# ── 校验面（编辑） ─────────────────────────────────────────────


def test_edit_empty_ops_rejected(tmp_path, src_image):
    gen = ImageGenerator()
    for bad in (None, [], "resize"):
        out = gen.edit(str(src_image), ops=bad, path=str(tmp_path / "o.png"))
        assert out.startswith("[错误]") and "ops" in out


def test_edit_too_many_ops_rejected(tmp_path, src_image):
    gen = ImageGenerator()
    ops = [{"op": "flip", "direction": "h"}] * (MAX_OPS + 1)
    out = gen.edit(str(src_image), ops=ops, path=str(tmp_path / "o.png"))
    assert out.startswith("[错误]") and "操作条数超限" in out


def test_edit_unknown_op_reports_index(tmp_path, src_image):
    gen = ImageGenerator()
    out = gen.edit(str(src_image), ops=[
        {"op": "flip", "direction": "h"},
        {"op": "魔改"},
    ], path=str(tmp_path / "o.png"))
    assert out.startswith("[错误]") and "第 2 步" in out and "未知操作" in out


def test_edit_bad_op_params_rejected(tmp_path, src_image):
    gen = ImageGenerator()
    cases = [
        [{"op": "resize", "width": 0, "height": 5}],          # 非正
        [{"op": "crop", "x": 0, "y": 0, "width": 9999, "height": 5}],  # 越界
        [{"op": "rotate", "degrees": 9999}],                  # 角度超范围
        [{"op": "flip", "direction": "x"}],                   # 方向非法
        [{"op": "brightness", "factor": 99}],                 # 因子超范围
        [{"op": "watermark", "text": ""}],                    # 空水印
        [{"op": "watermark", "text": "x", "position": "天上"}],  # 位置非法
        [{"op": "watermark", "text": "x", "opacity": 5}],     # 不透明度超范围
        ["not-a-dict"],                                       # 非对象
    ]
    for ops in cases:
        out = gen.edit(str(src_image), ops=ops, path=str(tmp_path / "o.png"))
        assert out.startswith("[错误]"), ops


def test_edit_missing_source_reports(tmp_path):
    gen = ImageGenerator()
    out = gen.edit(str(tmp_path / "不存在.png"),
                   ops=[{"op": "flip", "direction": "h"}])
    assert out.startswith("[错误]") and "不存在" in out


def test_edit_non_image_source_reports(tmp_path):
    p = tmp_path / "text.txt"
    p.write_text("不是图片", encoding="utf-8")
    gen = ImageGenerator()
    out = gen.edit(str(p), ops=[{"op": "flip", "direction": "h"}])
    assert out.startswith("[错误]") and "无法解析为图像" in out


def test_edit_source_out_of_roots_rejected(tmp_path, monkeypatch, src_image):
    monkeypatch.setenv("SOULMATE_IMAGE_ROOTS", "/nonexistent-root-xyz")
    gen = ImageGenerator()
    out = gen.edit(str(src_image), ops=[{"op": "flip", "direction": "h"}])
    assert out.startswith("[错误]") and "越界" in out


# ── 信息面 ─────────────────────────────────────────────────────


def test_info_real_and_error(tmp_path, src_image):
    gen = ImageGenerator()
    out = gen.info(str(src_image))
    assert "200x100" in out and "PNG" in out and "RGB" in out
    out = gen.info(str(tmp_path / "不存在.png"))
    assert out.startswith("[错误]")


# ── 原子落盘 ───────────────────────────────────────────────────


def test_atomic_save_failure_leaves_no_partial(tmp_path, monkeypatch, src_image):
    gen = ImageGenerator()

    def boom(*a, **k):
        raise IOError("磁盘炸了")

    monkeypatch.setattr(Image.Image, "save", boom)
    out = gen.edit(str(src_image), ops=[{"op": "flip", "direction": "h"}],
                   path=str(tmp_path / "o.png"))
    assert out.startswith("[错误]")
    leftovers = [p for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert leftovers == [] and not (tmp_path / "o.png").exists()


# ── 单例 ───────────────────────────────────────────────────────


def test_singleton():
    a = get_image_generator()
    b = get_image_generator()
    assert a is b


# ── soulmate_agent 接线护栏 + 真实 dispatch 烟测 ────────────────


def test_soulmate_agent_wiring_guards():
    src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    # 工具定义
    assert '"name": "image_act"' in src
    # 两处dispatch点：主循环 + code-mode内层
    assert src.count('func_name == "image_act"') >= 2
    # 分发helper + lazy-init + 顶部import
    assert "def _image_dispatch" in src
    assert "_get_image_generator" in src
    assert "from agent.image_generator import" in src
    # 系统提示工具清单
    assert "image_act: 图像生成/编辑" in src


def test_image_dispatch_smoke(monkeypatch, tmp_path):
    """__new__构造+lazy-init真实dispatch烟测：create/edit/info/未知action四态。"""
    import agent.soulmate_agent as sa

    gen = ImageGenerator()
    monkeypatch.setattr(sa, "_get_image_generator", lambda: gen)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)  # 不过__init__

    out = agent._image_dispatch({
        "action": "create", "width": 120, "height": 60,
        "background": "#123456", "title": "烟测标题",
        "path": str(tmp_path / "烟测.png"),
    })
    assert "✅" in out and "MEDIA:" in out and (tmp_path / "烟测.png").is_file()

    out = agent._image_dispatch({
        "action": "edit", "source": str(tmp_path / "烟测.png"),
        "ops": [{"op": "resize", "width": 60, "height": 30}],
        "path": str(tmp_path / "烟测2.png"),
    })
    assert "✅" in out and (tmp_path / "烟测2.png").is_file()

    out = agent._image_dispatch({"action": "info", "source": str(tmp_path / "烟测.png")})
    assert "120x60" in out

    out = agent._image_dispatch({"action": "不存在"})
    assert out.startswith("[错误]") and "未知 action" in out
    # lazy-init 已缓存实例
    assert getattr(agent, "_image_generator", None) is gen


def test_image_dispatch_prompt_fstring_builds():
    """系统提示是 f-string：image_act 行构造不炸、花括号转义正确。"""
    import agent.soulmate_agent as sa
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)
    prompt = agent._build_base_system_prompt("/tmp")
    assert "image_act: 图像生成/编辑" in prompt
    assert "{{" not in prompt  # 转义被正确还原
