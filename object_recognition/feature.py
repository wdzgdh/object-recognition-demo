# -*- coding: utf-8 -*-
"""特征提取器：加载 ImageNet 预训练 ResNet18，输出 512 维特征向量。"""
import torch
import torch.nn as nn
from torchvision import models, transforms
from torchvision.models import ResNet18_Weights

import config


class FeatureExtractor:
    """封装 ResNet18 特征提取。

    取 avgpool 之后、fc 之前的输出（512 维），作为通用视觉特征。
    """

    def __init__(self, device=None):
        # 自动检测设备
        if device is None:
            if config.DEVICE == "cuda" and torch.cuda.is_available():
                self.device = "cuda"
            else:
                self.device = "cpu"
        else:
            self.device = device

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

    @torch.no_grad()
    def extract(self, image):
        """从一张图像提取特征。

        参数:
            image: PIL.Image 或 numpy.ndarray(H,W,3) 或已归一化的 Tensor(C,H,W)
        返回:
            numpy.ndarray, shape (512,)，已做 L2 归一化
        """
        # 统一转成 PIL 后走 transform
        if isinstance(image, torch.Tensor):
            img = image
            if img.dim() == 4:
                img = img.squeeze(0)
            if img.dim() == 3 and img.shape[0] == 3:  # C,H,W 已处理
                pass
        else:
            from PIL import Image
            if not isinstance(image, Image.Image):
                # 假定是 numpy BGR/HWC（OpenCV 读入）
                import cv2
                image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                image = Image.fromarray(image)
            img = self.transform(image)  # C,H,W

        img = img.unsqueeze(0).to(self.device)
        feat = self.model(img)  # (1, 512)
        feat = feat.squeeze(0).cpu().numpy()

        # L2 归一化
        import numpy as np
        norm = np.linalg.norm(feat)
        if norm > 0:
            feat = feat / norm
        return feat.astype("float32")

    @torch.no_grad()
    def extract_from_file(self, image_path: str):
        """从图片文件提取特征。"""
        from PIL import Image
        image = Image.open(image_path).convert("RGB")
        return self.extract(image)

    @torch.no_grad()
    def extract_tta(self, image):
        """测试时增强（TTA）：原图 + 水平翻转，取平均特征。

        对"类内多实例、外观差异大"的场景，翻转增强能提高特征稳定性，
        减少拍摄角度/方向带来的偶然波动。
        """
        from PIL import Image
        import numpy as np
        # 转 PIL
        if isinstance(image, Image.Image):
            pil = image
        else:
            import cv2
            pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

        f_orig = self.extract(pil)
        f_flip = self.extract(pil.transpose(Image.FLIP_LEFT_RIGHT))
        # 平均后重新归一化
        feat = (f_orig + f_flip) / 2.0
        norm = np.linalg.norm(feat)
        if norm > 0:
            feat = feat / norm
        return feat.astype("float32")
