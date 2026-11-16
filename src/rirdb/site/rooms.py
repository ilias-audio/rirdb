"""The rooms page: every room of the measured datasets, plotted by reverberation time and DRR, with the filters
of the datasets page."""

from __future__ import annotations

from rirdb.site import charts, words
from rirdb.site.datasets import anchor, command_box, facet, get_fields, room_types
from rirdb.site.layout import copybox, e, page

PER_PAGE = 50


def points(search: dict, up: str = "") -> list[dict]:
    """The rooms with both a median T30 and a median DRR, for the plot."""
    ds = search["datasets"]
    out = []
    for i, s in enumerate(search["spaces"]):
        if s["t30"] is None or s["drr"] is None:
            continue
        d = ds[s["d"]]
        out.append({"i": i, "t30": s["t30"], "drr": s["drr"], "kind": s["kind"],
                    "href": f'{up}d/{d["id"]}.html#{anchor(s["id"])}',
                    "tip": f'{s["label"]} ({d["name"]}): T30 {s["t30"]:.2f} s, DRR {s["drr"]:+.1f} dB'})
    return out


def legend() -> str:
    return ('<p class="legend kinds">' + "".join(f'<span><i class="k-{k}"></i>{e(label)}</span>'
                                                for k, label in words.KINDS.items()) + "</p>")


def room_row(s: dict, i: int, d: dict) -> str:
    vals = " · ".join(x for x in (f'T30 {s["t30"]:.2f} s' if s["t30"] is not None else "",
                                  f'DRR {s["drr"]:+.1f} dB' if s["drr"] is not None else "",
                                  f'{s["volume"]:,.0f} m³' if s["volume"] else "") if x)
    return (f'<li class="roomrow" data-i="{i}"><i class="kind k-{s["kind"]}" title="{e(words.KINDS[s["kind"]])}"></i>'
            f'<div><a href="d/{e(d["id"])}.html#{e(anchor(s["id"]))}">{e(s["label"])}</a>'
            f'<span class="quiet small"> · {e(words.ROOM_TYPES.get(s["type"], s["type"]))}</span>'
            f'<div class="small quiet">{e(d["name"])}</div></div>'
            f'<div class="small num">{vals}<div class="faint"><span class="count"></span></div></div></li>')


def rooms_page(search: dict) -> str:
    ds, sp = search["datasets"], search["spaces"]
    types = room_types(sp)
    facets = "".join([
        facet("kind", "Kind of space", list(words.KINDS.items()), multi=True),
        facet("format", "Format", [(f, words.FORMATS[f]) for f in words.SEARCH_FORMATS]),
        facet("use", "The licence allows", [(u, words.USES[u]) for u in ("commercial", "training", "redistribute")]),
        facet("t30", "Reverberation time", [(k, label) for k, label, _, _ in words.REVERB], multi=True),
        facet("grade", "Decay quality", [("S", "S"), ("A", "A or better"), ("B", "B or better"), ("C", "C or better")]),
        facet("provides", "The authors provide", list(words.PROVIDES.items()), multi=True, hint="all ticked"),
        facet("type", "Room type", [(t, words.ROOM_TYPES.get(t, t)) for t in types], multi=True, show=0),
    ])
    pts = points(search)
    rows = "".join(room_row(s, i, ds[s["d"]]) for i, s in enumerate(sp))
    body = f"""
<div class="wrap page">
<div class="page-head"><h1>Rooms</h1>
<p class="lead">The {len(sp):,} rooms of the {sum(d["status"] == "measured" for d in ds)} measured datasets, with
outdoor sites, vehicles and devices among them. Each dot is a room at the median reverberation time and
direct-to-reverberant ratio of its IRs. Filter them, then open a room on its dataset's page.</p></div>
<div class="find">
<form class="facets" id="filters" aria-label="Filters">{facets}{get_fields()}
<button type="reset" class="clear">Clear all filters</button></form>
<div class="results">
<div class="searchbar"><input id="text" name="text" form="filters" type="search" autocomplete="off"
 placeholder="Find a room or a dataset: cathedral, York, lecture…" aria-label="Find a room"></div>
<button type="button" class="filterbtn" aria-controls="filters" aria-expanded="false">Filters</button>
<div class="resultline"><p id="result" aria-live="polite"><b>{len(sp):,}</b> rooms</p></div>
<div class="chips" id="chips"></div>
{command_box()}
<figure class="plot"><div class="plotbox">{charts.scatter(pts, "Every room by median reverberation time and DRR")}</div>
<figcaption>{legend()}<span class="quiet small">{len(pts):,} rooms have both values. DRR mostly reflects where the
authors put the microphones, so read it as a property of the measurements as much as of the room. Values beyond an
axis sit on its edge.</span></figcaption></figure>
<ul class="roomlist" id="rooms">{rows}</ul>
<button type="button" class="more" id="more" hidden></button>
</div></div></div>"""
    return page("Rooms · RIRDB", body, current="rooms.html", scripts=("search-data.js",))
