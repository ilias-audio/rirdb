"""The ndarray reader's optional parameters: what each one sets, and that loading still gives the right samples."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from rirdb.adapters import get_adapter


def _read(params, root):
    """(adapter, records) for a toy dataset read with these params."""
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="ndarray", params=params),
                         analysis=SimpleNamespace(reference_role=None))
    ad = get_adapter("ndarray", params)
    return ad, list(ad.iter_records(ds, root))


def test_orientation_sh_norm_and_src_default(tmp_path):
    """Ambisonic arrays with sources all around: sh_norm and orientation_known reach the records. Keys in two
    parts of the name are joined as in wav_tree, and a file whose name has no source gets src_default."""
    a = np.zeros((2, 4, 30))
    np.save(tmp_path / "BK211_1_S3.npy", a)
    np.save(tmp_path / "BK211_2.npy", a)
    params = {"glob": "*.npy", "container": "npy", "time_axis": 2, "channel_axis": 1, "record_axes": [0],
              "fs": 48000, "roles": ["W", "X", "Y", "Z"], "capture_format": "foa_fuma", "sh_norm": "FuMa",
              "orientation_known": False, "src_default": "Base",
              "pattern": r"(?P<cond>BK\d+)_(?P<cond_2>\d)(?:_(?P<src>S\d))?\.npy$"}
    _, recs = _read(params, tmp_path)
    assert len(recs) == 4
    assert {r.sh_norm for r in recs} == {"FuMa"} and not any(r.orientation_known for r in recs)
    by_file = {r.local_key.split("#")[0]: r for r in recs}
    assert by_file["BK211_1_S3.npy"].condition_key == "BK211_1" and by_file["BK211_1_S3.npy"].src_key == "S3"
    assert by_file["BK211_2.npy"].condition_key == "BK211_2" and by_file["BK211_2.npy"].src_key == "Base"


def test_defaults_leave_orientation_known_and_no_sh_norm(tmp_path):
    np.save(tmp_path / "x.npy", np.zeros((3, 30)))
    _, recs = _read({"glob": "*.npy", "container": "npy", "time_axis": 1, "fs": 8000}, tmp_path)
    assert all(r.orientation_known and r.sh_norm is None and r.src_key is None for r in recs)
    assert [r.rcv_key for r in recs] == ["0", "1", "2"]


def test_an_unknown_sh_norm_is_refused(tmp_path):
    np.save(tmp_path / "x.npy", np.zeros((3, 30)))
    with pytest.raises(ValueError, match="sh_norm"):
        _read({"glob": "*.npy", "container": "npy", "time_axis": 1, "fs": 8000, "sh_norm": "ambix"}, tmp_path)


def test_one_ir_per_file_has_an_empty_item(tmp_path):
    """record_axes []: the file holds one IR, so its key is the bare path and ir_id the hash of the path."""
    from rirdb.index import file_of, ir_id, split_key

    a = np.random.default_rng(0).standard_normal((40, 1))
    np.save(tmp_path / "x.npy", a)
    ad, recs = _read({"glob": "*.npy", "container": "npy", "time_axis": 0, "record_axes": [], "fs": 16000},
                     tmp_path)
    assert [r.local_key for r in recs] == ["x.npy"] and split_key(recs[0].local_key, file_of(recs[0].locator)) == ("x.npy", "")
    assert recs[0].rcv_key is None
    assert recs[0].ir_id.split(":")[1] == ir_id("toy", "x.npy", "").split("-")[1]
    x, fs = ad.load(recs[0].locator, tmp_path)
    np.testing.assert_array_equal(x, a.T)


def test_one_ir_per_v5_variable_has_an_empty_item(tmp_path):
    """rwth_em64: h[samples, 64 microphones] in each file, one IR per file."""
    import scipy.io

    a = np.random.default_rng(1).standard_normal((40, 5))
    scipy.io.savemat(tmp_path / "speaker_1.mat", {"h": a})
    ad, recs = _read({"glob": "*.mat", "container": "mat", "array": "h", "time_axis": 0, "channel_axis": 1,
                      "record_axes": [], "fs": 48000}, tmp_path)
    assert [r.local_key for r in recs] == ["speaker_1.mat"] and recs[0].channel_roles[-1] == "mic_4"
    x, _ = ad.load(recs[0].locator, tmp_path)
    np.testing.assert_array_equal(x, a.T)


def test_an_array_with_no_record_axes_ends_the_key(tmp_path):
    """array_pattern with one IR per array: rel#array, with no "#" after it."""
    import h5py

    with h5py.File(tmp_path / "f.h5", "w") as h:
        h["000000"] = np.zeros(30)
        h["000001"] = np.zeros(30)
    _, recs = _read({"glob": "f.h5", "container": "h5", "array_pattern": "[01]{6}", "time_axis": 0,
                     "fs": 8000}, tmp_path)
    assert [r.local_key for r in recs] == ["f.h5#000000", "f.h5#000001"]


