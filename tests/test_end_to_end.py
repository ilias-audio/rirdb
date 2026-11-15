"""The whole pipeline on a tiny generated dataset, from `pin` to `build`.

Seven generated impulse responses with known reverberation times, zipped, go through the same commands as a
real dataset, each in its own process with its own data root and registry, as on the cluster:

    pin       fetch/adopt_manual.sh   the zip is hashed into downloads.tsv, unpacked, and every file hashed
              into files.tsv.gz (the path of a dataset that cannot be downloaded by a script)
    index     rirdb dev index         tables/toy/index.parquet
    measure   rirdb dev index-v1, analyze, merge, audit, audit-merge (analyser v1), then the v1 converter
              scripts/one_off/v1_to_tables.py into tables/toy/v1/ (step 4 replaces these with `measure`)
    build     rirdb dev build         release/candidate/, and every check passes

If this test passes, the pipeline works from files to the release tables. It needs no downloaded data and no
network, and takes about a minute. The dataset is in toy.py and the pipeline in conftest.py (`toy`).
"""

from __future__ import annotations

import gzip
import json

import numpy as np
import pandas as pd
import pytest

from tests.toy import DUPLICATE, TOY_FILES

pytestmark = pytest.mark.xdist_group("toy")      # the pipeline runs once (conftest.py, `toy`)


def test_the_pin_records_the_download_and_every_file(toy):
    root, registry = toy
    pin = pd.read_csv(registry / "toy" / "downloads.tsv", sep="\t", dtype=str)
    assert pin[["relpath", "algo", "extract_to"]].values.tolist() == [["toy.zip", "sha256", "."]]
    assert pin["url"].str.startswith("http://127.0.0.1:").all()           # the site of conftest.py (toy_site)
    with gzip.open(registry / "toy" / "files.tsv.gz", "rt") as f:
        files = pd.read_csv(f, sep="\t")
    assert sorted(files["relpath"]) == sorted([*TOY_FILES, DUPLICATE[0]])


def test_the_index_table_lists_every_response(toy):
    root, _ = toy
    index = pd.read_parquet(root / "tables" / "toy" / "index.parquet")
    assert len(index) == len(TOY_FILES) + 1 and index["sampled"].all()
    assert set(index["space"]) == {"toy/hall", "toy/booth", "toy/stairs"}
    assert sorted(index["channels"].unique()) == [1, 2]          # the stereo file is one row with two channels
    assert index["ir_id"].str.fullmatch(r"toy-[0-9a-f]{16}").all()
    assert set(index["sample_format"]) == {"float32"}


def test_measured_values_match_the_known_decay(toy):
    root, _ = toy
    wide = pd.read_parquet(root / "metrics" / "v1" / "toy" / "wide.parquet").set_index("local_key")
    assert len(wide) == len(TOY_FILES) + 1
    assert (wide["error"] == "").all(), wide.loc[wide["error"] != "", "error"].to_dict()
    for rel, (t60, _) in TOY_FILES.items():
        t30 = wide.loc[rel, "t30_mid"]
        # one realisation of a random decay: about 3 % spread at 1.2 s, 5 % at 0.3 s (ISO 3382-2 Annex A)
        assert np.isfinite(t30) and abs(t30 - t60) / t60 < 0.20, f"{rel}: T30 {t30:.3f} s, expected {t60} s"
    assert wide.loc["hall/s1_mstereo.wav", "analysed_role"] == "SL+SR"


def test_the_signal_audit_finds_nothing_wrong(toy):
    root, _ = toy
    audit = pd.read_parquet(root / "audit" / "v1" / "toy" / "audit.parquet")
    assert len(audit) == len(TOY_FILES) + 1
    for flag in ("flag_not_decaying", "flag_dropout", "flag_sweep_like"):
        assert not audit[flag].any(), flag


def test_the_release_table_reads_the_known_answers(toy):
    root, _ = toy
    c = root / "release" / "candidate"
    irs = pd.read_parquet(c / "irs.parquet").assign(f=lambda d: d["file"]).set_index("f")
    assert len(irs) == len(TOY_FILES) + 1
    for rel, (t60, _) in TOY_FILES.items():
        assert abs(irs.loc[rel, "rt_mid"] - t60) / t60 < 0.20, rel
    assert set(irs.loc[["hall/s1_m1.wav", "stairs/s1_m1.wav"], "grade"]) <= {"S", "A", "B"}
    copy, original = DUPLICATE
    assert irs.loc[copy, "duplicate_of"] == irs.loc[original, "ir_id"]
    assert irs.loc[original, "duplicate_of"] == ""
    assert set(irs["licence"]) == {"CC0-1.0"} and set(irs["use_commercial"]) == {"yes"}
    assert set(irs["download"]) == {"toy.zip"}
    assert set(irs.loc[["hall/s1_mstereo.wav"], "format"]) == {"stereo"}
    meta = json.loads((c / "release.json").read_text())
    assert meta["rows"]["irs"] == len(irs) and "irs.parquet" in meta["files"]
