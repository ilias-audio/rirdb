"""The frame every page shares (header, footer), and the small pieces pages are made of."""

from __future__ import annotations

import html

import pandas as pd

from rirdb import __version__, client
from rirdb.site import words

REPO = "https://github.com/ilias-audio/rirdb"
ISSUES = REPO + "/issues"
PYPI = "https://pypi.org/project/rirdb/"
MAINTAINER = "Ilias Ibnyahya"
MAINTAINER_URL = "https://github.com/ilias-audio"

# The logo: an echogram, the direct sound and four reflections, each weaker.
LOGO = ('<svg viewBox="0 0 30 22" aria-hidden="true"><rect x="0" y="0" width="3" height="22" rx="1"/>'
        '<rect x="6" y="9" width="3" height="13" rx="1"/><rect x="12" y="13" width="3" height="9" rx="1"/>'
        '<rect x="18" y="16" width="3" height="6" rx="1"/><rect x="24" y="18.5" width="3" height="3.5" rx="1"/></svg>')
NAV = [("datasets.html", "Datasets"), ("rooms.html", "Rooms"), ("guide.html", "Guide"), ("about.html", "About")]

ICONS = {   # 16 px marks for yes / partly / no / not stated, drawn in currentColor
    "yes": '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="currentColor"/>'
           '<path d="M4.5 8.2l2.2 2.2 4.8-4.8" fill="none" stroke="var(--paper)" stroke-width="1.8"/></svg>',
    "partial": '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" '
               'stroke="currentColor" stroke-width="1.6"/><path d="M8 1.8a6.2 6.2 0 0 1 0 12.4z" fill="currentColor"/></svg>',
    "no": '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" '
          'stroke-width="1.6"/></svg>',
    "unknown": '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" '
               'stroke="currentColor" stroke-width="1.6" stroke-dasharray="2 2"/></svg>',
}


def e(x) -> str:
    """Text for HTML; an empty or missing value is an empty string."""
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) or x is pd.NA else html.escape(str(x))


def page(title: str, body: str, depth: int = 0, current: str = "", scripts: tuple[str, ...] = ()) -> str:
    up = "../" * depth
    nav = "".join(f'<a href="{up}{href}"{" aria-current=page" if href == current else ""}>{label}</a>'
                  for href, label in NAV)
    js = "".join(f'<script src="{up}{s}" defer></script>' for s in (*scripts, "site.js"))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><link rel="stylesheet" href="{up}style.css"><link rel="icon" href="{up}icon.svg">{js}</head>
<body>
<header class="site-head"><div class="wrap">
<a class="logo" href="{up}index.html" aria-label="RIRDB home">{LOGO}RIRDB</a>
<nav aria-label="Site">{nav}</nav>
</div></header>
<main>{body}</main>
<footer class="site-foot"><div class="wrap">
<div><h4>RIRDB</h4><ul><li>Release {e(__version__)}, tables {e(client.version())}</li>
<li>Maintained by <a href="{MAINTAINER_URL}">{MAINTAINER}</a></li><li>Catalogue and tables: CC BY 4.0. Code: MIT</li></ul></div>
<div><h4>Use</h4><ul><li><a href="{up}guide.html">Get started</a></li><li><a href="{up}guide.html#glossary">Glossary</a></li>
<li><a href="{up}reference.html">Data reference</a></li></ul></div>
<div><h4>Cite and contribute</h4><ul><li><a href="{up}guide.html#cite">Cite RIRDB</a></li>
<li><a href="{up}guide.html#add">Add a dataset</a></li><li><a href="{ISSUES}">Report a problem</a></li></ul></div>
<div><h4>Elsewhere</h4><ul><li><a href="{REPO}">GitHub</a></li><li><a href="{PYPI}">PyPI</a></li></ul></div>
</div></footer>
</body></html>
"""


def copybox(text: str) -> str:
    return (f'<div class="copybox"><pre>{e(text)}</pre><button type="button" data-copy>Copy</button></div>')


def pill(text: str, cls: str = "") -> str:
    return f'<span class="pill{" " + cls if cls else ""}">{e(text)}</span>'


def use_marks(uses: dict, which=("commercial", "training", "redistribute")) -> str:
    """Allowed uses as marks: a filled tick (allowed), an open circle (not allowed), a dashed one (not stated)."""
    out = []
    for u in which:
        v = uses.get(u, "unknown")
        icon, cls = {"yes": ("yes", "yes"), "unaltered_only": ("yes", "yes"), "no": ("no", "no")}.get(v, ("unknown", "faint"))
        note = " (unaltered)" if v == "unaltered_only" else ""
        out.append(f'<span class="use {cls}" title="{e(words.USES[u])}: {e(words.USE_VALUES.get(v, v))}">{ICONS[icon]}'
                   f'<span>{e(words.USES[u])}{note}</span></span>')
    return f'<span class="uses">{"".join(out)}</span>'


def grade_bar(grades: dict) -> str:
    """One bar, a part per grade (S dark to D light, no grade hatched), and its legend in words for screen readers."""
    order = ["S", "A", "B", "C", "D", "none"]
    total = sum(grades.get(g, 0) for g in order)
    if not total:
        return ""
    said = ", ".join(f"{'no grade' if g == 'none' else g} {grades[g]:,}" for g in order if grades.get(g))
    parts = "".join(f'<i class="g{g}" style="flex:{grades[g] / total:.4f}"></i>' for g in order if grades.get(g))
    return f'<span class="gradebar" role="img" aria-label="Decay quality: {said}" title="Decay quality: {said}">{parts}</span>'


def grade_legend(grades: dict | None = None) -> str:
    keys = ["S", "A", "B", "C", "D"]
    return ('<span class="legend">' + "".join(
        f'<span><i class="g{g}"></i>{g}{f" {grades.get(g, 0):,}" if grades is not None else ""}</span>' for g in keys)
        + "</span>")


def table(head: list[str], rows: list[list[str]], num: set[int] = frozenset(), cls: str = "plain") -> str:
    th = "".join(f'<th{" class=num" if i in num else ""}>{h}</th>' for i, h in enumerate(head))
    tr = "".join("<tr>" + "".join(f'<td{" class=num" if i in num else ""}>{c}</td>' for i, c in enumerate(r)) + "</tr>"
                 for r in rows)
    return f'<div class="scroll"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#15171c"/>'
            '<g fill="#9db4ff"><rect x="5" y="5" width="3" height="22" rx="1"/><rect x="10.5" y="13" width="3" height="14" rx="1"/>'
            '<rect x="16" y="17" width="3" height="10" rx="1"/><rect x="21.5" y="20.5" width="3" height="6.5" rx="1"/></g></svg>')
