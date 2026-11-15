"""Docs section of the rirdb website.

    index.html             home: what the site is, a search box, corpus totals, every section, key findings
    methods/index.html     how rirdb works end to end, the analyzer record (docs/decisions/analyzer-v1.md) and a
                           glossary of every parameter, flag, grade, subset and index field
    methods/findings.html  docs/decisions/dataset-findings.md, with every dataset linked to its page
    downloads/index.html   every machine-readable file the site and the repository offer, with sizes and columns
    about/index.html       what rirdb is, who made it, how to cite it and how to contribute
    404.html               not-found page; GitHub Pages serves it at any depth, so its links are absolute

Repository Markdown is rendered with markdown-it-py (raw HTML off). Relative links in it point to the site page
that renders the target, or to the file on GitHub; inline code that names a tracked file links to it too.
"""

from __future__ import annotations

import csv
import hashlib
import json
import posixpath
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token
from markupsafe import Markup, escape

from rirdb import paths
from rirdb.report.site import charts
from rirdb.report.site.core import (HERE, FORMAT_LABELS, KIND_LABELS, REPO_BRANCH, REPO_URL, SITE_URL, Site, fmt_bytes,
                                    fmt_int, repo_blob, repo_raw)
from rirdb.report.site.data import FINDING_IDS, slug

REPO = paths.REPO_ROOT
ANALYZER_DOC = "docs/decisions/analyzer-v1.md"
FINDINGS_DOC = "docs/decisions/dataset-findings.md"
ANALYZER_CONFIG = "configs/analyzer_v1.yaml"
HOSTING_PLAN = "docs/plans/hosting-and-backlog.md"
UPSTREAM_URL = "https://github.com/RoyJames/room-impulse-responses"

# repository files that a site page renders: links to them go to the page
SITE_DOCS = {
    ANALYZER_DOC: "methods/index.html",
    FINDINGS_DOC: "methods/findings.html",
    "reports/guide/index.html": "guide/index.html",
    "reports/overview/index.html": "overview/index.html",
}


# ----------------------------------------------------------------------------- repository files

@lru_cache(maxsize=1)
def tracked() -> tuple[frozenset, frozenset]:
    """(files, folders) tracked by git; falls back to what is on disk when git is not available."""
    try:
        out = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True, check=True).stdout
        files = {f for f in out.splitlines() if f}
    except (OSError, subprocess.CalledProcessError):
        files = set()
    dirs = {p for f in files for p in (posixpath.dirname(f),) if p}
    for d in list(dirs):
        while "/" in d:
            d = posixpath.dirname(d)
            dirs.add(d)
    return frozenset(files), frozenset(dirs)


def is_tracked(path: str) -> bool:
    files, dirs = tracked()
    if not files:  # no git: trust the disk
        return (REPO / path).exists()
    p = path.rstrip("/")
    return p in files or p in dirs


def repo_url(path: str) -> str | None:
    """GitHub URL of a tracked file or folder, else None."""
    files, dirs = tracked()
    p = path.strip().rstrip("/")
    if not p or p.startswith(("/", "$", "~")) or " " in p:
        return None
    if p in files or (not files and (REPO / p).is_file()):
        return repo_blob(p)
    if p in dirs or (not files and (REPO / p).is_dir()):
        return f"{REPO_URL}/tree/{REPO_BRANCH}/{p}"
    return None


def wbr(path: str) -> Markup:
    """A path that may wrap after each '/' (instead of mid-word) in a narrow table column."""
    return Markup("/<wbr>").join(escape(p) for p in path.split("/"))


def size_of(p: Path) -> int | None:
    return p.stat().st_size if p.is_file() else None


def folder_stats(folder: Path, pattern: str) -> dict:
    fs = sorted(folder.glob(pattern)) if folder.is_dir() else []
    return {"n": len(fs), "bytes": sum(f.stat().st_size for f in fs) if fs else None}


# ----------------------------------------------------------------------------- Markdown

def front_matter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep:
            meta[k.strip()] = v.strip().strip('"')
    return meta, text[m.end():]


def _table_open(self, tokens, idx, options, env):
    return '<div class="tbl-wrap"><table class="tbl md-tbl">\n'


def _table_close(self, tokens, idx, options, env):
    return "</table></div>\n"


@lru_cache(maxsize=1)
def md() -> MarkdownIt:
    m = MarkdownIt("commonmark", {"html": False}).enable("table")
    m.add_render_rule("table_open", _table_open)
    m.add_render_rule("table_close", _table_close)
    return m


def code_url(text: str) -> str | None:
    """A code span that names a tracked file or folder (e.g. `configs/analyzer_v1.yaml`, `analysis/reference.py`)."""
    t = text.strip()
    if "/" not in t and not re.search(r"\.(py|md|yaml|yml|csv|tsv|json|sh)$", t):
        return None
    for cand in (t, f"src/rirdb/{t}"):
        u = repo_url(cand)
        if u:
            return u
    return None


def rewrite_href(href: str, doc_dir: str, root: str) -> str:
    if not href or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href) or href.startswith(("#", "//")):
        return href
    path, _, frag = href.partition("#")
    target = posixpath.normpath(posixpath.join(doc_dir, path))
    tail = f"#{frag}" if frag else ""
    if target in SITE_DOCS:
        return root + SITE_DOCS[target] + tail
    return (repo_url(target) or repo_blob(target)) + tail


PCT_SPACE = re.compile(r"(\d) ?%(?!\w)")


def _fix_inline(children: list[Token], doc_dir: str, root: str) -> list[Token]:
    out, in_link = [], 0
    for c in children:
        if c.type == "text":
            c.content = PCT_SPACE.sub("\\1\u00a0%", c.content)  # "6 %" never wraps before the sign, as fmt_pct
        if c.type == "link_open":
            c.attrSet("href", rewrite_href(c.attrGet("href") or "", doc_dir, root))
            in_link += 1
        elif c.type == "link_close":
            in_link -= 1
        elif c.type == "code_inline" and not in_link:
            u = code_url(c.content)
            if u:
                out += [Token("link_open", "a", 1, attrs={"href": u}), c, Token("link_close", "a", -1)]
                continue
        out.append(c)
    return out


def plain(s: str) -> str:
    return re.sub(r"[`*]", "", s).strip()


def render_md(text: str, *, root: str, doc_dir: str = "", shift: int = 0, drop_h1: bool = True,
              id_prefix: str = "") -> tuple[Markup, list[tuple[int, str, str]]]:
    """Markdown -> (HTML, headings [(level, text, id)]). Headings move down by `shift` levels; the first-level
    heading is dropped (the page has its own h1)."""
    m = md()
    env: dict = {}
    tokens = m.parse(text, env)
    keep, toc, i = [], [], 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "heading_open":
            level = int(t.tag[1:])
            if level == 1 and drop_h1:
                i += 3
                continue
            new = min(level + shift, 6)
            title = re.sub(r"\s*\(.*\)$", "", plain(tokens[i + 1].content)) or plain(tokens[i + 1].content)
            hid = id_prefix + slug(title)
            t.tag = tokens[i + 2].tag = f"h{new}"
            t.attrSet("id", hid)
            toc.append((new, title, hid))
        elif t.type == "th_open":
            t.attrSet("scope", "col")
        if t.children:
            t.children = _fix_inline(t.children, doc_dir, root)
        keep.append(t)
        i += 1
    return Markup(m.renderer.render(keep, m.options, env)), toc


def render_inline(text: str, *, root: str, doc_dir: str = "") -> Markup:
    m = md()
    env: dict = {}
    tokens = m.parseInline(text, env)
    for t in tokens:
        if t.children:
            t.children = _fix_inline(t.children, doc_dir, root)
    return Markup(m.renderer.render(tokens, m.options, env))


def read_doc(rel: str) -> tuple[dict, str]:
    f = REPO / rel
    if not f.exists():
        return {}, ""
    return front_matter(f.read_text())


# ----------------------------------------------------------------------------- dataset findings

