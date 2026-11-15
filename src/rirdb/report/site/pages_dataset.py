"""Dataset pages: d/<id>.html for every registry dataset.

One page per row of site.json "datasets", with the detail from data/ds/<id>.json: where and how to get the
files (lock, manifest, fetch script, Apocrita copy), the licence in plain words, what the authors publish
about the rooms, what rirdb indexed, the full analysis (grades, parameters, distributions against the
corpus, per-band boxes, validity, quality flags, duplicates), the spaces, the CLAP map, validation against
published values, known issues, query examples, the probe output and how to cite.

Every number comes from the data files; the text around it is fixed wording. Pages of datasets that were
not analysed say why, and keep everything needed to fetch them.
"""

from __future__ import annotations

import re
from collections import Counter
from statistics import median

from markupsafe import Markup, escape

from rirdb import paths
from rirdb.report.site import charts
from rirdb.report.site.core import (FORMAT_LABELS, KIND_LABELS, SITE_URL, SOURCE_LABELS, doi_url, fmt_bytes, fmt_fs,
                                    fmt_int, fmt_num, fmt_pct, record_url, repo_blob, repo_raw)
from rirdb.report.site.data import BAND_KEYS, SIGNAL_PARAMS

try:  # the Spaces page's category words, so a category reads the same on both pages
    from rirdb.report.site.pages_catalog import CATEGORY_LABEL
except ImportError:  # pragma: no cover
    CATEGORY_LABEL = {}

# ----------------------------------------------------------------------------- vocabulary

# findings rows (docs/decisions/dataset-findings.md) that are about the host, not the data (also pages_get)
HOST_ISSUE_RE = re.compile(r"offline|firewall|HTTP 403|truncated upstream|refuses scripted|Request Rejected", re.I)

TYPE_LABEL = {"measured": "Measured", "simulated": "Simulated", "production": "Production library",
              "hybrid": "Measured and simulated"}
STATUS_TEXT = {
    "active": "In rirdb's store and checked against the lock file.",
    "planned": "In a download wave, not fetched yet.",
    "manual": "Needs a manual download (see the instructions); not adopted by rirdb yet.",
    "broken": "The published files are damaged or incomplete, so they cannot be extracted.",
    "deferred": "Not fetched yet: deferred to a later wave.",
}
ACCESS_TEXT = {
    "open": "No account or registration needed.",
    "registration": "Free registration needed before you can download.",
    "manual": "Download by hand, following the instructions: rirdb has no scripted download for it.",
    "broken": "The download does not work.",
}
WAVE_TEXT = {0: "smoke test", 1: "many spaces, sparse positions", 2: "several conditions per space",
             3: "dense grids", 4: "simulated and production sets, after the measured corpus"}
TIER_TEXT = {
    "store": "Group store: permanent, read-only for the group.",
    "inplace": "Group store, used in place (added by another group member).",
    "scratch": "Scratch: purged 65 days after the last change, re-fetched when needed.",
}

# plain-language licence reading, as in the Field Guide (src/rirdb/report/guide_template.html)
LICENCE_CLASSES = {
    "open": ("Open", "Use for anything, including products and model training. Credit the authors."),
    "share_alike": ("Share-alike", "Like open, but if you share the IRs, or versions you have changed, they must carry the "
                    "same licence. Papers and measured results are not affected; whether a track or a model made with "
                    "the IRs counts as an adaptation is not settled."),
    "non_commercial": ("Non-commercial", "Any use that is not mainly for money: research, teaching, personal and non-profit "
                       "projects. No products, paid services or commercially used models; work done for a company may "
                       "count as commercial."),
    "conflict": ("Conflicting statements", "The record, the files or the paper give different licences. Follow the "
                 "stricter one until the authors confirm."),
    "custom": ("Custom terms", "The publisher's own conditions. Read them: some allow music-making but forbid "
               "redistribution or AI training."),
    "mixed": ("Per space", "A different licence for each space, chosen by each contributor."),
    "unspecified": ("Not stated", "No licence anywhere. By default all rights are reserved: there are no terms to read, "
                    "so ask the authors first."),
}
SPDX_LABEL = {
    "LicenseRef-unspecified": "No licence stated", "LicenseRef-per-space": "Per space (mostly CC BY-SA)",
    "LicenseRef-EchoThief": "EchoThief licence", "LicenseRef-Voxengo-IR": "Voxengo terms",
    "LicenseRef-TAU-NonCommercial": "Non-commercial (custom)", "LicenseRef-Treble-NonCommercial": "Non-commercial (custom)",
    "LicenseRef-S3A-academic": "Academic use (custom)", "LicenseRef-GWA": "GWA terms",
    "LicenseRef-CC-PublicDomain": "Public domain", "GPL-3.0-only": "GPL 3.0", "ODC-By-1.0": "ODC-By 1.0",
    "Apache-2.0": "Apache 2.0", "MIT": "MIT", "CC0-1.0": "CC0",
}
PERM_TEXT = {
    "commercial": {"yes": ("yes", "Commercial use"), "no": ("no", "No commercial use"),
                   "no_until": ("no", "No commercial use until the authors confirm"),
                   "unclear": ("unknown", "Commercial use not stated"), "ask": ("unknown", "Commercial use: ask the authors")},
    "training": {"allowed": ("yes", "ML training"), "same_licence": ("part", "ML training (a shared model may need the same licence)"),
                 "non_commercial_only": ("part", "ML training, non-commercial only"), "prohibited": ("no", "No ML training"),
                 "unclear": ("unknown", "ML training not stated"), "ask": ("unknown", "ML training: ask the authors")},
    "redistribute": {"yes": ("yes", "Share the audio"), "same_licence": ("part", "Share under the same licence"),
                     "nc": ("part", "Share non-commercially"), "nc_same": ("part", "Share non-commercially, same licence"),
                     "ask": ("unknown", "Sharing: ask the authors"), "unaltered_only": ("part", "Share unaltered files only"),
                     "no": ("no", "Do not share the audio"), "unknown": ("unknown", "Sharing not stated"),
                     "unclear": ("unknown", "Sharing not stated")},
}
PERM_FALLBACK = {"commercial": "Commercial use not stated", "training": "ML training not stated",
                 "redistribute": "Sharing not stated"}
# real SPDX identifiers (the others are LicenseRef-... or CC ids without a version)
SPDX_RE = re.compile(r"^(CC-BY(-NC)?(-SA|-ND)?-\d\.\d|CC0-1\.0|MIT|Apache-2\.0|GPL-\d\.\d-(only|or-later)|ODC-By-1\.0"
                     r"|BSD-\d-Clause)$")

INFO_FIELDS = [("dimensions", "Dimensions"), ("volume", "Volume"), ("geometry", "3-D model"), ("materials", "Materials"),
               ("positions", "Source and receiver positions"), ("images", "Photos or plans"),
               ("reference_acoustics", "Reference acoustics"), ("conditions", "Changing conditions")]
INFO_WORD = {"yes": "Published", "partial": "Partly", "no": "Not published", None: "Not checked"}

FLAG_GROUPS = {
    "content": ("Content", "Flagged IRs are left out of the clean-rooms view."),
    "decay": ("Decay", "Limits the decay analysis; the IR is kept."),
    "signal": ("Signal", "A property of the file or of the recording."),
    "spatial": ("Channel layout", "What the analysed channel is, and whether its orientation is known."),
}
# one space's kind (KIND_LABELS are plural group names)
KIND_ONE = {"room": "room", "outdoor": "outdoor", "vehicle": "vehicle", "scale_model": "scale model", "device": "device",
            "plate": "plate reverb", "spring": "spring reverb", "digital_reverb": "digital reverb",
            "creative": "creative / processed", "anechoic": "anechoic", "virtual": "virtual / modelled"}
EDC_MODE = {"lundeby": "Lundeby noise-floor compensation", "schroeder_nofloor": "plain Schroeder integration (no noise floor found)",
            "failed": "noise-floor fit failed, so no decay values", "n/a": "not computed"}
# a binaural or stereo IR is analysed on both channels: "a|b" when their modes differ (left|right)
EDC_SHORT = {"lundeby": "Lundeby", "schroeder_nofloor": "plain Schroeder", "failed": "fit failed", "n/a": "not computed"}

# histograms: (key, log x); titles and units come from site.json "params"
HISTS = [("t30_mid", True), ("edt_mid", True), ("c80_mid", False), ("drr_bb", False), ("sti", False),
         ("d50_mid", False), ("t_mix_ned_ms", False), ("pnr_db", False), ("bw_eff_hz", True), ("br", True)]
# inline range bars: axis span per parameter (the histogram bins where the corpus has them)
RANGE_AXES = {"t20_mid": (0.05, 20, True), "c50_mid": (-15, 35, False), "ts_mid": (0.005, 2, True),
              "tr": (0.3, 3, True), "iacc_e3": (0, 1, False), "jlf_low": (0, 1, False),
              "duration_raw_s": (0.01, 100, True), "onset_ms": (0.1, 2000, True)}
LOG_KEYS = {"t30_mid", "edt_mid", "br", "bw_eff_hz"}
BAND_METRIC_UNITS = {"t30": ("T30", "s", 2), "edt": ("EDT", "s", 2), "c80": ("C80", "dB", 1), "d50": ("D50", "", 2)}
STRIP_LABELS = {k: ("BB" if k == "bb" else (f"{int(k) // 1000}k" if int(k) >= 1000 else k)) for k in BAND_KEYS}

# chart width (px): the with-side layout gives each column of a two-column grid about 400 px; phones get a
# second drawing at NW, so its text is not scaled down below about 11 px
CW = 400
NW = 320

TOC = [("get", "Get the data"), ("licence", "Licence and use"), ("rooms", "Room information"), ("contents", "Contents"),
       ("analysis", "Analysis"), ("spaces", "Spaces"), ("map", "CLAP map"), ("validation", "Validation"),
       ("issues", "Known issues"), ("use", "Use it with rirdb"), ("probe", "Probe output"), ("cite", "Cite")]


