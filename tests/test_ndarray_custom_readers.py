"""Three readers for one dataset each (adapters/custom.py): anu_md_omp, otimp_mat and npz_scene."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import scipy.io

from rirdb.adapters import get_adapter
from rirdb.adapters.custom import AnuMdOmpAdapter


def _read(name, params, root, ir_kinds=("room",)):
    ds = SimpleNamespace(id="toy", ir_kinds=list(ir_kinds), adapter=SimpleNamespace(name=name, params=params),
                         analysis=SimpleNamespace(reference_role=None))
    ad = get_adapter(name, params)
    return ad, list(ad.iter_records(ds, root))


def test_anu_md_omp(tmp_path):
    """28 microphones x 4 sources per file; the sources are drawn per file, so the source key names the file."""
    rng = np.random.default_rng(0)
    rirs = {f"rir_s{s}": rng.standard_normal((28, 100)) for s in range(1, 5)}
    (tmp_path / "rir").mkdir()
    scipy.io.savemat(tmp_path / "rir/rir_rt03_007.mat", {**rirs, "source": rng.uniform(0, 3, (4, 3))})
    ad, recs = _read("anu_md_omp", {"n_samples": 100}, tmp_path, ir_kinds=("virtual",))
    assert len(recs) == 112 and len({r.local_key for r in recs}) == 112
    r = next(r for r in recs if r.local_key == "rir/rir_rt03_007.mat#rir_s2#5")
    assert (r.src_key, r.rcv_key, r.condition_key, r.ir_kind) == ("rt03-trial007-s2", "mic06", "rt60-0.3s", "virtual")
    assert r.rcv_pos == tuple(AnuMdOmpAdapter.MICS[5])
    x, fs = ad.load(r.locator, tmp_path)
    np.testing.assert_array_equal(x, rirs["rir_s2"][5][None, :])
    assert fs == 16000


def _otimp_subject(folder, n_mics=6):
    """subject_01.mat: three chair rotations of (samples, microphones, 3 loudspeakers), and metadata.csv."""
    rng = np.random.default_rng(1)
    rirs = {v: rng.standard_normal((40, n_mics, 3)) for v in ("ha_rir_0", "ha_rir_n15", "ha_rir_n30")}
    unit = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])           # (xyz, loudspeaker)
    scipy.io.savemat(folder / "subject_01.mat", {**rirs, "fs": 44100.0, "doa_az_deg": [0.0, 90.0, 0.0],
                                                  "doa_el_deg": [0.0, 0.0, 90.0], "doa_unit_vec": unit})
    (folder / "metadata.csv").write_text("subj_id,gender,in_ear_valid\n1,x,1\n")
    return rirs


def test_otimp_mat(tmp_path):
    (tmp_path / "mat").mkdir()
    rirs = _otimp_subject(tmp_path / "mat")
    ad, recs = _read("otimp_mat", {"glob": "mat/subject_*.mat"}, tmp_path)
    assert len(recs) == 9
    r = next(r for r in recs if r.local_key == "mat/subject_01.mat#ha_rir_n15#2")
    assert (r.src_key, r.rcv_key, r.src_pos) == ("ls03_az0_el90", "subject01_yaw-15", (0.0, 0.0, 1.6))
    assert recs[1].src_pos == (0.0, 1.9, 0.0) and r.extra["subject_kind"] == "mannequin"
    x, fs = ad.load(r.locator, tmp_path)
    np.testing.assert_array_equal(x, rirs["ha_rir_n15"][:, :, 2].T)
    assert fs == 44100


def test_otimp_mat_refuses_another_microphone_count(tmp_path):
    (tmp_path / "mat").mkdir()
    _otimp_subject(tmp_path / "mat", n_mics=4)
    with pytest.raises(ValueError, match="microphones"):
        _read("otimp_mat", {"glob": "mat/subject_*.mat"}, tmp_path)


def test_npz_scene(tmp_path):
    """One RIR per file, so the item is empty; the room's size and the distance go into the label and extra."""
    h = np.random.default_rng(2).standard_normal(64).astype(np.float32)
    np.savez(tmp_path / "sample_00001_rir.npz", h_full=h, h_direct=h * 0, room_dim=np.array([4.0, 5.0, 3.0]),
             distance_m=np.array(1.5), absorption_coeff=np.array(0.25))
    ad, recs = _read("npz_scene", {"glob": "*.npz"}, tmp_path)
    r = recs[0]
    assert r.local_key == "sample_00001_rir.npz" and r.room_key == "sample-00001"
    assert r.room_label == "simulated shoebox room 4.00 x 5.00 x 3.00 m, absorption 0.250 (scene 00001)"
    assert r.extra == {"dims_m": [4.0, 5.0, 3.0], "distance_m": 1.5, "absorption_coeff": 0.25, "volume_m3": 60.0}
    x, fs = ad.load(r.locator, tmp_path)
    np.testing.assert_array_equal(x, h.astype(np.float64)[None, :])
    assert fs == 16000