def split_top(s: str, sep: str = ",") -> list[str]:
    """Split on `sep` outside parentheses and code spans."""
    parts, buf, depth, code = [], [], 0, False
    for ch in s:
        if ch == "`":
            code = not code
        elif not code and ch == "(":
            depth += 1
        elif not code and ch == ")":
            depth = max(depth - 1, 0)
        if ch == sep and depth == 0 and not code:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


# one name -> one registry id (FINDING_IDS keys that name a single dataset, plus the short forms used in lists)
EXTRA_NAMES = {"ACE": "ace", "Motus": "motus", "BRUDEX": "brudex", "trajectoRIR": "trajectorir", "DRR-scaled": "drr_scaled_brirs"}


@lru_cache(maxsize=1)
def name_ids() -> dict[str, str]:
    out = {k: v[0] for k, v in FINDING_IDS.items() if len(v) == 1 and "," not in k}
    out.update(EXTRA_NAMES)
    return out


def link_subject(subject: str, root: str, names: dict[str, str]) -> tuple[Markup, list[str]]:
    """The 'Dataset' cell with each dataset name linked to its page. Returns (html, linked ids)."""
    known = name_ids()
    html, linked = [], []
    for part in split_top(subject):
        m = re.match(r"^(.*?)(\s*\(.*\))?$", part, flags=re.S)
        name, rest = (m.group(1) or "").strip(), m.group(2) or ""
        did = known.get(name) or next((known[k] for k in sorted(known, key=len, reverse=True) if name.startswith(k)), None)
        if did and did in names:
            tail = Markup(" ") + render_inline(rest.strip(), root=root) if rest.strip() else Markup("")
            html.append(Markup('<a href="{}d/{}.html">{}</a>').format(root, did, name) + tail)
            linked.append(did)
        else:
            html.append(render_inline(part, root=root))
    return Markup(", ").join(html), linked


def parse_findings(root: str, names: dict[str, str]) -> dict:
    """docs/decisions/dataset-findings.md -> intro, sections (title, columns, rows), outro."""
    meta, body = read_doc(FINDINGS_DOC)
    if not body:
        return {"meta": meta, "title": "", "intro": Markup(""), "sections": [], "rows": []}
    title = ""
    pre, sections, cur = [], [], None
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            cur = {"title": line[3:].strip(), "columns": [], "rows": [], "before": [], "after": []}
            sections.append(cur)
            continue
        if cur is None:
            pre.append(line)
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if not cur["columns"]:
                cur["columns"] = cells
            else:
                cur["rows"].append(cells)
            continue
        (cur["after"] if cur["rows"] else cur["before"]).append(line)
    all_rows = []
    col_cls = {"dataset": "c-ds", "finding": "c-find", "evidence": "c-ev", "handling": "c-hand"}
    for s in sections:
        s["id"] = slug(s["title"])
        s["key"] = "licence" if s["title"].lower().startswith("licence") else "data"
        s["classes"] = [col_cls.get(c.lower(), "") for c in s["columns"]]
        s["before"] = render_md("\n".join(s["before"]), root=root, doc_dir="docs/decisions")[0] if "".join(s["before"]).strip() else Markup("")
        s["after"] = render_md("\n".join(s["after"]), root=root, doc_dir="docs/decisions")[0] if "".join(s["after"]).strip() else Markup("")
        out_rows = []
        for cells in s["rows"]:
            n_cols = max(len(s["columns"]), 1)
            cells = (cells + [""] * n_cols)[:n_cols]
            subject = cells[0]
            subj_html, linked = link_subject(subject, root, names)
            ids = next((v for k, v in sorted(FINDING_IDS.items(), key=lambda kv: -len(kv[0])) if subject.startswith(k)), [])
            also = [i for i in ids if i not in linked and i in names]
            row = {"section": s["key"], "subject": subject, "subject_plain": plain(subject), "subject_html": subj_html,
                   "linked": linked, "also": [(i, names[i]) for i in also],
                   "cells": [render_inline(c, root=root, doc_dir="docs/decisions") for c in cells[1:]],
                   "raw": cells[1:], "ids": sorted(set(linked) | set(also))}
            row["search"] = " ".join([plain(subject)] + [names[i] for i in row["ids"]] + row["ids"] + [plain(c) for c in cells[1:]]).lower()
            out_rows.append(row)
            all_rows.append(row)
        s["rows"] = out_rows
    intro = render_md("\n".join(pre), root=root, doc_dir="docs/decisions")[0] if "".join(pre).strip() else Markup("")
    return {"meta": meta, "title": title, "intro": intro, "sections": sections, "rows": all_rows}


# the five findings the home page leads with: (section, subject prefix, text the finding must contain)
HOME_FINDINGS = [
    ("data", "OpenAIR", "offline"),
    ("data", "SRIRACHA", ""),
    ("data", "TAU-SRIR", ""),
    ("data", "dEchorate", "cut-off"),
    ("licence", "EchoThief", ""),
]


def pick_findings(rows: list[dict]) -> list[dict]:
    out = []
    for sec, prefix, needle in HOME_FINDINGS:
        r = next((r for r in rows if r["section"] == sec and r["subject"].startswith(prefix)
                  and needle.lower() in (r["raw"][0] if r["raw"] else "").lower()), None)
        if r:
            out.append(dict(r, end=end_stop(r["raw"][0] if r["raw"] else "")))
    return out


# ----------------------------------------------------------------------------- shared numbers

def corpus_numbers(site: Site) -> dict:
    d = site.data
    c = d.get("corpus") or {}
    rows = d.get("datasets") or []
    survey = d.get("survey") or []
    lock = [r for r in rows if r.get("lock_summary")]
    sub = [r for r in rows if r.get("analysed") and r.get("analysis_scope") == "subsample_v1"]
    # lock rows (data/ds/<id>.json): an "@mirror" row is a mirrored web folder, not a file, and has no checksum;
    # the download total (corpus.download_bytes) leaves out rows without a stated size and the web folders
    n_pinned, n_unstated, mirrors = 0, 0, []
    for r in lock:
        for x in (site.dataset(r["id"]).get("lock") or []):
            if x.get("relpath") == "@mirror":
                mirrors.append({"name": r["name"], "bytes": (r.get("manifest") or {}).get("bytes")})
            else:
                n_pinned += 1
                n_unstated += x.get("size") is None
    not_counted = ([plural(n_unstated, "file") + " of unstated size"] if n_unstated else []) + [
        f"{m['name']}'s web folder" + (f" ({fmt_bytes(m['bytes'])})" if m["bytes"] else "") for m in mirrors]
    dups = sorted(((r.get("metrics") or {}).get("n_duplicates") or 0, r["name"]) for r in rows)[::-1]
    return {
        "n_registry": len(rows), "n_survey": len(survey), "n_all": len(rows) + len(survey),
        "n_analysed": sum(1 for r in rows if r.get("analysed")),
        "n_not_analysed": sum(1 for r in rows if not r.get("analysed")),
        "n_irs_analysed": c.get("n_irs_analysed"), "n_irs_indexed": c.get("n_irs_indexed"),
        "n_spaces": c.get("n_spaces"), "n_clean": c.get("n_clean_rooms_irs"),
        "download_bytes": c.get("download_bytes"), "manifest_bytes": c.get("manifest_bytes"),
        "manifest_files": c.get("manifest_files"),
        "n_lock_ds": len(lock), "n_lock_files": n_pinned,
        "mirror_names": join_and([f"{m['name']}'s web folder" for m in mirrors]),
        "not_counted": join_and(not_counted),
        "n_manifest_ds": sum(1 for r in rows if r.get("manifest")),
        "n_index_ds": sum(1 for r in rows if (r.get("index") or {}).get("n_records")),
        "n_sub": len(sub), "n_full": sum(1 for r in rows if r.get("analysed")) - len(sub),
        "sub_analysed": sum((r.get("metrics") or {}).get("n_analysed") or 0 for r in sub),
        "sub_records": sum((r.get("index") or {}).get("n_records") or 0 for r in sub),
        "n_duplicates": c.get("n_duplicate_copies"),
        "dup_by_ds": join_and([f"{fmt_int(n)} in {name}" for n, name in dups if n]),
    }


