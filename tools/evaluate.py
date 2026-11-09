# -*- coding: utf-8 -*-
"""离线评测：跑一批标注好的图片，出准确率 / 未知召回 / 混淆矩阵 / 阈值扫描（B6）。

目录约定（与 batch_register.py 对称）：

    data/test/<物体名>/*.jpg     ← 已知样本，子目录名 = 真实类别
    data/unknown/*.jpg           ← 未注册物体的图片，真实类别视为「未知物体」

用法：

    # 全量评测（用当前特征库，默认阈值）
    python tools/evaluate.py

    # 指定数据/输出目录，并做 TTA
    python tools/evaluate.py --test ../data/test --unknown ../data/unknown \
        --out ../docs/eval --tta

    # 只扫阈值，不重新提特征（复用上次的 report.csv）
    python tools/evaluate.py --from-report ../docs/eval/report.csv

产物（默认写到 docs/eval/）：
    report.csv            逐张明细：文件 / 真实 / 预测 / 原始分 / 是否正确
    summary.csv           汇总指标：Top-1、Top-3、未知召回、误接收率、误拒率
    confusion.csv         混淆矩阵（行=真实，列=预测）
    threshold_sweep.csv   不同阈值下指标如何变化，用来选阈值

设计说明：
    - 纯 Python 的指标计算在 tools/metrics.py，本文件只负责「跑图 → 落盘」。
    - 重依赖（torch / PIL）延迟导入，--help 在无 torch 环境也能跑。
    - 退出码：0 正常；2 没有可用样本。
"""
import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "object_recognition")
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from metrics import (                                    # noqa: E402
    UNKNOWN_LABEL, confusion_matrix, fmt_pct, summarize, sweep)

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")
DEFAULT_THRESHOLDS = [round(0.50 + 0.05 * i, 2) for i in range(10)]  # 0.50 ~ 0.95


# ---------------------------------------------------------------- 数据收集

def collect_known(test_dir):
    """返回 [(真实类名, 图片路径), ...]，类名取自子目录名。"""
    rows = []
    if not os.path.isdir(test_dir):
        return rows
    for name in sorted(os.listdir(test_dir)):
        sub = os.path.join(test_dir, name)
        if not os.path.isdir(sub):
            continue
        for f in sorted(os.listdir(sub)):
            if f.lower().endswith(IMAGE_EXT):
                rows.append((name, os.path.join(sub, f)))
    return rows


def collect_unknown(unknown_dir):
    """返回 [(UNKNOWN_LABEL, 图片路径), ...]；目录不存在时返回空。"""
    rows = []
    if not os.path.isdir(unknown_dir):
        return rows
    for f in sorted(os.listdir(unknown_dir)):
        p = os.path.join(unknown_dir, f)
        if os.path.isfile(p) and f.lower().endswith(IMAGE_EXT):
            rows.append((UNKNOWN_LABEL, p))
    return rows


# ---------------------------------------------------------------- 跑图

def run_inference(samples, library, threshold, extract_fn, limit=0):
    """逐张提特征 + 检索，返回 records。单张失败只记录不中断。

    extract_fn 接一个图片路径返回 (512,) 特征，通常是
    extractor.extract_from_file（快）或 extractor.extract_tta（稳）。
    """
    import recognize as rec

    if limit and limit > 0:
        samples = samples[:limit]

    records, errors = [], []
    total = len(samples)
    for i, (true_name, path) in enumerate(samples, 1):
        try:
            feat = extract_fn(path)
            r = rec.recognize(feat, library, threshold=threshold)
        except Exception as e:                       # 单张坏图不该毁掉整轮评测
            errors.append((path, "%s: %s" % (type(e).__name__, e)))
            continue

        # best_name 是「只按相似度选出的最佳类」，用来做阈值扫描：
        # 真实系统里 threshold 会在 recognize() 内部把低于阈值的判成未知。
        top_k = r.get("top_k") or []
        best_name = top_k[0]["name"] if top_k else UNKNOWN_LABEL
        records.append({
            "file": os.path.basename(path),
            "path": path,
            "true": true_name,
            "pred": r["name"],
            "best_name": best_name,
            "raw_score": round(float(r["score"]), 4),
            "correct": r["name"] == true_name,
            "top_k": [t["name"] for t in top_k],
        })
        if total >= 20 and (i % 20 == 0 or i == total):
            print("  进度 %d/%d ..." % (i, total))
    return records, errors


