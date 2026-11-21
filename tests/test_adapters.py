"""Adapters return exactly the right samples, as (channels, time), for any axis layout."""

from __future__ import annotations

from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from rirdb.adapters import get_adapter


def _dataset(params):
    return SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="ndarray", params=params),
                           analysis=SimpleNamespace(reference_role=None))


@pytest.mark.parametrize("layout", ["src_mic_time", "time_src_mic", "src_time_chan"])
@pytest.mark.parametrize("container", ["h5", "npy"])
def test_ndarray_adapter_axes(tmp_path, layout, container):
    rng = np.random.default_rng(0)
    if layout == "src_mic_time":
        a = rng.standard_normal((3, 4, 50)).astype(np.float32)
        params = {"time_axis": 2, "record_axes": [0, 1]}
        expect = lambda i, j: a[i, j][None, :]  # noqa: E731
    elif layout == "time_src_mic":
        a = rng.standard_normal((50, 3, 4)).astype(np.float32)
        params = {"time_axis": 0, "record_axes": [1, 2]}
        expect = lambda i, j: a[:, i, j][None, :]  # noqa: E731
    else:  # one record per source, 4 simultaneous channels, time before channels
        a = rng.standard_normal((3, 50, 4)).astype(np.float32)
        params = {"time_axis": 1, "channel_axis": 2, "record_axes": [0]}
        expect = lambda i, j=None: a[i].T  # noqa: E731
    if container == "h5":
        with h5py.File(tmp_path / "toy.h5", "w") as h:
            h["ir"] = a
        params.update(glob="*.h5", container="h5", array="ir", fs=16000)
    else:
        np.save(tmp_path / "toy.npy", a)
        params.update(glob="*.npy", container="npy", fs=16000)
    ds = _dataset(params)
    ad = get_adapter("ndarray", params)
    recs = list(ad.iter_records(ds, tmp_path))
    n_expected = 12 if layout != "src_time_chan" else 3
    assert len(recs) == n_expected
    assert len({r.local_key for r in recs}) == n_expected
    for r in recs:
        x, fs = ad.load(r.locator, tmp_path)
        assert fs == 16000
        want = expect(*r.locator["idx"])
        assert x.shape == want.shape
        np.testing.assert_allclose(x, want, rtol=0, atol=0)


def test_ndarray_channel_subset_and_source_records(tmp_path):
    """UPV_RIR_DB layout: MATLAB v5 h_ctrl[time, sources, channels]; keep one ear pair, records = sources."""
    import scipy.io

    rng = np.random.default_rng(1)
    a = rng.standard_normal((50, 8, 4))
    d = tmp_path / "RC1" / "SC2"
    d.mkdir(parents=True)
    scipy.io.savemat(d / "DH_RC1_SC2_Z05_RIR.mat", {"h_ctrl": a})
    params = {"glob": "**/DH_*_RIR.mat", "pattern": r"(?P<cond>RC\d)/(?P<src>SC\d)/DH_RC\d_SC\d_(?P<rcv>Z\d+)_RIR\.mat$",
              "container": "mat", "array": "h_ctrl", "time_axis": 0, "channel_axis": 2, "record_axes": [1],
              "record_is": "src", "channels": [2, 3], "roles": ["L", "R"], "fs": 44100}
    ad = get_adapter("ndarray", params)
    recs = list(ad.iter_records(_dataset(params), tmp_path))
    assert len(recs) == 8
    assert [r.src_key for r in recs[:2]] == ["SC2-0", "SC2-1"]
    assert {r.rcv_key for r in recs} == {"Z05"} and {r.condition_key for r in recs} == {"RC1"}
    assert recs[0].channel_roles == ("L", "R")
    for r in recs:
        x, fs = ad.load(r.locator, tmp_path)
        assert fs == 44100
        np.testing.assert_array_equal(x, a[:, r.locator["idx"][0], 2:4].T)


