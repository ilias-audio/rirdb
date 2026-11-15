"""Get the data: get/index.html, a fetch script for every registry dataset, the lock files and the manifests.

    get/index.html                     how to get any dataset, in order of effort, and one table of all of them
    get/<id>.sh                        a standalone bash script per registry dataset (not in the sitemap)
    data/locks/<id>.lock.tsv           copies of registry/<id>/downloads.tsv
    data/manifests/<id>.files.tsv.gz   copies of registry/<id>/files.tsv.gz

The scripts are generated from registry/<id>/dataset.yaml (`load_registry()`) and registry/<id>/downloads.tsv.
They download every locked file from the original source with curl (resumable, retried), check it with
`md5sum -c` or `sha256sum -c`, skip files that already verify, and print how to extract the archives the
way fetch/extract.sh does, so the extracted paths match the manifest. Other tools only where the source
needs them: wget (a mirrored web folder), git (GitHub, GIT=1), huggingface-cli or git-lfs (Hugging Face),
jq (a Zenodo record that rirdb has not resolved yet). Every script is checked with `bash -n` at build time.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import textwrap
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from markupsafe import Markup, escape

from rirdb import paths
from rirdb.registry import load_registry
from rirdb.report.site import charts
from rirdb.report.site.core import SITE_URL, SOURCE_LABELS, doi_url, fmt_bytes, fmt_int, record_url, repo_blob
from rirdb.report.site.data import read_tsv
from rirdb.report.site.pages_dataset import (ACCESS_TEXT, HOST_ISSUE_RE, LICENCE_CLASSES, PERM_FALLBACK, PERM_TEXT,
                                             STATUS_TEXT, WAVE_TEXT, inline_md, manual_reason, public_record,
                                             refine, spdx_label)

# fetch/extract.sh: the archives it extracts (split-zip parts .z01... are joined with their .zip)
EXTRACT_RE = re.compile(r"\.(zip|tar|tar\.gz|tgz|tbz2|tar\.bz2|tar\.xz)$")
SPLIT_RE = re.compile(r"\.z\d\d$")
STEM_EXTS = (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".tar", ".zip")
# a zip over 4 GiB needs ZIP64 records; some are written without them (RSoANU) or trip unzip's zip-bomb check (MYRiAD v1)
ZIP64_BYTES = 4 * 1024 ** 3
# files a Wayback snapshot page links to (the archived Fokke collection: StuffIt and zip archives)
WAYBACK_EXTS = "sit|zip|hqx|sea|wav|aif|aiff"

TIER = {
    "store": ("Group store", "permanent, group read-only"),
    "inplace": ("Group store, used in place", "permanent; added by another group member"),
    "scratch": ("Scratch", "working copy; purged after a period without changes"),
}
KIND_DO = {
    "zenodo": ("Downloads every file in the lock from Zenodo and checks the md5 that Zenodo publishes. Without a "
               "lock (a record rirdb has not resolved yet) it lists the record's files from the Zenodo API first.",
               "curl, md5sum; jq when there is no lock"),
    "figshare": ("Downloads every file in the lock from Figshare and checks the md5 that Figshare publishes.",
                 "curl, md5sum"),
    "depositonce": ("Downloads every file in the lock from DepositOnce (TU Berlin) and checks the md5 that the "
                    "repository publishes.", "curl, md5sum"),
    "http": ("Downloads the files in the lock from the lab or company server and checks the sha256 that rirdb "
             "recorded on its first download: these hosts publish no checksums.", "curl, sha256sum"),
    "github": ("Downloads GitHub's archive of the pinned commit and checks its sha256. GitHub's archive server "
               "cannot resume, so an interrupted archive is downloaded again from the start. With GIT=1 it clones "
               "the pinned commit instead and checks every file against rirdb's manifest. Without a lock it clones "
               "the tip of the branch.",
               "curl, sha256sum; git and git-lfs for GIT=1. Without a lock: git, and git-lfs when the repository "
               "uses it"),
    "hf": ("Lists the repository's files and their total size from the Hugging Face API, then downloads the "
           "repository at the given revision with huggingface-cli, or with git and git-lfs (git-lfs checks the "
           "sha256 of every large file).", "curl, jq; huggingface-cli, or git with git-lfs"),
    "wget_mirror": ("Mirrors the web folder with wget, then checks every file against rirdb's manifest (one "
                    "sha256 per file).", "wget, sha256sum"),
    "wayback": ("Reads the archived page on the Wayback Machine and downloads every file it links to. There is no "
                "reference checksum: the script records the sha256 of what it received, and on the next run "
                "downloads again a file that no longer matches it.", "curl, sha256sum"),
    "manual": ("Prints the registry's instructions and the homepage, then stops without downloading. Where rirdb "
               "adopted a copy, it checks your copy against rirdb's checksums.", "bash; sha256sum to check a copy"),
}
# the commands the picker on get/index.html shows, per script mode (table_row "pick"); __ID__ is the dataset id
DRY = "DRY_RUN=1 bash __ID__.sh"
RUN = "bash __ID__.sh ~/datasets"
PICK_CMDS = {
    "files": ["# list the files without downloading", DRY,
              "# download into ~/datasets/__ID__/ and check every file", RUN],
    "mirror": ["# show what it would mirror, and the size of rirdb's copy", DRY,
               "# mirror into ~/datasets/__ID__/ and check every file against rirdb's manifest", RUN],
    "wayback": ["# list the files without downloading", DRY,
                "# download into ~/datasets/__ID__/ (no reference checksums: it records the sha256 of each file)", RUN],
    "hf": ["# list the files and their total size without downloading", DRY,
           "# download into ~/datasets/__ID__/ (no rirdb checksums yet)", RUN],
    "git_live": ["# show where it would clone", DRY,
                 "# clone the tip of the branch into ~/datasets/__ID__/ (no pinned commit to check against)", RUN],
    "manual": ["# prints how to get the files (no scripted download)", "bash __ID__.sh"],
    "manual_lock": ["# prints how to get the files; with a copy in ~/datasets/__ID__/, checks it against",
                    "# rirdb's checksums", RUN],
}
PICK_CMDS["github"] = PICK_CMDS["zenodo_live"] = PICK_CMDS["files"]

LOCK_COLUMNS = [
    ("relpath", "Path of the file inside the dataset folder. Datasets drawn from several records prefix it with "
                "the first 8 characters of the record id. <code>@mirror</code> marks a mirrored web folder."),
    ("url", "Where the file is downloaded from. <code>inplace:&lt;path&gt;</code> is a copy rirdb used where it "
            "already was; <code>manual:</code> a file added by hand."),
    ("size", "Size in bytes, as the host states it; <code>-</code> when the server does not say."),
    ("algo", "<code>md5</code> or <code>sha256</code>; <code>manifest</code> for a mirrored web folder."),
    ("checksum", "The checksum: the provider's (Zenodo, Figshare, DepositOnce md5), or the sha256 rirdb computed "
                 "on its first download where the host publishes none."),
    ("provider_version", "What was pinned: <code>zenodo:&lt;record&gt;</code>, <code>figshare:&lt;article&gt;:v&lt;n&gt;"
                         "</code>, <code>depositonce:&lt;item&gt;</code>, <code>github:&lt;repo&gt;@&lt;commit&gt;</code>, "
                         "or the server's ETag and Last-Modified for plain HTTP."),
    ("resolved_at", "When rirdb resolved the source (UTC)."),
]
MANIFEST_COLUMNS = [
    ("relpath", "Path of the file after extraction, relative to the dataset folder: each archive is extracted in "
                "place, or into a folder named after it when the dataset has several archives."),
    ("size", "Size in bytes."),
    ("sha256", "sha256 of the extracted file."),
]


# ----------------------------------------------------------------------------- small helpers

def q(s) -> str:
    return shlex.quote(str(s))


def lc_first(s: str) -> str:
    return s if s[:2].isupper() else s[:1].lower() + s[1:]


def comment(label: str, text: str, width: int = 100) -> list[str]:
    """`# Label:     text` wrapped at `width`, continuation lines aligned under the text."""
    head = f"# {label + ':' if label else '':<11} "
    lines = _wrap(text, width - len(head)) or [""]
    return [head + lines[0]] + ["#" + " " * (len(head) - 1) + ln for ln in lines[1:]]


def wrap_text(text: str, indent: str = "  ", width: int = 96) -> list[str]:
    return [indent + ln for ln in _wrap(text, width - len(indent))]


def _wrap(text: str, width: int) -> list[str]:
    """Wrap plain text; a number and its unit ("5.3 GB") stay on one line."""
    text = " ".join(str(text).replace("`", "").split())
    text = re.sub(r"(\d) (B|kB|MB|GB|TB|files?|IRs?|rooms?)\b", "\\1\u00a0\\2", text)
    lines = textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False)
    return [ln.replace("\u00a0", " ") for ln in lines]


def archive_stem(rel: str) -> str:
    for ext in STEM_EXTS:
        if rel.endswith(ext):
            return rel[: -len(ext)]
    return rel


def size_of(r: dict) -> int | None:
    s = r.get("size")
    return int(s) if s is not None and str(s).isdigit() else None


def read_lock(i: str) -> list[dict]:
    f = paths.downloads_tsv(i)
    return read_tsv(f) if f.exists() else []


def read_manifest(i: str) -> list[dict]:
    f = paths.files_tsv(i)
    return read_tsv(f) if f.exists() else []


def safe_name(rel: str) -> None:
    """Checksum lines cannot hold newlines or backslashes (md5sum escapes them); the scripts assume neither."""
    if "\n" in rel or "\\" in rel or "\t" in rel:
        raise ValueError(f"unsupported character in file name {rel!r}")


# ----------------------------------------------------------------------------- licence, sizes, checksums

def licence_info(row: dict) -> dict:
    lic, sv = row.get("licence") or {}, row.get("survey") or {}
    spdx = lic.get("spdx")
    label = sv.get("licence_label") or spdx_label(spdx)
    cls = sv.get("licence_class") or lic.get("class") or "unspecified"
    r = refine(cls, sv.get("commercial") or lic.get("commercial"), sv.get("training") or lic.get("training"),
               sv.get("redistribute") or lic.get("redistribute"), spdx, label)
    perms = []
    for k in ("commercial", "training", "redistribute"):
        perms.append((PERM_TEXT[k].get(r.get(k) or "") or (None, PERM_FALLBACK[k]))[1])
    cls_label, cls_text = LICENCE_CLASSES.get(cls, (cls, ""))
    if cls == "unspecified":
        # the label may name a code licence or a freeware note, so do not repeat "no licence anywhere"
        line = f"{label}. Without a licence for the data, all rights are reserved by default: ask the authors first."
    elif cls == "mixed":
        line = f"{label}. {cls_text}"      # the label already says it; the three permissions would all be "ask"
    else:
        line = f"{label} ({cls_label.lower()}): " + "; ".join(lc_first(p) for p in perms) + "."
    return {"label": label, "cls": cls, "cls_label": cls_label, "line": line, "spdx": spdx}


CHECKSUM_KINDS = {
    "provider": ("md5 from the provider", "md5 as published by the provider."),
    "first": ("sha256 from rirdb's first download",
              "sha256 computed by rirdb on its first download: the host publishes none."),
    "per_file": ("sha256 per file", "A web folder: rirdb's manifest holds the sha256 of every mirrored file."),
    "adopted": ("sha256 of rirdb's copy", "sha256 of the copy rirdb adopted (no public download to pin)."),
    "none": ("none yet", "No lock file: rirdb has not resolved this source yet."),
}


def checksum_kind(row: dict, lock: list[dict]) -> tuple[str, str, str]:
    """(key, short label, longer text) of what checks the downloads."""
    if not lock:
        key = "none"
    elif lock[0].get("relpath") == "@mirror":
        key = "per_file"
    elif {r.get("url", "").split(":", 1)[0] for r in lock} & {"inplace", "manual"}:
        key = "adopted"
    else:
        algos = sorted({r.get("algo") for r in lock if r.get("checksum") not in (None, "", "-")})
        if algos == ["md5"] and (row.get("files") or {}).get("provider_checksum"):
            key = "provider"
        elif algos == ["sha256"]:
            key = "first"
        else:
            return "other", ", ".join(algos) or "none", ""
    pub = public_record(row, lock) if key == "adopted" else None
    if pub:
        return key, CHECKSUM_KINDS[key][0], (f"sha256 of every extracted file in the group-store copy rirdb uses in "
                                             f"place; {pub} holds the same files in archives.")
    return (key, *CHECKSUM_KINDS[key])


def lock_bytes(lock: list[dict]) -> tuple[int, int]:
    """(bytes of the rows with a stated size, number of rows without one); @mirror rows do not count."""
    known, unknown = 0, 0
    for r in lock:
        if r.get("relpath") == "@mirror":
            continue
        s = size_of(r)
        if s is None:
            unknown += 1
        else:
            known += s
    return known, unknown


def host_issues(site, i: str) -> list[dict]:
    det = site.dataset(i)
    out = []
    for f in det.get("findings") or []:
        if f.get("section") != "data" or not HOST_ISSUE_RE.search(f"{f.get('finding')} {f.get('evidence') or ''}"):
            continue
        f = dict(f)
        # a row with an empty Evidence cell ("| |") reaches site.json as evidence "| <handling>", handling None
        ev = (f.get("evidence") or "").strip()
        if ev.startswith("|") and not f.get("handling"):
            f["evidence"], f["handling"] = None, ev.lstrip("| ").strip() or None
        out.append(f)
    return out


def archived_copies(notes: str | None, lock: list[dict]) -> dict[str, str]:
    """{relpath: Wayback URL} for locked files whose archived copy the registry notes name as a raw `id_` capture
    of the same URL (Aachen AIR: the institute host refused connections; the archived zip matches the lock). The
    script tries that copy when the original URL fails, and the lock's checksum still checks what arrives."""
    out = {}
    for m in re.finditer(r"https://web\.archive\.org/web/\d+id_/(\S+)", notes or ""):
        wb, orig = m.group(0).rstrip(".,;)"), m.group(1).rstrip(".,;)").split("://", 1)[-1]
        for r in lock:
            if (r.get("url") or "").split("://", 1)[-1] == orig and r.get("checksum") not in (None, "", "-"):
                out[r["relpath"]] = wb
    return out


