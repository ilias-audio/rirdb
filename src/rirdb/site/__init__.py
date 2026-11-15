"""The public website, built from the release tables alone (so GitHub Actions can build it from the release).

    rirdb dev site --out site/

    index.html          what RIRDB is, a search box, the collection at a glance, ways in        home.py
    datasets.html       all datasets, with the search and its filters                          datasets.py
    d/<dataset>.html    one page per dataset: what it is, what its authors provide, its rooms,  datasets.py
                        what RIRDB measured (a dataset not measured yet: the first parts only)
    rooms.html          every room, plotted and listed, with the same filters                   rooms.py
    guide.html          get started, glossary, cite, add a dataset                              guide.py
    about.html          who, why, how IRs are measured, how datasets are chosen                 guide.py
    reference.html      every column of the tables                                              guide.py
    search-data.js      what the two search pages read (data.py), loaded once by the browser
    style.css, site.js, fonts/, icon.svg

words.py turns codes into words, layout.py holds the frame every page shares, charts.py draws the charts (SVG,
no library). Every page works without JavaScript; site.js adds the search, the tooltips and the Copy buttons.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rirdb import client

HERE = Path(__file__).parent


def build(out: Path) -> Path:
    from rirdb.site import datasets as ds_pages
    from rirdb.site import guide, home, rooms
    from rirdb.site.data import primary, search_data
    from rirdb.site.layout import ICON_SVG

    irs, datasets, spaces = client.table("irs"), client.table("datasets"), client.table("spaces")
    search = search_data(irs, spaces, datasets)
    out = Path(out)
    (out / "d").mkdir(parents=True, exist_ok=True)
    for name in ("style.css", "site.js"):
        shutil.copyfile(HERE / name, out / name)
    shutil.copytree(HERE / "fonts", out / "fonts", dirs_exist_ok=True)
    (out / "icon.svg").write_text(ICON_SVG)
    data = json.dumps(search, separators=(",", ":"), default=lambda o: o.item())
    (out / "search-data.js").write_text(f"window.RIRDB = {data};\n")

    write = lambda path, html: (out / path).write_text(html)
    write("index.html", home.home(search))
    write("datasets.html", ds_pages.datasets_page(search))
    write("rooms.html", rooms.rooms_page(search))
    write("guide.html", guide.guide_page())
    write("about.html", guide.about_page(len(datasets), int((datasets["status"] == "measured").sum()), len(irs)))
    write("reference.html", guide.reference_page())
    for old, new in (("columns.html", "reference.html"), ("spaces.html", "rooms.html")):    # addresses of v2
        write(old, f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={new}">'
                   f'<link rel="canonical" href="{new}"><a href="{new}">{new}</a>\n')

    corpus = primary(irs)
    cards = {c["id"]: c for c in search["datasets"]}
    for _, d in datasets.iterrows():
        i = d["dataset"]
        own = client.table(f"source_meta.{i}") if client.has(f"source_meta.{i}.parquet") else None
        write(f"d/{i}.html", ds_pages.dataset_page(d, cards[i], irs, spaces, own, corpus))
    (out / ".nojekyll").write_text("")
    return out