def test_sofa_first_per_source(tmp_path):
    """Repeated measurements (same SourcePosition) collapse to the first one, whatever the layout."""
    ir = np.random.default_rng(2).standard_normal((6, 2, 20))
    sp = np.array([[1, 0, 1.5], [1, 0, 1.5], [-2, 4, 0], [-2, 4, 0], [1, 0, 0], [1, 0, 0]], dtype=float)
    with h5py.File(tmp_path / "pos_1.0X_0.0Y.sofa", "w") as h:
        h["Data.IR"] = ir
        h["Data.SamplingRate"] = np.array([48000.0])
        h["SourcePosition"] = sp
    params = {"glob": "*.sofa", "pattern": r"_(?P<rcv>-?[\d.]+X_-?[\d.]+Y)\.sofa$", "first_per_source": True,
              "capture_format": "sdm_array", "reference_role": "mic_1"}
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="sofa", params=params),
                         analysis=SimpleNamespace(reference_role=None))
    ad = get_adapter("sofa", params)
    recs = list(ad.iter_records(ds, tmp_path))
    assert [r.locator["m"] for r in recs] == [0, 2, 4]
    assert len({r.src_key for r in recs}) == 3 and {r.rcv_key for r in recs} == {"1.0X_0.0Y"}
    x, fs = ad.load(recs[1].locator, tmp_path)
    assert fs == 48000
    np.testing.assert_array_equal(x, ir[2])


def test_sofa_measurements_as_receivers(tmp_path):
    """MRTD layout: one file per loudspeaker, m = receiver position; all-zero SourcePosition = unknown."""
    ir = np.random.default_rng(3).standard_normal((5, 4, 20))
    with h5py.File(tmp_path / "offices_zoom_ls_2.sofa", "w") as h:
        h["Data.IR"] = ir
        h["Data.SamplingRate"] = np.array([48000.0])
        h["SourcePosition"] = np.zeros((5, 3))
        h["ListenerPosition"] = np.arange(15, dtype=float).reshape(5, 3)
    params = {"glob": "*.sofa", "pattern": r"^(?P<room>[a-z-]+)_zoom_(?P<src>ls_\d)\.sofa$", "m_is": "rcv",
              "roles": ["FLU", "FRD", "BLD", "BRU"], "capture_format": "tetra_raw", "reference_role": "mean"}
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="sofa", params=params),
                         analysis=SimpleNamespace(reference_role=None))
    recs = list(get_adapter("sofa", params).iter_records(ds, tmp_path))
    assert len(recs) == 5 and {r.src_key for r in recs} == {"ls_2"}
    assert [r.rcv_key for r in recs] == [f"m{i}" for i in range(5)]
    assert recs[0].src_pos is None and recs[1].rcv_pos == (3.0, 4.0, 5.0)
    assert recs[0].room_key == "offices" and recs[0].channel_roles == ("FLU", "FRD", "BLD", "BRU")


def test_sofa_spherical_source_positions(tmp_path):
    """Surrey and Huddersfield give SourcePosition as azimuth, elevation (degrees) and distance, around the
    listener: the published position is in metres, x y z, so the distance is the radius."""
    with h5py.File(tmp_path / "brir.sofa", "w") as h:
        h["Data.IR"] = np.zeros((3, 2, 20))
        h["Data.SamplingRate"] = np.array([48000.0])
        h["SourcePosition"] = np.array([[0, 0, 1.5], [90, 0, 1.5], [270, 30, 2.0]], dtype=float)
        h["SourcePosition"].attrs["Type"] = b"spherical"
        h["ListenerPosition"] = np.array([[1.0, 1.0, 1.2]])
    params = {"glob": "*.sofa", "capture_format": "binaural", "roles": ["L", "R"]}
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="sofa", params=params),
                         analysis=SimpleNamespace(reference_role=None))
    recs = list(get_adapter("sofa", params).iter_records(ds, tmp_path))
    np.testing.assert_allclose(recs[0].src_pos, (2.5, 1.0, 1.2), atol=1e-9)
    np.testing.assert_allclose(recs[1].src_pos, (1.0, 2.5, 1.2), atol=1e-9)
    dist = np.linalg.norm(np.subtract(recs[2].src_pos, recs[2].rcv_pos))
    assert dist == pytest.approx(2.0) and recs[2].src_pos[2] == pytest.approx(1.2 + 1.0)