# ----------------------------------------------------------------------------- the fetch scripts

def mode_of(row: dict, lock: list[dict]) -> str:
    kind = row["source_kind"]
    if kind == "manual" or any(r.get("url", "").startswith(("inplace:", "manual:")) for r in lock):
        return "manual"
    if kind == "wget_mirror":
        return "mirror"
    if kind == "github":
        return "github" if lock else "git_live"
    if kind == "hf":
        return "hf"
    if lock:
        return "files"
    if kind == "zenodo":
        return "zenodo_live"
    if kind == "wayback":
        return "wayback"
    return "info"


def extract_lines(lock: list[dict]) -> list[str]:
    """Commands that extract the archives as fetch/extract.sh does: in place when the dataset has one archive,
    into a folder named after each archive when it has several (so paths match the manifest)."""
    size = {r["relpath"]: size_of(r) or 0 for r in lock}
    arch = [r for r in lock if EXTRACT_RE.search(r["relpath"])]
    multi = len(arch) > 1
    out, big = [], []
    for r in arch:
        a = r["relpath"]
        stem = archive_stem(a)
        dest = stem if multi else "."
        if a.endswith(".zip"):
            base = a[:-4]
            parts = sorted(p for p in size if SPLIT_RE.search(p) and p[:-4] == base)
            total = size[a] + sum(size[p] for p in parts)
            env = "UNZIP_DISABLE_ZIPBOMB_DETECTION=TRUE " if total > ZIP64_BYTES else ""
            target = base + ".joined.zip" if parts else a
            if parts:
                out.append(f"zip -s 0 {q(a)} --out {q(target)}    # joins the split parts "
                           f"{', '.join(p[len(base):] for p in parts)} first")
            out.append(f"{env}unzip -q {q(target)} -d {q(dest)}")
            if total > ZIP64_BYTES:
                big.append((target, stem, dest))
        else:
            out.append(("" if dest == "." else f"mkdir -p {q(dest)} && ") + f"tar -xf {q(a)} -C {q(dest)}")
    if big:
        # comments, so the block can be pasted into a shell as it is: these run only when unzip fails
        out.append("")
        out.append("# Only if unzip fails on a zip over 4 GiB that passed its checksum: try 7-Zip, and if that")
        out.append("# fails too, rebuild the zip directory from the local headers (rirdb needed this for")
        out.append("# RSoANU's em32 zip):")
        if len(big) == 1:
            a, stem, dest = big[0]
            out.append(f"#   7z x -y -o{q(dest)} {q(a)}")
            out.append(f"#   zip -FF {q(a)} --out {q(stem + '.repaired.zip')} && unzip -q {q(stem + '.repaired.zip')} -d {q(dest)}")
        else:
            out.append("#   7z x -y -o<folder> <name>.zip")
            out.append("#   zip -FF <name>.zip --out <name>.repaired.zip && unzip -q <name>.repaired.zip -d <folder>")
    return out


