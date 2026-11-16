"""`search`, `info` and `rirdb.select()` on the generated dataset's release (conftest.py, `user`)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from rirdb.cli import app
import numpy as np
import pandas as pd

from rirdb.select import (download_bytes, download_plan, human, licences_text, select, spread_per_space,
                          year_range)

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


def test_per_space_keeps_at_most_n_of_each_space(user):
    every, two = select().table, select(per_space=2).table
    assert two.groupby("space").size().max() == 2 and set(two["space"]) == set(every["space"])
    assert two["ir_id"].tolist() == select(per_space=2).table["ir_id"].tolist()      # the same IRs every time


def test_the_spread_runs_from_the_shortest_to_the_longest_reverb():
    rt = [0.3, 0.31, 0.32, 0.33, 0.5, 0.9, 1.4, 2.0, np.nan]
    rows = pd.DataFrame({"space": "s", "ir_id": [f"i{k}" for k in range(9)], "rt_mid": rt})
    assert sorted(spread_per_space(rows, 3)["rt_mid"]) == [0.3, 0.9, 2.0]   # 0.3, about 0.77 (log middle), 2.0
    assert spread_per_space(rows.iloc[[0, 4, 8]], 2)["rt_mid"].tolist() == [0.3, 0.5]   # rt_mid first, then by id
    assert len(spread_per_space(rows, 20)) == 9                                         # fewer than n: all of them


def test_max_archive_leaves_out_irs_in_bigger_downloads(user):
    every = select().table
    size = download_bytes(every)
    assert (size == download_plan(every)["size_bytes"].iloc[0]).all()       # one download, toy.zip, taken whole
    assert select(max_archive=f"{int(size.iloc[0])}B").table.equals(every)
    assert select(max_archive=f"{int(size.iloc[0]) - 1}B").table.empty
    with pytest.raises(ValueError, match="not a size"):
        select(max_archive="big")


def test_search_takes_the_options_of_get(user):
    out = CliRunner().invoke(app, ["search", "--n", "2", "--balance", "space", "--per-space", "2"])
    assert out.exit_code == 0, out.output
    assert out.output.startswith("2 IRs")


def test_one_per_space_takes_the_typical_ir():
    rows = pd.DataFrame({"space": "s", "ir_id": [f"i{k}" for k in range(5)], "rt_mid": [0.3, 0.5, 0.6, 0.7, 2.0]})
    assert spread_per_space(rows, 1)["rt_mid"].tolist() == [0.6]                  # the median, not the driest


def test_year_keeps_the_datasets_published_in_the_range(user):
    every = select().table
    assert select(year="2021").table.equals(every) and select(year="2020-").table.equals(every)   # toy: 2021
    assert select(year="2022-").table.empty and select(year="-2020").table.empty
    assert year_range("2018-2020") == (2018, 2020) and year_range("-2015") == (None, 2015)
    with pytest.raises(ValueError, match="not a year"):
        select(year="recent")
    with pytest.raises(ValueError, match="not a year"):
        select(year="2020-2018")


def test_a_known_defect_is_kept_and_warned_about(user):
    t = select().table
    booth = t["file"].str.startswith("booth/")
    assert booth.any() and t.loc[booth, "known_defect"].eq("The booth files are too short to trust.").all()
    assert t.loc[~booth, "known_defect"].eq("").all()
    assert select().known_defects() == [f"toy: {booth.sum()} IRs with a known defect. The booth files are too short "
                                        "to trust."]
    assert select(dataset="toy", t30="1-").known_defects() == []          # the hall and stairs only
    out = CliRunner().invoke(app, ["search"])
    assert "Warning toy: 2 IRs with a known defect" in out.output


def test_a_pinned_dataset_not_measured_yet_keeps_its_unknown_size(monkeypatch):
    """A GitHub tarball has no size until its files are listed, and only a measured dataset has a files table."""
    import rirdb.select as s

    downloads = pd.DataFrame({"dataset": ["gh"], "download": ["gh-abc.tar.gz"], "size_bytes": [np.nan], "source_rank": [0]})
    monkeypatch.setattr(s.client, "table", lambda name: downloads if name == "downloads" else pytest.fail(name))
    monkeypatch.setattr(s.client, "has", lambda name: False)
    assert np.isnan(s.download_sizes()[("gh", "gh-abc.tar.gz")])
