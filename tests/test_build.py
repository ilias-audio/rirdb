"""`build` (src/rirdb/build.py) and its checks (checks.py, checks.sql): the rules, on small frames."""

import pandas as pd

import numpy as np

from rirdb.build import decay_grade, download_of, grade_of, primary_rows
from rirdb.checks import column_checks, sql_checks, threshold_checks


def test_each_file_finds_its_download():
    downloads = pd.DataFrame({"relpath": ["a.zip", "b.tar.gz", "notes.pdf"], "extract_to": ["a", "b", "-"]})
    files = pd.Series(["a/x.wav", "b/y.wav|b/z.wav", "notes.pdf", "added-by-hand.txt"])
    assert download_of(files, downloads).tolist() == ["a.zip", "b.tar.gz", "notes.pdf", ""]
    one = pd.DataFrame({"relpath": ["all.zip"], "extract_to": ["."]})
    assert download_of(pd.Series(["Audio/x.wav"]), one).tolist() == ["all.zip"]


def test_every_space_has_a_primary_layout():
    r = pd.DataFrame({"space": ["s/a", "s/a", "s/b", "s/b"], "layout": ["foa_ambix", "stereo", "stereo", "binaural"],
                      "is_preferred": [True, False, False, False]})
    assert primary_rows(r).tolist() == [True, False, False, True]      # s/b: its first layout, binaural


def test_decay_grades_follow_thresholds_yaml():
    rows = []
    for ir, valid_t30 in (("a", True), ("b", False)):
        for band in (125, 250, 500, 1000, 2000, 4000):
            rows.append({"ir_id": ir, "band_hz": band, "valid_t30": valid_t30, "valid_t20": True})
    octave = pd.DataFrame(rows)
    any_valid = pd.Series([True, True, False], index=["a", "b", "c"])
    assert decay_grade(octave, any_valid).tolist() == ["A", "B", "D"]


def test_grade_s_needs_every_defect_assessed():
    decay = np.array(["A", "A", "A", "A", "B"])
    n_major, n_minor = np.array([0, 0, 0, 1, 0]), np.array([0, 1, 0, 0, 0])
    assessed = np.array([True, True, False, True, True])         # row 3: no signal audit ran (decision D)
    space = np.array(["hall", "hall", "hall", "hall", "anechoic"])
    assert grade_of(decay, n_major, n_minor, assessed, space).tolist() == ["S", "A", "A", "C", ""]
    (name, query), = threshold_checks()
    assert "defect_dropouts IS NULL" in query and "defect_inverted_channel" not in query   # v1 does not measure it


def test_the_checks_are_generated_and_read():
    names = [n for n, _ in column_checks()]
    assert "irs.grade is one of S|A|B|C|D" in names
    assert "irs.ir_id is never empty" in names
    sql = dict(sql_checks())
    assert "a grade S row has no defect" in sql and all(q.lstrip().upper().startswith(("SELECT", "WITH")) for q in sql.values())


def test_status_has_one_line_per_dataset():
    from rirdb.registry import load_registry
    from rirdb.status import status

    s = status()
    assert len(s) == len(load_registry()) == 222
    assert set(s["stage"]) <= {"catalogued", "registered", "pinned", "indexed", "measured", "candidate"}


def test_one_primary_layout_per_space():
    r = pd.DataFrame({"space": ["s/a"] * 4, "layout": ["mono_omni", "mono_omni", "stereo", "binaural"],
                      "is_preferred": [True, True, True, False]})
    assert primary_rows(r).tolist() == [True, True, False, False]      # the preferred layout with the most rows


def test_no_noise_floor_is_no_defect():
    from rirdb.build import defects

    m = pd.DataFrame({"v1_audit_error": "", "v1_error": "", "fs": 48000, "dropout_ms": 0.0, "active_s": 1.0,
                      "env_slope_db_s": -60.0, "glide_db": 0.0, "tonal_frac": 0.0, "bw_eff_hz": 20000.0,
                      "v1_clipped": False, "v1_nonfinite": False, "v1_all_zero": False, "v1_quantized_tail": False,
                      "dc_offset_db": -120.0, "peak_to_noise_db": [np.inf, 20.0, np.nan]})
    assert defects(m, pd.Series(["mono"] * 3))["defect_high_noise_floor"].tolist() == [False, True, pd.NA]


def test_what_it_is_comes_from_the_registry():
    """The datasets table carries the registry's words: description, provider, what the authors provide, caveats."""
    from rirdb.build import what_it_is
    from rirdb.registry import Dataset

    d = Dataset.model_validate({
        "id": "x", "name": "X", "type": "measured", "status": "catalogued", "content": "Two  rooms,\n measured.",
        "license": {"spdx": "CC-BY-4.0"}, "room_info": {"positions": "yes", "volume": "no", "where": "paper, section 2"},
        "web_check": {"institution": "Somewhere", "country": "UK", "caveats": ["One.", "Two\n lines."]}})
    w = what_it_is(d)
    assert w["description"] == "Two rooms, measured." and w["institution"] == "Somewhere" and w["country"] == "UK"
    assert (w["provides_positions"], w["provides_volume"], w["provides_geometry"]) == ("yes", "no", "")
    assert w["provides_where"] == "paper, section 2" and w["caveats"] == "One.\nTwo lines."