def script_context(site, row: dict, ds, lock: list[dict], manifest: list[dict]) -> dict:
    i = row["id"]
    mode = mode_of(row, lock)
    lic = licence_info(row)
    src = ds.source
    det_url = f"{SITE_URL}d/{i}.html"
    manifest_url = f"{SITE_URL}data/manifests/{i}.files.tsv.gz"
    lock_url = f"{SITE_URL}data/locks/{i}.lock.tsv"
    resolved = max((r.get("resolved_at") or "" for r in lock), default="")[:10] or None
    known, unknown = lock_bytes(lock)
    n_files = sum(1 for r in lock if r.get("relpath") != "@mirror")
    mf = row.get("manifest") or {}

    # ---- header
    h: list[str] = []
    h += comment("Licence", lic["line"])
    if ds.homepage:
        h += comment("Homepage", ds.homepage)
    if row.get("doi"):
        h += comment("DOI", doi_url(row["doi"]))
    if row.get("paper"):
        h += comment("Paper", f"{row['paper']['title']}, {row['paper']['url']}")
    kind_label = SOURCE_LABELS.get(src.kind, src.kind)
    if src.kind in ("zenodo", "figshare", "depositonce") and src.records:
        recs = ", ".join(f"{r} ({record_url(src.kind, r)})" for r in src.records)
        h += comment("Source", f"{kind_label}, {'records' if len(src.records) > 1 else 'record'} {recs}")
    elif src.kind == "github":
        if lock:
            pv = lock[0].get("provider_version") or ""
            h += comment("Source", f"GitHub repository {pv.removeprefix('github:')} (the commit rirdb pinned)")
        else:
            tip = "branch tip" if "@" in (src.records[0] if src.records else "") else "tip of the default branch"
            h += comment("Source", f"GitHub repository {', '.join(src.records)} ({tip}; rirdb has not pinned a commit)")
    elif src.kind == "hf":
        h += comment("Source", f"Hugging Face dataset {', '.join(src.records)} (repository@revision)")
    elif src.kind in ("http", "wget_mirror", "wayback"):
        h += comment("Source", f"{kind_label}: {' '.join(src.urls)}")
    else:
        h += comment("Source", "manual download (see the instructions printed below)")
    if lock and mode != "mirror":
        size_txt = fmt_bytes(known) + (f" plus {unknown} file{'s' if unknown > 1 else ''} of unstated size" if unknown else "")
        if known == 0 and unknown:
            size_txt = "size not stated by the server"
        h += comment("Files", f"{fmt_int(n_files)} file{'s' if n_files != 1 else ''}, {size_txt}; resolved on "
                              f"{resolved} (rirdb lock file {lock_url})")
    elif mode == "mirror":
        h += comment("Files", f"a web folder, mirrored file by file; rirdb's copy of {resolved} holds "
                              f"{fmt_int(mf.get('n_files'))} files, {fmt_bytes(mf.get('bytes'))}")
    else:
        h += comment("Files", "no rirdb lock file yet: " + {
            "zenodo_live": "the script lists the record's files from the Zenodo API when it runs.",
            "git_live": "the script clones the current tip of the "
                        + ("branch." if "@" in (src.records[0] if src.records else "") else "default branch."),
            "hf": "the script lists the repository's files and their total size from the Hugging Face API, "
                  "then downloads the repository at the revision above (DRY_RUN=1 only lists them).",
            "wayback": "the script lists the files the archived page links to when it runs.",
        }.get(mode, "see the instructions below."))
    if mf and mode != "mirror":
        h += comment("Extracted", f"{fmt_int(mf.get('n_files'))} files, {fmt_bytes(mf.get('bytes'))} in rirdb's copy "
                                  f"(manifest with one sha256 per file: {manifest_url})")
    _, ck_short, ck_text = checksum_kind(row, lock)
    if not lock:      # say what checks the files instead, if anything
        ck_text = {
            "zenodo_live": "no lock file yet: the md5 of each file comes from the Zenodo record when the script runs.",
            "git_live": "no lock file: the clone is not pinned to a commit, so there is nothing to compare with.",
            "hf": "no lock file yet. Cloned with git, git-lfs checks each large file against the sha256 the "
                  "repository holds.",
            "wayback": "none published: the script records the sha256 of each file it receives.",
            "manual": "no lock file: rirdb has not adopted a copy yet.",
        }.get(mode, ck_text)
        ck_text = ck_text[:1].upper() + ck_text[1:]
    if ck_text:
        h += comment("Checksums", ck_text)
    h += comment("Status", f"{row['status']}. {STATUS_TEXT.get(row['status'], '')} Access: {row['access']}."
                           + (f" {ACCESS_TEXT[row['access']]}" if row["access"] in ("open", "registration") else ""))
    h += comment("Details", det_url)
    h += comment("Generated", f"{site.data.get('generated')} by rirdb from registry/{i}/dataset.yaml"
                              + (f" and registry/{i}/downloads.tsv" if lock else ""))

    notes = []
    if row["status"] == "broken":
        notes.append(f"BROKEN: {ds.notes or 'the published files cannot be used as they are.'}")
    elif ds.notes:
        notes.append(ds.notes)
    for f in host_issues(site, i):
        if row["status"] == "broken" and "truncated" in f["finding"]:
            continue
        notes.append(f"Host problem found by rirdb: {f['finding']}"
                     + (f" ({f['evidence']})" if f.get("evidence") else ""))
    for n in notes:
        h += ["#"] + comment("Note", n)

    # ---- run instructions
    run = comment("Run", f"bash {i}.sh [target_dir]    (target_dir defaults to the current directory)")
    if mode == "manual":
        # the same words as the dataset page's "Download with a script" card
        run += comment("", f"Prints how to get the files and the homepage, then stops: "
                           f"{manual_reason(site.dataset(i), row, 'see the note above')}."
                           + (f" If <target_dir>/{i}/ already holds a copy, checks that copy against rirdb's "
                              "checksums and leaves it untouched." if lock else ""))
    else:
        again = {
            "files": "resumes partial files and skips files that already verify",
            "github": "skips a file that already verifies; a partial download starts again from the beginning, "
                      "because GitHub's archive server cannot resume",
            "zenodo_live": "resumes partial files and skips files that already verify",
            "wayback": "resumes partial files and skips files that still match the sha256 recorded when they "
                       "were received",
            "mirror": "fetches only the files that are missing",
            "git_live": "updates the clone",
            "hf": "continues where the last run stopped",
        }.get(mode, "is safe")
        run += comment("", f"Files go to <target_dir>/{i}/. Running it again {again}. "
                           f"DRY_RUN=1 bash {i}.sh shows what it would do.")
    if mode == "github":
        run += comment("", f"GIT=1 bash {i}.sh clones the pinned commit with git instead of downloading GitHub's archive.")
    needs = {
        "files": "bash, curl, coreutils (md5sum, sha256sum)",
        "github": "bash, curl, coreutils; git (and git-lfs if the repository uses it) for GIT=1",
        "git_live": "bash, git; git-lfs if the repository uses it (curl, if present, checks that before cloning)",
        "hf": "bash, curl, jq; hf or huggingface-cli (pip install huggingface_hub), or git with git-lfs",
        "zenodo_live": "bash, curl, jq, coreutils (md5sum)",
        "mirror": "bash, wget, curl, gzip, coreutils (sha256sum)",
        "wayback": "bash, curl, coreutils (sha256sum); unar or lsar to open StuffIt (.sit) archives",
        "manual": "bash" + (", coreutils (sha256sum) to check a copy" if lock else ""),
        "info": "bash",
    }[mode]
    run += comment("Needs", needs)
    if mode in ("files", "github", "zenodo_live", "wayback"):
        run += comment("Archives", "not extracted: the commands to extract them are printed at the end (they "
                                   "are also in next_steps below).")

    # ---- checksum lists and files
    md5, sha = [], []
    files = []
    alt = archived_copies(ds.notes, lock) if mode == "files" else {}
    for r in lock:
        rel = r.get("relpath") or ""
        if rel == "@mirror":
            continue
        safe_name(rel)
        ck = r.get("checksum")
        if ck and ck != "-":
            (md5 if r.get("algo") == "md5" else sha).append(f"{ck}  {rel}")
        files.append({"q_rel": q(rel), "size": size_of(r) if size_of(r) is not None else "-", "q_url": q(r.get("url")),
                      "q_alt": q(alt[rel]) if rel in alt else ""})
    tools = sorted({"md5sum" for _ in md5[:1]} | {"sha256sum" for _ in sha[:1]})

    # ---- what comes after the download
    nxt: list[str] = []
    if mode in ("files", "github"):
        ex = extract_lines(lock)
        if ex and row["status"] == "broken":
            nxt.append("Archives are not extracted. rirdb could not extract this one (see the warning below);")
            nxt.append("to try anyway, run in the dataset folder:")
            nxt += [("    " + e) if e else "" for e in ex]
        elif ex and (mf or mode == "github"):
            nxt.append("Archives are not extracted. To extract them the way rirdb does (the paths then match")
            nxt.append("rirdb's manifest of extracted files), run in the dataset folder:")
            nxt += [("    " + e) if e else "" for e in ex]
        elif ex:
            nxt.append("Archives are not extracted. To extract them, run in the dataset folder:")
            nxt += [("    " + e) if e else "" for e in ex]
        else:
            nxt.append("Nothing to extract: the files are ready to use.")
        if mode == "github":
            nxt.append("")
            nxt.append("Then check every extracted file against rirdb's manifest (written by this script):")
            nxt.append("    sha256sum -c --quiet checksums-files.sha256")
        elif mf:
            nxt.append("")
            nxt.append(f"Extracted, rirdb's copy holds {fmt_int(mf.get('n_files'))} files, {fmt_bytes(mf.get('bytes'))}. "
                       "To check every extracted file")
            nxt.append("against rirdb's manifest (one sha256 per file; files you do not have are skipped), run in the")
            nxt.append("dataset folder:")
            nxt.append(f"    curl -fsSL {q(manifest_url)} | gunzip \\")
            nxt.append("      | awk -F'\\t' 'NR > 1 { print $3 \"  \" $1 }' | sha256sum -c --ignore-missing --quiet -")
    if mode == "wayback":
        nxt.append("The archives are StuffIt (.sit) or zip files. Open .sit files with The Unarchiver's command-line")
        nxt.append("tools: unar <file>.sit (apt install unar, or brew install unar). Zip files: unzip -q <file>.zip.")
    if mode == "zenodo_live":
        nxt.append("Archives are not extracted. Extract each one in the dataset folder: unzip -q <file>.zip, or")
        nxt.append("tar -xf <file>.tar.gz (also .tgz, .tbz2, .tar.xz). With several archives, rirdb extracts each")
        nxt.append("into a folder named after it: unzip -q <name>.zip -d <name>.")
    if row["status"] == "broken":
        nxt.append("")
        nxt += wrap_text(f"Warning: this dataset is marked broken in rirdb. {ds.notes or ''}", indent="")
    if nxt:
        nxt.append("")
    nxt += wrap_text(f"Licence: {lic['line']}", indent="")
    nxt.append(f"Dataset page (contents, analysis, known issues, how to cite): {det_url}")

    # ---- mode-specific values
    ctx = {
        "id": i, "name": row["name"], "mode": mode, "header": h, "run": run, "next": nxt,
        "title": f"# {row['name']} ({i}): fetch script generated by rirdb",
        "site_url": SITE_URL, "det_url": det_url, "md5": md5, "sha": sha, "files": files, "tools": tools,
        "n_files": len(files), "total": known if not unknown else "", "lock_url": lock_url,
        "manifest_url": manifest_url, "q_manifest_url": q(manifest_url), "status": row["status"],
        "manifest_n": mf.get("n_files"), "manifest_bytes_text": fmt_bytes(mf.get("bytes")) if mf else "",
        "has_alt": bool(alt),
        "q_fail_hint": q("The original host may be offline. For the files that failed there, the script also tried "
                         "the Wayback Machine's copy, which the registry notes name (same sha256 as rirdb's lock). "
                         "The Wayback Machine is slow: run the script again to resume.") if alt else "''",
        "q_intro": q(f"{row['name']} ({i}): {len(files)} file{'s' if len(files) != 1 else ''}, "
                     + (fmt_bytes(known) if known else "size not stated")
                     + (f" plus {unknown} of unstated size" if known and unknown else "")),
    }
    if mode == "github":
        pv = (lock[0].get("provider_version") or "").removeprefix("github:")
        repo, _, commit = pv.partition("@")
        ctx.update(repo=q(repo), commit=q(commit), tree=q(f"{repo.split('/')[-1]}-{commit}"),
                   tree_files=[f"{m['sha256']}  {m['relpath']}" for m in manifest],
                   q_fail_hint=q(f"GitHub builds its archives on demand and their bytes can change while the files stay "
                             f"the same: GIT=1 bash {i}.sh clones the pinned commit and checks every file instead."))
        for m in manifest:
            safe_name(m["relpath"])
    elif mode == "git_live":
        repo, _, ref = src.records[0].partition("@")
        ctx.update(repo=q(repo), ref=q(ref), tree=q(repo.split("/")[-1]))  # no ref: the default branch
    elif mode == "hf":
        repo, _, rev = src.records[0].partition("@")
        ctx.update(repo=q(repo), rev=q(rev or "main"), tree=q(repo.split("/")[-1]))
    elif mode == "zenodo_live":
        ctx.update(records=" ".join(src.records), multi=len(src.records) > 1,
                   records_url=" ".join(record_url("zenodo", r) for r in src.records))
    elif mode == "mirror":
        url = src.urls[0]
        cut = len([p for p in urlparse(url).path.split("/") if p])
        ctx.update(url=q(url), accept=q(",".join(src.accept)) if src.accept else "", cut=cut,
                   reject=q(r"/(examples|images)/|\?C="))
    elif mode == "wayback":
        page = src.urls[0]
        m = re.search(r"web\.archive\.org/web/(\d+)", ds.homepage or "")
        ctx.update(page=q(page), snapshot=m.group(1) if m else "2", exts=WAYBACK_EXTS)
    if mode in ("manual", "info"):
        txt = []
        why = manual_reason(site.dataset(i), row, "see the dataset page") if mode == "manual" else "rirdb has no scripted download for it"
        txt += wrap_text(f"{row['name']}: this script prints how to get the files and the homepage, then stops: {why}.",
                         indent="")
        txt.append("")
        txt.append("How to get it (from the rirdb registry):")
        txt += wrap_text(src.instructions or "No instructions recorded: see the homepage.", indent="  ")
        if ds.homepage:
            txt.append(f"Homepage: {ds.homepage}")
        txt.append("")
        txt.append("What it holds:")
        txt += wrap_text(row.get("content") or "", indent="  ")
        n_ir, n_room = row.get("expected_irs"), row.get("expected_rooms")
        exp = [x for x in (f"{fmt_int(n_ir)} IR{'s' if n_ir != 1 else ''}" if n_ir else "",
                           f"{fmt_int(n_room)} room{'s' if n_room != 1 else ''}" if n_room else "",
                           row.get("expected_note") or "") if x]
        if exp:
            txt += wrap_text("Expected, as the authors state it: " + "; ".join(exp), indent="  ")
        if re.search(r"adopt_|RIRDB_ROOT|sbatch", src.instructions or ""):
            txt.append("")
            txt += wrap_text("Steps that run sbatch or mention $RIRDB_ROOT are for the rirdb pipeline on "
                             "Apocrita. Outside it, keep the files wherever you like.", indent="")
        txt.append("")
        txt += wrap_text(f"Licence: {lic['line']}", indent="")
        txt.append(f"Dataset page: {det_url}")
        ctx["manual_text"] = txt
        if lock:
            stor = row.get("storage") or {}
            pub = public_record(row, lock)
            ctx["manual_lock"] = True
            ctx["manual_lock_note"] = wrap_text(
                f"rirdb's copy ({fmt_int(n_files)} files, {fmt_bytes(known)}, adopted on {resolved}"
                + (f"; kept at {stor['path']} on Apocrita" if stor.get("path") else "")
                + ") is recorded file by file in this script, with a sha256 per file. "
                + (f"To check a download from {pub}, extract its archives into <target_dir>/{i}/ as the "
                   "instructions above say, so the paths match this list, then run this script again." if pub
                   else "A fresh download from the homepage may be laid out differently."), indent="")
            ctx["n_files_text"] = fmt_int(n_files)
    return ctx