def names_map(site: Site) -> dict[str, str]:
    return {r["id"]: r["name"] for r in site.data.get("datasets") or []}


# ----------------------------------------------------------------------------- home

def render_home(site: Site, num: dict, findings: dict) -> None:
    d = site.data
    root = ""
    picks = pick_findings(findings["rows"])
    every = [{"@type": "Dataset", "name": r["name"], "url": f"{SITE_URL}d/{r['id']}.html"}
             for r in (d.get("datasets") or []) + (d.get("survey") or [])]
    # 6 dense datasets are analysed on a fixed subsample, so "every IR" holds only for the others
    scope = (f" (all of them in {num['n_full']} datasets, a fixed subsample in the {num['n_sub']} largest)"
             if num["n_sub"] else "")
    lede = (f"Every public room impulse response dataset we could find: where to get it and what its licence allows, "
            f"and, for the {num['n_analysed']} that rirdb downloaded and analysed, an acoustic analysis of their "
            f"IRs{scope}.")
    jsonld = {
        "@context": "https://schema.org", "@type": "DataCatalog", "name": "rirdb",
        "alternateName": "rirdb: room impulse response datasets", "url": SITE_URL, "inLanguage": "en-GB",
        "description": (f"A checked catalogue of {num['n_all']} public room impulse response datasets: where to get "
                        f"each one, its licence, and for {num['n_analysed']} of them an ISO 3382 analysis of their "
                        f"impulse responses{scope}."),
        "creator": {"@type": "Person", "name": "Ilias Ibnyahya",
                    "affiliation": {"@type": "Organization", "name": "Queen Mary University of London"}},
        "isBasedOn": REPO_URL,
        "dateModified": (d.get("generated") or "")[:10] or None,
        "dataset": every,
    }
    jsonld = {k: v for k, v in jsonld.items() if v is not None}
    site.page("index.html", "home.html", title="rirdb", nav=None, jsonld=jsonld,
              description=(f"A checked catalogue of public room impulse response datasets: where to get them, their "
                           f"licences, and an acoustic analysis of {fmt_int(num['n_irs_analysed'])} IRs from "
                           f"{num['n_analysed']} datasets."),
              num=num, lede=lede, picks=picks, n_findings=len(findings["rows"]),
              clap_n=((d.get("clap") or {}).get("meta") or {}).get("n"))


# ----------------------------------------------------------------------------- methods

GROUPS = [
    ("content", "Content", "Excluded from the clean measured rooms subset."),
    ("decay", "Decay", "Limits the decay analysis; the IR is kept. Filter on it when it matters."),
    ("signal", "Signal", "A property of the file or the recording; the IR is kept."),
    ("spatial", "Spatial", "How the analysed channel relates to the capture; the IR is kept."),
]

# what the per-IR index and metrics fields mean (README.md "Picking IRs by constraints", query.py, registry.py)
FIELDS = [
    ("measured", "true for IRs recorded in real spaces; false for production libraries, simulated and hybrid sets."),
    ("ir_kind", "What the IR is of. <code>room</code> is an indoor space; the other kinds are listed below. The "
                "clean subset keeps rooms only."),
    ("preferred", "One representation per measured position. Where a dataset publishes the same position in several "
                  "formats (OpenAIR: B-format and a mono copy), only one is preferred, so statistics count each "
                  "position once."),
    ("dup_of", "Set on exact duplicates: the ir_id of the copy that is kept. Two records are duplicates when their "
               "decoded samples are identical (sha1 of the float32 samples), even under different names. Only the "
               "<code>corpus_dedup</code> and <code>clean_rooms</code> views have it (<code>rirdb dev query \"dup_of IS "
               "NULL\" --view corpus_dedup</code>); <code>clean_rooms</code> already leaves the copies out. Use "
               "<code>dup_of IS NULL</code> for training and evaluation splits. The metrics snapshots do not have "
               "this column{dups}."),
    ("training_use", "Whether the licence allows training machine-learning models: <code>allowed</code>, "
                     "<code>prohibited</code>, <code>ask</code> or <code>unknown</code>. An explicit registry value "
                     "wins; otherwise open licences (CC BY, CC0, MIT, Apache, GPL, ODC-By, BSD) give "
                     "<code>allowed</code> and custom or unstated terms <code>unknown</code>. CC BY-NC licences also "
                     "give <code>allowed</code>, meaning non-commercial use only: check <code>license_spdx</code> "
                     "or the catalogue's commercial-use column as well."),
    ("redistribute_audio", "Whether the audio may be shared again: <code>yes</code>, <code>unaltered_only</code> "
                           "(complete and unchanged, e.g. Voxengo), <code>no</code> or <code>unknown</code>."),
    ("license_spdx", "Licence of the IR as an SPDX id (<code>LicenseRef-...</code> for custom terms). Licences are "
                     "per IR: OpenAIR contributors chose one per space. Neither <code>license_spdx</code> nor <code>training_use</code> "
                     "shows a licence conflict (the record names one licence, the files or the paper another, as for "
                     "SRIRACHA and the TU Ilmenau robot set); the licence class in the catalogue on the Datasets page does, "
                     "so check it before relying on these columns."),
    ("capture_format", "Channel layout of the record; values below."),
    ("grade", "A to D, from which decay times are valid (see Grades)."),
    ("valid_&lt;metric&gt;_&lt;band&gt;", "Whether a value meets the decay-range rule (EDT 20 dB, T20 35 dB, T30 45 dB "
                                    "of usable range). Invalid values are kept in the table but never used in statistics. "
                                    "The metrics snapshots keep only <code>valid_t30_&lt;band&gt;</code>."),
    ("edc_mode_&lt;band&gt;", "How the decay curve of each band was integrated (e.g. <code>edc_mode_bb</code>, "
                              "broadband): <code>lundeby</code> (Lundeby-compensated Schroeder), "
                              "<code>schroeder_nofloor</code> (plain Schroeder, for IRs that end before any noise "
                              "floor) or <code>failed</code>. In the query views only, not in the metrics snapshots."),
]

EXAMPLE_QUERIES = """# inside a SLURM job, with the pixi environment active
rirdb dev query "t30_mid BETWEEN 1.2 AND 1.8 AND valid_t30_500 AND c80_mid > 0 AND ir_kind = 'room' AND preferred" \\
    --columns dataset_id,room_id,category,t30_mid,edt_mid,c80_mid,drr_bb,sti --order-by "c80_mid DESC"
rirdb dev query "category = 'concert_hall' AND grade = 'A' AND training_use = 'allowed'" --limit 0 --csv picks.csv
rirdb dev query "dup_of IS NOT NULL" --view corpus_dedup --columns ir_id,dataset_id,dup_of --limit 0 --csv copies.csv
rirdb dev query "room_geometry = 'yes'" --view datasets --columns dataset_id,name,room_info_where"""


def corpus_flag_counts(site: Site) -> tuple[dict[str, int], int]:
    """IRs carrying each flag over all analysed datasets, summed from data/ds/<id>.json (analysis.flags), and the
    number of analysed IRs they are counted over."""
    counts: dict[str, int] = {}
    total = 0
    for r in site.data.get("datasets") or []:
        if not r.get("analysed"):
            continue
        a = (site.dataset(r["id"]) or {}).get("analysis") or {}
        if not a.get("flags"):
            continue
        total += a.get("n_analysed") or 0
        for f in a["flags"]:
            if f.get("n") is not None:
                counts[f["flag"]] = counts.get(f["flag"], 0) + int(f["n"])
    return counts, total


def plural(n: int | None, one: str, many: str | None = None) -> str:
    return f"{fmt_int(n)} {one if n == 1 else (many or one + 's')}"


