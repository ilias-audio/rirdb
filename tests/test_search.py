"""`search`, `info` and `rirdb.select()` on the generated dataset's release (conftest.py, `user`)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from rirdb.cli import app
from rirdb.select import download_plan, human, licences_text, select

pytestmark = pytest.mark.xdist_group("toy")


def test_without_format_a_selection_takes_primary_rows(user):
    assert len(select().table) == 6                         # hall: its mono layout (3 rows) is primary, not stereo
    stereo = select(format="stereo").table
    assert stereo["file"].tolist() == ["hall/s1_mstereo.wav"]


def test_filters(user):
    assert len(select(use="commercial").table) == 6          # CC0: every primary row
    assert set(select(grade="A").table["grade"]) <= {"S", "A"}
    assert select(dataset="toy", exclude_dataset="toy").table.empty
    with pytest.raises(ValueError, match="no dataset nope"):
        select(dataset="nope")
    with pytest.raises(ValueError, match="grade 'X'"):
        select(grade="X")


def test_sizes_count_each_download_once_and_the_wav_files(user):
    sel = select()
    plan = download_plan(sel.table)
    assert plan[["download", "n_irs"]].values.tolist() == [["toy.zip", 6]]
    s = sel.sizes()
    assert s.n_downloads == 1 and s.download_bytes == plan["size_bytes"].iloc[0] and s.unknown_size == 0
    # 6 IRs as float32 (the primary rows); the stereo IR alone is smaller, and --format mono too
    assert s.output_bytes > select(format="stereo").sizes().output_bytes > 0
    mono = select(dataset="toy", format="mono").sizes().output_bytes
    assert mono == s.output_bytes                            # the primary rows are the mono ones


def test_search_prints_the_split_and_the_size(user):
    out = CliRunner().invoke(app, ["search", "--format", "stereo"])
    assert out.exit_code == 0, out.output
    assert "1 IR in 1 space from 1 dataset" in out.output and "toy.zip" not in out.output
    assert "Download:" in out.output and "measured" not in out.output and "simulated" in out.output


def test_info_names_the_licence_and_the_counts(user):
    out = CliRunner().invoke(app, ["info", "toy"])
    assert out.exit_code == 0, out.output
    assert "Licence: CC0-1.0" in out.output and "7 in the table, 7 found in the files" in out.output
    assert "rirdb get --dataset toy" in out.output
    assert CliRunner().invoke(app, ["info", "nope"]).exit_code != 0


def test_small_helpers():
    assert human(1234567) == "1.2 MB" and human(999) == "999 B" and human(127.4e9) == "127.4 GB"
    import pandas as pd
    assert licences_text(pd.Series(["A", "B", "A"])) == "A, B"
    assert licences_text(pd.Series(["A", "B", "C"])) == "3 licences, one per space"


def test_space_type_t30_and_provides_on_the_command_line(user):
    out = CliRunner().invoke(app, ["search", "--t30", "1.0-1.5"]).output
    assert out.startswith("3 IRs")                                   # the hall: three primary rows at 1.2 s
    out = CliRunner().invoke(app, ["search", "--provides", "geometry"]).output
    assert out.startswith("0 IRs")                                   # the toy dataset's authors provide nothing
    bad = CliRunner().invoke(app, ["search", "--space-type", "spaceship"])
    assert bad.exit_code != 0 and "spaceship" in str(bad.exception)
