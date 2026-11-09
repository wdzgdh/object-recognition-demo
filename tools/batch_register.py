# -*- coding: utf-8 -*-
"""批量注册：把 data/register/<类名>/*.jpg 一次性录入特征库（B6）。

用法（在 object_recognition 目录下，或指定 --pkg）：

    python tools/batch_register.py --dir ../data/register
    python tools/batch_register.py --dir ../data/register --tta --reset
    python tools/batch_register.py --dir ../data/register --dry-run

目录约定：
    data/register/水杯/1.jpg ...
    data/register/马/2.jpg ...
子目录名即物体名。会把原图路径一起传给 library.add()，因此缩略图也会生成。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "object_recognition")
if PKG not in sys.path:
    sys.path.insert(0, PKG)

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")


def collect(register_dir):
    """返回 [(类名, [图片路径...]), ...]，按目录名排序。"""
    groups = []
    if not os.path.isdir(register_dir):
        return groups
    for name in sorted(os.listdir(register_dir)):
        sub = os.path.join(register_dir, name)
        if not os.path.isdir(sub):
            continue
        files = sorted(
            os.path.join(sub, f) for f in os.listdir(sub)
            if f.lower().endswith(IMAGE_EXT))
        if files:
            groups.append((name, files))
    return groups


def main(argv=None):
    ap = argparse.ArgumentParser(description="批量注册物体到特征库")
    ap.add_argument("--dir", default=os.path.join(os.path.dirname(ROOT), "data", "register"),
                    help="注册图根目录，子目录名=物体名")
    ap.add_argument("--tta", action="store_true", help="用 TTA（原图+翻转）提特征，更稳但慢一倍")
    ap.add_argument("--reset", action="store_true", help="先清空特征库再注册")
    ap.add_argument("--dry-run", action="store_true", help="只列出将要注册的内容")
    args = ap.parse_args(argv)

    groups = collect(args.dir)
    if not groups:
        print("没有找到可注册的图片。目录：%s" % args.dir)
        print("目录结构应为：%s/<物体名>/*.jpg" % args.dir)
        return 2

    total = sum(len(f) for _n, f in groups)
    print("发现 %d 个物体 / %d 张图片" % (len(groups), total))
    for name, files in groups:
        flag = "  (仅 1 张，建议 ≥5 张)" if len(files) == 1 else ""
        print("  - %s：%d 张%s" % (name, len(files), flag))
    if args.dry_run:
        return 0

    # 重依赖延迟导入，让 --dry-run 与 --help 在无 torch 环境也能跑
    import config
    from feature import FeatureExtractor
    from library import FeatureLibrary

    lib = FeatureLibrary()
    if args.reset:
        for name in list(lib.object_stats()):
            lib.delete(name)
        print("已清空原有特征库")

    extractor = FeatureExtractor()
    print("设备：%s；ROI 比例：%.2f；TTA：%s"
          % (extractor.device, extractor.roi_ratio, "开" if args.tta else "关"))

    ok_objects, ok_images, failed = 0, 0, []
    for name, files in groups:
        feats, used_paths = [], []
        for p in files:
            try:
                f = extractor.extract_tta(p) if args.tta else extractor.extract_from_file(p)
                feats.append(f)
                used_paths.append(p)
            except Exception as e:
                failed.append((p, "%s: %s" % (type(e).__name__, e)))
        if not feats:
            continue
        n = lib.add(name, feats, image_paths=used_paths)
        ok_objects += 1
        ok_images += n
        print("  已录入 %s：%d 张" % (name, n))

    stats = lib.stats()
    print("\n完成：%d 个物体 / %d 张特征（库内共 %d 个物体 / %d 张）"
          % (ok_objects, ok_images, stats["objects"], stats["images"]))
    if failed:
        print("跳过 %d 张（无法读取）：" % len(failed))
        for p, err in failed[:10]:
            print("  - %s → %s" % (os.path.basename(p), err))
    if stats["objects"] < 5:
        print("提醒：课程要求至少 5 个物体，当前只有 %d 个。" % stats["objects"])
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