def check_syntax(paths_: list[Path]) -> list[str]:
    bad = []
    for p in paths_:
        r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
        if r.returncode:
            bad.append(f"{p.name}: {r.stderr.strip()}")
    return bad


# ----------------------------------------------------------------------------- legacy get_*.sh scripts

def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def legacy_scripts(site, reg: dict, locks: dict) -> list[dict]:
    out = []
    ids = list(reg)
    for f in sorted(paths.REPO_ROOT.glob("get_*.sh")):
        text = f.read_text(errors="replace")
        urls = sorted(set(re.findall(r"https?://[^\s\"'<>)]+", text)))
        stem = norm(f.stem.removeprefix("get_"))
        match = [i for i in ids if norm(i) == stem] or [i for i in ids if norm(i).startswith(stem)]
        if len(match) != 1:
            exact = {u for i in ids for u in reg[i].source.urls}
            match = [i for i in ids if any(u in reg[i].source.urls for u in urls)] if set(urls) & exact else []
        ds_id = match[0] if len(match) == 1 else None
        calls = [c for c in dict.fromkeys(re.findall(r"get_[\w-]+\.sh", text)) if c != f.name]
        off = set()
        for block in re.findall(r"if false &&.*?\bfi\b", text, flags=re.S):
            off |= set(re.findall(r"get_[\w-]+\.sh", block))
        row = {
            "name": f.name, "href": repo_blob(f.name), "id": ds_id,
            "ds_name": next((d["name"] for d in site.datasets if d["id"] == ds_id), None),
            "hosts": sorted({urlparse(u).netloc for u in urls}),
            "tool": "wget" if re.search(r"\bwget\b", text) else "curl" if re.search(r"\bcurl\b", text) else "–",
            "extracts": bool(re.search(r"\b(unzip|tar)\b", text)),
            "deletes": bool(re.search(r"rm -rf|-delete\b", text)) and not ds_id,
            "checks": bool(re.search(r"md5sum|sha256sum|shasum", text)),
            "calls": [(c, c in off) for c in calls],
            "issues": [], "n_urls": len([u for u in urls if "$" not in u]),
        }
        notes: list = []
        if row["tool"] != "–":
            notes.append(f"Downloads with {row['tool']}" + (" and extracts the archives." if row["extracts"]
                                                           else "; does not extract."))
        if calls:
            disabled = [c for c, o in row["calls"] if o]
            row["n_on"] = len(calls) - len(disabled)
            if disabled:
                notes.append(f"{', '.join(disabled)} {'is' if len(disabled) == 1 else 'are'} switched off in it.")
        if row["deletes"]:
            notes.append("Deletes the files it does not need after extracting.")
        if not row["checks"]:
            notes.append("No checksums.")
        if ds_id:
            for fd in host_issues(site, ds_id):
                notes.append(inline_md(fd["finding"].rstrip(".") + "."))
                row["host_issue"] = True
            n_lock = len([r for r in locks.get(ds_id, []) if r.get("relpath") != "@mirror"])
            if n_lock and row["n_urls"] > n_lock:
                notes.append(f"Fetches {row['n_urls']} files; rirdb pins {n_lock} of them.")
        row["notes"] = notes
        out.append(row)
    return out