def _rwth_like(tmp_path):
    """data.mat (HDF5) like rwth_continuous_brirs: referenceIrs[24 angles x 50 samples x 2 loudspeakers x 4 ears],
    the angles in referenceAnglesDegree (two sweeps, so each angle twice)."""
    import h5py

    a = np.random.default_rng(2).standard_normal((24, 50, 2, 4))
    with h5py.File(tmp_path / "data.mat", "w") as h:
        h["data/referenceIrs"] = a
        h["data/referenceAnglesDegree"] = np.tile(np.arange(-180.0, 180.0, 30.0), 2)[None, :]
    params = {"glob": "data.mat", "container": "h5", "array": "data/referenceIrs", "time_axis": 1,
              "channel_axis": 3, "record_axes": [0, 2], "channels": [0, 1], "roles": ["L", "R"], "fs": 44100,
              "select": {0: list(range(0, 24, 2))},
              "axis_keys": {0: {"as": "rcv", "names_from": "data/referenceAnglesDegree", "format": "{:g}deg"},
                            2: {"as": "src", "names": ["left", "right"]}}}
    return a, params


def test_select_and_axis_keys(tmp_path):
    """select keeps every other angle; the keys keep the original index, so the ir_ids are those of a full read."""
    a, params = _rwth_like(tmp_path)
    ad, recs = _read(params, tmp_path)
    assert len(recs) == 24
    assert {r.locator["idx"][0] for r in recs} == set(range(0, 24, 2))
    assert recs[0].local_key == "data.mat#0,0" and recs[3].local_key == "data.mat#2,1"
    assert (recs[0].rcv_key, recs[0].src_key) == ("-180deg", "left")
    assert (recs[3].rcv_key, recs[3].src_key) == ("-120deg", "right")
    for r in recs[:6]:
        k, s = r.locator["idx"]
        x, _ = ad.load(r.locator, tmp_path)
        np.testing.assert_array_equal(x, a[k, :, s, :2].T)
    full = {r.local_key for r in _read({**params, "select": None}, tmp_path)[1]}
    assert len(full) == 48 and {r.local_key for r in recs} <= full


def test_axis_keys_join_the_pattern_key(tmp_path):
    """surrey_bformat and spheres: names per index of one record axis, after the pattern's own value."""
    np.save(tmp_path / "source_A.npy", np.zeros((3, 20)))
    params = {"glob": "*.npy", "container": "npy", "time_axis": 1, "record_axes": [0], "fs": 48000,
              "pattern": r"source_(?P<src>\w)\.npy$", "select": {0: [0, 2]},
              "axis_keys": {0: {"as": "src", "names": ["az000", "az010", "az020"]}}}
    _, recs = _read(params, tmp_path)
    assert [(r.local_key, r.src_key, r.rcv_key) for r in recs] == [("source_A.npy#0", "A_az000", None),
                                                                   ("source_A.npy#2", "A_az020", None)]


@pytest.mark.parametrize("change", [{"axis_keys": {0: {"as": "rcv", "names": ["a", "b"]}}},
                                    {"axis_keys": {1: {"as": "rcv", "names": ["a", "b", "c"]}}},
                                    {"axis_keys": {0: {"as": "room", "names": ["a", "b", "c"]}}},
                                    {"select": {1: [0]}}, {"select": {0: [3]}}])
def test_select_and_axis_keys_are_checked(tmp_path, change):
    """A names list of the wrong length, an axis that is not a record axis, an unknown key or index: an error."""
    np.save(tmp_path / "x.npy", np.zeros((3, 20)))
    with pytest.raises(ValueError):
        _read({"glob": "*.npy", "container": "npy", "time_axis": 1, "record_axes": [0], "fs": 8000, **change},
              tmp_path)


def test_positions_from_a_side_file(tmp_path):
    """hearing_anything_anywhere: receiver positions in bin_xyzs.npy beside the array; the key is the place in cm."""
    d = tmp_path / "classroomBase"
    d.mkdir()
    np.save(d / "bin_RIRs.npy", np.zeros((2, 2, 30)))
    np.save(d / "bin_xyzs.npy", np.array([[1.2, -0.35, 1.5], [0.0, 2.004, 1.5]]))
    params = {"glob": "*/bin_RIRs.npy", "container": "npy", "time_axis": 2, "channel_axis": 1, "record_axes": [0],
              "fs": 48000, "src_default": "Base",
              "positions": {"rcv": {"npy": "bin_xyzs.npy", "key_from_position": True, "key_prefix": "bin_"}}}
    _, recs = _read(params, tmp_path)
    assert [r.rcv_key for r in recs] == ["bin_120_-35_150", "bin_0_200_150"]
    assert recs[0].rcv_pos == (1.2, -0.35, 1.5) and recs[0].src_pos is None and recs[0].src_key == "Base"
    assert recs[0].local_key == "classroomBase/bin_RIRs.npy#0"


