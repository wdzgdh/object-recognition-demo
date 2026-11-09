# -*- coding: utf-8 -*-
"""特征库健壮性测试（B2）。只依赖 numpy，不需要 torch。"""
import json
import os

import numpy as np
import pytest

import config
import library as lib_mod
from library import FeatureLibrary, safe_dir_name

DIM = config.FEATURE_DIM


def _rand_feats(n, dim=DIM, seed=0):
    rng = np.random.default_rng(seed)
    f = rng.normal(size=(n, dim)).astype("float32")
    return f / np.linalg.norm(f, axis=1, keepdims=True)


@pytest.fixture()
def paths(tmp_path):
    return str(tmp_path / "library.json"), str(tmp_path / "library_feats.npy")


def _make(paths):
    return FeatureLibrary(library_path=paths[0], feats_path=paths[1])


def test_add_and_reload(paths):
    lib = _make(paths)
    assert lib.is_empty()
    n = lib.add("水杯", _rand_feats(3))
    assert n == 3

    again = _make(paths)
    assert not again.is_empty()
    assert again.items[0]["name"] == "水杯"
    assert again.feats.shape == (3, DIM)
    assert again.object_stats() == {"水杯": 3}
    assert again.validate()[0] is True


def test_meta_feature_mismatch_is_truncated(paths):
    """核心回归：元数据 5 条、特征 3 条，加载后必须对齐到 3 且给出告警。"""
    json_path, feats_path = paths
    items = [{"id": str(i), "name": "水杯", "created_at": "2026-01-01 00:00:00"}
             for i in range(5)]
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "items": items}, f, ensure_ascii=False)
    np.save(feats_path, _rand_feats(3))

    lib = _make(paths)
    assert len(lib.items) == 3
    assert lib.feats.shape[0] == 3
    assert lib.load_warning and "不一致" in lib.load_warning


def test_dimension_mismatch_goes_empty(paths):
    json_path, feats_path = paths
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "items": [{"id": "1", "name": "水杯"}]}, f)
    np.save(feats_path, _rand_feats(1, dim=256))

    lib = _make(paths)
    assert lib.is_empty()
    assert "维度不符" in (lib.load_warning or "")


def test_broken_meta_does_not_raise(paths):
    json_path, _feats = paths
    with open(json_path, "w", encoding="utf-8") as f:
        f.write("{ 这不是 json ")
    lib = _make(paths)
    assert lib.items == []
    assert "无法解析" in (lib.load_warning or "")


def test_save_is_atomic_no_tmp_left(paths):
    lib = _make(paths)
    lib.add("马", _rand_feats(2))
    base = os.path.dirname(paths[0])
    leftovers = [f for f in os.listdir(base) if f.endswith(".tmp") or ".tmp." in f]
    assert leftovers == []


def test_delete_one_and_rename(paths):
    lib = _make(paths)
    lib.add("水杯", _rand_feats(3, seed=1))
    lib.add("马", _rand_feats(2, seed=2))

    assert lib.delete_one("水杯", 0) is True
    assert lib.object_stats() == {"水杯": 2, "马": 2}
    assert lib.feats.shape[0] == 4
    assert lib.delete_one("水杯", 9) is False

    assert lib.rename("马", "白马") == 2
    assert lib.object_stats() == {"水杯": 2, "白马": 2}

    reloaded = _make(paths)
    assert reloaded.object_stats() == {"水杯": 2, "白马": 2}


def test_delete_all_removes_feats_file(paths):
    lib = _make(paths)
    lib.add("水杯", _rand_feats(2))
    assert os.path.exists(paths[1])
    lib.delete("水杯")
    assert not os.path.exists(paths[1])
    assert lib.is_empty()


def test_thumbnails_and_zip_roundtrip(paths, tmp_path):
    from PIL import Image

    src = tmp_path / "a.jpg"
    Image.new("RGB", (640, 480), (200, 30, 30)).save(src)

    lib = _make(paths)
    lib.add("水杯", _rand_feats(1), image_paths=[str(src)])
    thumbs = lib.thumb_paths("水杯")
    assert len(thumbs) == 1 and os.path.exists(thumbs[0])
    # 缩略图确实被缩小
    with Image.open(thumbs[0]) as im:
        assert max(im.size) <= lib_mod.THUMB_MAX_SIDE

    zip_path = str(tmp_path / "backup.zip")
    ok, msg = lib.export_zip(zip_path)
    assert ok, msg

    other = FeatureLibrary(library_path=str(tmp_path / "l2.json"),
                           feats_path=str(tmp_path / "f2.npy"))
    ok, msg = other.import_zip(zip_path)
    assert ok, msg
    assert other.object_stats() == {"水杯": 1}
    assert len(other.thumb_paths("水杯")) == 1


def test_invalid_add_raises(paths):
    lib = _make(paths)
    with pytest.raises(ValueError):
        lib.add("", _rand_feats(1))
    with pytest.raises(ValueError):
        lib.add("水杯", _rand_feats(1, dim=10))


def test_safe_dir_name():
    assert safe_dir_name("水杯/杯子") == "水杯_杯子"
    assert safe_dir_name("") == "object"


def test_single_instance_lock(paths):
    a = _make(paths)
    ok, _msg = a.acquire_lock()
    assert ok is True
    ok2, msg2 = _make(paths).acquire_lock()
    assert ok2 is False and "占用" in msg2
    a.release_lock()
