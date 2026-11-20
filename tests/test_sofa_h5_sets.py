"""The h5_sets reader (adapters/h5_sets.py): measurement sets kept as 2-D arrays in the groups of HDF5 files."""

from __future__ import annotations

from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from rirdb.adapters import get_adapter


def reader_and_records(tmp_path, params):
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="h5_sets", params=params),
                         analysis=SimpleNamespace(reference_role=None))
    reader = get_adapter("h5_sets", params)
    return reader, list(reader.iter_records(ds, tmp_path))


def test_h5_sets_two_sets_with_positions(tmp_path):
    """The DTU room layout: one array per set, positions beside each, one source, the rate as a string attribute."""
    rng = np.random.default_rng(0)
    plane, sphere = rng.standard_normal((4, 50)), rng.standard_normal((2, 50))
    with h5py.File(tmp_path / "rir_011.h5", "w") as h:
        h["dataset_plane/impulse_response"] = plane
        h["dataset_plane/position"] = np.arange(12.0).reshape(3, 4)             # (3, n): rows x, y, z
        h["dataset_sphere/impulse_response"] = sphere
        h["dataset_sphere/position"] = np.ones((3, 2))
        h["dataset_plane/noise"] = np.zeros((4, 50))                          # not a set
        h["source/position"] = np.array([[1.0], [2.0], [0.5]])
        h.attrs["sampling_frequency"] = "48000 samples/second"
    params = {"glob": "rir_*.h5", "pattern": r"rir_(?P<room>\d+)\.h5$",
              "sets": r"dataset_(?P<set>plane|sphere)/impulse_response", "time_axis": 1,
              "fs_attr": "sampling_frequency", "positions": "position", "src_position": "source/position"}
    reader, recs = reader_and_records(tmp_path, params)
    assert [r.local_key for r in recs] == [f"rir_011.h5#dataset_plane/impulse_response#{k}" for k in range(4)] + \
        [f"rir_011.h5#dataset_sphere/impulse_response#{k}" for k in range(2)]
    assert [r.rcv_key for r in recs] == ["plane-0", "plane-1", "plane-2", "plane-3", "sphere-0", "sphere-1"]
    assert recs[1].rcv_pos == (1.0, 5.0, 9.0) and recs[5].rcv_pos == (1.0, 1.0, 1.0)
    assert {r.src_pos for r in recs} == {(1.0, 2.0, 0.5)} and {r.fs for r in recs} == {48000}
    assert recs[0].room_key == "011" and recs[0].channel_roles == ("omni",) and recs[0].n_samples == 50
    x, fs = reader.load(recs[1].locator, tmp_path)
    assert fs == 48000 and x.shape == (1, 50)
    np.testing.assert_array_equal(x[0], plane[1])


def test_h5_sets_set_names_and_root_positions(tmp_path):
    """The Jabra layout: rirs and ref_rirs at the root, positions {set: path} stored (n, 3) in millimetres."""
    rng = np.random.default_rng(1)
    rirs = rng.standard_normal((40, 4))                                        # time first
    with h5py.File(tmp_path / "jabra.h5", "w") as h:
        h["rirs"], h["ref_rirs"] = rirs, rng.standard_normal((40, 2))
        h["mic_grid"] = np.arange(12.0).reshape(4, 3) * 1000
        h["ref_mic_grid"] = np.zeros((2, 3))
        h["loudspeaker_position"] = np.array([500.0, 0.0, 1000.0])
        h.attrs["fs"] = 16000
    params = {"glob": "*.h5", "sets": r"(?P<set>rirs|ref_rirs)", "set_names": {"rirs": "grid", "ref_rirs": "ref"},
              "positions": {"rirs": "mic_grid", "ref_rirs": "ref_mic_grid"}, "src_position": "loudspeaker_position",
              "pos_scale": 0.001, "time_axis": 0, "fs_attr": "fs", "room_default": "lab"}
    reader, recs = reader_and_records(tmp_path, params)
    assert [r.rcv_key for r in recs] == ["ref-0", "ref-1", "grid-0", "grid-1", "grid-2", "grid-3"]
    grid = [r for r in recs if r.rcv_key.startswith("grid")]
    assert grid[2].rcv_pos == (6.0, 7.0, 8.0) and grid[0].src_pos == (0.5, 0.0, 1.0)
    assert {r.room_key for r in recs} == {"lab"} and {r.fs for r in recs} == {16000}
    x, _ = reader.load(grid[3].locator, tmp_path)
    np.testing.assert_array_equal(x[0], rirs[:, 3])