def test_ndarray_crop(tmp_path):
    a = np.random.default_rng(4).standard_normal((2, 3, 1000)).astype(np.float32)
    np.save(tmp_path / "deconvolved.npy", a)
    params = {"glob": "*.npy", "container": "npy", "time_axis": 2, "record_axes": [0, 1], "fs": 100, "crop_s": 2.5}
    ad = get_adapter("ndarray", params)
    recs = list(ad.iter_records(_dataset(params), tmp_path))
    assert len(recs) == 6 and {r.n_samples for r in recs} == {250}
    x, fs = ad.load(recs[4].locator, tmp_path)
    np.testing.assert_array_equal(x, a[1, 1, :250][None, :])


def test_wav_tree_room_from_a_folder_and_tokens_from_file_names(tmp_path):
    """The options scripts/onboard/guess.py writes: room_level, condition_level, tokens."""
    import soundfile as sf
    for rel in ("HallA/RIRs/curtains open/S1_R2.wav", "HallA/RIRs/curtains shut/Src2-Mic3.wav",
                "HallB/RIRs/curtains open/KU100 LS 16-2.wav", "HallB/RIRs/curtains open/Room3.wav"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(tmp_path / rel), np.zeros(100), 8000)
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(
        name="wav_tree", params={"room_level": 0, "condition_level": 2, "tokens": True}))
    recs = {r.local_key: r for r in get_adapter("wav_tree").iter_records(ds, tmp_path)}
    got = {k: (r.room_key, r.condition_key, r.src_key, r.rcv_key) for k, r in recs.items()}
    assert got["HallA/RIRs/curtains open/S1_R2.wav"] == ("halla", "curtains open", "S1", "R2")
    assert got["HallA/RIRs/curtains shut/Src2-Mic3.wav"] == ("halla", "curtains shut", "Src2", "Mic3")
    assert got["HallB/RIRs/curtains open/KU100 LS 16-2.wav"][2] == "LS16"
    assert got["HallB/RIRs/curtains open/Room3.wav"][2:] == (None, None)     # "Room3" is not a receiver token


def test_wav_tree_room_from_regex_moves_a_file_published_under_the_wrong_room(tmp_path):
    """sonic_palimpsest swapped the KM184 files of two rooms: room_from_regex wins over the folder."""
    import soundfile as sf
    for rel in ("Cellar/KM184-1.wav", "Cellar/Omni-1.wav", "Billiard/KM184-1.wav"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(tmp_path / rel), np.zeros(100), 8000)
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="wav_tree", params={
        "room_level": 0, "room_from_regex": {"^Cellar/KM184-": "Billiard", "^Billiard/KM184-": "Cellar"}}))
    rooms = {r.local_key: r.room_key for r in get_adapter("wav_tree").iter_records(ds, tmp_path)}
    assert rooms == {"Cellar/KM184-1.wav": "billiard", "Cellar/Omni-1.wav": "cellar", "Billiard/KM184-1.wav": "cellar"}


def test_sofa_axes_from_dimension_names(tmp_path):
    """MultiSpeakerBRIR stores Data.IR as (M, R, E, N): the reader takes the order from the dimension names, not
    the SOFA 1 order (M, R, N, E). Read the other way, every sample became a loudspeaker (Two!Ears, Eurecat)."""
    ir = np.random.default_rng(5).standard_normal((3, 2, 4, 20))          # 3 head orientations, 2 ears, 4 speakers
    with h5py.File(tmp_path / "brir.sofa", "w") as h:
        for name, n in zip("MREN", ir.shape):
            h[name] = np.arange(n, dtype=float)
            h[name].make_scale(name)
        h["Data.IR"] = ir
        for k, name in enumerate("MREN"):
            h["Data.IR"].dims[k].attach_scale(h[name])
        h["Data.SamplingRate"] = np.array([44100.0])
    params = {"glob": "*.sofa", "capture_format": "binaural", "roles": ["L", "R"]}
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="sofa", params=params),
                         analysis=SimpleNamespace(reference_role=None))
    ad = get_adapter("sofa", params)
    recs = list(ad.iter_records(ds, tmp_path))
    assert len(recs) == 12 and {r.n_samples for r in recs} == {20}
    assert {r.src_key for r in recs} == {"e0", "e1", "e2", "e3"} and recs[0].rcv_key == "m0"
    rec = next(r for r in recs if r.locator["m"] == 2 and r.locator["e"] == 3)
    x, _ = ad.load(rec.locator, tmp_path)
    np.testing.assert_array_equal(x, ir[2, :, 3, :])


