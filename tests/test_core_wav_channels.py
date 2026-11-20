"""wav_channels: a file whose channels are separate receivers gives one record per group of channels (munich_underground)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from rirdb.adapters import get_adapter


def records(root, params):
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="wav_channels", params=params))
    ad = get_adapter("wav_channels", params)
    return ad, list(ad.iter_records(ds, root))


def channels_file(path, n_channels):
    """A file whose channel i is the constant i + 1, so a loaded channel tells which one it is."""
    sf.write(path, np.tile(np.arange(1, n_channels + 1) / 10, (50, 1)), 16000, subtype="FLOAT")


PARAMS = {
    "files": [
        {"pattern": r"^head_(?P<src>S\d)\.wav$",
         "records": [{"channels": [0, 1, 2, 3, 4, 5], "rcv": "R1", "capture_format": "binaural",
                      "roles": ["L", "R", "a", "b", "c", "d"]}]},
        {"pattern": r"^omni_(?P<src>S\d)\.wav$",
         "records": [{"channels": [i], "rcv": f"R{i + 2}"} for i in range(4)]},
    ],
    "src_positions": {"S1": [1, 2, 3]},
    "rcv_positions": {"R1": [0, 0, 1], "R3": [4, 5, 6]},
}


def test_a_file_read_whole_has_an_empty_item(tmp_path):
    channels_file(tmp_path / "head_S1.wav", 6)
    ad, recs = records(tmp_path, PARAMS)
    assert [r.local_key for r in recs] == ["head_S1.wav"]
    assert recs[0].locator == {"relpath": "head_S1.wav", "container": "audio"}
    assert recs[0].capture_format == "binaural" and len(recs[0].channel_roles) == 6
    assert recs[0].src_pos == (1, 2, 3) and recs[0].rcv_pos == (0, 0, 1)
    x, fs = ad.load(recs[0].locator, tmp_path)
    assert x.shape == (6, 50) and fs == 16000


def test_a_file_split_into_channels(tmp_path):
    channels_file(tmp_path / "omni_S1.wav", 4)
    ad, recs = records(tmp_path, PARAMS)
    assert [r.local_key for r in recs] == [f"omni_S1.wav#{i}" for i in range(4)]
    assert [r.rcv_key for r in recs] == ["R2", "R3", "R4", "R5"]
    assert recs[1].rcv_pos == (4, 5, 6) and recs[0].rcv_pos is None
    assert {r.capture_format for r in recs} == {"mono_omni"} and recs[0].channel_roles == ("omni",)
    for i, r in enumerate(recs):
        x, _ = ad.load(r.locator, tmp_path)
        assert x.shape == (1, 50)
        np.testing.assert_allclose(x, (i + 1) / 10, rtol=1e-6)


def test_a_rule_asking_for_a_missing_channel_raises(tmp_path):
    channels_file(tmp_path / "head_S1.wav", 4)
    with pytest.raises(ValueError, match="asks for channel 5"):
        records(tmp_path, PARAMS)