def test_positions_from_arrays_in_the_file(tmp_path):
    """aalto_coupled_simulated: rcvPos (3, N) as MATLAB v7.3 stores it, one row per record; srcPos (3, 1), one
    point for every record."""
    import h5py

    rcv = np.array([[0.0, 0.3, 0.6, 0.9], [1.0, 1.0, 1.0, 1.0], [1.5, 1.5, 1.5, 1.5]])
    with h5py.File(tmp_path / "srirs.mat", "w") as h:
        h["d/srirs"] = np.zeros((4, 30, 4))
        h["d/rcvPos"] = rcv
        h["d/srcPos"] = np.array([[2.0], [3.0], [1.2]])
    params = {"glob": "srirs.mat", "container": "h5", "array": "d/srirs", "time_axis": 1, "channel_axis": 0,
              "record_axes": [2], "fs": 32000,
              "positions": {"rcv": {"array": "d/rcvPos"}, "src": {"array": "d/srcPos"}}}
    _, recs = _read(params, tmp_path)
    assert [r.rcv_pos for r in recs] == [tuple(rcv[:, k]) for k in range(4)]
    assert {r.src_pos for r in recs} == {(2.0, 3.0, 1.2)} and [r.rcv_key for r in recs] == ["0", "1", "2", "3"]


def test_positions_from_the_file_name(tmp_path):
    """rir_44k: one IR per file, the positions in its name."""
    name = "rir_omni_mic_0.48_3.2_6.54_source_-1.22_5.2_6.57.npy"
    np.save(tmp_path / name, np.zeros(30))
    params = {"glob": "*.npy", "container": "npy", "time_axis": 0, "record_axes": [], "fs": 16000,
              "pattern": r"(?P<rcv>mic_(?P<rx>[^_]+)_(?P<ry>[^_]+)_(?P<rz>[^_]+))_"
                         r"(?P<src>source_(?P<sx>[^_]+)_(?P<sy>[^_]+)_(?P<sz>[^_]+))\.npy$",
              "positions": {"rcv": {"groups": ["rx", "ry", "rz"]}, "src": {"groups": ["sx", "sy", "sz"]}}}
    _, recs = _read(params, tmp_path)
    r = recs[0]
    assert r.local_key == name and r.rcv_key == "mic_0.48_3.2_6.54" and r.src_key == "source_-1.22_5.2_6.57"
    assert r.rcv_pos == (0.48, 3.2, 6.54) and r.src_pos == (-1.22, 5.2, 6.57)


def test_positions_must_match_the_records(tmp_path):
    np.save(tmp_path / "RIRs.npy", np.zeros((3, 30)))
    np.save(tmp_path / "xyzs.npy", np.zeros((4, 3)))
    with pytest.raises(ValueError, match="positions"):
        _read({"glob": "RIRs.npy", "container": "npy", "time_axis": 1, "record_axes": [0], "fs": 8000,
               "positions": {"rcv": {"npy": "xyzs.npy"}}}, tmp_path)


def test_cache_gives_the_same_samples(tmp_path):
    """ertd: gzip chunks across records. With cache_arrays 2, load reads each array once and slices it in memory;
    the samples are those of a read without the cache, and at most two arrays stay in memory."""
    import h5py

    from rirdb.adapters import ndarray
    from rirdb.deliver import audio_hash

    rng = np.random.default_rng(3)
    for name in ("ertd1.mat", "ertd2.mat", "ertd3.mat"):
        with h5py.File(tmp_path / name, "w") as h:
            h.create_dataset("ertd/rirs", data=rng.standard_normal((6, 40)), chunks=(6, 2), compression="gzip")
    params = {"glob": "ertd*.mat", "container": "h5", "array": "ertd/rirs", "time_axis": 1, "record_axes": [0],
              "fs": 48000, "pattern": r"(?P<cond>ertd\d)\.mat$"}
    plain, recs = _read(params, tmp_path)
    cached, cached_recs = _read({**params, "cache_arrays": 2}, tmp_path)
    assert [r.to_row() for r in cached_recs] == [r.to_row() for r in recs]       # the index does not change
    ndarray._ARRAY_CACHE.clear()
    for r in recs:
        x, fs = cached.load(r.locator, tmp_path)
        want, _ = plain.load(r.locator, tmp_path)
        np.testing.assert_array_equal(x, want)
        assert audio_hash(x) == audio_hash(want) and fs == 48000
        assert len(ndarray._ARRAY_CACHE) <= 2
    assert len(ndarray._ARRAY_CACHE) == 2


def test_v5_variables_are_read_once_per_file(tmp_path):
    """MATLAB v5 files are read whole; the last variable read stays in memory, one at a time."""
    import scipy.io

    from rirdb.adapters import ndarray

    a = np.random.default_rng(4).standard_normal((30, 3))
    scipy.io.savemat(tmp_path / "a.mat", {"h": a})
    scipy.io.savemat(tmp_path / "b.mat", {"h": -a})
    ad, recs = _read({"glob": "*.mat", "container": "mat", "array": "h", "time_axis": 0, "record_axes": [1],
                      "fs": 8000}, tmp_path)
    for r in recs:
        x, _ = ad.load(r.locator, tmp_path)
        sign = 1 if r.local_key.startswith("a.mat") else -1
        np.testing.assert_array_equal(x, sign * a[:, r.locator["idx"][0]][None, :])
        assert len(ndarray._V5_CACHE) == 1
