"""Server-rendered SVG charts for the rirdb website.

Every function returns `Markup` holding one `<svg class="viz">` (or a small HTML block). Marks carry CSS
classes, never literal colours, so the charts follow the light/dark tokens in static/site.css. Every mark
that carries a value has a `data-tip` (site.js shows it on hover and keyboard focus). Specs follow the
dataviz rules: bars <= 24 px thick with a 4 px rounded data end, square at the baseline, a 2 px surface
gap between touching bars, hairline recessive grid, text in ink tokens (never the series colour), a legend
whenever two or more series are drawn.

    hist(h, ref=None, ...)            share-of-IRs histogram on fixed bins, optional reference outline
    bands(bands, ref=None, ...)       per-octave-band box plots (p5/p25/p50/p75/p95) + reference medians
    hbars(items, ...)                 labelled horizontal bars (rates, counts)
    vbars(items, ...)                 column chart (counts per year, per category)
    grade_bar(grades)                 tall segmented bar A/B/C/D with legend
    stack100(rows, keys, ...)         one 100 % stacked bar per row (e.g. grades per dataset)
    map_points(xy, background, ...)   dataset points over the frozen CLAP map background
    scatter(points, ...)              x vs y with optional y = x and +-10 % guides (validation)
    range_mini(p10, p50, p90, ...)    inline p10-p90 bar with median dot (tables)
    strip(values, ...)                HTML heat strip (validity per band)
    legend(items)                     HTML legend list
"""

from __future__ import annotations

import math
from html import escape as _esc

from markupsafe import Markup

FONT_W = 6.1  # average glyph width (px) of 11 px body text, for label fitting
PCT = " %"  # no-break space: a tooltip or legend never wraps "%" away from its number


def esc(s) -> str:
    return _esc(str(s), quote=True)


def _f(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".") if abs(v - round(v)) > 1e-9 else str(int(round(v)))


def fmt_val(v, nd: int = 2, unit: str = "") -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    s = f"{v:,.{nd}f}"
    if s.startswith("-"):
        s = "−" + s[1:]
    return f"{s} {unit}".strip() if unit else s


