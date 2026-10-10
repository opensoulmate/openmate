"""test_image_tools.py — 路线图 7.4 图像生成/编辑工具面单测（确定性 Pillow 处理，
生成走注入 fake generator，不打网络）。

覆盖诚实多态：生成双后端（AI/程序化兜底显式标注）/AI 失败不假称成功/编辑各动作
尺寸与像素实证/参数拒绝/文件名白名单/0字节与不可解码不算成功/接线护栏 + __new__
真实 dispatch 烟测 + lazy-init 构造。（真实 Pillow 全链路实证走"真实机器烟测"。）
"""
from pathlib import Path

import pytest

from agent.image_tools import ImageTools, ACTIONS, EDIT_ACTIONS


def _mk(tmp_path, generator=None, now=None):
    return ImageTools(out_dir=str(tmp_path / "imgs"), generator=generator,
                      now=now or (lambda: 1700000000.0))


def _src_img(tmp_path, w=100, h=80, color=(200, 30, 40)):
    from PIL import Image
    p = tmp_path / "src.png"
    Image.new("RGB", (w, h), color).save(str(p))
    return p


def _gen_ok_png(prompt, w, h):
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 200, 10)).save(buf, format="PNG")
    return buf.getvalue()


def _out_path(out: str) -> Path:
    return Path(out.split("MEDIA:")[-1].strip())


# ── 编辑：确定性动作 ─────────────────────────────────────────────────

class TestEdit:
    def test_resize_dims(self, tmp_path):
        tools = _mk(tmp_path)
        src = _src_img(tmp_path)
        out = tools.run("resize", {"path": str(src), "width": 50, "height": 40})
        assert "✅ 图像已处理（resize）" in out and "MEDIA:" in out
        p = _out_path(out)
        from PIL import Image
        with Image.open(str(p)) as im:
            assert im.size == (50, 40)

    def test_crop_dims_and_bounds(self, tmp_path):
        tools = _mk(tmp_path)
        src = _src_img(tmp_path, 100, 80)
        out = tools.run("crop", {"path": str(src), "box": [10, 10, 30, 20]})
        p = _out_path(out)
        from PIL import Image
        with Image.open(str(p)) as im:
            assert im.size == (30, 20)
        # 越界/非法 box 显式拒绝
        assert "❌ 参数错误" in tools.run("crop", {"path": str(src), "box": [90, 90, 50, 50]})
        assert "❌ 参数错误" in tools.run("crop", {"path": str(src), "box": [0, 0, -1, 5]})
        assert "❌ 参数错误" in tools.run("crop", {"path": str(src), "box": [1, 2]})

    def test_rotate_expand_swaps_dims(self, tmp_path):
        tools = _mk(tmp_path)
        src = _src_img(tmp_path, 100, 80)
        out = tools.run("rotate", {"path": str(src), "angle": 90})
        from PIL import Image
        with Image.open(str(_out_path(out))) as im:
            assert im.size == (80, 100)  # expand=True
        assert "❌ 参数错误" in tools.run("rotate", {"path": str(src), "angle": 999})

    def test_flip_pixel_mirror(self, tmp_path):
        tools = _mk(tmp_path)
        from PIL import Image
        im = Image.new("RGB", (4, 2), (0, 0, 0))
        im.putpixel((0, 0), (255, 0, 0))
        src = tmp_path / "asym.png"
        im.save(str(src))
        out = tools.run("flip", {"path": str(src), "direction": "horizontal"})
        with Image.open(str(_out_path(out))) as f:
            assert f.getpixel((3, 0)) == (255, 0, 0) and f.getpixel((0, 0)) == (0, 0, 0)
        assert "❌ 参数错误" in tools.run("flip", {"path": str(src), "direction": "diagonal"})

    def test_convert_format_and_jpeg_flatten(self, tmp_path):
        tools = _mk(tmp_path)
        from PIL import Image
        rgba = tmp_path / "t.png"
        Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(str(rgba))  # 全透明
        out = tools.run("convert", {"path": str(rgba), "format": "jpg"})
        p = _out_path(out)
        assert p.suffix == ".jpg" and p.read_bytes()[:2] == b"\xff\xd8"
        with Image.open(str(p)) as im:
            assert im.mode == "RGB"
            assert im.getpixel((5, 5)) == (255, 255, 255)  # 透明→白底
        assert "❌ 参数错误" in tools.run("convert", {"path": str(rgba), "format": "tiff"})

    def test_annotate_draws_text(self, tmp_path):
        tools = _mk(tmp_path)
        src = _src_img(tmp_path, 200, 100, (0, 0, 0))
        out = tools.run("annotate", {"path": str(src), "text": "水印文字",
                                     "position": "center", "color": "#FFFFFF"})
        p = _out_path(out)
        from PIL import Image
        with Image.open(str(p)) as im:
            assert im.convert("L").getextrema()[1] > 100  # 黑底上画了白字
        # 参数拒绝
        assert "❌ 参数错误" in tools.run("annotate", {"path": str(src), "text": ""})
        assert "❌ 参数错误" in tools.run("annotate", {"path": str(src), "text": "x", "position": "lefttop"})
        assert "❌ 参数错误" in tools.run("annotate", {"path": str(src), "text": "x", "color": "rainbow"})
        # x,y 显式坐标可用
        out2 = tools.run("annotate", {"path": str(src), "text": "hi", "position": "5,10"})
        assert "✅" in out2

    def test_compose_layouts(self, tmp_path):
        tools = _mk(tmp_path)
        a = _src_img(tmp_path, 100, 50)
        b = tmp_path / "b.png"
        from PIL import Image
        Image.new("RGB", (60, 30), (0, 255, 0)).save(str(b))
        out = tools.run("compose", {"path": str(a), "path2": str(b), "layout": "horizontal"})
        with Image.open(str(_out_path(out))) as im:
            assert im.size == (160, 50)
        out = tools.run("compose", {"path": str(a), "path2": str(b), "layout": "vertical"})
        with Image.open(str(_out_path(out))) as im:
            assert im.size == (100, 80)
        out = tools.run("compose", {"path": str(a), "path2": str(b), "layout": "overlay",
                                    "position": [10, 5]})
        with Image.open(str(_out_path(out))) as im:
            assert im.size == (100, 50)
        assert "❌ 参数错误" in tools.run(
            "compose", {"path": str(a), "path2": str(b), "layout": "overlay", "position": [90, 40]})
        assert "❌ 参数错误" in tools.run("compose", {"path": str(a), "path2": str(b), "layout": "circle"})

    def test_thumbnail_caps(self, tmp_path):
        tools = _mk(tmp_path)
        src = _src_img(tmp_path, 100, 80)
        out = tools.run("thumbnail", {"path": str(src), "width": 50, "height": 50})
        with _PILopen(_out_path(out)) as im:
            assert im.width <= 50 and im.height <= 50