# ----------------------------------------------------------------------------- the page

def scratch_purge() -> dict:
    """From the storage decision record: the purge delay in days, and when the scratch copies made on a
    given day become purge-eligible ("around 2026-12-04 for files extracted on 2026-09-30")."""
    f = paths.REPO_ROOT / "docs" / "decisions" / "storage-2026-10.md"
    text = f.read_text() if f.exists() else ""
    m = re.search(r"purged (\d+) days after", text)
    d = re.search(r"around (\d{4}-\d\d-\d\d) for files\s+extracted on (\d{4}-\d\d-\d\d)", text)
    return {"days": int(m.group(1)) if m else None, "on": d.group(1) if d else None, "since": d.group(2) if d else None}


def render(site) -> None:
    reg = {d.id: d for d in load_registry(catalogued=False)}

    # 1. the lock files and manifests, as they are
    for f in sorted(paths.DATASETS_DIR.glob("*/downloads.tsv")):
        site.copy(f, f"data/locks/{f.parent.name}.lock.tsv")
    for f in sorted(paths.DATASETS_DIR.glob("*/files.tsv.gz")):
        site.copy(f, f"data/manifests/{f.parent.name}.files.tsv.gz")

    # 2. a fetch script per registry dataset
    tpl = site.env.get_template("get_script.sh.j2")
    written, rows, locks, modes = [], [], {}, Counter()
    for row in site.datasets:
        i = row["id"]
        ds = reg.get(i)
        if ds is None:
            print(f"[site] get: {i} is in site.json but not in the registry; no script")
            continue
        lock, manifest = read_lock(i), []
        locks[i] = lock
        if row["source_kind"] == "github" and lock:
            manifest = read_manifest(i)
        ctx = script_context(site, row, ds, lock, manifest)
        modes[ctx["mode"]] += 1
        written.append(site.write(f"get/{i}.sh", tpl.render(**ctx).rstrip() + "\n"))
        rows.append(table_row(site, row, lock))
    bad = check_syntax(written)
    if bad:
        raise RuntimeError("generated fetch scripts fail `bash -n`:\n" + "\n".join(bad))
    print(f"[site] get: {len(written)} fetch scripts ({', '.join(f'{k} {v}' for k, v in sorted(modes.items()))}), "
          "all pass bash -n")

    # 3. the page
    site.page("get/index.html", "get.html", title="Get the data", nav="get",
              description="How to download every room impulse response dataset in the rirdb registry: from the "
                          "source, with a generated fetch script that checks checksums, with the rirdb pipeline, "
                          "or from the copies at QMUL.",
              **page_context(site, reg, rows, locks))


