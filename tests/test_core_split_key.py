"""split_key takes the file from the reader's locator, so a "#" in a file name is kept (sonic_palimpsest)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from rirdb.adapters.base import make_ir_id
from rirdb.index import file_of, index_table, ir_id, split_key


@pytest.mark.parametrize("key, locator, item", [
    ("03a-#3Mezz.wav", {"relpath": "03a-#3Mezz.wav"}, ""),
    ("x#1.h5#3", {"relpath": "x#1.h5"}, "3"),
    ("a.wav|b.wav#0", {"files": ["a.wav", "b.wav"]}, "0"),
    ("a.wav|b.wav", {"files": ["a.wav", "b.wav"]}, ""),
    ("f.sofa#3#1", {"relpath": "f.sofa"}, "3#1"),
])
def test_split_key_takes_the_file_from_the_locator(key, locator, item):
    file = file_of(locator)
    assert split_key(key, file) == (file, item)
    assert ir_id("ds", file, item).split("-", 1)[1] == make_ir_id("ds", key).split(":", 1)[1]


def test_a_key_that_does_not_start_with_its_file_is_refused():
    with pytest.raises(ValueError, match="does not start with its file"):
        split_key("other.wav#1", "a.wav")
    with pytest.raises(ValueError):
        split_key("a.wav1", "a.wav")             # the file must be followed by "#" or nothing


def test_index_table_reads_a_file_with_a_hash_in_its_name(tmp_path, monkeypatch):
    monkeypatch.setenv("RIRDB_ROOT", str(tmp_path))
    files = tmp_path / "raw" / "toy" / "files"
    files.mkdir(parents=True)
    for name in ("a-#3.wav", "b.wav"):
        sf.write(files / name, np.zeros(100), 16000)
    d = SimpleNamespace(id="toy", ir_kinds=["room"], cap=None,
                        adapter=SimpleNamespace(name="wav_tree", params={}),
                        analysis=SimpleNamespace(reference_role=None, keep_s=None, start_s=None))
    df, _ = index_table(d)
    assert sorted(df["file"]) == ["a-#3.wav", "b.wav"]
    assert (df["item"] == "").all()
    assert set(df["ir_id"]) == {ir_id("toy", "a-#3.wav", ""), ir_id("toy", "b.wav", "")}
