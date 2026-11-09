# -*- coding: utf-8 -*-
"""特征提取器：加载 ImageNet 预训练 ResNet18，输出 512 维特征向量。

取 avgpool 之后、fc 之前的输出（512 维），作为通用视觉特征。

预处理一致性（修复 P1-R1/R2）：
    注册图（本地 jpg，可能竖拍、带 EXIF）与实时帧（摄像头 640×480 BGR）
    必须走**完全相同**的路径：
        to_pil(修正 EXIF) → center_roi(ROI_RATIO) → Resize256/CenterCrop224/Normalize
    这样同一物体在两条通道上的特征才可比；中心 ROI 同时压掉了一部分背景，
    降低「同一张桌上的未录入物体误过阈值」的概率。
"""
import numpy as np
import torch
import torch.nn as nn
from torchvision import models, transforms
from torchvision.models import ResNet18_Weights

import config
from utils.image import center_roi, load_image, to_pil


class FeatureExtractor:
    """封装 ResNet18 特征提取。"""

    def __init__(self, device=None, roi_ratio=None):
        # 自动检测设备
        if device is None:
            if config.DEVICE == "cuda" and torch.cuda.is_available():
                self.device = "cuda"
            else:
                self.device = "cpu"
        else:
            self.device = device

        self.roi_ratio = float(
            roi_ratio if roi_ratio is not None
            else getattr(config, "ROI_RATIO", 1.0))

        # 加载 ImageNet 预训练 ResNet18，去掉分类头
        self.model = models.resnet18(weights=ResNet18_Weights.IMAGENET1K_V1)
        self.model.fc = nn.Identity()  # 保留 avgpool -> 512 维输出
        self.model.to(self.device)
        self.model.eval()

        # 预处理必须与训练时一致
        self.transform = transforms.Compose([
            transforms.Resize(256),
            transforms.CenterCrop(config.IMG_SIZE),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ])

    # ---------- 参数 ----------
    def set_roi_ratio(self, ratio):
        """运行时调整 ROI 比例（评测脚本扫参用）。"""
        try:
            self.roi_ratio = max(0.05, min(1.0, float(ratio)))
        except (TypeError, ValueError):
            pass
        return self.roi_ratio

    # ---------- 前向 ----------
    @torch.no_grad()
    def _forward(self, batch):
        feat = self.model(batch)
        return feat

    @torch.no_grad()
    def extract(self, image):
        """从一张图像提取特征。

        参数:
            image: PIL.Image / 图片路径 / numpy.ndarray(BGR 或 RGB) / 已归一化 Tensor(C,H,W)
        返回:
            numpy.ndarray, shape (512,)，已做 L2 归一化
        """
        if isinstance(image, torch.Tensor):
            img = image
            if img.dim() == 4:
                img = img.squeeze(0)
            # 已经是网络输入张量，不再做 ROI / transform
        else:
            pil = to_pil(image) if not isinstance(image, str) else load_image(image)
            pil = center_roi(pil, self.roi_ratio)
            img = self.transform(pil)  # C,H,W

        img = img.unsqueeze(0).to(self.device)
        feat = self.model(img)  # (1, 512)
        feat = feat.squeeze(0).cpu().numpy()

        norm = np.linalg.norm(feat)
        if norm > 0:
            feat = feat / norm
        return feat.astype("float32")

    @torch.no_grad()
    def extract_from_file(self, image_path):
        """从图片文件提取特征（自动修正 EXIF 方向）。"""
        return self.extract(load_image(image_path))

    @torch.no_grad()
    def extract_batch(self, images):
        """批量提特征。images 为路径或图像列表，返回 (N,512) float32。"""
        if not images:
            return np.zeros((0, config.FEATURE_DIM), dtype="float32")
        feats = [self.extract(im) for im in images]
        return np.stack(feats).astype("float32")

    @torch.no_grad()
    def extract_tta(self, image):
        """测试时增强（TTA）：原图 + 水平翻转，取平均特征。

        对「类内多实例、外观差异大」的场景，翻转增强能提高特征稳定性，
        减少拍摄角度/方向带来的偶然波动。
        """
        if isinstance(image, str):
            pil = load_image(image)
        elif isinstance(image, torch.Tensor):
            return self.extract(image)
        else:
            pil = to_pil(image)

        pil = center_roi(pil, self.roi_ratio)
        f_orig = self.extract(self.transform(pil))
        f_flip = self.extract(self.transform(pil.transpose(_FLIP_LR)))
        feat = (f_orig + f_flip) / 2.0
        norm = np.linalg.norm(feat)
        if norm > 0:
            feat = feat / norm
        return feat.astype("float32")

    def warmup(self):
        """预热：跑一次前向，避免用户看到的第一帧特别慢。"""
        try:
            dummy = torch.zeros(1, 3, config.IMG_SIZE, config.IMG_SIZE,
                                device=self.device)
            with torch.no_grad():
                self.model(dummy)
            return True
        except Exception:
            return False


# PIL 的翻转常量，避免在多处重复导入 Image
try:
    from PIL import Image as _PILImage
    _FLIP_LR = _PILImage.FLIP_LEFT_RIGHT
except Exception:  # pragma: no cover
    _FLIP_LR = 0
