"""Readers of one dataset each: stonehenge_octave, tugraz_rir_bin and iem_digital_twins."""

from __future__ import annotations

import json
import struct
from types import SimpleNamespace

import h5py
import numpy as np
import pytest
import soundfile as sf

from rirdb.adapters import get_adapter

BANDS = (125, 250, 500, 1000, 2000, 4000)


def records(name, root, params):
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name=name, params=params))
    ad = get_adapter(name, params)
    return ad, list(ad.iter_records(ds, root))


def stonehenge_folder(folder, bands=BANDS):
    folder.mkdir(parents=True)
    rng = np.random.default_rng(0)
    x = {b: rng.uniform(-1, 1, 64) for b in BANDS}
    for b in BANDS:
        sf.write(folder / f"IR_{b}.wav", x[b], 16000, subtype="DOUBLE")
    gains = {b: (10.0 + i, 0.5 + i / 10) for i, b in enumerate(BANDS)}         # (gain dB, peak)
    (folder / "gains.txt").write_text("".join(f"band gain peak\n{b} {gains[b][0]} {gains[b][1]}\n" for b in bands))
    return x, gains


def test_stonehenge_octave(tmp_path):
    x, gains = stonehenge_folder(tmp_path / "2200BC model" / "s1-m1")
    ad, recs = records("stonehenge_octave", tmp_path, {})
    assert len(recs) == 1
    r = recs[0]
    assert r.channel_roles == tuple(f"oct{b}" for b in BANDS)
    assert (r.condition_key, r.src_key, r.rcv_key) == ("2200BC model", "s1", "m1")
    (tmp_path / "2200BC model" / "s1-m1" / "gains.txt").unlink()        # `get` fetches the WAVs only
    y, fs = ad.load(json.loads(json.dumps(r.locator)), tmp_path)        # as the index stores the locator
    want = np.vstack([x[b] * gains[b][1] / 10 ** (gains[b][0] / 20) for b in BANDS])
    assert fs == 16000
    np.testing.assert_allclose(y, want, rtol=1e-12)


def test_stonehenge_a_band_without_gain_raises(tmp_path):
    stonehenge_folder(tmp_path / "g" / "s1-m1", bands=BANDS[:-1])
    with pytest.raises(KeyError, match="4000"):
        records("stonehenge_octave", tmp_path, {})


def tugraz_files(root, wrong_length=False):
    """data/classroom: an IDX1 index of two entries and a data001 holding two (16, 50) float32 arrays."""
    folder = root / "data" / "classroom"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps(
        {"microphones": [{"id": 1, "position": {"x": 1.0, "y": 2.0, "z": 1.2}, "radius": 0.1, "rotation": 0.0}]}))
    rng = np.random.default_rng(1)
    arrays = [rng.standard_normal((16, 50)).astype(np.float32) for _ in range(2)]
    data, entries = b"", []
    for i, a in enumerate(arrays):
        block = struct.pack("<BB2I", ord("f"), 2, 16, 50) + a.tobytes()
        length = len(block) + (4 if wrong_length else 0)
        words = np.zeros(9, dtype="<u4")
        words[0] = 1000 * i
        words[2:5] = np.array([500.0 * (i + 1), 250.0, np.pi / 2 * i], dtype="<f4").view("<u4")
        words[5:9] = [1 << 16 | i, 1, len(data), length]
        entries.append(words.tobytes())
        data += block
    (folder / "data001").write_bytes(data)
    (folder / "index").write_bytes(b"IDX1" + struct.pack("<I", 2) + b"".join(entries))
    return arrays


def test_tugraz_rir_bin(tmp_path):
    arrays = tugraz_files(tmp_path)
    ad, recs = records("tugraz_rir_bin", tmp_path, {"rooms": ["classroom"]})
    assert [r.local_key for r in recs] == ["data/classroom/data001#1,0.5000,0.2500,0",
                                           "data/classroom/data001#1,1.0000,0.2500,90"]
    assert recs[1].src_pos == (1.0, 0.25, 1.21) and recs[1].rcv_pos == (1.0, 2.0, 1.2)
    for r, a in zip(recs, arrays):
        x, fs = ad.load(r.locator, tmp_path)
        assert fs == 48000 and x.shape == (16, 50) == (len(r.channel_roles), r.n_samples)
        np.testing.assert_array_equal(x, a.astype(np.float64))


def test_tugraz_a_wrong_byte_length_raises(tmp_path):
    tugraz_files(tmp_path, wrong_length=True)
    with pytest.raises(ValueError, match="do not fill"):
        records("tugraz_rir_bin", tmp_path, {"rooms": ["classroom"]})


def sofa(path, ir, delay=0.0):
    with h5py.File(path, "w") as h:
        h["Data.IR"] = ir
        h["Data.SamplingRate"] = np.array([48000.0])
        h["Data.Delay"] = np.full((1, ir.shape[1]), delay)


def iem_files(root, diffuse_shape=(2, 4, 30)):
    dom = root / "Dom" / "IR_denoised" / "ImpulseResponses_001"
    dom.mkdir(parents=True)
    sf.write(dom / "hS1_M1_Sweep2entzerrt.wav", np.zeros((20, 3)), 48000)
    studio = root / "Experimentalstudio"
    studio.mkdir()
    rng = np.random.default_rng(2)
    direct, diffuse = rng.standard_normal((2, 4, 30)), rng.standard_normal(diffuse_shape)
    sofa(studio / "EM_Line_1_Direct_Left.sofa", direct)
    sofa(studio / "EM_Line_1_Diffuse_Left.sofa", diffuse)
    return direct, diffuse


def test_iem_digital_twins(tmp_path):
    direct, diffuse = iem_files(tmp_path)
    ad, recs = records("iem_digital_twins", tmp_path, {})
    assert [r.room_key for r in recs] == ["dom", "experimentalstudio", "experimentalstudio"]
    assert recs[0].src_key == "S1" and recs[0].rcv_key == "M1" and recs[0].extra["preferred"]
    for k, r in enumerate(recs[1:]):
        assert r.local_key.endswith(f"_Diffuse_Left.sofa#{k}") and r.rcv_key == f"line1_p{k + 1:02d}"
        x, fs = ad.load(r.locator, tmp_path)
        assert fs == 48000
        np.testing.assert_allclose(x, direct[k] + diffuse[k])


def test_iem_a_diffuse_part_of_another_shape_raises(tmp_path):
    iem_files(tmp_path, diffuse_shape=(2, 4, 31))
    with pytest.raises(ValueError, match="cannot be added"):
        records("iem_digital_twins", tmp_path, {})
