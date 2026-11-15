"""The pages of the website, as HTML strings: home, datasets (with the search), spaces (with the search and the
T30-DRR plot), one page per measured dataset, and the columns of the tables."""

from __future__ import annotations

import csv
import html
import json
import re
from pathlib import Path

import pandas as pd

from rirdb import __version__, client
from rirdb.select import GRADES, ORIGINS, USES, cap_of, dataset_download_bytes, grade_split, size_text
from rirdb.site import charts
from rirdb.site.data import BANDS, DB_EDGES, METADATA, SEARCH_FORMATS, T30_EDGES, acoustics, metadata, space_bands

REPO = "https://github.com/ilias-audio/rirdb"
PYPI = "https://pypi.org/project/rirdb/"
GRADE_HELP = ("S and A: T30 valid in every octave from 125 Hz to 4 kHz (S: no defect at all, every defect checked). "
              "B: T20 or T30 valid from 250 Hz to 2 kHz. C: some decay value valid, or a major defect. D: none valid. "
              "No grade: an anechoic space.")
PRIMARY_NOTE = ("A space published in two layouts (say Ambisonic and binaural) counts once, as <code>rirdb search</code> "
                "counts it.")                                       # HTML

with open(Path(__file__).parents[1] / "columns.csv", newline="") as _f:
    COLUMNS = list(csv.DictReader(_f))
MEANING = {r["name"]: r["meaning"] for r in COLUMNS if r["table"] == "irs"}


def e(x) -> str:
    """Text for HTML; an empty or missing value is an empty string."""
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) or x is pd.NA else html.escape(str(x))


def page(title: str, body: str, depth: int = 0) -> str:
    up = "../" * depth
    nav = (f'<a href="{up}index.html">RIRDB</a><a href="{up}datasets.html">Datasets</a>'
           f'<a href="{up}spaces.html">Spaces</a><a href="{up}columns.html">Columns</a>'
           f'<a href="{REPO}">GitHub</a><a href="{PYPI}">PyPI</a>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{e(title)}</title><link rel="stylesheet" href="{up}style.css">'
            f'<script src="{up}site.js" defer></script></head>'
            f'<body><header><nav>{nav}</nav></header><main>{body}</main>'
            f'<footer>RIRDB {e(__version__)}, tables {e(client.version())}. The audio stays with its authors and keeps its '
            f'licence; the catalogue and the tables are CC BY 4.0, the code MIT. A preview: ids, rows and columns may '
            f'change before 1.0.</footer></body></html>\n')


def table(head: list[str], rows: list[list[str]], num: set[int] = frozenset(), cls: str = "",
          row_attrs: list[str] | None = None) -> str:
    """An HTML table; cells are given as HTML, the columns in `num` right-aligned. A cell may be a pair
    (html, sort key): the key is what a sortable table sorts by."""
    th = "".join(f'<th{" class=n" if i in num else ""}>{h}</th>' for i, h in enumerate(head))

    def td(i, c):
        text, key = c if isinstance(c, tuple) else (c, None)
        attrs = (" class=n" if i in num else "") + (f' data-sort="{e(key)}"' if key is not None else "")
        return f"<td{attrs}>{text}</td>"

    tr = "".join(f"<tr{(' ' + row_attrs[k]) if row_attrs else ''}>" + "".join(td(i, c) for i, c in enumerate(r))
                 + "</tr>" for k, r in enumerate(rows))
    cls_attr = f' class="{cls}"' if cls else ""
    return (f'<div class="box"><table{cls_attr}><thead><tr>{th}</tr></thead>'
            f'<tbody>{tr}</tbody></table></div>')


def grade_bar(grades: pd.Series) -> str:
    """One bar, a segment per grade, its width the share of IRs."""
    counts = grades.replace("", "none").fillna("none").value_counts()
    total = counts.sum()
    parts = "".join(f'<i class="g{g}" style="width:{100 * counts.get(g, 0) / total:.2f}%" '
                    f'title="{g}: {counts.get(g, 0):,}"></i>' for g in [*GRADES, "none"] if counts.get(g, 0))
    return f'<span class="bar" role="img" aria-label="grades">{parts}</span>'


def anchor(space: str) -> str:
    """The id of a space's row on its dataset page: reverbfx/protoverb-100 -> s-protoverb-100."""
    return "s-" + re.sub(r"[^a-z0-9-]+", "-", space.split("/", 1)[-1].lower())


