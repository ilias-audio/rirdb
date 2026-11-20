"""The ita_audio_mat reader: ITA-Toolbox itaAudio objects in a MATLAB v7.3 file, built here with h5py."""

from __future__ import annotations

from types import SimpleNamespace

import h5py
import numpy as np
import pytest
import scipy.io

from rirdb.adapters import get_adapter

COMPLEX = np.dtype([("real", "<f8"), ("imag", "<f8")])         # how MATLAB stores complex numbers in HDF5


def _chars(text: str) -> np.ndarray:
    return np.array([[ord(c)] for c in text], dtype=np.uint16)


def _ita_file(path, structs):
    """A v7.3 file as ITA saves it: result.ir.ir_<k> and, in #refs#, one itaAudio struct per loudspeaker,
    reached through the object references of #subsystem#/MCOS. structs: {k: (domain, data, dataFactor)}."""
    with h5py.File(path, "w") as h:
        refs = []
        for k, (domain, data, factor) in structs.items():
            s = h.create_group(f"#refs#/s{k}")
            if np.iscomplexobj(data):
                c = np.empty(data.shape, COMPLEX)
                c["real"], c["imag"] = data.real, data.imag
                data = c
            s["data"] = data
            s["domain"] = _chars(domain)
            s["samplingRate"] = np.array([[48000.0]])
            s["comment"] = _chars(f"Impulse Response: {k} -> L R")
            s["dataFactor"] = np.array([[factor]])
            h.create_group(f"result/ir/ir_{k}")
            refs.append(s.ref)
        mcos = h.create_dataset("#subsystem#/MCOS", (1, len(refs) + 1), dtype=h5py.ref_dtype)
        mcos[0, 0] = h["result"].ref                           # not an itaAudio struct: skipped
        for i, r in enumerate(refs):
            mcos[0, i + 1] = r
        h["result/headtrackingMessage"] = _chars("tracker: 0.1 0.2 1.2 5 0 0")


def _read(params, root):
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="ita_audio_mat", params=params),
                         analysis=SimpleNamespace(reference_role=None))
    ad = get_adapter("ita_audio_mat", params)
    return ad, list(ad.iter_records(ds, root))


def test_ita_audio_mat(tmp_path):
    rng = np.random.default_rng(0)
    time = rng.standard_normal((2, 64))
    spectrum = rng.standard_normal((2, 33)) + 1j * rng.standard_normal((2, 33))
    name = "circ_1p5_090_x$y_KU100.mat"
    _ita_file(tmp_path / name, {1: ("time", time, 2.0), 2: ("freq", spectrum, 1.0)})
    scipy.io.savemat(tmp_path / "speakerPositions.mat",
                     {"loudspeaker": {"ls_1": {"Pos": [1.0, 2.0, 3.0]}, "ls_2": {"Pos": [-1.0, 0.0, 1.5]}}})
    params = {"room_default": "lab", "groups": [{"glob": "*_KU100.mat", "field": "ir", "capture_format": "binaural",
                                                 "roles": ["L", "R"], "device": "KU 100"}]}
    ad, recs = _read(params, tmp_path)
    assert [r.local_key for r in recs] == [f"{name}#ls1", f"{name}#ls2"]
    assert [(r.src_key, r.rcv_key, r.n_samples) for r in recs] == [("ls01", "circ_1p5_090", 64),
                                                                   ("ls02", "circ_1p5_090", 64)]
    assert recs[0].src_pos == (1.0, 2.0, 3.0) and recs[0].rcv_pos == (0.1, 0.2, 1.2)
    x, fs = ad.load(recs[0].locator, tmp_path)
    np.testing.assert_array_equal(x, 2.0 * time)                    # dataFactor scales the samples
    assert fs == 48000
    x, _ = ad.load(recs[1].locator, tmp_path)
    np.testing.assert_allclose(x, np.fft.irfft(spectrum, n=64, axis=1), rtol=0, atol=1e-12)
    with pytest.raises(ValueError, match="roles"):
        _read({**params, "groups": [{**params["groups"][0], "roles": ["W", "X", "Y"]}]}, tmp_path)