def test_wav_tree_joins_channel_files_and_reads_keys_in_parts(tmp_path):
    """join: files that differ only in the channel are one IR; src and src_2 make one key; max_s leaves out
    recordings; format_from_regex names the format from the path."""
    import soundfile as sf
    for rel, n in (("Main/ESMA/Main_ESMA_M01_S01_FL.wav", 100), ("Main/ESMA/Main_ESMA_M02_S01_FR.wav", 100),
                   ("Main/Bformat/VR_Bformat_S01_W.wav", 100), ("Main/Bformat/VR_Bformat_S01_X.wav", 100),
                   ("Main/Bformat/VR_Bformat_S01_Y.wav", 100), ("Main/Bformat/VR_Bformat_S01_Z.wav", 100),
                   ("Main/ESMA/sweep_take.wav", 8000 * 70)):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(tmp_path / rel), np.full(n, 0.1 if rel.endswith("FR.wav") else 0.0), 8000)
    params = {"join": r"_(?:M(?P<ch>\d+)_)?S\d+_(?P<ch_2>[A-Za-z]+)\.wav$", "max_s": 60,
              "pattern": r"/(?P<cond>[^/]+)/[A-Za-z]+_[^_]+_(?:M\d+_)?(?P<src>S)(?P<src_2>\d+)_", "room_from": "dataset",
              "format_from_regex": {"Bformat/": "foa_fuma"}, "sh_norm": "FuMa"}
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="wav_tree", params=params))
    ad = get_adapter("wav_tree")
    recs = {r.condition_key: r for r in ad.iter_records(ds, tmp_path)}
    assert set(recs) == {"ESMA", "Bformat"}                         # the 70 s recording is left out
    esma, bf = recs["ESMA"], recs["Bformat"]
    assert esma.channel_roles == ("01_FL", "02_FR") and esma.src_key == "S_01"
    assert bf.channel_roles == ("W", "X", "Y", "Z") and bf.capture_format == "foa_fuma" and bf.sh_norm == "FuMa"
    assert esma.local_key == "Main/ESMA/Main_ESMA_M01_S01_FL.wav|Main/ESMA/Main_ESMA_M02_S01_FR.wav"
    x, fs = ad.load(esma.locator, tmp_path)
    assert x.shape == (2, 100) and fs == 8000 and x[1, 0] == pytest.approx(0.1, abs=1e-3)   # 16-bit WAV


