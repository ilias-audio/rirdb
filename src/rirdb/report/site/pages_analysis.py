"""Analysis section of the rirdb website.

    analysis/index.html        corpus analysis: what was analysed, grades, nested subsets, a per-dataset
                               comparison table, corpus distributions, per-band boxes, T30 validity, categories
    analysis/quality.html      signal quality: what the audit checks, a dataset x flag heat table, signal
                               integrity, exact duplicates, and what the clean measured rooms leave out
    analysis/validation.html   agreement with values the dataset authors published (OpenAIR, ACE)
    analysis/map.html          the frozen CLAP map: definitions, one small map per dataset, realism/coverage

Every number comes from reports/site/data/ (site.json, ds/<id>.json) or from the report files the analysis
wrote (reports/overview/overview.json, reports/corpus/duplicates.csv). Text that quotes a finding is read
from docs/decisions/dataset-findings.md through the per-dataset JSON.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict

from markupsafe import Markup, escape

from rirdb import paths
from rirdb.report.site import charts
from rirdb.report.site.core import Site, band_label, fmt_int, fmt_num, fmt_pct

try:  # keep the site's wording in one place; fall back if a sibling module changes
    from rirdb.report.site.pages_catalog import CATEGORY_LABEL
except ImportError:  # pragma: no cover
    CATEGORY_LABEL = {}
try:  # a dataset whose spaces are all modelled (Voxengo): its T30 is not over IRs of real spaces
    from rirdb.report.site.pages_catalog import not_real_kind
except ImportError:  # pragma: no cover
    def not_real_kind(spaces: list[dict], index_kinds) -> str | None:
        return None
try:  # the catalogue's T30/T20 rule, so the comparison table, the catalogue and the spaces page agree
    from rirdb.report.site.pages_catalog import decay_of
except ImportError:  # pragma: no cover
    def decay_of(m: dict | None) -> dict | None:
        """T30 where at least half the room IRs allow it; otherwise T30-or-T20 (kind "T20")."""
        if not m or not m.get("n_analysed"):
            return None
        share = m.get("valid_t30_mid_rooms")
        if share is None:
            share = m.get("valid_t30_mid")
        if m.get("t30_p50") is not None and share is not None and share >= 0.5:
            return {"kind": "T30", "p10": m.get("t30_p10"), "p50": m["t30_p50"], "p90": m.get("t30_p90"), "share": share}
        if m.get("t30_or_t20_p50") is not None:
            return {"kind": "T20", "p10": m.get("t30_or_t20_p10"), "p50": m["t30_or_t20_p50"],
                    "p90": m.get("t30_or_t20_p90"), "share": share}
        return None
try:
    from rirdb.report.site.pages_dataset import no_analysis_reason
except ImportError:  # pragma: no cover
    def no_analysis_reason(row: dict) -> str:
        return "Not analysed yet."

SUBNAV = [
    ("index", "Corpus analysis", "analysis/index.html"),
    ("quality", "Signal quality", "analysis/quality.html"),
    ("validation", "Validation", "analysis/validation.html"),
    ("map", "CLAP map", "analysis/map.html"),
]
BANDS = ["63", "125", "250", "500", "1000", "2000", "4000", "8000"]
BAND_LABELS = {b: band_label(b) for b in BANDS}
GROUPS = [
    ("content", "Content", "The IR holds something other than a room decay. These four take an IR out of the clean measured rooms."),
    ("signal", "Signal", "Properties of the file and the recording chain. Kept: they describe the recording."),
    ("decay", "Decay", "Limits on the decay analysis. Kept: the validity rules already drop the values they affect."),
    ("spatial", "Spatial", "Channel layout of the analysed channel. Kept: informational."),
]
STATUS_LABEL = {"active": "Fetched", "manual": "Manual download", "broken": "Broken upstream", "planned": "Planned",
                "deferred": "Deferred"}
MAP_PANEL_MAX = 600  # points drawn per small map (every k-th point of the map sample)
AUDIT_EXAMPLES = 12  # example files listed per audit flag


# ----------------------------------------------------------------------------- helpers

def md_inline(text: str | None) -> Markup:
    """Escape, then render `code` spans of the findings tables."""
    if not text:
        return Markup("")
    s = re.sub(r"(\d) ?%(?!\w)", "\\1\u00a0%", str(escape(text)))  # "2.2 %" never wraps before the sign, as fmt_pct
    return Markup(re.sub(r"`([^`]+)`", r"<code>\1</code>", s))


def is_log(edges: list) -> bool:
    if len(edges) < 3 or edges[0] <= 0:
        return False
    r = [edges[i + 1] / edges[i] for i in range(len(edges) - 1)]
    d = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
    return (max(r) - min(r)) < 1e-3 * max(r) and (max(d) - min(d)) > 1e-3 * max(d)


def share_hist(h: dict, *, unit: str, nd: int, title: str, noun: str = "IRs", log_x: bool | None = None,
               w: int = 440, height: int = 200) -> Markup:
    """Share-per-bin histogram without a reference outline: charts.hist's marks and spacing, plus the counted
    noun in the tooltip ("spaces" or "IRs") and percent ticks that keep a decimal when the step needs one."""
    edges, counts = h["edges"], h["counts"]
    log_x = is_log(edges) if log_x is None else log_x
    n = max(sum(counts), 1)
    share = [c / n for c in counts]
    ml, mr, mt, mb = 40, 10, 8, 30
    pw, ph = w - ml - mr, height - mt - mb
    lo, hi = edges[0], edges[-1]
    if log_x:
        X = lambda v: ml + (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo)) * pw  # noqa: E731
    else:
        X = lambda v: ml + (v - lo) / (hi - lo) * pw  # noqa: E731
    top = max(share + [1e-9])
    yt = charts.nice_ticks(0, top, 4)
    ymax = yt[-1] if yt[-1] >= top else top
    Y = lambda v: mt + ph - v / ymax * ph  # noqa: E731
    pnd = 0 if all(abs(t * 100 - round(t * 100)) < 1e-6 for t in yt) else 1
    out = [charts.svg_open(w, height, title or "Histogram")]
    for t in yt:
        out.append(f'<line class="gl" x1="{ml}" x2="{ml + pw}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>')
        out.append(f'<text x="{ml - 6}" y="{Y(t) + 3.5:.1f}" text-anchor="end">{t * 100:.{pnd}f}{charts.PCT}</text>')
    xt = charts.log_ticks(lo, hi) if log_x else charts.nice_ticks(lo, hi, 6)
    if log_x and pw / math.log10(hi / lo) < 90:  # crowded log axis (the narrow drawings): decades only
        dec = [t for t in xt if abs(math.log10(t) - round(math.log10(t))) < 1e-9]
        xt = dec if len(dec) >= 2 else xt
    for t in xt:
        out.append(f'<line class="ax" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{mt + ph}" y2="{mt + ph + 4}"/>')
        out.append(f'<text x="{X(t):.1f}" y="{mt + ph + 16}" text-anchor="middle">{charts.tick_label(t)}</text>')
    if unit:
        out.append(f'<text x="{ml + pw}" y="{height - 1}" text-anchor="end">{charts.esc(unit)}</text>')
    for i, s in enumerate(share):
        x0, x1 = X(edges[i]), X(edges[i + 1])
        bw = min(x1 - x0 - 2, 24)
        if bw <= 0.5:
            bw = max(x1 - x0 - 0.5, 0.5)
        xc = (x0 + x1) / 2
        tip = (f"{charts.fmt_val(edges[i], nd)}–{charts.fmt_val(edges[i + 1], nd)} {unit}".strip()
               + f"\n{s * 100:.1f}\u00a0% of {noun} ({counts[i]:,})")
        if s > 0:
            out.append(f'<path class="s1" d="{charts.bar_up(xc - bw / 2, Y(0), bw, Y(0) - Y(s), min(4, bw / 2))}"/>')
        out.append(f'<rect class="hit" x="{x0:.2f}" y="{mt}" width="{max(x1 - x0, 1):.2f}" height="{ph}" '
                   f'data-tip="{charts.esc(tip)}"/>')
    out.append(f'<line class="ax" x1="{ml}" x2="{ml + pw}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    out.append("</svg>")
    return Markup("".join(out))


def hist_rows(h: dict, nd: int) -> list[dict]:
    e, c = h["edges"], h["counts"]
    n = max(sum(c), 1)
    return [{"lo": fmt_num(e[i], nd), "hi": fmt_num(e[i + 1], nd), "n": c[i], "share": c[i] / n}
            for i in range(len(c)) if c[i]]


def corpus_box(ref: dict) -> dict:
    """reference_bands[metric] -> charts.bands box data ({band: {"n", "q"}})."""
    return {b: {"n": v.get("n_rooms", 0), "q": v["q"]} for b, v in ref.items() if v.get("q")}


def bands_chart(bx: dict, *, unit: str, nd: int, zero: bool, title: str, w: int = 480, height: int = 230) -> Markup:
    svg = charts.bands(bx, None, unit=unit, nd=nd, zero=zero, title=title, w=w, height=height)
    return Markup(str(svg).replace(f"This dataset: median, 25–75{charts.PCT} box, 5–95{charts.PCT} whisker",
                                   f"Spread over spaces: median, 25–75{charts.PCT} box, 5–95{charts.PCT} whisker"))


def shade(v: float | None) -> tuple[str, str]:
    """Inline style of a heat cell (class "heat-c"): a 0-1 weight on a square-root scale (most rates are small).
    The page CSS mixes --series-1 into the surface up to a per-theme maximum and keeps --ink as the text colour,
    so every cell stays at 4.5:1 or better in both themes (a fixed white/dark switch cannot: the same mix is
    light in one theme and dark in the other)."""
    if not v:
        return "", ""
    return f"--w:{0.12 + math.sqrt(min(v, 1.0)) * 0.88:.3f}", "var(--ink)"


def heat_strip(values: dict, keys: list[str], *, labels: dict | None = None) -> Markup:
    """charts.strip's markup (.strip / .strip-head) with the theme-safe heat cells of shade()."""
    labels = labels or {}
    head = "".join(f"<div>{escape(labels.get(k, k))}</div>" for k in keys)
    cells = []
    for k in keys:
        v = values.get(k)
        if v is None:
            cells.append('<div style="background:var(--sunk);color:var(--muted)">–</div>')
            continue
        txt = f"{v * 100:.0f}"  # per cent without the sign (the title says so): the cells are narrow
        hz = f"{int(k) // 1000} kHz" if k.isdigit() and int(k) >= 1000 else f"{k} Hz"
        cells.append(f'<div class="heat-c" style="--w:{0.12 + min(max(v, 0), 1) * 0.88:.3f}" '
                     f'data-tip="{escape(hz)}: {v * 100:.0f}{charts.PCT}">{txt}</div>')
    n = len(keys)
    return Markup(f'<div class="strip strip-head" style="--n:{n}">{head}</div>'
                  f'<div class="strip" style="--n:{n}">{"".join(cells)}</div>')


