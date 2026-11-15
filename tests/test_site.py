"""The website (rirdb.site), built from the generated dataset's release over local HTTP (conftest.py, `user`)."""

from __future__ import annotations

import json
import re

import pytest

from rirdb import client
from rirdb.select import Query, matching
from rirdb.site import build
from rirdb.site.data import T30_EDGES, bin_index, search_data

pytestmark = pytest.mark.xdist_group("toy")


def test_the_site_has_its_pages(user, tmp_path):
    out = build(tmp_path / "site")
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*.html")) == [
        "columns.html", "d/toy.html", "datasets.html", "index.html", "spaces.html"]
    toy = (out / "d" / "toy.html").read_text()
    assert "Toy dataset (generated for the tests)" in toy and "CC0-1.0" in toy and "rirdb get --dataset toy" in toy
    assert 'href="../style.css"' in toy and (out / "style.css").exists() and (out / "site.js").exists()
    assert "<b>7</b>" in (out / "index.html").read_text()                     # 7 IRs in the tables
    assert "<code>ir_id</code>" in (out / "columns.html").read_text()


def test_a_dataset_page_shows_its_acoustics_metadata_and_spaces(user, tmp_path):
    toy = (build(tmp_path / "site") / "d" / "toy.html").read_text()
    for section in ('id="acoustics"', 'id="metadata"', 'id="quality"', 'id="spaces"'):
        assert section in toy
    assert toy.count('<svg class="chart"') == 4                              # T30, T30 per octave, DRR, C50
    assert 'id="s-hall"' in toy and 'id="s-booth"' in toy and 'id="s-stairs"' in toy
    assert "source position" in toy and "condition (state of the room)" in toy


def test_the_spaces_page_links_each_space_to_its_row(user, tmp_path):
    spaces = (build(tmp_path / "site") / "spaces.html").read_text()
    assert spaces.count('<tr data-i="') == 3
    assert 'href="d/toy.html#s-hall"' in spaces


def test_the_search_data_sits_in_the_page_and_reads_back(user, tmp_path):
    page = (build(tmp_path / "site") / "datasets.html").read_text()
    text = re.search(r'<script type="application/json" id="search-data">(.*?)</script>', page, re.S).group(1)
    data = json.loads(text)
    assert [s["id"] for s in data["spaces"]] == ["toy/booth", "toy/hall", "toy/stairs"]


def kept(data: dict, format: str | None = None, grade: str | None = None) -> int:
    """The IRs the page keeps, by the rules of site.js (rowKept), written again in Python."""
    c = {name: i for i, name in enumerate(data["columns"])}
    total = 0
    for r in data["counts"]:
        if format is None and not r[c["primary"]]:
            continue
        if format == "mono" and r[c["format"]] != "mono" and not r[c["mono_w"]]:
            continue
        if format not in (None, "mono") and r[c["format"]] != format:
            continue
        if grade and (r[c["grade"]] not in data["grades"] or
                      data["grades"].index(r[c["grade"]]) > data["grades"].index(grade)):
            continue
        total += r[c["n"]]
    return total


@pytest.mark.parametrize("format", [None, "mono", "stereo"])
@pytest.mark.parametrize("grade", [None, "A", "C"])
def test_the_search_counts_what_rirdb_search_selects(user, format, grade):
    irs, spaces = client.table("irs"), client.table("spaces")
    data = search_data(irs, spaces)
    want = len(matching(irs, Query(format=format, grade=grade or "any")))
    assert kept(data, format, grade) == want


def test_t30_bins_are_equal_in_log_time_and_clip_at_the_ends():
    import pandas as pd
    b = bin_index(pd.Series([0.01, 0.05, 0.55, 1.0, 99.0, None]), T30_EDGES)
    assert b.tolist() == [0, 0, T30_EDGES.index(0.5), T30_EDGES.index(1), len(T30_EDGES) - 2, -1]