def test_gtu_rir_never_runs_a_pickle_and_reads_samples_at_their_offset(tmp_path):
    """gtu_rir's RIR.pickle.dat is read without unpickling it freely: a pickle that imports anything but the four
    numpy names is refused, and an IR's samples are read at the offset its opcodes give."""
    import os
    import pickle
    from rirdb.adapters.gtu_rir import GtuRirPickleAdapter, read_metadata, sample_offsets
    (tmp_path / "evil.dat").write_bytes(pickle.dumps([os.system]))
    with pytest.raises(pickle.UnpicklingError, match="posix.system|nt.system|is not allowed"):
        read_metadata(tmp_path / "evil.dat")
    ir = np.linspace(-1, 1, 3000, dtype=np.float32)
    (tmp_path / "x.dat").write_bytes(pickle.dumps([b"short", ir.tobytes()], protocol=4))
    [(offset, n, op)] = sample_offsets(tmp_path / "x.dat")               # only the long byte string
    loc = {"relpath": "x.dat", "offset": offset, "opcode": op, "n": n // 4, "dtype": "<f4"}
    x, fs = GtuRirPickleAdapter().load(loc, tmp_path)
    assert fs == 44100 and np.array_equal(x[0], ir.astype(np.float64))


def test_hf_parquet_reads_channel_lists_per_microphone_and_audio_bytes(tmp_path):
    """RIR-Former stores each row's channels as lists of floats (one IR per microphone); Treble10 and CNAM store WAV
    bytes in an HF Audio column, whose file name can key the receiver."""
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq
    import soundfile as sf
    rir = [[[float(c * 10 + k) for k in range(8)] for c in range(3)] for _ in range(2)]   # 2 rows, 3 mics, 8 samples
    (tmp_path / "exp1").mkdir()
    pq.write_table(pa.table({"rir": rir, "room_id": [7, 9]}), tmp_path / "exp1" / "train-00000-of-00001.parquet")
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="hf_parquet", params={
        "glob": "exp1/*.parquet", "audio": "rir", "fs": 8000, "per_channel": True, "room": "room-{room_id}",
        "receiver": "mic{ch:02d}"}))
    a = get_adapter("hf_parquet", ds.adapter.params)
    recs = list(a.iter_records(ds, tmp_path))
    assert len(recs) == 6 and {r.room_key for r in recs} == {"room-7", "room-9"} and recs[4].rcv_key == "mic01"
    x, fs = a.load(recs[4].locator, tmp_path)                               # row 1, microphone 1
    assert fs == 8000 and x.tolist() == [rir[1][1]]
    buf = io.BytesIO()
    sf.write(buf, np.array([[0.5, -0.5]] * 16), 16000, format="WAV", subtype="FLOAT")
    audio = pa.array([{"bytes": buf.getvalue(), "path": "room_00001.00_0797.wav"}],
                     pa.struct([("bytes", pa.binary()), ("path", pa.string())]))
    pq.write_table(pa.table({"audio": audio}), tmp_path / "exp1" / "data-00000.parquet")
    ds.adapter.params = {"glob": "exp1/data-*.parquet", "receiver": "{file_stem}"}
    b = get_adapter("hf_parquet", ds.adapter.params)
    [rec] = list(b.iter_records(ds, tmp_path))
    x, fs = b.load(rec.locator, tmp_path)
    assert rec.rcv_key == "room_00001.00_0797" and rec.channel_roles == ("mic_0", "mic_1") and fs == 16000
    assert x.shape == (2, 16) and x[0, 0] == 0.5


def test_ndarray_paths_take_the_patterns_groups(tmp_path):
    """SpaDeNRIR keeps each room's arrays under a group named after the file: room_000.hdf5 holds
    room_000/rir_data/rirs; "{room}" in the paths reads them with one set of params."""
    rng = np.random.default_rng(3)
    data = {}
    for room in ("room_000", "room_001"):
        rirs = rng.standard_normal((4, 2, 30)).astype(np.float32)        # microphones x sources x time
        with h5py.File(tmp_path / f"{room}.hdf5", "w") as h:
            h[f"{room}/rir_data/rirs"] = rirs
            h[f"{room}/rir_data/mic_coords"] = rng.random((4, 3))
        data[room] = rirs
    params = {"glob": "room_*.hdf5", "pattern": r"^(?P<room>room_\d{3})\.hdf5$", "container": "h5",
              "array": "{room}/rir_data/rirs", "time_axis": 2, "record_axes": [0, 1], "fs": 48000,
              "positions": {"rcv": {"array": "{room}/rir_data/mic_coords", "axis": 0}}}
    a = get_adapter("ndarray", params)
    recs = list(a.iter_records(_dataset(params), tmp_path))
    assert len(recs) == 16 and {r.room_key for r in recs} == {"room-000", "room-001"}
    r = recs[-1]                                                          # room_001, microphone 3, source 1
    x, fs = a.load(r.locator, tmp_path)
    assert np.allclose(x[0], data["room_001"][3, 1]) and r.rcv_pos is not None