# ----------------------------------------------------------------------------- small helpers

def spdx_label(i: str | None) -> str:
    if not i:
        return "Not stated"
    if i in SPDX_LABEL:
        return SPDX_LABEL[i]
    m = re.match(r"^CC-(.*?)(?:-(\d\.\d))?$", i)
    if m:
        return "CC " + m.group(1) + (" " + m.group(2) if m.group(2) else "")
    return i.removeprefix("LicenseRef-").replace("-", " ")


def ext_link(href: str, text: str | None = None) -> Markup:
    return Markup('<a href="{h}" rel="noopener">{t}<svg aria-hidden="true" width="12" height="12" '
                  'style="margin-left:3px;vertical-align:-1px"><use href="#i-ext"/></svg></a>').format(h=href, t=text or href)


def short_url(u: str, n: int = 52) -> str:
    """Link text for a long URL: host and path without the scheme; a long path keeps its host and its
    last segment (huggingface.co/…/Treble10-RIR)."""
    s = re.sub(r"^https?://(www\.)?", "", u).rstrip("/")
    if len(s) <= n:
        return s
    host, _, path = s.partition("/")
    last = path.rsplit("/", 1)[-1]
    if len(host) + len(last) + 3 <= n:
        return f"{host}/…/{last}"
    return s[: n - 18] + "…" + s[-17:]


def fnum(v, nd: int = 2) -> str:
    """fmt_num without a negative zero: -0.004 at 2 decimals reads 0.00, not −0.00."""
    if v is not None and isinstance(v, (int, float)) and v == v and round(v, nd) == 0:
        v = 0.0
    return fmt_num(v, nd)


URL_RE = re.compile(r"(https?://[^\s<>()\"']+[^\s<>()\"'.,;:])")


def inline_md(s: str | None) -> Markup:
    """Registry / findings text: `code` spans and bare URLs, everything else escaped."""
    if not s:
        return Markup("")
    out = []
    for i, part in enumerate(str(s).split("`")):
        if i % 2:
            out.append(Markup("<code>{}</code>").format(part))
        else:
            pieces = URL_RE.split(part)
            for j, piece in enumerate(pieces):
                # "6 %" never wraps before the sign, as fmt_pct
                out.append(ext_link(piece) if j % 2 else escape(re.sub(r"(\d) ?%(?!\w)", "\\1\u00a0%", piece)))
    return Markup("").join(out)


