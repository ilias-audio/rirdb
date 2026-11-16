"""The charts of the website, as SVG written in Python: no chart library, and they show without JavaScript.

Every mark carries a <title>, shown on hover and keyboard focus (site.js turns it into a tooltip). Colours come
from style.css (classes bar, outline, grid, axis, k-room ...), so they follow the light or dark theme. Every chart
of a dataset page uses the same axes on every page, so two datasets compare at a glance.
"""

from __future__ import annotations

import math
from html import escape

W, H = 420, 190                   # the drawing's own units; it scales to its box (about 1:1 on a desktop)
LEFT, RIGHT, TOP, BOTTOM = 40, 10, 16, 34


def _svg(body: str, label: str, w: int = W, h: int = H, cls: str = "chart") -> str:
    return f'<svg class="{cls}" viewBox="0 0 {w} {h}" role="img" aria-label="{escape(label)}">{body}</svg>'


def _log_x(lo: float, hi: float, x0: float, width: float):
    return lambda v: x0 + width * math.log(min(max(v, lo), hi) / lo) / math.log(hi / lo)


def histogram(counts: list[int], corpus: list[int], edges: list[float], *, unit: str, axis: str, label: str,
              ticks: list[float], log: bool = False, fmt=lambda v: f"{v:g}", median: float | None = None) -> str:
    """The share of a dataset's IRs in each bin (columns), over the share of the whole collection (grey outline).
    Bins are equal on the axis (equal in log time when `log`); the end bins hold everything beyond them."""
    n_bins, plot_w, base = len(counts), W - LEFT - RIGHT, H - BOTTOM
    total, ctotal = sum(counts) or 1, sum(corpus) or 1
    share, cshare = [n / total for n in counts], [n / ctotal for n in corpus]
    top = max(max(share), max(cshare)) * 1.1 or 1
    step = plot_w / n_bins
    y = lambda v: base - (base - TOP) * v / top
    x = _log_x(edges[0], edges[-1], LEFT, plot_w) if log else \
        (lambda v: LEFT + plot_w * (v - edges[0]) / (edges[-1] - edges[0]))
    grid = max(5, int(top * 100 // 2 // 5 * 5))                   # a round percentage near half the top
    parts = []
    for p in (grid, 2 * grid):
        if p / 100 < top:
            parts.append(f'<line class="grid" x1="{LEFT}" x2="{W - RIGHT}" y1="{y(p / 100):.1f}" y2="{y(p / 100):.1f}"/>'
                         f'<text x="{LEFT - 6}" y="{y(p / 100) + 4:.1f}" text-anchor="end">{p} %</text>')
    for t in ticks:
        parts.append(f'<text x="{x(t):.1f}" y="{base + 15}" text-anchor="middle">{escape(fmt(t))}</text>')
    parts.append(f'<text x="{W - RIGHT}" y="{H - 3}" text-anchor="end">{escape(axis)}</text>')
    bw = min(24.0, step - 2)
    for i, (n, v) in enumerate(zip(counts, share)):
        lo, hi = edges[i], edges[i + 1]
        span = f"below {fmt(hi)}" if i == 0 else f"{fmt(lo)} or more" if i == n_bins - 1 else f"{fmt(lo)} to {fmt(hi)}"
        tip = f"{n:,} IRs ({v:.0%}) {span} {unit}; whole collection {cshare[i]:.0%}"
        bar = (f'<rect class="bar" x="{LEFT + i * step + (step - bw) / 2:.1f}" y="{y(v):.1f}" width="{bw:.1f}" '
               f'height="{base - y(v):.1f}" rx="2"/>') if n else ""
        parts.append(f'<g class="hit" tabindex="0"><title>{escape(tip)}</title><rect class="target" '
                     f'x="{LEFT + i * step:.1f}" y="{TOP}" width="{step:.1f}" height="{base - TOP}"/>{bar}</g>')
    path = f"M{LEFT},{base}" + "".join(f"V{y(v):.1f}H{LEFT + (i + 1) * step:.1f}" for i, v in enumerate(cshare))
    parts.append(f'<path class="outline" d="{path}V{base}"/>')
    if median is not None:
        mx = x(median)
        anchor, dx = ("end", -5) if mx > LEFT + plot_w * 0.72 else ("start", 5)
        parts.append(f'<line class="median" x1="{mx:.1f}" x2="{mx:.1f}" y1="{TOP - 6}" y2="{base}"/>'
                     f'<text class="label" x="{mx + dx:.1f}" y="{TOP + 2}" text-anchor="{anchor}">median '
                     f'{escape(fmt(round(median, 2)))} {escape(unit)}</text>')
    parts.append(f'<line class="axis" x1="{LEFT}" x2="{W - RIGHT}" y1="{base}" y2="{base}"/>')
    return _svg("".join(parts), label)


def band_boxes(stats: dict[int, dict | None], names: list[str], label: str, lo: float = 0.05, hi: float = 25) -> str:
    """One box per octave on a log time axis: the box spans the middle half of the IRs, the line across it is the
    median, the whiskers reach the 5th and 95th percentiles."""
    bands = list(stats)
    plot_w, base = W - LEFT - RIGHT, H - BOTTOM
    step = plot_w / len(bands)
    y = lambda v: base - (base - TOP) * math.log(min(max(v, lo), hi) / lo) / math.log(hi / lo)
    parts = []
    for t in (0.1, 0.3, 1, 3, 10):
        parts.append(f'<line class="grid" x1="{LEFT}" x2="{W - RIGHT}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                     f'<text x="{LEFT - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{t:g} s</text>')
    bw = min(22.0, step - 10)
    for i, (b, name) in enumerate(zip(bands, names)):
        cx = LEFT + (i + 0.5) * step
        parts.append(f'<text x="{cx:.1f}" y="{base + 15}" text-anchor="middle">{escape(name)}</text>')
        s = stats[b]
        if not s:
            parts.append(f'<g class="hit" tabindex="0"><title>{escape(name)}: no valid T30</title><rect class="target" '
                         f'x="{cx - step / 2:.1f}" y="{TOP}" width="{step:.1f}" height="{base - TOP}"/></g>')
            continue
        tip = (f"{name}: median {s['p50']:.2f} s; half of the IRs between {s['p25']:.2f} and {s['p75']:.2f} s; "
               f"90 % between {s['p5']:.2f} and {s['p95']:.2f} s ({s['n']:,} IRs)")
        parts.append(
            f'<g class="hit" tabindex="0"><title>{escape(tip)}</title>'
            f'<rect class="target" x="{cx - step / 2:.1f}" y="{TOP}" width="{step:.1f}" height="{base - TOP}"/>'
            f'<line class="whisker" x1="{cx:.1f}" x2="{cx:.1f}" y1="{y(s["p95"]):.1f}" y2="{y(s["p5"]):.1f}"/>'
            f'<rect class="bar" x="{cx - bw / 2:.1f}" y="{y(s["p75"]):.1f}" width="{bw:.1f}" '
            f'height="{max(y(s["p25"]) - y(s["p75"]), 2):.1f}" rx="2"/>'
            f'<line class="mid" x1="{cx - bw / 2:.1f}" x2="{cx + bw / 2:.1f}" y1="{y(s["p50"]):.1f}" '
            f'y2="{y(s["p50"]):.1f}"/></g>')
    parts.append(f'<text x="{W - RIGHT}" y="{H - 3}" text-anchor="end">octave band</text>'
                 f'<line class="axis" x1="{LEFT}" x2="{W - RIGHT}" y1="{base}" y2="{base}"/>')
    return _svg("".join(parts), label)


def band_curve(values: list[float | None], names: list[str], lo: float = 0.1, hi: float = 10) -> str:
    """A small line of a room's median T30 per octave, on a fixed log axis (0.1 to 10 s, the faint line at 1 s), so
    the curves of two rooms compare. A missing octave breaks the line."""
    w, h, pad = 110, 30, 3
    x = lambda i: pad + (w - 2 * pad) * i / (len(values) - 1)
    y = lambda v: h - pad - (h - 2 * pad) * math.log(min(max(v, lo), hi) / lo) / math.log(hi / lo)
    runs, run = [], []
    for i, v in enumerate(values):
        if v is None:
            runs, run = (runs + [run] if run else runs), []
        else:
            run.append((x(i), y(v)))
    runs += [run] if run else []
    if not runs:
        return ""
    marks = "".join(f'<polyline class="line" points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in r)}"/>' if len(r) > 1
                    else f'<circle class="dot" cx="{r[0][0]:.1f}" cy="{r[0][1]:.1f}" r="2"/>' for r in runs)
    tip = ", ".join(f"{n} {v:.2f} s" for n, v in zip(names, values) if v is not None)
    return (f'<svg class="curve" viewBox="0 0 {w} {h}" role="img" aria-label="Median T30 per octave: {escape(tip)}">'
            f'<title>Median T30: {escape(tip)}</title><line class="grid" x1="{pad}" x2="{w - pad}" y1="{y(1):.1f}" '
            f'y2="{y(1):.1f}"/>{marks}</svg>')


def mini_hist(counts: list[int]) -> str:
    """The small T30 histogram of a result card (the same bins as the dataset page)."""
    if not counts or not sum(counts):
        return ""
    top, step = max(counts), 170 / len(counts)
    bars = "".join(f'<rect x="{i * step + 0.5:.1f}" y="{30 - 28 * n / top:.1f}" width="{step - 1:.1f}" '
                   f'height="{28 * n / top:.1f}"/>' for i, n in enumerate(counts) if n)
    return f'<svg class="mini" viewBox="0 0 170 30" aria-hidden="true">{bars}</svg>'
