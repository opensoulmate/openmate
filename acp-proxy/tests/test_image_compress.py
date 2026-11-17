# -*- coding: utf-8 -*-
"""C6 Pillow 确定性压图管线——长边 1600/JPEG 82%/单张≤300KB 硬保证。

通用压图能力（含图交付防超上传体积上限，非投标专用）。
"""
import random
from pathlib import Path

import pytest

from agent import image_compress


def _noise_image(path, size, mode="RGB", seed=42):
    """确定性噪声图（JPEG 压不动 → 真考 300KB 硬上限）。"""
    from PIL import Image
    rng = random.Random(seed)
    w, h = size
    if mode == "RGB":
        img = Image.frombytes("RGB", (w, h), rng.randbytes(w * h * 3))
    else:
        img = Image.frombytes("RGBA", (w, h), rng.randbytes(w * h * 4))
    img.save(path)
    return path


def test_compress_hard_cap_and_dims(tmp_path):
    """超大噪声图 → 长边≤1600 且 ≤300KB 硬保证。"""
    src = _noise_image(tmp_path / "big.png", (2400, 1800))
    assert src.stat().st_size > 300 * 1024        # 前置：原图超限
    res = image_compress.compress_image(src)
    assert res["after"] <= image_compress.DEFAULT_MAX_BYTES
    assert max(res["width"], res["height"]) <= 1600
    assert res["reencoded"] is True
    assert res["dst"].endswith(".min.jpg")
    assert Path(res["dst"]).is_file()
    assert Path(res["dst"]).read_bytes()[:2] == b"\xff\xd8"   # JPEG


def test_compress_deterministic(tmp_path):
    """同输入恒出同输出（逐字节可复现）。"""
    src = _noise_image(tmp_path / "n.png", (1600, 1200), seed=7)
    a = image_compress.compress_image(src, tmp_path / "a.jpg")
    b = image_compress.compress_image(src, tmp_path / "b.jpg")
    assert (Path(a["dst"]).read_bytes() == Path(b["dst"]).read_bytes()
            and a["quality"] == b["quality"])


def test_compress_compliant_passthrough(tmp_path):
    """已合规 JPEG 原样复制不二压（防重复有损）。"""
    src = _noise_image(tmp_path / "ok.png", (800, 600), seed=1)
    small = image_compress.compress_image(src, tmp_path / "ok.jpg")
    again = image_compress.compress_image(small["dst"], tmp_path / "ok2.jpg")
    assert again["reencoded"] is False
    assert Path(again["dst"]).read_bytes() == Path(small["dst"]).read_bytes()


def test_compress_rgba_white_background(tmp_path):
    from PIL import Image
    src = tmp_path / "alpha.png"
    Image.frombytes("RGBA", (400, 400),
                    random.Random(3).randbytes(400 * 400 * 4)).save(src)
    res = image_compress.compress_image(src)
    out = Image.open(res["dst"])
    assert out.mode == "RGB"


def test_compress_batch_manifest(tmp_path):
    imgs = [_noise_image(tmp_path / f"img{i}.png", (2000, 1400), seed=i)
            for i in range(3)]
    res = image_compress.compress_batch(imgs, tmp_path / "out")
    assert res["count"] == 3
    assert res["total_after"] == sum(f["after"] for f in res["files"])
    assert res["saved_bytes"] > 0
    assert res["over_budget"] == []
    assert all(f["after"] <= image_compress.DEFAULT_MAX_BYTES
               for f in res["files"])
    # 目录模式同源收图
    res2 = image_compress.compress_batch(tmp_path, tmp_path / "out2")
    assert res2["count"] == 3


def test_compress_errors(tmp_path):
    with pytest.raises(ValueError):
        image_compress.compress_image(tmp_path / "nope.png")
    bad = tmp_path / "not-image.jpg"
    bad.write_text("plain text")
    with pytest.raises(ValueError):
        image_compress.compress_image(bad)
    with pytest.raises(ValueError):
        image_compress.compress_image(__file__, tmp_path / "x.png")
    with pytest.raises(ValueError):
        image_compress.compress_batch([], tmp_path)