def num(v, digits: int = 2, unit: str = "") -> str:
    return "" if v is None or pd.isna(v) else f"{v:,.{digits}f}{unit}"


def primary(irs: pd.DataFrame) -> pd.DataFrame:
    return irs[irs["is_primary"].astype(bool)]


# ------------------------------------------------------------------------------------------- the search

def search_json(data: dict) -> str:
    """The search data, inside the page (no request to make, so the page also works from a file)."""
    text = json.dumps(data, separators=(",", ":"), default=lambda o: o.item()).replace("</", "<\\/")
    return f'<script type="application/json" id="search-data">{text}</script>'


def filters(irs: pd.DataFrame, spaces_page: bool = False) -> str:
    """The row of filters above the results. Their names are the options of `rirdb search` where it has them."""
    def select(name, label, options):
        opts = "".join(f'<option value="{e(v)}">{e(t)}</option>' for v, t in options)
        return f'<label>{label}<select name="{name}"><option value="">any</option>{opts}</select></label>'

    types = sorted(irs["space_type"].dropna().unique())
    edges = [(f"{t:g}", f"{t:g} s") for t in T30_EDGES]
    hint = "space, dataset" if spaces_page else "name or id"
    parts = [
        f'<label class="grow">Search<input type="search" name="text" placeholder="{hint}" autocomplete="off"></label>',
        select("format", "Format", [(f, f) for f in SEARCH_FORMATS]),
        select("origin", "Origin", [(o, o) for o in ORIGINS]),
        select("grade", "Grade", [(g, f"{g} or better" if g != "S" else "S") for g in GRADES]),
        select("use", "Licence allows", [(u, u) for u in USES]),
        select("type", "Space type", [(t, t.replace("_", " ")) for t in types]),
        select("t30_from", "T30 from", edges[:-1]),
        select("t30_to", "T30 to", edges[1:]),
    ]
    if spaces_page:
        parts += ['<label>Volume from<input type="number" name="vol_from" min="0" step="any" inputmode="decimal" '
                  'placeholder="m³"></label>',
                  '<label>to<input type="number" name="vol_to" min="0" step="any" inputmode="decimal" '
                  'placeholder="m³"></label>']
    return (f'<form id="filters" class="filters" role="search">{"".join(parts)}'
            f'<button type="reset">Clear</button></form>'
            f'<p id="result" class="result" aria-live="polite"></p><pre id="cli" hidden></pre>')


# ------------------------------------------------------------------------------------------- the pages

def home(irs: pd.DataFrame, datasets: pd.DataFrame) -> str:
    measured = datasets[datasets["n_irs"].notna()]
    split = grade_split(primary(irs))
    split_rows = [[e(o), *[f"{v:,}" for v in r]] for o, r in split.iterrows()]
    body = f"""
<h1>Find room impulse responses across public datasets, and get them as one folder</h1>
<p class="lede">More than 200 room impulse response datasets are public, each with its own host, licence, file format
and quality. RIRDB catalogues them, measures every IR with the same analyser, and lets you select, download and
verify IRs by licence, format, origin, quality and room.</p>
<div class="tiles">
  <div><b>{len(datasets)}</b><span>datasets catalogued</span></div>
  <div><b>{len(measured)}</b><span>measured, in the tables</span></div>
  <div><b>{len(irs):,}</b><span>IRs in the tables</span></div>
  <div><b>{irs['space'].nunique():,}</b><span>spaces</span></div>
</div>
<p><a href="datasets.html">Search the datasets</a> by format, licence, grade, space type and T30 ·
<a href="spaces.html">Search the spaces</a> and see their T30 and DRR</p>
<h2>Use it</h2>
<pre>pip install rirdb
rirdb search --format binaural --grade A     # what a selection holds and costs; nothing is downloaded
rirdb info sriracha                          # one dataset: licence, sizes, quality, what its cap leaves out
rirdb get --dataset rochester                # every file as published, each checked by its sha256
rirdb get --use research --format binaural --grade A --balance space --n 500</pre>
<p><code>get</code> writes one folder: the WAV files in the format you ask for (AmbiX for Ambisonics),
<code>irs.csv</code> with every measured value, <code>LICENSES.md</code>, <code>references.bib</code>, and
<code>selection.lock</code>, which rebuilds the same IRs anywhere. Every dataset is downloaded from its authors'
site; every file is checked against its sha256, and every IR against the hash of its samples.</p>
<h2>The IRs by origin and grade</h2>
<p class="muted">{e(GRADE_HELP)} One layout per space is counted.</p>
{table(["origin", *GRADES, "no grade"], split_rows, num=set(range(1, 8)))}
<p><a href="datasets.html">Every dataset</a> · <a href="spaces.html">Every space</a> ·
<a href="columns.html">Every column of the tables</a></p>
"""
    return page("RIRDB", body)


