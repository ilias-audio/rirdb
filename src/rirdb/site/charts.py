"""The charts of the website, as SVG written in Python: no chart library, and they show without JavaScript.

Every mark has a <title>, which the browser shows on hover (site.js turns it into a nicer tooltip), and every
chart sits next to a table of its numbers. Colours come from style.css (classes mark, wash, grid, axis), so
they follow the light or dark theme.
"""

from __future__ import annotations

import math
from html import escape

W, H = 400, 190                   # the drawing's own units; it scales to the width of its box (about 1:1 on a
                                  # desktop, so the 11 px labels stay readable on a phone too)
LEFT, RIGHT, TOP, BOTTOM = 44, 8, 10, 30


def _svg(body: str, label: str, w: int = W, h: int = H) -> str:
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{escape(label)}" '
            f'preserveAspectRatio="xMidYMid meet">{body}</svg>')


def _column(x: float, y: float, w: float, base: float) -> str:
    """A column from the baseline up to y: square at the baseline, 4 px rounded at the top (data end)."""
    r = min(4.0, w / 2, max(base - y, 0) / 2)
    return (f"M{x:.1f},{base:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}H{x + w - r:.1f}"
            f"Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}V{base:.1f}Z")


def _nice_max(n: int) -> int:
    """The top of a count axis: 1, 2 or 5 times a power of ten, at or above n."""
    if n <= 5:
        return max(n, 1)
    p = 10 ** math.floor(math.log10(n))
    return next(m * p for m in (1, 2, 5, 10) if m * p >= n)


def _count(n: float) -> str:
    return f"{n / 1000:g}K" if n >= 10000 else f"{n:,.0f}"


def histogram(counts: list[int], edges: list[float], unit: str, label: str, ticks: list[float],
              log: bool = False, fmt=lambda v: f"{v:g}") -> str:
    """Columns of counts over bins of equal width on the axis (equal in log time when `log`)."""
    n_bins = len(counts)
    plot_w, base = W - LEFT - RIGHT, H - BOTTOM
    top = _nice_max(max(counts) if counts else 0)
    step = plot_w / n_bins
    pos = (lambda v: LEFT + plot_w * (math.log(v / edges[0]) / math.log(edges[-1] / edges[0]))) if log else \
          (lambda v: LEFT + plot_w * (v - edges[0]) / (edges[-1] - edges[0]))
    parts = []
    for frac in (0, 0.5, 1):                                    # count gridlines: 0, half, top
        y = base - frac * (base - TOP)
        parts.append(f'<line class="grid" x1="{LEFT}" x2="{W - RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text class="tick" x="{LEFT - 6}" y="{y + 4:.1f}" text-anchor="end">{_count(frac * top)}</text>')
    for t in ticks:
        x = pos(t)
        parts.append(f'<text class="tick" x="{x:.1f}" y="{base + 16}" text-anchor="middle">{escape(fmt(t))}</text>')
    parts.append(f'<text class="tick" x="{W - RIGHT}" y="{H - 2}" text-anchor="end">{escape(unit)}</text>')
    w = min(24.0, step - 2)                                     # 2 px of air between neighbours
    for i, n in enumerate(counts):
        lo, hi = edges[i], edges[i + 1]
        first, last = i == 0, i == n_bins - 1
        span = (f"below {fmt(hi)}" if first else f"{fmt(lo)} or more" if last else f"{fmt(lo)} to {fmt(hi)}")
        tip = f"{n:,} IRs: {span} {unit}"
        x = LEFT + i * step + (step - w) / 2
        mark = f'<path class="mark" d="{_column(x, base - (base - TOP) * n / top, w, base)}"/>' if n else ""
        parts.append(f'<g class="hit" tabindex="0"><title>{escape(tip)}</title>'
                     f'<rect class="target" x="{LEFT + i * step:.1f}" y="{TOP}" width="{step:.1f}" '
                     f'height="{base - TOP}"/>{mark}</g>')
    parts.append(f'<line class="axis" x1="{LEFT}" x2="{W - RIGHT}" y1="{base}" y2="{base}"/>')
    return _svg("".join(parts), label)