def not_analysed_parts(rows: list[dict]) -> list[str]:
    """Why the registry datasets without analysis have none, grouped, from their registry status."""
    rest = [r for r in rows if not r.get("analysed")]
    later = [r for r in rest if r.get("status") in ("planned", "deferred")]
    manual = [r for r in rest if r.get("status") == "manual"]
    broken = [r for r in rest if r.get("status") == "broken"]
    other = [r for r in rest if r not in later and r not in manual and r not in broken]
    parts = []
    if later:
        sim = sum(1 for r in later if r.get("type") == "simulated")
        parts.append(f"{fmt_int(len(later))} {'is' if len(later) == 1 else 'are'} planned or deferred"
                     + (f" ({fmt_int(sim)} of them simulated)" if sim else ""))
    if manual:
        parts.append(f"{fmt_int(len(manual))} need{'s' if len(manual) == 1 else ''} a manual or registered download")
    if broken:
        parts.append(f"{fmt_int(len(broken))} {'is' if len(broken) == 1 else 'are'} broken upstream")
    if other:
        dl = sum(1 for r in other if r.get("lock_summary"))
        if dl:
            parts.append(f"{fmt_int(dl)} {'is' if dl == 1 else 'are'} downloaded but not indexed yet")
        if len(other) > dl:
            parts.append(f"{fmt_int(len(other) - dl)} {'is' if len(other) - dl == 1 else 'are'} not fetched yet")
    return parts


def join_and(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1] if parts else ""


def lower_first(m: Markup) -> Markup:
    """'The website …' -> 'the website …' when the text follows a colon (leaves acronyms such as 'IRs' alone)."""
    s = str(m)
    if len(s) > 1 and s[0].isupper() and s[1].islower():
        return Markup(s[0].lower() + s[1:])
    return m


def dual(wide: Markup, narrow: Markup) -> Markup:
    """The same chart drawn twice: CSS shows the narrow drawing on phones, so its text stays readable."""
    return Markup('<div class="vw-wide">') + wide + Markup('</div><div class="vw-narrow">') + narrow + Markup("</div>")


def end_stop(raw: str) -> str:
    """The full stop a table cell lacks when it is used as a sentence."""
    return "" if raw.rstrip().endswith((".", "?")) else "."


def subsets(site: Site) -> dict:
    f = paths.REPORTS_DIR / "overview" / "overview.json"
    labels = {"all": "All analysed IRs", "measured": "Measured", "measured_rooms": "Measured room IRs",
              "clean_rooms": "Clean measured rooms"}
    try:
        ov = json.loads(f.read_text())
    except (OSError, ValueError):
        ov = {}
    sql = ov.get("subset_sql") or {}
    dist = ov.get("distributions") or {}
    names = ov.get("subsets") or labels
    rows = []
    for k in names:
        dd = dist.get(k) or {}
        rows.append({"key": k, "label": names[k], "sql": sql.get(k), "n_irs": dd.get("n_irs"), "n_rooms": dd.get("n_rooms"),
                     "n_datasets": dd.get("n_datasets")})
    return {"rows": rows, "generated": ov.get("generated")}


def pipeline_steps(num: dict, site: Site) -> list[dict]:
    d = site.data
    clap = (d.get("clap") or {}).get("meta") or {}
    sd = d.get("survey_date")
    # each step: what it does, and the commands with what each writes ("io": cmd, verb, out, tail); a command
    # of None introduces files kept by hand
    steps = [
        {"id": "step-registry", "name": "Registry",
         "what": "One folder per dataset, with one YAML file: sources, file selections, licence terms (every statement "
                 "where they conflict), expected counts, capture formats and what is published about the rooms, plus a "
                 "CSV of the spaces corrected by hand. Datasets found on the web but not yet downloaded are catalogued "
                 "the same way, with their checked facts.",
         "io": [{"cmd": None, "verb": "Kept by hand:", "out": ["registry/<id>/dataset.yaml", "registry/<id>/spaces.csv"]},
                {"cmd": "rirdb dev registry compile", "verb": "checks the registry entries and writes",
                 "out": ["compiled/sources.tsv"], "tail": " and the dataset tables of README.md"}],
         "count": f"{num['n_registry']} registry datasets; {num['n_survey']} more catalogued from their web pages"
                  + (f", facts checked on {sd}" if sd else "") + "."},
        {"id": "step-download", "name": "Download with checksums",
         "what": "Every file is resolved to a pinned version (Zenodo record, Figshare version, Git commit, or the "
                 "server's ETag) and checked against the provider's checksum, or against a sha256 recorded on first "
                 "download where the host publishes none. Archives are extracted and every extracted file gets a sha256, "
                 "so a purged or damaged copy is detected and fetched again.",
         "io": [{"cmd": "sbatch jobs/download.sh fetch/run.sh --wave 1",
                 "out": ["$RIRDB_ROOT/raw/<id>/", "registry/<id>/downloads.tsv", "registry/<id>/files.tsv.gz"]}],
         "count": f"{num['n_lock_ds']} datasets pinned: {fmt_int(num['n_lock_files'])} files with a checksum"
                  + (f", and {num['mirror_names']}, checked file by file against its manifest" if num["mirror_names"] else "")
                  + f". {fmt_bytes(num['download_bytes'])} to download"
                  + (f", not counting {num['not_counted']}" if num["not_counted"] else "")
                  + f"; {fmt_int(num['manifest_files'])} extracted files with a sha256 for {num['n_manifest_ds']} "
                    f"datasets ({fmt_bytes(num['manifest_bytes'])})."},
        {"id": "step-index", "name": "Index",
         "what": "One row per IR record (one source position heard at one receiver position) in a single table: file "
                 "locator, capture format, sample rate, positions, space, kind of IR, the preferred representation and "
                 "the licence of that IR.",
         "io": [{"cmd": "rirdb dev index-v1 <id>", "out": ["$RIRDB_ROOT/index/irs/", "$RIRDB_ROOT/index/rooms/"]}],
         "count": f"{fmt_int(num['n_irs_indexed'])} IR records from {num['n_index_ds']} datasets, in "
                  f"{fmt_int(num['n_spaces'])} spaces."},
        {"id": "step-analyse", "name": "Analyse",
         "what": "Each analysed IR is read from the original files: ISO 3382-1/-2 parameters per octave band "
                 "from 63 Hz to 8 kHz with explicit noise handling, IEC 60268-16 STI, DRR, echo density and mixing time, "
                 "IACC for binaural and JLF for B-format IRs. Each value carries a validity flag; quality flags and an "
                 "A to D grade summarise each IR. The decoded samples are hashed, so exact duplicates can be found "
                 "even under different names.",
         "io": [{"cmd": "jobs/analyze_dataset.sh <id> <n_shards>", "out": ["$RIRDB_ROOT/metrics/v1/<id>/wide.parquet"]}],
         "count": f"{fmt_int(num['n_irs_analysed'])} IRs from {num['n_analysed']} datasets."
                  + (f" Every IR of {num['n_full']} of them; the {num['n_sub']} densest are analysed on a fixed, "
                     f"deterministic subsample ({fmt_int(num['sub_analysed'])} of their {fmt_int(num['sub_records'])} "
                     f"records) that keeps at least one record per space and condition." if num["n_sub"] else "")},
        {"id": "step-audit", "name": "Audit",
         "what": "A signal-integrity pass over the analysed IRs: sweep residue, tonal lines, dropouts, unsteady noise "
                 "floors, quantised tails and where the content really stops (effective bandwidth). The query views "
                 "then mark exact duplicates: of the records with identical samples, all but the one with the lowest "
                 "ir_id.",
         "io": [{"cmd": "jobs/audit_dataset.sh <id> <n_shards>", "out": ["$RIRDB_ROOT/audit/v1/<id>/audit.parquet"]}],
         "count": f"{fmt_int(num['n_clean'])} IRs remain in the clean measured rooms subset, which leaves out the IRs "
                  f"with content flags and the {fmt_int(num['n_duplicates'])} exact duplicate copies."},
        {"id": "step-map", "name": "CLAP map",
         "what": "CLAP audio embeddings of each IR, and of its effect on dry recordings, projected to two dimensions "
                 "with UMAP and frozen, so that new IRs (for example reverb renders) can be placed on the same map and "
                 "scored for realism against the real rooms.",
         "io": [{"cmd": "rirdb dev map fit", "out": ["$RIRDB_ROOT/maps/clap_v1/", "snapshots/clap_map_v1/"]}],
         "count": (f"{fmt_int(clap.get('n'))} IRs on the map, at most {fmt_int(clap.get('cap_per_dataset'))} per dataset; "
                   f"model {clap.get('model')}." if clap.get("n") else "")},
        {"id": "step-report", "name": "Reports",
         "what": "The corpus report, the validation against published values, the interactive overview, the Field "
                 "Guide, one Markdown card per dataset, the committed metrics snapshots and this website, all built "
                 "from the same tables.",
         "io": [{"cmd": "rirdb dev report corpus", "out": ["reports/corpus/"], "tail": ", with duplicates.csv"},
                {"cmd": "rirdb dev report validation", "out": ["reports/validation/"]},
                {"cmd": "rirdb dev report overview", "out": ["reports/overview/"]},
                {"cmd": "rirdb dev report guide", "out": ["reports/guide/"]},
                {"cmd": "rirdb dev report cards", "out": ["docs/datasets/"]},
                {"cmd": "rirdb dev snapshot", "out": ["snapshots/metrics_core/"]},
                {"cmd": "rirdb dev report site", "out": ["reports/site/"],
                 "tail": ", with copies of the Field Guide and the overview"}],
         "count": f"Built {d.get('generated')}" + (f" from commit {d.get('git')}" if d.get("git") else "")
                  + f", analyzer {d.get('analyzer_version')}."},
    ]
    for s in steps:
        for io in s["io"]:
            io.setdefault("verb", "writes")
            io.setdefault("tail", "")
            io["out"] = [(o, repo_url(o)) for o in io["out"]]
    return steps