# ---------------------------------------------------------------- 落盘

def _write_csv(path, header, rows):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    # utf-8-sig：Excel 双击打开不乱码
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def write_report(records, out_dir):
    rows = [[r["file"], r["true"], r["pred"], r["best_name"],
             "%.4f" % r["raw_score"], "1" if r["correct"] else "0",
             "/".join(r["top_k"])] for r in records]
    return _write_csv(os.path.join(out_dir, "report.csv"),
                      ["文件", "真实", "预测", "最佳类", "原始分", "是否正确", "TopK"], rows)


def summary_rows(meta):
    """把 summarize() 的结果摊平成 [[指标, 数值], ...]，附每类样本数。"""
    rows = [[k, v] for k, v in meta.items() if k != "per_class"]
    per_class = meta.get("per_class") or {}
    if per_class:
        rows.append(["每类样本数", ""])
        for name, cnt in sorted(per_class.items()):
            rows.append(["  " + name, cnt])
    return rows


def write_confusion(records, out_dir):
    labels, matrix = confusion_matrix(records)
    rows = [[labels[i]] + m for i, m in enumerate(matrix)]
    return _write_csv(os.path.join(out_dir, "confusion.csv"),
                      ["真实\\预测"] + labels, rows)


def write_sweep(records, out_dir, thresholds):
    table = sweep(records, thresholds)
    rows = [[t["threshold"], t["top1"], t["unknown_recall"],
             t["false_accept"], t["known_reject"]] for t in table]
    path = _write_csv(os.path.join(out_dir, "threshold_sweep.csv"),
                      ["阈值", "Top1", "未知召回", "误接收率", "误拒率"], rows)
    return path, table


# ---------------------------------------------------------------- 报告打印

def print_summary(meta, table=None):
    print("\n" + "=" * 52)
    print("评测样本：%d 张（已知 %d / 未知 %d）"
          % (meta["samples"], meta["known"], meta["unknown"]))
    print("-" * 52)
    print("Top-1 准确率     : %s" % fmt_pct(meta["top1"]))
    print("Top-3 命中率     : %s" % fmt_pct(meta["top3"]))
    print("未知召回率       : %s   （越高越不会把陌生物体说成已知）"
          % fmt_pct(meta["unknown_recall"]))
    print("误接收率(FAR)    : %s   （越低越好）" % fmt_pct(meta["false_accept"]))
    print("误拒率(FRR)      : %s   （已知被说成未知，阈值过高的代价）"
          % fmt_pct(meta["known_reject"]))
    print("=" * 52)

    if table:
        print("\n阈值扫描（挑阈值用）：")
        print("  阈值   Top1    未知召回  误接收   误拒")
        best = max(table, key=lambda t: (t["top1"] + t["unknown_recall"]) / 2)
        for t in table:
            mark = "  ← 综合最佳" if t is best else ""
            print("  %.2f   %s   %s   %s   %s%s"
                  % (t["threshold"], fmt_pct(t["top1"]), fmt_pct(t["unknown_recall"]),
                     fmt_pct(t["false_accept"]), fmt_pct(t["known_reject"]), mark))


# ---------------------------------------------------------------- 入口

def load_report(path):
    """从已有 report.csv 还原 records（--from-report，跳过提特征）。"""
    records = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("真实"):
                continue
            topk_raw = (row.get("TopK") or "").strip()
            records.append({
                "file": row.get("文件", ""),
                "true": row["真实"],
                "pred": row.get("预测", ""),
                "best_name": row.get("最佳类") or row.get("预测", ""),
                "raw_score": float(row.get("原始分") or 0.0),
                # TopK 列是 "甲/乙/丙" 形式，拆回列表才能算 Top-K 命中
                "top_k": [x for x in topk_raw.split("/") if x],
            })
    return records


