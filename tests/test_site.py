"""The website (rirdb.site), built from the generated dataset's release over local HTTP (conftest.py, `user`)."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from rirdb import client
from rirdb.select import Query, matching
from rirdb.site import build
from rirdb.site.data import T30_EDGES, bin_index, search_data
from rirdb.site.words import REVERB, licence_short, room_label

pytestmark = pytest.mark.xdist_group("toy")


@pytest.fixture
def site(user, tmp_path):
    return build(tmp_path / "site")


def test_the_site_has_its_pages(site):
    pages = sorted(p.relative_to(site).as_posix() for p in site.rglob("*.html"))
    assert pages == ["about.html", "columns.html", "d/toy.html", "datasets.html", "guide.html", "index.html",
                     "reference.html", "rooms.html", "spaces.html"]
    for f in ("style.css", "site.js", "search-data.js", "icon.svg", "fonts/plex-sans.woff2"):
        assert (site / f).exists(), f
    assert 'url=reference.html' in (site / "columns.html").read_text()           # the v2 address still works
    home = (site / "index.html").read_text()
    assert "Room impulse responses from 1 public datasets" in home and "rirdb search" in home
    assert 'href="https://github.com/ilias-audio"' in home                       # the maintainer, in the footer


def test_a_measured_dataset_page_has_every_part(site):
    toy = (site / "d" / "toy.html").read_text()
    assert "Toy dataset (generated for the tests)" in toy and "rirdb get --dataset toy" in toy
    for part in ('id="about"', 'id="provides"', 'id="rooms"', 'id="measured"', 'id="quality"', 'id="examples"'):
        assert part in toy, part
    assert toy.count('<svg class="chart"') == 4                                  # T30, per octave, DRR, C50
    assert 'id="room-hall"' in toy and 'id="room-booth"' in toy and 'id="room-stairs"' in toy
    assert "Measured by RIRDB" in toy and "CC0" in toy


def test_the_search_data_is_written_once_for_both_search_pages(site):
    text = (site / "search-data.js").read_text()
    data = json.loads(text.removeprefix("window.RIRDB = ").rstrip().removesuffix(";"))
    assert [s["id"] for s in data["spaces"]] == ["toy/booth", "toy/hall", "toy/stairs"]
    assert [d["id"] for d in data["datasets"]] == ["toy"]
    for page in ("datasets.html", "rooms.html"):
        assert 'src="search-data.js"' in (site / page).read_text()
    assert (site / "rooms.html").read_text().count('class="roomrow"') == 3


def kept(data: dict, format: str | None = None, grade: str | None = None, t30: list[str] = (),
         space_type: list[str] = ()) -> int:
    """The IRs the page keeps, by the rules of site.js (rowKept, spaceKept), written again in Python."""
    c = {name: i for i, name in enumerate(data["columns"])}
    ranges = {k: (lo, hi) for k, _, lo, hi in REVERB}
    bins = {b for k in t30 for b in range(len(T30_EDGES) - 1)
            if T30_EDGES[b] >= ranges[k][0] - 1e-9 and (ranges[k][1] is None or T30_EDGES[b + 1] <= ranges[k][1] + 1e-9)}
    total = 0
    for r in data["counts"]:
        if space_type and data["spaces"][r[c["space"]]]["type"] not in space_type:
            continue
        if format is None and not r[c["primary"]]:
            continue
        if format == "mono" and r[c["format"]] != "mono" and not r[c["mono_w"]]:
            continue
        if format not in (None, "mono") and r[c["format"]] != format:
            continue
        if grade and (r[c["grade"]] not in data["grades"] or
                      data["grades"].index(r[c["grade"]]) > data["grades"].index(grade)):
            continue
        if t30 and r[c["t30"]] not in bins:
            continue
        total += r[c["n"]]
    return total


@pytest.mark.parametrize("format", [None, "mono", "stereo"])
@pytest.mark.parametrize("grade", [None, "A"])
@pytest.mark.parametrize("t30", [[], ["class"], ["room", "class", "hall"]])
def test_the_search_counts_what_rirdb_search_selects(user, format, grade, t30):
    irs, spaces, datasets = client.table("irs"), client.table("spaces"), client.table("datasets")
    data = search_data(irs, spaces, datasets)
    ranges = {k: (lo, hi) for k, _, lo, hi in REVERB}
    t30_arg = f"{ranges[t30[0]][0]}-{ranges[t30[-1]][1] or ''}" if t30 else None
    want = len(matching(irs, Query(format=format, grade=grade or "any", t30=t30_arg), datasets))
    assert kept(data, format, grade, t30) == want


def test_space_types_count_as_on_the_command_line(user):
    irs, spaces, datasets = client.table("irs"), client.table("spaces"), client.table("datasets")
    data = search_data(irs, spaces, datasets)
    types = sorted({s["type"] for s in data["spaces"]})
    for t in types:
        assert kept(data, space_type=[t]) == len(matching(irs, Query(space_type=[t]), datasets)), t


def test_t30_bins_are_equal_in_log_time_and_clip_at_the_ends():
    b = bin_index(pd.Series([0.01, 0.05, 0.55, 1.0, 99.0, None]), T30_EDGES)
    assert b.tolist() == [0, 0, T30_EDGES.index(0.5), T30_EDGES.index(1), len(T30_EDGES) - 2, -1]
    assert all(lo in T30_EDGES or lo == 0 for _, _, lo, _ in REVERB)          # the filter ranges sit on bin edges


def test_words():
    assert licence_short("CC-BY-NC-SA-4.0") == "CC BY-NC-SA 4.0" and licence_short("CC0-1.0") == "CC0"
    assert licence_short("LicenseRef-per-space") == "Varies per room"
    assert room_label("aula_carolina", "x/aula-carolina") == "Aula carolina" and room_label("", "x/hall") == "Hall"
