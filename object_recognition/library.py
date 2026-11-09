# -*- coding: utf-8 -*-
"""特征库管理：物体名称 + 特征的增删查与持久化。

存储方案（v2）：
  - library.json          元数据（id / name / created_at / thumb）
  - library_feats.npy     特征矩阵 (N, 512)，顺序与 items 一一对应
  - thumbs/<安全名>/*.jpg  每张录入图的原图副本（可选，供界面显示缩略图）

健壮性设计（对应缺陷 P0-3）：
  - 加载时校验 items 与 feats 的**数量**和**维度**，不一致按较短者截断，
    并把中文告警放进 self.load_warning 交给界面显示，绝不带着脏数据跑检索
  - 写盘原子化：先写 .tmp 再 os.replace，崩溃不会留下「元数据 10 条、特征 9 条」
  - 可选单实例文件锁：GUI 传 acquire_lock=True，第二个实例会拿到 (False, 提示)

跨设备迁移：
  - export_zip() 导出单个自包含备份（清单 + 两个数据文件 + 缩略图）
  - import_zip() 支持 replace / merge，导入前完整性校验，失败不破坏现有库
"""
import json
import os
import re
import shutil
import time
import uuid
import zipfile

import numpy as np

import config

BACKUP_VERSION = 2
THUMB_MAX_SIDE = 320
THUMB_QUALITY = 85