def words_list(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def counts_text(d: dict | None, label=lambda k: k) -> str:
    if not d:
        return ""
    return ", ".join(f"{label(k)} {fmt_int(v)}" for k, v in sorted(d.items(), key=lambda kv: -kv[1]))


def manual_reason(det: dict, row: dict, see: str = "see Known issues") -> str:
    """Why get/<id>.sh prints instructions and stops; the dataset page and the script use the same words.
    A host that refuses scripts (a data finding says so), a registration, or no scripted download in the
    registry (GWA, RAF and SMARD download with plain HTTP, but rirdb has no adapter for them yet)."""
    if any(re.search(r"refuse|reject", f"{f.get('finding') or ''} {f.get('evidence') or ''}", re.I)
           for f in det.get("findings") or [] if f.get("section") == "data"):
        return f"the host blocks scripted downloads ({see})"
    if row.get("access") == "registration":
        return "the files need a free registration"
    pub = public_record(row, det.get("lock") or [])
    if pub:
        return f"rirdb uses a group-store copy in place and has not scripted the download from {pub}"
    return "rirdb has no scripted download for it"


def public_record(row: dict, lock: list[dict]) -> str | None:
    """'Zenodo record 16186381' when rirdb uses a group-store copy in place (every lock row inplace:) but the
    registry names a public repository record for the same files (ReverbFX); otherwise None."""
    recs = row.get("records") or []
    if (not lock or not recs or row.get("source_kind") not in ("zenodo", "figshare", "depositonce")
            or not all((r.get("url") or "").startswith("inplace:") for r in lock)):
        return None
    return f"{SOURCE_LABELS[row['source_kind']]} record{'s' if len(recs) > 1 else ''} {', '.join(recs)}"


def ext_label(k: str) -> str:
    """A manifest extension key: "wav" -> ".wav"; data.manifests files a name without a dot as "(none)"."""
    return "no extension" if k == "(none)" else f".{k}"


def files_breakdown(mf: dict) -> str:
    """'25 .sofa, 1 .pdf and 26 macOS metadata files (._* or .DS_Store)': the parts add up to n_files, since
    data.manifests counts macOS metadata apart from the file types."""
    parts = [f"{fmt_int(n)} {'with no extension' if k == '(none)' else '.' + k}"
             for k, n in (mf.get("extensions") or {}).items()]
    mac = mf.get("n_macos_metadata") or 0
    if mac:
        parts.append(f"{fmt_int(mac)} macOS metadata file{'s' if mac != 1 else ''} (._* or .DS_Store)")
    return ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else "".join(parts)


def pct_d1(v) -> str:
    """Share with one decimal; "<0.1 %" for a non-zero share that would print as 0.0 %."""
    if v is not None and 0 < v < 0.0005:
        return "<0.1 %"
    return fmt_pct(v, 1)


def pct1(v) -> str:
    """One decimal under 10 %, none above."""
    return pct_d1(v) if v is not None and 0 < v < 0.1 else fmt_pct(v, 0)


def dual(wide: Markup, narrow: Markup) -> Markup:
    """The same chart drawn twice: CSS shows the narrow drawing on phones, so its text stays readable."""
    return Markup('<div class="vw-wide">') + wide + Markup('</div><div class="vw-narrow">') + narrow + Markup("</div>")


def cat_label(k: str | None) -> str:
    k = k or "unknown"
    return CATEGORY_LABEL.get(k, k.replace("_", " "))


def space_counts(det: dict, key: str, field: str) -> dict:
    """Records (or analysed IRs) per space kind or category, from the space rows, which carry the hand
    corrections of the Spaces page (registry/<id>/spaces.csv, a dataset's space_type); largest first."""
    c: Counter = Counter()
    for s in det.get("spaces") or []:
        c[s.get(key) or "unknown"] += s.get(field) or 0
    return {k: v for k, v in sorted(c.items(), key=lambda kv: -kv[1]) if v}


# "CC BY-NC-SA 4.0" in a survey licence label
CC_LABEL_RE = re.compile(r"\bCC BY((?:-NC)?(?:-SA|-ND)?) (\d\.\d)")


def strict_cc(label: str | None) -> str | None:
    """Where the statements conflict and both name a CC licence, the stricter one as an SPDX id
    ("CC BY 4.0 (Zenodo) vs CC BY-NC-SA 4.0 (files)" -> CC-BY-NC-SA-4.0); None otherwise."""
    found = CC_LABEL_RE.findall(label or "")
    if len(found) < 2:
        return None
    parts, ver = max(found, key=lambda t: len(t[0]))
    return f"CC-BY{parts}-{ver}"


def ir_training(spdx: str | None) -> str:
    """ML training as one IR's licence allows it (OpenAIR: one licence per space)."""
    s = spdx or "LicenseRef-unspecified"
    if s == "LicenseRef-unspecified":
        return "not stated"
    if s in ("LicenseRef-CC-PublicDomain", "CC0-1.0", "MIT", "Apache-2.0", "ODC-By-1.0") or re.fullmatch(r"CC-BY(-\d\.\d)?", s):
        return "allowed"
    if s.startswith("CC-BY"):
        if "-NC" in s:
            return "non-commercial only, no derivatives" if "-ND" in s else "non-commercial only"
        if "-SA" in s:
            return "allowed, but a shared model may need the same licence"
        if "-ND" in s:
            return "allowed, no derivatives"
    if s.startswith("GPL"):
        return "allowed, but a shared model may need the same licence"
    return "read the terms"


def table_html(head: list[tuple[str, str]], rows: list[list], caption: str | None = None) -> Markup:
    """Small static table. head: (label, "n" | ""), rows: cell values (str / Markup)."""
    h = Markup("").join(Markup('<th scope="col"{c}>{t}</th>').format(c=Markup(' class="n"') if cls == "n" else "", t=t)
                        for t, cls in head)
    body = []
    for r in rows:
        cells = []
        for (t, cls), v in zip(head, r):
            cells.append(Markup("<td{c}>{v}</td>").format(c=Markup(' class="n"') if cls == "n" else "", v=v))
        body.append(Markup("<tr>") + Markup("").join(cells) + Markup("</tr>"))
    cap = Markup('<caption class="sr-only">{}</caption>').format(caption) if caption else ""
    return (Markup('<div class="tbl-wrap"><table class="tbl">') + cap + Markup("<thead><tr>") + h
            + Markup("</tr></thead><tbody>") + Markup("").join(body) + Markup("</tbody></table></div>"))


def refine(cls: str | None, commercial, training, redistribute, spdx: str | None, label: str | None) -> dict:
    """The Field Guide's plain reading: conditional sharing and training for share-alike and non-commercial
    terms, the stricter reading where statements conflict, no permission at all where no licence is stated."""
    sa = bool(re.search(r"SA\b|GPL", f"{spdx or ''} {label or ''}"))
    if cls == "unspecified":
        return {"commercial": "ask", "training": "ask", "redistribute": "ask"}
    if cls == "conflict" and commercial == "unclear":
        commercial = "no_until"
    # a conflict whose stricter side is share-alike or GPL reads as share-alike: no unconditional training or sharing
    copyleft = cls == "share_alike" or (cls == "conflict" and sa)
    if copyleft and training == "allowed":
        training = "same_licence"
    if redistribute == "yes":
        if copyleft and (cls == "share_alike" or commercial == "yes"):
            redistribute = "same_licence"
        elif cls == "non_commercial" or (cls == "conflict" and commercial != "yes"):
            redistribute = "nc_same" if sa else "nc"
    return {"commercial": commercial, "training": training, "redistribute": redistribute}


# ----------------------------------------------------------------------------- page sections

def header(row: dict, det: dict) -> dict:
    ix, m, sv = row.get("index") or {}, row.get("metrics") or {}, row.get("survey") or {}
    pills = []
    if row.get("year"):
        pills.append(("", str(row["year"]), "Year published"))
    pills.append(("accent", TYPE_LABEL.get(row["type"], row["type"]), "Dataset type"))
    # what the index found where the dataset is indexed (as for sample rates and kinds), else the registry's list
    found = ix.get("formats_found") or {}
    for f in sorted(found, key=lambda k: -found[k]) if found else row.get("capture_formats") or []:
        pills.append(("", FORMAT_LABELS.get(f, f), f"Capture format found in the index: {fmt_int(found[f])} IRs" if found
                      else "Capture format (registry)"))
    fs = sorted(int(k) for k in ix["fs_found"]) if ix.get("fs_found") else sorted(row.get("fs_hz") or [])
    for f in fs:
        pills.append(("", fmt_fs(f), "Sample rate"))
    kinds = list(space_counts(det, "ir_kind", "n_records") or ix.get("kinds_found") or {}) or (row.get("ir_kinds") or [])
    for k in kinds:
        pills.append(("", KIND_LABELS.get(k, k), "Kind of IR"))
    pills.append(("", f"Status: {row['status']}", STATUS_TEXT.get(row["status"], "")))
    pills.append(("", f"Access: {row['access']}", ACCESS_TEXT.get(row["access"], "")))

    tiles = []
    if ix.get("n_records") is not None:
        sub = []
        if m.get("n_analysed") is not None:
            sub.append(f"{fmt_int(m['n_analysed'])} analysed")
        # the registry's expected count: the authors' figure or rirdb's count of the published files
        if row.get("expected_irs") is not None and row["expected_irs"] != ix["n_records"]:
            sub.append(f"expected: {fmt_int(row['expected_irs'])}")
        tiles.append({"value": fmt_int(ix["n_records"]), "label": "IRs indexed" + (f" ({'; '.join(sub)})" if sub else "")})
    elif row.get("expected_irs") is not None:
        tiles.append({"value": fmt_int(row["expected_irs"]), "label": "IRs, from the authors' description (not indexed here)"})
    n_sp = row.get("n_spaces_indexed") or ix.get("n_spaces")
    if n_sp:
        sub = f" (expected: {fmt_int(row['expected_rooms'])})" if row.get("expected_rooms") not in (None, n_sp) else ""
        tiles.append({"value": fmt_int(n_sp), "label": ("Space" if n_sp == 1 else "Spaces") + " indexed" + sub})
    elif row.get("expected_rooms") is not None:
        tiles.append({"value": fmt_int(row["expected_rooms"]),
                      "label": ("Space" if row["expected_rooms"] == 1 else "Spaces") + ", from the authors' description"})
    ls, mf = row.get("lock_summary"), row.get("manifest")
    inplace = bool(det.get("lock")) and all((r.get("url") or "").startswith("inplace:") for r in det["lock"])
    if ls and ls.get("bytes"):
        tiles.append({"value": fmt_bytes(ls["bytes"]),
                      "label": ("Group-store copy" if inplace else "Download")
                      + f", {fmt_int(ls['n'])} file{'s' if ls['n'] != 1 else ''}"})
    elif ls and det.get("lock") and det["lock"][0].get("relpath") == "@mirror":
        tiles.append({"value": "Mirror", "label": "Download: a web folder, copied file by file"})
    elif ls:
        tiles.append({"value": "–", "label": "Download size: not published by the host"})
    if mf and mf.get("bytes"):
        tiles.append({"value": fmt_bytes(mf["bytes"]), "label": f"Extracted, {fmt_int(mf['n_files'])} files"})
    lic = row.get("licence") or {}
    tiles.append({"value": sv.get("licence_label") or spdx_label(lic.get("spdx")), "label": "Licence", "small": True})
    return {"pills": pills, "tiles": tiles}


def get_section(row: dict, det: dict, reg) -> dict:
    sv = row.get("survey") or {}
    facts: list[tuple[str, Markup]] = []
    if row.get("homepage"):
        facts.append(("Homepage", ext_link(row["homepage"], short_url(row["homepage"]))))
    if row.get("doi"):
        u = doi_url(row["doi"])
        if sv.get("doi_is_paper"):
            facts.append(("Paper DOI", ext_link(u, row["doi"]) + Markup(' <span class="muted small">(the DOI of the '
                                                                          'paper; the data has no DOI of its own)</span>')))
        else:
            facts.append(("DOI", ext_link(u, row["doi"])))
    kind = row.get("source_kind")
    recs = []
    for rec in row.get("records") or []:
        u = record_url(kind, rec)
        recs.append(ext_link(u, f"{SOURCE_LABELS.get(kind, kind)} {rec}") if u else escape(rec))
    if recs:
        facts.append(("Repository record" + ("s" if len(recs) > 1 else ""), Markup("<br>").join(recs)))
    src = reg.source if reg is not None else None
    if src is not None and src.urls:
        if kind == "wayback":
            # the original site is gone: link the Wayback Machine's copy, at the snapshot the fetch script reads
            m = re.search(r"web\.archive\.org/web/(\d+)", row.get("homepage") or "")
            snap = m.group(1) if m else "2"
            links = [ext_link(f"https://web.archive.org/web/{snap}/{u}", short_url(u)) if u.startswith("http") else escape(u)
                     for u in src.urls]
            facts.append(("Source URL" + ("s" if len(src.urls) > 1 else ""), Markup("<br>").join(links)
                          + Markup(' <span class="muted small">(the Wayback Machine\'s copy)</span>')))
        else:
            facts.append(("Source URL" + ("s" if len(src.urls) > 1 else ""),
                          Markup("<br>").join(ext_link(u, short_url(u)) if u.startswith("http") else escape(u)
                                              for u in src.urls)))
    facts.append(("Source", escape(SOURCE_LABELS.get(kind, kind or "–"))))
    facts.append(("Access", escape(f"{row['access'].capitalize()}. " + ACCESS_TEXT.get(row["access"], ""))))
    status = f"{row['status'].capitalize()}. " + STATUS_TEXT.get(row["status"], "")
    if row.get("wave") is not None:
        status += f" Download wave {row['wave']} ({WAVE_TEXT.get(row['wave'], '')})."
    facts.append(("Status", escape(status)))
    if sv.get("host"):
        facts.append(("Host", escape(sv["host"])))
    inst = ", ".join(x for x in (sv.get("institution"), sv.get("country")) if x)
    if inst:
        facts.append(("Institution", escape(inst)))
    selection = []
    if src is not None:
        for label, vals in (("Only files matching", src.include), ("Skipping", src.exclude), ("Accepting", src.accept)):
            if vals:
                selection.append((label, vals))

    lock = det.get("lock") or []
    # what get/<id>.sh does (as pages_get.mode_of): a manual source or a copy adopted in place has no scripted download
    script_manual = row.get("source_kind") == "manual" or any(
        (r.get("url") or "").startswith(("inplace:", "manual:")) for r in lock)
    instr = inline_md(src.instructions) if src is not None and src.instructions else Markup("")
    if src is not None and re.search(r"adopt_|RIRDB_ROOT|sbatch", src.instructions or ""):
        # the same caveat the fetch script prints (pages_get)
        instr += Markup(" Steps that run <code>sbatch</code> or mention <code>$RIRDB_ROOT</code> are for the rirdb "
                        "pipeline on Apocrita. Outside it, keep the files wherever you like.")
    callout = None
    st = row["status"]
    if st == "broken":
        callout = ("bad", "The published download is broken.", inline_md(row.get("notes")))
    elif st == "manual" or row["access"] in ("manual", "registration") or (script_manual and instr):
        what = {"registration": "Free registration is needed.",
                "manual": ("The files are public, but rirdb's script does not download them." if public_record(row, lock)
                           else "This dataset needs a manual download.")}
        callout = ("warn", what.get(row["access"], "This dataset needs a manual step to download."), instr)
    elif st in ("planned", "deferred"):
        callout = ("", "Not fetched by rirdb yet.",
                   Markup("The fetch script downloads it from the original source. ") + inline_md(row.get("notes")))

    rows = []
    for i, r in enumerate(sorted(lock, key=lambda r: natural_key(r.get("relpath") or ""))):
        url = r.get("url") or ""
        ck = r.get("checksum")
        rows.append({
            "i": i, "relpath": "Whole web folder (mirrored)" if r.get("relpath") == "@mirror" else r.get("relpath"),
            "size": r.get("size"), "algo": r.get("algo"), "checksum": ck if ck and ck != "-" else None,
            "url": url if url.startswith("http") else None,
            "where": ("local copy in the group store" if url.startswith("inplace:")
                      else "added by hand" if url.startswith("manual:") else url),
        })
    ls = row.get("lock_summary") or {}
    algos = [a for a in ls.get("algos") or [] if a != "manifest"]
    files = row.get("files") or {}
    lock_note = None
    if lock and lock[0].get("relpath") == "@mirror":
        lock_note = ("The source is a web folder, so there is no archive to check; rirdb mirrors it file by file and "
                     "the manifest holds the sha256 of every file.")
    elif algos and lock and all((r.get("url") or "").startswith("inplace:") for r in lock):
        pub = public_record(row, lock)
        lock_note = (f"Checksums ({', '.join(algos)}) computed by rirdb when it adopted the group-store copy: they are "
                     + (f"of the extracted files, not of the archives in {pub}." if pub
                        else "of its files, which have no public download URL in the registry."))
    elif algos:
        lock_note = (f"Checksums ({', '.join(algos)}) "
                     + ("as published by the provider." if files.get("provider_checksum")
                        else "computed by rirdb on the first download: the host publishes none."))
    mf = row.get("manifest")
    top = (det.get("manifest") or mf or {}).get("top_dirs") or []
    storage = row.get("storage") or det.get("storage")
    i = row["id"]
    inplace = sorted({(r.get("url") or "").removeprefix("inplace:") for r in lock
                      if (r.get("url") or "").startswith("inplace:")})
    if inplace:
        pipeline = (f"# rirdb uses the group-store copy in place (no copy is made):\n"
                    f"sbatch jobs/quick_cpu.sh bash scripts/storage/adopt_inplace.sh {i} {inplace[0]}\n"
                    f"sbatch jobs/quick_cpu.sh pixi run rirdb dev verify {i}")
    elif row["source_kind"] == "manual":
        pipeline = (f"# after placing the files in $RIRDB_ROOT/raw/_inbox/{i}/\n"
                    f"sbatch jobs/download.sh fetch/adopt_manual.sh {i}\n"
                    f"sbatch jobs/quick_cpu.sh pixi run rirdb dev verify {i}")
    else:
        pipeline = (f"sbatch jobs/download.sh fetch/run.sh {i}\n"
                    f"sbatch jobs/quick_cpu.sh pixi run rirdb dev verify {i}"
                    + (f"\n# or check every dataset of wave {row['wave']}:\n"
                       f"sbatch jobs/quick_cpu.sh pixi run rirdb dev verify --wave {row['wave']}"
                       if row.get("wave") is not None else ""))
    if script_manual:
        why = manual_reason(det, row)
        script_text = ("A standalone script for this dataset, made from the registry: it prints how to get the files "
                       f"and the homepage, then stops: {why}."
                       + (" If <target_dir>/" + row["id"] + "/ already holds a copy, it checks that copy against "
                          "rirdb's checksums." if lock else "") + " No audio is hosted here.")
    else:
        script_text = ("A standalone script for this dataset, made from the registry"
                       + (" and the lock file" if lock else "")
                       + ": it downloads from the original source and checks what it can."
                       + (" Known issues records a problem with the host: read it before running the script."
                          if any(f.get("section") == "data" and HOST_ISSUE_RE.search(
                              f"{f.get('finding')} {f.get('evidence') or ''}") for f in det.get("findings") or [])
                          else "")
                       + " No audio is hosted here.")
    return {
        "pipeline": pipeline, "script_text": script_text,
        "facts": facts, "selection": selection, "callout": callout,
        # a long lock (ReverbFX: 1,834 files) shows 500 rows; the TSV holds every row
        "lock_rows": rows[:25], "lock_rest": rows[25:500], "lock_more": max(0, len(rows) - 500),
        "lock_n": len(rows), "lock_note": lock_note,
        "lock_resolved": (ls.get("resolved_at") or "")[:10] or None,
        "has_lock": bool(lock), "manifest": mf, "top_dirs": top, "breakdown": files_breakdown(mf) if mf else "",
        "storage": storage if storage and storage.get("path") else None,
        "storage_text": TIER_TEXT.get((storage or {}).get("tier"), ""),
        "can_pipeline": st != "broken",
    }


def licence_section(site, row: dict, det: dict) -> dict:
    lic, sv = row.get("licence") or {}, row.get("survey") or {}
    spdx = lic.get("spdx")
    label = sv.get("licence_label") or spdx_label(spdx)
    cls = sv.get("licence_class") or lic.get("class")
    r = refine(cls, sv.get("commercial") or lic.get("commercial"), sv.get("training") or lic.get("training"),
               sv.get("redistribute") or lic.get("redistribute"), spdx, label)
    perms = [PERM_TEXT[k].get(r[k] or "", ("unknown", PERM_FALLBACK[k])) for k in ("commercial", "training", "redistribute")]
    if cls == "mixed":
        perms = [("unknown", "Commercial use: check each space"), ("unknown", "ML training: check each space"),
                 ("unknown", "Sharing: check each space")]
    # share-alike and changes: read from the licence that applies. A publisher's own terms (LicenseRef-..., other
    # than public domain) say nothing through their identifier; conflicting statements follow the stricter licence.
    sa, nd = lic.get("share_alike"), lic.get("no_derivatives")
    word = {"unspecified": "ask the authors", "custom": "read the terms", "mixed": "check each space"}.get(cls)
    if cls == "conflict":
        strict = strict_cc(label)
        if strict:
            sa, nd = "-SA" in strict, "-ND" in strict
        else:
            word = "read the stricter terms"
    elif word is None and (spdx or "").startswith("LicenseRef-") and spdx != "LicenseRef-CC-PublicDomain":
        word = "read the terms"
    if word:
        perms += [("unknown", f"Share-alike: {word}"), ("unknown", f"Changes: {word}")]
    else:
        perms.append(("part", "Share-alike: adaptations keep the licence") if sa
                     else ("yes", "No share-alike condition"))
        perms.append(("no", "No derivatives: share unchanged copies only") if nd
                     else ("yes", "Changes allowed"))
    spdx_url = f"https://spdx.org/licenses/{spdx}.html" if spdx and SPDX_RE.match(spdx) else None
    # licences per space and per IR (OpenAIR: one per space)
    spaces = det.get("spaces") or []
    a = det.get("analysis") or {}
    per_space = Counter(s.get("licence") or "LicenseRef-unspecified" for s in spaces if s.get("licence"))
    per_ir = a.get("licences") or {}
    keys = sorted(set(per_space) | set(per_ir), key=lambda k: -(per_space.get(k, 0) * 10**7 + per_ir.get(k, 0)))
    split = None
    if len(keys) > 1:
        split = [{"spdx": k, "label": spdx_label(k), "spaces": per_space.get(k), "irs": per_ir.get(k)} for k in keys]
    registry_split = site.data.get("openair_licences") if row["id"] == "openair" else None
    # ML training read from each IR's own licence (the index's training_use calls every CC BY variant "allowed")
    training_ir: Counter = Counter()
    for k, n in per_ir.items():
        training_ir[ir_training(k)] += n
    cls_label, cls_text = LICENCE_CLASSES.get(cls, (cls or "–", ""))
    return {"label": label, "spdx": spdx, "spdx_url": spdx_url, "url": lic.get("url"), "cls_label": cls_label,
            # a URL that was checked and states no licence is not where a licence is "stated"
            "url_label": "Checked for a licence at" if cls == "unspecified" else "Licence stated at",
            "cls_text": cls_text, "perms": perms, "note": inline_md(lic.get("note")) if lic.get("note") else None,
            "survey_note": inline_md(sv.get("licence_note")) if sv.get("licence_note") else None,
            "split": split,
            "registry_split": [(spdx_label(k), n) for k, n in sorted(registry_split.items(), key=lambda kv: -kv[1])]
            if registry_split else None,
            "training_ir": sorted(training_ir.items(), key=lambda kv: -kv[1]) if len(training_ir) > 1 else None}


def rooms_section(row: dict) -> dict:
    ri = row.get("room_info") or {}
    fields = [(label, ri.get(k), INFO_WORD.get(ri.get(k), ri.get(k))) for k, label in INFO_FIELDS]
    checked = any(v is not None for _, v, _ in fields)
    return {"fields": fields, "where": inline_md(row.get("room_info_where")) if row.get("room_info_where") else None,
            "checked": checked}


def contents_section(row: dict, det: dict, reg) -> dict:
    ix, a = row.get("index") or {}, det.get("analysis") or {}
    facts: list[tuple[str, str]] = []
    if ix:
        facts.append(("IR records indexed", fmt_int(ix.get("n_records"))))
        if ix.get("n_channels") is not None:
            facts.append(("Audio channels in those records", fmt_int(ix["n_channels"])))
        facts.append(("Spaces", fmt_int(ix.get("n_spaces"))))
        # kinds from the space rows, as the Spaces table and the Spaces page show them (with the hand
        # corrections); the per-IR index kinds where a dataset has no space rows
        sp_kinds = space_counts(det, "ir_kind", "n_records")
        if sp_kinds:
            rooms = [s for s in det.get("spaces") or [] if s.get("ir_kind") == "room"]
            n_room = sum(s.get("n_records") or 0 for s in rooms)
            facts.append(("Of which rooms", "none" if not n_room else
                          f"{fmt_int(n_room)} records in {fmt_int(len(rooms))} space{'s' if len(rooms) != 1 else ''}"))
        elif ix.get("n_room_records") is not None:
            facts.append(("Of which rooms", "none" if not ix["n_room_records"] else
                          f"{fmt_int(ix['n_room_records'])} records in {fmt_int(ix.get('n_room_spaces'))} "
                          f"space{'s' if ix.get('n_room_spaces') != 1 else ''}"))
        if ix.get("n_conditions"):
            facts.append(("Conditions (settings, panels, furniture ...)", fmt_int(ix["n_conditions"])))
        # only where the index carries coordinates: 0 would read as "no positions published" (see Room information)
        if ix.get("share_positions"):
            facts.append(("Records with source and receiver coordinates in rirdb's index", fmt_pct(ix["share_positions"])))
        if ix.get("formats_found"):
            facts.append(("Capture formats found", counts_text(ix["formats_found"], lambda k: FORMAT_LABELS.get(k, k))))
            if set(row.get("capture_formats") or []) - set(ix["formats_found"]):
                # the registry describes the capture (an array, a binaural head), the index each file it reads
                facts.append(("Capture formats (registry)",
                              ", ".join(FORMAT_LABELS.get(f, f) for f in row["capture_formats"])))
        if ix.get("fs_found"):
            facts.append(("Sample rates found", counts_text(ix["fs_found"], lambda k: fmt_fs(k))))
        if sp_kinds or ix.get("kinds_found"):
            facts.append(("Kinds of IR", counts_text(sp_kinds or ix["kinds_found"], lambda k: KIND_LABELS.get(k, k))))
        if ix.get("duration_s_p50") is not None:
            d = f"{fmt_num(ix['duration_s_p50'], 2)} s median"
            if ix.get("duration_s_max") is not None:
                d += f", {fmt_num(ix['duration_s_max'], 2)} s longest"
            facts.append(("File length", d))
    else:
        if row.get("capture_formats"):
            facts.append(("Capture formats (registry)", ", ".join(FORMAT_LABELS.get(f, f) for f in row["capture_formats"])))
        if row.get("fs_hz"):
            facts.append(("Sample rates (registry)", ", ".join(fmt_fs(f) for f in row["fs_hz"])))
        if row.get("ir_kinds"):
            facts.append(("Kinds of IR (registry)", ", ".join(KIND_LABELS.get(k, k) for k in row["ir_kinds"])))
    roles = {(k or "(not named)"): v for k, v in (a.get("roles") or {}).items()}
    if roles:
        facts.append(("Channel analysed", counts_text(roles)))
    elif reg is not None and reg.analysis.reference_role:
        facts.append(("Channel analysed", reg.analysis.reference_role))
    fl = row.get("files") if (row.get("files") or {}).get("extensions") else row.get("manifest") or {}
    ext = fl.get("extensions")
    if ext:
        mac = fl.get("n_macos_metadata") or 0
        facts.append(("File types after extraction", counts_text(ext, ext_label)
                      + (f" (and {fmt_int(mac)} macOS metadata file{'s' if mac != 1 else ''})" if mac else "")))
    # expected against found
    exp_irs, exp_rooms = row.get("expected_irs"), row.get("expected_rooms")
    expected = None
    if exp_irs is not None or exp_rooms is not None:
        said = words_list([f"{fmt_int(exp_irs)} IRs" if exp_irs is not None else "",
                           f"{fmt_int(exp_rooms)} space{'s' if exp_rooms != 1 else ''}" if exp_rooms is not None else ""])
        # registry "expected": confirmed against the files by `rirdb dev probe` once indexed, so often rirdb's own
        # count of the published files rather than the authors' figure (the note says which)
        if ix.get("n_records") is not None:
            expected = (f"The registry expects {said}. rirdb indexed {fmt_int(ix['n_records'])} IR records in "
                        f"{fmt_int(ix.get('n_spaces'))} space{'s' if ix.get('n_spaces') != 1 else ''}.")
        else:
            expected = f"From the authors' description: {said}."
    # categories per space, with the Spaces page's corrections and words
    cats = space_counts(det, "category", "n_analysed") or a.get("categories") or {}
    cat_chart = None
    if len(cats) > 1:
        items = [{"label": cat_label(k), "value": v, "tip": f"{cat_label(k)}: {v:,} analysed IRs"}
                 for k, v in sorted(cats.items(), key=lambda kv: -kv[1])]
        cat_chart = dual(charts.hbars(items, w=CW, title="Analysed IRs per space category"),
                         charts.hbars(items, w=NW, title="Analysed IRs per space category"))
    return {"facts": facts, "expected": expected, "expected_note": (row.get("expected_note") or "").rstrip(".") or None, "indexed": bool(ix),
            "cat_chart": cat_chart, "content": row.get("content")}


def no_analysis_reason(row: dict) -> str:
    st, wave, typ = row["status"], row.get("wave"), row["type"]
    later = (" Simulated and production sets are wave 4: rirdb fetches and analyses them after the measured corpus."
             if wave == 4 else "")
    if st == "broken":
        return "The published download is broken, so there is nothing to extract or analyse."
    if st == "manual":
        return ("The files need a manual download and have not been adopted into rirdb yet, so they are not indexed "
                "or analysed.")
    if st in ("planned", "deferred"):
        what = "planned for a download wave" if st == "planned" else "deferred"
        kind = f" ({TYPE_LABEL.get(typ, typ).lower()})" if typ != "measured" else ""
        return f"Not fetched yet: the dataset{kind} is {what}.{later}"
    if row.get("lock_summary"):
        return "Downloaded and checked against the lock file, but not indexed or analysed yet." + later
    return "Not analysed yet." + later


def analysis_section(site, row: dict, det: dict, root: str) -> dict | None:
    a = det.get("analysis")
    if not a:
        return None
    ix = row.get("index") or {}
    pinfo = {p["key"]: p for p in site.data.get("params") or []}
    ref_h = site.data.get("reference_hists") or {}
    out: dict = {"n_analysed": a.get("n_analysed"), "n_stats": a.get("n_stats"), "n_errors": a.get("n_errors"),
                 "errors": a.get("errors") or {}}
    # scope
    n_rec, n_sub = ix.get("n_records"), ix.get("n_in_subsample")
    if row.get("analysis_scope") == "subsample_v1" and n_rec:
        out["scope"] = (f"rirdb analysed a fixed subsample of {fmt_int(n_sub or a['n_analysed'])} of the "
                        f"{fmt_int(n_rec)} indexed records ({pct1((n_sub or a['n_analysed']) / n_rec)}): a deterministic "
                        "hash of each record's name, so the same records are picked on every run, keeping at least one "
                        "record per space and condition.")
        out["subsampled"] = True
    else:
        out["scope"] = (f"rirdb analysed all {fmt_int(a['n_analysed'])} indexed records." if n_rec in (None, a["n_analysed"])
                        else f"rirdb analysed {fmt_int(a['n_analysed'])} of the {fmt_int(n_rec)} indexed records.")
        out["subsampled"] = False
    kinds = space_counts(det, "ir_kind", "n_records") or ix.get("kinds_found") or a.get("kinds") or {}
    out["anechoic"] = bool(kinds) and set(kinds) <= {"anechoic"}
    # the room-acoustic values (parameters, bands, validity) leave out stats IRs that are not of real spaces
    n_sp = a.get("n_stats_space") if a.get("n_stats_space") is not None else a.get("n_stats")
    left = {k: v for k, v in (a.get("stats_left_out") or {}).items() if v}
    out["left_out"] = out["left_room"] = None
    out["n_space"] = n_sp
    if left:
        n_left = sum(left.values())
        # "41 anechoic IRs are", "16 virtual / modelled and 11 device IRs are"
        word = {"virtual": "modelled", "scale_model": "scale-model"}
        parts = [f"{fmt_int(v)} {word.get(k) or KIND_ONE.get(k, 'unlabelled')}" for k, v in sorted(left.items(), key=lambda kv: -kv[1])]
        what = (parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]) + \
            (" IRs are" if n_left != 1 else " IR is")
        out["left_room"] = f"{what} left out of the room parameters"
        out["left_out"] = (f"The room-acoustic values (parameters, bands, validity) cover the {fmt_int(n_sp)} of them "
                           f"that are of real spaces: {what} left out.")
    # grades
    g = a.get("grades") or {}
    out["grades"] = {k: g.get(k, 0) for k in "ABCD"}
    out["n_graded"] = sum(out["grades"].values())
    out["grade_bar"] = (dual(charts.grade_bar(out["grades"], w=CW, title="Grades of the analysed IRs"),
                             charts.grade_bar(out["grades"], w=NW, title="Grades of the analysed IRs"))
                        if out["n_graded"] else None)
    out["grade_text"] = list((site.data.get("grades") or {}).items())
    # parameter table
    params = []
    for p in a.get("params") or []:
        k = p["key"]
        if k in ref_h:
            lo, hi, log = ref_h[k]["edges"][0], ref_h[k]["edges"][-1], k in LOG_KEYS
        else:
            lo, hi, log = RANGE_AXES.get(k, (None, None, False))
        if log and lo is not None and lo <= 0:
            lo = hi / 1000
        unit = p.get("unit") or ""
        nd = p.get("nd", 2)
        rng = None
        if lo is not None and p.get("p50") is not None:
            u = f" {unit}" if unit else ""
            tip = (f"{p['label']}: 10–90\u00a0% {fnum(p.get('p10'), nd)}–{fnum(p.get('p90'), nd)}{u}, "
                   f"median {fnum(p['p50'], nd)}{u}")
            rng = charts.range_mini(p.get("p10"), p["p50"], p.get("p90"), lo=lo, hi=hi, log=log, tip=tip)
        params.append({"key": k, "label": p["label"], "unit": unit, "nd": nd, "n": p.get("n"), "p10": p.get("p10"),
                       "p50": p.get("p50"), "p90": p.get("p90"), "p10_s": fnum(p.get("p10"), nd),
                       "p50_s": fnum(p.get("p50"), nd), "p90_s": fnum(p.get("p90"), nd), "info": (pinfo.get(k) or {}).get("info"), "rng": rng,
                       "axis": (f"axis {charts.tick_label(lo)}–{charts.tick_label(hi)}{' ' + unit if unit else ''}"
                                f"{', log' if log else ''}" if lo is not None else "")})
    out["params"] = params
    out["param_note"] = None
    if left:
        sig = [p["label"] for p in params if p["key"] in SIGNAL_PARAMS]
        sig = [s if i == 0 else s[:1].lower() + s[1:] for i, s in enumerate(sig)]
        out["param_note"] = (f"Room parameters over the {fmt_int(n_sp)} IRs of real spaces used for statistics: "
                             f"{out['left_room']}."
                             + (f" {', '.join(sig[:-1]) + ' and ' + sig[-1] if len(sig) > 1 else sig[0]} cover all "
                                f"{fmt_int(a.get('n_stats'))} IRs used for statistics." if sig else ""))
    # valid T30 at 500 Hz-1 kHz over the same IRs as the parameter table (t30_mid is set only where both
    # octaves are valid), not over every analysed IR as site.json metrics.valid_t30_mid counts it
    t30 = next((p for p in params if p["key"] == "t30_mid"), None)
    out["valid_t30"] = ((t30["n"] or 0) if t30 else 0) / n_sp if n_sp else None
    out["valid_t30_label"] = (f"Of the {fmt_int(n_sp)} IRs" + (" of real spaces" if left else "")
                              + " used for statistics, with a valid T30 at 500 Hz–1 kHz")
    # histograms against the corpus
    hists = []
    for k, log in HISTS:
        h = (a.get("hists") or {}).get(k)
        if not h or not h.get("n"):
            continue
        p = pinfo.get(k) or {}
        unit, nd = p.get("unit") or "", p.get("nd", 2)
        ref = ref_h.get(k)
        ref_label = f"Corpus: one median per space ({ref['subset']})" if ref else ""
        hargs = dict(log_x=log, height=190, unit=unit, nd=max(nd, 1) if k != "bw_eff_hz" else 0,
                     label="This dataset", ref_label=ref_label or "Corpus",
                     title=f"{p.get('label', k)}: share of this dataset's IRs per bin, against the corpus")
        svg = dual(charts.hist(h, ref, w=CW, **hargs), charts.hist(h, ref, w=NW, **hargs))
        clipped = (h.get("below") or 0) + (h.get("above") or 0)
        sub = f"{fmt_int(h['n'])} IRs" + (f"; {fmt_int(clipped)} outside the axis are counted in the end bins" if clipped else "")
        table = None
        if k == "t30_mid":
            rn = sum(ref["counts"]) if ref else 0
            rows = []
            for i, c in enumerate(h["counts"]):
                rc = ref["counts"][i] if ref and ref.get("edges") == h["edges"] else None
                if not c and not rc:
                    continue
                rows.append([f"{fmt_num(h['edges'][i], 2)}–{fmt_num(h['edges'][i + 1], 2)}", fmt_int(c),
                             pct_d1(c / h["n"]), pct_d1(rc / rn) if rc is not None and rn else "–"])
            table = table_html([("T30 (s)", ""), ("IRs", "n"), ("Share", "n"), ("Corpus share", "n")], rows,
                               "T30 histogram of this dataset against the corpus")
        hists.append({"key": k, "title": f"{p.get('label', k)}" + (f" ({unit})" if unit else ""),
                      "sub": sub, "svg": svg, "info": p.get("info"), "table": table})
    out["hists"] = hists
    # per-band boxes
    bands = []
    for m, (label, unit, nd) in BAND_METRIC_UNITS.items():
        bx = (a.get("bands") or {}).get(m) or {}
        if not any((v or {}).get("n") for k, v in bx.items() if k != "bb"):
            continue
        ref = (a.get("ref_bands") or {}).get(m) or {}
        val = (a.get("validity") or {}).get(m) or {}
        bargs = dict(height=220, unit=unit, nd=nd, validity=val, zero=m != "c80",
                     title=f"{label} per octave band: this dataset against the corpus median")
        svg = dual(charts.bands(bx, ref, w=CW, **bargs), charts.bands(bx, ref, w=NW, **bargs))
        rows = []
        for b in BAND_KEYS:
            q = (bx.get(b) or {}).get("q")
            rq = (ref.get(b) or {}).get("q")
            rows.append([("Broadband" if b == "bb" else f"{STRIP_LABELS[b]} Hz"), fmt_int((bx.get(b) or {}).get("n")),
                         fnum(q[0], nd) if q else "–", fnum(q[2], nd) if q else "–", fnum(q[4], nd) if q else "–",
                         fmt_pct(val[b]) if val.get(b) is not None else "–", fnum(rq[2], nd) if rq else "–"])
        table = table_html([("Band", ""), ("n valid", "n"), ("5\u00a0%", "n"), ("Median", "n"), ("95\u00a0%", "n"),
                            ("Valid", "n"), ("Corpus median", "n")], rows, f"{label} per octave band")
        bands.append({"title": f"{label} per octave band" + (f" ({unit})" if unit else ""), "svg": svg, "table": table})
    out["bands"] = bands
    # validity strips
    strips = []
    for m, label in (("t30", "T30"), ("t20", "T20"), ("edt", "EDT")):
        val = (a.get("validity") or {}).get(m)
        if val and any(v for v in val.values()):
            strips.append({"label": label, "html": charts.strip(val, BAND_KEYS, labels=STRIP_LABELS)})
    out["strips"] = strips
    # quality flags, grouped
    finfo = {f["flag"]: f for f in site.data.get("flags") or []}
    fl = a.get("flags") or []
    vmax = max([f["share"] for f in fl if f.get("n")] + [0.01])
    groups, zero = [], []
    for g, (glabel, gtext) in FLAG_GROUPS.items():
        fired = [f for f in fl if f["group"] == g and f.get("n")]
        zero += [f["label"] for f in fl if f["group"] == g and not f.get("n")]
        if not fired:
            continue
        fired.sort(key=lambda f: -f["share"])
        items = [{"label": f["label"], "value": f["share"], "text": f"{pct1(f['share'])} ({fmt_int(f['n'])})",
                  "tip": f"{f['label']}: {fmt_int(f['n'])} of {fmt_int(a.get('n_analysed'))} IRs ({pct1(f['share'])})\n"
                         f"{(finfo.get(f['flag']) or {}).get('info', '')}"} for f in fired]
        ftitle = f"{glabel} flags: share of analysed IRs"
        groups.append({"label": glabel, "text": gtext,
                       "svg": dual(charts.hbars(items, w=CW, max_v=vmax, label_w=160, title=ftitle),
                                   charts.hbars(items, w=NW, max_v=vmax, label_w=150, title=ftitle)),
                       "defs": [(f["label"], (finfo.get(f["flag"]) or {}).get("info", ""), f["flag"]) for f in fired]})
    out["flag_groups"] = groups
    out["flags_zero"] = zero
    out["n_flags"] = len(fl)
    # signal notes
    notes = []
    rs = a.get("resampled_from") or {}
    if rs:
        notes.append(("Upsampled files", "; ".join(
            f"{fmt_int(n)} IR{'s' if n != 1 else ''} carry content only up to {fmt_num(int(k) / 2000, 0 if int(k) % 2000 == 0 else 1)} kHz: "
            f"resampled from {fmt_fs(int(k))}" for k, n in rs.items())))
    bw = next((p for p in params if p["key"] == "bw_eff_hz"), None)
    if bw and bw.get("p50") is not None:
        notes.append(("Effective bandwidth", f"median {fmt_num(bw['p50'] / 1000, 1)} kHz (10–90\u00a0%: "
                                             f"{fmt_num((bw['p10'] or 0) / 1000, 1)}–{fmt_num((bw['p90'] or 0) / 1000, 1)} kHz)"))
    em = a.get("edc_mode_bb") or {}
    if em:
        # "a|b": the two channels of a binaural or stereo IR took different paths; one bucket per pair, either order
        modes: Counter = Counter()
        for k, v in em.items():
            parts = k.split("|")
            if len(set(parts)) == 1:
                modes[EDC_MODE.get(parts[0], parts[0].replace("_", " "))] += v
            else:
                a_, b_ = sorted(set(parts))[:2]
                modes[f"left and right channels differ ({EDC_SHORT.get(a_, a_)} on one, "
                      f"{EDC_SHORT.get(b_, b_)} on the other)"] += v
        notes.append(("Noise floor (broadband decay)", "; ".join(
            f"{k}: {fmt_int(v)} IR{'s' if v != 1 else ''}" for k, v in sorted(modes.items(), key=lambda kv: -kv[1]))))
    out["notes"] = notes
    # duplicates
    d = a.get("duplicates") or {}
    dd = det.get("duplicates_detail") or {}
    known = {r["id"] for r in site.datasets}
    cross = [{"id": k, "n": v, "href": f"{root}d/{k}.html" if k in known else None}
             for k, v in sorted((dd.get("cross_with") or d.get("cross_with") or {}).items(), key=lambda kv: -kv[1])]
    out["dups"] = {"n_copies": d.get("n_copies") or 0, "n_in_groups": dd.get("n_in_groups") or d.get("n_in_groups") or 0,
                   "n_groups": dd.get("n_groups") or d.get("n_groups") or 0, "n_cross": dd.get("n_cross") or d.get("n_cross") or 0,
                   "cross": cross}
    return out