def _PILopen(p):
    from PIL import Image
    return Image.open(str(p))


# ── 生成：双后端诚实多态 ─────────────────────────────────────────────

class TestGenerate:
    def test_procedural_fallback_honest_label(self, tmp_path):
        tools = _mk(tmp_path)  # 无 API 后端 → 程序化绘制
        out = tools.run("generate", {"prompt": "一座雪山", "width": 200, "height": 120})
        assert "程序化绘制（非AI生成" in out and "不理解画面语义" in out  # 诚实标注，禁假称 AI
        assert "MEDIA:" in out and "generate(程序化绘制)" in out
        p = _out_path(out)
        assert p.exists() and p.stat().st_size > 0
        with _PILopen(p) as im:
            assert im.size == (200, 120)

    def test_ai_backend_success_no_fallback_label(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOULMATE_IMAGEGEN_API_URL", "http://fake")
        tools = _mk(tmp_path, generator=_gen_ok_png)
        out = tools.run("generate", {"prompt": "一只猫", "width": 64, "height": 64})
        assert "generate(AI)" in out and "程序化绘制" not in out
        with _PILopen(_out_path(out)) as im:
            assert im.size == (64, 64) and im.getpixel((5, 5)) == (10, 200, 10)

    def test_ai_backend_failure_no_fake_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOULMATE_IMAGEGEN_API_URL", "http://fake")

        def boom(prompt, w, h):
            raise RuntimeError("API 500")
        tools = _mk(tmp_path, generator=boom)
        out = tools.run("generate", {"prompt": "x"})
        assert out.startswith("❌ AI 图像生成失败") and "API 500" in out
        assert "✅" not in out                    # 不假称成功、不降级冒充
        assert "程序化绘制" not in out.split("\n")[0]

    def test_ai_backend_empty_content_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOULMATE_IMAGEGEN_API_URL", "http://fake")
        tools = _mk(tmp_path, generator=lambda p, w, h: b"")
        out = tools.run("generate", {"prompt": "x"})
        assert "❌" in out and "空内容" in out

    def test_ai_backend_garbage_bytes_not_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOULMATE_IMAGEGEN_API_URL", "http://fake")
        tools = _mk(tmp_path, generator=lambda p, w, h: b"\x00\x01notanimage")
        out = tools.run("generate", {"prompt": "x"})
        assert "❌" in out and "无效" in out and "✅" not in out

    def test_generate_requires_prompt(self, tmp_path):
        tools = _mk(tmp_path)
        assert "❌ 参数错误" in tools.run("generate", {})


# ── 参数拒绝 / 路径安全 / 0字节 ──────────────────────────────────────

class TestRejects:
    def test_unknown_action(self, tmp_path):
        tools = _mk(tmp_path)
        out = tools.run("delete", {"path": "/x"})
        assert "❌ 未知 action" in out
        assert set(EDIT_ACTIONS) < set(ACTIONS) and "generate" in ACTIONS

    def test_missing_input_file(self, tmp_path):
        tools = _mk(tmp_path)
        out = tools.run("resize", {"path": str(tmp_path / "nope.png"), "width": 10, "height": 10})
        assert "❌ 参数错误" in out and "不存在" in out

    def test_undecodable_input(self, tmp_path):
        tools = _mk(tmp_path)
        bad = tmp_path / "bad.png"
        bad.write_bytes(b"garbage")
        out = tools.run("resize", {"path": str(bad), "width": 10, "height": 10})
        assert "❌" in out and "不可解码" in out

    def test_filename_traversal_rejected(self, tmp_path):
        tools = _mk(tmp_path)
        src = _src_img(tmp_path)
        out = tools.run("resize", {"path": str(src), "width": 10, "height": 10,
                                   "filename": "../evil.png"})
        assert "❌ 参数错误" in out and ("非法" in out or "越界" in out)
        assert not (tmp_path / "evil.png").exists()

    def test_auto_filename_lands_in_outdir(self, tmp_path):
        tools = _mk(tmp_path)
        src = _src_img(tmp_path)
        out = tools.run("resize", {"path": str(src), "width": 10, "height": 10})
        p = _out_path(out)
        assert p.parent == tools.out_dir.resolve() and p.name.startswith("src-resize-")

    def test_bad_size_rejected(self, tmp_path):
        tools = _mk(tmp_path)
        src = _src_img(tmp_path)
        assert "❌ 参数错误" in tools.run("resize", {"path": str(src), "width": 99999, "height": 10})


# ── 接线护栏 + dispatch 烟测 ─────────────────────────────────────────

def _src() -> str:
    return (Path(__file__).resolve().parent.parent
            / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")


def test_wiring_source_guards():
    src = _src()
    # 工具定义进 LLM 工具面
    assert '"name": "image"' in src
    assert "builtin_tools.extend(image_tool)" in src
    # 主循环 dispatch + code-mode 批内 dispatch 两处调用 + 共享入口定义
    assert src.count("self._image_dispatch(func_args, session_id)") == 2
    assert "def _image_dispatch" in src
    # 系统提示工具清单
    assert "- image: 图像生成/编辑" in src
    # 顶部 import
    assert "from agent.image_tools import ImageTools" in src
    # sandbox 出网归类
    sa = (Path(__file__).resolve().parent.parent / "agent" / "sandbox_policy.py").read_text(encoding="utf-8")
    assert '"image"' in sa


def test_real_dispatch_smoke(tmp_path):
    from agent.soulmate_agent import SoulMateAgent
    agent = SoulMateAgent.__new__(SoulMateAgent)
    src = _src_img(tmp_path)
    agent._image_tools_inst = _mk(tmp_path)
    out = agent._image_dispatch({"action": "resize", "path": str(src),
                                 "width": 20, "height": 20})
    assert "✅ 图像已处理（resize）" in out
    assert _out_path(out).exists()
    # 未知 action 也走共享入口显式报因
    assert "❌ 未知 action" in agent._image_dispatch({"action": "wat"})


def test_dispatch_lazy_init_constructs_image_tools(monkeypatch):
    import agent.soulmate_agent as sa

    made = {}

    class FakeTools:
        def __init__(self, *a, **k):
            made["init"] = True

        def run(self, action, params):
            return f"FAKE:{action}:{params.get('path', '')}"

    monkeypatch.setattr(sa, "ImageTools", FakeTools)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)
    out = agent._image_dispatch({"action": "flip", "path": "/tmp/x.png"})
    assert made.get("init") is True          # lazy-init 真的构造了
    assert out == "FAKE:flip:/tmp/x.png"     # 参数透传（action剥离）