def table_row(site, row: dict, lock: list[dict]) -> dict:
    i = row["id"]
    known, unknown = lock_bytes(lock)
    mf = row.get("manifest") or {}
    lic = licence_info(row)
    ck_key, ck_short, ck_text = checksum_kind(row, lock)
    mode = mode_of(row, lock)
    n_lock = sum(1 for r in lock if r.get("relpath") != "@mirror")
    files_txt = f"{fmt_int(n_lock)} file{'s' if n_lock != 1 else ''}"
    if lock and lock[0].get("relpath") == "@mirror":
        size_text = fmt_bytes(mf.get("bytes")) if mf else "–"
        size_sub, size_v = "web folder, as mirrored", mf.get("bytes") or 0
    elif lock and known:
        size_text, size_v = fmt_bytes(known), known
        size_sub = files_txt + (f", {unknown} of unstated size" if unknown else "")
        if all((r.get("url") or "").startswith("inplace:") for r in lock):
            size_sub += "; extracted, in rirdb's copy"
    elif lock:
        size_text, size_v = "size not stated", mf.get("bytes") or 0
        size_sub = files_txt + (f"; {fmt_bytes(mf.get('bytes'))} extracted" if mf else "")
    else:
        size_text, size_v = "no lock yet", -1
        size_sub = "DRY_RUN=1 lists the size" if mode in ("zenodo_live", "hf") else None
    return {
        "id": i, "name": row["name"], "access": row["access"], "kind": row["source_kind"],
        # the commands the picker on get/index.html shows depend on what the script does
        "pick": "manual_lock" if mode == "manual" and lock else "manual" if mode == "info" else mode,
        "kind_label": SOURCE_LABELS.get(row["source_kind"], row["source_kind"]),
        "n_lock": n_lock if lock else None, "size_text": size_text, "size_sub": size_sub, "size_v": size_v,
        "ck": ck_short, "ck_key": ck_key, "ck_text": ck_text, "lic_cls": lic["cls"], "lic_cls_label": lic["cls_label"],
        "lic_label": lic["label"], "status": row["status"], "has_lock": bool(lock),
        "has_manifest": bool(row.get("manifest")), "manifest_file": mf.get("file"),
        "search": f"{row['name']} {i} {row['source_kind']} {lic['label']} {row['status']} {row['access']}",
    }


