"""Readers of IRs stored as text: csv_tree (tuni_rcair) and kinect_rir_txt (kinect_rir)."""

from __future__ import annotations

import gzip
from types import SimpleNamespace

import numpy as np
import pytest

from rirdb.adapters import get_adapter


def records(name, root, params):
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name=name, params=params))
    ad = get_adapter(name, params)
    return ad, list(ad.iter_records(ds, root))


CSV_PARAMS = {"glob": "**/*.csv", "pattern": r"(?P<room>[^/]+)/(?P<src>s\d)_(?P<rcv>r\d)\.csv$", "fs": 48000,
              "roles": ["a", "b", "c"], "ir_kind_from_regex": {"Anechoic": "anechoic"}}


def test_csv_tree(tmp_path):
    x = np.arange(30, dtype=float).reshape(10, 3) / 100
    for room in ("Office", "AnechoicChamber"):
        (tmp_path / room).mkdir()
        text = "\n".join(",".join(f"{v:.2f}" for v in row) for row in x) + "\n\n"     # a trailing blank line
        (tmp_path / room / "s1_r2.csv").write_text(text)
    ad, recs = records("csv_tree", tmp_path, CSV_PARAMS)
    assert [r.local_key for r in recs] == ["AnechoicChamber/s1_r2.csv", "Office/s1_r2.csv"]
    assert [r.ir_kind for r in recs] == ["anechoic", "room"]
    assert recs[1].room_key == "office" and recs[1].src_key == "s1" and recs[1].rcv_key == "r2"
    for r in recs:
        y, fs = ad.load(r.locator, tmp_path)
        assert fs == 48000 and y.shape == (3, r.n_samples) == (3, 10)
        np.testing.assert_allclose(y, x.T)


def test_csv_tree_roles_must_match_the_columns(tmp_path):
    (tmp_path / "Office").mkdir()
    (tmp_path / "Office" / "s1_r2.csv").write_text("0.1,0.2\n0.3,0.4\n")
    with pytest.raises(ValueError, match="2 columns, 3 roles"):
        records("csv_tree", tmp_path, CSV_PARAMS)


def test_kinect_rir_txt(tmp_path):
    mics = [(1.0, 2.0, 1.5), (1.1, 2.0, 1.5), (1.2, 2.0, 1.5), (1.3, 2.0, 1.5)]
    src = (3.0, 4.0, 1.7)
    xyz = lambda p: "_".join(f"{v:g}" for v in p)      # noqa: E731
    name = "__".join(["0.3", "4_5_3", *map(xyz, mics), xyz(src)]) + ".rir.gz"
    folder = tmp_path / "kinect_rir" / "tr" / "000" / "rir"
    folder.mkdir(parents=True)
    n = int(0.3 * 16000)
    x = np.random.default_rng(0).standard_normal((n, 4)).round(6)
    with gzip.open(folder / name, "wt") as f:
        np.savetxt(f, x, delimiter="\t", fmt="%.6f")
    ad, recs = records("kinect_rir_txt", tmp_path, {})
    assert len(recs) == 1
    r = recs[0]
    assert r.n_samples == n and r.src_pos == src
    np.testing.assert_allclose(r.rcv_pos, (1.15, 2.0, 1.5))
    assert r.room_key == "tr000-0-3-4x5x3" and r.capture_format == "array_raw" and r.ir_kind == "virtual"
    y, fs = ad.load(r.locator, tmp_path)
    assert fs == 16000 and y.shape == (4, n)
    np.testing.assert_allclose(y, x.T)