def main(argv=None):
    ap = argparse.ArgumentParser(description="物体识别离线评测")
    ap.add_argument("--test", default=os.path.join(ROOT, "data", "test"),
                    help="已知样本根目录，子目录名=类别（默认 data/test）")
    ap.add_argument("--unknown", default=os.path.join(ROOT, "data", "unknown"),
                    help="未知样本目录（默认 data/unknown，可不存在）")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "eval"),
                    help="结果输出目录（默认 docs/eval）")
    ap.add_argument("--threshold", type=float, default=None,
                    help="判定阈值，默认取 config.get_threshold()")
    ap.add_argument("--thresholds", default="",
                    help="阈值扫描列表，逗号分隔，如 0.6,0.7,0.8；留空用 0.50~0.95")
    ap.add_argument("--tta", action="store_true", help="用 TTA 提特征（慢一倍，通常更稳）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 张，快速冒烟")
    ap.add_argument("--from-report", default="",
                    help="复用已有 report.csv，只重算指标与阈值扫描")
    args = ap.parse_args(argv)

    if args.thresholds.strip():
        thresholds = [float(x) for x in args.thresholds.split(",") if x.strip()]
    else:
        thresholds = list(DEFAULT_THRESHOLDS)

    # ---- 路径 A：复用旧报告 ----
    if args.from_report:
        if not os.path.isfile(args.from_report):
            print("找不到报告文件：%s" % args.from_report)
            return 2
        records = load_report(args.from_report)
        meta = summarize(records)
        p_sum = _write_csv(os.path.join(args.out, "summary.csv"),
                          ["指标", "数值"], summary_rows(meta))
        p_conf = write_confusion(records, args.out)
        p_sweep, table = write_sweep(records, args.out, thresholds)
        print_summary(meta, table)
        print("\n已输出：")
        for p in (p_sum, p_conf, p_sweep):
            print("  %s" % p)
        return 0

    # ---- 路径 B：真正跑一遍 ----
    samples = collect_known(args.test) + collect_unknown(args.unknown)
    if not samples:
        print("没有找到评测图片。")
        print("  已知样本：%s/<物体名>/*.jpg" % args.test)
        print("  未知样本：%s/*.jpg（可选）" % args.unknown)
        return 2

    import config
    from feature import FeatureExtractor
    from library import FeatureLibrary

    library = FeatureLibrary()
    lib_stats = library.stats()
    if not lib_stats["objects"]:
        print("特征库是空的，先运行 tools/batch_register.py 注册物体。")
        return 2

    threshold = args.threshold
    if threshold is None:
        getter = getattr(config, "get_threshold", None)
        threshold = getter() if callable(getter) else config.THRESHOLD

    extractor = FeatureExtractor()
    print("特征库：%d 个物体 / %d 张特征"
          % (lib_stats["objects"], lib_stats["images"]))
    print("设备：%s；阈值：%.2f；TTA：%s；样本：%d 张"
          % (extractor.device, threshold, "开" if args.tta else "关", len(samples)))

    extract_fn = extractor.extract_tta if args.tta else extractor.extract_from_file
    records, errors = run_inference(samples, library, threshold, extract_fn, args.limit)
    if not records:
        print("全部样本都读取失败，无法评测。")
        for p, err in errors[:5]:
            print("  - %s → %s" % (os.path.basename(p), err))
        return 2

    meta = summarize(records)
    meta["threshold"] = round(float(threshold), 3)

    p_report = write_report(records, args.out)
    p_conf = write_confusion(records, args.out)
    p_sum = _write_csv(os.path.join(args.out, "summary.csv"),
                       ["指标", "数值"], summary_rows(meta))
    p_sweep, table = write_sweep(records, args.out, thresholds)
    print_summary(meta, table)

    print("\n已输出：")
    for p in (p_report, p_sum, p_conf, p_sweep):
        print("  %s" % p)
    if errors:
        print("\n跳过 %d 张（无法读取）：" % len(errors))
        for p, err in errors[:10]:
            print("  - %s → %s" % (os.path.basename(p), err))
    return 0


if __name__ == "__main__":
    sys.exit(main())
