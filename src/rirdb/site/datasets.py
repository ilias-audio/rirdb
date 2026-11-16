"""The datasets page (the search) and the page of each dataset, measured or not."""

from __future__ import annotations

import pandas as pd

from rirdb.select import cap_of
from rirdb.site import charts, words
from rirdb.site.data import (BANDS, DB_EDGES, ARCHIVE_LIMITS, MANY_ROOMS, T30_EDGES, acoustics, histogram, labels, own_fields,
                             primary, shown_space, space_bands, text)
from rirdb.site.layout import (ICONS, copybox, e, grade_bar, grade_legend, page, pill, table, use_marks)

PER_PAGE = 30


# ------------------------------------------------------------------------------------------- the search

def facet(name: str, title: str, options: list[tuple[str, str]], multi: bool = False, hint: str = "",
          show: int | None = None, any_label: str = "Any", counts: bool = True, outside: bool = False) -> str:
    """A group of filters: radio buttons (one choice, "Any" first) or tick boxes (several). Its name is the
    address parameter and, where the command line has it, the option of `rirdb search`. With `show`, the options
    after the first `show` fold away under "more". `outside`: the inputs sit outside the filters form (in the What
    you get view) and belong to it by their form attribute."""
    kind = "checkbox" if multi else "radio"
    opts = [] if multi else [(f'{name}-any', "", any_label)]
    opts += [(f"{name}-{v}", v, label) for v, label in options]
    owner = ' form="filters"' if outside else ""
    labels = [
        f'<label class="opt" for="{e(i)}"><input type="{kind}" id="{e(i)}" name="{e(name)}" value="{e(v)}"{owner}'
        f'{" checked" if not multi and not v else ""}><span>{e(label)}</span>'
        + (f'<span class="c" data-for="{e(i)}"></span>' if counts else "") + "</label>"
        for i, v, label in opts]
    if show is not None and len(labels) > show + 2:            # fold the rest away
        more = f"Show all {len(labels)}" if show == 0 else f"{len(labels) - show} more"
        labels = labels[:show] + [f'<details class="more-opts"><summary>{more}</summary>'
                                  + "".join(labels[show:]) + "</details>"]
    rows = "".join(labels)
    return (f'<fieldset class="facet"><legend>{e(title)}{f" <small>{e(hint)}</small>" if hint else ""}</legend>'
            f'{rows}</fieldset>')


def room_types(spaces: list[dict]) -> list[str]:
    """The room types of the search, the most common first."""
    count = {}
    for s in spaces:
        count[s["type"]] = count.get(s["type"], 0) + 1
    return sorted((t for t in count if t != "unknown"), key=lambda t: (-count[t], t))


def archive_facet() -> str:
    """The largest download (`--max-archive`): a filter, since it leaves out the IRs of bigger downloads."""
    return facet("archive", "Largest download", [(k, k.replace("MB", " MB").replace("GB", " GB")) for k in ARCHIVE_LIMITS],
                 hint="each is taken whole")


def year_facet(datasets: list[dict]) -> str:
    """The years of publication, from and to (`--year`), under a small chart of the datasets of each year that the
    other filters keep (site.js draws it)."""
    known = [d["year"] for d in datasets if d["year"]]
    years = range(max(known), min(known) - 1, -1) if known else []         # every year, so any range can be chosen

    def choose(name: str, label: str) -> str:
        opts = '<option value="">any</option>' + "".join(f'<option value="{y}">{y}</option>' for y in years)
        return f'<label class="yr">{label} <select name="{name}" id="{name}">{opts}</select></label>'
    return (f'<fieldset class="facet"><legend>Published</legend><div class="yearbars" id="yearbars" aria-hidden="true">'
            f'</div><div class="yearaxis" aria-hidden="true"><span id="ylo"></span><span id="yhi"></span></div>'
            f'<div class="yrs">{choose("since", "From")}{choose("until", "to")}</div></fieldset>')