def page_context(site, reg: dict, rows: list[dict], locks: dict) -> dict:
    data = site.data
    corpus = data["corpus"]
    dss = site.datasets
    by_id = {d["id"]: d for d in dss}

    # the dataset the examples use: the smallest active, open dataset with provider checksums
    cand = [r for r in rows if r["has_lock"] and r["status"] == "active" and r["access"] == "open"
            and r["ck_key"] == "provider" and r["size_v"] > 0]
    ex = min(cand, key=lambda r: r["size_v"]) if cand else rows[0]
    small3 = [r["id"] for r in sorted(cand, key=lambda r: r["size_v"])[:3]]
    sha_cand = [r for r in rows if r["ck_key"] == "first" and r["size_v"] > 0]
    ex_sha = min(sha_cand, key=lambda r: r["size_v"]) if sha_cand else None

    n_lock_files = sum(r["n_lock"] or 0 for r in rows)
    kinds = Counter(r["kind"] for r in rows)      # the Source filter of the table: the registry's source kind
    # the cards say what each script does, so a script that only prints instructions counts as a manual download
    # (ReverbFX: a Zenodo source, but rirdb uses the group-store copy in place)
    card_kinds = Counter("manual" if r["pick"] in ("manual", "manual_lock") else r["kind"] for r in rows)
    kind_rows = [{"kind": k, "label": SOURCE_LABELS.get(k, k), "n": card_kinds[k], "do": KIND_DO.get(k, ("", ""))[0],
                  "needs": KIND_DO.get(k, ("", ""))[1]} for k in sorted(card_kinds, key=lambda k: -card_kinds[k])]

    # a mirrored web folder has no sizes in its lock: its size is that of rirdb's mirrored copy
    def dl_bytes(d: dict) -> int:
        if d["source_kind"] == "wget_mirror":
            return (d.get("manifest") or {}).get("bytes") or 0
        return (d.get("lock_summary") or {}).get("bytes") or 0

    mirrors = [{"id": d["id"], "name": d["name"], "bytes": dl_bytes(d)} for d in dss
               if d["source_kind"] == "wget_mirror" and dl_bytes(d)]

    # waves (README: download waves of the pipeline; fetch/lib_common.sh ids_from_args selects open and
    # active or planned datasets of a wave)
    waves = []
    for w in sorted({d.get("wave") for d in dss if d.get("wave") is not None}):
        members = [d for d in dss if d.get("wave") == w]
        fetchable = [d for d in members if d["access"] == "open" and d["status"] in ("active", "planned")]
        waves.append({"wave": w, "text": WAVE_TEXT.get(w, ""), "n": len(members), "n_fetch": len(fetchable),
                      "bytes": sum(dl_bytes(d) for d in fetchable)})
    n_no_wave = sum(1 for d in dss if d.get("wave") is None)

    # QMUL copies (registry/storage.tsv)
    store_rows = []
    for d in dss:
        s = d.get("storage") or {}
        if not s.get("path"):
            continue
        mf = d.get("manifest") or {}
        # registry/storage.tsv counts can lag behind the manifest (files added to the dataset later)
        mf_n = mf.get("n_files") if mf.get("n_files") not in (None, s.get("n_files")) else None
        store_rows.append({"id": d["id"], "name": d["name"], "tier": s.get("tier"),
                           "tier_label": TIER.get(s.get("tier"), (s.get("tier"), ""))[0], "folder": s.get("folder"),
                           "path": s["path"],
                           "path_html": Markup("/<wbr>").join(escape(p) for p in s["path"].split("/")),
                           "n_files": s.get("n_files"), "bytes": s.get("bytes"), "manifest_n": mf_n,
                           "verified_on": s.get("verified_on") or None})
    store_rows.sort(key=lambda r: (r["tier"] == "scratch", r["name"].lower()))
    tiers = []
    for t in ("store", "inplace", "scratch"):
        rs = [r for r in store_rows if r["tier"] == t]
        if rs:
            tiers.append({"tier": t, "label": TIER[t][0], "what": TIER[t][1], "n": len(rs),
                          "bytes": sum(r["bytes"] or 0 for r in rs), "files": sum(r["n_files"] or 0 for r in rs)})

    # registration, browser or other manual steps
    manual = []
    for d in dss:
        ds = reg.get(d["id"])
        if ds is None or not (ds.source.kind == "manual" or d["access"] in ("registration", "manual")):
            continue
        manual.append({"id": d["id"], "name": d["name"], "access": d["access"], "status": d["status"],
                       "instructions": inline_md(ds.source.instructions), "homepage": ds.homepage,
                       "has_lock": bool(locks.get(d["id"])),
                       "why": manual_reason(site.dataset(d["id"]), d, "see Hosts at risk below")})

    # hosts at risk: findings about hosts, broken datasets, datasets outside a DOI repository
    issues = []
    for d in dss:
        for f in host_issues(site, d["id"]):
            issues.append({"id": d["id"], "name": d["name"], "finding": inline_md(f["finding"]),
                           "evidence": inline_md(f.get("evidence")) if f.get("evidence") else None,
                           "handling": inline_md(f.get("handling")) if f.get("handling") else None,
                           "status": d["status"]})
    fragile = []
    for d in dss:
        if d["source_kind"] in ("http", "github", "wget_mirror", "wayback") and d["status"] != "deferred":
            fragile.append({"id": d["id"], "name": d["name"], "kind": SOURCE_LABELS.get(d["source_kind"]),
                            "host": urlparse((reg[d["id"]].source.urls or [d.get("homepage") or ""])[0]).netloc
                            if d["id"] in reg else "", "doi": d.get("doi"),
                            "has_manifest": bool(d.get("manifest")), "has_lock": bool(locks.get(d["id"]))})
    unpinned = [{"id": d["id"], "name": d["name"], "status": d["status"],
                 "kind": SOURCE_LABELS.get(d["source_kind"], d["source_kind"])}
                for d in dss if not locks.get(d["id"]) and d["source_kind"] != "manual"]

    # storage: download and extracted size per dataset
    sizes = []
    for d in dss:
        ls, mf = d.get("lock_summary") or {}, d.get("manifest") or {}
        if not mf.get("bytes") and not ls.get("bytes"):
            continue
        # a lock of files used in place (ReverbFX) holds the extracted files, not what one downloads
        inplace = locks.get(d["id"]) and all((r.get("url") or "").startswith("inplace:") for r in locks[d["id"]])
        sizes.append({"id": d["id"], "name": d["name"], "dl": 0 if inplace else ls.get("bytes") or 0,
                      "ex": mf.get("bytes") or 0, "files": mf.get("n_files") or 0})
    sizes.sort(key=lambda s: -s["ex"])
    total_ex = sum(s["ex"] for s in sizes)
    top = [s for s in sizes if s["ex"]][:15]
    rest = [s for s in sizes if s["ex"]][15:]
    chart = charts.hbars([{"label": s["name"], "value": s["ex"] / 1e9, "text": fmt_bytes(s["ex"]),
                           "href": f"../d/{s['id']}.html",
                           "tip": f"{s['name']}: {fmt_bytes(s['ex'])} extracted"
                                  + (f", {fmt_bytes(s['dl'])} to download" if s["dl"] else "")}
                          for s in top], w=380, label_w=160,
                         title=f"Disk space after extraction, the {len(top)} largest datasets")
    cum, n_half = 0, 0
    for s in sizes:
        cum += s["ex"]
        n_half += 1
        if cum >= total_ex / 2:
            break
    unknown_rows = sum(lock_bytes(v)[1] for v in locks.values())
    # archives kept next to their extracted files: the extracted total plus every archive in the locks
    # (files that are not archives are already in the manifests, so they are not counted twice)
    arch_rows = [r for v in locks.values() for r in v if EXTRACT_RE.search(r.get("relpath") or "")
                 or SPLIT_RE.search(r.get("relpath") or "")]
    arch_bytes = sum(size_of(r) or 0 for r in arch_rows)
    arch_unknown = sum(1 for r in arch_rows if size_of(r) is None)
    peak = total_ex + arch_bytes

    # archives that need care when extracting: split zips, and zips over 4 GiB (ZIP64)
    split_ids, big_zip = [], []
    for i, lock in locks.items():
        rels = {r["relpath"]: size_of(r) or 0 for r in lock}
        if any(SPLIT_RE.search(p) for p in rels):
            split_ids.append(i)
        big = [p for p, s in rels.items() if p.endswith(".zip") and s > ZIP64_BYTES]
        if big:
            big_zip.append({"id": i, "name": by_id[i]["name"] if i in by_id else i, "n": len(big),
                            "bytes": max(rels[p] for p in big)})
    big_zip.sort(key=lambda b: -b["bytes"])

    # survey-only datasets
    avail = Counter(s.get("availability") or "unknown" for s in data.get("survey") or [])

    # datasets whose findings record a zip repaired with zip -FF (docs/decisions/dataset-findings.md)
    repaired = [{"id": d["id"], "name": d["name"]} for d in dss
                if any("zip -FF" in f"{f.get('evidence') or ''} {f.get('handling') or ''}"
                       for f in site.dataset(d["id"]).get("findings") or [])]

    # legacy scripts whose host now refuses scripted downloads (the registry marks the dataset access: manual)
    legacy = legacy_scripts(site, reg, locks)
    legacy_dead = [s for s in legacy if s["id"] and s.get("host_issue") and by_id[s["id"]]["access"] == "manual"]

    status_n = Counter(r["status"] for r in rows)
    access_n = Counter(r["access"] for r in rows)
    return {
        "mirrors": mirrors, "arch_bytes": arch_bytes, "arch_unknown": arch_unknown, "split_ids": split_ids,
        "big_zip": big_zip, "repaired": repaired, "legacy_dead": legacy_dead, "purge": scratch_purge(),
        "n_manual_scripts": sum(1 for r in rows if mode_of(by_id[r["id"]], locks.get(r["id"]) or []) == "manual"),
        "pick_tpls": {k: "\n".join([f"curl -fLO {SITE_URL}get/__ID__.sh"] + v) for k, v in PICK_CMDS.items()},
        "rows": rows, "ex": ex, "ex_sha": ex_sha, "small3": small3, "n_lock_files": n_lock_files,
        "n_with_lock": sum(1 for r in rows if r["has_lock"]),
        "n_md5": sum(1 for r in rows if r["ck_key"] == "provider"),
        "n_sha_first": sum(1 for r in rows if r["ck_key"] == "first"),
        "kind_rows": kind_rows, "waves": waves, "n_no_wave": n_no_wave,
        "store_rows": store_rows, "tiers": tiers, "purge_days": scratch_purge()["days"],
        "manual": manual, "issues": issues, "fragile": fragile, "unpinned": unpinned,
        "legacy": legacy,
        "sizes": sizes, "top": top, "rest_n": len(rest), "rest_bytes": sum(s["ex"] for s in rest),
        "chart": chart, "total_ex": total_ex, "n_half": n_half, "unknown_rows": unknown_rows, "peak": peak,
        "avail": sorted(avail.items(), key=lambda kv: -kv[1]),
        "status_n": sorted(status_n.items(), key=lambda kv: -kv[1]),
        "access_n": sorted(access_n.items(), key=lambda kv: -kv[1]),
        "kinds_n": sorted(kinds.items(), key=lambda kv: -kv[1]),
        "lock_columns": [(k, Markup(v)) for k, v in LOCK_COLUMNS],
        "manifest_columns": [(k, Markup(v)) for k, v in MANIFEST_COLUMNS],
        "by_id": by_id, "source_labels": SOURCE_LABELS, "status_text": STATUS_TEXT, "access_text": ACCESS_TEXT,
        "licence_classes": LICENCE_CLASSES,
        "toc": [("ways", "Five ways"), ("script", "Fetch scripts"), ("pipeline", "rirdb pipeline"),
                ("legacy", "Legacy scripts"), ("qmul", "Copies at QMUL"), ("all", "All datasets"),
                ("manual", "No scripted download"), ("risk", "Hosts at risk"), ("verify", "Checking files"),
                ("extract", "Extracting archives"), ("storage", "Disk space"), ("survey", "Survey-only datasets")],
    }
