"""The guide and about sections of the home page (get started, choose what you get, glossary, cite, add a dataset;
who, why, how), and the data reference page."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from rirdb import __version__, client
from rirdb.site import words
from rirdb.site.layout import ISSUES, MAINTAINER, MAINTAINER_URL, REPO, copybox, e, page

# How to cite RIRDB: its paper (DMRN+21), and the software for the release used.
PAPER_BIB = """@inproceedings{ibnyahya2026rirdb,
  author    = {Ibnyahya, Ilias and Reiss, Joshua D.},
  title     = {{RIRDB}: A Database to Find and Explore Room Impulse Response Datasets},
  booktitle = {Digital Music Research Network One-day Workshop (DMRN+21)},
  address   = {London, UK},
  month     = dec,
  year      = {2026}
}"""
SOFTWARE_BIB = """@software{{rirdb,
  author  = {{Ibnyahya, Ilias}},
  title   = {{{{RIRDB}}: a database to find and explore room impulse response datasets}},
  version = {{{version}}},
  year    = {{2026}},
  url     = {{{url}}}
}}"""

GLOSSARY = [
    ("T30, T20", "t30", "The reverberation time: how long the sound takes to fall by 60 dB, extrapolated from the decay "
     "between −5 and −35 dB (T30) or −5 and −25 dB (T20) of the energy decay curve. RIRDB's main value, rt_mid, is "
     "T30 averaged over the 500 Hz and 1 kHz octaves, or T20 where T30 is not valid."),
    ("EDT", "edt", "Early decay time: the same, from the first 10 dB of the decay. It follows what a listener hears as "
     "reverberance."),
    ("DRR", "drr", "Direct-to-reverberant ratio, in dB: the energy of the direct sound against everything after it. It "
     "depends on the source–receiver distance as much as on the room."),
    ("C50, C80", "c50", "Clarity: the energy of the first 50 or 80 ms against the rest, in dB. C50 is used for speech, "
     "C80 for music."),
    ("D50, centre time", "d50", "Definition (the share of energy in the first 50 ms) and the centre of gravity of the "
     "energy in time."),
    ("STI", "sti", "Speech transmission index, 0 to 1: how intelligible speech would be, computed from the IR (IEC "
     "60268-16)."),
    ("Early IACC, lateral fraction", "iacc", "Spatial measures: how different the two ears' signals are early on "
     "(binaural IRs), and the share of early energy arriving from the sides (Ambisonic IRs)."),
    ("Octave bands", "bands", "The values per frequency band, from 31.5 Hz to 16 kHz. A value is published only where "
     "the decay in that band is long and clean enough to measure."),
    ("Decay quality (S to D)", "grades", "How much of the decay could be measured. " + " ".join(
        f"{v}." for v in words.GRADES.values()) + " A major defect (clipping, dropouts, no decay…) caps the grade at C. "
     "Anechoic rooms have no grade."),
    ("Format", "formats", "Mono, stereo, binaural (two ears, a dummy head or a person), Ambisonic (a sound field "
     "in spherical harmonics) or a microphone array. rirdb get writes Ambisonics as AmbiX."),
    ("Layout", "layouts", "The device behind a format: " + "; ".join(f"{v}" for v in list(words.LAYOUTS.values())[:6])
     + ", and others (see the data reference)."),
    ("Origin", "origin", "Measured in a real space, simulated by software, or the response of an effect or device "
     "(a plate, a plugin)."),
    ("Room", "room", "Any space a dataset measured: rooms and halls, but also outdoor sites, vehicles and devices. The "
     "tables call it a space."),
    ("Condition", "condition", "A state of a room that a dataset varies on purpose: panels, furniture, curtains, "
     "doors."),
    ("Known defect", "defect", "A defect of the files as published that no analysis can repair: a damaged file, a "
     "recording where an IR should be, renders that look stretched in time. The IRs stay in the table with the defect "
     "in words (column known_defect); rirdb search, rirdb get and the website say so."),
    ("One layout per room", "primary", "A room published in two formats (say Ambisonic and binaural) is counted once, "
     "in its main layout, unless you ask for a format. rirdb search and the site count the same way."),
]


def guide_sections() -> str:
    """The guide, a part of the home page (#guide): get started, choose exactly what you get, Python, glossary,
    cite, add a dataset. Each part keeps the address it had on guide.html, which now leads here."""
    gloss = "".join(f'<dt id="g-{a}">{e(term)}</dt><dd>{e(text)}</dd>' for term, a, text in GLOSSARY)
    python = ('from rirdb.select import select' + "\n\n"
              + 'sel = select(format="binaural", grade="A", year="2018-", per_space=20, max_archive="2GB",\n'
              + '             balance="space", n=200)\n'
              + 'sel.table            # one row per IR, every column of the data reference\n'
              + 'sel.sizes()          # the download, and the IRs as WAV\n'
              + 'sel.known_defects()  # what to know before using them\n'
              + 'sel.download("irs/")')
    return f"""
<section class="band" id="guide"><div class="wrap prose">
<div class="section-head"><h2>Guide</h2><p class="lead">Install RIRDB, find IRs, get them as one folder, and cite
what you use.</p></div>

<section id="start"><h3>Get started</h3>
<p>RIRDB needs Python 3.11 or later. It installs no machine-learning libraries.</p>
{copybox("pip install rirdb")}
<p>See what a selection holds and costs before anything is downloaded:</p>
{copybox("rirdb search --format binaural --use commercial --t30 1.25-2.5")}
<p>Read about one dataset, then get it, every file as published and checked:</p>
{copybox("rirdb info bras" + chr(10) + "rirdb get --dataset bras")}
<p>Or get a balanced selection across datasets, written as one folder:</p>
{copybox("rirdb get --use training --format mono --grade A --balance space --n 500 --out my-irs")}
<p>The folder holds the WAV files, <code>irs.csv</code> with every measured value, <code>LICENSES.md</code>,
<code>references.bib</code> and <code>selection.lock</code>, which gets the same IRs again anywhere with
<code>rirdb get --lock</code>. The filters are those of the <a href="datasets.html">datasets page</a>, which writes the
command for any search: <code>--format</code>, <code>--use</code>, <code>--kind</code>, <code>--grade</code>,
<code>--space-type</code>, <code>--t30</code>, <code>--provides</code>, <code>--year</code> and <code>--dataset</code>.
<code>rirdb search --help</code> lists them all.</p></section>

<section id="choose"><h3>Choose exactly what you get</h3>
<p>The <a href="datasets.html">datasets page</a> builds the command as you click: filters on the left, <b>Pick</b> or
<b>Leave out</b> on any dataset, then <a href="datasets.html?tab=get">What you get</a>: how many IRs, rooms and
datasets, the download, and charts of what the command selects (its reverberation times, the IRs of each dataset and
of each room), each over what the filters alone would give. The command, the same in Python, and what it would
download follow. <code>rirdb search</code> takes every option of <code>rirdb get</code>, so you can look before
anything is downloaded.</p>
<p><b>At most so many per room</b> (<code>--per-space</code>). A room measured in many ways can hold thousands of IRs:
ARNI, a variable-acoustics room, has 26,221 in 5,246 panel settings. <code>--per-space 50</code> keeps 50 of each room,
spread over its reverberation times from the shortest to the longest, so one room gives its whole range and does not
outweigh the others.</p>
{copybox("rirdb search --dataset arni --per-space 50")}
<p><b>No big downloads</b> (<code>--max-archive</code>). Datasets come as their authors publish them, and a download is
taken whole: one SRIRACHA condition is a single 8.9 GB file. <code>--max-archive 2GB</code> leaves out every IR that
comes in a download larger than 2 GB (or of unknown size), so a selection across many datasets stays small.</p>
{copybox("rirdb search --format mono --per-space 20 --max-archive 2GB")}
<p><b>A draw</b> (<code>--n</code>, <code>--balance</code>, <code>--seed</code>). <code>--n 500</code> draws 500 of the
IRs left; <code>--balance space</code>, <code>dataset</code> or <code>t30</code> makes each room, each dataset or each
range of reverberation time equally likely, so a dataset with many IRs does not fill the draw. The seed and every
IR drawn are written in <code>selection.lock</code>.</p>
{copybox("rirdb get --use training --format mono --per-space 20 --max-archive 2GB --n 1000 --balance t30 --out my-irs")}
<p><b>Recent datasets</b> (<code>--year</code>): <code>--year 2024-</code> keeps the datasets published in 2024 or
later, <code>--year 2015-2020</code> those of these years.</p>
</section>

<section id="python"><h3>In Python</h3>
<p>Every option of the command line is an argument of <code>select</code>, with <code>_</code> for <code>-</code>.</p>
{copybox(python)}
</section>

<section id="glossary"><h3>Glossary</h3><dl class="glossary">{gloss}</dl>
<p>Every column of the tables, with its unit and what an empty cell means: the <a href="reference.html">data
reference</a>.</p></section>

<section id="cite"><h3>Cite</h3>
<p>Cite the datasets you use: <code>rirdb get</code> writes their references into <code>references.bib</code>, and each
dataset page links its paper. To cite RIRDB, cite its paper:</p>
{copybox(PAPER_BIB)}
<p>and, to name the exact release you used, the software:</p>
{copybox(SOFTWARE_BIB.format(version=__version__, url=REPO))}
</section>

<section id="add"><h3>Add a dataset</h3>
<p>Know a public dataset of impulse responses that is missing, or a wrong licence or link? Open an issue on
<a href="{ISSUES}">GitHub</a> with the dataset's page, or send a pull request with its folder in
<code>registry/</code> (one <code>dataset.yaml</code>, described in the repository).</p></section>
</div></section>"""


def about_sections(datasets_count: int, measured: int, n_irs: int) -> str:
    """Who makes RIRDB, why, how it measures and chooses datasets: the last part of the home page (#about)."""
    return f"""
<section class="band" id="about"><div class="wrap prose">
<div class="section-head"><h2>About</h2><p class="lead">RIRDB catalogues the public datasets of room impulse
responses, measures every IR the same way, and gets them for you as one checked folder.</p></div>
<section id="who"><h3>Who</h3><p>RIRDB is made and maintained by <a href="{MAINTAINER_URL}">{MAINTAINER}</a>. Questions,
problems and new datasets go to <a href="{ISSUES}">GitHub issues</a>; pull requests are welcome.</p></section>
<section id="why"><h3>Why</h3><p>More than 200 room impulse response datasets are public, each with its own host,
licence, file format and quality. Finding the right IRs, checking what you may do with them, and reading them all in
the same way takes days. RIRDB does that work once, in the open.</p></section>
<section id="how-measured"><h3>How every IR is measured</h3>
<p>One analyser, the same for every dataset. The decay is found with Lundeby's method for the noise floor and a
noise-compensated Schroeder integral, then T20, T30 and EDT are fitted in each octave band (ISO 3382). A value is
published only where its decay is valid; otherwise the cell is empty and the data reference says why. Clarity, DRR,
STI and the spatial measures follow, and every IR is checked for defects: clipping, dropouts, a missing decay, sweep
residue, tones, a band-limited signal, a quantisation or noise floor. The decay quality grade (S to D) sums this up;
the <a href="#g-grades">glossary</a> explains it. A defect of the files as published that no analysis can repair (a
damaged file, a recording where an IR should be) is kept and said: see <a href="#g-defect">known defect</a>.</p></section>
<section id="how-chosen"><h3>How datasets are chosen</h3>
<p>Any public dataset of impulse responses of real or simulated rooms, or of effects and devices, is catalogued:
{datasets_count} so far. Each has its licence recorded, with every statement the authors make about it; where they
disagree, the strictest one is used. Datasets are downloaded and measured in order, small and well documented first:
{measured} are measured today ({n_irs:,} IRs). Very dense grids are measured on a fixed sample and stay downloadable in
full. The audio always stays with its authors: RIRDB downloads it from their site and keeps their licence.</p></section>
<section id="releases"><h3>Releases</h3><p>This is release {e(__version__)}, with tables {e(client.version())}. Every
release freezes the tables with a checksum for each file, so a selection made today can be rebuilt exactly. The
catalogue and the tables are CC BY 4.0, the code MIT. Until 1.0, ids, rows and columns may change.</p></section>
</div></section>"""


# ------------------------------------------------------------------------------------------- the data reference

TABLES = {
    "irs": "One row per impulse response. What irs.csv from rirdb get holds.",
    "spaces": "One row per room or other space.",
    "datasets": "One row per dataset, measured or not.",
    "bands": "One row per IR and frequency band: the values per octave.",
    "downloads": "One row per file to download, with its size and checksum.",
    "files": "One row per file of a dataset, after unpacking.",
    "readers": "One row per IR: where it sits in its file. rirdb get uses it to cut each IR.",
    "all_irs": "Every IR of the three dense grids: listed and downloadable, though only a sample is measured.",
}
IRS_GROUPS = [
    ("Identity and files", r"^(ir_id|dataset|space|condition|source|receiver|is_primary|duplicate_of|file|item|download|audio_hash)$"),
    ("Signal", r"^(fs|channels|duration_s|sample_format|measured_on|format|layout|usable_top_hz|effective_bits|peak_to_noise_db)$"),
    ("Room and positions", r"^(origin|space_type|volume_m3|distance_m|src_.|rcv_.)$"),
    ("Licence", r"^(licence|licence_conditions|use_.*|redistribute)$"),
    ("Processing", r"^(is_processed|proc_.*)$"),
    ("Quality", r"^(grade|defects_.*|defect_.*)$"),
    ("Room acoustics", r"."),
]
PROVENANCE = {"registry": ("from", "From the dataset"), "index": ("from", "From the dataset"),
              "measure": ("meas", "Measured by RIRDB"), "build": ("der", "Worked out by RIRDB"),
              "pin": ("der", "Recorded by RIRDB")}


def column_block(c: dict, name_html: str = "", meaning: str = "") -> str:
    cls, said = PROVENANCE.get(c["origin"], ("der", c["origin"]))
    values = c["allowed"].split("|") if c["allowed"] else []
    chips = "".join(f"<span>{e(v)}</span>" for v in values[:12]) + (f"<span>+{len(values) - 12} more</span>"
                                                                   if len(values) > 12 else "")
    kind = " · ".join(x for x in (c["type"], c["unit"].replace("m3", "m³")) if x)
    empty = c["empty_means"] if c["empty_means"] and not c["empty_means"].startswith("never empty") else ""
    return (f'<div class="col" id="{e(c["table"])}-{e(c["name"])}" data-text="{e((c["name"] + " " + c["meaning"]).lower())}">'
            f'<div class="name"><code>{name_html or e(c["name"])}</code><span class="type">{e(kind)}</span>'
            f'<span class="prov {cls}">{e(said)}</span></div><p>{meaning or e(c["meaning"])}</p>'
            + (f'<div class="vals">{chips}</div>' if chips else "")
            + (f'<p class="emp">Empty when {e(empty)}.</p>' if empty else "") + "</div>")


def reference_page() -> str:
    with open(Path(__file__).parents[1] / "columns.csv", newline="") as f:
        cols = list(csv.DictReader(f))
    tables = [t for t in dict.fromkeys(c["table"] for c in cols) if t in TABLES]
    toc, body = [], []
    for t in tables:
        mine = [c for c in cols if c["table"] == t]
        toc.append(f'<a class="t" href="#t-{t}">{t}</a>')
        body.append(f'<h2 id="t-{t}">{t}</h2><p class="quiet">{e(TABLES[t])}</p>')
        if t != "irs":
            body.append("".join(column_block(c) for c in mine))
            continue
        used = set()
        for g, (title, rx) in enumerate(IRS_GROUPS):
            group = [c for c in mine if c["name"] not in used and re.match(rx, c["name"])]
            used |= {c["name"] for c in group}
            toc.append(f'<a href="#irs-g{g}">{title}</a>')
            body.append(f'<h3 id="irs-g{g}">{title}</h3>')
            bands = [c for c in group if re.match(r"^t30_\d+$", c["name"])]
            defects = [c for c in group if c["name"].startswith("defect_")]
            body.append("".join(column_block(c) for c in group if c not in bands and c not in defects))
            if defects:
                body.append(column_block({**defects[0], "allowed": ""}, "defect_clipping … defect_inverted_channel",
                                         "One true/false column per defect, true where it was found: "
                                         + ", ".join(f"<code>{e(c['name'])}</code>" for c in defects) + "."))
            if bands:
                body.append(column_block(bands[0], "t30_31 … t30_16000",
                                         "T30 in each octave band, 31.5 Hz to 16 kHz (also in the bands table)."))
    page_body = f"""
<div class="wrap page">
<div class="page-head"><h1>Data reference</h1>
<p class="lead">What the release tables hold, column by column: what you find in <code>irs.csv</code> from
<code>rirdb get</code>, or in the tables from Python. A value is published only where it is valid; otherwise the cell is
empty, and each column says when.</p>
<div class="searchbar narrow"><input id="colq" type="search" placeholder="Find a column: t30, licence, distance…"
 aria-label="Find a column"></div></div>
<div class="ref"><nav class="toc" aria-label="Tables">{"".join(toc)}</nav><div class="cols">{"".join(body)}</div></div>
</div>"""
    return page("Data reference · RIRDB", page_body)
