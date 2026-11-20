"""Readers driven by the authors' metadata files: asn_json (asn_database) and rir_mega_meta (rir_mega)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from rirdb.adapters import get_adapter


def records(name, root, params):
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name=name, params=params))
    ad = get_adapter(name, params)
    return ad, list(ad.iter_records(ds, root))


def test_asn_json(tmp_path):
    out = tmp_path / "ASN_Database_3.2_WAV" / "OUT"
    out.mkdir(parents=True)
    paths = {}
    for i, n in enumerate((100, 101, 102, 100)):
        paths[f"mic_{i}"] = f"RIR_1_A0_{88 + i}_OMNI.WAV"
        sf.write(out / paths[f"mic_{i}"], np.full(n, (i + 1) / 10), 16000, subtype="FLOAT")
    pair = {"node_id": 7, "src_id": "A0", "node_position": [1, 2, 1.2], "node_orientation": 0.5,
            "source_position": [3, 4, 1.5], "microphone_positions": [[1, 1, 1, 1], [2, 2, 2, 2], [1.2] * 4],
            "rir_paths": paths}
    (out / "RIR.json").write_text(json.dumps({"living_room": {"pair_1": pair}}))
    ad, recs = records("asn_json", tmp_path, {})
    assert len(recs) == 1
    r = recs[0]
    assert r.n_samples == 100 and r.channel_roles == ("mic_0", "mic_1", "mic_2", "mic_3")
    assert r.rcv_key == "node07" and r.src_key == "A0" and r.room_key == "living-room"
    assert r.src_pos == (3, 4, 1.5) and r.rcv_pos == (1, 2, 1.2)
    assert r.local_key == "|".join(f"ASN_Database_3.2_WAV/OUT/{paths[f'mic_{i}']}" for i in range(4))
    x, fs = ad.load(r.locator, tmp_path)
    assert x.shape == (4, 100) and fs == 16000
    np.testing.assert_allclose(x[:, 0], [0.1, 0.2, 0.3, 0.4], rtol=1e-6)


def test_rir_mega_meta(tmp_path):
    part = tmp_path / "p" / "p"
    (part / "train" / "wavs").mkdir(parents=True)
    rows = ["id,wav,split,seed_room,family,source,microphone,room_size"]
    for i in (1, 2):
        sf.write(part / "train" / "wavs" / f"rir_{i:06d}.wav", np.zeros((40, 8)), 16000)
        rows.append(f'r{i},train/wavs/rir_{i:06d}.wav,train,{1000 + i},living_room,"[1, 2, {i}]","[3, 4, 1]","[5, 6, 3]"')
    sf.write(part / "train" / "wavs" / "rir_000003.wav", np.zeros((40, 8)), 16000)      # not listed: not read
    (part / "metadata.csv").write_text("\n".join(rows) + "\n")
    ad, recs = records("rir_mega_meta", tmp_path, {"parts": {"p/p": "linear8"}})
    assert [r.local_key for r in recs] == ["p/p/train/wavs/rir_000001.wav", "p/p/train/wavs/rir_000002.wav"]
    assert [r.room_key for r in recs] == ["room-1001", "room-1002"]
    assert recs[1].src_pos == (1, 2, 2) and recs[1].rcv_pos == (3, 4, 1) and recs[1].rcv_key == "linear8"
    assert len(recs[0].channel_roles) == 8
    x, fs = ad.load(recs[0].locator, tmp_path)
    assert x.shape == (8, 40) and fs == 16000
