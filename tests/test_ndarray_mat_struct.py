"""The mat_struct reader: IRs in a field of a MATLAB v5 struct, in a cell array, or in a struct array."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import scipy.io

from rirdb.adapters import get_adapter


def _read(params, root, ir_kinds=("room",)):
    ds = SimpleNamespace(id="toy", ir_kinds=list(ir_kinds), adapter=SimpleNamespace(name="mat_struct", params=params),
                         analysis=SimpleNamespace(reference_role=None))
    ad = get_adapter("mat_struct", params)
    return ad, list(ad.iter_records(ds, root))


def test_one_ir_per_file(tmp_path):
    """tuil_srir_rooms: recordings.ir is (samples, 7 microphones), recordings.fs the rate. The item is empty."""
    ir = np.random.default_rng(0).standard_normal((60, 7))
    (tmp_path / "measurements" / "H2505").mkdir(parents=True)
    scipy.io.savemat(tmp_path / "measurements/H2505/H2505_30_1_0.mat", {"recordings": {"ir": ir, "fs": 48000.0}})
    params = {"glob": "measurements/*/*.mat", "var": "recordings", "field": "ir", "fs_field": "fs",
              "pattern": r"/(?P<room>H\d{4})_(?P<src>\d+)_\d_(?P<rcv>\d)\.mat$", "capture_format": "sdm_array",
              "reference_role": "mic_6"}
    ad, recs = _read(params, tmp_path)
    r = recs[0]
    assert len(recs) == 1 and r.local_key == "measurements/H2505/H2505_30_1_0.mat"
    assert (r.room_key, r.src_key, r.rcv_key, r.fs, r.n_samples) == ("h2505", "30", "0", 48000, 60)
    assert r.channel_roles == tuple(f"mic_{i}" for i in range(7)) and r.extra["reference_role"] == "mic_6"
    x, fs = ad.load(r.locator, tmp_path)
    np.testing.assert_array_equal(x, ir.T)
    assert fs == 48000


def test_cell_rows(tmp_path):
    """tuil_tv_studio_brirs: brirData.impulseResponse is a cell array {name, samples x 2; ...}, one row per
    loudspeaker; the row's name is the source."""
    rng = np.random.default_rng(1)
    brirs = [rng.standard_normal((40, 2)) for _ in range(3)]
    cells = np.empty((3, 2), dtype=object)
    for k, (name, x) in enumerate(zip(["LS 1", "LS 2", "Sub"], brirs)):
        cells[k, 0], cells[k, 1] = name, x
    (tmp_path / "101").mkdir()
    scipy.io.savemat(tmp_path / "101/brir005.mat", {"brirData": {"impulseResponse": cells, "fs": 48000.0}})
    params = {"glob": "*/brir*.mat", "pattern": r"^(?P<rcv>\d+)/brir(?P<rcv_2>\d+)\.mat$", "var": "brirData",
              "field": "impulseResponse", "records": "cells", "cell_column": 1, "name_column": 0,
              "fs_field": "fs", "room_default": "tv-studio", "capture_format": "binaural", "roles": ["L", "R"]}
    ad, recs = _read(params, tmp_path)
    assert [r.local_key for r in recs] == [f"101/brir005.mat#{k}" for k in range(3)]
    assert [r.src_key for r in recs] == ["LS 1", "LS 2", "Sub"] and recs[0].rcv_key == "101_005"
    for r, want in zip(recs, brirs):
        x, _ = ad.load(r.locator, tmp_path)
        np.testing.assert_array_equal(x, want.T)


def _struct_array(rng):
    """sIR as a struct array of 3 loudspeakers: ir (samples, 2), fs, label, az, el, r."""
    s = np.empty((1, 3), dtype=[(n, object) for n in ("ir", "fs", "label", "az", "el", "r")])
    irs = []
    for k, (label, az, el) in enumerate([("B.1", 0.0, 0.0), ("B.2", 90.0, 0.0), ("C.1", 0.0, 90.0)]):
        irs.append(rng.standard_normal((30, 2)))
        s[0, k] = (irs[-1], 44100.0, label, az, el, 2.0)
    return s, irs


