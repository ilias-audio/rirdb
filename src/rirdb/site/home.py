"""The home page, the one page to read: what RIRDB is, ways in, how it works, the guide and the about sections."""

from __future__ import annotations

from collections import Counter

from rirdb import client
from rirdb.select import by_dataset, human, plural, select, size_text
from rirdb.site import words
from rirdb.site.guide import about_sections, guide_sections
from rirdb.site.layout import e, page

# The search shown in the terminal: the first of these that finds IRs in the release.
EXAMPLES = [{"format": "binaural", "grade": "A"}, {"grade": "A"}, {}]


def terminal() -> str:
    """What `rirdb search --format binaural --grade A` prints, run on the release when the site is built."""
    for example in EXAMPLES:
        sel = select(**example)
        if not sel.table.empty:
            break
    t = sel.table
    options = "".join(f" --{k} {v}" for k, v in example.items())
    s = sel.sizes()
    plan = sel.plan()
    rows = by_dataset(t, plan).head(5)
    width = max(len(d) for d in rows.index)
    lines = [f'<span class="prompt">$</span> rirdb search{options}',
             f'{plural(len(t), "IR")} in {plural(t["space"].nunique(), "space")} from '
             f'{plural(t["dataset"].nunique(), "dataset")} (release {client.version()})', ""]
    lines += [f'{e(d.ljust(width))}  {r["irs"]:>5,} IRs  {r["spaces"]:>2} spaces  {e(r["licence"])}' for d, r in rows.iterrows()]
    if len(t["dataset"].unique()) > len(rows):
        lines.append(f'<span class="faint">… and {t["dataset"].nunique() - len(rows)} more datasets</span>')
    lines += ["", f'Download: {e(size_text(s.download_bytes, s.unknown_size))}. The IRs as WAV: {human(s.output_bytes)}.', "",
              '<span class="prompt">$</span> rirdb get --format binaural --grade A --balance space --n 200',
              '<span class="faint">200 IRs, every room equally likely, in one folder with irs.csv, LICENSES.md,</span>',
              '<span class="faint">references.bib and selection.lock</span>']
    return f'<pre class="terminal" aria-label="An example in the terminal">{chr(10).join(lines)}</pre>'