def datasets_page(irs: pd.DataFrame, datasets: pd.DataFrame, search: dict) -> str:
    measured = datasets[datasets["n_irs"].notna()].sort_values("dataset")
    prim = primary(irs)
    rows, attrs = [], []
    for _, d in measured.iterrows():
        i = d["dataset"]
        mine, mine_p = irs[irs["dataset"] == i], prim[prim["dataset"] == i]
        size, unknown = dataset_download_bytes(i)
        t30 = mine_p["rt_mid"].median()
        attrs.append(f'data-dataset="{e(i)}" data-text="{e((i + " " + str(d["name"])).lower())}"')
        rows.append([f'<a href="d/{e(i)}.html">{e(i)}</a>', e(d["name"]), (e(_year(d)), _year(d) or 0),
                     (f'{len(mine_p):,}', len(mine_p)), (f'{mine_p["space"].nunique():,}', mine_p["space"].nunique()),
                     e(", ".join(sorted(mine["format"].unique()))), e(d["licence"]),
                     (e(size_text(size, unknown)), size), (num(t30, 2, " s"), "" if pd.isna(t30) else round(t30, 3)),
                     (grade_bar(mine["grade"]), mine["grade"].isin(GRADES[:2]).mean().round(3))])
    rest = datasets[datasets["n_irs"].isna()].sort_values("dataset")
    later = [[e(d["dataset"]), _link(d["name"], d["homepage"]), (e(_year(d)), _year(d) or 0), e(d["licence"]),
              e(d["status"])] for _, d in rest.iterrows()]
    later_attrs = [f'data-text="{e((d["dataset"] + " " + str(d["name"])).lower())}"' for _, d in rest.iterrows()]
    body = f"""
<h1>Datasets</h1>
<p class="lede">{len(measured)} datasets are measured and in the tables of release {e(client.version())}. The other
{len(rest)} are catalogued: their licence and source are recorded, and they are measured in later releases.
Filter the measured ones by what you need; the IR and space counts follow the filters.</p>
{filters(irs)}
<h2>In the tables</h2>
{table(["dataset", "name", "year", "IRs", "spaces", "formats", "licence", "download", "median T30", "grades"],
       rows, num={2, 3, 4, 7, 8}, cls="sortable datasets", row_attrs=attrs)}
<p class="muted">IRs and spaces: those the filters keep. {PRIMARY_NOTE} Grades, left to right: S, A, B, C, D, none.
{e(GRADE_HELP)} Click a column's name to sort.</p>
<section id="catalogued">
<h2>Catalogued, not measured yet</h2>
{table(["dataset", "name", "year", "licence", "status"], later, num={2}, cls="sortable", row_attrs=later_attrs)}
</section>
{search_json(search)}
"""
    return page("Datasets · RIRDB", body)