def tick_label(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1000:
        s = f"{v / 1000:g}k"
    elif a >= 1:
        s = f"{v:g}"
    else:
        s = f"{v:.3g}"
    return s.replace("-", "−")


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    span = hi - lo
    raw = span / max(n, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.ceil(lo / step - 1e-9) * step
    out, v = [], start
    while v <= hi + 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def log_ticks(lo: float, hi: float) -> list[float]:
    out = []
    for e in range(math.floor(math.log10(lo)) - 1, math.ceil(math.log10(hi)) + 1):
        for m in (1, 2, 5):
            v = m * 10 ** e
            if lo * 0.999 <= v <= hi * 1.001:
                out.append(v)
    return out


def bar_up(x: float, y_base: float, w: float, h: float, r: float = 4.0) -> str:
    """Path of a column growing up from y_base: square at the baseline, rounded data end."""
    if h <= 0 or w <= 0:
        return ""
    r = min(r, w / 2, h)
    y = y_base - h
    return (f"M{x:.2f},{y_base:.2f}V{y + r:.2f}Q{x:.2f},{y:.2f} {x + r:.2f},{y:.2f}"
            f"H{x + w - r:.2f}Q{x + w:.2f},{y:.2f} {x + w:.2f},{y + r:.2f}V{y_base:.2f}Z")


def bar_right(x0: float, y: float, w: float, h: float, r: float = 4.0) -> str:
    """Path of a bar growing right from x0: square at the baseline, rounded data end."""
    if h <= 0 or w <= 0:
        return ""
    r = min(r, h / 2, w)
    x = x0 + w
    return (f"M{x0:.2f},{y:.2f}H{x - r:.2f}Q{x:.2f},{y:.2f} {x:.2f},{y + r:.2f}"
            f"V{y + h - r:.2f}Q{x:.2f},{y + h:.2f} {x - r:.2f},{y + h:.2f}H{x0:.2f}Z")


def svg_open(w: int, h: int, label: str, cls: str = "viz") -> str:
    return (f'<svg class="{cls}" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="{esc(label)}" preserveAspectRatio="xMinYMin meet">')


def legend(items: list[tuple[str, str]]) -> Markup:
    """items: (css class of the swatch: 's1' | 'line' | 'g-A' ..., label)."""
    lis = []
    for cls, label in items:
        if cls == "line":
            sw = '<span class="sw line" style="background:var(--ink-2)"></span>'
        elif cls == "s1":
            sw = '<span class="sw" style="background:var(--series-1)"></span>'
        elif cls.startswith("g-"):
            var = {"A": "--ord-3", "B": "--ord-2", "C": "--ord-1", "D": "--other"}[cls[2:]]
            sw = f'<span class="sw" style="background:var({var})"></span>'
        elif cls == "ring":
            sw = '<span class="sw" style="background:var(--surface);border:1.5px solid var(--ink-2);border-radius:50%"></span>'
        elif cls == "box":
            sw = '<span class="sw" style="background:color-mix(in srgb,var(--series-1) 35%,var(--surface));border-top:2px solid var(--series-1)"></span>'
        else:
            sw = f'<span class="sw" style="background:var({cls})"></span>'
        lis.append(f"<li>{sw}{esc(label)}</li>")
    return Markup('<ul class="legend">' + "".join(lis) + "</ul>")


# ----------------------------------------------------------------------------- histogram

def hist(h: dict, ref: dict | None = None, *, log_x: bool = False, unit: str = "", nd: int = 2,
         label: str = "This dataset", ref_label: str = "Corpus (one value per space)", w: int = 560,
         height: int = 200, title: str = "") -> Markup:
    """`h` and `ref` are data.hist() dicts on the same edges. Bars: share of this dataset's IRs per bin;
    reference: share of the corpus spaces per bin, drawn as an outline."""
    edges = h["edges"]
    counts = h["counts"]
    n = max(sum(counts), 1)
    share = [c / n for c in counts]
    rshare = None
    if ref and ref.get("edges") == edges and sum(ref["counts"]):
        rn = sum(ref["counts"])
        rshare = [c / rn for c in ref["counts"]]
    ml, mr, mt, mb = 40, 10, 8, 30
    pw, ph = w - ml - mr, height - mt - mb
    lo, hi = edges[0], edges[-1]
    if log_x:
        X = lambda v: ml + (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo)) * pw  # noqa: E731
    else:
        X = lambda v: ml + (v - lo) / (hi - lo) * pw  # noqa: E731
    top = max(share + (rshare or []) + [1e-9])
    yt = nice_ticks(0, top, 4)
    ymax = yt[-1] if yt[-1] >= top else top
    Y = lambda v: mt + ph - v / ymax * ph  # noqa: E731
    out = [svg_open(w, height, title or "Histogram")]
    for t in yt:
        out.append(f'<line class="gl" x1="{ml}" x2="{ml + pw}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>')
        out.append(f'<text x="{ml - 6}" y="{Y(t) + 3.5:.1f}" text-anchor="end">{f"{t * 100:.1f}".rstrip("0").rstrip(".")}{PCT}</text>')
    xt = log_ticks(lo, hi) if log_x else nice_ticks(lo, hi, 6)
    for t in xt:
        out.append(f'<line class="ax" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{mt + ph}" y2="{mt + ph + 4}"/>')
        out.append(f'<text x="{X(t):.1f}" y="{mt + ph + 16}" text-anchor="middle">{tick_label(t)}</text>')
    if unit:
        out.append(f'<text x="{ml + pw}" y="{height - 1}" text-anchor="end">{esc(unit)}</text>')
    for i, s in enumerate(share):
        x0, x1 = X(edges[i]), X(edges[i + 1])
        bw = min(x1 - x0 - 2, 24)
        if bw <= 0.5:
            bw = max(x1 - x0 - 0.5, 0.5)
        xc = (x0 + x1) / 2
        tip = f"{label}: {fmt_val(edges[i], nd)}–{fmt_val(edges[i + 1], nd)} {unit}\n{s * 100:.1f}{PCT} of IRs ({counts[i]:,})"
        if rshare is not None:
            tip += f"\n{ref_label}: {rshare[i] * 100:.1f}{PCT}"
        if s > 0:
            out.append(f'<path class="s1" d="{bar_up(xc - bw / 2, Y(0), bw, Y(0) - Y(s), min(4, bw / 2))}"/>')
        out.append(f'<rect class="hit" x="{x0:.2f}" y="{mt}" width="{max(x1 - x0, 1):.2f}" height="{ph}" data-tip="{esc(tip)}"/>')
    if rshare is not None:
        pts = []
        for i, s in enumerate(rshare):
            pts += [(X(edges[i]), Y(s)), (X(edges[i + 1]), Y(s))]
        d = f"M{pts[0][0]:.1f},{Y(0):.1f}" + "".join(f"L{x:.1f},{y:.1f}" for x, y in pts) + f"L{pts[-1][0]:.1f},{Y(0):.1f}"
        out.append(f'<path class="ref-fill" d="{d}Z" pointer-events="none"/>')
        out.append(f'<path class="ref-line" d="{d}" pointer-events="none"/>')
    out.append(f'<line class="ax" x1="{ml}" x2="{ml + pw}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    out.append("</svg>")
    leg = legend([("s1", f"{label} ({h['n']:,} IRs)"), ("line", f"{ref_label}" + (f" ({ref['n']:,})" if ref else ""))]) if rshare is not None else Markup("")
    return Markup("".join(out)) + leg


# ----------------------------------------------------------------------------- per-band boxes

def bands(bx: dict, ref: dict | None = None, *, unit: str = "s", nd: int = 2, validity: dict | None = None,
          w: int = 560, height: int = 230, title: str = "", band_keys=None, zero: bool = True) -> Markup:
    """bx: {band: {"n", "q": [p5, p25, p50, p75, p95]}}; ref: {band: {"q": [...]}} (median drawn)."""
    keys = band_keys or [k for k in ("63", "125", "250", "500", "1000", "2000", "4000", "8000") if k in bx]
    vals = [v for k in keys for v in (bx.get(k) or {}).get("q", [])]
    rvals = [ref[k]["q"][2] for k in keys if ref and ref.get(k, {}).get("q")]
    if not vals:
        return Markup('<p class="muted small">No valid values in any band.</p>')
    lo, hi = min(vals + rvals), max(vals + rvals)
    if zero and lo > 0:
        lo = 0
    yt = nice_ticks(lo, hi, 5)
    y0, y1 = min(yt[0], lo), max(yt[-1], hi)
    ml, mr, mt, mb = 44, 10, 10, 30
    pw, ph = w - ml - mr, height - mt - mb
    Y = lambda v: mt + ph - (v - y0) / (y1 - y0 or 1) * ph  # noqa: E731
    slot = pw / len(keys)
    out = [svg_open(w, height, title or "Per-band distribution")]
    for t in yt:
        out.append(f'<line class="gl" x1="{ml}" x2="{ml + pw}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>')
        out.append(f'<text x="{ml - 6}" y="{Y(t) + 3.5:.1f}" text-anchor="end">{tick_label(t)}</text>')
    if y0 < 0 < y1:
        out.append(f'<line class="ax" x1="{ml}" x2="{ml + pw}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    for i, k in enumerate(keys):
        xc = ml + slot * (i + 0.5)
        lab = "bb" if k == "bb" else (f"{int(k) // 1000}k" if int(k) >= 1000 else k)
        out.append(f'<text class="lbl" x="{xc:.1f}" y="{mt + ph + 16}" text-anchor="middle">{lab}</text>')
        b = bx.get(k) or {}
        q = b.get("q")
        tip = f"{lab} Hz octave" if k != "bb" else "Broadband"
        if q:
            bw = min(slot * 0.5, 22)
            out.append(f'<line class="whisk" x1="{xc:.1f}" x2="{xc:.1f}" y1="{Y(q[0]):.1f}" y2="{Y(q[4]):.1f}"/>')
            out.append(f'<rect class="box" x="{xc - bw / 2:.1f}" y="{Y(q[3]):.1f}" width="{bw:.1f}" height="{max(Y(q[1]) - Y(q[3]), 1):.1f}" rx="2"/>')
            out.append(f'<line class="med" x1="{xc - bw / 2:.1f}" x2="{xc + bw / 2:.1f}" y1="{Y(q[2]):.1f}" y2="{Y(q[2]):.1f}"/>')
            tip += (f"\nmedian {fmt_val(q[2], nd, unit)} (n = {b['n']:,})\n25–75{PCT}: {fmt_val(q[1], nd)}–{fmt_val(q[3], nd)}"
                    f"\n5–95{PCT}: {fmt_val(q[0], nd)}–{fmt_val(q[4], nd)}")
        else:
            tip += "\nno valid values"
            out.append(f'<text x="{xc:.1f}" y="{mt + ph - 6}" text-anchor="middle">–</text>')
        if validity and validity.get(k) is not None:
            tip += f"\nvalid in {validity[k] * 100:.0f}{PCT} of IRs"
        r = (ref or {}).get(k, {}).get("q")
        if r:
            out.append(f'<circle class="refmed" cx="{xc + min(slot * 0.5, 22) / 2 + 7:.1f}" cy="{Y(r[2]):.1f}" r="4"/>')
            tip += f"\ncorpus median {fmt_val(r[2], nd, unit)}"
        out.append(f'<rect class="hit" x="{xc - slot / 2:.1f}" y="{mt}" width="{slot:.1f}" height="{ph + 20}" data-tip="{esc(tip)}"/>')
    out.append(f'<text x="{ml - 6}" y="{mt - 1}" text-anchor="end">{esc(unit)}</text>' if unit else "")
    out.append("</svg>")
    items = [("box", f"This dataset: median, 25–75{PCT} box, 5–95{PCT} whisker")]
    if ref:
        items.append(("ring", "Corpus median (clean measured rooms, one value per space)"))
    return Markup("".join(out)) + legend(items)


# ----------------------------------------------------------------------------- bars

def hbars(items: list[dict], *, w: int = 560, fmt=None, max_v: float | None = None, label_w: int | None = None,
          title: str = "") -> Markup:
    """items: {"label", "value", "tip"?, "href"?, "text"? (printed value)}. Value labels at the bar tip."""
    if not items:
        return Markup('<p class="muted small">Nothing to show.</p>')
    fmt = fmt or (lambda v: f"{v:,.0f}")
    lw = label_w or min(max(int(max(len(str(i["label"])) for i in items) * FONT_W) + 12, 80), 240)
    row, bh = 24, 10
    height = row * len(items) + 6
    vmax = max_v or max((i["value"] or 0) for i in items) or 1
    # room right of the longest bar for its value label ("100 % (53,446)" needs about 95 px)
    rm = max(70, int(max(len(str(i.get("text") or fmt(i["value"] or 0))) for i in items) * FONT_W) + 10)
    pw = w - lw - rm
    out = [svg_open(w, height, title or "Bar chart")]
    for k, it in enumerate(items):
        y = 3 + k * row
        v = it["value"] or 0
        bw = max(v / vmax * pw, 0)
        lab = str(it["label"])
        maxc = int((lw - 10) / FONT_W)
        if len(lab) > maxc:
            lab = lab[: maxc - 1] + "…"
        text = f'<text class="lbl" x="{lw - 8}" y="{y + row / 2 + 4:.1f}" text-anchor="end">{esc(lab)}</text>'
        if it.get("href"):
            text = f'<a href="{esc(it["href"])}">{text}</a>'
        out.append(text)
        out.append(f'<rect class="track" x="{lw}" y="{y + (row - bh) / 2:.1f}" width="{pw}" height="{bh}" rx="{bh / 2}"/>')
        if bw > 0:
            out.append(f'<path class="s1" d="{bar_right(lw, y + (row - bh) / 2, max(bw, 2), bh, 4)}"/>')
        out.append(f'<text class="val" x="{lw + max(bw, 0) + 6:.1f}" y="{y + row / 2 + 4:.1f}">{esc(it.get("text") or fmt(v))}</text>')
        tip = it.get("tip") or f"{it['label']}: {it.get('text') or fmt(v)}"
        out.append(f'<rect class="hit" x="0" y="{y}" width="{w}" height="{row}" data-tip="{esc(tip)}"/>')
    out.append("</svg>")
    return Markup("".join(out))


def vbars(items: list[dict], *, w: int = 560, height: int = 200, fmt=None, title: str = "", rotate: bool = False) -> Markup:
    """Column chart. items: {"label", "value", "tip"?}."""
    if not items:
        return Markup('<p class="muted small">Nothing to show.</p>')
    fmt = fmt or (lambda v: f"{v:,.0f}")
    ml, mr, mt, mb = 40, 6, 14, 44 if rotate else 26
    pw, ph = w - ml - mr, height - mt - mb
    vmax = max((i["value"] or 0) for i in items) or 1
    yt = nice_ticks(0, vmax, 4)
    ymax = max(yt[-1], vmax)
    Y = lambda v: mt + ph - v / ymax * ph  # noqa: E731
    slot = pw / len(items)
    bw = min(slot - 2, 24)
    out = [svg_open(w, height, title or "Column chart")]
    for t in yt:
        out.append(f'<line class="gl" x1="{ml}" x2="{ml + pw}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>')
        out.append(f'<text x="{ml - 6}" y="{Y(t) + 3.5:.1f}" text-anchor="end">{tick_label(t)}</text>')
    every = max(1, math.ceil(len(items) * 28 / pw)) if not rotate else 1
    for i, it in enumerate(items):
        xc = ml + slot * (i + 0.5)
        v = it["value"] or 0
        if v > 0:
            out.append(f'<path class="s1" d="{bar_up(xc - bw / 2, Y(0), bw, Y(0) - Y(v), min(4, bw / 2))}"/>')
        if i % every == 0:
            lab = esc(it["label"])
            if rotate:
                out.append(f'<text class="lbl" transform="translate({xc + 3:.1f},{mt + ph + 8}) rotate(-45)" text-anchor="end">{lab}</text>')
            else:
                out.append(f'<text class="lbl" x="{xc:.1f}" y="{mt + ph + 16}" text-anchor="middle">{lab}</text>')
        tip = it.get("tip") or f"{it['label']}: {fmt(v)}"
        out.append(f'<rect class="hit" x="{xc - slot / 2:.1f}" y="{mt}" width="{slot:.1f}" height="{ph}" data-tip="{esc(tip)}"/>')
    out.append(f'<line class="ax" x1="{ml}" x2="{ml + pw}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    out.append("</svg>")
    return Markup("".join(out))


GRADE_TEXT = {"A": "T30 valid 125 Hz–4 kHz", "B": "T20/T30 valid 250 Hz–2 kHz", "C": "partly valid", "D": "nothing valid"}


def grade_bar(grades: dict, *, w: int = 560, title: str = "Grades") -> Markup:
    """Tall segmented bar (28 px): one block per grade, share printed inside when it fits."""
    keys = [k for k in "ABCD" if grades.get(k)]
    total = sum(grades.get(k, 0) for k in "ABCD")
    if not total:
        return Markup('<p class="muted small">No graded IRs.</p>')
    h, gap = 28, 2
    avail = w - gap * (len(keys) - 1)
    out = [svg_open(w, h, title)]
    x = 0.0
    for k in keys:
        n = grades[k]
        bw = n / total * avail
        share = n / total
        out.append(f'<rect class="g-{k}" x="{x:.2f}" y="0" width="{max(bw, 1):.2f}" height="{h}" rx="4" '
                   f'data-tip="{esc(f"Grade {k} ({GRADE_TEXT[k]}): {n:,} IRs, {share * 100:.1f}{PCT}")}"/>')
        lab = f"{k} {share * 100:.0f}{PCT}" if share >= 0.005 else k
        if bw >= len(lab) * FONT_W + 12:
            out.append(f'<text class="on-{k}" x="{x + 8:.2f}" y="{h / 2 + 4}" style="font-weight:700" pointer-events="none">{esc(lab)}</text>')
        x += bw + gap
    out.append("</svg>")
    return Markup("".join(out)) + legend([(f"g-{k}", f"{k}: {GRADE_TEXT[k]}") for k in "ABCD"])


def stack100(rows: list[dict], *, keys=("A", "B", "C", "D"), w: int = 760, label_w: int = 190, title: str = "") -> Markup:
    """rows: {"label", "href"?, "parts": {key: n}}. One 100 % stacked bar per row, n at the right."""
    row, bh = 24, 14
    height = row * len(rows) + 6
    pw = w - label_w - 64
    out = [svg_open(w, height, title or "Stacked bars")]
    for i, r in enumerate(rows):
        y = 3 + i * row
        total = sum(r["parts"].get(k, 0) for k in keys)
        lab = str(r["label"])
        maxc = int((label_w - 10) / FONT_W)
        if len(lab) > maxc:
            lab = lab[: maxc - 1] + "…"
        t = f'<text class="lbl" x="{label_w - 8}" y="{y + row / 2 + 4:.1f}" text-anchor="end">{esc(lab)}</text>'
        out.append(f'<a href="{esc(r["href"])}">{t}</a>' if r.get("href") else t)
        if not total:
            continue
        x = float(label_w)
        present = [k for k in keys if r["parts"].get(k)]
        avail = pw - 2 * (len(present) - 1)
        for k in present:
            n = r["parts"][k]
            bw = n / total * avail
            out.append(f'<rect class="g-{k}" x="{x:.2f}" y="{y + (row - bh) / 2:.1f}" width="{max(bw, 0.8):.2f}" height="{bh}" rx="3" '
                       f'data-tip="{esc(f"{r["label"]}: grade {k}, {n:,} IRs ({n / total * 100:.1f}{PCT})")}"/>')
            x += bw + 2
        out.append(f'<text class="val" x="{label_w + pw + 6}" y="{y + row / 2 + 4:.1f}">{total:,}</text>')
    out.append("</svg>")
    return Markup("".join(out))


# ----------------------------------------------------------------------------- map, scatter

MAP_PAD = 0.03  # must match data.MAP_PAD (the background PNG covers [-pad, 1 + pad])


def map_points(xy: list, background: str, *, size: int = 420, title: str = "CLAP map", r: float = 2.2,
               tip: str = "") -> Markup:
    """xy in 1/1000 of the unit square (data.clap), y up. `background` is the corpus PNG href."""
    s = size
    span = 1 + 2 * MAP_PAD
    P = lambda v: (v / 1000 + MAP_PAD) / span * s  # noqa: E731
    out = [svg_open(s, s, title),
           f'<rect class="track" x="0" y="0" width="{s}" height="{s}" rx="6" opacity=".5"/>',
           f'<image href="{esc(background)}" x="0" y="0" width="{s}" height="{s}" preserveAspectRatio="none"/>']
    out.append('<g class="pts">')
    for x, y in xy:
        out.append(f'<circle class="pt" cx="{P(x):.1f}" cy="{s - P(y):.1f}" r="{r}"/>')
    out.append("</g>")
    if tip:
        out.append(f'<rect class="hit" x="0" y="0" width="{s}" height="{s}" data-tip="{esc(tip)}"/>')
    out.append("</svg>")
    return Markup("".join(out))


def scatter(points: list[dict], *, x_label: str, y_label: str, log: bool = False, diag: bool = True, band10: bool = False,
            w: int = 420, height: int = 380, title: str = "") -> Markup:
    """points: {"x", "y", "tip"}. y = x line and optional +-10 % band."""
    pts = [p for p in points if p.get("x") is not None and p.get("y") is not None and (not log or (p["x"] > 0 and p["y"] > 0))]
    if not pts:
        return Markup('<p class="muted small">No points.</p>')
    xs = [p["x"] for p in pts] + [p["y"] for p in pts]
    lo, hi = min(xs), max(xs)
    if log:
        lo, hi = lo / 1.15, hi * 1.15
        T = lambda v: math.log(v)  # noqa: E731
        ticks = log_ticks(lo, hi)
    else:
        pad = (hi - lo) * 0.06 or 1
        lo, hi = lo - pad, hi + pad
        T = lambda v: v  # noqa: E731
        ticks = nice_ticks(lo, hi, 5)
    ml, mr, mt, mb = 46, 10, 10, 40
    pw, ph = w - ml - mr, height - mt - mb
    X = lambda v: ml + (T(v) - T(lo)) / (T(hi) - T(lo)) * pw  # noqa: E731
    Y = lambda v: mt + ph - (T(v) - T(lo)) / (T(hi) - T(lo)) * ph  # noqa: E731
    out = [svg_open(w, height, title or f"{y_label} vs {x_label}")]
    for t in ticks:
        out.append(f'<line class="gl" x1="{ml}" x2="{ml + pw}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>')
        out.append(f'<line class="gl" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{mt}" y2="{mt + ph}"/>')
        out.append(f'<text x="{ml - 6}" y="{Y(t) + 3.5:.1f}" text-anchor="end">{tick_label(t)}</text>')
        out.append(f'<text x="{X(t):.1f}" y="{mt + ph + 15}" text-anchor="middle">{tick_label(t)}</text>')
    if band10:
        d = (f"M{X(lo):.1f},{Y(lo * 1.1):.1f}L{X(hi / 1.1):.1f},{Y(hi):.1f}L{X(hi):.1f},{Y(hi):.1f}"
             f"L{X(hi):.1f},{Y(hi * 0.9):.1f}L{X(lo / 0.9 if lo / 0.9 < hi else lo):.1f},{Y(lo):.1f}L{X(lo):.1f},{Y(lo):.1f}Z")
        out.append(f'<path class="ref-fill" d="{d}"/>')
    if diag:
        out.append(f'<line class="ref-line" x1="{X(lo):.1f}" y1="{Y(lo):.1f}" x2="{X(hi):.1f}" y2="{Y(hi):.1f}"/>')
    for p in pts:
        out.append(f'<circle class="pt" cx="{X(p["x"]):.1f}" cy="{Y(p["y"]):.1f}" r="4" data-tip="{esc(p.get("tip", ""))}"/>')
    out.append(f'<text class="lbl" x="{ml + pw / 2:.1f}" y="{height - 4}" text-anchor="middle">{esc(x_label)}</text>')
    out.append(f'<text class="lbl" transform="translate(11,{mt + ph / 2:.1f}) rotate(-90)" text-anchor="middle">{esc(y_label)}</text>')
    out.append("</svg>")
    return Markup("".join(out))


# ----------------------------------------------------------------------------- inline

def range_mini(p10, p50, p90, *, lo: float = 0.1, hi: float = 10.0, log: bool = True, tip: str = "", w: int = 120) -> Markup:
    """Inline p10-p90 bar with a median dot (catalogue / dataset tables)."""
    if p50 is None:
        return Markup("")
    T = (lambda v: math.log(max(v, lo * 1.0001))) if log else (lambda v: v)
    X = lambda v: 4 + (min(max(T(v), T(lo)), T(hi)) - T(lo)) / (T(hi) - T(lo)) * (w - 8)  # noqa: E731
    a, b = X(p10 if p10 is not None else p50), X(p90 if p90 is not None else p50)
    return Markup(f'<svg class="rng" viewBox="0 0 {w} 12" width="{w}" height="12" role="img" aria-label="{esc(tip)}"'
                  f' data-tip="{esc(tip)}"><rect class="t" x="0" y="4" width="{w}" height="4" rx="2"/>'
                  f'<rect class="b" x="{a:.1f}" y="3" width="{max(b - a, 2):.1f}" height="6" rx="3"/>'
                  f'<circle class="m" cx="{X(p50):.1f}" cy="6" r="4"/></svg>')


def strip(values: dict, keys: list[str], *, labels: dict | None = None, fmt=None, head: bool = True) -> Markup:
    """HTML heat strip: one cell per key, shaded by a 0-1 value (sequential, one hue). Cells use the
    theme-safe .heat-c class (ink text on a capped mix). head=False leaves out the label row. A cell shows the
    share in per cent without the sign (say "in per cent" in the title), so "100" fits a phone-width cell;
    the tooltip gives it with the sign."""
    fmt = fmt or (lambda v: f"{v * 100:.0f}")
    cells = []
    for k in keys:
        v = values.get(k)
        if v is None:
            cells.append('<div style="background:var(--sunk);color:var(--muted)">–</div>')
            continue
        tip = f"{(labels or {}).get(k, k)}: {v * 100:.0f}{PCT}"
        cells.append(f'<div class="heat-c" style="--w:{0.12 + min(max(v, 0), 1) * 0.88:.3f}" '
                     f'data-tip="{esc(tip)}">{fmt(v)}</div>')
    n = len(keys)
    top = ""
    if head:
        top = f'<div class="strip strip-head" style="--n:{n}">' + "".join(
            f"<div>{esc((labels or {}).get(k, k))}</div>" for k in keys) + "</div>"
    return Markup(f'{top}<div class="strip" style="--n:{n}">{"".join(cells)}</div>')
