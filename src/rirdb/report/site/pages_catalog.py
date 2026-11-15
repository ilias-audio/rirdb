"""Catalogue section of the rirdb website.

    datasets/index.html      one table of every dataset: the registry rows and the survey-only rows
    d/<survey id>.html       one page per dataset the survey found on the web (not in the registry)
    spaces/index.html        every indexed space, with medians of its clean IRs
    data/catalog.csv/.json   the catalogue table as plain data (columns mirror reports/guide/catalog.csv)
    data/spaces.csv          the spaces table as plain data

The plain-language reading of each dataset (capture families, availability, licence class and the three
permissions) follows the Field Guide's catalogue logic (src/rirdb/report/guide_template.html: FAM, AVAIL,
refine(), fromRegistry(), fromNew(), decayOf()), so the site and the guide agree row for row.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from collections import Counter

from markupsafe import Markup

from rirdb.report.site import charts
from rirdb.report.site.charts import esc, log_ticks, svg_open, tick_label
from rirdb.report.site.core import SITE_NAME, SITE_URL, Site, doi_url

# ----------------------------------------------------------------------------- vocabulary (as in the guide)

FAM = {"mono_omni": "mono", "stereo": "stereo", "binaural": "binaural", "foa_fuma": "ambisonic", "foa_ambix": "ambisonic",
       "hoa_sh": "ambisonic", "em32_raw": "ambisonic", "sma_raw": "ambisonic", "tetra_raw": "ambisonic",
       "array_raw": "array", "sdm_array": "array"}
FAM_ORDER = ["mono", "stereo", "binaural", "ambisonic", "array"]
FAM_SHORT = {"mono": "Mono", "stereo": "Stereo", "binaural": "Binaural", "ambisonic": "Ambisonic", "array": "Array"}
FAM_LABEL = {"mono": "Mono", "stereo": "Stereo", "binaural": "Binaural", "ambisonic": "Ambisonic / spherical",
             "array": "Microphone array"}
# words added to a row's search text, so a search for "binaural" or "ambisonic" finds every row of that family
# (the home page's "start from what you need" links search for these words)
FAM_SEARCH = {"mono": "mono omni", "stereo": "stereo", "binaural": "binaural brir",
              "ambisonic": "ambisonic ambisonics spherical array", "array": "microphone array"}
TYPE_LABEL = {"measured": "Measured", "simulated": "Simulated", "production": "Production library",
              "hybrid": "Measured + simulated"}

# licence classes: key, label, colour token, what it means (the guide's CLASSES)
CLASSES = [
    ("open", "Open", "--series-1",
     "Use for anything, including products and model training. Credit the authors."),
    ("share_alike", "Share-alike", "--series-2",
     "Like open, but if you share the IRs, or versions you have changed, they must carry the same licence. "
     "Whether a track or a model made with the IRs counts as an adaptation is not settled."),
    ("non_commercial", "Non-commercial", "--series-3",
     "Research, teaching, personal and non-profit use only. No products, paid services or commercially used models."),
    ("conflict", "Conflicting statements", "--series-4",
     "The record, the files or the paper give different licences. Follow the stricter one until the authors confirm."),
    ("custom", "Custom terms", "--series-5",
     "The publisher's own conditions. Read them: some allow music-making but forbid redistribution or AI training."),
    ("mixed", "Per space", "--series-6",
     "A different licence for each room, chosen by each contributor."),
    ("unspecified", "Not stated", "--other",
     "No licence anywhere. By default all rights are reserved, so ask the authors first."),
]
CLASS = {k: {"key": k, "label": l, "color": c, "text": t} for k, l, c, t in CLASSES}

SPDX_LABEL = {
    "LicenseRef-unspecified": "No licence stated", "LicenseRef-per-space": "Per space (mostly CC BY-SA)",
    "LicenseRef-EchoThief": "EchoThief licence", "LicenseRef-Voxengo-IR": "Voxengo terms",
    "LicenseRef-TAU-NonCommercial": "Non-commercial (custom)", "LicenseRef-Treble-NonCommercial": "Non-commercial (custom)",
    "LicenseRef-S3A-academic": "Academic use (custom)", "LicenseRef-GWA": "GWA terms",
    "LicenseRef-CC-PublicDomain": "Public domain", "GPL-3.0-only": "GPL 3.0", "ODC-By-1.0": "ODC-By 1.0",
    "Apache-2.0": "Apache 2.0", "MIT": "MIT", "CC0-1.0": "CC0",
}
HOST_LABEL = {"zenodo": "Zenodo", "depositonce": "DepositOnce", "figshare": "Figshare", "github": "GitHub",
              "hf": "Hugging Face", "huggingface": "Hugging Face", "http": "Website", "wget_mirror": "Website",
              "wayback": "Wayback Machine", "manual": "Website", "openslr": "OpenSLR"}

# permission values after refine(): (badge class, full sentence)
PERM_TEXT = {
    "commercial": {"yes": ("yes", "Commercial use allowed"), "no": ("no", "No commercial use"),
                   "no_until": ("no", "No commercial use until the authors confirm"),
                   "unclear": ("unk", "Commercial use not stated"), "ask": ("unk", "Commercial use: ask the authors")},
    "training": {"allowed": ("yes", "ML training allowed"),
                 "same_licence": ("part", "ML training allowed; a shared model may need the same licence"),
                 "non_commercial_only": ("part", "ML training for non-commercial use only"),
                 "prohibited": ("no", "No ML training"), "unclear": ("unk", "ML training not stated"),
                 "ask": ("unk", "ML training: ask the authors")},
    "redistribute": {"yes": ("yes", "You may share the audio"), "same_licence": ("part", "Share under the same licence"),
                     "nc": ("part", "Share non-commercially"), "nc_same": ("part", "Share non-commercially, same licence"),
                     "ask": ("unk", "Sharing: ask the authors"), "unaltered_only": ("part", "Share unaltered files only"),
                     "no": ("no", "Do not share the audio"), "unknown": ("unk", "Sharing not stated"),
                     "unclear": ("unk", "Sharing not stated")},
}
PERM_SHORT = {"commercial": "Products", "training": "ML training", "redistribute": "Sharing"}
PERM_SCORE = {"yes": 2, "part": 1, "unk": 0, "no": -1}

# availability: value -> (group, label, explanation)
AVAIL = {
    "open": ("open", "Open download", "Anyone can download the files, without an account."),
    "free_registration": ("gated", "Free registration",
                          "The files are free, but you must register, log in or fill in a form before downloading."),
    "registration": ("gated", "Free registration",
                     "The files are free, but you must register, log in or fill in a form before downloading."),
    "on_request": ("gated", "On request", "You have to ask the authors for the files."),
    "manual": ("gated", "Manual download",
               "The files are public, but they have to be downloaded by hand: no stable direct link a script can use."),
    "archived": ("gone", "Archive only",
                 "The original site is gone; a copy survives in a web archive (Internet Archive)."),
    "broken": ("gone", "Broken download", "The published download links did not work when checked."),
    "offline": ("gone", "Offline", "The host did not serve the files when checked, and no archived copy of the files was found."),
}
AVAIL_GROUPS = [("open", "Open download"), ("gated", "Registration or manual download"), ("gone", "Broken or archive only")]
AVAIL_BADGE = {"open": "yes", "gated": "part", "gone": "no"}
AVAIL_SHORT = {"open": "Open", "free_registration": "Registration", "registration": "Registration", "on_request": "On request",
               "manual": "Manual", "archived": "Archive only", "broken": "Broken", "offline": "Offline"}

STATUS = {"analysed": "Analysed by rirdb", "registry": "In registry, not analysed", "survey": "Surveyed only"}

INFO_FIELDS = [("dimensions", "Dimensions"), ("volume", "Volume"), ("geometry", "3-D model"), ("materials", "Materials"),
               ("positions", "Source and receiver positions"), ("images", "Photos or plans"),
               ("reference_acoustics", "Reference acoustics"), ("conditions", "Changing conditions")]
INFO_TEXT = {"yes": ("yes", "Published"), "partial": ("partial", "In part"), "no": ("no", "Not published"),
             None: ("unknown", "Not checked")}

# the web check (the web_check block of registry/<id>/dataset.yaml) rates each record high, medium or low but does not define the levels,
# so the text only restates the rating
CONFIDENCE_TEXT = {
    "high": "The survey rated this record well supported by the evidence below.",
    "medium": "The survey rated this record partly supported: some facts could not be confirmed from the evidence below.",
    "low": "The survey rated this record weakly supported: treat the facts as leads and check them at the source.",
}

# space categories (rooms.py) in plain words
CATEGORY_LABEL = {
    "outdoor": "Outdoors", "office_meeting": "Offices and meeting rooms", "lecture_classroom": "Classrooms, lecture halls",
    "unknown": "Not labelled", "hospitality_retail": "Shops, cafés and bars", "domestic": "Homes and hotel rooms",
    "virtual": "Modelled, not a real space", "circulation": "Corridors and foyers",
    "tunnel_underpass": "Tunnels and underpasses", "public_interior": "Museums and public halls",
    "stairwell": "Stairwells", "worship": "Places of worship", "underground": "Caves, cellars and crypts",
    "studio_booth": "Studios, listening rooms", "sports_leisure": "Sports and leisure", "laboratory": "Acoustics laboratories",
    "theatre_auditorium": "Theatres and auditoria", "industrial": "Factories and warehouses",
    "concert_hall": "Concert halls", "large_structure": "Towers and castles", "vehicle": "Vehicles",
    "transport_hub": "Stations and terminals", "device": "Devices",
    "anechoic": "Anechoic chambers", "scale_model": "Scale models",
}
KIND_ONE = {"room": "Room", "outdoor": "Outdoor", "vehicle": "Vehicle", "virtual": "Modelled", "anechoic": "Anechoic",
            "device": "Device", "scale_model": "Scale model"}
NOT_SPACE = ("anechoic", "virtual", "device", "scale_model")


# ----------------------------------------------------------------------------- helpers

def spdx_label(i: str | None) -> str:
    if not i:
        return "Not stated"
    if i in SPDX_LABEL:
        return SPDX_LABEL[i]
    if i.startswith("CC-"):
        body = i[3:]
        parts = body.rsplit("-", 1)
        if len(parts) == 2 and len(parts[1]) == 3 and parts[1][1] == "." and parts[1][0].isdigit():
            return f"CC {parts[0]} {parts[1]}"
        return f"CC {body}"
    return i.removeprefix("LicenseRef-").replace("-", " ")


def refine(lic: dict) -> dict:
    """The guide's plain reading: conditional sharing and training for share-alike and non-commercial terms,
    the stricter reading where statements conflict, no permission at all where no licence is stated."""
    sa = re.search(r"SA\b|GPL", f"{lic.get('spdx') or ''} {lic.get('label') or ''}") is not None
    cls = lic["cls"]
    if cls == "unspecified":
        lic.update(commercial="ask", training="ask", redistribute="ask")
        return lic
    if cls == "conflict" and lic["commercial"] == "unclear":
        lic["commercial"] = "no_until"
    # a conflict whose stricter side is share-alike or GPL reads as share-alike: no unconditional training or sharing
    copyleft = cls == "share_alike" or (cls == "conflict" and sa)
    if copyleft and lic["training"] == "allowed":
        lic["training"] = "same_licence"
    if lic["redistribute"] == "yes":
        if copyleft and (cls == "share_alike" or lic["commercial"] == "yes"):
            lic["redistribute"] = "same_licence"
        elif cls == "non_commercial" or (cls == "conflict" and lic["commercial"] != "yes"):
            lic["redistribute"] = "nc_same" if sa else "nc"
    return lic


def perms_of(lic: dict) -> list[dict]:
    out = []
    for kind in ("commercial", "training", "redistribute"):
        v = lic.get(kind) or "unclear"
        cls, full = PERM_TEXT[kind].get(v) or PERM_TEXT[kind]["unclear"]
        out.append({"kind": kind, "value": v, "cls": cls, "short": PERM_SHORT[kind], "full": full})
    return out


def ml_group(training: str) -> str:
    """Comma list for the ML-training filter."""
    if training == "allowed":
        return "yes,any"
    if training in ("same_licence", "non_commercial_only"):
        return "cond,any"
    if training == "prohibited":
        return "no"
    return "unk"


def fs_text(fs: list) -> str:
    if not fs:
        return ""
    return " / ".join(f"{f / 1000:g}" for f in fs) + "\u00a0kHz"    # no break between the number and the unit


def decay_of(m: dict | None) -> dict | None:
    """T30 at mid frequencies where at least half the room IRs allow it; otherwise T30-or-T20 (guide's decayOf)."""
    if not m or not m.get("n_analysed"):
        return None
    share = m.get("valid_t30_mid_rooms")
    if share is None:
        share = m.get("valid_t30_mid")
    if m.get("t30_p50") is not None and share is not None and share >= 0.5:
        return {"kind": "T30", "p10": m.get("t30_p10"), "p50": m["t30_p50"], "p90": m.get("t30_p90"), "share": share}
    if m.get("t30_or_t20_p50") is not None:
        return {"kind": "T20", "p10": m.get("t30_or_t20_p10"), "p50": m["t30_or_t20_p50"], "p90": m.get("t30_or_t20_p90"),
                "share": share}
    return None


def secs(v) -> str:
    return "–" if v is None else f"{v:.2f} s"


# kinds of IR that are not of a real space (data.NOT_SPACE_KINDS), as a word before "IRs"
NOT_SPACE_WORD = {"virtual": "modelled", "anechoic": "anechoic", "device": "device", "scale_model": "scale-model"}


def not_real_kind(spaces: list[dict], index_kinds) -> str | None:
    """'modelled' for a dataset whose spaces in the site's space table (kinds corrected as in the Field Guide)
    are all of a kind that is not a real space while the index still files its IRs as real spaces, as Voxengo's:
    its median T30 is then over those IRs, so say so instead of "IRs of real spaces"."""
    kinds = {s.get("ir_kind") for s in spaces}
    if kinds and kinds <= set(NOT_SPACE_WORD) and set(index_kinds or ()) - set(NOT_SPACE_WORD):
        return " and ".join(NOT_SPACE_WORD[k] for k in sorted(kinds))
    return None


def t30_cell(t: dict | None, over: str | None = None) -> Markup:
    if not t:
        return Markup("")
    if t["kind"] == "T30":
        what = "T30 at 500 Hz and 1 kHz"
    else:
        what = (f"T30 where measurable, otherwise T20 (T30 measurable in {t['share'] * 100:.0f} % of room IRs)"
                if t.get("share") is not None else "T30 where measurable, otherwise T20")
    whose = f"over its {over} IRs (it has no IRs of real spaces)" if over else "IRs of real spaces"
    tip = (f"{what}, {whose}: 10th percentile {secs(t['p10'])}, median {secs(t['p50'])}, "
           f"90th percentile {secs(t['p90'])}")
    return charts.range_mini(t["p10"], t["p50"], t["p90"], tip=tip, w=84)


def day_text(iso: str | None) -> str | None:
    """2026-10-14 -> 14 October 2026."""
    if not iso:
        return None
    try:
        from datetime import date
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    return f"{d.day} {d:%B %Y}"


def text_blob(*parts) -> str:
    return " ".join(str(p) for p in parts if p).lower()


def fam_words(fams) -> str:
    return " ".join(FAM_SEARCH[f] for f in FAM_ORDER if f in fams)


def r4(v):
    if v is None:
        return None
    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        return round(v, 4)
    return v


# ----------------------------------------------------------------------------- catalogue rows

def from_registry(r: dict, space_labels: dict[str, list[str]], over: dict[str, str] | None = None) -> dict:
    ix, m, sv = r.get("index") or {}, r.get("metrics") or {}, r.get("survey") or {}
    lic = r.get("licence") or {}
    # the registry's status "manual" means a script could not fetch the files, and its access field says why
    # (registration, or a host that refuses scripts); access "open" there only means rirdb has not scripted it yet
    avail = "open"
    if r["status"] == "broken":
        avail = "broken"
    elif r["status"] == "manual" and r.get("access") in ("registration", "manual"):
        avail = r["access"]
    elif r.get("source_kind") == "wayback":
        avail = "archived"
    # the survey's web check may refine that reading, but not turn a registry manual, registration or broken
    # reading back into an open download (it records what a browser sees, not what a script can fetch)
    if sv.get("availability") and (avail == "open" or AVAIL.get(sv["availability"], AVAIL["open"])[0] != "open"):
        avail = sv["availability"]
    fs = sorted(int(k) for k in ix["fs_found"]) if ix.get("fs_found") else sorted(r.get("fs_hz") or [])
    analysed = bool(m.get("n_analysed"))
    n_irs = sv.get("n_irs_display")
    n_irs_src = "published"
    if n_irs is None and ix.get("n_records") is not None:
        n_irs, n_irs_src = ix["n_records"], "indexed"
    if n_irs is None:  # the guide's order: survey overlay, then the registry's expected count
        n_irs = sv["n_irs"] if sv.get("n_irs") is not None else r.get("expected_irs")
    n_sp = sv.get("n_rooms_display")
    if n_sp is None:
        n_sp = ix.get("n_spaces")
    if n_sp is None:
        n_sp = sv["n_rooms"] if sv.get("n_rooms") is not None else r.get("expected_rooms")
    licence = refine({"spdx": lic.get("spdx"), "label": sv.get("licence_label") or spdx_label(lic.get("spdx")),
                      "cls": sv.get("licence_class") or lic.get("class") or "unspecified",
                      "commercial": sv.get("commercial") or lic.get("commercial"),
                      "training": sv.get("training") or lic.get("training"),
                      "redistribute": sv.get("redistribute") or lic.get("redistribute"),
                      "url": lic.get("url"), "note": sv.get("licence_note") or lic.get("note")})
    fams = {FAM[x] for x in r.get("capture_formats") or [] if x in FAM}
    status = "analysed" if analysed else "registry"
    content = sv.get("content") or r.get("content")
    return {
        "id": r["id"], "name": sv.get("name") or r["name"], "year": r.get("year"), "type": r.get("type"),
        "in_registry": True, "analysed": analysed, "registry_status": r["status"], "rirdb_status": status,
        "availability": avail, "capture_formats": list(r.get("capture_formats") or []),
        "fams": [x for x in FAM_ORDER if x in fams], "fs": fs,
        "n_irs": n_irs, "n_irs_source": n_irs_src if n_irs is not None else None, "n_spaces": n_sp,
        "count_override": sv.get("n_irs_display") is not None and ix.get("n_records") is not None,
        "n_irs_indexed": ix.get("n_records"), "n_irs_analysed": m.get("n_analysed"),
        "licence": licence, "host": sv.get("host") or HOST_LABEL.get(r.get("source_kind"), "Website"),
        "homepage": sv.get("homepage") or r.get("homepage"),
        "doi": None if sv.get("doi_is_paper") else r.get("doi"),
        "paper_url": (r.get("paper") or {}).get("url"),
        "institution": sv.get("institution"), "country": sv.get("country"), "content": content,
        "t30": decay_of(m) if analysed else None, "t30_over": (over or {}).get(r["id"]) if analysed else None,
        "subsampled": r.get("analysis_scope") == "subsample_v1",
        "_search": text_blob(r["id"], r["name"], content, sv.get("institution"), sv.get("country"), sv.get("host"),
                             licence["label"], fam_words(fams), " ".join(space_labels.get(r["id"], []))),
    }


def from_survey(n: dict) -> dict:
    licence = refine({"spdx": n.get("licence_spdx"), "label": n.get("licence_label") or "Not stated",
                      "cls": n.get("licence_class") or "unspecified", "commercial": n.get("commercial_use") or "unclear",
                      "training": n.get("ml_training") or "unclear", "redistribute": n.get("redistribution") or "unclear",
                      "url": None, "note": n.get("licence_note")})
    fams = set(n.get("fams") or [])
    return {
        "id": n["id"], "name": n["name"], "year": n.get("year"), "type": n.get("type") or "measured",
        "in_registry": False, "analysed": False, "registry_status": None, "rirdb_status": "survey",
        "availability": n.get("availability") or "open", "capture_formats": [],
        "fams": [x for x in FAM_ORDER if x in fams], "fs": sorted(n.get("fs_hz") or []),
        "n_irs": n.get("n_irs"), "n_irs_source": "published" if n.get("n_irs") is not None else None, "count_override": False,
        "n_spaces": n.get("n_rooms"), "n_irs_indexed": None, "n_irs_analysed": None,
        "licence": licence, "host": n.get("host") or "Website", "homepage": n.get("landing_url"),
        "doi": n.get("doi"), "paper_url": None,
        "institution": n.get("institution"), "country": n.get("country"), "content": n.get("description"),
        "t30": None, "t30_over": None, "subsampled": False,
        "_search": text_blob(n["id"], n["name"], n.get("found_as"), n.get("description"), n.get("institution"),
                             n.get("country"), n.get("host"), licence["label"], fam_words(fams)),
    }


def decorate(d: dict) -> dict:
    """Display fields shared by the table, the data files and the survey pages."""
    lic = d["licence"]
    grp, label, expl = AVAIL.get(d["availability"], AVAIL["open"])
    d["avail_group"], d["avail_label"], d["avail_text"] = grp, label, expl
    d["avail_badge"] = AVAIL_BADGE[grp]
    d["avail_short"] = AVAIL_SHORT.get(d["availability"], label)
    d["type_label"] = TYPE_LABEL.get(d["type"], d["type"] or "")
    d["fam_labels"] = [FAM_LABEL[x] for x in d["fams"]]
    d["fam_short"] = [FAM_SHORT[x] for x in d["fams"]]
    d["fs_text"] = fs_text(d["fs"])
    d["cls"] = CLASS.get(lic["cls"], CLASS["unspecified"])
    d["perms"] = perms_of(lic)
    d["perm_score"] = sum(PERM_SCORE[p["cls"]] for p in d["perms"])
    d["status_label"] = STATUS[d["rirdb_status"]]
    d["url"] = f"d/{d['id']}.html"
    d["origin"] = " · ".join(x for x in (d["institution"], d["country"]) if x and x != "Unknown")
    d["t30_html"] = t30_cell(d["t30"], d.get("t30_over"))
    # filter keys (comma lists, see site.js)
    d["f_type"] = "hybrid,measured,simulated" if d["type"] == "hybrid" else (d["type"] or "")
    d["f_st"] = {"analysed": "analysed,reg", "registry": "regonly,reg", "survey": "survey"}[d["rirdb_status"]]
    d["f_ml"] = ml_group(lic["training"])
    d.setdefault("twin", None)  # survey rows: the registry dataset the record also describes (catalogue())
    return d


def norm_url(u: str | None) -> str:
    u = re.sub(r"^https?://(www\.)?", "", (u or "").strip().lower())
    return u.split("?", 1)[0].rstrip("/")


def registry_twins(registry: list[dict], survey: list[dict]) -> dict[str, str]:
    """Survey id -> registry id, for survey records that describe a registry dataset (or part of one): the same
    DOI, or the registry's paper among the survey's evidence. The survey ran before these joined the registry."""
    out = {}
    for n in survey:
        doi = (n.get("doi") or "").lower()
        ev = {norm_url(u) for u in n.get("evidence_urls") or []}
        for r in registry:
            paper = norm_url((r.get("paper") or {}).get("url"))
            if (doi and doi == (r.get("doi") or "").lower()) or (paper and paper in ev):
                out[n["id"]] = r["id"]
                break
    return out


ZENODO_ID = re.compile(r"(?:zenodo\.org/(?:records?|deposit)/|10\.5281/zenodo\.)(\d+)", re.I)


def same_record_twins(registry: list[dict], survey: list[dict]) -> dict[str, str]:
    """Survey id -> registry id where the survey record is a registry dataset itself: the same DOI, the same
    Zenodo record (the survey's landing page or DOI against the registry's records, DOI or homepage), or the
    same landing page as the registry's homepage."""
    def ndoi(d) -> str:
        return re.sub(r"^https?://(dx\.)?doi\.org/", "", (d or "").strip().lower())

    def zids(*vals) -> set[str]:
        return {m.group(1) for v in vals if v for m in [ZENODO_ID.search(v)] if m}

    out = {}
    for n in survey:
        doi, land, lz = ndoi(n.get("doi")), norm_url(n.get("landing_url")), zids(n.get("landing_url"), n.get("doi"))
        for r in registry:
            rz = {x.split("@")[0] for x in r.get("records") or []} if r.get("source_kind") == "zenodo" else set()
            rz |= zids(r.get("doi"), r.get("homepage"))
            if (doi and doi == ndoi(r.get("doi"))) or (lz & rz) or (land and land == norm_url(r.get("homepage"))):
                out[n["id"]] = r["id"]
                break
    return out


def drop_survey_twins(data: dict) -> list[str]:
    """Leave out, with a warning, survey records that are registry datasets (same DOI or landing record), so no
    dataset is counted twice. Called once on the loaded data (build) and again by catalogue() as a guard."""
    tw = same_record_twins(data.get("datasets") or [], data.get("survey") or [])
    for sid, rid in tw.items():
        print(f"[site] WARNING survey record {sid} has the DOI or landing record of registry dataset {rid}: "
              "left out of the site; remove its folder registry/<id>/")
    if tw:
        data["survey"] = [n for n in data.get("survey") or [] if n["id"] not in tw]
    return list(tw)


def catalogue(site: Site) -> list[dict]:
    drop_survey_twins(site.data)
    space_labels: dict[str, list[str]] = {}
    for s in site.data.get("spaces") or []:
        lab = s.get("room_label") or s.get("room_key")
        if lab:
            space_labels.setdefault(s["dataset_id"], []).append(str(lab))
    by_ds: dict[str, list[dict]] = {}
    for s in site.data.get("spaces") or []:
        by_ds.setdefault(s["dataset_id"], []).append(s)
    kinds_ix = {r["id"]: (r.get("index") or {}).get("kinds_found") for r in site.datasets}
    over = {k: w for k, v in by_ds.items() if (w := not_real_kind(v, kinds_ix.get(k)))}
    rows = [decorate(from_registry(r, space_labels, over)) for r in site.datasets]
    by_id = {d["id"]: d for d in rows}
    twins = registry_twins(site.datasets, site.data.get("survey") or [])
    for n in site.data.get("survey") or []:
        d = decorate(from_survey(n))
        t = by_id.get(twins.get(n["id"]))
        d["twin"] = {"id": t["id"], "name": t["name"], "url": t["url"], "analysed": t["analysed"]} if t else None
        rows.append(d)
    return rows


def survey_cite(n: dict) -> str:
    """The registry pages' citation line (pages_dataset.cite_section), from the survey's facts."""
    ref = doi_url(n.get("doi")) or n.get("landing_url")
    who = n.get("institution") if n.get("institution") not in (None, "Unknown") else None
    lic = (n.get("licence_label") or "not stated").rstrip(".")
    # no full stop straight after a URL, so a copied URL stays clean
    return (n["name"] + (f" ({n['year']})" if n.get("year") else "") + (f". {who}" if who else "")
            + f". Licence: {lic}." + (f" Available at {ref}" if ref else ""))


# ----------------------------------------------------------------------------- data files

CATALOG_COLS = ["id", "name", "year", "type", "in_registry", "analysed", "availability", "capture_formats", "fs_hz",
                "n_irs", "n_spaces", "licence", "licence_class", "commercial_use", "ml_training", "redistribute_audio",
                "host", "homepage", "doi", "institution", "country", "content",
                # added by the site
                "rirdb_status", "registry_status", "capture_families", "availability_group", "licence_spdx",
                "n_irs_source", "n_irs_indexed", "n_irs_analysed", "t30_kind", "t30_p10_s", "t30_p50_s", "t30_p90_s",
                "paper_url", "site_url"]


def catalog_record(d: dict) -> dict:
    lic, t = d["licence"], d["t30"] or {}
    return {
        "id": d["id"], "name": d["name"], "year": d["year"], "type": d["type"],
        "in_registry": d["in_registry"], "analysed": d["analysed"], "availability": d["availability"],
        "capture_formats": d["capture_formats"], "fs_hz": d["fs"], "n_irs": d["n_irs"], "n_spaces": d["n_spaces"],
        "licence": lic["label"], "licence_class": lic["cls"], "commercial_use": lic["commercial"],
        "ml_training": lic["training"], "redistribute_audio": lic["redistribute"], "host": d["host"],
        "homepage": d["homepage"], "doi": d["doi"], "institution": d["institution"], "country": d["country"],
        "content": d["content"], "rirdb_status": d["rirdb_status"], "registry_status": d["registry_status"],
        "capture_families": d["fams"], "availability_group": d["avail_group"], "licence_spdx": lic.get("spdx"),
        "n_irs_source": d["n_irs_source"], "n_irs_indexed": d["n_irs_indexed"], "n_irs_analysed": d["n_irs_analysed"],
        "t30_kind": t.get("kind"), "t30_p10_s": r4(t.get("p10")), "t30_p50_s": r4(t.get("p50")),
        "t30_p90_s": r4(t.get("p90")), "paper_url": d["paper_url"], "site_url": SITE_URL + d["url"],
    }


def to_csv(rows: list[dict], cols: list[str]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        out = []
        for c in cols:
            v = r.get(c)
            if isinstance(v, bool):
                v = "yes" if v else "no"
            elif isinstance(v, list):
                v = "|".join(str(x) for x in v)
            elif v is None:
                v = ""
            out.append(v)
        w.writerow(out)
    return buf.getvalue()


SPACE_COLS = ["room_id", "physical_id", "dataset_id", "dataset_name", "room_label", "room_key", "category",
              "category_label", "category_fixed", "ir_kind", "volume_m3", "dims_m", "n_irs", "n_clean", "n_analysed",
              "t30_s", "t30_source", "t30_mid_s", "t30_or_t20_s", "share_valid_t30", "t30_any_s", "edt_s", "c80_db",
              "drr_db", "licence", "formats", "description", "materials", "bg_noise_dba", "note", "dataset_url"]


def space_record(s: dict) -> dict:
    return {
        "room_id": s["room_id"], "physical_id": s["physical_id"], "dataset_id": s["dataset_id"],
        "dataset_name": s["dataset_name"], "room_label": s.get("room_label"), "room_key": s.get("room_key"),
        "category": s.get("category"), "category_label": s["category_label"], "category_fixed": bool(s.get("category_fixed")),
        "ir_kind": s.get("ir_kind"), "volume_m3": r4(s.get("volume_m3")), "dims_m": s.get("dims_m"),
        "n_irs": s.get("n_irs"), "n_clean": s.get("n_clean"), "n_analysed": s.get("n_analysed"),
        "t30_s": r4(s["t30_shown"]), "t30_source": s["t30_kind"], "t30_mid_s": r4(s.get("t30")),
        "t30_or_t20_s": r4(s.get("t30_or_t20")), "share_valid_t30": r4(s.get("share_valid_t30")),
        "t30_any_s": r4(s.get("t30_any")), "edt_s": r4(s.get("edt")), "c80_db": r4(s.get("c80")), "drr_db": r4(s.get("drr")),
        "licence": s.get("licence"), "formats": s.get("formats"), "description": s.get("description"),
        "materials": s.get("materials"), "bg_noise_dba": s.get("bg_noise_dba"), "note": s.get("note"),
        "dataset_url": SITE_URL + f"d/{s['dataset_id']}.html",
    }


# ----------------------------------------------------------------------------- spaces

def room_t(s: dict) -> tuple[float | None, str | None]:
    """T30 where most of the space's clean IRs allow it, else the T30-or-T20 median (guide's roomT)."""
    if s.get("t30") is not None and (s.get("share_valid_t30") is None or s["share_valid_t30"] >= 0.5):
        return s["t30"], "T30"
    if s.get("t30_or_t20") is not None:
        return s["t30_or_t20"], "T20"
    return None, None


def fix_reason(s: dict) -> str:
    """Why the space's category differs from the naming rules: the dataset's space_type, or a row of
    registry/<id>/spaces.csv (whose note says why, where it says anything)."""
    from rirdb.report import guide

    if s["dataset_id"] in guide.dataset_space_types():
        return "The library's IRs were modelled, not recorded, so its spaces count as modelled."
    note = (guide.curated_spaces_all().get(s["room_id"]) or {}).get("note")
    return f"Category corrected by hand: {note}" if note else "Category corrected by hand."


def spaces_rows(site: Site, names: dict[str, str]) -> list[dict]:
    rows = []
    by_room = {s["room_id"]: s for s in site.data.get("spaces") or []}
    for s in site.data.get("spaces") or []:
        s = dict(s)
        s["dataset_name"] = names.get(s["dataset_id"], s["dataset_id"])
        s["category_label"] = CATEGORY_LABEL.get(s.get("category"), (s.get("category") or "").replace("_", " "))
        s["kind_label"] = KIND_ONE.get(s.get("ir_kind"), s.get("ir_kind") or "")
        s["t30_shown"], s["t30_kind"] = room_t(s)
        s["fix_reason"] = fix_reason(s) if s.get("category_fixed") else None
        s["licence_label"] = spdx_label(s.get("licence")) if s.get("licence") else None
        same = s.get("physical_id") if s.get("physical_id") != s["room_id"] else None
        s["same_as"] = None
        if same:
            o = by_room.get(same)
            s["same_as"] = {"room_id": same, "dataset_id": same.split("/")[0],
                            "label": (o or {}).get("room_label") or same.split("/", 1)[-1],
                            "dataset_name": names.get(same.split("/")[0], same.split("/")[0])}
        s["label"] = s.get("room_label") or s.get("room_key") or s["room_id"]
        s["_search"] = text_blob(s["label"], s.get("room_key"), s["dataset_name"], s["dataset_id"], s["category_label"],
                                 s.get("description"), s.get("materials"), s.get("note"))
        rows.append(s)
    return rows


def scatter_loglog(points: list[dict], *, x_label: str, y_label: str, w: int = 440, height: int = 360,
                   title: str = "") -> Markup:
    """x vs y on two independent log axes (charts.scatter shares one range for both). points: {x, y, tip}."""
    pts = [p for p in points if (p.get("x") or 0) > 0 and (p.get("y") or 0) > 0]
    if not pts:
        return Markup('<p class="muted small">No points.</p>')
    xlo, xhi = min(p["x"] for p in pts) / 1.4, max(p["x"] for p in pts) * 1.4
    ylo, yhi = min(p["y"] for p in pts) / 1.25, max(p["y"] for p in pts) * 1.25
    ml, mr, mt, mb = 46, 12, 10, 40
    pw, ph = w - ml - mr, height - mt - mb
    X = lambda v: ml + (math.log(v) - math.log(xlo)) / (math.log(xhi) - math.log(xlo)) * pw  # noqa: E731
    Y = lambda v: mt + ph - (math.log(v) - math.log(ylo)) / (math.log(yhi) - math.log(ylo)) * ph  # noqa: E731
    xt = log_ticks(xlo, xhi)
    if len(xt) > 9:
        xt = [t for t in xt if abs(math.log10(t) - round(math.log10(t))) < 1e-9]
    yt = log_ticks(ylo, yhi)
    if len(yt) > 8:
        yt = [t for t in yt if abs(math.log10(t) - round(math.log10(t))) < 1e-9]
    out = [svg_open(w, height, title or f"{y_label} against {x_label}")]
    for t in yt:
        out.append(f'<line class="gl" x1="{ml}" x2="{ml + pw}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>')
        out.append(f'<text x="{ml - 6}" y="{Y(t) + 3.5:.1f}" text-anchor="end">{tick_label(t)}</text>')
    for t in xt:
        out.append(f'<line class="gl" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{mt}" y2="{mt + ph}"/>')
        out.append(f'<text x="{X(t):.1f}" y="{mt + ph + 15}" text-anchor="middle">{tick_label(t)}</text>')
    out.append(f'<line class="ax" x1="{ml}" x2="{ml + pw}" y1="{mt + ph}" y2="{mt + ph}"/>')
    out.append(f'<line class="ax" x1="{ml}" x2="{ml}" y1="{mt}" y2="{mt + ph}"/>')
    for p in sorted(pts, key=lambda p: p["x"]):
        out.append(f'<circle class="pt" cx="{X(p["x"]):.1f}" cy="{Y(p["y"]):.1f}" r="4.5" tabindex="0" '
                   f'data-tip="{esc(p.get("tip", ""))}"/>')
    out.append(f'<text class="lbl" x="{ml + pw / 2:.1f}" y="{height - 4}" text-anchor="middle">{esc(x_label)}</text>')
    out.append(f'<text class="lbl" transform="translate(11,{mt + ph / 2:.1f}) rotate(-90)" text-anchor="middle">{esc(y_label)}</text>')
    out.append("</svg>")
    return Markup("".join(out))


def dual(wide: Markup, narrow: Markup) -> Markup:
    """A wide and a narrow drawing of one chart; spaces.html shows the narrow one on phones (.vw-wide/.vw-narrow)."""
    return Markup('<div class="vw-wide">') + wide + Markup('</div><div class="vw-narrow">') + narrow + Markup("</div>")


# ----------------------------------------------------------------------------- JSON-LD

def survey_jsonld(n: dict, d: dict) -> list[dict]:
    url = SITE_URL + d["url"]
    ld: dict = {
        "@context": "https://schema.org", "@type": "Dataset", "name": n["name"],
        "description": n.get("description") or n["name"], "url": url,
    }
    if n.get("landing_url"):
        ld["sameAs"] = n["landing_url"]
    if n.get("doi"):
        doi = n["doi"]
        ld["identifier"] = doi if doi.startswith("http") else f"https://doi.org/{doi}"
    spdx, cls = n.get("licence_spdx"), d["licence"]["cls"]
    if spdx and not spdx.startswith("LicenseRef-") and cls != "conflict":
        ld["license"] = f"https://spdx.org/licenses/{spdx}.html"
    elif n.get("licence_label") and cls != "unspecified":  # no licence stated: claim none
        # conflicting statements: name them all (the label does), never one SPDX licence
        ld["license"] = {"@type": "CreativeWork", "name": n["licence_label"]}
    if n.get("institution") and n["institution"] != "Unknown":
        ld["creator"] = {"@type": "Organization", "name": n["institution"]}
    if n.get("year"):
        ld["datePublished"] = str(n["year"])
    if n.get("availability") == "open":
        ld["isAccessibleForFree"] = True
    ld["keywords"] = ["room impulse response", "reverberation"] + [FAM_LABEL[f] for f in d["fams"]]
    ld["includedInDataCatalog"] = {"@type": "DataCatalog", "name": f"{SITE_NAME}: room impulse response datasets",
                                   "url": SITE_URL + "datasets/"}
    crumbs = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Datasets", "item": SITE_URL + "datasets/"},
        {"@type": "ListItem", "position": 2, "name": n["name"], "item": url}]}
    return [ld, crumbs]


