"""The public website, built from the release tables alone (so GitHub Actions can build it from the release).

    rirdb dev site --out site/

    index.html          what RIRDB does, and the commands
    datasets.html       the measured datasets, with a search and filters; the catalogued ones
    spaces.html         every space, with the same search, and a T30-DRR plot
    d/<dataset>.html    one page per measured dataset: its acoustics, its metadata, its quality, its spaces
    columns.html        every column of the tables
    style.css, site.js  the look, and the search, tooltips and sorting (every page also works without it)

pages.py writes the HTML, charts.py the SVG charts, data.py the numbers. Nothing is fetched by the pages: the
search data sits inside the page.
"""

from __future__ import annotations

from pathlib import Path

from rirdb import client

HERE = Path(__file__).parent


def build(out: Path) -> Path:
    from rirdb.site import pages
    from rirdb.site.data import search_data

    irs, datasets, spaces = client.table("irs"), client.table("datasets"), client.table("spaces")
    search = search_data(irs, spaces)
    (out / "d").mkdir(parents=True, exist_ok=True)
    for name in ("style.css", "site.js"):
        (out / name).write_text((HERE / name).read_text())
    (out / "index.html").write_text(pages.home(irs, datasets))
    (out / "datasets.html").write_text(pages.datasets_page(irs, datasets, search))
    (out / "spaces.html").write_text(pages.spaces_page(irs, search))
    (out / "columns.html").write_text(pages.columns_page())
    for _, d in datasets[datasets["n_irs"].notna()].iterrows():
        i = d["dataset"]
        own = client.table(f"source_meta.{i}") if client.has(f"source_meta.{i}.parquet") else None
        (out / "d" / f"{i}.html").write_text(pages.dataset_page(d, irs, spaces, own))
    (out / ".nojekyll").write_text("")
    return out