def test_struct_array(tmp_path):
    """oldenburg_gesture_lab: one record per element; the source's name and direction come from the element,
    the channel names from csMicLabels, the receiver at the origin."""
    s, irs = _struct_array(np.random.default_rng(2))
    (tmp_path / "raw").mkdir()
    scipy.io.savemat(tmp_path / "raw/ir_with_platform.mat",
                     {"sIR": s, "csMicLabels": np.array(["InEar.L", "InEar.R"], dtype=object)})
    params = {"groups": [{"glob": "raw/*.mat", "pattern": r"_(?P<cond>with_platform)\.mat$", "var": "sIR",
                          "records": "elements", "field": "ir", "fs_field": "fs", "src_field": "label",
                          "pos_fields": ["az", "el", "r"], "roles_var": "csMicLabels", "rcv": "centre",
                          "capture_format": "array_raw"}]}
    ad, recs = _read(params, tmp_path)
    assert [r.local_key for r in recs] == [f"raw/ir_with_platform.mat#{k}" for k in range(3)]
    assert [r.src_key for r in recs] == ["B.1", "B.2", "C.1"]
    assert {(r.rcv_key, r.condition_key, r.fs, r.channel_roles) for r in recs} == {
        ("centre", "with_platform", 44100, ("InEar.L", "InEar.R"))}
    np.testing.assert_allclose(recs[1].src_pos, (0.0, 2.0, 0.0), atol=1e-12)
    np.testing.assert_allclose(recs[2].src_pos, (0.0, 0.0, 2.0), atol=1e-12)
    assert recs[0].rcv_pos == (0.0, 0.0, 0.0)
    x, fs = ad.load(recs[2].locator, tmp_path)
    np.testing.assert_array_equal(x, irs[2].T)
    assert fs == 44100


def test_roles_and_formats_by_channel_count(tmp_path):
    """salford_room_in_room: omni files and SoundField files in one folder; sh_norm only on the B-format."""
    rng = np.random.default_rng(3)
    scipy.io.savemat(tmp_path / "Position_1_A_Omni.mat",
                     {"obj": {"measurements": {"ir": rng.standard_normal(50)}, "device": {"fs": 48000.0}}})
    scipy.io.savemat(tmp_path / "Position_1_A_SF.mat",
                     {"obj": {"measurements": {"ir": rng.standard_normal((50, 4))}, "device": {"fs": 48000.0}}})
    params = {"glob": "*.mat", "var": "obj", "field": "measurements.ir", "fs_field": "device.fs",
              "channel_formats": {1: "mono_omni", 4: "foa_fuma"}, "roles": {1: ["omni"], 4: ["W", "X", "Y", "Z"]},
              "sh_norm": "FuMa", "orientation_known": False, "ir_kind_from_regex": {"_SF": "vehicle"}}
    ad, recs = _read(params, tmp_path, ir_kinds=("room", "vehicle"))
    omni, sf = recs
    assert (omni.capture_format, omni.channel_roles, omni.sh_norm, omni.ir_kind) == ("mono_omni", ("omni",), None,
                                                                                    "room")
    assert (sf.capture_format, sf.channel_roles, sf.sh_norm, sf.ir_kind) == ("foa_fuma", ("W", "X", "Y", "Z"),
                                                                            "FuMa", "vehicle")
    assert not sf.orientation_known and omni.local_key == "Position_1_A_Omni.mat"
    x, _ = ad.load(sf.locator, tmp_path)
    assert x.shape == (4, 50)
    with pytest.raises(ValueError, match="roles"):
        _read({**params, "roles": ["W", "X", "Y"]}, tmp_path)