def natural_key(s: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s or "")]


def space_licences(dataset_id: str) -> dict[str, dict]:
    """The `licence` columns of registry/<id>/spaces.csv (OpenAIR): space -> {spdx, source, note}, the
    registry's per-space licence with the archived page it comes from."""
    from rirdb.rooms import curated_spaces

    s = curated_spaces(dataset_id)
    if "licence" not in s:
        return {}
    return {r["space"]: {"spdx": r["licence"], "source": r["licence_source"], "note": r["licence_note"]}
            for r in s.to_dict("records") if r["licence"]}


def spaces_section(det: dict, dataset_id: str = "") -> dict | None:
    reg_lic = space_licences(dataset_id) if dataset_id else {}
    rows = []
    for s in sorted(det.get("spaces") or [], key=lambda s: natural_key(s.get("room_label") or s.get("room_id"))):
        t = s.get("t30")
        t_fallback = t_any = False
        if t is None or (s.get("share_valid_t30") is not None and s["share_valid_t30"] < 0.5):
            if s.get("t30_or_t20") is not None:
                t, t_fallback = s["t30_or_t20"], s.get("t30_or_t20") != s.get("t30")
        if t is None and s.get("t30_any") is not None and not s.get("n_clean"):
            # no clean measured IR (production libraries): the median T30 over every IR of the space
            t, t_any = s["t30_any"], True
        rows.append({**s, "t_room": t, "t_fallback": t_fallback, "t_any": t_any, "t_s": fnum(t, 2),
                     "edt_s": fnum(s.get("edt"), 2),
                     "c80_s": fnum(s.get("c80"), 1), "drr_s": fnum(s.get("drr"), 1),
                     "licence_label": spdx_label(s["licence"]) if s.get("licence") else None,
                     "lic_reg": reg_lic.get(s.get("room_key") or ""),
                     "category_label": cat_label(s.get("category")),
                     "sub":"; ".join(x for x in (s.get("dims_m") and f"{s['dims_m']} m", s.get("description"),
                                                  s.get("materials"), s.get("note")) if x)})
    labels = Counter(r.get("room_label") for r in rows)
    for r in rows:
        r["key_sub"] = r.get("room_key") if labels[r.get("room_label")] > 1 else None
    if not rows:
        return None
    kinds = {r.get("ir_kind") for r in rows}
    # mixed kinds: a space that is not a room says what it is under its category (unless the category says it)
    for r in rows:
        k = KIND_ONE.get(r.get("ir_kind"), r.get("ir_kind"))
        r["kind_sub"] = (k if len(kinds) > 1 and r.get("ir_kind") not in (None, "room")
                         and k != (r.get("category") or "").replace("_", " ") else None)
    lics = {r.get("licence") for r in rows}
    for r in rows:
        lr = r.pop("lic_reg") or {}
        r["lic_source"], r["lic_note"] = lr.get("source") or None, lr.get("note") or None
        # the registry's licence where it is more than a version number away from the index's per-IR one
        reg_spdx = lr.get("spdx") or ""
        r["lic_reg_label"] = (spdx_label(reg_spdx) if reg_spdx and not re.fullmatch(
            re.escape(r.get("licence") or "") + r"(-\d\.\d)?", reg_spdx) else None)
    has = {k: any(r.get(k) is not None for r in rows) for k in ("n_clean", "t_room", "edt", "c80", "drr")}
    return {"rows": rows,
            "kind": KIND_ONE.get(next(iter(kinds)), next(iter(kinds))) if len(kinds) == 1 else None,
            "show_volume": any(r.get("volume_m3") is not None for r in rows),
            "show_clean": has["n_clean"], "show_t": has["t_room"], "show_edt": has["edt"], "show_c80": has["c80"],
            "show_drr": has["drr"], "any_t_any": any(r["t_any"] for r in rows),
            "any_fallback": any(r["t_fallback"] for r in rows),
            "show_licence": len(lics) > 1, "licence": spdx_label(next(iter(lics))) if len(lics) == 1 and next(iter(lics)) else None,
            "lic_file": f"registry/{dataset_id}/spaces.csv" if reg_lic else None}


