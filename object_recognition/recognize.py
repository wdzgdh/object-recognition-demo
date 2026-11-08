# -*- coding: utf-8 -*-
"""识别逻辑：查询特征与特征库做余弦相似度检索，输出物体名或"未知物体"。

判断规则（简单、明确）：
- 相似度 >= 阈值 → 识别为最高相似度的类目
- 相似度 <  阈值 → 判为"未知物体"

阈值是唯一、严格的判断标准（界面滑条可调）。
"""
import numpy as np

import config


def recognize(feat, library, threshold=None, top_agg=1):
    """识别单张图的特征。

    参数:
        feat: ndarray (512,)，已 L2 归一化
        library: FeatureLibrary 实例
        threshold: 相似度阈值，默认 config.THRESHOLD
        top_agg: 类内聚合取 Top-K 近邻的 K 值（1 = 取最大值，即原始行为）
    返回:
        dict: {
            "name": str,          # 最相似类目名 或 "未知物体"
            "score": float,       # 该类目的聚合相似度
            "raw_score": float,   # 该类的最大原始相似度
            "is_unknown": bool,
            "top_k": list[dict],  # [{name, score}] 降序
        }
    """
    threshold = threshold if threshold is not None else config.THRESHOLD
    feat = np.asarray(feat, dtype="float32").reshape(1, -1)

    if library.is_empty():
        return {
            "name": "未知物体",
            "score": 0.0,
            "raw_score": 0.0,
            "is_unknown": True,
            "top_k": [],
        }

    db_feats = library.get_feats()  # (N, 512)
    sims = feat @ db_feats.T
    sims = sims[0]                  # (N,)

    names = [it["name"] for it in library.items]

    # ---- 类内聚合：每个类目取 Top-K 近邻平均（默认 K=1，即取最大值）----
    unique_names = []
    for n in names:
        if n not in unique_names:
            unique_names.append(n)

    class_scores = {}   # name -> 聚合得分
    class_max = {}      # name -> 该类的最大原始相似度
    for name in unique_names:
        idx = [i for i, n in enumerate(names) if n == name]
        s = sims[idx]
        k = min(top_agg, len(s))
        top_s = np.sort(s)[::-1][:k]
        class_scores[name] = float(np.mean(top_s))
        class_max[name] = float(s.max())

    ranked = sorted(class_scores.items(), key=lambda x: x[1], reverse=True)
    top_k = [{"name": n, "score": s} for n, s in ranked[: config.TOP_K]]

    best_name, best_score = ranked[0]
    raw_best = class_max[best_name]

    # ---- 唯一判断：绝对阈值 ----
    is_unknown = raw_best < threshold
    if is_unknown:
        best_name = "未知物体"

    return {
        "name": best_name,
        "score": float(best_score),
        "raw_score": float(raw_best),
        "is_unknown": is_unknown,
        "top_k": top_k,
    }