def render_methods(site: Site, num: dict) -> None:
    root = "../"
    meta, body = read_doc(ANALYZER_DOC)
    doc_html, doc_toc = render_md(body, root=root, doc_dir="docs/decisions", shift=1, id_prefix="an-")
    d = site.data
    counts, n_flagged_over = corpus_flag_counts(site)
    groups = []
    for key, label, text in GROUPS:
        # "NaN" spelled out: the site checker reads a bare NaN in page text as a leaked value
        fl = [dict(f, n=counts.get(f["flag"]),
                   share=(counts[f["flag"]] / n_flagged_over) if f["flag"] in counts and n_flagged_over else None,
                   info=re.sub(r"\bNaN\b", "Not-a-number", f.get("info") or ""))
              for f in d.get("flags") or [] if f.get("group") == key]
        if fl:
            groups.append({"key": key, "label": label, "text": text, "flags": fl})
    grades = (d.get("corpus") or {}).get("grades") or {}
    n_graded = sum(grades.values()) or 0
    grade_rows = [{"g": g, "text": t, "n": grades.get(g), "share": (grades.get(g) or 0) / n_graded if n_graded else None}
                  for g, t in (d.get("grades") or {}).items()]
    cfg_path = REPO / ANALYZER_CONFIG
    cfg = None
    if cfg_path.exists():
        raw = cfg_path.read_bytes()
        cfg = {"text": raw.decode(errors="replace"), "sha": hashlib.sha256(raw).hexdigest()[:16],
               "url": repo_url(ANALYZER_CONFIG) or repo_blob(ANALYZER_CONFIG)}
    dups = (f", so they still hold the {fmt_int(num['n_duplicates'])} exact duplicate copies"
            + (f" ({num['dup_by_ds']})" if num["dup_by_ds"] else "")) if num["n_duplicates"] else ""
    fields = [(slug(re.sub(r"&lt;|&gt;", "", k)), Markup(k), Markup(v.replace("{dups}", dups))) for k, v in FIELDS]
    # the grade bar drawn twice, so its labels stay readable on phones (.vw-wide/.vw-narrow in methods.html)
    grade_title = "Grades of all analysed IRs"
    grade_chart = (dual(charts.grade_bar(grades, w=560, title=grade_title),
                        charts.grade_bar(grades, w=320, title=grade_title)) if n_graded else None)
    site.page("methods/index.html", "methods.html", title="Methods", nav="methods",
              description="How rirdb downloads, checks, indexes and analyses room impulse responses, and what "
                          "each parameter, flag and grade on this site means.",
              num=num, n_flagged_over=n_flagged_over, steps=pipeline_steps(num, site), doc_html=doc_html, doc_toc=doc_toc, doc_meta=meta,
              doc_url=repo_url(ANALYZER_DOC) or repo_blob(ANALYZER_DOC), groups=groups, grade_rows=grade_rows,
              grade_chart=grade_chart,
              subsets=subsets(site), fields=fields, kinds=list(KIND_LABELS.items()),
              formats=list(FORMAT_LABELS.items()), example_queries=EXAMPLE_QUERIES, cfg=cfg,
              readme_url=repo_blob("README.md"))


def render_findings(site: Site, num: dict, findings: dict) -> None:
    rows = findings["rows"]
    ids = sorted({i for r in rows for i in r["ids"]})
    n_data = sum(1 for r in rows if r["section"] == "data")
    n_lic = sum(1 for r in rows if r["section"] == "licence")
    site.page("methods/findings.html", "findings.html", title="Dataset findings", nav="methods",
              description="What consolidating the public room impulse response datasets revealed: problems in the "
                          "upstream data, their evidence, how rirdb handles them, and what the licences allow.",
              num=num, f=findings, n_data=n_data, n_lic=n_lic, n_ids=len(ids),
              doc_url=repo_url(FINDINGS_DOC) or repo_blob(FINDINGS_DOC))


# ----------------------------------------------------------------------------- downloads

SITE_JSON_DOC = {
    "generated": "Build time (UTC).",
    "git": "Commit the data was collected from.",
    "analyzer_version": "Analyzer version of the metrics.",
    "survey_date": "Date the web facts (licences, hosts, availability) were checked.",
    "survey_note": "What the survey facts are and where they come from.",
    "corpus": "Totals: IRs indexed and analysed, datasets, spaces, clean IRs, grades, duplicates, download and extracted sizes.",
    "datasets": "One object per registry dataset: registry facts, licence reading, index counts, metric summary, files, survey facts, storage, lock and manifest summaries.",
    "survey": "One object per dataset found on the web but not in the registry: facts from its landing page.",
    "spaces": "One object per indexed space, with medians of its clean IRs.",
    "validity_t30": "Share of the clean measured-room IRs with a valid T30, per octave band, counted three ways: per IR "
                    "(<code>ir</code>), median of the per-dataset shares (<code>dataset_median</code>) and share of "
                    "spaces with at least one valid value (<code>space</code>); with <code>n_irs</code>, "
                    "<code>n_datasets</code> and <code>n_spaces</code>. Each dataset's own shares are in "
                    "<code>data/ds/&lt;id&gt;.json</code> (<code>analysis.validity</code>).",
    "openair_licences": "OpenAIR's licence per space, with the archived page each comes from.",
    "reference_hists": "Corpus reference histograms (one value per space) on the bins the dataset pages use.",
    "reference_bands": "Corpus per-band box plots (clean measured rooms).",
    "clap": "CLAP map settings, background images and the realism and coverage of every dataset.",
    "validation": "Summaries of the comparisons with published values (OpenAIR, ACE).",
    "params": "The acoustic parameters: key, label, unit, decimals and meaning.",
    "flags": "The quality flags: column, label, group and meaning.",
    "grades": "The A to D grade definitions.",
}

DS_JSON_DOC = {
    "id": "Dataset id.",
    "lock": "Rows of the lock file: every downloaded file with its URL, size, checksum and pinned version.",
    "manifest": "Extracted files: count, bytes, extensions and top-level folders.",
    "storage": "Where rirdb keeps its copy at QMUL.",
    "probe": "Text output of rirdb dev probe: what the files hold.",
    "findings": "Rows of the dataset findings that concern this dataset.",
    "validation": "Per-space comparison with published values (OpenAIR, ACE arrays only).",
    "duplicates_detail": "Exact duplicates inside the dataset and with other datasets.",
    "clap": "This dataset's points on the two CLAP maps (coordinates in 1/1000 of the unit square) and its realism scores.",
    "spaces": "The dataset's spaces, as in spaces.csv.",
    "analysis": "Only for analysed datasets: parameter percentiles, per-band box plots and validity, histograms on the "
                "corpus bins, grades, every flag's count and share, formats, sample rates, kinds, categories, duplicates, "
                "bandwidth and licences.",
}