def get_fields() -> str:
    """The options of `rirdb get` that shape how many IRs you get, next to what they do (the What you get view):
    at most so many per room, a random draw and what it spreads over. The command box prints them with the
    filters (site.js)."""
    def number(name: str, title: str, hint: str, placeholder: str) -> str:
        return (f'<fieldset class="facet"><legend><label for="{name}">{e(title)}</label> <small>{e(hint)}</small></legend>'
                f'<input class="numin" type="number" min="1" step="1" inputmode="numeric" id="{name}" name="{name}" '
                f'form="filters" placeholder="{e(placeholder)}"></fieldset>')
    return ('<div class="getctl">'
            + number("per", "At most per room", "spread over its reverb", "no limit")
            + number("n", "How many IRs", "a random draw", "all")
            + facet("balance", "Spread the draw over", [("space", "Rooms"), ("dataset", "Datasets"),
                                                        ("t30", "Reverberation times")], any_label="Every IR alike",
                    counts=False, outside=True)
            + "</div>")


def get_view() -> str:
    """What you get: the options that shape the amount, four numbers, the charts of the IRs the command selects
    (each over a grey outline of everything the filters match, before the per-room cap and the draw), the command,
    and the rooms you get. site.js fills it."""
    def chart(key: str, title: str, what: str, wide: bool = False) -> str:
        return (f'<figure class="getchart{" wide" if wide else ""}" id="ch-{key}"><figcaption><b>{title}</b>'
                f'<span>{what}</span></figcaption><div class="chartbox"></div></figure>')
    return (f'<section class="tabpanel getview" id="get" role="tabpanel" aria-labelledby="tab-get" hidden>'
            f'{get_fields()}<div class="stats" id="stats" aria-live="polite"></div>'
            f'<p class="note warn" id="defects" hidden></p>'
            f'<div class="getcharts">'
            + chart("t30", "Reverberation time", "T30 at 500 Hz and 1 kHz of the IRs you get; the grey outline is every "
                    "IR the filters match, before the per-room cap and the draw.")
            + chart("per", "IRs per room", "How many IRs each room gives, before the draw; grey: without the per-room "
                    "cap.")
            + chart("ds", "IRs per dataset", "The datasets that give the most, and how many their IRs were before the "
                    "per-room cap and the draw.")
            + chart("types", "Rooms by type", "The rooms you get, by room type; in a draw, about how many.")
            + chart("rooms", "Rooms by reverberation time and DRR", "Each dot is a room (a room type, for a dataset of "
                    "thousands of generated rooms) at the median of its IRs; those the command cannot take IRs from are faded. DRR mostly reflects where the microphones were.",
                    wide=True)
            + f'</div>{command_box()}'
            f'<div class="getrooms"><h3>Rooms you get</h3><p class="small quiet" id="roomsnote"></p>'
            f'<ul class="roomlist" id="roomlist"></ul><button type="button" class="more" id="moreroom" hidden></button>'
            f'</div></section>')


def command_box() -> str:
    """The command that selects what the page keeps: look first, get it, or the same in Python (site.js)."""
    def block(key: str, title: str, hint: str) -> str:
        return (f'<div class="cmdpart"><p class="small"><b>{title}</b> <span class="quiet">{hint}</span></p>'
                f'<div class="copybox"><pre id="cmd-{key}"></pre><button type="button" data-copy>Copy</button></div></div>')
    return ('<section id="cmd" class="cmd" aria-label="Your command" hidden><h2 class="cmdhead">Your command</h2>'
            '<p id="cmdsum" class="small" aria-live="polite"></p>'
            + block("search", "Look first", "nothing is downloaded")
            + block("get", "Get it", "downloads, checks every file, writes one folder")
            + block("py", "In Python", "the same selection")
            + '<p class="note" id="cmdnote" hidden></p></section>')


