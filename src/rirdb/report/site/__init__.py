"""The rirdb website: every dataset, where to get it, what is inside, and what the analysis found.

    rirdb dev report site                      collect the data (DB, compute node) and build reports/site/
    rirdb dev report site --reuse-data         rebuild the pages from reports/site/data/ (no DB needed)
    rirdb dev report site --only datasets      rebuild one section

Sections are page modules with a `render(site)` function (see core.Site). Output is plain static files
with relative links: publish the folder as it is (GitHub Pages, a Hugging Face static Space, or open
index.html locally).
"""

from __future__ import annotations

import importlib
import json
import re
import shutil
from pathlib import Path

from rirdb import paths
from rirdb.report.site.core import HERE, NAV, OUT_DIR, SITE_NAME, Site, make_env

# section name -> module (rendered in this order; "docs" writes the sitemap last-but-one)
SECTIONS = {
    "datasets": "rirdb.report.site.pages_dataset",     # d/<id>.html for registry datasets
    "catalog": "rirdb.report.site.pages_catalog",      # datasets/, spaces/, d/<survey id>.html
    "analysis": "rirdb.report.site.pages_analysis",    # analysis/*
    "get": "rirdb.report.site.pages_get",              # get/, get/<id>.sh
    "docs": "rirdb.report.site.pages_docs",            # home, methods/, about/, downloads/, 404
}


# site.json keys the Field Guide's own data (guide.build) does not carry
GUIDE_ROW_EXTRA = ("storage", "manifest", "lock_summary", "analysed", "n_spaces_indexed", "n_findings")
GUIDE_SPACE_EXTRA = ("room_key", "dims_m", "description", "materials", "bg_noise_dba", "note", "licence", "n_records", "formats")
# the Field Guide's row links; a link to the dataset's page on this site goes before them
GUIDE_ROW_HOOK = "if (d.homepage) links.push(linkOut(d.homepage, 'Website'));"


def back_bar(current: str) -> str:
    """The site's navigation as a thin bar above a standalone report (which keeps its own header)."""
    links = []
    for key, label, path in NAV:
        cur = ' aria-current="page" style="color:inherit;font-weight:700"' if key == current else ' style="color:inherit"'
        links.append(f'<a href="../{path}"{cur}>{label}</a>')
    return (f'<nav aria-label="{SITE_NAME} website" style="display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center;'
            "padding:8px clamp(16px,4vw,40px);font-size:14px;line-height:1.4;"
            "border-bottom:1px solid var(--rule,var(--line,#dde1e8));background:var(--surface,var(--panel,#fff));"
            f'color:var(--ink,#121725)"><a href="../index.html" style="color:inherit;font-weight:700">{SITE_NAME} home</a>'
            + "".join(links) + "</nav>\n")


