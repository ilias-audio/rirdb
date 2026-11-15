"""`rirdb get` and the output folder (deliver.py), on the generated dataset's release over local HTTP; and the
rules of formats and of the draw, on small frames."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest
import soundfile as sf
from typer.testing import CliRunner

from rirdb import client
from rirdb.cli import app, parse_size
from rirdb.deliver import ambix, audio_hash
from rirdb.fetch import sha256_of
from rirdb.select import Query, draw, matching, weights, t30_range
from tests.toy import DUPLICATE, TOY_FILES

pytestmark = pytest.mark.xdist_group("toy")


def get(*args: str) -> str:
    out = CliRunner().invoke(app, ["get", *args])
    assert out.exit_code == 0, out.output
    return out.output


def test_get_a_dataset_gives_every_file_as_published(user, tmp_path):
    get("--dataset", "toy", "--out", str(tmp_path / "out"))
    files = client.table("files.toy").set_index("file")["sha256"]
    published = tmp_path / "out" / "files" / "toy"
    assert sorted(p.relative_to(published).as_posix() for p in published.rglob("*.wav")) == sorted([*TOY_FILES, DUPLICATE[0]])
    assert all(sha256_of(published / f) == files[f] for f in files.index)
    irs = pd.read_csv(tmp_path / "out" / "irs.csv")
    assert len(irs) == 7 and irs["path"].str.startswith("files/toy/").all()
    assert "CC0-1.0" in (tmp_path / "out" / "LICENSES.md").read_text()
    bib = (tmp_path / "out" / "references.bib").read_text()
    assert "@misc{toy," in bib and "@misc{rirdb," in bib
    lock = json.loads((tmp_path / "out" / "selection.lock").read_text())
    assert lock["tables"] == "0.9" and len(lock["irs"]) == 7


def test_get_cuts_each_ir_and_checks_its_samples(user, tmp_path):
    get("--format", "stereo", "--out", str(tmp_path / "out"))
    irs = pd.read_csv(tmp_path / "out" / "irs.csv")
    assert irs["path"].tolist() == [f"audio/toy/{irs['ir_id'][0]}.wav"]
    x, fs = sf.read(tmp_path / "out" / irs["path"][0], dtype="float32", always_2d=True)
    assert fs == 48000 and x.shape[1] == 2 and sf.info(tmp_path / "out" / irs["path"][0]).subtype == "FLOAT"
    assert audio_hash(x.T) == irs["audio_hash"][0]                # the samples as published, untouched


def test_a_draw_is_rebuilt_from_its_lock(user, tmp_path):
    get("--n", "3", "--balance", "space", "--seed", "1", "--out", str(tmp_path / "a"))
    first = pd.read_csv(tmp_path / "a" / "irs.csv")
    assert len(first) == 3 and "weight" in first and len(list((tmp_path / "a" / "audio" / "toy").glob("*.wav"))) == 3
    get("--lock", str(tmp_path / "a" / "selection.lock"), "--out", str(tmp_path / "b"))
    a, b = (json.loads((tmp_path / f / "selection.lock").read_text())["irs"] for f in ("a", "b"))
    assert a == b
    wavs = sorted(p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*.wav"))
    assert all((tmp_path / "a" / p).read_bytes() == (tmp_path / "b" / p).read_bytes() for p in wavs)   # byte for byte
    get("--n", "3", "--balance", "space", "--seed", "2", "--out", str(tmp_path / "c"))
    assert json.loads((tmp_path / "c" / "selection.lock").read_text())["irs"] != a


def test_resampling_on_export(user, tmp_path):
    get("--format", "stereo", "--fs", "16000", "--out", str(tmp_path / "out"))
    irs = pd.read_csv(tmp_path / "out" / "irs.csv")
    info = sf.info(tmp_path / "out" / irs["path"][0])
    assert info.samplerate == 16000 and abs(info.duration - irs["duration_s"][0]) < 0.01


def test_ambix_from_fuma_and_n3d():
    x = np.arange(4.0)[:, None] * np.ones((4, 3))               # channels W, X, Y, Z = 0, 1, 2, 3
    fuma = ambix(x, ["W", "X", "Y", "Z"], "FuMa")
    assert fuma[:, 0].tolist() == [0.0, 2.0, 3.0, 1.0]           # ACN order W, Y, Z, X
    w = np.ones((1, 3))
    assert ambix(np.vstack([w, w, w, w]), ["W", "X", "Y", "Z"], "FuMa")[0, 0] == pytest.approx(math.sqrt(2))
    n3d = ambix(np.ones((9, 2)), ["W", *[f"ACN{i}" for i in range(1, 9)]], "N3D")
    assert n3d[:, 0].tolist() == pytest.approx([1, *[1 / math.sqrt(3)] * 3, *[1 / math.sqrt(5)] * 5])
    acn = ambix(x, ["W", "Y", "Z", "X"], "SN3D")
    assert acn.tolist() == x.tolist()


def test_mono_takes_w_only_where_a_space_has_no_mono_row():
    irs = pd.DataFrame({"ir_id": list("abcd"), "space": ["s1", "s1", "s2", "s3"],
                        "format": ["mono", "ambisonic", "ambisonic", "binaural"], "is_primary": True,
                        "origin": "measured", "grade": "A", "dataset": "d"})
    assert matching(irs, Query(format="mono"))["ir_id"].tolist() == ["a", "c"]


def test_the_draw_balances_and_is_repeatable():
    rows = pd.DataFrame({"ir_id": [str(i) for i in range(10)], "space": ["big"] * 8 + ["small"] * 2,
                         "dataset": "d", "rt_mid": [0.5] * 8 + [3.0, np.nan]})
    w = weights(rows, "space")
    assert w.groupby(rows["space"]).sum().tolist() == pytest.approx([0.5, 0.5])
    t30 = weights(rows, "t30")
    assert t30.iloc[9] == 0 and t30.iloc[8] == pytest.approx(0.5)
    q = Query(n=4, balance="space", seed=7)
    assert draw(rows, q)["ir_id"].tolist() == draw(rows, q)["ir_id"].tolist()
    assert len(draw(rows, Query(balance="t30"))) == 10             # without --n, nothing is left out
    assert len(draw(rows, Query(balance="t30", n=9))) == 9         # no rt_mid: never drawn
    long = rows.assign(rt_mid=[0.5] * 8 + [30.0, 3.0])               # 30 s counts in the last bin
    assert weights(long, "t30").iloc[8] == pytest.approx(1 / 3)                   # three bins, one third each


def test_sizes_are_read_as_powers_of_1000():
    assert parse_size("20GB") == 20e9 and parse_size("500 mb") == 500e6 and parse_size("1.5TB") == 1.5e12


def test_a_lock_of_a_whole_dataset_gives_the_files_again(user, tmp_path):
    get("--dataset", "toy", "--out", str(tmp_path / "a"))
    get("--lock", str(tmp_path / "a" / "selection.lock"), "--out", str(tmp_path / "b"))
    assert (tmp_path / "b" / "files" / "toy" / "hall" / "s1_m1.wav").exists() and not (tmp_path / "b" / "audio").exists()


def test_the_output_folder_is_complete_or_refused(user, tmp_path):
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "notes.txt").write_text("not rirdb's")
    out = CliRunner().invoke(app, ["get", "--format", "stereo", "--out", str(mine)])
    assert out.exit_code != 0 and (mine / "notes.txt").exists()                   # never written into
    get("--format", "stereo", "--out", str(tmp_path / "out"))
    get("--n", "2", "--out", str(tmp_path / "out"))                                 # an earlier output: replaced
    assert len(pd.read_csv(tmp_path / "out" / "irs.csv")) == 2 and not (tmp_path / "out.partial").exists()


def test_irs_csv_describes_the_files_written(user, tmp_path):
    get("--format", "stereo", "--fs", "16000", "--out", str(tmp_path / "out"))
    irs = pd.read_csv(tmp_path / "out" / "irs.csv")
    assert irs["fs"].tolist() == [16000] and irs["channels"].tolist() == [2] and irs["format"].tolist() == ["stereo"]


def test_bibtex_text_is_escaped():
    from rirdb.deliver import latex, url_text

    assert latex("Room & Hall_1 {x}") == r"Room \& Hall\_1 \{x\}"
    assert url_text("https://a.org/x%20y#z") == r"https://a.org/x\%20y\#z"


def test_space_type_t30_and_provides_filters():
    irs = pd.DataFrame({"ir_id": list("abcd"), "space": ["s1", "s2", "s3", "s4"], "format": "mono", "is_primary": True,
                        "origin": "measured", "grade": "A", "dataset": ["d1", "d1", "d2", "d2"],
                        "space_type": ["concert_hall", "worship", "domestic", "concert_hall"],
                        "rt_mid": [1.8, 2.6, 0.4, np.nan]})
    datasets = pd.DataFrame({"dataset": ["d1", "d2"], "provides_positions": ["yes", "partial"],
                             "provides_geometry": ["no", "yes"]})
    ids = lambda **kw: matching(irs, Query(**kw), datasets)["ir_id"].tolist()
    assert ids(space_type=["concert_hall", "worship"]) == ["a", "b", "d"]
    assert ids(t30="1.25-2.5") == ["a"] and ids(t30="2.5-") == ["b"]          # no rt_mid: never kept
    assert ids(provides=["positions"]) == ["a", "b", "c", "d"]                  # yes or partly
    assert ids(provides=["positions", "geometry"]) == ["c", "d"]                # all of them
    assert t30_range("0-0.315") == (0.0, 0.315)
    for bad in ("1.2", "2-1", "a-b"):
        with pytest.raises(ValueError, match="not a range"):
            t30_range(bad)


def test_kind_of_space():
    irs = pd.DataFrame({"ir_id": list("abcdef"), "space": list("abcdef"), "format": "mono", "is_primary": True,
                        "grade": "A", "dataset": "d",
                        "space_type": ["concert_hall", "outdoor", "anechoic", "virtual", "device", "underground"],
                        "origin": ["measured", "measured", "measured", "measured", "device", "measured"]})
    assert matching(irs, Query(kind=["indoor"]))["ir_id"].tolist() == ["a", "f"]        # caves, tunnels: indoors
    assert matching(irs, Query(kind=["outdoor", "anechoic"]))["ir_id"].tolist() == ["b", "c"]
    assert matching(irs, Query(kind=["simulated", "device"]))["ir_id"].tolist() == ["d", "e"]


def test_the_website_and_the_command_line_sort_spaces_the_same_way():
    from rirdb.select import kind_of
    from rirdb.site.words import ROOM_TYPES, kind_of as one
    pairs = [(t, o) for t in ROOM_TYPES for o in ("measured", "simulated", "device")]
    got = kind_of(pd.Series([t for t, _ in pairs]), pd.Series([o for _, o in pairs])).tolist()
    assert got == [one(t, o) for t, o in pairs]