def card(d: dict, i: int) -> str:
    """One result: what the dataset is, who made it, what it holds, what its licence allows."""
    measured = d["status"] == "measured"
    status = pill("Measured by RIRDB", "measured") if measured else pill("Not measured yet")
    fmts = "".join(pill(words.FORMATS.get(f, f)) for f in d["formats"])
    by = " · ".join(e(x) for x in (d["by"], d["year"]) if x)
    if measured:
        side = (f'<span class="num"><span class="count">{d["n"]:,} IRs</span> · {d["rooms"]} '
                f'{"room" if d["rooms"] == 1 else "rooms"}</span>{charts.mini_hist(d["t30"])}'
                + (f'<span>T30 median {d["t30_median"]:g} s</span>' if d["t30_median"] else "") + grade_bar(d["grades"]))
    else:
        prov = ", ".join(words.PROVIDES_SHORT[p] for p in d["provides"])
        side = (f'<span>{e(d["size"]) if d["size"] else "Size not stated"}</span>'
                + (f'<span>Provides {e(prov)}</span>' if prov else ""))
    picks = (f'<div class="picks"><button type="button" class="pick" data-pick="dataset" data-id="{e(d["id"])}" '
             f'aria-pressed="false">Pick</button><button type="button" class="pick" data-pick="exclude" '
             f'data-id="{e(d["id"])}" aria-pressed="false">Leave out</button></div>') if measured else ""
    defect = pill("Known defect", "defect") if d["defect"] else ""
    return (f'<article class="card item" {sort_keys(d, i)}><div class="main"><h3><a href="d/{e(d["id"])}.html">'
            f'{e(d["name"])}</a></h3><p class="by">{by}</p><p class="desc">{e(d["description"])}</p>'
            f'<div class="meta">{status}{defect}{fmts}{pill(words.licence_short(d["licence"]))}</div>'
            f'{use_marks(d["uses"])}{picks}</div><div class="side">{side}</div></article>')


def sort_keys(d: dict, i: int) -> str:
    """What site.js sorts and filters a card or a table row by."""
    return (f'data-d="{i}" data-n="{d["n"]}" data-rooms="{d["rooms"]}" data-year="{d["year"] or 0}" '
            f'data-measured="{int(d["status"] == "measured")}"')


def table_row(d: dict, i: int) -> str:
    """One dataset as a row of the table view: what to compare at a glance."""
    measured = d["status"] == "measured"
    picks = (f'<button type="button" class="pick" data-pick="dataset" data-id="{e(d["id"])}" aria-pressed="false">Pick'
             f'</button><button type="button" class="pick" data-pick="exclude" data-id="{e(d["id"])}" '
             f'aria-pressed="false">Leave out</button>') if measured else ""
    defect = pill("Known defect", "defect") if d["defect"] else ""
    cells = [
        f'<a href="d/{e(d["id"])}.html">{e(d["name"])}</a>{defect}<div class="faint small">{e(d["by"])}</div>',
        e(d["year"] or ""), e(words.ORIGINS.get(d["origin"], d["origin"])),
        e(", ".join(words.FORMATS.get(f, f) for f in d["formats"])),
        f'<span class="count">{d["n"]:,}</span>' if measured else '<span class="faint">not measured</span>',
        f'{d["rooms"]:,}' if measured else "", f'{d["t30_median"]:g} s' if d["t30_median"] else "",
        e(d["size"]), e(words.licence_short(d["licence"])), f'<div class="picks">{picks}</div>']
    num = {4, 5, 6}
    return (f'<tr class="dsrow item" {sort_keys(d, i)}>'
            + "".join(f'<td{" class=num" if k in num else ""}>{c}</td>' for k, c in enumerate(cells)) + "</tr>")


def table_view(ds: list[dict]) -> str:
    head = [("name", "Dataset"), ("year", "Year"), ("", "Kind"), ("", "Formats"), ("n", "IRs"), ("rooms", "Rooms"),
            ("", "T30 median"), ("", "Download"), ("", "Licence"), ("", "")]
    th = "".join(f'<th{" class=num" if k in (4, 5, 6) else ""}>'
                 + (f'<button type="button" class="sortby" data-sort="{s}">{t}</button>' if s else t) + "</th>"
                 for k, (s, t) in enumerate(head))
    rows = "".join(table_row(d, i) for i, d in enumerate(ds))
    return (f'<div class="scroll" id="tablebox" hidden><table class="plain dstable"><thead><tr>{th}</tr></thead>'
            f'<tbody id="rows">{rows}</tbody></table></div>')


