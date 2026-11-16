"""The home page: what RIRDB is, a search box, ways in, and what the tool prints (the rooms chart is on the rooms page)."""

from __future__ import annotations

from collections import Counter

from rirdb import client
from rirdb.select import by_dataset, human, plural, select, size_text
from rirdb.site import words
from rirdb.site.layout import ISSUES, MAINTAINER, MAINTAINER_URL, e, page

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


def home(search: dict) -> str:
    ds, sp = search["datasets"], search["spaces"]
    fmt_count = Counter(f for d in ds for f in d["formats"])
    kind_count = Counter(s["kind"] for s in sp)
    use_count = Counter(u for d in ds for u, v in d["uses"].items() if v in ("yes", "unaltered_only"))
    prov_count = Counter(p for d in ds for p in d["provides"])
    browse = [
        ("By format", [(f"datasets.html?format={f}", words.FORMATS[f], f"{fmt_count[f]} datasets") for f in words.SEARCH_FORMATS]),
        ("By kind of space", [(f"rooms.html?kind={k}", label, plural(kind_count[k], "room")) for k, label in words.KINDS.items()]),
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
    presets = [("rooms.html?kind=indoor", "Real rooms only"),
               ("datasets.html?format=binaural&use=commercial", "Binaural, commercial use allowed"),
               ("rooms.html?type=concert_hall&type=worship", "Concert halls and churches"),
               ("datasets.html?format=ambisonic&status=measured", "Ambisonic, measured"),
               ("datasets.html?provides=positions", "With source and receiver positions")]
    body = f"""
<section class="hero"><div class="wrap">
<h1>Room impulse responses from {len(ds)} public datasets, in one place</h1>
<p class="lead">Find IRs by format, licence, room and reverberation time. See what each dataset contains before you
download it. Get them as one checked folder, with their licences and citations.</p>
<form class="bigsearch" action="datasets.html" role="search"><input name="text" type="search"
 placeholder="Search: church, Eigenmike, binaural, York…" aria-label="Search the datasets"><button type="submit">Search</button></form>
<p class="presets"><span>Try</span>{"".join(f'<a href="{h}">{e(t)}</a>' for h, t in presets)}</p>
</div></section>

<section class="browse"><div class="wrap"><h2>Start from what you need</h2><div class="browse-grid">{browse_html}</div></div></section>

<section class="how"><div class="wrap how-grid">
<div class="steps"><h2>How it works</h2>
<div><span class="n">Find</span><p>Search the datasets by what they contain and by what their licence allows, or the
rooms by type and reverberation time.</p></div>
<div><span class="n">Check</span><p>Every measured dataset has its reverberation time per octave, direct-to-reverberant
ratio, clarity and a decay-quality grade, all measured the same way, and what its authors publish about the rooms.</p></div>
<div><span class="n">Get</span><p><code>pip install rirdb</code>. Every file is downloaded from its authors' site and
checked against its checksum; every IR is checked against the hash of its samples.</p>
<p><a href="guide.html">Get started</a></p></div></div>
{terminal()}
</div></section>

<section class="who"><div class="wrap"><p>RIRDB is maintained by <a href="{MAINTAINER_URL}">{MAINTAINER}</a>. Found a
wrong licence, a broken link, or a dataset we miss? <a href="{ISSUES}">Tell us on GitHub</a>.</p></div></section>"""
    return page("RIRDB: room impulse response datasets", body)