def spaces_page(irs: pd.DataFrame, search: dict) -> str:
    spaces = search["spaces"]
    points = [{**s, "i": k, "href": f'd/{s["dataset"]}.html#{anchor(s["id"])}'}
              for k, s in enumerate(spaces) if s["t30"] is not None and s["drr"] is not None]
    rows = [[f'<a href="d/{e(s["dataset"])}.html#{anchor(s["id"])}">{e(s["label"])}</a>',
             f'<a href="d/{e(s["dataset"])}.html">{e(s["dataset"])}</a>', e(s["type"].replace("_", " ")),
             (f'{s["n"]:,}', s["n"]), (num(s["volume"], 0), s["volume"] if s["volume"] is not None else ""),
             (num(s["t30"], 2), s["t30"] if s["t30"] is not None else ""),
             (num(s["drr"], 1), s["drr"] if s["drr"] is not None else ""), e(", ".join(s["formats"])),
             e(s["licence"])] for s in spaces]
    attrs = [f'data-i="{k}"' for k in range(len(spaces))]
    body = f"""
<h1>Spaces</h1>
<p class="lede">The {len(spaces):,} spaces of the measured datasets: rooms, halls, outdoor sites, devices. Each dot
is one space, at the median T30 and median DRR of its IRs. Filter them; click a dot or a name for its dataset.</p>
{filters(irs, spaces_page=True)}
<figure class="wide">{charts.scatter(points, "Median T30 against median DRR, one dot per space")}
<figcaption class="muted">{len(points):,} spaces with both values. Median T30: rt_mid over the space's IRs
(T30 at 500 Hz and 1 kHz, or T20 where T30 is not valid). Median DRR: {e(MEANING["drr_db"])}. Values outside the
axes sit on its edge.</figcaption></figure>
{table(["space", "dataset", "type", "IRs", "volume m³", "median T30 s", "median DRR dB", "formats", "licence"],
       rows, num={3, 4, 5, 6}, cls="sortable", row_attrs=attrs)}
<p class="muted">IRs: those the filters keep. {PRIMARY_NOTE} Space type, T30 and volume are filters of this page;
the command line does not have them yet.</p>
{search_json(search)}
"""
    return page("Spaces · RIRDB", body)


def dataset_page(d: pd.Series, irs: pd.DataFrame, spaces: pd.DataFrame, own: pd.DataFrame | None) -> str:
    i = d["dataset"]
    mine = irs[irs["dataset"] == i]
    mine_p = primary(mine)
    size, unknown = dataset_download_bytes(i)
    cap = cap_of(i)
    facts = [
        ("Licence", f'{e(d["licence"])}: research {e(d["use_research"])}, commercial {e(d["use_commercial"])}, '
                    f'training {e(d["use_training"])}, redistribution {e(d["redistribute"])}'),
        ("Origin", e(d["origin"])),
        ("IRs", f'{len(mine):,} in the table, {int(d["n_irs_found"]):,} found in the files'
                + (f', {int(d["n_irs_stated"]):,} stated by the authors' if pd.notna(d["n_irs_stated"]) else "")),
        ("Download", e(size_text(size, unknown)) + ", every file as published"),
        ("Formats", e(", ".join(f"{f} {n:,}" for f, n in mine["format"].value_counts().items()))),
        ("Layouts", e(", ".join(f"{f} {n:,}" for f, n in mine["layout"].value_counts().items()))),
        ("Sample rates", e(", ".join(f"{int(r)} Hz" for r in sorted(mine["fs"].unique())))),
        ("Space types", e(", ".join(f"{t.replace('_', ' ')} {n:,}" for t, n in
                                    mine_p.groupby("space_type")["space"].nunique().sort_values(ascending=False).items()))
                        + " (spaces)"),
        ("Links", " · ".join(_link(t, u) for t, u in (("homepage", d["homepage"]),
                                                      ("DOI", f'https://doi.org/{d["doi"]}' if d["doi"] else ""),
                                                      ("paper", d["paper_url"])) if u)),
    ]
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts if v)
    cap_html = "".join(f"<p>{e(line.strip())}</p>" for line in cap.lines()) if cap else ""
    defects = {c.removeprefix("defect_").replace("_", " "): int(mine[c].fillna(False).sum())
               for c in mine if c.startswith("defect_")}
    defects_html = ", ".join(f"{e(k)} {n:,}" for k, n in defects.items() if n) or "none found"
    body = f"""
<h1>{e(d["name"])}</h1>
<p class="lede"><code>{e(i)}</code>{e(f" · {_year(d)}" if _year(d) else "")}</p>
<dl class="facts">{dl}</dl>
{f'<div class="note">{cap_html}</div>' if cap else ""}
<p class="jump"><a href="#acoustics">Acoustics</a> · <a href="#metadata">Metadata</a> · <a href="#quality">Quality</a>
· <a href="#spaces">Spaces</a></p>
<h2>Get it</h2>
<pre>rirdb info {e(i)}
rirdb get --dataset {e(i)}            # every file as published, verified</pre>
{acoustics_section(mine_p, bool(cap))}
{metadata_section(mine_p, own)}
<h2 id="quality">Quality{" of the sampled IRs" if cap else ""}</h2>
<p>{grade_bar(mine["grade"])}</p>
<p class="muted">{e(", ".join(f"{g} {n:,}" for g, n in mine["grade"].replace("", "none").value_counts().items()))}.
Defects: {defects_html}. {e(GRADE_HELP)}</p>
{f'<h2>Known issues</h2><p>{e(d["known_issues"])}</p>' if d["known_issues"] else ""}
{spaces_section(i, mine, mine_p, spaces)}
"""
    return page(f"{d['name']} · RIRDB", body, depth=1)