def map_section(site, det: dict, root: str) -> dict | None:
    sig = ((det.get("clap") or {}).get("signals")) or {}
    if not any(len(v.get("xy") or []) for v in sig.values()):
        return None
    clap = site.data.get("clap") or {}
    meta = clap.get("meta") or {}
    base = clap.get("baseline") or {}
    out = []
    for s, label in (("ir", "The IR itself"), ("reverb_vector", "Its effect on dry recordings")):
        v = sig.get(s) or {}
        xy = v.get("xy") or []
        if not xy:
            continue
        bg = ((clap.get("signals") or {}).get(s) or {}).get("background") or f"assets/clap_{s}.png"
        score = v.get("score")
        rows = base.get(s) or []
        med = {k: median([r[k] for r in rows if r.get(k) is not None]) if any(r.get(k) is not None for r in rows) else None
               for k in ("realism", "coverage_ir", "coverage_room")}
        out.append({"key": s, "label": label, "n": len(xy),
                    "svg": charts.map_points(xy, root + bg, size=420, r=3.2 if len(xy) < 300 else 2.4,
                                             title=f"CLAP map, {label.lower()}: this dataset's {len(xy):,} IRs over the corpus"),
                    "score": score, "med": med, "n_ds": len(rows)})
    return {"signals": out, "k": meta.get("k"), "cap": meta.get("cap_per_dataset"), "model": meta.get("model"),
            "n_map": meta.get("n")}