def test_h5_sets_groups_rows_channels(tmp_path):
    """The Niels Bohr layout: an em32 group whose array is one IR of 4 channels, and a line whose rows are IRs."""
    rng = np.random.default_rng(2)
    em, line = rng.standard_normal((4, 50)), rng.standard_normal((3, 50))
    with h5py.File(tmp_path / "rir_em32.h5", "w") as h:
        h["eigenmike01/impulse_response"] = em
        h["eigenmike01/position_centre"] = np.array([[1.0], [2.0], [3.0]])
        h.attrs["sampling_rate"] = "48000 samples/second"
    with h5py.File(tmp_path / "rir_line.h5", "w") as h:
        h["line01/impulse_response"] = line
        h["line01/posRIR"] = np.zeros((3, 3))
        h.attrs["sampling_rate"] = "48000 samples/second"
    common = {"time_axis": 1, "fs_attr": "sampling_rate", "src_default": "BM6"}
    params = {"groups": [
        {**common, "glob": "rir_em32.h5", "sets": r"(?P<set>eigenmike\d+)/impulse_response", "rows": "channels",
         "positions": "position_centre", "capture_format": "em32_raw", "reference_role": "mean"},
        {**common, "glob": "rir_line.h5", "sets": r"(?P<set>line\d+)/impulse_response", "positions": "posRIR"}]}
    reader, recs = reader_and_records(tmp_path, params)
    assert [r.local_key for r in recs] == ["rir_em32.h5#eigenmike01/impulse_response"] + \
        [f"rir_line.h5#line01/impulse_response#{k}" for k in range(3)]
    assert [r.rcv_key for r in recs] == ["eigenmike01", "line01-0", "line01-1", "line01-2"]
    assert recs[0].channel_roles == ("mic_0", "mic_1", "mic_2", "mic_3") and recs[0].rcv_pos == (1.0, 2.0, 3.0)
    assert {r.src_key for r in recs} == {"BM6"} and recs[0].extra["reference_role"] == "mean"
    x, _ = reader.load(recs[0].locator, tmp_path)
    np.testing.assert_array_equal(x, em)
    x, _ = reader.load(recs[3].locator, tmp_path)
    np.testing.assert_array_equal(x, line[2:3])


def write_one_set(path, array, positions=None, source=None):
    with h5py.File(path, "w") as h:
        h["set_a/ir"] = array
        if positions is not None:
            h["set_a/pos"] = positions
        if source is not None:
            h["source"] = source


def test_h5_sets_rejects_bad_input(tmp_path):
    params = {"glob": "*.h5", "sets": r"(?P<set>set_a)/ir", "time_axis": 1, "fs": 8000}
    write_one_set(tmp_path / "a.h5", np.zeros((50, 3)))                       # time on axis 0, not 1
    with pytest.raises(ValueError, match="shorter axis"):
        reader_and_records(tmp_path, params)
    write_one_set(tmp_path / "a.h5", np.zeros((3, 50)), positions=np.zeros((3, 4)))
    with pytest.raises(ValueError, match="positions of 3 IRs"):
        reader_and_records(tmp_path, {**params, "positions": "pos"})
    write_one_set(tmp_path / "a.h5", np.zeros((3, 50)), source=np.zeros((2, 3)))
    with pytest.raises(ValueError, match="one position"):
        reader_and_records(tmp_path, {**params, "src_position": "source"})
    write_one_set(tmp_path / "a.h5", np.zeros((3, 50)))
    with pytest.raises(ValueError, match="2 roles for 3 channels"):
        reader_and_records(tmp_path, {**params, "rows": "channels", "roles": ["a", "b"]})
    with pytest.raises(ValueError, match="2 roles for 1 channels"):
        reader_and_records(tmp_path, {**params, "roles": ["a", "b"]})
    positions = np.arange(9.0).reshape(3, 3)                                    # 3 IRs: read as (n, 3)
    write_one_set(tmp_path / "a.h5", np.zeros((3, 50)), positions=positions)
    _, recs = reader_and_records(tmp_path, {**params, "positions": "pos"})
    assert [r.rcv_pos for r in recs] == [tuple(p) for p in positions]