def acoustics_section(rows: pd.DataFrame, capped: bool) -> str:
    a = acoustics(rows)
    t30_ticks, db_ticks = [0.1, 0.3, 1, 3, 10], [-20, -10, 0, 10, 20, 30]
    db = lambda v: f"{v:+g}" if v else "0"

    def figure(title, n, what, chart, numbers, counted="IRs with a value"):
        if not n:
            return f'<figure><figcaption><b>{title}</b><span>No valid value in this dataset.</span></figcaption></figure>'
        return (f'<figure><figcaption><b>{title}</b><span>{what} {n:,} {counted}.</span></figcaption>{chart}'
                f'<details><summary>The numbers</summary>{numbers}</details></figure>')

    def bins_table(counts, edges, unit, fmt):
        last = len(counts) - 1
        rows = [[("below " if k == 0 else "") + (fmt(edges[k + 1]) if k == 0 else fmt(edges[k]) + (
                 " or more" if k == last else f" to {fmt(edges[k + 1])}")) + f" {unit}", f"{n:,}"]
                for k, n in enumerate(counts) if n]
        return table(["range", "IRs"], rows, num={1})

    band_rows = [[f"{b:g} Hz" if b < 1000 else f"{b / 1000:g} kHz", f'{s["n"]:,}',
                  *[f'{s[p]:.2f}' for p in ("p5", "p25", "p50", "p75", "p95")]]
                 for b, s in a["bands"].items() if s]
    figures = [
        figure("Reverberation time (T30)", a["t30_n"], e(MEANING["rt_mid"]) + ".",
               charts.histogram(a["t30"], T30_EDGES, "s", "T30 of the IRs", t30_ticks, log=True),
               bins_table(a["t30"], T30_EDGES, "s", lambda v: f"{v:g}")),
        figure("T30 per octave", sum(s["n"] for s in a["bands"].values() if s),
               "Box: half of the IRs; line: median; whiskers: 5th to 95th percentile.",
               charts.band_boxes(a["bands"], "T30 per octave band"),
               table(["octave", "IRs", "5 %", "25 %", "median", "75 %", "95 %"], band_rows, num=set(range(1, 7))),
               counted="valid octave values over all bands"),
        figure("Direct-to-reverberant ratio (DRR)", a["drr_n"], e(MEANING["drr_db"]) + ".",
               charts.histogram(a["drr"], DB_EDGES, "dB", "DRR of the IRs", db_ticks, fmt=db),
               bins_table(a["drr"], DB_EDGES, "dB", db)),
        figure("Clarity (C50)", a["c50_n"], e(MEANING["c50_mid"]) + ".",
               charts.histogram(a["c50"], DB_EDGES, "dB", "C50 of the IRs", db_ticks, fmt=db),
               bins_table(a["c50"], DB_EDGES, "dB", db)),
    ]
    names = {"rt_mid": ("T30 (rt_mid)", "s"), "edt_mid": ("EDT", "s"), "drr_db": ("DRR", "dB"),
             "c50_mid": ("C50", "dB"), "sti": ("STI", ""), "distance_m": ("distance", "m"),
             "volume_m3": ("volume", "m³")}
    ranges = [[e(names[c][0]), f'{s["n"]:,}', *[f'{s[p]:,.2f}' for p in ("p5", "p50", "p95")], e(names[c][1])]
              for c, s in a["ranges"].items() if s]
    return f"""
<h2 id="acoustics">Acoustics{" of the sampled IRs" if capped else ""}</h2>
<p class="muted">Over the {len(rows):,} IRs of the dataset. {PRIMARY_NOTE} Every dataset page uses the same axes;
a value outside an axis counts at its edge.</p>
<div class="charts">{"".join(figures)}</div>
{table(["value", "IRs", "5th percentile", "median", "95th percentile", "unit"], ranges, num={1, 2, 3, 4}) if ranges else ""}
"""


