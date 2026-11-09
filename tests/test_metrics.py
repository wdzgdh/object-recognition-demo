# -*- coding: utf-8 -*-
"""评测指标测试（B6）：纯 Python，不需要 torch / numpy。"""
import pytest

from metrics import (
    UNKNOWN_LABEL, confusion_matrix, false_accept_rate, fmt_pct,
    known_reject_rate, summarize, sweep, top1_accuracy, topk_accuracy,
    unknown_recall)


def rec(true, pred, best=None, score=0.9, top_k=None):
    """构造一条评测记录。"""
    d = {"true": true, "pred": pred,
         "best_name": best if best is not None else pred,
         "raw_score": score}
    if top_k is not None:
        d["top_k"] = top_k
    return d


# ---------------------------------------------------------------- Top-1

def test_top1_ignores_unknown_samples():
    records = [rec("水杯", "水杯"), rec("马", "马"), rec("猫", "狗"),
               rec(UNKNOWN_LABEL, "水杯"), rec(UNKNOWN_LABEL, UNKNOWN_LABEL)]
    # 已知 3 条里对 2 条；未知样本不进分母
    assert top1_accuracy(records) == pytest.approx(2 / 3)


def test_top1_empty_is_zero():
    assert top1_accuracy([]) == 0.0
    assert top1_accuracy([rec(UNKNOWN_LABEL, UNKNOWN_LABEL)]) == 0.0


def test_top1_counts_rejected_known_as_wrong():
    """已知样本被判"未知"也算没认出来，不能悄悄从分母里消失。"""
    records = [rec("水杯", UNKNOWN_LABEL), rec("马", "马")]
    assert top1_accuracy(records) == pytest.approx(0.5)


# ---------------------------------------------------------------- Top-K

def test_topk_accepts_str_and_dict_items():
    records = [
        rec("水杯", UNKNOWN_LABEL, top_k=["马", "水杯", "猫"]),
        rec("马", UNKNOWN_LABEL, top_k=[{"name": "水杯"}, {"name": "马"}]),
        rec("猫", UNKNOWN_LABEL, top_k=["狗"]),
    ]
    assert topk_accuracy(records, k=3) == pytest.approx(2 / 3)


def test_topk_k_limits_lookahead():
    records = [rec("丙", UNKNOWN_LABEL, top_k=["甲", "乙", "丙"])]
    assert topk_accuracy(records, k=2) == 0.0
    assert topk_accuracy(records, k=3) == 1.0


def test_topk_skips_unknown_samples():
    records = [rec("甲", "甲", top_k=["甲"]), rec(UNKNOWN_LABEL, "乙", top_k=["乙"])]
    assert topk_accuracy(records, k=3) == 1.0


# ---------------------------------------------------------------- 未知 / 误接收 / 误拒

def test_unknown_recall_and_false_accept_are_complementary():
    records = [rec(UNKNOWN_LABEL, UNKNOWN_LABEL), rec(UNKNOWN_LABEL, "水杯"),
               rec(UNKNOWN_LABEL, "马"), rec(UNKNOWN_LABEL, UNKNOWN_LABEL)]
    assert unknown_recall(records) == pytest.approx(0.5)
    assert false_accept_rate(records) == pytest.approx(0.5)
    assert unknown_recall(records) + false_accept_rate(records) == pytest.approx(1.0)


def test_unknown_metrics_empty_is_zero():
    only_known = [rec("水杯", "水杯")]
    assert unknown_recall(only_known) == 0.0
    assert false_accept_rate(only_known) == 0.0


def test_known_reject_rate():
    records = [rec("水杯", UNKNOWN_LABEL), rec("马", "马"), rec("猫", "猫"),
               rec(UNKNOWN_LABEL, UNKNOWN_LABEL)]
    assert known_reject_rate(records) == pytest.approx(1 / 3)


# ---------------------------------------------------------------- 混淆矩阵

def test_confusion_matrix_counts_and_labels():
    records = [rec("水杯", "水杯"), rec("水杯", "马"), rec("马", "马"),
               rec(UNKNOWN_LABEL, "马")]
    labels, matrix = confusion_matrix(records)
    idx = {n: i for i, n in enumerate(labels)}
    assert matrix[idx["水杯"]][idx["水杯"]] == 1
    assert matrix[idx["水杯"]][idx["马"]] == 1
    assert matrix[idx["马"]][idx["马"]] == 1
    assert matrix[idx[UNKNOWN_LABEL]][idx["马"]] == 1
    # 每一行的和 = 该真实类的样本数
    assert sum(sum(row) for row in matrix) == len(records)


def test_confusion_matrix_always_has_unknown_row():
    labels, _m = confusion_matrix([rec("水杯", "水杯")])
    assert UNKNOWN_LABEL in labels


def test_confusion_matrix_explicit_labels():
    records = [rec("水杯", "水杯"), rec("马", "马")]
    labels, matrix = confusion_matrix(records, labels=["水杯", "马"])
    assert labels == ["水杯", "马"]
    assert len(matrix) == 2 and all(len(row) == 2 for row in matrix)
    # 不在 labels 里的预测被忽略，不抛异常
    labels2, m2 = confusion_matrix([rec("水杯", "外星人")], labels=["水杯"])
    assert m2 == [[0]]


# ---------------------------------------------------------------- 汇总 / 扫描

def test_summarize_fields():
    records = [rec("水杯", "水杯", top_k=["水杯"]), rec(UNKNOWN_LABEL, UNKNOWN_LABEL)]
    s = summarize(records)
    for key in ("samples", "known", "unknown", "top1", "top3",
                "unknown_recall", "false_accept", "known_reject", "per_class"):
        assert key in s
    assert s["samples"] == 2 and s["known"] == 1 and s["unknown"] == 1
    assert s["top1"] == 1.0 and s["unknown_recall"] == 1.0
    assert s["per_class"] == {"水杯": 1}


def test_sweep_threshold_tradeoff():
    """阈值升高 → 误接收率单调不增，误拒率单调不减。"""
    records = [
        rec("水杯", "水杯", best="水杯", score=0.95),
        rec("马", "马", best="马", score=0.72),
        rec(UNKNOWN_LABEL, "水杯", best="水杯", score=0.68),
        rec(UNKNOWN_LABEL, "马", best="马", score=0.55),
    ]
    table = sweep(records, [0.5, 0.7, 0.8, 0.99])
    fas = [t["false_accept"] for t in table]
    frrs = [t["known_reject"] for t in table]
    assert fas == sorted(fas, reverse=True)
    assert frrs == sorted(frrs)

    # 低阈值：所有样本都被接收 → FAR 高、FRR 为 0
    low = table[0]
    assert low["false_accept"] == 1.0 and low["known_reject"] == 0.0
    assert low["top1"] == 1.0

    # 高阈值：全部判未知 → FAR 为 0、FRR 为 1
    high = table[-1]
    assert high["false_accept"] == 0.0 and high["known_reject"] == 1.0
    assert high["top1"] == 0.0


def test_sweep_threshold_rounded():
    table = sweep([rec("甲", "甲", best="甲", score=0.9)], [0.804999])
    assert table[0]["threshold"] == 0.805


def test_sweep_missing_raw_score_defaults_to_zero():
    records = [{"true": "甲", "best_name": "甲"}]
    assert sweep(records, [0.1])[0]["top1"] == 0.0


# ---------------------------------------------------------------- 格式化

def test_fmt_pct():
    assert fmt_pct(0.876) == "87.6%"
    assert fmt_pct(0) == "0.0%"
    assert fmt_pct(1) == "100.0%"