def safe_dir_name(name):
    """把物体名转成合法目录名（去掉 Windows 非法字符）。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name or "").strip()
    return cleaned or "object"


class FeatureLibrary:
    def __init__(self, library_path=None, feats_path=None, acquire_lock=False):
        self.library_path = library_path or config.LIBRARY_PATH
        self.feats_path = feats_path or config.FEATS_PATH
        base_dir = os.path.dirname(os.path.abspath(self.library_path)) or config.DATA_DIR
        self.thumb_dir = os.path.join(base_dir, "thumbs")
        self.lock_path = os.path.join(base_dir, ".library.lock")

        self.items = []           # list[dict]: id/name/created_at/thumb
        self.feats = None         # ndarray (N, 512)
        self.load_warning = None  # 加载期发现的问题（中文），供界面提示
        self._lock_fh = None
        self.lock_ok, self.lock_message = True, ""

        self.load()
        if acquire_lock:
            # 调用方（GUI）自己决定拿到 False 时怎么处理：弹窗提示并退出即可
            self.lock_ok, self.lock_message = self.acquire_lock()

    # ---------- 单实例锁 ----------
    def acquire_lock(self):
        """尝试占住特征库锁。返回 (ok, 中文说明)。可重复调用。"""
        if self._lock_fh is not None:
            return True, ""
        try:
            os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
            fh = open(self.lock_path, "a+b")
            try:
                if os.path.getsize(self.lock_path) == 0:
                    fh.write(b"0")
                    fh.flush()
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.close()
                return False, "特征库已被另一个窗口占用，请先关闭已运行的程序"
            self._lock_fh = fh
            return True, ""
        except Exception as e:
            return False, "无法创建锁文件：%s: %s" % (type(e).__name__, e)

    def release_lock(self):
        """释放锁（程序退出时调用）。即使没拿到锁也不报错。"""
        fh, self._lock_fh = self._lock_fh, None
        if fh is None:
            return
        try:
            fh.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass

    # ---------- 持久化 ----------
    def load(self):
        """从磁盘读取并校验。任何异常都降级为空库，同时写 self.load_warning。"""
        self.items = []
        self.feats = None
        self.load_warning = None

        if os.path.exists(self.library_path):
            try:
                with open(self.library_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                items = data.get("items", [])
                if isinstance(items, list):
                    self.items = [it for it in items
                                  if isinstance(it, dict) and it.get("name")]
                else:
                    self._warn("特征库元数据格式异常，已忽略元数据")
            except Exception as e:
                self._warn("特征库元数据无法解析（%s），已按空库启动" % type(e).__name__)

        if os.path.exists(self.feats_path):
            try:
                feats = np.load(self.feats_path)
                if feats.ndim == 1:
                    feats = feats.reshape(-1, config.FEATURE_DIM)
                self.feats = np.asarray(feats, dtype="float32")
            except Exception as e:
                self.feats = None
                self.items = []
                self._warn("特征文件无法读取（%s），已按空库启动" % type(e).__name__)

        self._repair()

    def _warn(self, msg):
        self.load_warning = (self.load_warning + "；" + msg) if self.load_warning else msg

    def _repair(self):
        """校验并修正 items / feats 的一致性（对应缺陷 P0-3）。"""
        if self.feats is None or len(self.feats) == 0:
            if self.items:
                self._warn("元数据有 %d 条但特征文件为空，已清空元数据" % len(self.items))
                self.items = []
            self.feats = None
            return

        if self.feats.shape[1] != config.FEATURE_DIM:
            self._warn("特征维度不符（期望 %d，实际 %d），已按空库启动"
                       % (config.FEATURE_DIM, self.feats.shape[1]))
            self.items, self.feats = [], None
            return

        n_item, n_feat = len(self.items), int(self.feats.shape[0])
        if n_item != n_feat:
            k = min(n_item, n_feat)
            self._warn("特征库不一致（元数据 %d 条 / 特征 %d 条），已按前 %d 条对齐"
                       % (n_item, n_feat, k))
            self.items = self.items[:k]
            self.feats = self.feats[:k]

    @staticmethod
    def _atomic_write_json(path, obj):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _save(self):
        """原子写：元数据与特征各自 tmp + replace，不会出现半截文件。"""
        self._atomic_write_json(
            self.library_path, {"version": BACKUP_VERSION, "items": self.items})

        if self.feats is not None and len(self.feats) > 0:
            tmp = self.feats_path + ".tmp.npy"
            np.save(tmp, self.feats)
            os.replace(tmp, self.feats_path)
        else:
            # 库空了就删掉特征文件，避免下次启动读到旧矩阵
            try:
                if os.path.exists(self.feats_path):
                    os.remove(self.feats_path)
            except OSError:
                pass

    # ---------- 查询 ----------
    def list_objects(self):
        names = []
        for it in self.items:
            if it["name"] not in names:
                names.append(it["name"])
        return names

    def object_stats(self):
        stats = {}
        for it in self.items:
            stats[it["name"]] = stats.get(it["name"], 0) + 1
        return stats

    def get_feats(self):
        return self.feats

    def is_empty(self):
        return self.feats is None or len(self.feats) == 0

    def validate(self):
        """只校验不修改，返回 (ok, 中文说明)。"""
        if self.feats is None or len(self.feats) == 0:
            if self.items:
                return False, "有 %d 条元数据但没有特征" % len(self.items)
            return True, "空库"
        if self.feats.ndim != 2 or self.feats.shape[1] != config.FEATURE_DIM:
            return False, "特征维度异常：%s" % (self.feats.shape,)
        if len(self.items) != self.feats.shape[0]:
            return False, "数量不一致：元数据 %d / 特征 %d" % (
                len(self.items), self.feats.shape[0])
        missing = 0
        for it in self.items:
            fn = it.get("thumb")
            if fn and not os.path.exists(
                    os.path.join(self.thumb_dir, safe_dir_name(it["name"]), fn)):
                missing += 1
        if missing:
            return False, "有 %d 张缩略图文件缺失（不影响识别）" % missing
        return True, "正常"

    def stats(self):
        size = 0
        for p in (self.library_path, self.feats_path):
            try:
                if os.path.exists(p):
                    size += os.path.getsize(p)
            except OSError:
                pass
        dim = 0
        if self.feats is not None and self.feats.ndim == 2:
            dim = int(self.feats.shape[1])
        return {
            "objects": len(self.object_stats()),
            "images": len(self.items),
            "dim": dim,
            "bytes": size,
            "path": self.library_path,
        }

    def thumb_paths(self, name):
        """该物体的缩略图绝对路径列表（文件不存在的不返回）。"""
        out_dir = os.path.join(self.thumb_dir, safe_dir_name(name))
        paths = []
        for it in self.items:
            if it["name"] != name:
                continue
            fn = it.get("thumb")
            if fn:
                p = os.path.join(out_dir, fn)
                if os.path.exists(p):
                    paths.append(p)
        return paths

    # ---------- 写入 ----------
    def add(self, name, feats, image_paths=None, images=None):
        """录入一个物体的若干张图片特征。

        参数:
            name:        物体名称（同名会追加）
            feats:       list[np.ndarray] 或 ndarray(N,512)
            image_paths: 可选，原图路径列表；给了就存缩略图副本（供界面显示）
            images:      可选，PIL.Image 或 BGR ndarray 列表（摄像头抓拍用）
        返回:
            本次写入的特征条数
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("物体名称不能为空")

        feats = np.asarray(feats, dtype="float32")
        if feats.ndim == 1:
            feats = feats.reshape(1, -1)
        if feats.ndim != 2 or feats.shape[1] != config.FEATURE_DIM:
            raise ValueError("特征维度不符：期望 %d 维，实际 %s"
                             % (config.FEATURE_DIM, feats.shape))

        thumbs = self._store_thumbs(name, feats.shape[0], image_paths, images)

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for i in range(feats.shape[0]):
            item = {"id": uuid.uuid4().hex, "name": name, "created_at": now}
            if i < len(thumbs) and thumbs[i]:
                item["thumb"] = thumbs[i]
            self.items.append(item)

        self.feats = feats if self.feats is None else np.concatenate(
            [self.feats, feats], axis=0)
        self._save()
        return int(feats.shape[0])

    def delete(self, name):
        """删除某个物体的所有特征（含缩略图）。返回删除的图片条数。"""
        keep_idx = [i for i, it in enumerate(self.items) if it["name"] != name]
        removed = len(self.items) - len(keep_idx)
        if removed == 0:
            return 0
        self.items = [self.items[i] for i in keep_idx]
        if self.feats is not None:
            self.feats = self.feats[keep_idx] if keep_idx else None
        self._remove_thumb_dir(name)
        self._save()
        return removed

    def delete_one(self, name, index):
        """删除某物体的第 index 张特征（0 基，按该物体的录入顺序）。"""
        idxs = [i for i, it in enumerate(self.items) if it["name"] == name]
        if index < 0 or index >= len(idxs):
            return False
        gidx = idxs[index]
        removed_item = self.items.pop(gidx)
        if self.feats is not None and len(self.feats) > gidx:
            self.feats = np.delete(self.feats, gidx, axis=0)
            if len(self.feats) == 0:
                self.feats = None
        fn = removed_item.get("thumb")
        if fn:
            try:
                os.remove(os.path.join(self.thumb_dir, safe_dir_name(name), fn))
            except OSError:
                pass
        self._save()
        return True

    def rename(self, old_name, new_name):
        """重命名物体（含缩略图目录）。返回受影响的特征条数。"""
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("新名称不能为空")
        if old_name == new_name:
            return 0
        hit = [it for it in self.items if it["name"] == old_name]
        if not hit:
            return 0
        for it in hit:
            it["name"] = new_name
        src = os.path.join(self.thumb_dir, safe_dir_name(old_name))
        dst = os.path.join(self.thumb_dir, safe_dir_name(new_name))
        try:
            if os.path.isdir(src) and not os.path.isdir(dst):
                os.rename(src, dst)
            elif os.path.isdir(src):
                # 目标已存在（同名物体已有缩略图）→ 合并
                os.makedirs(dst, exist_ok=True)
                for fn in os.listdir(src):
                    try:
                        os.replace(os.path.join(src, fn), os.path.join(dst, fn))
                    except OSError:
                        pass
                os.rmdir(src)
        except OSError:
            pass
        self._save()
        return len(hit)

    # ---------- 缩略图 ----------
    def _remove_thumb_dir(self, name):
        d = os.path.join(self.thumb_dir, safe_dir_name(name))
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)

    def _store_thumbs(self, name, count, image_paths=None, images=None):
        """把原图缩到 320px 存副本，返回与特征顺序对应的文件名列表（失败为 None）。"""
        sources = []
        if image_paths:
            sources.extend(list(image_paths)[:count])
        if images:
            need = max(0, count - len(sources))
            sources.extend(list(images)[:need])
        if not sources:
            return []

        out_dir = os.path.join(self.thumb_dir, safe_dir_name(name))
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError:
            return []

        names = []
        for src in sources:
            try:
                img = self._to_pil(src)
                if img is None:
                    names.append(None)
                    continue
                img = img.convert("RGB")
                img.thumbnail((THUMB_MAX_SIDE, THUMB_MAX_SIDE))
                fn = uuid.uuid4().hex + ".jpg"
                img.save(os.path.join(out_dir, fn), "JPEG", quality=THUMB_QUALITY)
                names.append(fn)
            except Exception:
                names.append(None)
        return names

    @staticmethod
    def _to_pil(src):
        """路径 / PIL.Image / BGR ndarray → PIL.Image（自动修正 EXIF 方向）。"""
        from PIL import Image, ImageOps
        if isinstance(src, Image.Image):
            img = src
        elif isinstance(src, str):
            img = Image.open(src)
        else:
            arr = np.asarray(src)
            if arr.ndim == 2:
                img = Image.fromarray(arr.astype("uint8"))
            elif arr.ndim == 3 and arr.shape[2] == 3:
                img = Image.fromarray(arr[:, :, ::-1].astype("uint8"))  # BGR→RGB
            elif arr.ndim == 3 and arr.shape[2] == 4:
                img = Image.fromarray(arr[:, :, [2, 1, 0, 3]].astype("uint8"))
            else:
                return None
        try:
            return ImageOps.exif_transpose(img)
        except Exception:
            return img

    # ---------- 跨设备备份 / 恢复 ----------
    def export_zip(self, zip_path):
        """把整个特征库（含缩略图）打包成单个 zip。返回 (ok, message)。"""
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
                if os.path.isdir(self.thumb_dir):
                    base = os.path.dirname(self.thumb_dir)
                    for root, _dirs, files in os.walk(self.thumb_dir):
                        for fn in files:
                            full = os.path.join(root, fn)
                            rel = os.path.relpath(full, base)
                            z.write(full, rel.replace(os.sep, "/"))
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
        """导入备份（replace 覆盖 / merge 合并）。校验不通过不破坏现有库。"""
        _manifest, err = self.inspect_zip(zip_path)
        if err:
            return False, err

        import tempfile
        tmp_dir = tempfile.mkdtemp(prefix="lib_import_")
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                for member in z.namelist():
                    if member.startswith("thumbs/") or member in (
                            "library.json", "library_feats.npy"):
                        z.extract(member, tmp_dir)

            with open(os.path.join(tmp_dir, "library.json"), "r", encoding="utf-8") as f:
                items = json.load(f).get("items", [])
            feats = np.load(os.path.join(tmp_dir, "library_feats.npy"))
            if feats.ndim == 1:
                feats = feats.reshape(-1, config.FEATURE_DIM)

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
            self._restore_thumbs(tmp_dir)
            self.load()
            return True, "已%s %d 个物体 / %d 张图片特征" % (
                "合并" if mode == "merge" else "恢复",
                len(self.object_stats()), len(self.items))
        except Exception as e:
            return False, "导入失败（现有特征库未被改动）: %s" % e
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _restore_thumbs(self, src_root):
        """把备份里的 thumbs/ 合并回当前缩略图目录。"""
        src = os.path.join(src_root, "thumbs")
        if not os.path.isdir(src):
            return
        try:
            shutil.copytree(src, self.thumb_dir, dirs_exist_ok=True)
        except Exception:
            pass
