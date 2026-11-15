"""The public website, built from the release tables alone (so GitHub Actions can build it from the release).

    rirdb dev site --out site/      index.html, datasets.html, d/<dataset>.html, columns.html, style.css

Plain HTML and one stylesheet: no JavaScript, no template engine, no data that is not in the release.
"""

from __future__ import annotations

import csv
import html
from pathlib import Path

import pandas as pd

from rirdb import __version__, client
from rirdb.select import GRADES, cap_of, dataset_download_bytes, grade_split, human, plural, size_text

REPO = "https://github.com/ilias-audio/rirdb"
PYPI = "https://pypi.org/project/rirdb/"
GRADE_HELP = ("S and A: T30 valid in every octave from 125 Hz to 4 kHz (S: no defect at all, every defect checked). "
              "B: T20 or T30 valid from 250 Hz to 2 kHz. C: some decay value valid, or a major defect. D: none valid. "
              "No grade: an anechoic space.")


def e(x) -> str:
    """Text for HTML; an empty or missing value is an empty string."""
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) or x is pd.NA else html.escape(str(x))


def page(title: str, body: str, depth: int = 0) -> str:
    up = "../" * depth
    nav = (f'<a href="{up}index.html">RIRDB</a><a href="{up}datasets.html">Datasets</a>'
           f'<a href="{up}columns.html">Columns</a><a href="{REPO}">GitHub</a><a href="{PYPI}">PyPI</a>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{e(title)}</title><link rel="stylesheet" href="{up}style.css"></head>'
            f'<body><header><nav>{nav}</nav></header><main>{body}</main>'
            f'<footer>RIRDB {e(__version__)}, tables {e(client.version())}. The audio stays with its authors and keeps its '
            f'licence; the catalogue and the tables are CC BY 4.0, the code MIT. A preview: ids, rows and columns may '
            f'change before 1.0.</footer></body></html>\n')


def table(head: list[str], rows: list[list[str]], num: set[int] = frozenset()) -> str:
    """An HTML table; cells are given as HTML, the columns in `num` right-aligned."""
    th = "".join(f'<th{" class=n" if i in num else ""}>{h}</th>' for i, h in enumerate(head))
    tr = "".join("<tr>" + "".join(f'<td{" class=n" if i in num else ""}>{c}</td>' for i, c in enumerate(r)) + "</tr>"
                 for r in rows)
    return f'<div class="box"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


def grade_bar(grades: pd.Series) -> str:
    """One bar, a segment per grade, its width the share of IRs."""
    counts = grades.replace("", "none").fillna("none").value_counts()
    total = counts.sum()
    parts = "".join(f'<i class="g{g}" style="width:{100 * counts.get(g, 0) / total:.2f}%" '
                    f'title="{g}: {counts.get(g, 0):,}"></i>' for g in [*GRADES, "none"] if counts.get(g, 0))
    return f'<span class="bar" role="img" aria-label="grades">{parts}</span>'


# ------------------------------------------------------------------------------------------- the pages

def home(irs: pd.DataFrame, datasets: pd.DataFrame) -> str:
    measured = datasets[datasets["n_irs"].notna()]
    split = grade_split(irs[irs["is_primary"]])
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
<p><a href="datasets.html">Every dataset</a> · <a href="columns.html">Every column of the tables</a></p>
"""
    return page("RIRDB", body)


def datasets_page(irs: pd.DataFrame, datasets: pd.DataFrame) -> str:
    measured = datasets[datasets["n_irs"].notna()].sort_values("dataset")
    rows = []
    for _, d in measured.iterrows():
        mine = irs[irs["dataset"] == d["dataset"]]
        size, unknown = dataset_download_bytes(d["dataset"])
        rows.append([f'<a href="d/{e(d["dataset"])}.html">{e(d["dataset"])}</a>', e(d["name"]), e(_year(d)),
                     f'{int(d["n_irs"]):,}', f'{mine["space"].nunique():,}',
                     e(", ".join(sorted(mine["format"].unique()))), e(d["licence"]), e(size_text(size, unknown)),
                     grade_bar(mine["grade"])])
    rest = datasets[datasets["n_irs"].isna()].sort_values("dataset")
    later = [[e(d["dataset"]), _link(d["name"], d["homepage"]), e(_year(d)), e(d["licence"]), e(d["status"])]
             for _, d in rest.iterrows()]
    body = f"""
<h1>Datasets</h1>
<p class="lede">{len(measured)} datasets are measured and in the tables of release {e(client.version())}. The other
{len(rest)} are catalogued: their licence and source are recorded, and they are measured in later releases.</p>
<h2>In the tables</h2>
{table(["dataset", "name", "year", "IRs", "spaces", "formats", "licence", "download", "grades"], rows, num={3, 4, 7})}
<p class="muted">Grades, left to right: S, A, B, C, D, none. {e(GRADE_HELP)}</p>
<h2>Catalogued, not measured yet</h2>
{table(["dataset", "name", "year", "licence", "status"], later)}
"""
    return page("Datasets · RIRDB", body)


def dataset_page(d: pd.Series, irs: pd.DataFrame, spaces: pd.DataFrame) -> str:
    i = d["dataset"]
    mine = irs[irs["dataset"] == i]
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
        ("Sample rates", e(", ".join(f"{int(r)} Hz" for r in sorted(mine["fs"].unique())))),
        ("Links", " · ".join(x for x in (_link("homepage", d["homepage"]),
                                           _link("DOI", f'https://doi.org/{d["doi"]}' if d["doi"] else ""),
                                           _link("paper", d["paper_url"])) if x)),
    ]
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts if v)
    cap_html = "".join(f"<p>{e(line.strip())}</p>" for line in cap.lines()) if cap else ""
    ranges = []
    for label, col, unit in (("rt_mid", "rt_mid", "s"), ("DRR", "drr_db", "dB"), ("distance", "distance_m", "m")):
        v = mine[col].dropna()
        if len(v):
            ranges.append([e(label), f"{v.quantile(0.05):.2f}", f"{v.median():.2f}", f"{v.quantile(0.95):.2f}", unit])
    sp = spaces[spaces["dataset"] == i].sort_values("space")
    sp_rows = [[e(s["label"] or s["space"]), e(s["space_type"]), e(s["licence"]), f'{int(s["n_irs"]):,}']
               for _, s in sp.iterrows() if s["n_irs"]]
    defects = {c.removeprefix("defect_").replace("_", " "): int(mine[c].fillna(False).sum())
               for c in mine if c.startswith("defect_")}
    defects_html = ", ".join(f"{e(k)} {n:,}" for k, n in defects.items() if n) or "none found"
    body = f"""
