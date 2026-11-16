"""The public website, built from the release tables alone (so GitHub Actions can build it from the release).

    rirdb dev site --out site/

    index.html          what RIRDB is, ways in, how it works, the guide and about sections      home.py, guide.py
    datasets.html       all datasets, with the search and its filters, and What you get:       datasets.py
                        charts of what the command selects, and the command itself
    d/<dataset>.html    one page per dataset: what it is, what its authors provide, its rooms,  datasets.py
                        what RIRDB measured (a dataset not measured yet: the first parts only)
    rooms.html, guide.html, about.html   old addresses: they lead to What you get and the home page's sections
    reference.html      every column of the tables                                              guide.py
    search-data.js      what the datasets page reads (data.py), loaded once by the browser
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
    from rirdb.site import guide, home
    from rirdb.site.data import primary, search_data
    from rirdb.site.layout import ICON_SVG, moved

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
    write("index.html", home.home(search, len(primary(irs))))    # IRs as rirdb search counts them
    write("datasets.html", ds_pages.datasets_page(search))
    write("reference.html", guide.reference_page())
    # old addresses: the rooms page (v3), the guide and about pages (v3), the columns and spaces pages (v2)
    write("rooms.html", moved("datasets.html", add="tab=get"))
    write("spaces.html", moved("datasets.html", add="tab=get"))
    write("guide.html", moved("index.html", section="#guide"))
    write("about.html", moved("index.html", section="#about"))
    write("columns.html", moved("reference.html"))

    corpus = primary(irs)
    cards = {c["id"]: c for c in search["datasets"]}
    for _, d in datasets.iterrows():
        i = d["dataset"]
        own = client.table(f"source_meta.{i}") if client.has(f"source_meta.{i}.parquet") else None
        write(f"d/{i}.html", ds_pages.dataset_page(d, cards[i], irs, spaces, own, corpus))
    (out / ".nojekyll").write_text("")
    return out
