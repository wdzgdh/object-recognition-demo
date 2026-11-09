# -*- coding: utf-8 -*-
"""评测脚本端到端测试（B6）：用替身顶掉 torch 依赖，验证 CSV 产物与指标。

不加载真模型，所以能在无 GPU / 无 torch 的 CI 上跑。
"""
import csv
import os
import sys
import types

import numpy as np
import pytest

import evaluate

DIM = 512
UNKNOWN = evaluate.UNKNOWN_LABEL


def _basis(i):
    v = np.zeros(DIM, dtype="float32")
    v[i] = 1.0
    return v


class FakeExtractor:
    """按文件名决定返回哪个基向量：cup→0，horse→1，其他→5（与两类都正交）。"""

    device = "cpu"

    def __init__(self, *a, **kw):
        pass

    def extract_from_file(self, path):
        name = os.path.basename(path)
        if name.startswith("cup"):
            return _basis(0)
        if name.startswith("horse"):
            return _basis(1)
        return _basis(5)

    # evaluate 在 --tta 时改用这个；这里保持同样行为以便断言
    def extract_tta(self, path):
        return self.extract_from_file(path)


class FakeLibrary:
    """两个已注册物体，与 FakeExtractor 的基向量对齐。"""

    def __init__(self, *a, **kw):
        self.items = [{"name": "水杯"}, {"name": "马"}]
        self._feats = np.stack([_basis(0), _basis(1)])

    def is_empty(self):
        return False

    def get_feats(self):
        return self._feats

    def stats(self):
        return {"objects": 2, "images": 2}


@pytest.fixture
def fake_torch_deps(monkeypatch):
    """把 feature / library 换成替身，绕开 torch。"""
    fm = types.ModuleType("feature")
    fm.FeatureExtractor = FakeExtractor
    lm = types.ModuleType("library")
    lm.FeatureLibrary = FakeLibrary
    monkeypatch.setitem(sys.modules, "feature", fm)
    monkeypatch.setitem(sys.modules, "library", lm)
    return fm, lm


def _make_dataset(root):
    """test/水杯/cup1.jpg、test/马/horse1.jpg、unknown/thing1.jpg"""
    test_dir = os.path.join(root, "test")
    unknown_dir = os.path.join(root, "unknown")
    for d, files in ((os.path.join(test_dir, "水杯"), ["cup1.jpg", "cup2.jpg"]),
                     (os.path.join(test_dir, "马"), ["horse1.jpg"]),
                     (unknown_dir, ["thing1.jpg"]),
                     (os.path.join(test_dir, "空目录"), [])):
        os.makedirs(d, exist_ok=True)
        for f in files:
            with open(os.path.join(d, f), "wb") as fh:
                fh.write(b"not-a-real-jpeg")   # 替身不读像素
    return test_dir, unknown_dir


def _read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_full_run_produces_all_outputs(fake_torch_deps, tmp_path):
    test_dir, unknown_dir = _make_dataset(str(tmp_path / "data"))
    out = str(tmp_path / "out")

    code = evaluate.main(["--test", test_dir, "--unknown", unknown_dir,
                          "--out", out, "--threshold", "0.5"])
    assert code == 0

    for name in ("report.csv", "summary.csv", "confusion.csv", "threshold_sweep.csv"):
        assert os.path.isfile(os.path.join(out, name)), name

    report = _read_csv(os.path.join(out, "report.csv"))
    assert len(report) == 4                       # 3 张已知 + 1 张未知
    assert {r["真实"] for r in report} == {"水杯", "马", UNKNOWN}

    summary = {r["指标"]: r["数值"] for r in _read_csv(os.path.join(out, "summary.csv"))}
    assert float(summary["top1"]) == 1.0          # 完全可分
    assert float(summary["unknown_recall"]) == 1.0
    assert float(summary["false_accept"]) == 0.0
    assert summary["samples"] == "4"


def test_limit_option(fake_torch_deps, tmp_path):
    test_dir, unknown_dir = _make_dataset(str(tmp_path / "data"))
    out = str(tmp_path / "out")
    evaluate.main(["--test", test_dir, "--unknown", unknown_dir,
                   "--out", out, "--limit", "2"])
    assert len(_read_csv(os.path.join(out, "report.csv"))) == 2


def test_empty_dataset_returns_2(fake_torch_deps, tmp_path):
    empty = str(tmp_path / "nothing")
    os.makedirs(empty, exist_ok=True)
    assert evaluate.main(["--test", empty, "--unknown", empty,
                          "--out", str(tmp_path / "out")]) == 2


def test_threshold_controls_unknown(fake_torch_deps, tmp_path):
    """阈值拉满 → 所有样本都被判未知（FAR 0、FRR 1）。"""
    test_dir, unknown_dir = _make_dataset(str(tmp_path / "data"))
    out = str(tmp_path / "out")
    evaluate.main(["--test", test_dir, "--unknown", unknown_dir,
                   "--out", out, "--threshold", "1.01"])
    summary = {r["指标"]: r["数值"] for r in _read_csv(os.path.join(out, "summary.csv"))}
    assert float(summary["known_reject"]) == 1.0
    assert float(summary["false_accept"]) == 0.0


def test_from_report_roundtrip(fake_torch_deps, tmp_path):
    """先跑一轮，再用 --from-report 复算，指标应一致。"""
    test_dir, unknown_dir = _make_dataset(str(tmp_path / "data"))
    out1 = str(tmp_path / "out1")
    evaluate.main(["--test", test_dir, "--unknown", unknown_dir,
                   "--out", out1, "--threshold", "0.5"])
    first = {r["指标"]: r["数值"] for r in _read_csv(os.path.join(out1, "summary.csv"))}

    out2 = str(tmp_path / "out2")
    assert evaluate.main(["--from-report", os.path.join(out1, "report.csv"),
                          "--out", out2]) == 0
    second = {r["指标"]: r["数值"] for r in _read_csv(os.path.join(out2, "summary.csv"))}

    for k in ("samples", "known", "unknown", "top1", "top3",
              "unknown_recall", "false_accept", "known_reject"):
        assert first[k] == second[k], k


def test_from_report_missing_file(tmp_path):
    assert evaluate.main(["--from-report", str(tmp_path / "nope.csv"),
                          "--out", str(tmp_path / "out")]) == 2


def test_collect_helpers(tmp_path):
    test_dir, unknown_dir = _make_dataset(str(tmp_path / "data"))
    assert len(evaluate.collect_known(test_dir)) == 3
    assert len(evaluate.collect_unknown(unknown_dir)) == 1
    assert evaluate.collect_known(str(tmp_path / "missing")) == []
    assert evaluate.collect_unknown(str(tmp_path / "missing")) == []
