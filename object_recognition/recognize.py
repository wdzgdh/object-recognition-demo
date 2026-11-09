# -*- coding: utf-8 -*-
"""识别逻辑：查询特征与特征库做余弦相似度检索，输出物体名或"未知物体"。

判断规则（简单、明确）：
- 相似度 >= 阈值 → 识别为最高相似度的类目
- 相似度 <  阈值 → 判为"未知物体"

**口径统一（修复 P1-R3）**：界面显示的分数与"是否未知"的判定必须用同一个数。
一律以「查询特征与该物体所有注册特征的最大相似度」= raw_score 为准，
类内平均只作为参考字段 agg_score 返回，不参与判定，也不出现在 Top-K 里。

阈值是唯一、严格的判断标准（界面滑条可调，改动会落盘到 config.THRESHOLD_STORE）。
"""
from collections import deque

import numpy as np

import config

UNKNOWN_NAME = "未知物体"


def _class_max_scores(sims, names):
    """把逐条特征的相似度聚合成「每个类的最佳分数」。

    参数:
        sims:  ndarray(N,)  与 names 一一对应的相似度
        names: list[str]    长度为 N 的类目名（可能含重复）
    返回:
        (order, class_raw, class_agg)
        order      : 按 raw 降序的类目名列表
        class_raw  : {name: 最大相似度}
        class_agg  : {name: 该类平均相似度}（仅参考，不参与判定）
    """
    buckets = {}
    for i, n in enumerate(names):
        buckets.setdefault(n, []).append(float(sims[i]))

    class_raw, class_agg = {}, {}
    for n, vals in buckets.items():
        arr = np.asarray(vals, dtype="float32")
        class_raw[n] = float(arr.max())
        class_agg[n] = float(arr.mean())

    order = sorted(class_raw, key=lambda k: class_raw[k], reverse=True)
    return order, class_raw, class_agg


def recognize(feat, library, threshold=None, top_agg=1):
    """识别单张图的特征。

    参数:
        feat:      ndarray (512,)，已 L2 归一化
        library:   FeatureLibrary 实例
        threshold: 相似度阈值，默认 config.THRESHOLD
        top_agg:   兼容保留参数。判定口径恒为 max，不再受它影响
    返回:
        dict: {
            "name": str,          # 最相似类目名 或 "未知物体"
            "score": float,       # 与判定同口径（= raw_score）
            "raw_score": float,   # 最大相似度，判定依据
            "agg_score": float,   # 该类平均相似度，仅供参考
            "is_unknown": bool,
            "top_k": list[dict],  # [{name, score}] 按 score 降序
        }
    """
    threshold = threshold if threshold is not None else config.THRESHOLD
    feat = np.asarray(feat, dtype="float32").reshape(1, -1)

    empty = {
        "name": UNKNOWN_NAME,
        "score": 0.0,
        "raw_score": 0.0,
        "agg_score": 0.0,
        "is_unknown": True,
        "top_k": [],
    }
    if library.is_empty():
        return empty

    db_feats = library.get_feats()          # (N, 512)
    if db_feats is None or len(db_feats) == 0:
        return empty

    sims = (feat @ db_feats.T)[0]           # (N,)

    names = [it["name"] for it in library.items]
    # 库若出现元数据/特征长度不一致（历史脏数据），按较短者对齐，绝不越界
    n = min(len(names), int(sims.shape[0]))
    if n == 0:
        return empty
    names = names[:n]
    sims = sims[:n]

    order, class_raw, class_agg = _class_max_scores(sims, names)
    top_k = [{"name": name, "score": class_raw[name]}
             for name in order[: max(1, int(config.TOP_K))]]

    best_name = order[0]
    raw_best = class_raw[best_name]
    is_unknown = raw_best < threshold
    if is_unknown:
        best_name = UNKNOWN_NAME

    return {
        "name": best_name,
        "score": float(raw_best),           # 与判定同口径
        "raw_score": float(raw_best),
        "agg_score": float(class_agg[order[0]]),
        "is_unknown": bool(is_unknown),
        "top_k": top_k,
    }


class ResultSmoother:
    """实时结果时间平滑：连续 n 帧一致才认为稳定（修复 P1-R5 名称跳动）。

    用法：
        sm = ResultSmoother(config.SMOOTH_N)
        name = sm.update(result["name"])   # 未稳定返回 None，调用方保持上一次显示
    """

    def __init__(self, n=3):
        self.n = max(1, int(n))
        self._buf = deque(maxlen=self.n)

    def reset(self):
        self._buf.clear()

    def update(self, name):
        if not name:
            return None
        self._buf.append(name)
        if self.n == 1:
            return name
        if len(self._buf) < self.n:
            return None
        first = self._buf[0]
        if all(x == first for x in self._buf):
            return first
        return None

    @property
    def stable(self):
        """当前缓冲区是否已经收敛到同一个值。"""
        return bool(self._buf) and len(set(self._buf)) == 1