def as_document(page: str, bar: str) -> str:
    """A report page body (claude.ai artifact style: <title>, <style>, then content) as a standalone
    document with the bar first in <body>, as guide.build() wraps it; a full document gets the bar only."""
    if re.search(r"<body\b", page):
        return re.sub(r"<body\b[^>]*>", lambda m: m.group(0) + "\n" + bar, page, count=1)
    head, end, body = page.partition("</style>")
    if not end:
        head, body = "", page
    return ('<!doctype html>\n<html lang="en-GB">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            f"{head}{end}\n</head>\n<body>\n{bar}{body}</body>\n</html>\n")


def field_guide(site: Site) -> None:
    """The Field Guide rebuilt from this build's data, so its registry, numbers and date match the site's,
    with the site's navigation on top and a link from each dataset row to its page here."""
    from rirdb.report import guide

    tpl = Path(guide.__file__).parent / "guide_template.html"
    if not tpl.exists():
        site.copy(paths.REPORTS_DIR / "guide" / "index.html", "guide/index.html")
        return
    d = site.data
    g = {"generated": d.get("generated"), "analyzer_version": d.get("analyzer_version"),
         "datasets": [{k: v for k, v in r.items() if k not in GUIDE_ROW_EXTRA} for r in d["datasets"]],
         "new_datasets": d.get("survey") or [], "survey_date": d.get("survey_date"),
         "spaces": [{k: v for k, v in s.items() if k not in GUIDE_SPACE_EXTRA} for s in d.get("spaces") or []],
         "validity_t30": d.get("validity_t30"), "openair_licences": d.get("openair_licences")}
    pages = sorted({r["id"] for r in d["datasets"]} | {n["id"] for n in d.get("survey") or []})  # every d/<id>.html
    js = json.dumps(g, separators=(",", ":"), default=str).replace("</", "<\\/")
    page = tpl.read_text().replace("/*GUIDE_JSON*/null", f"{js};\nconst RIRDB_PAGES = new Set({json.dumps(pages)})", 1)
    if GUIDE_ROW_HOOK in page:
        page = page.replace(GUIDE_ROW_HOOK, "if (RIRDB_PAGES.has(d.id)) links.push(h('a', { href: '../d/' + d.id + "
                            "'.html', text: 'rirdb page' }));\n  " + GUIDE_ROW_HOOK, 1)
    else:
        print("[site] guide: row-link hook not found in guide_template.html; rows link only to the sources")
    site.write("guide/index.html", as_document(page, back_bar("guide")))


def copy_static(site: Site) -> None:
    for f in (HERE / "static").iterdir():
        if f.is_file() and not f.name.startswith("."):
            site.copy(f, f"assets/{f.name}")
    # the two standalone reports, each with the site's navigation on top
    field_guide(site)
    ov = paths.REPORTS_DIR / "overview" / "index.html"
    if ov.exists():
        site.write("overview/index.html", as_document(ov.read_text(), back_bar("")))
    else:
        print(f"[site] missing {ov}")
    for name in ("openair_t30_edt.png",):
        site.copy(paths.REPORTS_DIR / "validation" / name, f"assets/{name}")
    for name in ("t30_bands.png", "t30_c80.png", "coverage_t30_drr.png"):
        site.copy(paths.REPORTS_DIR / "corpus" / name, f"assets/{name}")
    site.write(".nojekyll", "")


def build(out_dir: Path = OUT_DIR, only: list[str] | None = None, reuse_data: bool = False) -> Path:
    from rirdb.report.site import data as sdata

    out_dir.mkdir(parents=True, exist_ok=True)
    if reuse_data and (out_dir / "data" / "site.json").exists():
        d = sdata.load(out_dir)
    else:
        d = sdata.collect(out_dir)
    from rirdb.report.site.pages_catalog import drop_survey_twins
    drop_survey_twins(d)  # every section, the Field Guide and the counts then see the same survey list
    site = Site(out=out_dir, data=d, env=make_env())
    copy_static(site)
    for name, mod in SECTIONS.items():
        if only and name not in only:
            continue
        try:
            m = importlib.import_module(mod)
        except ModuleNotFoundError as e:
            if e.name == mod:
                print(f"[site] section {name}: module not written yet, skipped")
                continue
            raise
        m.render(site)
        print(f"[site] section {name}: done")
    if not only:
        write_sitemap(site)
    return out_dir / "index.html" if not only else out_dir


def write_sitemap(site: Site) -> None:
    from rirdb.report.site.core import SITE_URL

    urls = sorted(set(site.pages))
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    day = (site.data.get("generated") or "")[:10]
    for u in urls:
        loc = SITE_URL + u.removesuffix("index.html")
        xml.append(f"  <url><loc>{loc}</loc>" + (f"<lastmod>{day}</lastmod>" if day else "") + "</url>")
    xml.append("</urlset>")
    site.write("sitemap.xml", "\n".join(xml) + "\n")
    site.write("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}sitemap.xml\n")


def clean_output(out_dir: Path = OUT_DIR) -> None:
    """Remove generated pages but keep data/ (for --reuse-data)."""
    for p in out_dir.iterdir():
        if p.name != "data":
            shutil.rmtree(p) if p.is_dir() else p.unlink()
