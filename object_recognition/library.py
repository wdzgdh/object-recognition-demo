# -*- coding: utf-8 -*-
"""特征库管理：物体名称 + 特征的增删查与持久化。

存储方案：
  - library.json  元数据（id / name / created_at / 图片数）
  - library_feats.npy  (N, 512) 特征矩阵，顺序与 items 一一对应

跨设备迁移：
  - export_zip() 导出单个自包含备份文件（含清单 + 两个数据文件）
  - import_zip() 导入备份，支持 replace / merge 两种模式，导入前做完整性校验，
    校验失败不会破坏现有特征库。
"""
import json
import os
import time
import uuid
import zipfile

import numpy as np

import config

BACKUP_VERSION = 1


class FeatureLibrary:
    def __init__(self, library_path=None, feats_path=None):
        self.library_path = library_path or config.LIBRARY_PATH
        self.feats_path = feats_path or config.FEATS_PATH
        self.items = []          # list[dict]: id/name/created_at/num_images
        self.feats = None        # ndarray (N, 512)
        self.load()

    # ---------- 持久化 ----------
    def load(self):
        self.items = []
        self.feats = None
        if os.path.exists(self.library_path):
            with open(self.library_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.items = data.get("items", [])
        if os.path.exists(self.feats_path):
            self.feats = np.load(self.feats_path)
            if self.feats.ndim == 1:
                self.feats = self.feats.reshape(-1, config.FEATURE_DIM)

    def _save(self):
        os.makedirs(os.path.dirname(self.library_path), exist_ok=True)
        with open(self.library_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "items": self.items}, f, ensure_ascii=False, indent=2)
        if self.feats is not None and len(self.feats) > 0:
            np.save(self.feats_path, self.feats)

    # ---------- 查询 ----------
    def list_objects(self):
        """返回已录入物体名称列表（去重，按录入顺序）。"""
        names = []
        for it in self.items:
            if it["name"] not in names:
                names.append(it["name"])
        return names

    def object_stats(self):
        """返回 {名称: 图片数} 统计。"""
        stats = {}
        for it in self.items:
            stats[it["name"]] = stats.get(it["name"], 0) + 1
        return stats

    def get_feats(self):
        return self.feats

    def is_empty(self):
        return self.feats is None or len(self.feats) == 0

    # ---------- 写入 ----------
    def add(self, name: str, feats):
        """录入一个物体的若干张图片特征。

        参数:
            name: 物体名称
            feats: list[np.ndarray] 或 ndarray(N,512)，多张图的特征
        """
        feats = np.asarray(feats, dtype="float32")
        if feats.ndim == 1:
            feats = feats.reshape(1, -1)
        for feat in feats:
            item = {
                "id": uuid.uuid4().hex,
                "name": name,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.items.append(item)
        if self.feats is None:
            self.feats = feats
        else:
            self.feats = np.concatenate([self.feats, feats], axis=0)
        self._save()

    def delete(self, name: str):
        """删除某个物体的所有特征。返回删除的图片条数。"""
        keep_idx = [i for i, it in enumerate(self.items) if it["name"] != name]
        removed = len(self.items) - len(keep_idx)
        if removed == 0:
            return 0
        self.items = [self.items[i] for i in keep_idx]
        if self.feats is not None:
            self.feats = self.feats[keep_idx]
            if len(self.feats) == 0:
                self.feats = None
        self._save()
        return removed

    # ---------- 跨设备备份 / 恢复 ----------
    def export_zip(self, zip_path):
        """把整个特征库打包成单个 zip（自包含，可直接拷到另一台设备）。

        返回 (ok, message)。
        """
        if self.is_empty():
            return False, "特征库为空，没有可导出的内容"
        try:
            os.makedirs(os.path.dirname(os.path.abspath(zip_path)), exist_ok=True)
            manifest = {
                "backup_version": BACKUP_VERSION,
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "feature_dim": config.FEATURE_DIM,
                "item_count": len(self.items),
                "objects": self.object_stats(),
            }
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("manifest.json",
                           json.dumps(manifest, ensure_ascii=False, indent=2))
                z.write(self.library_path, "library.json")
                z.write(self.feats_path, "library_feats.npy")
            return True, "已导出 %d 个物体 / %d 张图片特征" % (
                len(self.object_stats()), len(self.items))
        except Exception as e:
            return False, "导出失败: %s" % e

    @staticmethod
    def inspect_zip(zip_path):
        """读取备份清单（不修改现有库）。返回 (manifest_or_None, error)。"""
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                names = z.namelist()
                if "library.json" not in names or "library_feats.npy" not in names:
                    return None, "备份文件缺少数据文件，不是有效的特征库备份"
                manifest = json.loads(z.read("manifest.json").decode("utf-8")) \
                    if "manifest.json" in names else {}
            return manifest, None
        except Exception as e:
            return None, "无法读取备份文件: %s" % e

    def import_zip(self, zip_path, mode="replace"):
        """导入备份。

        mode="replace" 覆盖当前库（换设备恢复用）
        mode="merge"   追加到当前库（合并两台设备的数据）

        导入前先在临时目录校验完整性，任何异常都不会破坏现有特征库。
        返回 (ok, message)。
        """
        manifest, err = self.inspect_zip(zip_path)
        if err:
            return False, err

        import tempfile
        tmp_dir = tempfile.mkdtemp(prefix="lib_import_")
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extract("library.json", tmp_dir)
                z.extract("library_feats.npy", tmp_dir)
            json_path = os.path.join(tmp_dir, "library.json")
            npy_path = os.path.join(tmp_dir, "library_feats.npy")

            with open(json_path, "r", encoding="utf-8") as f:
                items = json.load(f).get("items", [])
            feats = np.load(npy_path)
            if feats.ndim == 1:
                feats = feats.reshape(-1, config.FEATURE_DIM)

            # 完整性校验
            if len(items) == 0:
                return False, "备份中没有特征数据"
            if feats.shape[0] != len(items):
                return False, "备份数据不一致：元信息 %d 条，特征 %d 条" % (
                    len(items), feats.shape[0])
            if feats.shape[1] != config.FEATURE_DIM:
                return False, "特征维度不符：期望 %d，实际 %d" % (
                    config.FEATURE_DIM, feats.shape[1])

            if mode == "merge":
                self.items = self.items + items
                self.feats = (feats if self.feats is None
                              else np.concatenate([self.feats, feats], axis=0))
            else:
                self.items = items
                self.feats = feats

            self._save()
            self.load()  # 从磁盘重读，确保内存与文件一致
            return True, "已%s %d 个物体 / %d 张图片特征" % (
                "合并" if mode == "merge" else "恢复",
                len(self.object_stats()), len(self.items))
        except Exception as e:
            return False, "导入失败（现有特征库未被改动）: %s" % e
        finally:
            try:
                for fn in ("library.json", "library_feats.npy"):
                    p = os.path.join(tmp_dir, fn)
                    if os.path.exists(p):
                        os.remove(p)
                os.rmdir(tmp_dir)
            except Exception:
                pass