def band_boxes(stats: dict[int, dict | None], label: str, lo: float = 0.05, hi: float = 25) -> str:
    """One box per octave on a log time axis: the box spans the 25th to 75th percentile, the line through it
    is the median, the whiskers reach the 5th and 95th percentile."""
    bands = list(stats)
    plot_w, base = W - LEFT - RIGHT, H - BOTTOM
    step = plot_w / len(bands)
    y = lambda v: base - (base - TOP) * math.log(min(max(v, lo), hi) / lo) / math.log(hi / lo)
    parts = []
    for t in (0.1, 0.3, 1, 3, 10):
        parts.append(f'<line class="grid" x1="{LEFT}" x2="{W - RIGHT}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                     f'<text class="tick" x="{LEFT - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{t:g} s</text>')
    w = min(24.0, step - 2)
    for i, b in enumerate(bands):
        cx = LEFT + (i + 0.5) * step
        name = f"{b:g} Hz" if b < 1000 else f"{b / 1000:g} kHz"
        parts.append(f'<text class="tick" x="{cx:.1f}" y="{base + 16}" text-anchor="middle">{name}</text>')
        s = stats[b]
        if not s:
            parts.append(f'<g class="hit" tabindex="0"><title>{name}: no valid T30</title>'
                         f'<rect class="target" x="{cx - step / 2:.1f}" y="{TOP}" width="{step:.1f}" '
                         f'height="{base - TOP}"/></g>')
            continue
        tip = (f"{name}: median {s['p50']:.2f} s, half of the IRs between {s['p25']:.2f} and {s['p75']:.2f} s, "
               f"90 % between {s['p5']:.2f} and {s['p95']:.2f} s ({s['n']:,} IRs with a valid T30)")
        box_top, box_bottom = y(s["p75"]), y(s["p25"])
        parts.append(
            f'<g class="hit" tabindex="0"><title>{escape(tip)}</title>'
            f'<rect class="target" x="{cx - step / 2:.1f}" y="{TOP}" width="{step:.1f}" height="{base - TOP}"/>'
            f'<line class="whisker" x1="{cx:.1f}" x2="{cx:.1f}" y1="{y(s["p95"]):.1f}" y2="{y(s["p5"]):.1f}"/>'
            f'<rect class="mark" x="{cx - w / 2:.1f}" y="{box_top:.1f}" width="{w:.1f}" '
            f'height="{max(box_bottom - box_top, 2):.1f}" rx="3"/>'
            f'<line class="median" x1="{cx - w / 2:.1f}" x2="{cx + w / 2:.1f}" y1="{y(s["p50"]):.1f}" '
            f'y2="{y(s["p50"]):.1f}"/></g>')
    parts.append(f'<line class="axis" x1="{LEFT}" x2="{W - RIGHT}" y1="{base}" y2="{base}"/>')
    return _svg("".join(parts), label)


def band_curve(values: list[float | None], names: list[str], lo: float = 0.1, hi: float = 10) -> str:
    """A small line of the median T30 per octave of one space, on a fixed log axis (0.1 to 10 s), so the curves
    of two spaces compare. A missing octave breaks the line."""
    w, h, pad = 96, 24, 3
    x = lambda i: pad + (w - 2 * pad) * i / (len(values) - 1)
    y = lambda v: h - pad - (h - 2 * pad) * math.log(min(max(v, lo), hi) / lo) / math.log(hi / lo)
    segments, current = [], []
    for i, v in enumerate(values):
        if v is None:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(f"{x(i):.1f},{y(v):.1f}")
    if current:
        segments.append(current)
    if not segments:
        return ""
    lines = "".join(f'<polyline class="line" points="{" ".join(s)}"/>' if len(s) > 1 else
                    f'<circle class="dot" cx="{s[0].split(",")[0]}" cy="{s[0].split(",")[1]}" r="2"/>'
                    for s in segments)
    tip = ", ".join(f"{n} {v:.2f} s" for n, v in zip(names, values) if v is not None)
    return (f'<svg class="curve" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="median T30 per octave: {escape(tip)}"><title>Median T30: {escape(tip)}</title>'
            f'<line class="grid" x1="{pad}" x2="{w - pad}" y1="{y(1):.1f}" y2="{y(1):.1f}"/>{lines}</svg>')


def scatter(points: list[dict], label: str, x_lo: float = 0.05, x_hi: float = 25, y_lo: float = -40,
            y_hi: float = 40) -> str:
    """The spaces page: one dot per space, median T30 (log) against median DRR. Each dot has data-i, the space's
    index in the page's data, so site.js can hide the dots a filter removes."""
    w, h = 960, 400                                             # wider and taller: 900 dots, a page wide
    plot_w, base = w - LEFT - RIGHT, h - BOTTOM
    x = lambda v: LEFT + plot_w * math.log(min(max(v, x_lo), x_hi) / x_lo) / math.log(x_hi / x_lo)
    y = lambda v: base - (base - TOP) * (min(max(v, y_lo), y_hi) - y_lo) / (y_hi - y_lo)
    parts = []
    for t in (-40, -30, -20, -10, 0, 10, 20, 30, 40):
        parts.append(f'<line class="grid" x1="{LEFT}" x2="{w - RIGHT}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                     f'<text class="tick" x="{LEFT - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{t:+d} dB</text>'
                     if t else
                     f'<line class="axis" x1="{LEFT}" x2="{w - RIGHT}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                     f'<text class="tick" x="{LEFT - 6}" y="{y(t) + 4:.1f}" text-anchor="end">0 dB</text>')
    for t in (0.1, 0.3, 1, 3, 10):
        parts.append(f'<line class="grid" x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{TOP}" y2="{base}"/>'
                     f'<text class="tick" x="{x(t):.1f}" y="{base + 16}" text-anchor="middle">{t:g} s</text>')
    parts.append(f'<text class="tick" x="{w - RIGHT}" y="{h - 2}" text-anchor="end">median T30</text>')
    for p in points:
        tip = f"{p['label']} ({p['dataset']}): T30 {p['t30']:.2f} s, DRR {p['drr']:+.1f} dB, {p['n']:,} IRs"
        cx, cy = x(p["t30"]), y(p["drr"])                     # a 9 px hit area around a 3.5 px dot
        parts.append(f'<a href="{escape(p["href"])}" class="pt" data-i="{p["i"]}"><title>{escape(tip)}</title>'
                     f'<circle class="target" cx="{cx:.1f}" cy="{cy:.1f}" r="9"/>'
                     f'<circle class="dot" cx="{cx:.1f}" cy="{cy:.1f}" r="3.5"/></a>')
    return _svg("".join(parts), label, w, h)
