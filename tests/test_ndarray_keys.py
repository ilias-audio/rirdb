"""The ndarray reader's keys for the layouts of the active datasets: a guard for published ir_ids.

An ir_id is a hash of the reader's key (file#item), so these keys must not change when the reader does."""

from __future__ import annotations

from types import SimpleNamespace

import h5py
import numpy as np
import scipy.io

from rirdb.adapters import get_adapter
from rirdb.index import file_of, ir_id, split_key


def _records(params, root):
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="ndarray", params=params),
                         analysis=SimpleNamespace(reference_role=None))
    return list(get_adapter("ndarray", params).iter_records(ds, root))


def _ids_agree(recs):
    """The reader's own id and the index's id (from file and item) are the same hash."""
    for r in recs:
        assert r.ir_id.split(":", 1)[1] == ir_id("toy", *split_key(r.local_key, file_of(r.locator))).split("-", 1)[1]


def test_h5_records_over_two_axes(tmp_path):
    """MIRACLE, SRIRACHA, MP-RIR, soundcam: one record per index pair."""
    with h5py.File(tmp_path / "toy.h5", "w") as h:
        h["ir"] = np.zeros((50, 3, 4), dtype=np.float32)
    recs = _records({"glob": "*.h5", "container": "h5", "array": "ir", "time_axis": 0, "record_axes": [1, 2],
                     "fs": 16000}, tmp_path)
    assert [r.local_key for r in recs][:2] == ["toy.h5#0,0", "toy.h5#0,1"]
    assert recs[6].local_key == "toy.h5#1,2"
    _ids_agree(recs)


def test_one_ir_per_v5_file_keeps_item_0(tmp_path):
    """dirha_wsj and isobel: one (N, 1) array per file, record_axes omitted. Their published item is '0'."""
    scipy.io.savemat(tmp_path / "x.mat", {"risp_imp": np.zeros((100, 1))})
    recs = _records({"glob": "*.mat", "container": "mat", "array": "risp_imp", "time_axis": 0, "fs": 48000},
                    tmp_path)
    assert [r.local_key for r in recs] == ["x.mat#0"]
    _ids_agree(recs)


def test_v5_records_along_the_source_axis(tmp_path):
    """upv_rir_db: h_ctrl[time, sources, channels], one record per source."""
    d = tmp_path / "RC1" / "SC2"
    d.mkdir(parents=True)
    scipy.io.savemat(d / "DH_RC1_SC2_Z05_RIR.mat", {"h_ctrl": np.zeros((50, 8, 4))})
    recs = _records({"glob": "**/DH_*_RIR.mat", "container": "mat", "array": "h_ctrl", "time_axis": 0,
                     "channel_axis": 2, "record_axes": [1], "record_is": "src", "channels": [2, 3], "fs": 44100,
                     "pattern": r"(?P<cond>RC\d)/(?P<src>SC\d)/DH_RC\d_SC\d_(?P<rcv>Z\d+)_RIR\.mat$"}, tmp_path)
    assert recs[3].local_key == "RC1/SC2/DH_RC1_SC2_Z05_RIR.mat#3"
    assert (recs[3].src_key, recs[3].rcv_key, recs[3].condition_key) == ("SC2-3", "Z05", "RC1")
    _ids_agree(recs)


def test_array_pattern_puts_the_array_in_the_item(tmp_path):
    """dEchorate: one HDF5 array per room condition."""
    with h5py.File(tmp_path / "f.h5", "w") as h:
        h["000000"] = np.zeros((20, 6, 2))
        h["011111"] = np.zeros((20, 6, 2))
    recs = _records({"glob": "f.h5", "container": "h5", "array_pattern": "[01]{6}", "time_axis": 0,
                     "record_axes": [1, 2], "fs": 48000}, tmp_path)
    keys = {r.local_key for r in recs}
    assert len(recs) == 24 and "f.h5#000000#5,1" in keys and "f.h5#011111#0,0" in keys
    assert {r.condition_key for r in recs} == {"000000", "011111"}
    _ids_agree(recs)