CATALOG_DOC = {
    "id": "rirdb id. Survey-only datasets have ids starting with <code>new_</code>.",
    "name": "Dataset name.",
    "year": "Year of release or of the paper.",
    "type": "Measured, production library, simulated or hybrid.",
    "in_registry": "In the rirdb registry.",
    "analysed": "Analysed by rirdb.",
    "availability": "How the files can be had.",
    "capture_formats": "Capture formats (registry datasets).",
    "fs_hz": "Sample rates in Hz: as found in the files for indexed datasets, otherwise as published.",
    "n_irs": "Number of IRs: counted in the index, or as published (see <code>n_irs_source</code>). Empty when not stated.",
    "n_spaces": "Number of spaces: rooms, halls, outdoor sites.",
    "licence": "Licence, as a short label.",
    "licence_class": "Licence family.",
    "commercial_use": "Whether commercial use is allowed.",
    "ml_training": "Whether training machine-learning models is allowed.",
    "redistribute_audio": "Whether the audio may be shared again.",
    "host": "Where the files are hosted.",
    "homepage": "Landing page.",
    "doi": "Dataset DOI, where there is one (paper DOIs are left out).",
    "institution": "Institution that published the dataset.",
    "country": "Country of the institution.",
    "content": "What the dataset holds, in one or two sentences.",
    "rirdb_status": "analysed; registry (in the registry, not analysed); survey (catalogued from its web page only).",
    "registry_status": "Registry status; empty for survey rows.",
    "capture_families": "Capture formats grouped into families.",
    "availability_group": "Availability grouped: open download, gated (registration, request or manual), gone (broken or archived only).",
    "licence_spdx": "SPDX licence id, or <code>LicenseRef-...</code> for custom terms.",
    "n_irs_source": "Where <code>n_irs</code> comes from.",
    "n_irs_indexed": "IR records in rirdb's index.",
    "n_irs_analysed": "IRs analysed by rirdb.",
    "t30_kind": "T30, or T20 where fewer than half of the IRs allow a valid T30.",
    "t30_p10_s": "10th percentile of the mid-frequency decay time over the IRs of real spaces, in seconds.",
    "t30_p50_s": "Median of the same, in seconds.",
    "t30_p90_s": "90th percentile of the same, in seconds.",
    "paper_url": "The paper that describes the dataset.",
    "site_url": "The dataset's page on this site.",
}

SPACES_DOC = {
    "room_id": "rirdb space id: <code>&lt;dataset_id&gt;/&lt;room_key&gt;</code>.",
    "physical_id": "Shared by records of the same physical space in different datasets.",
    "dataset_id": "Dataset id.",
    "dataset_name": "Dataset name.",
    "room_label": "Name of the space as its dataset gives it.",
    "room_key": "Key of the space inside its dataset.",
    "category": "Kind of space, from naming rules and hand corrections.",
    "category_label": "The category in words.",
    "category_fixed": "The category was corrected by hand.",
    "ir_kind": "Kind of IR (see Methods).",
    "volume_m3": "Volume in m³, where the dataset publishes it.",
    "dims_m": "Dimensions in metres, where published.",
    "n_irs": "IR records of the space in the index.",
    "n_clean": "IRs in the clean subset: measured, preferred, no duplicates, no content flags.",
    "n_analysed": "IRs analysed.",
    "t30_s": "Decay time shown on the site: the T30 median where at least half the clean IRs allow T30, otherwise the T30-or-T20 median (s).",
    "t30_source": "Which decay time <code>t30_s</code> is.",
    "t30_mid_s": "Median T30 (mean of 500 Hz and 1 kHz) over the clean IRs, in seconds.",
    "t30_or_t20_s": "Median of T30, or T20 where T30 is not valid, in seconds.",
    "share_valid_t30": "Share of the clean IRs with a valid mid-frequency T30.",
    "t30_any_s": "Median T30 over all preferred, non-duplicate IRs, including those outside the clean subset (s).",
    "edt_s": "Median EDT over the clean IRs, in seconds.",
    "c80_db": "Median C80 over the clean IRs, in dB.",
    "drr_db": "Median DRR over the clean IRs, in dB.",
    "licence": "Licence of most of the space's IRs (SPDX).",
    "formats": "Capture formats of the space's IRs.",
    "description": "Description, where the dataset gives one.",
    "materials": "Surface materials, where published.",
    "bg_noise_dba": "Background noise level in dB(A), where published.",
    "note": "Notes.",
    "dataset_url": "The dataset's page on this site.",
}

# columns whose values are listed with their counts on the downloads page
CATEGORICAL = {"type", "in_registry", "analysed", "availability", "capture_formats", "licence_class", "commercial_use",
               "ml_training", "redistribute_audio", "rirdb_status", "registry_status", "capture_families",
               "availability_group", "n_irs_source", "t30_kind", "category", "category_fixed", "ir_kind", "t30_source"}

LOCK_DOC_FALLBACK = [
    ("relpath", "Path of the file inside the dataset folder."), ("url", "Where the file is downloaded from."),
    ("size", "Size in bytes, as the host states it."), ("algo", "Checksum algorithm."), ("checksum", "The checksum."),
    ("provider_version", "The pinned version."), ("resolved_at", "When rirdb resolved the source (UTC)."),
]
MANIFEST_DOC_FALLBACK = [("relpath", "Path of the extracted file."), ("size", "Size in bytes."),
                         ("sha256", "sha256 of the extracted file.")]
CLAP_REF_COLUMNS = [
    ("ir_id", "IR record id."), ("dataset_id", "Dataset id."), ("room_id", "Space id."),
    ("clean", "In the clean measured rooms subset (only these have a radius)."),
    ("x_ir, y_ir", "Position on the map of the IR itself, scaled to the unit square."),
    ("radius_ir", "Distance to the 5th nearest IR from another dataset, in the 512-d CLAP space."),
    ("x_reverb_vector, y_reverb_vector", "Position on the map of the IR's effect on dry recordings."),
    ("radius_reverb_vector", "The same radius for that map."),
]


