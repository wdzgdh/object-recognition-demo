# -*- coding: utf-8 -*-
"""评测指标（B6）：纯 Python，不依赖 numpy / torch，方便单元测试与 CI。

约定：
    - 已知类别样本：true 为注册时的物体名
    - 未知样本：true 为 UNKNOWN_LABEL
    - pred 为识别输出（已知类名 或 "未知物体"）
"""
from collections import Counter

UNKNOWN_LABEL = "未知物体"


def top1_accuracy(records):
    """已知样本的 Top-1 正确率（未知样本不计入分母）。"""
    known = [r for r in records if r["true"] != UNKNOWN_LABEL]
    if not known:
        return 0.0
    hit = sum(1 for r in known if r["pred"] == r["true"])
    return hit / len(known)


def topk_accuracy(records, k=3):
    """已知样本的 Top-K 命中率。records 需带 top_k 列表（元素为 dict 或 str）。"""
    known = [r for r in records if r["true"] != UNKNOWN_LABEL]
    if not known:
        return 0.0
    hit = 0
    for r in known:
        names = []
        for item in (r.get("top_k") or [])[:k]:
            names.append(item["name"] if isinstance(item, dict) else item)
        if r["true"] in names:
            hit += 1
    return hit / len(known)


def unknown_recall(records):
    """未知样本被正确判为"未知物体"的比例。"""
    unknown = [r for r in records if r["true"] == UNKNOWN_LABEL]
    if not unknown:
        return 0.0
    hit = sum(1 for r in unknown if r["pred"] == UNKNOWN_LABEL)
    return hit / len(unknown)


def false_accept_rate(records):
    """未知样本被误判成某个已知物体的比例（= 1 - 未知召回）。"""
    unknown = [r for r in records if r["true"] == UNKNOWN_LABEL]
    if not unknown:
        return 0.0
    bad = sum(1 for r in unknown if r["pred"] != UNKNOWN_LABEL)
    return bad / len(unknown)


def known_reject_rate(records):
    """已知样本被误判为未知的比例（阈值过高的代价）。"""
    known = [r for r in records if r["true"] != UNKNOWN_LABEL]
    if not known:
        return 0.0
    rej = sum(1 for r in known if r["pred"] == UNKNOWN_LABEL)
    return rej / len(known)


def confusion_matrix(records, labels=None):
    """返回 (labels, matrix)。labels 为空时按出现顺序自动收集。

    matrix[真实][预测] = 次数；真实/预测都包含 UNKNOWN_LABEL。
    """
    if labels is None:
        labels = []
        for r in records:
            for v in (r["true"], r["pred"]):
                if v not in labels:
                    labels.append(v)
        if UNKNOWN_LABEL not in labels:
            labels.append(UNKNOWN_LABEL)

    index = {name: i for i, name in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for r in records:
        i = index.get(r["true"])
        j = index.get(r["pred"])
        if i is None or j is None:
            continue
        matrix[i][j] += 1
    return labels, matrix


def summarize(records):
    """一次性算出全部指标，供命令行打印与写 CSV。"""
    known_total = sum(1 for r in records if r["true"] != UNKNOWN_LABEL)
    unknown_total = sum(1 for r in records if r["true"] == UNKNOWN_LABEL)
    return {
        "samples": len(records),
        "known": known_total,
        "unknown": unknown_total,
        "top1": round(top1_accuracy(records), 4),
        "top3": round(topk_accuracy(records, 3), 4),
        "unknown_recall": round(unknown_recall(records), 4),
        "false_accept": round(false_accept_rate(records), 4),
        "known_reject": round(known_reject_rate(records), 4),
        "per_class": dict(Counter(
            r["true"] for r in records if r["true"] != UNKNOWN_LABEL)),
    }


def sweep(records, thresholds):
    """按不同阈值重算指标（records 需带 raw_score / is_unknown 所需的原始分）。

    参数:
        records: [{"true":..., "best_name":..., "raw_score":...}]
        thresholds: 阈值序列
    返回:
        [{threshold, top1, unknown_recall, false_accept, known_reject}, ...]
    """
    out = []
    for t in thresholds:
        preds = []
        for r in records:
            is_known = r.get("raw_score", 0.0) >= t
            pred = r.get("best_name", UNKNOWN_LABEL) if is_known else UNKNOWN_LABEL
            preds.append({"true": r["true"], "pred": pred})
        s = {
            "threshold": round(float(t), 3),
            "top1": round(top1_accuracy(preds), 4),
            "unknown_recall": round(unknown_recall(preds), 4),
            "false_accept": round(false_accept_rate(preds), 4),
            "known_reject": round(known_reject_rate(preds), 4),
        }
        out.append(s)
    return out


def fmt_pct(v):
    return "%.1f%%" % (v * 100)
