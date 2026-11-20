"""The readers that compute their samples (adapters/computed.py): h5_freqresp, tf_mat and stft_subband."""

from __future__ import annotations

from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from rirdb.adapters import get_adapter
from rirdb.adapters.base import ir_hash


def reader_and_records(tmp_path, name, params):
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name=name, params=params),
                         analysis=SimpleNamespace(reference_role=None))
    reader = get_adapter(name, params)
    return reader, list(reader.iter_records(ds, tmp_path))


def test_h5_freqresp_inverts_rfft(tmp_path):
    """Three known IRs of 64 samples stored as rfft bins (0 to 1000 Hz in steps of 31.25 Hz), positions in mm."""
    irs = np.random.default_rng(0).standard_normal((3, 64))
    with h5py.File(tmp_path / "classroom_frequency_responses.h5", "w") as h:
        h["aperture_z1866mm/response"] = np.fft.rfft(irs, axis=1).T              # (33 bins, 3 positions)
        h["aperture_z1866mm/frequency"] = np.arange(33) * 31.25
        h["aperture_z1866mm/xyz"] = np.array([[1000.0, 0, 0], [0, 2000, 0], [0, 0, 500]])
    params = {"glob": "*.h5", "pattern": r"(?P<room>classroom)_frequency_responses\.h5$",
              "sets": r"aperture_(?P<set>z\d+mm)/response", "freq_axis": 0, "frequency": "frequency",
              "positions": "xyz", "pos_scale": 0.001}
    reader, recs = reader_and_records(tmp_path, "h5_freqresp", params)
    assert [r.rcv_key for r in recs] == ["z1866mm-0", "z1866mm-1", "z1866mm-2"]
    assert {(r.fs, r.n_samples) for r in recs} == {(2000, 64)} and recs[1].rcv_pos == (0.0, 2.0, 0.0)
    for k, r in enumerate(recs):
        x, fs = reader.load(r.locator, tmp_path)
        assert fs == 2000 and x.shape == (1, 64)
        np.testing.assert_allclose(x[0], irs[k], atol=1e-12)
    h0 = ir_hash(reader, recs[0].locator, tmp_path, x)                       # the bins' hash, not the samples'
    assert h0 == ir_hash(reader, recs[0].locator, tmp_path, x + 1e-9) != ir_hash(reader, recs[1].locator, tmp_path, x)
    with h5py.File(tmp_path / "classroom_frequency_responses.h5", "a") as h:
        h["aperture_z1866mm/frequency"][...] = np.arange(1, 34) * 31.25
    with pytest.raises(ValueError, match="start at 0 Hz"):
        reader_and_records(tmp_path, "h5_freqresp", params)


def write_tf_mat(folder, f, n_positions=4):
    """TF_data.mat with txy, a 1 x 2 cell of (bins, 2) transfer functions at the frequencies f, and the positions."""
    import scipy.io

    rng = np.random.default_rng(1)
    txy = np.empty((1, 2), dtype=object)
    for g in range(2):
        txy[0, g] = rng.standard_normal((f.size, 2)) + 1j * rng.standard_normal((f.size, 2))
    folder.mkdir()
    scipy.io.savemat(folder / "TF_data.mat", {"txy": txy, "f": f[None, :]})
    xyz = np.arange(3.0 * n_positions).reshape(3, n_positions)
    scipy.io.savemat(folder / "position_132mic.mat", {"x": xyz[0], "y": xyz[1], "z": xyz[2]})
    return txy


def test_tf_mat_recipe_and_checks(tmp_path):
    f_res = 2.0
    f = f_res * np.arange(1, 17)                                                 # 2, 4, ..., 32 Hz
    txy = write_tf_mat(tmp_path / "ok", f)
    params = {"file": "TF_data.mat", "positions": "position_132mic.mat", "src_pos": [0.64, 4.21, 0.11],
              "room_default": "rectangular-room", "low_cut_hz": 5}
    reader, recs = reader_and_records(tmp_path / "ok", "tf_mat", params)
    assert [r.local_key for r in recs] == ["TF_data.mat#1,1", "TF_data.mat#1,2", "TF_data.mat#2,1", "TF_data.mat#2,2"]
    assert [r.rcv_key for r in recs] == ["mic1", "mic2", "mic3", "mic4"] and recs[2].rcv_pos == (2.0, 6.0, 10.0)
    assert {(r.fs, r.n_samples) for r in recs} == {(64, 32)}
    h = txy[0, 1][:, 0].copy()
    h[:2] = 0                                                                    # the bins up to 5 Hz: 2 and 4 Hz
    want = np.fft.irfft(np.concatenate([[0], h[:-1] / 2, h[-1:]]), n=32)
    x, fs = reader.load(recs[2].locator, tmp_path / "ok")
    assert fs == 64
    np.testing.assert_allclose(x[0], want, atol=1e-12)
    write_tf_mat(tmp_path / "from0", f - f_res)                                  # starts at 0 Hz
    with pytest.raises(ValueError, match="f_res, 2 f_res"):
        reader_and_records(tmp_path / "from0", "tf_mat", params)
    write_tf_mat(tmp_path / "positions", f, n_positions=3)
    with pytest.raises(ValueError, match="3 positions for 4 microphones"):
        reader_and_records(tmp_path / "positions", "tf_mat", params)


def test_stft_subband_identity_filter(tmp_path):
    """Filters that pass every bin unchanged (taps [1, 0, 0, 0]) give back an impulse at lag 0."""
    import scipy.io

    x = np.zeros((2, 2, 17, 3, 4, 2), dtype=complex)            # distance, elevation, bin, azimuth, tap, capsule
    x[:, :, :, :, 0, :] = 1
    x[1, 1, :, 2] = 0                                                            # an empty slot
    scipy.io.savemat(tmp_path / "Tietotalo_RIR.mat", {"rir_DB": x})
    params = {"fs": 48000, "room": "corridor"}
    reader, recs = reader_and_records(tmp_path, "stft_subband", params)
    assert len(recs) == 2 * 2 * 3 - 1 and "Tietotalo_RIR.mat#1,1,2" not in {r.local_key for r in recs}
    assert {r.n_samples for r in recs} == {(4 + 1) * 16} and recs[0].channel_roles == ("c1", "c2")
    r = next(r for r in recs if r.local_key == "Tietotalo_RIR.mat#1,1,1")      # 2 m, -30 degrees, -170 degrees
    el, az = np.radians(-30), np.radians(-170)
    np.testing.assert_allclose(r.src_pos, (2 * np.cos(el) * np.cos(az), 2 * np.cos(el) * np.sin(az), 2 * np.sin(el)),
                               atol=1e-4)
    assert r.src_key == "r2m_el-30_az-170"
    y, fs = reader.load(r.locator, tmp_path)
    assert fs == 48000 and y.shape == (2, 80)
    assert (np.abs(y).argmax(axis=1) == 0).all() and np.allclose(y[:, 0], 1, atol=0.05)
    h = ir_hash(reader, r.locator, tmp_path, y)
    assert h == ir_hash(reader, recs[0].locator, tmp_path, y)                 # the same filters: the same hash
    other_hop = get_adapter("stft_subband", {**params, "hop": 8})
    assert ir_hash(other_hop, r.locator, tmp_path, y) != h                   # another setting: another hash


def test_adapters_registered():
    from rirdb.adapters import ADAPTERS

    assert {"h5_sets", "h5_freqresp", "tf_mat", "stft_subband"} <= set(ADAPTERS)
