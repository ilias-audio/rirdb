"""wav_tree: plain records stay exactly as they were (their keys and locators are published), plus its optional
parameters `segments` and `layout_from_regex`."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from rirdb.adapters import get_adapter


def records(root, params):
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="wav_tree", params=params))
    ad = get_adapter("wav_tree", params)
    return ad, list(ad.iter_records(ds, root))


def test_wav_tree_plain_records_are_unchanged(tmp_path):
    """No `segments`, no `layout_from_regex`: the key is the file and the locator has nothing more (the 92 active
    wav_tree datasets depend on it: audio_key hashes the locator)."""
    sf.write(tmp_path / "one.wav", np.zeros((100, 2)), 16000)
    sf.write(tmp_path / "pair-L.wav", np.zeros(100), 16000)
    sf.write(tmp_path / "pair-R.wav", np.zeros(100), 16000)
    _, recs = records(tmp_path, {"glob": "one.wav"})
    assert [r.local_key for r in recs] == ["one.wav"]
    assert recs[0].locator == {"relpath": "one.wav", "container": "audio"}
    assert recs[0].channel_roles == ("SL", "SR") and recs[0].capture_format == "stereo"
    _, recs = records(tmp_path, {"glob": "pair-*.wav", "join": r"-(?P<ch>[LR])\.wav$"})
    assert [r.local_key for r in recs] == ["pair-L.wav|pair-R.wav"]
    assert recs[0].locator == {"files": ["pair-L.wav", "pair-R.wav"], "container": "audio_pair"}
    assert recs[0].channel_roles == ("L", "R")


def test_wav_tree_segments(tmp_path):
    """A 2-channel file of two IRs one after the other (farina_car_irs): keys f#0 and f#1, half the samples each."""
    x = np.stack([np.arange(100), 1000 + np.arange(100)], axis=1) / 2000
    sf.write(tmp_path / "car-2x2.wav", x, 16000, subtype="FLOAT")
    params = {"glob": "*.wav", "segments": 2, "segment_names": ["L", "R"], "roles": {2: ["L", "R"]},
              "channel_formats": {2: "binaural"}}
    ad, recs = records(tmp_path, params)
    assert [r.local_key for r in recs] == ["car-2x2.wav#0", "car-2x2.wav#1"]
    assert [r.src_key for r in recs] == ["L", "R"]
    assert {r.n_samples for r in recs} == {50} and {r.capture_format for r in recs} == {"binaural"}
    assert recs[1].locator == {"relpath": "car-2x2.wav", "container": "audio", "segment": 1, "segments": 2}
    for i, r in enumerate(recs):
        y, fs = ad.load(r.locator, tmp_path)
        np.testing.assert_allclose(y, x[i * 50:(i + 1) * 50].T, rtol=1e-6)


LAYOUTS = {
    r"[-_](?:WX|YZ)[-_.]": {4: {"roles": ["W", "X", "Y", "Z"], "format": "foa_fuma", "sh_norm": "FuMa"}},
    r"[-_](?:WY|XZ)[-_.]": {4: {"roles": ["W", "Y", "X", "Z"], "format": "foa_fuma"},
                            2: {"roles": ["W", "Y"], "format": "array_raw", "orientation_known": False}},
}


def test_wav_tree_layout_from_regex(tmp_path):
    """B-format in two stereo files, in two channel orders, and a lone W Y file (farina_collection)."""
    for name in ("x-WX.wav", "x-YZ.wav", "y-WY.wav", "y-XZ.wav", "z-WY.wav"):
        sf.write(tmp_path / name, np.zeros((20, 2)), 16000)
    params = {"glob": "*.wav", "join": r"[xy]-(?P<ch>WX|YZ|WY|XZ)\.wav$", "layout_from_regex": LAYOUTS}
    _, recs = records(tmp_path, params)
    got = {r.local_key: (r.channel_roles, r.capture_format, r.sh_norm, r.orientation_known) for r in recs}
    assert got == {"x-WX.wav|x-YZ.wav": (("W", "X", "Y", "Z"), "foa_fuma", "FuMa", True),
                   "y-WY.wav|y-XZ.wav": (("W", "Y", "X", "Z"), "foa_fuma", None, True),
                   "z-WY.wav": (("W", "Y"), "array_raw", None, False)}


def test_wav_tree_layout_without_its_own_sh_norm_keeps_the_dataset_one(tmp_path):
    sf.write(tmp_path / "a-WY.wav", np.zeros((20, 4)), 16000)
    _, recs = records(tmp_path, {"glob": "*.wav", "sh_norm": "SN3D", "layout_from_regex": LAYOUTS})
    assert recs[0].sh_norm == "SN3D" and recs[0].channel_roles == ("W", "Y", "X", "Z")


def test_wav_tree_layout_roles_of_the_wrong_length_raise(tmp_path):
    sf.write(tmp_path / "a-WY.wav", np.zeros((20, 4)), 16000)
    bad = {r"WY": {4: {"roles": ["W", "Y"]}}}
    with pytest.raises(ValueError, match="4 channels"):
        records(tmp_path, {"glob": "*.wav", "layout_from_regex": bad})
