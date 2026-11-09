# -*- coding: utf-8 -*-
"""图像读取与预处理的小工具（B5）。

集中解决三个问题：
1. **EXIF 方向**：手机竖拍图带 Orientation 标记，不修正会让特征完全错向
2. **成像域一致性**：注册图（本地文件）与实时帧（摄像头 640×480）走同一条
   「转 PIL → 中心 ROI 裁切 → ResNet 预处理」路径，避免两边特征漂移
3. **BGR/RGB 混乱**：OpenCV 读进来是 BGR，PIL 是 RGB，统一在这里转
"""
import numpy as np


def to_pil(image):
    """PIL.Image / 路径 / BGR ndarray / RGB ndarray → PIL.Image。

    返回的图像已经做过 EXIF 方向修正（对 ndarray 无影响）。
    """
    from PIL import Image, ImageOps

    if isinstance(image, Image.Image):
        pil = image
    elif isinstance(image, str):
        pil = Image.open(image)
    else:
        arr = np.asarray(image)
        if arr.ndim == 2:
            pil = Image.fromarray(arr.astype("uint8"), mode="L")
        elif arr.ndim == 3 and arr.shape[2] == 3:
            pil = Image.fromarray(arr[:, :, ::-1].astype("uint8"))  # BGR → RGB
        elif arr.ndim == 3 and arr.shape[2] == 4:
            pil = Image.fromarray(arr[:, :, [2, 1, 0, 3]].astype("uint8"))
        else:
            raise ValueError("不支持的图像形状：%s" % (arr.shape,))

    try:
        pil = ImageOps.exif_transpose(pil)
    except Exception:
        pass
    return pil


def load_image(path):
    """从文件读图，返回 RGB 的 PIL.Image（已修正 EXIF 方向）。"""
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        try:
            from PIL import ImageOps
            im = ImageOps.exif_transpose(im)
        except Exception:
            pass
        return im.copy()


def center_roi(image, ratio=1.0):
    """按比例取中心区域。

    ratio=1.0 原样返回；0.75 表示保留中间 75%（宽高各留 75% 边长）。
    物体通常位于画面中间，裁掉四周能显著降低背景对特征的干扰。
    """
    try:
        ratio = float(ratio)
    except (TypeError, ValueError):
        ratio = 1.0
    if ratio >= 1.0 or ratio <= 0.05:
        return image

    w, h = image.size
    cw, ch = int(round(w * ratio)), int(round(h * ratio))
    left, top = (w - cw) // 2, (h - ch) // 2
    return image.crop((left, top, left + cw, top + ch))


def thumbnail(image, max_side=320):
    """等比缩到最长边不超过 max_side（原地修改并返回）。"""
    img = image.copy()
    img.thumbnail((max_side, max_side))
    return img


def is_image_file(path):
    """按扩展名粗筛，避免把 .txt 改名成 .jpg 的文件塞进模型。"""
    return str(path).lower().rsplit(".", 1)[-1] in (
        "jpg", "jpeg", "png", "bmp", "webp", "tif", "tiff")
