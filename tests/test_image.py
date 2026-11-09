# -*- coding: utf-8 -*-
"""图像预处理测试（B5）。只依赖 PIL + numpy，不需要 torch。"""
import numpy as np
import pytest
from PIL import Image

from utils.image import center_roi, is_image_file, load_image, thumbnail, to_pil


def test_center_roi_keeps_size_when_disabled():
    im = Image.new("RGB", (400, 300))
    assert center_roi(im, 1.0).size == (400, 300)


def test_center_roi_shrinks_to_ratio():
    im = Image.new("RGB", (400, 300))
    assert center_roi(im, 0.75).size == (300, 225)


def test_center_roi_actually_crops_center():
    im = Image.new("RGB", (10, 10), (0, 0, 0))
    im.putpixel((5, 5), (255, 0, 0))     # 中心红点
    im.putpixel((0, 0), (0, 255, 0))     # 角落绿点
    out = center_roi(im, 0.5)
    colors = {c for _n, c in out.getcolors(maxcolors=1000)}
    assert (255, 0, 0) in colors
    assert (0, 255, 0) not in colors     # 角落被裁掉


def test_to_pil_converts_bgr_to_rgb():
    bgr = np.zeros((4, 4, 3), dtype="uint8")
    bgr[:, :, 0] = 255                   # B 通道拉满
    assert to_pil(bgr).convert("RGB").getpixel((0, 0)) == (0, 0, 255)


def test_to_pil_accepts_pil_and_rejects_bad_shape():
    assert to_pil(Image.new("RGB", (8, 8))).size == (8, 8)
    with pytest.raises(ValueError):
        to_pil(np.zeros((2, 2, 7), dtype="uint8"))


def test_load_image_applies_exif_orientation(tmp_path):
    """竖拍照片：像素是横的，但 EXIF 要求旋转 90°。"""
    p = tmp_path / "rot.jpg"
    im = Image.new("RGB", (400, 200), (10, 20, 30))
    exif = Image.Exif()
    exif[0x0112] = 6                     # Orientation = 顺时针 90°
    im.save(p, exif=exif)

    assert load_image(str(p)).size == (200, 400)     # 已旋转
    with Image.open(p) as raw:
        assert raw.size == (400, 200)                 # 原文件没被改动


def test_thumbnail_limits_long_side():
    assert max(thumbnail(Image.new("RGB", (1000, 500)), 320).size) == 320


def test_is_image_file():
    assert is_image_file("a.JPG") is True
    assert is_image_file("a.txt") is False