def csv_schema(path: Path, doc: dict[str, str], categorical: set[str], max_values: int = 30) -> dict:
    """Columns of a CSV written by the site, with value lists for the categorical ones (from the file itself)."""
    if not path.is_file():
        return {"n_rows": None, "columns": [(k, Markup(v), None) for k, v in doc.items()]}
    with path.open(newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        header = next(rd, [])
        rows = list(rd)
    cols = []
    for j, c in enumerate(header):
        vals: dict[str, int] = {}
        for r in rows:
            v = r[j] if j < len(r) else ""
            for x in (v.split("|") if "|" in v else [v]):
                if x != "":
                    vals[x] = vals.get(x, 0) + 1
        shown = None
        if vals and c in categorical and len(vals) <= max_values:
            shown = sorted(vals.items(), key=lambda kv: (-kv[1], kv[0]))
        cols.append((c, Markup(doc.get(c, "")), shown))
    return {"n_rows": len(rows), "columns": cols}


def render_downloads(site: Site, num: dict) -> None:
    out, data_dir = site.out, site.out / "data"
    d = site.data
    rows = d.get("datasets") or []
    try:
        from rirdb.report.site.pages_get import LOCK_COLUMNS, MANIFEST_COLUMNS
        lock_cols = [(k, Markup(v)) for k, v in LOCK_COLUMNS]
        man_cols = [(k, Markup(v)) for k, v in MANIFEST_COLUMNS]
    except ImportError:
        lock_cols = [(k, Markup(escape(v))) for k, v in LOCK_DOC_FALLBACK]
        man_cols = [(k, Markup(escape(v))) for k, v in MANIFEST_DOC_FALLBACK]
    try:
        from rirdb.export import CORE
        core_cols = list(CORE)
    except Exception:  # noqa: BLE001 - the list is informative only
        core_cols = []

    catalog_json = data_dir / "catalog.json"
    try:
        n_catalog_json = len(json.loads(catalog_json.read_text())) if catalog_json.is_file() else None
    except ValueError:
        n_catalog_json = None
    cat_schema = csv_schema(data_dir / "catalog.csv", CATALOG_DOC, CATEGORICAL)
    sp_schema = csv_schema(data_dir / "spaces.csv", SPACES_DOC, CATEGORICAL)
    ds_stats = folder_stats(data_dir / "ds", "*.json")
    lock_stats = folder_stats(data_dir / "locks", "*.lock.tsv")
    man_stats = folder_stats(data_dir / "manifests", "*.files.tsv.gz")
    get_stats = folder_stats(out / "get", "*.sh")

    site_files = [
        {"path": "data/site.json", "what": "Everything the site shows, in one file: corpus totals, every registry and "
                                           "survey dataset, every space, the reference distributions and the glossary.",
         "fmt": "JSON", "bytes": size_of(data_dir / "site.json"), "n": None, "schema": "#schema-site"},
        {"path": "data/catalog.csv", "what": "The catalogue: one row per dataset, registry and survey, with the "
                                             "plain-language licence reading.",
         "fmt": "CSV (UTF-8)", "bytes": size_of(data_dir / "catalog.csv"),
         "n": f"{fmt_int(cat_schema['n_rows'])} rows" if cat_schema["n_rows"] is not None else None, "schema": "#schema-catalog"},
        {"path": "data/catalog.json", "what": "The same rows as a JSON array (lists stay lists).",
         "fmt": "JSON", "bytes": size_of(catalog_json),
         "n": f"{fmt_int(n_catalog_json)} rows" if n_catalog_json is not None else None, "schema": "#schema-catalog"},
        {"path": "data/spaces.csv", "what": "Every indexed space, with medians of its clean IRs.",
         "fmt": "CSV (UTF-8)", "bytes": size_of(data_dir / "spaces.csv"),
         "n": f"{fmt_int(sp_schema['n_rows'])} rows" if sp_schema["n_rows"] is not None else None, "schema": "#schema-spaces"},
        {"path": "data/ds/", "label": "data/ds/<id>.json", "what": "One file per registry dataset: the lock rows, "
                                                                     "manifest summary, findings, spaces, CLAP points and, for analysed datasets, the full analysis.",
         "fmt": "JSON", "bytes": ds_stats["bytes"], "n": plural(ds_stats['n'], 'file') if ds_stats["n"] else None,
         "schema": "#schema-ds", "folder": True},
        {"path": "data/locks/", "label": "data/locks/<id>.lock.tsv", "what": "Every file to download, with its URL, "
                                                                             "size, checksum and pinned version (copies of registry/<id>/downloads.tsv).",
         "fmt": "TSV", "bytes": lock_stats["bytes"], "n": plural(lock_stats['n'], 'file') if lock_stats["n"] else None,
         "schema": "#schema-lock", "folder": True},
        {"path": "data/manifests/", "label": "data/manifests/<id>.files.tsv.gz", "what": "Every file after extraction, "
                                                                                        "with its size and sha256 (copies of registry/<id>/files.tsv.gz).",
         "fmt": "TSV, gzip", "bytes": man_stats["bytes"], "n": plural(man_stats['n'], 'file') if man_stats["n"] else None,
         "schema": "#schema-manifest", "folder": True},
        {"path": "get/", "label": "get/<id>.sh", "what": "A fetch script per registry dataset: downloads the files from "
                                                         "the original host and checks their checksums.",
         "fmt": "bash", "bytes": get_stats["bytes"], "n": plural(get_stats['n'], 'script') if get_stats["n"] else None,
         "schema": None, "folder": True, "href": "get/index.html"},
    ]
    for f in site_files:
        f.setdefault("folder", False)
        f.setdefault("label", f["path"])

    snap_dir = REPO / "snapshots" / "metrics_core"
    per_ds = []
    for r in sorted(rows, key=lambda r: r["name"].lower()):
        i = r["id"]
        snap_rel = f"snapshots/metrics_core/{i}.parquet"
        card_rel = f"docs/datasets/{i}.md"
        per_ds.append({
            "id": i, "name": r["name"], "analysed": bool(r.get("analysed")),
            "ds": size_of(data_dir / "ds" / f"{i}.json"),
            "lock": size_of(data_dir / "locks" / f"{i}.lock.tsv"),
            "manifest": size_of(data_dir / "manifests" / f"{i}.files.tsv.gz"),
            "script": size_of(out / "get" / f"{i}.sh"),
            "snap": size_of(snap_dir / f"{i}.parquet") if is_tracked(snap_rel) else None, "snap_url": repo_raw(snap_rel),
            "card": is_tracked(card_rel), "card_url": repo_blob(card_rel),
        })
    snaps = [p for p in per_ds if p["snap"] is not None]

    # why a lock file or manifest is missing, from the registry status of those datasets
    by_id = {r["id"]: r for r in rows}
    no_lock = [by_id[p["id"]] for p in per_ds if p["lock"] is None]
    lock_only = [by_id[p["id"]] for p in per_ds if p["lock"] is not None and p["manifest"] is None]
    notes = []
    if no_lock:
        n_man = sum(1 for r in no_lock if r.get("status") == "manual")
        n_later = sum(1 for r in no_lock if r.get("status") in ("planned", "deferred"))
        why = [x for x in (plural(n_man, "manual download") if n_man else "",
                           f"{fmt_int(n_later)} planned or deferred" if n_later else "") if x]
        notes.append(f"No lock file for the {fmt_int(len(no_lock))} datasets rirdb does not download itself"
                     + (f" ({join_and(why)})" if why else "") + "; their pages say how to get them.")
    for r in lock_only:
        reason = ("the archive is broken upstream, so nothing could be extracted" if r.get("status") == "broken"
                  else "nothing has been extracted yet")
        notes.append(f"No manifest for {r['name']}: {reason}.")

    def repo_row(rel: str, what: str, fmt: str, pattern: str | None = None) -> dict | None:
        p = REPO / rel
        if pattern:
            fs = [f for f in sorted(p.glob(pattern)) if is_tracked(f.relative_to(REPO).as_posix())] if p.is_dir() else []
            if not fs:
                return None
            return {"path": rel, "label": f"{rel.rstrip('/')}/{pattern}", "what": what, "fmt": fmt,
                    "bytes": sum(f.stat().st_size for f in fs), "n": plural(len(fs), 'file'), "url": repo_url(rel)}
        if not is_tracked(rel):
            return None
        return {"path": rel, "label": rel, "what": what, "fmt": fmt, "bytes": size_of(p), "n": None,
                "url": repo_raw(rel) if p.is_file() else repo_url(rel)}

    repo_files = [x for x in [
        repo_row("snapshots/metrics_core/", "Per-IR metrics of every analysed dataset: core ISO parameters, grade, "
                 "flags, licence fields and room metadata, one row per IR.", "Parquet (ZSTD)", "*.parquet"),
        repo_row("snapshots/clap_map_v1/reference.parquet", "The frozen CLAP map: every mapped IR's position on both "
                 "maps and its neighbourhood radius.", "Parquet"),
        repo_row("snapshots/clap_map_v1/meta.json", "CLAP map settings: model and revision, UMAP parameters, "
                 "number of IRs, library versions.", "JSON"),
        repo_row("snapshots/clap_map_v1/baseline.json", "Realism and coverage of every real dataset against the "
                 "others, on both maps.", "JSON"),
        repo_row("reports/guide/guide.json", "The data behind the Reverb Dataset Field Guide.", "JSON"),
        repo_row("reports/guide/catalog.csv", "The Field Guide's catalogue (the site's catalog.csv adds columns).", "CSV"),
        repo_row("reports/overview/overview.json", "Corpus distributions for the nested subsets, per IR and per "
                 "space, and the signal-integrity summary.", "JSON"),
        repo_row("reports/corpus/duplicates.csv", "Exact duplicate IRs (identical decoded samples).", "CSV"),
        repo_row("reports/validation/openair_comparison.csv", "Per-space comparison with OpenAIR's published values.", "CSV"),
        repo_row("reports/validation/ace_comparison.csv", "Comparison with the ACE ground truth.", "CSV"),
        repo_row("registry/", "The registry, one folder per dataset: dataset.yaml (sources, file selections, licences, "
                 "counts, capture formats, room information, facts checked on the web), spaces.csv (spaces corrected "
                 "by hand, per-space licences), downloads.tsv (the originals of data/locks/) and files.tsv.gz "
                 "(the originals of data/manifests/).", "YAML, CSV, TSV", "*/*"),
        repo_row("registry/storage.tsv", "Where rirdb keeps each dataset at QMUL.", "TSV"),
        repo_row("docs/datasets/", "One Markdown card per registry dataset.", "Markdown", "*.md"),
    ] if x]

    ex = next((r["id"] for r in rows if r["id"] == "openair" and r.get("analysed")),
              next((r["id"] for r in rows if r.get("analysed")), "openair"))
    snippets = {
        "catalog": "\n".join([
            "import pandas as pd",
            "",
            f'site = "{SITE_URL}"',
            'cat = pd.read_csv(site + "data/catalog.csv")',
            "",
            "# datasets rirdb analysed whose licence allows commercial use and ML training",
            'pick = cat[(cat.analysed == "yes") & (cat.commercial_use == "yes") & (cat.ml_training == "allowed")]',
            'print(pick[["id", "name", "n_irs", "licence", "t30_p50_s"]])',
            "",
            'spaces = pd.read_csv(site + "data/spaces.csv")',
            'halls = spaces[spaces.category == "concert_hall"].sort_values("t30_s")',
        ]),
        "ds": "\n".join([
            "import json",
            "import urllib.request",
            "",
            "import pandas as pd",
            "",
            f'with urllib.request.urlopen("{SITE_URL}data/ds/{ex}.json") as r:',
            "    ds = json.load(r)",
            "",
            'params = pd.DataFrame(ds["analysis"]["params"])   # p5 ... p95 of every parameter',
            'files = pd.DataFrame(ds["lock"])                   # every file to download, with its checksum',
            'print(ds["analysis"]["grades"], len(ds["spaces"]), "spaces")',
        ]),
        "metrics": "\n".join([
            "import pandas as pd",
            "",
            f'm = pd.read_parquet("{repo_raw(f"snapshots/metrics_core/{ex}.parquet")}")',
            "",
            "# measured rooms, one representation per position, T30 valid from 125 Hz to 4 kHz",
            'rooms = m[(m.ir_kind == "room") & m.preferred & (m.grade == "A")]',
            'print(rooms.groupby("room_id")[["t30_mid", "edt_mid", "c80_mid", "drr_bb"]].median())',
        ]),
        "duckdb": "\n".join([
            f"git clone --depth 1 {REPO_URL}.git",
            f"cd {REPO_URL.rsplit('/', 1)[-1]}",
            "duckdb -c \"SELECT dataset_id, count(*) AS irs, median(t30_mid) AS t30_median_s",
            "           FROM 'snapshots/metrics_core/*.parquet'",
            "           WHERE ir_kind = 'room' AND preferred AND grade IN ('A', 'B')",
            "           GROUP BY dataset_id ORDER BY irs DESC\"",
        ]),
    }
    site.page("downloads/index.html", "downloads.html", title="Data files", nav=None,
              description="Every machine-readable file rirdb offers: the catalogue, spaces, per-dataset analysis, lock "
                          "files, manifests and per-IR metrics, with sizes, columns and loading examples.",
              num=num, site_files=site_files, per_ds=per_ds, n_snaps=len(snaps), pd_notes=notes, wbr=wbr,
              snap_bytes=sum(p["snap"] for p in snaps) if snaps else None,
              n_cards=sum(1 for p in per_ds if p["card"]), repo_files=repo_files,
              cat_schema=cat_schema, sp_schema=sp_schema, lock_cols=lock_cols, man_cols=man_cols,
              site_keys=[(k, Markup(SITE_JSON_DOC.get(k, ""))) for k in d.keys()],
              row_keys=sorted((rows[0] if rows else {}).keys()),
              ds_keys=list(DS_JSON_DOC.items()), core_cols=core_cols, clap_cols=CLAP_REF_COLUMNS, snippets=snippets)


# ----------------------------------------------------------------------------- about

def render_about(site: Site, num: dict, findings: dict) -> None:
    d = site.data
    gen = d.get("generated") or ""
    year = gen[:4] or None
    built = gen[:10]
    commit = f", commit {d['git']}" if d.get("git") else ""
    bib_lines = [
        "@misc{ibnyahya_rirdb,",
        "  author       = {Ibnyahya, Ilias},",
        "  title        = {rirdb: a checked catalogue and analysis of public room impulse response datasets},",
        f"  year         = {{{year}}}," if year else "",
        f"  howpublished = {{\\url{{{REPO_URL}}}}},",
        f"  note         = {{Website {SITE_URL}, built {built}{commit}}}",
        "}",
    ]
    bib = "\n".join(line for line in bib_lines if line)
    plain_cite = (f"Ilias Ibnyahya{f' ({year})' if year else ''}. rirdb: a checked catalogue and analysis of public "
                  f"room impulse response datasets. {REPO_URL} (website {SITE_URL}, built {built}{commit}).")
    offline = next((r for r in findings["rows"] if r["section"] == "data" and r["subject"].startswith("OpenAIR")
                    and "offline" in (r["raw"][0] if r["raw"] else "").lower()), None)
    offline_text = lower_first(offline["cells"][0]) + end_stop(offline["raw"][0]) if offline else None
    # the largest low-band C80 bias against OpenAIR's published values (analyzer-v1.md, known limitation)
    oa = [s for s in ((d.get("validation") or {}).get("openair") or {}).get("summary") or []
          if s.get("metric") == "c80" and s.get("median_diff") is not None]
    c80_low = min(oa, key=lambda s: s["median_diff"]) if oa else None
    if c80_low and c80_low["median_diff"] >= 0:
        c80_low = None
    site.page("about/index.html", "about.html", title="About rirdb", nav="about",
              description="What rirdb is and why, who made it, how to cite it, how to contribute, and how this site "
                          "is built.",
              num=num, bib=bib, plain_cite=plain_cite, offline_text=offline_text, c80_low=c80_low,
              not_analysed=join_and(not_analysed_parts(d.get("datasets") or [])),
              plan_url=repo_url(HOSTING_PLAN) or repo_blob(HOSTING_PLAN),
              registry_url=repo_blob("src/rirdb/registry.py"),
              upstream_url=UPSTREAM_URL, issues_url=f"{REPO_URL}/issues", has_licence=(REPO / "LICENSE").exists(),
              build_cmds="\n".join([
                  "# collect the data from the index and metrics, then build reports/site/",
                  "sbatch jobs/quick_cpu.sh pixi run rirdb dev report site",
                  "# rebuild the pages only, from reports/site/data/",
                  "sbatch jobs/quick_cpu.sh pixi run rirdb dev report site --reuse-data",
                  "# check links, anchors, headings and leaked values",
                  "sbatch jobs/quick_cpu.sh pixi run rirdb dev report site-check",
              ]))


# ----------------------------------------------------------------------------- 404

def render_404(site: Site) -> None:
    """Rendered with absolute links: GitHub Pages serves 404.html for a missing page at any depth. The stylesheet
    is also inlined, so the page reads right before the site is deployed or when opened from disk."""
    css_file = HERE / "static" / "site.css"
    css = css_file.read_text().replace("</", "<\\/") if css_file.exists() else ""
    html = site.env.get_template("404.html").render(
        root=SITE_URL, rel="404.html", title="Page not found", description="", nav=None, jsonld=None,
        data=site.data, generated=site.data.get("generated"), canonical=SITE_URL + "404.html", inline_css=Markup(css))
    site.write("404.html", html)


# ----------------------------------------------------------------------------- entry point

def render(site: Site) -> None:
    num = corpus_numbers(site)
    names = names_map(site)
    find_home = parse_findings("", names)
    find_methods = parse_findings("../", names)
    render_home(site, num, find_home)
    render_methods(site, num)
    render_findings(site, num, find_methods)
    render_downloads(site, num)
    render_about(site, num, find_methods)
    render_404(site)