def datasets_page(search: dict) -> str:
    ds = search["datasets"]
    n_measured = sum(d["status"] == "measured" for d in ds)
    types = room_types(search["spaces"])
    facets = "".join([
        facet("status", "Measured by RIRDB", [("measured", "Measured"), ("not", "Not measured yet")]),
        facet("kind", "Kind of space", list(words.KINDS.items()), multi=True),
        facet("format", "Format", [(f, words.FORMATS[f]) for f in words.SEARCH_FORMATS]),
        facet("use", "The licence allows", [(u, words.USES[u]) for u in ("commercial", "training", "redistribute")]),
        facet("provides", "The authors provide", list(words.PROVIDES.items()), multi=True, hint="all ticked"),
        facet("t30", "Reverberation time", [(k, label) for k, label, _, _ in words.REVERB], multi=True,
              hint="measured only"),
        facet("type", "Room type", [(t, words.ROOM_TYPES.get(t, t)) for t in types], multi=True, hint="measured only",
              show=0),
        facet("grade", "Decay quality", [("S", "S"), ("A", "A or better"), ("B", "B or better"), ("C", "C or better")],
              hint="measured only"),
        archive_facet(),
        year_facet(ds),
    ])
    presets = [("kind=indoor&tab=get", "Real rooms only"), ("format=binaural&use=commercial", "Binaural, commercial use allowed"),
               ("dataset=arni&per=50&tab=get", "50 ARNI IRs, dry to live"),
               ("format=mono&per=20&archive=2GB&tab=get", "Mono, 20 per room, small downloads"),
               ("t30=hall&t30=live", "Halls and churches"), ("since=2024", "Published since 2024"),
               ("provides=positions", "With source and receiver positions")]
    body = f"""
<div class="wrap page">
<div class="page-head"><h1>Datasets</h1>
<p class="lead">Every public room impulse response dataset we know of: {len(ds)} in all. {n_measured} are measured by
RIRDB; the others are described from their authors' pages and measured in later releases. Filter them, pick the ones
you want, and see what you get: the page writes the command.</p></div>
<div class="find">
<form class="facets" id="filters" aria-label="Filters">{facets}
<button type="reset" class="clear">Clear all filters</button></form>
<div class="results">
<div class="searchbar"><input id="text" name="text" form="filters" type="search" autocomplete="off"
 placeholder="Search names, descriptions, institutions, rooms: church, Eigenmike, York…" aria-label="Search the datasets"></div>
<button type="button" class="filterbtn" aria-controls="filters" aria-expanded="false">Filters</button>
<p class="presets"><span>Try</span>{"".join(f'<a href="?{q}">{e(t)}</a>' for q, t in presets)}</p>
<div class="chips" id="chips"></div>
<div class="tabs" role="tablist" aria-label="Views" hidden><button type="button" role="tab" id="tab-list" aria-controls="list"
 aria-selected="true" data-tab="">Datasets <span class="tabn" id="n-list"></span></button><button type="button"
 role="tab" id="tab-get" aria-controls="get" aria-selected="false" data-tab="get">What you get <span class="tabn"
 id="n-get"></span></button></div>
<input type="hidden" name="tab" id="tabview" form="filters">
<section class="tabpanel" id="list" role="tabpanel" aria-labelledby="tab-list">
<div class="resultline"><p id="result" aria-live="polite"><b>{len(ds)}</b> datasets</p>
<div class="listctl"><label for="sort">Sort <select id="sort" name="sort" form="filters"><option value="">Measured first</option>
<option value="n">Most IRs</option><option value="rooms">Most rooms</option><option value="year">Newest</option>
<option value="name">Name</option></select></label><span class="seg" role="group" aria-label="Show as"><button type="button"
 data-list="" aria-pressed="true">Cards</button><button type="button" data-list="table" aria-pressed="false">Table</button></span>
<input type="hidden" name="list" id="listview" form="filters"></div></div>
<p class="note" id="leftout" hidden></p>
<div class="cards" id="cards">{"".join(card(d, i) for i, d in enumerate(ds))}</div>
{table_view(ds)}
<button type="button" class="more" id="more" hidden></button>
</section>
{get_view()}
</div></div></div>"""
    return page("Datasets · RIRDB", body, current="datasets.html", scripts=("search-data.js",))