def home(search: dict, n_irs: int) -> str:
    """The one page to read: what RIRDB is and ways in, how it works, then the guide and the about sections
    (guide.py), under a bar of the sections that stays in view."""
    ds, sp = search["datasets"], search["spaces"]
    measured = sum(d["status"] == "measured" for d in ds)
    rooms = sum(s["rooms"] for s in sp)
    kind_count = Counter(k for s in sp for k in [s["kind"]] * s["rooms"])
    prov_count = Counter(p for d in ds for p in d["provides"])
    # the datasets each link lists, counted as the datasets page counts them (site.js): a measured dataset by its
    # IRs (mono: also the W channel of an Ambisonic room without a mono layout), the others by their pages
    c = {k: i for i, k in enumerate(search["columns"])}
    rows = {i: [] for i in range(len(ds))}
    for r in search["counts"]:
        rows[sp[r[c["space"]]]["d"]].append(r)

    def listed(irs_ok, page_ok) -> int:
        return sum(any(irs_ok(r) for r in rows[i]) if d["status"] == "measured" else page_ok(d) for i, d in enumerate(ds))
    fmt_count = {f: listed(lambda r, f=f: r[c["format"]] == f or (f == "mono" and r[c["mono_w"]]),
                           lambda d, f=f: f in d["formats"]) for f in words.SEARCH_FORMATS}
    use_count = {u: listed(lambda r, u=u: r[c["primary"]] and r[c[u]], lambda d, u=u: d["uses"][u] == "yes")
                 for u in ("commercial", "training", "redistribute")}
    browse = [
        ("By format", [(f"datasets.html?format={f}", words.FORMATS[f], f"{fmt_count[f]} datasets") for f in words.SEARCH_FORMATS]),
        ("By kind of space", [(f"datasets.html?kind={k}&amp;tab=get", label, plural(kind_count[k], "room"))
                              for k, label in words.KINDS.items()]),
        ("By what you may do", [
            ("datasets.html?use=commercial", "Commercial use allowed", f'{use_count["commercial"]} datasets'),
            ("datasets.html?use=training", "Machine learning allowed", f'{use_count["training"]} datasets'),
            ("datasets.html?use=redistribute", "Redistribution allowed", f'{use_count["redistribute"]} datasets'),
            ("datasets.html?provides=positions", "With source and receiver positions", f'{prov_count["positions"]} datasets'),
            ("datasets.html?provides=geometry", "With a 3D model or plans", f'{prov_count["geometry"]} datasets')]),
    ]
    browse_html = "".join(
        f'<div><h3>{e(title)}</h3><ul>' + "".join(f'<li><a href="{href}">{e(label)}</a><span>{e(count)}</span></li>'
                                                  for href, label, count in items) + "</ul></div>" for title, items in browse)
    presets = [("datasets.html?kind=indoor&amp;tab=get", "Real rooms only"),
               ("datasets.html?format=binaural&amp;use=commercial", "Binaural, commercial use allowed"),
               ("datasets.html?type=concert_hall&amp;type=worship&amp;tab=get", "Concert halls and churches"),
               ("datasets.html?dataset=arni&amp;per=50&amp;tab=get", "50 ARNI IRs, dry to live"),
               ("datasets.html?since=2024", "Published since 2024")]
    numbers = [(len(ds), "datasets catalogued"), (measured, "measured by RIRDB"), (n_irs, "impulse responses"),
               (rooms, "rooms and other spaces")]
    sections = [("top", "RIRDB"), ("how", "How it works"), ("start", "Get started"), ("choose", "Choose what you get"),
                ("glossary", "Glossary"), ("cite", "Cite"), ("about", "About")]
    body = f"""
<section class="hero" id="top"><div class="wrap">
<h1>Room impulse responses from {len(ds)} public datasets, in one place</h1>
<p class="lead">Find IRs by format, licence, room and reverberation time. See what each dataset contains, and what a
selection gives you, before you download it. Get them as one checked folder, with their licences and citations.</p>
<form class="bigsearch" action="datasets.html" role="search"><input name="text" type="search"
 placeholder="Search: church, Eigenmike, binaural, York…" aria-label="Search the datasets"><button type="submit">Search</button></form>
<p class="cta"><a class="button" href="datasets.html?tab=get">Choose IRs and get the command</a>
<a href="#start">or start with the guide</a></p>
<p class="presets"><span>Try</span>{"".join(f'<a href="{h}">{e(t)}</a>' for h, t in presets)}</p>
<dl class="numbers">{"".join(f"<div><dt>{e(t)}</dt><dd>{n:,}</dd></div>" for n, t in numbers)}</dl>
</div></section>

<nav class="sectionbar" aria-label="On this page"><div class="wrap">{"".join(f'<a href="#{a}">{t}</a>' for a, t in sections)}</div></nav>

<section class="browse"><div class="wrap"><h2>Start from what you need</h2><div class="browse-grid">{browse_html}</div></div></section>

<section class="how" id="how"><div class="wrap how-grid">
<div class="steps"><h2>How it works</h2>
<div><span class="n">Find</span><p>Filter the datasets by what they contain, when they were published and what their
licence allows; pick the ones you want or leave some out.</p></div>
<div><span class="n">Check</span><p>Every measured dataset has its reverberation time per octave, direct-to-reverberant
ratio, clarity and a decay-quality grade, all measured the same way, and what its authors publish about the rooms.
<a href="datasets.html?tab=get">What you get</a> shows the reverberation times, rooms and datasets of a selection
before you download it.</p></div>
<div><span class="n">Get</span><p><code>pip install rirdb</code>, then the command the page writes. Every file is
downloaded from its authors' site and checked against its checksum; every IR is checked against the hash of its
samples.</p></div></div>
{terminal()}
</div></section>
{guide_sections()}
{about_sections(len(ds), measured, n_irs)}"""
    return page("RIRDB: room impulse response datasets", body)