def metadata_section(rows: pd.DataFrame, own: pd.DataFrame | None) -> str:
    """Which metadata the dataset fills, and for what share of its IRs: what a user can filter or train on."""
    parts, group = [], None
    for m in metadata(rows, own):
        if m["group"] != group:
            group = m["group"]
            parts.append(f'<tr class="group"><th colspan="3" scope="colgroup">{e(group)}</th></tr>')
        share = m["n"] / m["of"] if m["of"] else 0
        cols = ", ".join(f"<code>{e(c)}</code>" for c in m["columns"])
        count = "all" if m["n"] == m["of"] and m["n"] else "none" if not m["n"] else f'{m["n"]:,} of {m["of"]:,}'
        parts.append(f'<tr{" class=empty" if not m["n"] else ""}><td>{e(m["label"])}'
                     f'{f"<small>{cols}</small>" if cols else ""}</td>'
                     f'<td><span class="meter" role="img" aria-label="{share:.0%}"><i style="width:{100 * share:.1f}%">'
                     f'</i></span></td><td class="n">{count}</td></tr>')
    own_note = ("Its own fields are kept as the dataset publishes them, in the table "
                "<code>source_meta</code>." if own is not None else "")
    return f"""
<h2 id="metadata">Metadata</h2>
<p class="muted">What the dataset tells about each IR, and for how many of its IRs: what you can filter on, group by
or learn from. A measured value counts only where it is valid. {own_note} The meaning of each column:
<a href="../columns.html">Columns</a>.</p>
<div class="box"><table class="coverage"><tbody>{"".join(parts)}</tbody></table></div>
"""


def spaces_section(dataset: str, mine: pd.DataFrame, mine_p: pd.DataFrame, spaces: pd.DataFrame) -> str:
    sp = spaces[(spaces["dataset"] == dataset) & (spaces["n_irs"] > 0)].sort_values("space")
    curves = space_bands(mine_p)
    t30 = mine_p.groupby("space")["rt_mid"].median()
    names = [f"{b:g} Hz" if b < 1000 else f"{b / 1000:g} kHz" for b in BANDS]
    rows, attrs = [], []
    for _, s in sp.iterrows():
        k = s["space"]
        values = [None if pd.isna(v) else float(v) for v in curves.loc[k]] if k in curves.index else []
        m = t30.get(k)
        rows.append([e(s["label"] or k), e(s["space_type"].replace("_", " ")), (f'{int(s["n_irs"]):,}', int(s["n_irs"])),
                     (num(s["volume_m3"], 0), "" if pd.isna(s["volume_m3"]) else s["volume_m3"]),
                     (num(m, 2), "" if m is None or pd.isna(m) else round(m, 3)),
                     charts.band_curve(values, names) if values else "", e(s["licence"])])
        attrs.append(f'id="{anchor(k)}"')
    return f"""
<h2 id="spaces">Spaces</h2>
<p class="muted">The curve: median T30 per octave, 63 Hz to 8 kHz, on the same scale for every space (0.1 to 10 s,
log; the line marks 1 s). A gap: no valid T30 in that octave.</p>
{table(["space", "type", "IRs", "volume m³", "median T30 s", "T30 per octave", "licence"], rows, num={2, 3, 4},
       cls="sortable", row_attrs=attrs)}
"""


def columns_page() -> str:
    parts = []
    for name in dict.fromkeys(r["table"] for r in COLUMNS):
        mine = [r for r in COLUMNS if r["table"] == name]
        allowed = lambda r: r["allowed"].replace("|", ", ") or (f'{r["min"]} to {r["max"]}' if r["min"] or r["max"] else "")
        parts.append(f'<h2 id="{e(name)}">{e(name)}</h2>' + table(
            ["column", "type", "unit", "values", "meaning", "empty means"],
            [[f"<code>{e(r['name'])}</code>", e(r["type"]), e(r["unit"]), e(allowed(r)), e(r["meaning"]),
              e(r["empty_means"])] for r in mine]))
    body = (f'<h1>Columns</h1><p class="lede">Every column of the release tables: what it means, its values, and what an '
            f'empty cell means. A value is published only where it is valid; otherwise the cell is empty.</p>{"".join(parts)}')
    return page("Columns · RIRDB", body)


def _year(d: pd.Series) -> str:
    return str(int(d["year"])) if pd.notna(d["year"]) else ""


def _link(text: str, url: str) -> str:
    return f'<a href="{e(url)}">{e(text)}</a>' if url else e(text)


assert all(m[2][0] in MEANING for m in METADATA), "every metadata column is a column of irs (columns.csv)"