# ----------------------------------------------------------------------------- render

def render(site: Site) -> None:
    rows = catalogue(site)
    data = site.data
    names = {d["id"]: d["name"] for d in rows}
    survey_day = day_text(data.get("survey_date"))

    # --- data files
    recs = [catalog_record(d) for d in rows]
    site.write("data/catalog.csv", to_csv(recs, CATALOG_COLS))
    site.write("data/catalog.json", json.dumps(recs, ensure_ascii=False, indent=1, default=str) + "\n")
    sp = spaces_rows(site, names)
    site.write("data/spaces.csv", to_csv([space_record(s) for s in sp], SPACE_COLS))

    # --- catalogue page
    n_open = sum(d["avail_group"] == "open" for d in rows)
    n_ml = sum(d["licence"]["training"] == "allowed" for d in rows)
    n_ml_cond = sum(d["licence"]["training"] in ("same_licence", "non_commercial_only") for d in rows)
    counts = {
        "all": len(rows), "registry": sum(d["in_registry"] for d in rows), "analysed": sum(d["analysed"] for d in rows),
        "survey": sum(not d["in_registry"] for d in rows), "open": n_open, "ml": n_ml, "ml_cond": n_ml_cond,
        "irs_stated": sum(d["n_irs"] is not None for d in rows),
        "locked": sum(bool(r.get("lock_summary")) for r in site.datasets),
    }
    filt = {
        "type": [(k, TYPE_LABEL[k], sum(k in d["f_type"].split(",") for d in rows)) for k in TYPE_LABEL],
        "fam": [(k, FAM_LABEL[k], sum(k in d["fams"] for d in rows)) for k in FAM_ORDER],
        "lic": [(c["key"], c["label"], sum(d["licence"]["cls"] == c["key"] for d in rows)) for c in CLASS.values()],
        # short labels: a chosen option must stay readable in a narrow select
        "ml": [(k, l, sum(k in d["f_ml"].split(",") for d in rows))
               for k, l in (("any", "Allowed, any"), ("yes", "No conditions"), ("cond", "With conditions"),
                            ("no", "Not allowed"), ("unk", "Not stated"))],
        "avail": [(k, l, sum(d["avail_group"] == k for d in rows)) for k, l in AVAIL_GROUPS],
        "st": [(k, l, sum(k in d["f_st"].split(",") for d in rows))
               for k, l in (("analysed", "Analysed"), ("reg", "In registry"), ("regonly", "Registry only"),
                            ("survey", "Surveyed only"))],
    }
    shown_published = [d["name"] for d in rows if d["count_override"]]
    twins = [d["name"] for d in rows if d["twin"]]
    t30_over = [(d["name"], d["t30_over"]) for d in rows if d["t30"] and d["t30_over"]]
    order = sorted(rows, key=lambda d: (-(d["year"] or 0), d["name"].lower()))
    site.page("datasets/index.html", "catalog.html", title="All datasets", nav="datasets",
              description=(f"{counts['all']} public room impulse response datasets in one table: what each holds, "
                           f"its licence, where to get it, and what rirdb's analysis found."),
              rows=order, counts=counts, filt=filt, classes=list(CLASS.values()), shown_published=shown_published,
              twins=twins, t30_over=t30_over, survey_date=survey_day, avail_groups=AVAIL_GROUPS)

    # --- survey pages
    survey_rows = {d["id"]: d for d in rows if not d["in_registry"]}
    for n in data.get("survey") or []:
        d = survey_rows[n["id"]]
        info = []
        for k, label in INFO_FIELDS:
            v = (n.get("room_info") or {}).get(k)
            yn, text = INFO_TEXT.get(v, INFO_TEXT[None])
            info.append({"label": label, "yn": yn, "text": text})
        desc = (n.get("description") or n["name"])
        if len(desc) > 300:
            desc = desc[:297].rsplit(" ", 1)[0] + "…"
        site.page(d["url"], "survey_dataset.html", title=n["name"], nav="datasets", description=desc,
                  jsonld=survey_jsonld(n, d), n=n, d=d, info=info, cite_line=survey_cite(n),
                  info_known=any(i["yn"] != "unknown" for i in info),
                  confidence_text=CONFIDENCE_TEXT.get(n.get("confidence")), survey_day=survey_day)

    # --- spaces page
    cat_counts = Counter(s.get("category") for s in sp)
    cat_items = [{"label": CATEGORY_LABEL.get(c, c), "value": v,
                  "tip": f"{CATEGORY_LABEL.get(c, c)}: {v:,} indexed space{'s' if v != 1 else ''}"}
                 for c, v in sorted(cat_counts.items(), key=lambda kv: (-kv[1], CATEGORY_LABEL.get(kv[0], kv[0])))]
    # drawn twice: the wide chart scaled into a phone column would print its text at under 8 px
    cat_chart = dual(charts.hbars(cat_items, w=440, title="Indexed spaces per category"),
                     charts.hbars(cat_items, w=340, title="Indexed spaces per category"))
    # one point per physical room with a published volume and a reverberation time
    seen, pts, pt_rows = set(), [], []
    for s in sorted(sp, key=lambda s: (s["room_id"] != s["physical_id"], s["room_id"])):
        if s.get("volume_m3") is None or s["t30_shown"] is None or s["physical_id"] in seen:
            continue
        seen.add(s["physical_id"])
        mark = "" if s["t30_kind"] == "T30" else " (T20)"
        pts.append({"x": s["volume_m3"], "y": s["t30_shown"],
                    "tip": f"{s['label']} · {s['dataset_name']}\n{s['volume_m3']:,.0f} m³, T30 {s['t30_shown']:.2f} s{mark}"})
        pt_rows.append(s)
    vol_args = dict(x_label="Volume (m³, log scale)", y_label="Median T30 (s, log scale)",
                    title="Median T30 against published room volume")
    vol_chart = dual(scatter_loglog(pts, **vol_args), scatter_loglog(pts, w=320, height=300, **vol_args))
    n_vol = sum(s.get("volume_m3") is not None for s in sp)
    sp_counts = {
        "n": len(sp), "physical": data["corpus"].get("n_spaces_physical", len({s["physical_id"] for s in sp})),
        "datasets": len({s["dataset_id"] for s in sp}), "volume": n_vol, "t30": sum(s["t30_shown"] is not None for s in sp),
        "t20": sum(s["t30_kind"] == "T20" for s in sp), "fixed": sum(bool(s.get("category_fixed")) for s in sp),
        "same": sum(s["same_as"] is not None for s in sp), "points": len(pts),
        "not_space": sum(s.get("ir_kind") in NOT_SPACE for s in sp),
        "subsampled": sorted(names[r["id"]] for r in site.datasets if r.get("analysis_scope") == "subsample_v1" and r["id"] in {s["dataset_id"] for s in sp}),
    }
    sp_filt = {
        "cat": [(c, CATEGORY_LABEL.get(c, c), v) for c, v in sorted(cat_counts.items(), key=lambda kv: CATEGORY_LABEL.get(kv[0], kv[0]))],
        "kind": [(k, KIND_ONE.get(k, k), v) for k, v in sorted(Counter(s.get("ir_kind") for s in sp).items(), key=lambda kv: -kv[1])],
        "ds": sorted({(s["dataset_id"], s["dataset_name"]) for s in sp}, key=lambda kv: kv[1].lower()),
        "ds_n": Counter(s["dataset_id"] for s in sp),
        "t30": [0.1, 0.2, 0.3, 0.5, 0.8, 1, 1.5, 2, 3, 5],
    }
    n_catalogue = len(site.datasets) + len(data.get("survey") or [])
    site.page("spaces/index.html", "spaces.html", title="Spaces", nav="spaces",
              description=(f"All {sp_counts['n']} spaces indexed by rirdb ({sp_counts['physical']} physical rooms and sites): "
                           f"category, volume, IR counts and median T30, EDT, C80 and DRR of their clean IRs."),
              spaces=sorted(sp, key=lambda s: (s["dataset_name"].lower(), str(s["label"]).lower())),
              cat_chart=cat_chart, cat_items=cat_items, vol_chart=vol_chart, pt_rows=pt_rows, sp=sp_counts, filt=sp_filt, n_catalogue=n_catalogue)