METRIC_NAME = {"t30": "T30", "edt": "EDT", "c80": "C80", "d50": "D50", "t20": "T20", "drr": "DRR"}
METRIC_UNIT = {"t30": "s", "t20": "s", "edt": "s", "c80": "dB", "drr": "dB", "d50": ""}
MEASURES = [("median_ratio", "median ratio, rirdb / published", lambda v, u: fnum(v, 2)),
            ("within_10pct", "within ±10\u00a0%", lambda v, u: fmt_pct(v)),
            ("median_diff", "median difference, rirdb − published", lambda v, u: fnum(v, 2) + (f" {u}" if u else "")),
            ("mae", "mean absolute difference", lambda v, u: fnum(v, 2) + (f" {u}" if u else "")),
            ("within_2db", "within ±2 dB", lambda v, u: fmt_pct(v)),
            ("n", "values compared", lambda v, u: fmt_int(v))]


def _point_where(p: dict) -> str:
    return p.get("space") or " ".join(x for x in (f"room {p['room_num']}" if p.get("room_num") else "",
                                                  f"position {p['pos']}" if p.get("pos") is not None else "",
                                                  str(p.get("config") or "")) if x)


def validation_section(det: dict) -> dict | None:
    """Agreement with published values as a metric x band matrix, and scatter plots of the values."""
    v = det.get("validation")
    if not v:
        return None
    cells: dict[tuple[str, str], dict] = {}
    for r in v.get("summary") or []:
        m, b = str(r.get("metric", "")), r.get("band")
        if b is None and "_" in m:
            m, b = m.rsplit("_", 1)
        cells[(m, str(b if b is not None else "bb"))] = r
    order = ["t30", "t20", "edt", "c80", "d50", "drr"]
    metrics = sorted({m for m, _ in cells}, key=lambda m: order.index(m) if m in order else 99)
    bands = sorted({b for _, b in cells}, key=lambda b: int(b) if b.isdigit() else 10**9)
    rows = []
    for m in metrics:
        for key, label, fmt in MEASURES:
            if not any(key in cells.get((m, b), {}) for b in bands):
                continue
            vals = []
            for b in bands:
                c = cells.get((m, b))
                vals.append(fmt(c[key], METRIC_UNIT.get(m, "")) if c and c.get(key) is not None else "–")
            rows.append({"label": f"{METRIC_NAME.get(m, m)}: {label}", "vals": vals, "m": m})
    heads = ["Broadband" if b == "bb" else f"{STRIP_LABELS.get(b, b)} Hz" for b in bands]
    # scatter plots: decay times on log axes with the +-10 % band, DRR on linear axes
    groups: dict[str, list] = {}
    for p in v.get("points") or []:
        m = str(p.get("metric", ""))
        base, b = (m.rsplit("_", 1) if "_" in m else (m, p.get("band")))
        if p.get("band") is not None:
            base, b = m, p["band"]
        if p.get("published") is None or p.get("ours") is None:
            continue
        u = METRIC_UNIT.get(base, "")
        band = "broadband" if str(b) == "bb" else f"{STRIP_LABELS.get(str(b), b)} Hz"
        nd = 2 if u == "s" else 1
        groups.setdefault(base, []).append({"x": p["published"], "y": p["ours"],
                                            "tip": f"{_point_where(p)}, {band}\npublished {fnum(p['published'], nd)} {u}, "
                                                   f"rirdb {fnum(p['ours'], nd)} {u}".rstrip()})
    charts_ = []
    for base in [m for m in ("t30", "edt", "drr") if m in groups][:2]:
        pts, name, u = groups[base], METRIC_NAME[base], METRIC_UNIT[base]
        log = u == "s"
        sargs = dict(x_label=f"Published {name} ({u})", y_label=f"rirdb {name} ({u})", log=log, band10=log,
                     title=f"{name}, rirdb against the published value" + (", log axes" if log else ""))
        charts_.append({"title": f"{name}: rirdb against the published value" + (", per octave band" if base != "drr" else ""),
                        "sub": f"{len(pts):,} values; the line is y = x" + (" and the band ±10\u00a0%" if log else ""),
                        "svg": dual(charts.scatter(pts, w=CW, height=CW - 30, **sargs),
                                    charts.scatter(pts, w=NW, height=NW - 20, **sargs))})
    return {"source": v.get("source"), "unit": v.get("unit"), "n_spaces": v.get("n_spaces"), "heads": heads, "rows": rows,
            "charts": charts_}