def share_text(v: float | None) -> str:
    if v is None:
        return "–"
    if v == 0:
        return "0"
    if v < 0.01:
        return "<1"
    return f"{v * 100:.0f}"


def dual(wide: Markup, narrow: Markup) -> Markup:
    """The same chart drawn twice: CSS shows the narrow drawing on phones, so its text stays readable."""
    return Markup('<div class="vw-wide">') + wide + Markup('</div><div class="vw-narrow">') + narrow + Markup("</div>")


def pct1(v) -> str:
    """Share with one decimal; "<0.1 %" for a non-zero share that would print as 0.0."""
    if v is None:
        return "–"
    if v == 0:
        return "0"
    return "<0.1\u00a0%" if v < 0.0005 else f"{v * 100:.1f}\u00a0%"


def num0(v, nd: int = 2) -> str:
    """fmt_num without a minus sign on a value that rounds to zero."""
    s = fmt_num(v, nd)
    return s[1:] if s.startswith("−") and not s.strip("−0.,") else s


def med(vals: list) -> float | None:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    k = len(v)
    return v[k // 2] if k % 2 else (v[k // 2 - 1] + v[k // 2]) / 2


def png_size(p) -> tuple[int | None, int | None]:
    """Width and height from a PNG's IHDR chunk; (None, None) if the file is missing or not a PNG."""
    try:
        with open(p, "rb") as fh:
            head = fh.read(24)
    except OSError:
        return None, None
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def load_overview() -> dict:
    f = paths.REPORTS_DIR / "overview" / "overview.json"
    return json.loads(f.read_text()) if f.exists() else {}


def analysed(site: Site) -> list[dict]:
    rows = [d for d in site.datasets if d.get("analysed") and d.get("metrics")]
    rows.sort(key=lambda d: (-(d["metrics"].get("n_analysed") or 0), d["name"].lower()))
    return rows


def finding_rows(site: Site, ds: str, prefix: str, section: str = "data") -> list[dict]:
    return [r for r in site.dataset(ds).get("findings") or []
            if r.get("section") == section and (r.get("subject") or "").startswith(prefix)]


def finding_view(r: dict) -> dict:
    return {"subject": r.get("subject"), "finding": md_inline(r.get("finding")), "evidence": md_inline(r.get("evidence")),
            "handling": md_inline(r.get("handling"))}


# ----------------------------------------------------------------------------- page 1: corpus analysis

def index_ctx(site: Site, ov: dict) -> dict:
    D = site.data
    C = D["corpus"]
    rows = analysed(site)
    params = {p["key"]: p for p in D.get("params") or []}

    tiles = [
        {"v": fmt_int(C["n_irs_analysed"]), "small": f"of {fmt_int(C['n_irs_indexed'])} indexed", "label": "IRs analysed"},
        {"v": fmt_int(C["n_datasets_analysed"]), "small": f"of {fmt_int(C['n_datasets_registry'])}", "label": "registry datasets analysed"},
        {"v": fmt_int(C["n_spaces"]), "small": "", "label": "spaces indexed"},
        {"v": fmt_int(C["n_spaces_physical"]), "small": "", "label": "physical spaces (a space in two datasets counted once)"},
        {"v": fmt_int(C["n_clean_rooms_irs"]), "small": "", "label": "IRs in clean measured rooms"},
        {"v": fmt_int(C["n_duplicate_copies"]), "small": "", "label": "exact duplicate copies set aside"},
    ]
    subsampled = [{"id": d["id"], "name": d["name"], "n_records": (d.get("index") or {}).get("n_records"),
                   "n": (d.get("metrics") or {}).get("n_analysed")}
                  for d in rows if d.get("analysis_scope") == "subsample_v1"]
    not_analysed = [{"id": d["id"], "name": d["name"], "status": STATUS_LABEL.get(d["status"], d["status"]),
                     "reason": no_analysis_reason(d)} for d in site.datasets if not d.get("analysed")]
    not_analysed.sort(key=lambda r: (r["status"], r["name"].lower()))

    # grades
    grade_bar = dual(charts.grade_bar(C["grades"], w=760, title="Grades of all analysed IRs"),
                     charts.grade_bar(C["grades"], w=360, title="Grades of all analysed IRs"))
    g_rows = []
    for d in rows:
        m = d["metrics"]
        parts = {k: int(m.get(f"grade_{k.lower()}") or 0) for k in "ABCD"}
        g_rows.append({"label": d["name"], "href": f"../d/{d['id']}.html#grades", "parts": parts, "id": d["id"],
                       "n": sum(parts.values())})
    grade_stack = dual(charts.stack100(g_rows, w=760, label_w=210, title="Share of grades A to D in each analysed dataset"),
                       charts.stack100(g_rows, w=380, label_w=150, title="Share of grades A to D in each analysed dataset"))

    # nested subsets (overview.json)
    subsets = []
    prev = None
    dist = ov.get("distributions") or {}
    for key, label in (ov.get("subsets") or {}).items():
        dd = dist.get(key) or {}
        n = dd.get("n_irs")
        subsets.append({"key": key, "label": label, "sql": (ov.get("subset_sql") or {}).get(key, ""), "n_irs": n,
                        "n_rooms": dd.get("n_rooms"), "n_datasets": dd.get("n_datasets"),
                        "drop": (prev - n) if (prev is not None and n is not None) else None})
        prev = n

    # per-dataset comparison
    cmp_rows = []
    by_ds: dict[str, list[dict]] = {}
    for s in D.get("spaces") or []:
        by_ds.setdefault(s["dataset_id"], []).append(s)
    for d in rows:
        over = not_real_kind(by_ds.get(d["id"]) or [], (d.get("index") or {}).get("kinds_found"))
        m, ix = d["metrics"], d.get("index") or {}
        n = int(m.get("n_analysed") or 0)
        gsum = sum(int(m.get(f"grade_{k}") or 0) for k in "abcd") or 1
        t = decay_of(m) or {}
        t_kind = t.get("kind", "T30")
        p10, p50, p90 = t.get("p10"), t.get("p50"), t.get("p90")
        what = "T30" if t_kind == "T30" else "T30 where valid, otherwise T20"
        tip = (f"{d['name']}: {what}" + (f" over its {over} IRs (no IRs of real spaces)" if over else "")
               + f", p10 {fmt_num(p10, 2)} s, median {fmt_num(p50, 2)} s, p90 {fmt_num(p90, 2)} s"
               if p50 is not None else "")
        cmp_rows.append({
            "id": d["id"], "name": d["name"], "n": n, "spaces": ix.get("n_spaces"),
            "type": d.get("type"), "subsample": d.get("analysis_scope") == "subsample_v1",
            "n_records": ix.get("n_records"),
            "grades": [{"k": k, "n": int(m.get(f"grade_{k.lower()}") or 0),
                        "share": int(m.get(f"grade_{k.lower()}") or 0) / gsum,
                        "text": share_text(int(m.get(f"grade_{k.lower()}") or 0) / gsum)} for k in "ABCD"],
            "t_kind": t_kind, "p10": p10, "p50": p50, "p90": p90, "over": over,
            "rng": charts.range_mini(p10, p50, p90, tip=tip, w=90) if p50 is not None else Markup(""),
            "edt": m.get("edt_p50"), "c80": m.get("c80_p50"), "drr": m.get("drr_p50"), "sti": m.get("sti_p50"),
            "valid": m.get("valid_t30_mid_rooms"), "pnr": m.get("pnr_p50"), "bw": m.get("bw_eff_p50"),
            "dup": m.get("share_duplicates"), "n_dup": m.get("n_duplicates"),
        })

    # corpus distributions: one value per space
    hists = []
    for key, h in (D.get("reference_hists") or {}).items():
        p = params.get(key, {"label": key, "unit": "", "nd": 2, "info": ""})
        unit = p.get("unit") or ""
        title = f"{p['label']}: one median per space"
        notes = []
        if h.get("below"):
            notes.append(f"{fmt_int(h['below'])} space{'s' if h['below'] != 1 else ''} below {fmt_num(h['edges'][0], p['nd'])} {unit} counted in the first bin")
        if h.get("above"):
            notes.append(f"{fmt_int(h['above'])} space{'s' if h['above'] != 1 else ''} above {fmt_num(h['edges'][-1], p['nd'])} {unit} counted in the last bin")
        note = "; ".join(notes)
        caption = p.get("info", "").replace("+-", "±") + (f" {note[:1].upper()}{note[1:]}." if note else "")
        hists.append({"key": key, "title": title, "label": p["label"], "unit": unit, "caption": caption,
                      "sub": f"{fmt_int(h['n'])} spaces · {h.get('subset', '')} · share of spaces per bin",
                      "svg": dual(share_hist(h, unit=unit, nd=p["nd"], title=title, noun="spaces"),
                                  share_hist(h, unit=unit, nd=p["nd"], title=title, noun="spaces", w=340, height=190)),
                      "rows": hist_rows(h, p["nd"])})

    # per-band boxes of the per-space medians
    rb = D.get("reference_bands") or {}
    band_specs = [("t30", "T30", "s", 2, True), ("edt", "EDT", "s", 2, True), ("c80", "C80", "dB", 1, False),
                  ("d50", "D50", "", 2, True)]
    bands = []
    for mk, label, unit, nd, zero in band_specs:
        if mk not in rb:
            continue
        bx = corpus_box(rb[mk])
        title = f"{label} per octave band: spread of the per-space medians"
        bands.append({"key": mk, "label": label, "unit": unit, "title": title,
                      "svg": dual(bands_chart(bx, unit=unit, nd=nd, zero=zero, title=title),
                                  bands_chart(bx, unit=unit, nd=nd, zero=zero, title=title, w=340, height=220)),
                      "rows": [{"band": BAND_LABELS[b], "n": bx[b]["n"], "q": [fmt_num(x, nd) for x in bx[b]["q"]]}
                               for b in BANDS if b in bx],
                      "n_max": max((bx[b]["n"] for b in bx if b in BANDS), default=0)})

    # T30 validity per band
    v = D.get("validity_t30") or {}
    validity = None
    if v:
        validity = {"n_irs": v.get("n_irs"), "n_datasets": v.get("n_datasets"), "n_spaces": v.get("n_spaces"),
                    "rows": [{"label": lab, "sub": sub, "strip": heat_strip({b: (v.get(b) or {}).get(k) for b in BANDS},
                                                                             BANDS, labels=BAND_LABELS)}
                             for k, lab, sub in (
                                 ("ir", "Per IR", "share of the clean measured-room IRs with a valid T30"),
                                 ("dataset_median", "Per dataset", "median over datasets of each dataset's valid share"),
                                 ("space", "Per space", "share of spaces with at least one valid T30"))],
                    "table": [{"band": BAND_LABELS[b], "ir": (v.get(b) or {}).get("ir"),
                               "ds": (v.get(b) or {}).get("dataset_median"), "space": (v.get(b) or {}).get("space")}
                              for b in BANDS]}

    # space categories
    sp = D.get("spaces") or []
    by_cat: dict[str, dict] = defaultdict(lambda: {"n": 0, "phys": set(), "clean": 0, "irs": 0})
    for s in sp:
        c = by_cat[s.get("category") or "unknown"]
        c["n"] += 1
        c["phys"].add(s.get("physical_id") or s.get("room_id"))
        c["clean"] += 1 if (s.get("n_clean") or 0) > 0 else 0
        c["irs"] += int(s.get("n_irs") or 0)
    cats = [{"key": k, "label": CATEGORY_LABEL.get(k, k.replace("_", " ").capitalize()), "n": c["n"],
             "phys": len(c["phys"]), "clean": c["clean"], "irs": c["irs"]}
            for k, c in by_cat.items()]
    cats.sort(key=lambda r: (-r["n"], r["label"]))
    cat_items = [{"label": c["label"], "value": c["n"],
                  "tip": f"{c['label']}: {c['n']:,} indexed space{'s' if c['n'] != 1 else ''}, {c['clean']:,} with clean IRs"}
                 for c in cats]
    cat_chart = dual(charts.hbars(cat_items, w=560, title="Indexed spaces per category"),
                     charts.hbars(cat_items, w=360, title="Indexed spaces per category"))
    names = {d["id"]: d["name"] for d in site.datasets}
    n_irs_sp = sum(int(s.get("n_irs") or 0) for s in sp) or 1
    big = sorted(sp, key=lambda s: -int(s.get("n_irs") or 0))[:5]
    space_facts = {
        "top_cats": [c["label"] for c in cats if c["key"] != "unknown"][:3],
        "n_merged": len(sp) - len({s.get("physical_id") or s.get("room_id") for s in sp}),
        "big": [{"id": s["dataset_id"], "name": names.get(s["dataset_id"], s["dataset_id"]), "n": s.get("n_irs")} for s in big],
        "big_share": sum(int(s.get("n_irs") or 0) for s in big) / n_irs_sp,
        "n_irs": n_irs_sp,
        "example": None,
    }
    for s in sp:  # a space indexed under two datasets, as an example
        pid = s.get("physical_id") or ""
        other = pid.split("/", 1)[0]
        if pid and pid != s["room_id"] and other != s["dataset_id"]:
            space_facts["example"] = {"label": (s.get("room_label") or s["room_id"]).replace("_", " "), "a": names.get(other, other),
                                      "b": names.get(s["dataset_id"], s["dataset_id"])}
            break

    figures = [
        {"src": "../assets/t30_bands.png", "title": "Frequency shape of the decay",
         "alt": ("Line chart: T30 in each octave band from 125 Hz to 8 kHz divided by the mid-frequency T30, median and "
                 "interquartile band, for three groups (measured omni or array, measured spatial or binaural, production "
                 "libraries). Measured omni and array IRs stay close to 1 up to 4 kHz; spatial, binaural and production "
                 "IRs fall from about 1.15 at 125 Hz to between 0.7 and 0.85 at 8 kHz.")},
        {"src": "../assets/t30_c80.png", "title": "Reverberance against clarity",
         "alt": ("Scatter plot of C80 against T30 (mid-frequency single numbers, valid values only, log time axis) for "
                 "measured omni or array, measured spatial or binaural, and production-library IRs. C80 falls from about "
                 "35 dB at 0.15 s to around 0 dB at 2 s and below 0 dB for the longest decays.")},
        {"src": "../assets/coverage_t30_drr.png", "title": "Coverage of T30 and DRR",
         "alt": ("Two-dimensional histogram of IR counts over T30 (log axis, 0.05 to 10 s) and broadband DRR (-25 to 30 dB), "
                 "white where no IR exists. The densest region is T30 from 0.15 to 3 s with DRR from -10 to 15 dB; "
                 "long decays with high DRR and very short decays with low DRR are sparse or empty.")},
    ]
    for f in figures:  # width and height attributes from the PNG itself, so the page does not jump
        f["w"], f["h"] = png_size(site.out / f["src"].removeprefix("../"))

    return {"tiles": tiles, "subsampled": subsampled, "not_analysed": not_analysed, "grade_bar": grade_bar,
            "grade_stack": grade_stack, "g_rows": g_rows, "subsets": subsets, "ov_generated": ov.get("generated"),
            "ov_version": ov.get("analyzer_version"), "cmp_rows": cmp_rows, "hists": hists, "bands": bands,
            "validity": validity, "cats": cats, "cat_chart": cat_chart, "figures": figures, "n_rows": len(rows),
            "space_facts": space_facts}


# ----------------------------------------------------------------------------- page 2: signal quality

EDC_LABEL = {"lundeby": "Noise floor found (Lundeby)", "schroeder_nofloor": "Decays to the end of the file (plain Schroeder)",
             "failed": "Noise-floor fit failed", "n/a": "No broadband decay analysed"}
EXAMPLE_COLS = {  # flag -> measures shown with its examples: (key, label, unit, decimals)
    "flag_sweep_like": [("glide_db", "glide", "dB", 1), ("tonal_frac", "tonal share", "", 2)],
    "flag_tonal": [("tonal_frac", "tonal share", "", 2)],
    "flag_not_decaying": [("env_slope_db_s", "envelope slope", "dB/s", 0)],
    "flag_dropout": [],
    "flag_nonstationary_noise": [("noise_burst_db", "burst", "dB", 1), ("noise_drift_db", "drift", "dB", 1)],
    "flag_upsampled": [("bw_eff_hz", "content to", "Hz", 0), ("resampled_from_hz", "source rate", "Hz", 0)],
    "flag_band_limited": [("bw_eff_hz", "content to", "Hz", 0)],
    "flag_quantized_tail": [],
}


def quality_ctx(site: Site, ov: dict) -> dict:
    D = site.data
    C = D["corpus"]
    rows = analysed(site)
    n_ok = C.get("n_irs_ok") or C["n_irs_analysed"]
    det = {d["id"]: (site.dataset(d["id"]).get("analysis") or {}) for d in rows}
    names = {d["id"]: d["name"] for d in site.datasets}

    tot, nds = Counter(), Counter()
    per: dict[str, dict] = {}
    for i, a in det.items():
        per[i] = {f["flag"]: f for f in a.get("flags") or []}
        for f in a.get("flags") or []:
            tot[f["flag"]] += int(f.get("n") or 0)
            nds[f["flag"]] += 1 if f.get("n") else 0

    flags = D.get("flags") or []
    groups = []
    for g, glabel, gtext in GROUPS:
        items = [{"flag": f["flag"], "label": f["label"], "info": f["info"], "n": tot[f["flag"]],
                  "share": tot[f["flag"]] / n_ok if n_ok else None, "nds": nds[f["flag"]],
                  "share_text": pct1(tot[f["flag"]] / n_ok if n_ok else None)}
                 for f in flags if f["group"] == g]
        groups.append({"key": g, "label": glabel, "text": gtext, "flags": items})
    never = [f["label"] for f in flags if not tot[f["flag"]]]

    # heat table: datasets x flags that fire somewhere, in group order
    cols = [f for g, _, _ in GROUPS for f in flags if f["group"] == g and tot[f["flag"]]]
    col_groups = [{"label": gl, "n": sum(1 for f in cols if f["group"] == g)} for g, gl, _ in GROUPS]
    col_groups = [c for c in col_groups if c["n"]]
    heat = []
    for d in sorted(rows, key=lambda d: d["name"].lower()):
        a = det[d["id"]]
        n_all = int(a.get("n_analysed") or 0) - int(a.get("n_errors") or 0)
        cells = []
        for f in cols:
            r = per[d["id"]].get(f["flag"]) or {}
            s, n = r.get("share"), int(r.get("n") or 0)
            style, _ = shade(s)
            cells.append({"v": s if s is not None else -1, "text": share_text(s), "style": style, "zero": not n,
                          "tip": f"{d['name']}, {f['label']}: {n:,} of {n_all:,} IRs ({(s or 0) * 100:.1f}\u00a0%)"})
        heat.append({"id": d["id"], "name": d["name"], "n": n_all, "cells": cells})

    # signal integrity (overview.json)
    integ = ov.get("integrity") or {}
    integrity = None
    if integ:
        on = integ.get("onset") or {}
        oh = on.get("hist") or {}
        onset_svg = None
        if oh.get("edges"):
            ms = {"edges": [e * 1000 for e in oh["edges"]], "counts": oh["counts"], "n": oh.get("n", sum(oh["counts"]))}
            onset_svg = dual(share_hist(ms, log_x=True, unit="ms", nd=1, noun="IRs with a pre-delay", w=560, height=200,
                                        title="Time before the direct sound in the files"),
                             share_hist(ms, log_x=True, unit="ms", nd=1, noun="IRs with a pre-delay", w=340, height=190,
                                        title="Time before the direct sound in the files"))
        edc = Counter()
        for k, n in (integ.get("edc_mode_bb") or {}).items():
            edc["mixed" if "|" in k else k] += int(n)
        edc_items = [{"label": EDC_LABEL.get(k, "Channels differ (multichannel IRs)" if k == "mixed" else k), "value": n,
                      "tip": f"{EDC_LABEL.get(k, 'Channels differ (multichannel IRs)' if k == 'mixed' else k)}: {n:,} IRs"}
                     for k, n in edc.most_common()]
        ihists = []
        specs = [
            ("pnr", "Peak-to-noise ratio", "dB", 1, False,
             "Peak level above the noise floor: the usable decay range. A T30 needs at least 45 dB in the band."),
            ("bw", "Effective bandwidth", "Hz", 0, True,
             "Where the content of the IR stops: the upper edge of the highest 1/12-octave band within 50 dB of the "
             "500 Hz–4 kHz level, over the first 0.5 s. The clusters sit just below the Nyquist frequencies of the common "
             "sample rates, so the values fall on a few band edges."),
            ("noise_burst", "Noise bursts after the decay", "dB", 0, False,
             "Loudest 20 ms frame over the median frame, in the noise after the broadband noise-floor intersection "
             "(stationary noise: about 1–3 dB). Over 10 dB raises the unsteady-noise flag. Only IRs with a measured "
             "noise floor and at least 0.25 s of it."),
            ("noise_drift", "Noise drift after the decay", "dB", 0, False,
             "Median level of the last third of that noise segment minus the first third (fade-outs, gating). Beyond "
             "±6 dB raises the unsteady-noise flag."),
        ]
        for key, title, unit, nd, log, cap in specs:
            h = integ.get(key) or {}
            if not h.get("edges"):
                continue
            q = h.get("q") or []
            sub = f"{fmt_int(h.get('n'))} IRs"
            if len(q) == 5:
                sub += f" · median {fmt_num(q[2], nd)} {unit} · 5–95\u00a0%: {fmt_num(q[0], nd)} to {fmt_num(q[4], nd)} {unit}"
            ihists.append({"title": title, "sub": sub, "caption": cap, "unit": unit, "rows": hist_rows(h, nd),
                           "svg": dual(share_hist(h, log_x=log, unit=unit, nd=nd, noun="IRs", w=440, title=title),
                                       share_hist(h, log_x=log, unit=unit, nd=nd, noun="IRs", w=340, height=190, title=title))})
        integrity = {
            "n_all": C["n_irs_analysed"], "audited": integ.get("audited"), "bw_audited": integ.get("bw_audited"),
            "share_10": on.get("share_over_10ms"), "share_100": on.get("share_over_100ms"), "n_zero": on.get("n_zero"),
            "lead": integ.get("leading_silence_share"), "trail": integ.get("trailing_zeros_share"),
            "onset_svg": onset_svg, "onset_n": oh.get("n"),
            "onset_rows": hist_rows({"edges": [e * 1000 for e in oh.get("edges", [])], "counts": oh.get("counts", [])}, 2)
            if oh.get("edges") else [],
            "edc_chart": dual(charts.hbars(edc_items, w=560, title="Broadband noise-floor method per IR"),
                              charts.hbars(edc_items, w=360, label_w=170, title="Broadband noise-floor method per IR")),
            "edc_items": edc_items, "edc_total": sum(edc.values()), "hists": ihists,
            "resampled": [{"fs": int(k), "n": int(n)} for k, n in sorted((integ.get("resampled_from") or {}).items(), key=lambda kv: int(kv[0]))],
        }
    # where each audit flag fires, with examples
    audit = []
    flag_info = {f["flag"]: f for f in flags}
    for fk, a in (integ.get("audit") or {}).items():
        meas = EXAMPLE_COLS.get(fk, [])
        ex = []
        for e in (a.get("examples") or [])[:AUDIT_EXAMPLES]:
            vals = []
            for key, lab, unit, nd in meas:
                if e.get(key) is not None:
                    vals.append(f"{lab} {fmt_num(e[key], nd)}{(' ' + unit) if unit else ''}")
            ex.append({"id": e.get("dataset_id"), "name": names.get(e.get("dataset_id"), e.get("dataset_id")),
                       "file": e.get("local_key") or e.get("ir_id"), "vals": "; ".join(vals)})
        info = flag_info.get(fk, {"label": fk, "info": "", "group": ""})
        audit.append({"flag": fk, "label": info["label"], "info": info["info"], "group": info["group"], "n": a.get("n"),
                      "by": [{"id": k, "name": names.get(k, k), "n": v} for k, v in (a.get("by_dataset") or {}).items()],
                      "examples": ex, "n_examples": len(a.get("examples") or [])})

    # exact duplicates
    dup_rows = []
    for d in rows:
        a = det[d["id"]]
        du = a.get("duplicates") or {}
        if du.get("n_copies") or du.get("n_in_groups"):
            dup_rows.append({"id": d["id"], "name": d["name"], "copies": du.get("n_copies", 0),
                             "groups": du.get("n_groups"), "in_groups": du.get("n_in_groups"), "cross": du.get("n_cross", 0),
                             "cross_with": [{"id": k, "name": names.get(k, k), "n": v} for k, v in (du.get("cross_with") or {}).items()],
                             "n": a.get("n_analysed"), "share": (du.get("n_copies", 0) / a["n_analysed"]) if a.get("n_analysed") else None})
    dup_rows.sort(key=lambda r: -r["copies"])
    n_cross = sum(r["cross"] or 0 for r in dup_rows)
    pairs = dup_pairs(names)
    dup_cases = []
    for ds, prefix in (("tau_srir", "TAU-SRIR"), ("rochester", "Rochester")):
        for r in finding_rows(site, ds, prefix):
            dup_cases.append({"id": ds, "name": names.get(ds, ds), **finding_view(r)})

    # what the clean measured rooms leave out
    dist = ov.get("distributions") or {}
    steps = []
    labels = ov.get("subsets") or {}
    keys = list(labels)
    why = {"measured": "Production libraries and other IRs that are not measurements (processed, designed or modelled).",
           "measured_rooms": ("Measured IRs that are not rooms: anechoic chambers, outdoor sites, devices, scale models, "
                              "vehicles, and modelled spaces inside measured datasets."),
           "clean_rooms": ("A second representation of the same position (only the preferred one is kept), exact duplicate copies, "
                           "and IRs with a content flag: sweep residue, tonal content, not decaying, dropouts.")}
    for i in range(1, len(keys)):
        a, b = dist.get(keys[i - 1]) or {}, dist.get(keys[i]) or {}
        if a.get("n_irs") is None or b.get("n_irs") is None:
            continue
        steps.append({"from": labels[keys[i - 1]], "to": labels[keys[i]], "drop": a["n_irs"] - b["n_irs"],
                      "drop_rooms": (a.get("n_rooms") or 0) - (b.get("n_rooms") or 0),
                      "drop_ds": (a.get("n_datasets") or 0) - (b.get("n_datasets") or 0), "why": why.get(keys[i], "")})
    content_flags = [f for f in flags if f["group"] == "content"]
    kinds_all = (dist.get("all") or {}).get("kinds") or {}

    heat_legend = [{"t": share_text(v) + "\u00a0%", "style": shade(v)[0]} for v in (0.005, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0)]
    return {"groups": groups, "never": never, "cols": cols, "col_groups": col_groups, "heat": heat, "n_ok": n_ok,
            "heat_legend": heat_legend,
            "integrity": integrity, "audit": audit, "dup_rows": dup_rows, "n_cross": n_cross, "pairs": pairs,
            "dup_cases": dup_cases, "steps": steps, "n_examples_max": AUDIT_EXAMPLES, "content_flags": content_flags, "kinds_all": kinds_all,
            "clean_sql": (ov.get("subset_sql") or {}).get("clean_rooms"), "n_clean": C["n_clean_rooms_irs"],
            "n_dup": C["n_duplicate_copies"], "n_datasets": len(rows)}


def dup_pairs(names: dict) -> list[dict]:
    """reports/corpus/duplicates.csv summarised per dataset (or dataset pair)."""
    f = paths.REPORTS_DIR / "corpus" / "duplicates.csv"
    if not f.exists():
        return []
    agg: dict[str, dict] = {}
    with open(f, newline="") as fh:
        for r in csv.DictReader(fh):
            key = r.get("datasets") or "?"
            a = agg.setdefault(key, {"key": key, "groups": 0, "extra": 0, "examples": []})
            a["groups"] += 1
            a["extra"] += int(r.get("extra") or 0)
            if len(a["examples"]) < 3:
                a["examples"].append(r.get("files") or "")
    out = []
    for key, a in sorted(agg.items(), key=lambda kv: -kv[1]["groups"]):
        ids = [x for x in re.split(r"[+,;| ]+", key) if x]
        out.append({**a, "ids": [{"id": i, "name": names.get(i, i)} for i in ids], "cross": len(ids) > 1})
    return out


# ----------------------------------------------------------------------------- page 3: validation

VAL_METRIC = {"t30": ("T30", "s"), "edt": ("EDT", "s"), "c80": ("C80", "dB"), "d50": ("D50", "")}


def validation_ctx(site: Site) -> dict:
    D = site.data
    names = {d["id"]: d["name"] for d in site.datasets}
    oa = site.dataset("openair").get("validation") or (D.get("validation") or {}).get("openair")
    ace = site.dataset("ace_arrays").get("validation") or (D.get("validation") or {}).get("ace_arrays")
    out: dict = {"openair": None, "ace": None}

    if oa:
        summ = oa.get("summary") or []
        bands = sorted({str(r["band"]) for r in summ}, key=int)
        by = {(r["metric"], str(r["band"])): r for r in summ}
        trows = []
        for mk in ("t30", "edt", "c80", "d50"):
            lab, unit = VAL_METRIC[mk]
            if not any((mk, b) in by for b in bands):
                continue
            ratio = mk in ("t30", "edt")
            trows.append({"m": mk, "label": f"{lab}: spaces compared", "vals": [fmt_int((by.get((mk, b)) or {}).get("n")) for b in bands]})
            if ratio:
                trows.append({"m": mk, "label": f"{lab}: median ratio (ours / published)",
                              "vals": [fmt_num((by.get((mk, b)) or {}).get("median_ratio"), 3) for b in bands]})
                trows.append({"m": mk, "label": f"{lab}: spaces within 10\u00a0%",
                              "vals": [fmt_pct((by.get((mk, b)) or {}).get("within_10pct")) for b in bands]})
            else:
                nd = 1 if mk == "c80" else 2
                u = f" ({unit})" if unit else ""
                trows.append({"m": mk, "label": f"{lab}: median difference{u}",
                              "vals": [num0((by.get((mk, b)) or {}).get("median_diff"), nd) for b in bands]})
                trows.append({"m": mk, "label": f"{lab}: mean absolute difference{u}",
                              "vals": [fmt_num((by.get((mk, b)) or {}).get("mae"), nd) for b in bands]})
        pts = oa.get("points") or []

        def sc(metric, band, *, log, title, unit, nd):
            sel = [p for p in pts if p["metric"] == metric and str(p["band"]) == band
                   and p.get("ours") is not None and p.get("published") is not None]
            data = [{"x": p["published"], "y": p["ours"],
                     "tip": f"{p['space']}\npublished {fmt_num(p['published'], nd)} {unit}\nrirdb {fmt_num(p['ours'], nd)} {unit}"}
                    for p in sel]
            lab = VAL_METRIC[metric][0]
            u = f" ({unit})" if unit else ""
            svg = dual(charts.scatter(data, x_label=f"OpenAIR published {lab}{u}", y_label=f"rirdb {lab}{u}", log=log,
                                      diag=True, band10=log, title=title),
                       charts.scatter(data, x_label=f"OpenAIR published {lab}{u}", y_label=f"rirdb {lab}{u}", log=log,
                                      diag=True, band10=log, title=title, w=340, height=320))
            s = by.get((metric, band)) or {}
            if log:
                sub = (f"{len(sel)} spaces · median ratio {fmt_num(s.get('median_ratio'), 3)} · "
                       f"{fmt_pct(s.get('within_10pct'))} within 10\u00a0% (shaded band) · log axes")
            else:
                sub = (f"{len(sel)} spaces · median difference {num0(s.get('median_diff'), nd)} {unit} · "
                       f"mean absolute difference {fmt_num(s.get('mae'), nd)} {unit}")
            table = [{"name": p["space"], "pub": fmt_num(p["published"], nd), "ours": fmt_num(p["ours"], nd),
                      "d": (fmt_num(p["ours"] / p["published"], 3) if log and p["published"] else fmt_num(p["ours"] - p["published"], nd))}
                     for p in sorted(sel, key=lambda p: p["space"])]
            return {"title": title, "sub": sub, "svg": svg, "table": table, "dlabel": "Ratio" if log else f"Difference{u}"}

        charts_oa = [
            sc("t30", "500", log=True, title="T30 at 500 Hz: rirdb against OpenAIR", unit="s", nd=2),
            sc("t30", "1000", log=True, title="T30 at 1 kHz: rirdb against OpenAIR", unit="s", nd=2),
            sc("edt", "1000", log=True, title="EDT at 1 kHz: rirdb against OpenAIR", unit="s", nd=2),
            sc("c80", "1000", log=False, title="C80 at 1 kHz: rirdb against OpenAIR", unit="dB", nd=1),
        ]
        lim = by.get(("c80", "125")) or {}
        lim_chart = sc("c80", "125", log=False, title="C80 at 125 Hz: rirdb against OpenAIR", unit="dB", nd=1) if lim else None
        hi = [abs(by[("c80", b)]["median_diff"]) for b in bands if ("c80", b) in by and int(b) >= 500
              and by[("c80", b)].get("median_diff") is not None]
        out["openair"] = {"n_spaces": oa.get("n_spaces"), "source": oa.get("source"), "bands": [BAND_LABELS.get(b, b) for b in bands],
                          "rows": trows, "charts": charts_oa, "lim": lim, "lim_chart": lim_chart,
                          "c80_hi_max": max(hi) if hi else None, "c80_250": by.get(("c80", "250")),
                          "d50_125": by.get(("d50", "125")),
                          "t30_ratio_range": rng_of([by[("t30", b)]["median_ratio"] for b in bands if ("t30", b) in by and b != "125"]),
                          "t30_within_range": rng_of([by[("t30", b)]["within_10pct"] for b in bands if ("t30", b) in by and b != "125"]),
                          "n_points": len(pts), "name": names.get("openair", "OpenAIR")}

    if ace:
        summ = ace.get("summary") or []
        order = ["t30_bb", "t30_500", "t30_1000", "t30_2000", "drr_bb"]
        lab = {"t30_bb": "T30, broadband", "t30_500": "T30, 500 Hz", "t30_1000": "T30, 1 kHz", "t30_2000": "T30, 2 kHz",
               "drr_bb": "DRR, full band"}
        srows = sorted(summ, key=lambda r: order.index(r["metric"]) if r["metric"] in order else 99)
        arows = [{"label": lab.get(r["metric"], r["metric"]), "n": r.get("n"),
                  "ratio": r.get("median_ratio"), "w10": r.get("within_10pct"), "diff": r.get("median_diff"),
                  "mae": r.get("mae"), "w2": r.get("within_2db")} for r in srows]
        pts = ace.get("points") or []
        rooms = sorted({str(p.get("room_num")) for p in pts})
        configs = sorted({str(p.get("config")) for p in pts})
        positions = sorted({str(p.get("pos")) for p in pts})

        def sca(metric, *, log, title, unit, nd, xl, yl):
            sel = [p for p in pts if p["metric"] == metric and p.get("ours") is not None and p.get("published") is not None]
            data = [{"x": p["published"], "y": p["ours"],
                     "tip": f"Room {p['room_num']}, position {p['pos']}, {p['config']}\nACE {fmt_num(p['published'], nd)} {unit}\nrirdb {fmt_num(p['ours'], nd)} {unit}"}
                    for p in sel]
            s = next((r for r in summ if r["metric"] == metric), {})
            if log:
                sub = (f"{len(sel)} IRs · median ratio {fmt_num(s.get('median_ratio'), 3)} · "
                       f"{fmt_pct(s.get('within_10pct'))} within 10\u00a0% (shaded band) · log axes")
            else:
                sub = (f"{len(sel)} IRs · median difference {num0(s.get('median_diff'), 2)} {unit} · "
                       f"mean absolute difference {fmt_num(s.get('mae'), 2)} {unit}")
            table = [{"name": f"Room {p['room_num']}, position {p['pos']}, {p['config']}", "pub": fmt_num(p["published"], nd),
                      "ours": fmt_num(p["ours"], nd),
                      "d": fmt_num(p["ours"] / p["published"], 3) if log and p["published"] else fmt_num(p["ours"] - p["published"], nd)}
                     for p in sorted(sel, key=lambda p: (str(p["room_num"]), str(p["pos"]), str(p["config"])))]
            return {"title": title, "sub": sub, "table": table, "dlabel": "Ratio" if log else f"Difference ({unit})",
                    "svg": dual(charts.scatter(data, x_label=xl, y_label=yl, log=log, diag=True, band10=log, title=title),
                                charts.scatter(data, x_label=xl, y_label=yl, log=log, diag=True, band10=log, title=title,
                                               w=340, height=320))}

        # what the comparison covers: IRs (room, position, array), room positions, rooms, parameters
        irs = {(str(p.get("room_num")), str(p.get("pos")), str(p.get("config"))) for p in pts}
        room_pos = {(str(p.get("room_num")), str(p.get("pos"))) for p in pts}
        n_spaces_ace = ((next((d for d in site.datasets if d["id"] == "ace_arrays"), {}).get("index") or {})
                        .get("n_spaces"))
        out["ace"] = {"source": ace.get("source"), "rows": arows, "n_points": len(pts), "rooms": rooms, "configs": configs,
                      "positions": positions, "name": names.get("ace_arrays", "ACE"), "n_irs": len(irs),
                      "n_room_pos": len(room_pos), "n_metrics": len({p["metric"] for p in pts}),
                      "n_spaces": n_spaces_ace,
                      "charts": [sca("t30_bb", log=True, title="Broadband T30: rirdb against ACE ground truth", unit="s", nd=2,
                                     xl="ACE ground-truth T30 (s)", yl="rirdb T30 (s)"),
                                 sca("drr_bb", log=False, title="DRR: rirdb against ACE ground truth", unit="dB", nd=1,
                                     xl="ACE ground-truth DRR (dB)", yl="rirdb DRR (dB)")]}
    return out


def rng_of(v: list) -> tuple | None:
    v = [x for x in v if x is not None]
    return (min(v), max(v)) if v else None


# ----------------------------------------------------------------------------- page 4: CLAP map

def map_ctx(site: Site) -> dict:
    D = site.data
    clap = D.get("clap") or {}
    meta = clap.get("meta") or {}
    sig = clap.get("signals") or {}
    base = clap.get("baseline") or {}
    names = {d["id"]: d["name"] for d in site.datasets}
    rows = sorted(analysed(site), key=lambda d: d["name"].lower())

    bgs = []
    for s, title in (("reverb_vector", "Effect on dry recordings"), ("ir", "The IR itself")):
        if s in sig:
            bgs.append({"key": s, "title": title, "label": sig[s].get("label"), "n": sig[s].get("n"),
                        "svg": charts.map_points([], "../" + sig[s]["background"], size=420,
                                                 title=f"Corpus CLAP map, {title.lower()}: {sig[s].get('n', 0):,} IRs in grey")})

    score = {s: {r["dataset"]: r for r in base.get(s) or []} for s in ("reverb_vector", "ir")}
    panels = []
    bg = "../" + (sig.get("reverb_vector") or {}).get("background", "assets/clap_reverb_vector.png")
    for d in rows:
        c = (site.dataset(d["id"]).get("clap") or {}).get("signals") or {}
        rv = c.get("reverb_vector") or {}
        xy = rv.get("xy") or []
        if not xy:
            continue
        step = max(1, math.ceil(len(xy) / MAP_PANEL_MAX))
        sc = score["reverb_vector"].get(d["id"])
        panels.append({"id": d["id"], "name": d["name"], "n": len(xy), "drawn": len(xy[::step]),
                       "realism": sc.get("realism") if sc else None,
                       "why": None if sc else unscored_reason(d, [s for s in site.data.get("spaces") or [] if s.get("dataset_id") == d["id"]]),
                       "svg": charts.map_points(xy[::step], bg, size=180, r=1.6,
                                                title=f"{d['name']}: {len(xy):,} IRs on the reverb-effect CLAP map",
                                                tip=f"{d['name']}: {len(xy):,} IRs on the map"
                                                    + (f"\nrealism {sc['realism'] * 100:.0f}\u00a0%" if sc and sc.get("realism") is not None else ""))})

    # yardstick table
    ids = sorted(set(score["reverb_vector"]) | set(score["ir"]), key=lambda i: names.get(i, i).lower())
    yard = []
    for i in ids:
        r = {"id": i, "name": names.get(i, i)}
        for s in ("reverb_vector", "ir"):
            x = score[s].get(i) or {}
            q = x.get("nearest_q") or [None] * 5
            r[s] = {"n": x.get("n"), "realism": x.get("realism"), "cov_ir": x.get("coverage_ir"),
                    "cov_room": x.get("coverage_room"), "q25": q[1], "q50": q[2], "q75": q[3]}
        yard.append(r)
    meds = {s: {k: med([r[s][k] for r in yard]) for k in ("realism", "cov_ir", "cov_room", "q50")} for s in ("reverb_vector", "ir")}
    least = sorted([r for r in yard if r["reverb_vector"]["realism"] is not None], key=lambda r: r["reverb_vector"]["realism"])[:3]
    find = None
    for ds in ("meshgrid3d", "tau_srir", "sriracha"):
        fr = finding_rows(site, ds, "CLAP map")
        if fr:
            find = finding_view(fr[0])
            break
    realism_hist = None
    rv_vals = [r["reverb_vector"]["realism"] for r in yard if r["reverb_vector"]["realism"] is not None]
    if rv_vals:
        edges = [i / 10 for i in range(11)]
        counts = [0] * 10
        for v in rv_vals:
            counts[min(int(v * 10), 9)] += 1
        items = [{"label": f"{i * 10}–{(i + 1) * 10}", "value": c,
                  "tip": f"Realism {i * 10}–{(i + 1) * 10}\u00a0%: {c} dataset{'s' if c != 1 else ''}"} for i, c in enumerate(counts)]
        realism_hist = dual(charts.vbars(items, w=480, height=200, title="Datasets per realism band on the reverb-effect map"),
                            charts.vbars(items, w=340, height=190, rotate=True,
                                         title="Datasets per realism band on the reverb-effect map"))
    mid = None  # the two middle scores when their number is even: the median is their mean
    sv = sorted(rv_vals)
    if sv and len(sv) % 2 == 0:
        mid = {"lo": sv[len(sv) // 2 - 1], "hi": sv[len(sv) // 2]}
    realism_rows = sorted([r for r in yard if r["reverb_vector"]["realism"] is not None],
                          key=lambda r: (r["reverb_vector"]["realism"], r["name"].lower()))
    return {"meta": meta, "bgs": bgs, "panels": panels, "yard": yard, "meds": meds, "least": least, "find": find,
            "realism_rows": realism_rows, "mid": mid,
            "panel_max": MAP_PANEL_MAX, "realism_hist": realism_hist, "n_scored": len(rv_vals),
            "n_unscored": sum(1 for p in panels if p["why"])}


def unscored_reason(d: dict, spaces: list[dict] | None = None) -> str:
    kinds = sorted(((d.get("index") or {}).get("kinds_found") or {}).keys())
    if d.get("type") != "measured":
        return "not scored: a production library, not measurements"
    if "room" not in kinds:
        # the index's kind can come from a keyword in the space name ("field" in Huddersfield); the site's space
        # table corrects it (registry/<id>/spaces.csv), but the per-IR kind, and so the score, waits for re-analysis
        fixed = sorted({CATEGORY_LABEL.get(s.get("category"), str(s.get("category"))) for s in spaces or []
                        if s.get("category_fixed") and s.get("ir_kind") == "room"})
        if fixed:
            return (f"not scored: the index's kind \u201c{' and '.join(kinds) or 'unlabelled'}\u201d is a keyword error in the "
                    f"space name (the Spaces page files it under {' and '.join(fixed)})")
        return f"not scored: no room IRs ({', '.join(kinds) or 'no kinds'})"
    return "not scored: no clean measured-room IRs in the map sample"


# ----------------------------------------------------------------------------- render

def render(site: Site) -> None:
    ov = load_overview()
    common = {"subnav": SUBNAV}
    C = site.data["corpus"]
    site.page("analysis/index.html", "analysis_index.html", title="Corpus analysis", nav="analysis", sub="index",
              description=(f"Analysis of {fmt_int(C['n_irs_analysed'])} room impulse responses from "
                           f"{C['n_datasets_analysed']} public datasets: grades, ISO 3382-1 parameters per dataset, "
                           "corpus distributions, per-band reverberation and validity."),
              **common, **index_ctx(site, ov))
    site.page("analysis/quality.html", "analysis_quality.html", title="Signal quality", nav="analysis", sub="quality",
              description=("What the rirdb signal audit checks in every IR, how often each flag fires in each dataset, "
                           "signal integrity, exact duplicates, and what the clean measured rooms leave out."),
              **common, **quality_ctx(site, ov))
    site.page("analysis/validation.html", "analysis_validation.html", title="Validation against published values",
              nav="analysis", sub="validation",
              description=("rirdb's reverberation, clarity and DRR values compared with the values OpenAIR and the ACE "
                           "challenge published for the same IRs."),
              **common, **validation_ctx(site))
    site.page("analysis/map.html", "analysis_map.html", title="CLAP map", nav="analysis", sub="map",
              description=("The frozen CLAP map of the rirdb corpus: how each dataset sits on it, and how real each "
                           "dataset looks to the others (realism and coverage)."),
              **common, **map_ctx(site))