<h1>{e(d["name"])}</h1>
<p class="lede"><code>{e(i)}</code>{e(f" · {_year(d)}" if _year(d) else "")}</p>
<dl class="facts">{dl}</dl>
{f'<div class="note">{cap_html}</div>' if cap else ""}
<h2>Get it</h2>
<pre>rirdb info {e(i)}
rirdb get --dataset {e(i)}            # every file as published, verified</pre>
<h2>Quality{" of the sampled IRs" if cap else ""}</h2>
<p>{grade_bar(mine["grade"])}</p>
<p class="muted">{e(", ".join(f"{g} {n:,}" for g, n in mine["grade"].replace("", "none").value_counts().items()))}.
Defects: {defects_html}.</p>
{"<h2>Ranges</h2>" + table(["value", "5th percentile", "median", "95th percentile", "unit"], ranges, num={1, 2, 3}) if ranges else ""}
{f'<h2>Known issues</h2><p>{e(d["known_issues"])}</p>' if d["known_issues"] else ""}
<h2>Spaces</h2>
{table(["space", "type", "licence", "IRs"], sp_rows, num={3})}
"""
    return page(f"{d['name']} · RIRDB", body, depth=1)


def columns_page() -> str:
    with open(Path(__file__).with_name("columns.csv"), newline="") as f:
        rows = list(csv.DictReader(f))
    parts = []
    for name in dict.fromkeys(r["table"] for r in rows):
        mine = [r for r in rows if r["table"] == name]
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


# ------------------------------------------------------------------------------------------- the build

def build(out: Path) -> Path:
    irs, datasets, spaces = client.table("irs"), client.table("datasets"), client.table("spaces")
    (out / "d").mkdir(parents=True, exist_ok=True)
    (out / "style.css").write_text(STYLE)
    (out / "index.html").write_text(home(irs, datasets))
    (out / "datasets.html").write_text(datasets_page(irs, datasets))
    (out / "columns.html").write_text(columns_page())
    for _, d in datasets[datasets["n_irs"].notna()].iterrows():
        (out / "d" / f"{d['dataset']}.html").write_text(dataset_page(d, irs, spaces))
    (out / ".nojekyll").write_text("")
    return out


STYLE = """
:root { --bg: #f6f8f8; --panel: #fff; --fg: #18211f; --muted: #5a6866; --line: #d9e1df; --accent: #0f6e66;
  --gS: #0b6b3a; --gA: #3f9a52; --gB: #3b7fae; --gC: #d29a2c; --gD: #c0473b; --gnone: #9aa6a4; color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { --bg: #111716; --panel: #182120; --fg: #e3ecea; --muted: #9fb0ad;
  --line: #2b3836; --accent: #4fc1b5; --gS: #3fbf7a; --gA: #7cc98a; --gB: #6eaee0; --gC: #e7b75a; --gD: #ec7d71;
  --gnone: #6d7c7a; color-scheme: dark; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
header, main, footer { max-width: 1100px; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left));
  padding-right: max(16px, env(safe-area-inset-right)); }
header nav { display: flex; flex-wrap: wrap; gap: 18px; padding-block: 16px; border-bottom: 1px solid var(--line); }
header nav a { color: var(--fg); text-decoration: none; font-weight: 600; }
header nav a:first-child { color: var(--accent); }
main { padding-block: 8px 48px; display: grid; gap: 14px; }
footer { color: var(--muted); font-size: .85rem; padding-block: 24px; border-top: 1px solid var(--line); }
h1, h2 { line-height: 1.2; text-wrap: balance; margin: 18px 0 0; } h1 { font-size: 1.9rem; } h2 { font-size: 1.3rem; }
p { margin: 0; max-width: 78ch; } .lede, .muted { color: var(--muted); } a { color: var(--accent); }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .88em; }
pre { margin: 0; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; overflow-x: auto; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; }
.tiles div { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; display: grid; }
.tiles b { font-size: 1.6rem; font-variant-numeric: tabular-nums; } .tiles span { color: var(--muted); font-size: .9rem; }
.box { overflow-x: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: .76rem; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.bar { display: inline-flex; width: 140px; height: 12px; border-radius: 3px; overflow: hidden; background: var(--line); vertical-align: middle; }
.bar i { display: block; height: 100%; }
.gS { background: var(--gS); } .gA { background: var(--gA); } .gB { background: var(--gB); } .gC { background: var(--gC); }
.gD { background: var(--gD); } .gnone { background: var(--gnone); }
dl.facts { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin: 0; }
dl.facts dt { color: var(--muted); } dl.facts dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.note { background: var(--panel); border: 1px solid var(--line); border-left: 4px solid var(--accent); border-radius: 6px;
  padding: 10px 14px; display: grid; gap: 6px; }
@media (max-width: 560px) { dl.facts { grid-template-columns: 1fr; } dl.facts dt { margin-top: 6px; } }
"""