def use_section(row: dict, det: dict) -> dict:
    i = row["id"]
    ix = row.get("index") or {}
    q = []
    if row.get("analysed"):
        q.append(("Every analysed IR of this dataset, with the core parameters, to a CSV file",
                  f"""rirdb dev query "dataset_id = '{i}'" --limit 0 --csv {i}.csv \\
    --columns ir_id,room_id,grade,t30_mid,edt_mid,c80_mid,drr_bb,sti"""))
        if ix.get("n_room_records") and row["type"] in ("measured", "hybrid"):
            q.append(("Clean measured room IRs only (one per position, no duplicates, no flagged content), grade A or B",
                      f"""rirdb dev query "dataset_id = '{i}' AND grade IN ('A', 'B')" --view clean_rooms \\
    --columns ir_id,room_id,category,t30_mid,edt_mid,c80_mid,drr_bb --order-by "t30_mid DESC\""""))
        else:
            q.append(("One representation per position, with bandwidth and file length",
                      f"""rirdb dev query "dataset_id = '{i}' AND preferred" \\
    --columns ir_id,room_id,ir_kind,grade,bw_eff_hz,duration_raw_s --limit 0"""))
    q.append(("The registry row: licence terms and what the dataset publishes about its rooms",
              f"""rirdb dev query "dataset_id = '{i}'" --view datasets \\
    --columns dataset_id,name,license_spdx,training_use,redistribute_audio,room_info_where"""))
    if not row.get("analysed") and row["status"] != "broken":
        steps = []
        if row.get("lock_summary"):
            pass  # already in the store: index it
        elif row["source_kind"] != "manual":
            steps.append(f"sbatch jobs/download.sh fetch/run.sh {i}")
        else:
            steps += [f"# after placing the files in $RIRDB_ROOT/raw/_inbox/{i}/",
                      f"sbatch jobs/download.sh fetch/adopt_manual.sh {i}"]
        steps += [f"sbatch jobs/quick_cpu.sh pixi run rirdb dev probe {i}", f"sbatch jobs/quick_cpu.sh pixi run rirdb dev index-v1 {i}",
                  "# a bash script: it submits a SLURM array of analysis jobs and the merge",
                  f"bash jobs/analyze_dataset.sh {i} <n_shards>"]
        q.append(("Fetch, index and analyse it yourself (an indexing adapter may be needed first)"
                  if not row.get("lock_summary") else "Index and analyse it yourself (an indexing adapter may be needed first)",
                  "\n".join(steps)))
    snap = paths.REPO_ROOT / "snapshots" / "metrics_core" / f"{i}.parquet"
    card = paths.REPO_ROOT / "docs" / "datasets" / f"{i}.md"
    return {"queries": q,
            "snapshot": repo_blob(f"snapshots/metrics_core/{i}.parquet") if snap.exists() else None,
            "snapshot_py": ('import pandas as pd\n'
                            f'url = ("{repo_raw("")}"\n       "snapshots/metrics_core/{i}.parquet")\n'
                            "df = pd.read_parquet(url)" if snap.exists() else None),
            "card": repo_blob(f"docs/datasets/{i}.md") if card.exists() else None}