# ------------------------------------------------------------------------------------------- one dataset

def provides_grid(d: pd.Series) -> str:
    cells = []
    for k, label in words.PROVIDES.items():
        v = d[f"provides_{k}"] or ""
        icon, cls = {"yes": ("yes", "yes"), "partial": ("partial", "part"), "no": ("no", "no")}.get(v, ("unknown", "faint"))
        cells.append(f'<div><span class="{cls}">{ICONS[icon]}</span><span>{e(label)} '
                     f'<span class="faint">· {words.AVAIL[v]}</span></span></div>')
    return f'<div class="provides">{"".join(cells)}</div>'


def as_room_types(rows_p: pd.DataFrame, spaces: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A dataset of more than MANY_ROOMS rooms as one row per room type (data.shown_space), its volume range noted."""
    shown = shown_space(spaces)
    rows_p = rows_p.assign(space=rows_p["space"].map(dict(zip(spaces["space"], shown))))
    g = spaces.assign(space=shown).groupby("space")
    vol = g["volume_m3"].agg(["min", "max"])
    note = [(f"volumes {lo:,.0f} to {hi:,.0f} m³; " if pd.notna(lo) else "") + "every room is a row of the spaces table"
            for lo, hi in zip(vol["min"], vol["max"])]
    out = g.first().assign(label=g.size().map("{:,} rooms".format), n_irs=g["n_irs"].sum(), volume_m3=float("nan"),
                           dims_m="", same_as="", note=note)
    return rows_p, out.reset_index()


def rooms_list(rows_p: pd.DataFrame, spaces: pd.DataFrame) -> str:
    if len(spaces) > MANY_ROOMS:
        rows_p, spaces = as_room_types(rows_p, spaces)
    curves = space_bands(rows_p)
    t30 = rows_p.groupby("space")["rt_mid"].median()
    drr = rows_p.groupby("space")["drr_db"].median()
    names = [words.octave(b) for b in BANDS]
    items = []
    for _, s in spaces.iterrows():
        k = s["space"]
        values = [None if pd.isna(v) else float(v) for v in curves.loc[k]] if k in curves.index else []
        dims, note, same_as = text(s["dims_m"]), text(s["note"]), text(s["same_as"])
        size = " · ".join(x for x in (f'{dims} m' if dims else "",
                                      f'{s["volume_m3"]:,.0f} m³' if pd.notna(s["volume_m3"]) else "") if x)
        same = (f' · same room as <a href="{e(same_as.split("/")[0])}.html#{e(anchor(same_as))}">{e(same_as)}</a>'
                if same_as else "")
        vals = (f'T30 {t30[k]:.2f} s' if pd.notna(t30.get(k)) else "No valid T30") + \
               (f'<br><span class="faint">DRR {drr[k]:+.1f} dB</span>' if pd.notna(drr.get(k)) else "")
        items.append(
            f'<div class="room" id="{e(anchor(k))}"><div><b>{e(words.room_label(s["label"], k))}</b>'
            f'<div class="quiet small">{e(words.ROOM_TYPES.get(s["space_type"], s["space_type"]))} · '
            f'{int(s["n_irs"]):,} IRs{" · " + e(size) if size else ""}{same}</div>'
            + (f'<div class="faint small">{e(note)}</div>' if note else "")
            + f'</div><div class="vals num">{vals}</div><div>{charts.band_curve(values, names) if values else ""}</div></div>')
    return f'<div class="rooms">{"".join(items)}</div>'


def anchor(space: str) -> str:
    """The id of a room's row on its dataset page: openair/york-minster -> room-york-minster."""
    return "room-" + space.split("/", 1)[-1]


def measured_sections(rows: pd.DataFrame, rows_p: pd.DataFrame, spaces: pd.DataFrame, corpus: pd.DataFrame,
                      capped: bool) -> str:
    a = acoustics(rows_p)
    t30_ticks, db_ticks = [0.1, 0.3, 1, 3, 10], [-20, -10, 0, 10, 20, 30]
    db = lambda v: f"{v:+g}" if v else "0"
    med = lambda c: rows_p[c].median() if rows_p[c].notna().any() else None

    def figure(title: str, n: int, what: str, chart: str) -> str:
        if not n:
            return f'<figure><figcaption><b>{e(title)}</b><span>No valid value in this dataset.</span></figcaption></figure>'
        return f'<figure><figcaption><b>{e(title)}</b><span>{what}</span></figcaption>{chart}</figure>'

    figs = [
        figure("Reverberation time", a["t30_n"], f"T30 at 500 Hz and 1 kHz (T20 where T30 is not valid), {a['t30_n']:,} IRs.",
               charts.histogram(a["t30"], histogram(corpus["rt_mid"], T30_EDGES), T30_EDGES, unit="s", axis="T30",
                                label="Reverberation time of the IRs against the whole collection", ticks=t30_ticks,
                                log=True, median=med("rt_mid"))),
        figure("Reverberation time per octave", sum(s["n"] for s in a["bands"].values() if s),
               "Each box holds the middle half of the IRs; the line across it is the median; the whiskers reach 5 and 95 %.",
               charts.band_boxes(a["bands"], [words.octave(b).replace(" Hz", "").replace(" kHz", "k") for b in BANDS],
                                 "T30 per octave band")),
        figure("Direct-to-reverberant ratio", a["drr_n"], f"DRR over the full band, {a['drr_n']:,} IRs.",
               charts.histogram(a["drr"], histogram(corpus["drr_db"], DB_EDGES), DB_EDGES, unit="dB", axis="DRR, dB",
                                label="DRR of the IRs against the whole collection", ticks=db_ticks, fmt=db,
                                median=med("drr_db"))),
        figure("Clarity", a["c50_n"], f"C50 at 500 Hz and 1 kHz, {a['c50_n']:,} IRs.",
               charts.histogram(a["c50"], histogram(corpus["c50_mid"], DB_EDGES), DB_EDGES, unit="dB", axis="C50, dB",
                                label="C50 of the IRs against the whole collection", ticks=db_ticks, fmt=db,
                                median=med("c50_mid"))),
    ]
    grades = {g: int(n) for g, n in rows["grade"].replace("", "none").value_counts().items()}
    defects = {c.removeprefix("defect_").replace("_", " "): int(rows[c].fillna(False).sum())
               for c in rows if c.startswith("defect_")}
    found = ", ".join(f"{k} ({n:,} IRs)" for k, n in defects.items() if n) or "none"
    ex = rows.sort_values("ir_id").groupby("format", group_keys=False).head(3).head(6)
    names = dict(zip(spaces["space"], spaces["label"]))
    ex_rows = [[f'<span class="mono">{e(r["ir_id"])}</span>', e(words.room_label(names.get(r["space"], ""), r["space"])),
                e(words.FORMATS.get(r["format"], r["format"])), f'{r["fs"] / 1000:g} kHz', f'{r["duration_s"]:.1f} s',
                f'{r["rt_mid"]:.2f} s' if pd.notna(r["rt_mid"]) else "–",
                f'{r["drr_db"]:+.1f} dB' if pd.notna(r["drr_db"]) else "–", e(r["grade"] or "–")] for _, r in ex.iterrows()]
    sample = " of the sampled IRs" if capped else ""
    return f"""
<section class="section" id="measured"><h2>What RIRDB measured{sample}</h2>
<p>Every IR is measured with the same analyser. The columns are this dataset; the grey outline is the whole collection
({len(corpus):,} IRs), so you can see where the dataset sits. {len(rows_p):,} IRs are counted, one per measurement: a
position published in two formats counts once.</p>
<div class="charts">{"".join(figs)}</div></section>
<section class="section" id="quality"><h2>Decay quality{sample}</h2>
<div class="gradeline">{grade_bar(grades)}{grade_legend(grades)}</div>
<p class="quiet">The grade says how much of the decay could be measured, S best. Defects found: {e(found)}.
<a href="../index.html#g-grades">How the grades work</a></p></section>
<section class="section" id="examples"><h2>Example IRs</h2>
<p class="quiet">Six rows of <code>irs.csv</code>, as <code>rirdb get</code> writes it. Every column is explained in the
<a href="../reference.html">data reference</a>.</p>
{table(["ir_id", "room", "format", "rate", "length", "T30", "DRR", "grade"], ex_rows, num={3, 4, 5, 6}, cls="plain ex")}
</section>"""


def dataset_page(d: pd.Series, c: dict, irs: pd.DataFrame, spaces: pd.DataFrame, own: pd.DataFrame | None,
                 corpus: pd.DataFrame) -> str:
    """The page of one dataset. A dataset not measured yet gets the same page without the measured parts."""
    i = d["dataset"]
    measured = d["status"] == "measured"
    rows = irs[irs["dataset"] == i]
    rows_p = primary(rows)
    sp = spaces[(spaces["dataset"] == i) & (spaces["n_irs"] > 0)].sort_values("space")
    cap = cap_of(i) if measured else None
    by = " · ".join(e(x) for x in (d["institution"], d["country"], None if pd.isna(d["year"]) else int(d["year"])) if x)
    caveats = [x for x in (d["caveats"] or "").split("\n") if x] + ([d["known_issues"]] if d["known_issues"] else [])
    status = pill("Measured by RIRDB", "measured") if measured else pill("Not measured yet")
    known = [x for x in str(d.get("known_defects") or "").split("\n") if x]
    def concerns(x: str) -> str:              # how many IRs of the table the defect concerns
        n = int(rows["known_defect"].str.contains(x, regex=False).sum()) if "known_defect" in rows else 0
        return f"{n:,} IRs of the table have it." if n else "No IR of the table comes from these files."
    defects = "" if not known else (
        '<div class="warnbox" id="defect"><h2>Known defect</h2><ul>'
        + "".join(f"<li>{e(x)}" + (f' <span class="quiet">{concerns(x)}</span>' if measured else "") + "</li>"
                  for x in known)
        + '</ul><p class="small quiet">The files are kept as published. The IRs a defect concerns stay in the table '
          'and in what <code>rirdb get</code> delivers, and <code>rirdb search</code> and <code>get</code> warn about '
          'them.</p></div>')
    banner = "" if measured else (
        f'<p class="banner">{e(words.STATUS.get(d["status"], d["status"]))}. The facts below are from the authors\' pages'
        + (f', read on {e(d["facts_checked_on"])}' if d["facts_checked_on"] else "") + ".</p>")
    # what each IR carries in the table
    per_ir = ""
    if measured:
        have = [f"{w} ({'all' if n == of else f'{n:,} of {of:,}'})" for w, n, of in labels(rows_p) if n]
        lack = [w for w, n, _ in labels(rows_p) if not n]
        fields = own_fields(own, rows_p["ir_id"])
        own_html = "".join(f'<li><code>{e(name)}</code>: {n:,} different values, e.g. {e(", ".join(ex))}</li>'
                           for name, n, ex in fields)
        per_ir = (f'<h3>In the table, for each IR</h3><p>Given: {e(", ".join(have)) or "no labels beyond the room"}.'
                  + (f' Not given: {e(", ".join(lack))}.' if lack else "") + "</p>"
                  + (f'<p>The dataset\'s own fields, kept as published (table <code>source_meta</code>):</p><ul>{own_html}</ul>'
                     if own_html else ""))
    where = f'<p class="where"><b>Where to find it:</b> {e(d["provides_where"])}</p>' if d["provides_where"] else ""
    cap_html = "".join(f"<p>{e(line.strip())}</p>" for line in cap.lines()) if cap else ""
    # the side panel
    licence = (f'<div><h4>Licence</h4><p><b>{e(words.licence_short(d["licence"]))}</b></p>'
               f'<p class="quiet small">{e(words.licence_name(d["licence"]))}</p>{use_marks(c["uses"])}</div>')
    get = (f'<div><h4>Get it</h4>{copybox(f"rirdb get --dataset {i}")}<p class="quiet small">{e(c["size"])}, every file as '
           f'published, checked against its checksum.</p><p class="small"><a href="../datasets.html?dataset={e(i)}&amp;tab=get">Choose IRs from it</a>: '
           f'at most so many per room, one format, a random draw.</p></div>') if measured else \
          (f'<div><h4>Get it</h4><p class="small">From the authors: <a href="{e(d["homepage"])}">{e(d["host"] or "homepage")}</a>'
           + (f' ({e(d["size_stated"])})' if d["size_stated"] else "") + ". RIRDB will download and check it once it is measured.</p></div>")
    contents = []
    if measured:
        contents += [("IRs", ", ".join(f"{n:,} {words.FORMATS.get(f, f).lower()}" for f, n in rows["format"].value_counts().items())),
                     ("Rooms", f"{len(sp):,}"),
                     ("Recorded with", ", ".join(words.LAYOUTS.get(l, l) for l in rows["layout"].value_counts().index)),
                     ("Sample rate", ", ".join(f"{r / 1000:g} kHz" for r in sorted(rows["fs"].unique())))]
    else:
        contents += [("Formats", ", ".join(words.FORMATS.get(f, f) for f in c["formats"])),
                     ("IRs, as stated", f'{int(d["n_irs_stated"]):,}' if pd.notna(d["n_irs_stated"]) else "")]
    contents += [("Files", d["file_formats"]), ("Kind", words.ORIGINS.get(d["origin"], d["origin"]))]
    dl = "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in contents if v)
    cite = []
    if d["paper_url"]:
        cite.append(f'<p class="small"><a href="{e(d["paper_url"])}">{e(d["paper_title"] or "The paper")}</a></p>')
    if d["doi"]:
        cite.append(f'<p class="small quiet">DOI <a href="https://doi.org/{e(d["doi"])}">{e(d["doi"])}</a></p>')
    if measured:
        cite.append('<p class="small quiet"><code>rirdb get</code> writes its BibTeX into references.bib.</p>')
    on_page = [("about", "Before you download"), ("provides", "What the authors provide"), ("rooms", "Rooms"),
               ("measured", "What RIRDB measured"), ("quality", "Decay quality"), ("examples", "Example IRs")]
    on_page = [(a, t) for a, t in on_page if measured or a in ("about", "provides")]
    on_page = ([("defect", "Known defect")] if known else []) + on_page
    aside = (f'<aside class="aside">{licence}{get}<div><h4>Contents</h4><dl>{dl}</dl></div>'
             + (f'<div><h4>Cite</h4>{"".join(cite)}</div>' if cite else "")
             + f'<div><h4>Links</h4><p class="small"><a href="{e(d["homepage"])}">{e(d["host"] or "Homepage")}</a></p></div>'
             + f'<nav class="small onpage" aria-label="On this page"><h4>On this page</h4>'
             + "".join(f'<a href="#{a}">{t}</a>' for a, t in on_page) + "</nav></aside>")
    body = f"""
<div class="wrap page"><div class="ds">
<div class="ds-main">
<div class="ds-head">{status}<h1>{e(d["name"])}</h1>{f'<p class="by">{by}</p>' if by else ""}
<p class="lead">{e(d["description"])}</p>{banner}</div>
{defects}
{f'<div class="note">{cap_html}</div>' if cap_html else ""}
<section class="section" id="about"><h2>Before you download</h2>
{"<ul>" + "".join(f"<li>{e(x)}</li>" for x in caveats) + "</ul>" if caveats else '<p class="quiet">Nothing recorded.</p>'}
</section>
<section class="section" id="provides"><h2>What the authors provide</h2>{provides_grid(d)}{where}{per_ir}</section>
{f'<section class="section" id="rooms"><h2>Rooms</h2><p class="quiet small">The curve is the median T30 in each octave from 63 Hz to 8 kHz, on the same scale for every room; the faint line marks 1 s.</p>{rooms_list(rows_p, sp)}</section>' if measured else ""}
{measured_sections(rows, rows_p, sp, corpus, bool(cap)) if measured else ""}
</div>{aside}</div></div>"""
    return page(f"{d['name']} · RIRDB", body, depth=1, current="datasets.html")
