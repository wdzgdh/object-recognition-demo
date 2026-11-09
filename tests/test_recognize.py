# -*- coding: utf-8 -*-
"""检索口径与时间平滑测试（B5）。只依赖 numpy，不需要 torch。"""
import numpy as np
import pytest

import config
import recognize as rec_mod
from recognize import ResultSmoother, UNKNOWN_NAME, recognize

DIM = config.FEATURE_DIM


class FakeLibrary:
    """最小特征库替身，只实现 recognize() 用到的接口。"""

    def __init__(self, names, feats):
        self.items = [{"name": n} for n in names]
        self._feats = np.asarray(feats, dtype="float32")

    def is_empty(self):
        return self._feats is None or len(self._feats) == 0

    def get_feats(self):
        return self._feats


def _unit(v):
    v = np.asarray(v, dtype="float32")
    return v / np.linalg.norm(v)


def _basis(i, dim=DIM):
    v = np.zeros(dim, dtype="float32")
    v[i] = 1.0
    return v


def test_known_object_hits():
    lib = FakeLibrary(["水杯", "马"], np.stack([_basis(0), _basis(1)]))
    r = recognize(_basis(0), lib, threshold=0.8)
    assert r["name"] == "水杯"
    assert r["is_unknown"] is False
    assert r["score"] == pytest.approx(1.0, abs=1e-6)


def test_unknown_below_threshold():
    lib = FakeLibrary(["水杯"], np.stack([_basis(0)]))
    r = recognize(_basis(5), lib, threshold=0.8)   # 正交 → 相似度 0
    assert r["name"] == UNKNOWN_NAME
    assert r["is_unknown"] is True


def test_score_equals_raw_score():
    """判定口径必须与显示口径一致（P1-R3）。"""
    lib = FakeLibrary(["水杯", "水杯", "马"],
                      np.stack([_basis(0), _unit(_basis(0) + 0.3 * _basis(2)), _basis(1)]))
    r = recognize(_basis(0), lib, threshold=0.5)
    assert r["score"] == r["raw_score"]
    assert r["agg_score"] <= r["raw_score"] + 1e-6


def test_topk_uses_raw_and_is_sorted():
    lib = FakeLibrary(["甲", "乙", "丙"],
                      np.stack([_basis(0), _unit(_basis(0) + _basis(1)), _basis(2)]))
    r = recognize(_basis(0), lib, threshold=0.1)
    scores = [t["score"] for t in r["top_k"]]
    assert scores == sorted(scores, reverse=True)
    assert r["top_k"][0]["name"] == "甲"
    assert len(r["top_k"]) <= config.TOP_K


def test_mismatched_lengths_do_not_raise():
    """历史脏数据：元数据比特征多，不能下标越界。"""
    lib = FakeLibrary(["甲", "乙", "丙"], np.stack([_basis(0)]))
    r = recognize(_basis(0), lib, threshold=0.3)
    assert r["name"] == "甲"


def test_empty_library():
    lib = FakeLibrary([], np.zeros((0, DIM), dtype="float32"))
    r = recognize(_basis(0), lib)
    assert r["is_unknown"] is True and r["top_k"] == []


def test_smoother_requires_n_consecutive():
    sm = ResultSmoother(3)
    assert sm.update("水杯") is None
    assert sm.update("水杯") is None
    assert sm.update("马") is None        # 中断，重新计数
    assert sm.update("马") is None
    assert sm.update("马") == "马"
    assert sm.stable is True


def test_smoother_n1_is_passthrough():
    assert ResultSmoother(1).update("水杯") == "水杯"


def test_unknown_name_constant():
    assert rec_mod.UNKNOWN_NAME == "未知物体"
    assert config.TOP_K >= 1