def issues_section(row: dict, det: dict) -> dict | None:
    f = det.get("findings") or []

    def card(x: dict) -> dict:
        return {k: inline_md(x.get(k)) for k in ("subject", "finding", "evidence", "handling")}

    data = [card(x) for x in f if x.get("section") == "data"]
    lic = [card(x) for x in f if x.get("section") == "licence"]
    caveats = [inline_md(c) for c in (row.get("survey") or {}).get("caveats") or []]
    notes = inline_md(row.get("notes")) if row.get("notes") else None
    if not (data or lic or caveats or notes):
        return None
    return {"data": data, "licence": lic, "caveats": caveats, "notes": notes}


def cite_section(row: dict) -> dict:
    sv = row.get("survey") or {}
    ref = doi_url(row["doi"]) if row.get("doi") and not sv.get("doi_is_paper") else row.get("homepage")
    who = sv.get("institution")
    lic = sv.get("licence_label") or spdx_label((row.get("licence") or {}).get("spdx"))
    # no full stop straight after a URL, so a copied URL stays clean
    line = (f"{row['name']}" + (f" ({row['year']})" if row.get("year") else "") + (f". {who}" if who else "")
            + f". Licence: {lic.rstrip('.')}." + (f" Available at {ref}" if ref else ""))
    # a Zenodo dataset published as several records: each record has its own DOI (TUIL: the office routes)
    more = []
    if row.get("source_kind") == "zenodo" and len(row.get("records") or []) > 1 and not sv.get("doi_is_paper"):
        more = [d for d in (f"10.5281/zenodo.{r}" for r in row["records"]) if d != row.get("doi")]
        if more and ref:
            line += "".join(f" and {doi_url(d)}" for d in more)
    paper = row.get("paper") or {}
    if paper.get("title"):
        line += (("; paper: " if ref else " Paper: ") + paper["title"].rstrip(".")
                 + (f", {paper['url']}" if paper.get("url") else ""))
    # what the authors ask to be cited, where the registry's licence note records it (SMARD)
    note = (row.get("licence") or {}).get("note") or ""
    asks = inline_md(note) if re.search(r"\bcite\b", note, re.I) else None
    return {"line": line, "paper": paper or None, "doi": row.get("doi"), "doi_is_paper": bool(sv.get("doi_is_paper")),
            "more_dois": more, "asks": asks, "homepage": row.get("homepage")}


# ----------------------------------------------------------------------------- JSON-LD

ENC = {"zip": "application/zip", "gz": "application/gzip", "tgz": "application/gzip", "tar": "application/x-tar",
       "wav": "audio/wav", "flac": "audio/flac", "pdf": "application/pdf", "mat": "application/x-matlab-data",
       "h5": "application/x-hdf5", "hdf5": "application/x-hdf5", "7z": "application/x-7z-compressed",
       "sofa": "application/x-netcdf", "json": "application/json", "csv": "text/csv", "txt": "text/plain"}


def jsonld(row: dict, det: dict) -> dict:
    sv, lic = row.get("survey") or {}, row.get("licence") or {}
    spdx = lic.get("spdx")
    licence = (f"https://spdx.org/licenses/{spdx}.html" if spdx and SPDX_RE.match(spdx) else lic.get("url"))
    if sv.get("licence_class") == "conflict":
        # as the page: follow the stricter licence; none where it is not a licence with an SPDX page
        strict = strict_cc(sv.get("licence_label"))
        licence = f"https://spdx.org/licenses/{strict}.html" if strict else None
    kw =["room impulse response", "RIR", "reverberation", "room acoustics", TYPE_LABEL.get(row["type"], row["type"])]
    kw += [FORMAT_LABELS.get(f, f) for f in row.get("capture_formats") or []]
    kw += [KIND_LABELS.get(k, k) for k in row.get("ir_kinds") or []]
    tech = {"measured": "Room impulse response measurement", "simulated": "Room acoustic simulation",
            "production": "Impulse responses for audio production", "hybrid": "Measurement and simulation"}
    o: dict = {"@context": "https://schema.org", "@type": "Dataset", "name": row["name"],
               "description": row.get("content") or row["name"], "url": f"{SITE_URL}d/{row['id']}.html",
               "keywords": list(dict.fromkeys(kw)),
               "measurementTechnique": [tech.get(row["type"], "Impulse response")] + [FORMAT_LABELS.get(f, f) for f in row.get("capture_formats") or []],
               "isAccessibleForFree": True,
               "includedInDataCatalog": {"@type": "DataCatalog", "name": "rirdb", "url": f"{SITE_URL}datasets/index.html"}}
    if row.get("homepage"):
        o["sameAs"] = row["homepage"]
    if row.get("doi"):
        if sv.get("doi_is_paper"):
            o["citation"] = doi_url(row["doi"])
        else:
            o["identifier"] = doi_url(row["doi"])
    if licence:
        o["license"] = licence
    if row.get("year"):
        o["datePublished"] = str(row["year"])
    if sv.get("institution"):
        o["creator"] = {"@type": "Organization", "name": sv["institution"]}
    # spatialCoverage only from where the recordings were made: survey "country" gives the institutions' countries
    if sv.get("recorded_in"):
        o["spatialCoverage"] = {"@type": "Place", "name": sv["recorded_in"]}
    if row.get("paper") and "citation" not in o:
        o["citation"] = row["paper"].get("url")
    if row.get("analysed"):
        o["variableMeasured"] = [
            {"@type": "PropertyValue", "name": n, "unitText": u, "description": d} for n, u, d in (
                ("T30", "s", "Reverberation time from the -5 to -35 dB decay range (ISO 3382)"),
                ("EDT", "s", "Early decay time (ISO 3382)"), ("C80", "dB", "Clarity for music (ISO 3382)"),
                ("DRR", "dB", "Direct-to-reverberant ratio"), ("STI", "", "Speech transmission index (IEC 60268-16)"))]
    dist = []
    for r in det.get("lock") or []:
        url = r.get("url") or ""
        if not url.startswith("http"):
            continue
        dd = {"@type": "DataDownload", "contentUrl": url,
              "name": "Web folder (mirrored file by file)" if r.get("relpath") == "@mirror" else r.get("relpath")}
        name = (r.get("relpath") or "").lower()
        ext = name.rsplit(".", 1)[-1] if "." in name else ""
        if ext in ENC:
            dd["encodingFormat"] = ENC[ext]
        if r.get("size"):
            dd["contentSize"] = fmt_bytes(r["size"])
        dist.append(dd)
        if len(dist) == 20:
            break
    if dist:
        o["distribution"] = dist
    return o


# ----------------------------------------------------------------------------- render

def load_registry_map() -> dict:
    try:
        from rirdb.registry import load_registry
        return {d.id: d for d in load_registry(catalogued=False)}
    except Exception as e:  # noqa: BLE001 - pages still render without the registry extras
        print(f"[site] datasets: registry not loaded ({e}); instructions and source URLs left out")
        return {}


def page_context(site, row: dict, reg) -> dict:
    i = row["id"]
    det = site.dataset(i)
    root = "../"
    ctx = {
        "ds": row, "det": det, "type_label": TYPE_LABEL.get(row["type"], row["type"]),
        "head": header(row, det), "get": get_section(row, det, reg), "lic": licence_section(site, row, det),
        "rooms": rooms_section(row), "contents": contents_section(row, det, reg),
        "an": analysis_section(site, row, det, root), "no_analysis": None if row.get("analysed") else no_analysis_reason(row),
        "spaces": spaces_section(det, i), "map": map_section(site, det, root), "val": validation_section(det),
        "issues": issues_section(row, det), "use": use_section(row, det),
        "probe": det.get("probe"), "cite": cite_section(row),
        "has_lock_file": paths.downloads_tsv(i).exists() or bool(det.get("lock")),
        "has_manifest_file": paths.files_tsv(i).exists() or bool(row.get("manifest")),
    }
    present = {"get": True, "licence": True, "rooms": True, "contents": True, "analysis": True,
               "spaces": bool(ctx["spaces"]), "map": bool(ctx["map"]), "validation": bool(ctx["val"]),
               "issues": bool(ctx["issues"]), "use": True, "probe": bool(ctx["probe"]), "cite": True}
    ctx["toc"] = [(k, label) for k, label in TOC if present[k]]
    return ctx


def describe(row: dict) -> str:
    s = row.get("content") or row["name"]
    if len(s) > 200:
        s = s[:197].rsplit(" ", 1)[0] + " ..."
    return s


def render(site) -> None:
    reg = load_registry_map()
    for row in site.datasets:
        ctx = page_context(site, row, reg.get(row["id"]))
        site.page(f"d/{row['id']}.html", "dataset.html", title=row["name"], description=describe(row), nav="datasets",
                  jsonld=jsonld(row, ctx["det"]), **ctx)
    print(f"[site] datasets: {len(site.datasets)} pages")
