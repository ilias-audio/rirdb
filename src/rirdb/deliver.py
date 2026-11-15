"""The output folder of `rirdb get` (release-1.0.md, A3).

    <out>/audio/<dataset>/<ir_id>.wav   a selection: one float32 WAV per IR, in the asked format and rate
    <out>/files/<dataset>/<file>        `get --dataset X` alone: every file as published (decision 7)
    <out>/irs.csv                       the selected rows, with `path` (and `weight` with a balance)
    <out>/all_irs.csv                   a dataset with a cap, delivered whole: every IR found
    <out>/LICENSES.md                   each dataset's licence, permitted uses and link; per-space licences
    <out>/references.bib                one entry per dataset, and one for RIRDB
    <out>/selection.lock                the tables' version, the command, the seed, each ir_id and audio_hash

Each IR is cut out of its published file by the dataset's reader, and checked against the hash of its decoded
samples (`audio_hash`) before it is converted.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd

from rirdb import __version__, client
from rirdb.fetch import FetchError, fetch_dataset, say

RIRDB_BIB = """@misc{rirdb,
  author = {Ibnyahya, Ilias and Reiss, Joshua D.},
  title = {{RIRDB}: A Database to Find and Explore Room Impulse Response Datasets},
  year = {2026},
  howpublished = {\\url{https://github.com/ilias-audio/rirdb}},
  note = {Tables version %s}
}
"""


# ------------------------------------------------------------------------------------------- channels

def audio_hash(x: np.ndarray) -> str:
    """The hash of decoded samples, as the release's `audio_hash` (analyser v1: SHA-1 of the float32
    samples, channels first, first 32 hex digits)."""
    return hashlib.sha1(np.ascontiguousarray(x, dtype=np.float32).tobytes()).hexdigest()[:32]


def ambix(x: np.ndarray, roles: list[str], sh_norm: str) -> np.ndarray:
    """Ambisonic channels -> AmbiX: ACN order (W, Y, Z, X, ...), SN3D. First order is reordered by the role
    names; FuMa's W is raised by 3 dB, N3D's order-n channels divided by sqrt(2n + 1). A normalisation the
    authors do not state is kept as published."""
    if set(roles[:4]) == {"W", "X", "Y", "Z"}:
        x = np.concatenate([x[[roles.index(r) for r in ("W", "Y", "Z", "X")]], x[4:]])
    x = x.copy()
    if sh_norm == "FuMa":
        x[0] *= math.sqrt(2)
    elif sh_norm == "N3D":
        for acn in range(len(x)):
            x[acn] /= math.sqrt(2 * math.isqrt(acn) + 1)
    return x


def to_format(x: np.ndarray, roles: list[str], fmt: str | None, sh_norm: str) -> np.ndarray:
    """(channels, samples) as published -> the asked format. Rows reach here only if the format allows them
    (select.matching): mono from a mono row or from W, stereo and binaural as they are."""
    if fmt == "ambisonic":
        return ambix(x, roles, sh_norm)
    if fmt == "mono" and "W" in roles:
        return ambix(x, roles, sh_norm)[:1]
    return x


def resample(x: np.ndarray, fs: int, to: int) -> np.ndarray:
    from scipy.signal import resample_poly

    r = Fraction(to, fs)
    return resample_poly(x, r.numerator, r.denominator, axis=1)



def write_wav(path: Path, x: np.ndarray, fs: int) -> None:
    """(channels, samples) -> a 32-bit float WAV. scipy writes no PEAK chunk, whose timestamp would make two
    runs of the same selection differ by a few bytes: the same lock gives the same files, byte for byte."""
    from scipy.io import wavfile

    wavfile.write(path, fs, np.ascontiguousarray(x.T, dtype=np.float32))

# ------------------------------------------------------------------------------------------- the folder

@contextmanager
def output_folder(out: Path):
    """Write into <out>.partial and rename it to <out> at the end, so a folder named <out> is always complete.
    An <out> that exists is replaced only if it holds an earlier rirdb output (a selection.lock)."""
    from rirdb.fetch import cache_dir

    out = out.resolve()
    if out == cache_dir().resolve() or cache_dir().resolve() in out.parents:
        raise FetchError(f"--out {out} is inside rirdb's cache: choose another folder")
    if out.exists() and any(out.iterdir()) and not (out / "selection.lock").exists():
        raise FetchError(f"{out} is not empty and holds no earlier rirdb output: choose another --out")
    partial = out.with_name(out.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)            # what a failed run left
    partial.mkdir(parents=True)
    yield partial
    if out.exists():
        say(f"replacing the earlier output in {out}")
        shutil.rmtree(out)
    partial.rename(out)


def write_selection(sel, out: Path, from_dir: Path | None = None, keep_archives: bool = False,
                    command: list[str] | None = None) -> Path:
    """Write the IRs of a Selection as WAV files, with irs.csv, LICENSES.md, references.bib, selection.lock.
    irs.csv describes the files written: their rate, channels and format."""
    from rirdb.adapters import get_adapter

    q = sel.query
    datasets = client.table("datasets").set_index("dataset")
    rows = sel.table.copy()
    rows["path"] = "audio/" + rows["dataset"] + "/" + rows["ir_id"] + ".wav"
    written = {}                                               # row -> (rate, channels) of the WAV
    with output_folder(out) as folder:
        for dataset, group in rows.groupby("dataset"):
            needed = sorted(group["file"].str.split("|").explode().unique())
            root = fetch_dataset(dataset, needed=needed, from_dir=from_dir, keep_archives=keep_archives)
            d = datasets.loc[dataset]
            reader = get_adapter(d["reader"], json.loads(d["reader_settings"] or "{}"))
            readers = client.table(f"readers.{dataset}").set_index("ir_id")
            say(f"{dataset}: writing {len(group):,} IRs")
            for r in group.itertuples():
                how = readers.loc[r.ir_id]
                x, fs = reader.load(json.loads(how["locator"]), root)
                if r.audio_hash and audio_hash(x) != r.audio_hash:
                    raise FetchError(f"{r.ir_id}: the decoded samples do not match the release (audio_hash)")
                y = to_format(x, how["channel_roles"].split(","), q.format, how["sh_norm"])
                if q.fs and q.fs != fs:
                    y = resample(y, fs, q.fs)
                (folder / r.path).parent.mkdir(parents=True, exist_ok=True)
                write_wav(folder / r.path, y, q.fs or fs)
                written[r.Index] = (q.fs or fs, y.shape[0])
        rows["fs"] = [written[i][0] for i in rows.index]
        rows["channels"] = [written[i][1] for i in rows.index]
        rows["format"] = q.format or rows["format"]
        if q.balance == "none":
            rows = rows.drop(columns="weight", errors="ignore")
        write_common(rows, folder, q, command)
    return out


def write_published(dataset: str, out: Path, from_dir: Path | None = None, keep_archives: bool = False,
                    command: list[str] | None = None) -> Path:
    """`get --dataset X` with no other filter: every file of the dataset as published, verified (decision 7)."""
    from rirdb.select import Query

    root = fetch_dataset(dataset, from_dir=from_dir, keep_archives=keep_archives)
    files = client.table(f"files.{dataset}")
    files = files[files["download"] != ""]
    with output_folder(out) as folder:
        say(f"{dataset}: writing {len(files):,} files")
        for f in files["file"]:
            link_or_copy(root / f, folder / "files" / dataset / f)
        rows = client.table("irs").query("dataset == @dataset").copy()
        rows["path"] = rows["file"].map(lambda fs: "|".join(f"files/{dataset}/{f}" for f in fs.split("|")))
        write_common(rows, folder, Query(dataset=[dataset]), command, as_published=True)
        if client.has(f"all_irs.{dataset}.parquet"):
            client.table(f"all_irs.{dataset}").to_csv(folder / "all_irs.csv", index=False)
    return out


def link_or_copy(src: Path, dest: Path) -> None:
    """A hard link where the cache and the folder share a disk (no second copy), else a copy. A file of the
    cache that changes is deleted and fetched again, never written over (fetch.py), so a link is safe."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def write_common(rows: pd.DataFrame, out: Path, q, command: list[str] | None, as_published: bool = False) -> None:
    rows.to_csv(out / "irs.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    used = sorted(rows["dataset"].unique())
    datasets = client.table("datasets").set_index("dataset").loc[used]
    (out / "LICENSES.md").write_text(licences_md(rows, datasets))
    (out / "references.bib").write_text(references_bib(datasets))
    keep = ["ir_id", "audio_hash", *(["weight"] if "weight" in rows else [])]
    lock = {"rirdb": __version__, "tables": client.version(), "command": command or sys.argv,
            "as_published": as_published, "query": dict(vars(q)), "irs": rows[keep].to_dict("records")}
    (out / "selection.lock").write_text(json.dumps(lock, indent=1) + "\n")


def licences_md(rows: pd.DataFrame, datasets: pd.DataFrame) -> str:
    names = licence_names()
    lines = ["# Licences", "",
             "Every dataset keeps its own licence. RIRDB records it and never relicenses it. Read each licence "
             "before you use or share these files, and cite each dataset (references.bib).", ""]
    for dataset, d in datasets.iterrows():
        mine = rows[rows["dataset"] == dataset]
        lines += [f"## {d['name']} (`{dataset}`)", "",
                  f"- Licence: {d['licence']}" + (f" ({names[d['licence']]})" if d["licence"] in names else ""),
                  f"- Research: {d['use_research']}; commercial: {d['use_commercial']}; training: "
                  f"{d['use_training']}; redistribution: {d['redistribute']}"]
        for label, value in (("Homepage", d["homepage"]), ("DOI", f"https://doi.org/{d['doi']}" if d["doi"] else ""),
                             ("Paper", d["paper_url"])):
            if value:
                lines.append(f"- {label}: {value}")
        lines.append(f"- IRs in this folder: {len(mine):,}")
        per_space = mine.groupby(["space", "licence"]).size().reset_index(name="irs")
        if per_space["licence"].nunique() > 1 or (per_space["licence"] != d["licence"]).any():
            lines += ["", "Each space has its own licence:", "", "| space | licence | IRs |", "|---|---|---|"]
            lines += [f"| {s} | {lic} | {n} |" for s, lic, n in per_space.itertuples(index=False)]
        lines.append("")
    return "\n".join(lines)


def licence_names() -> dict[str, str]:
    """id -> name, from licences.csv (shipped with the package)."""
    with open(Path(__file__).with_name("licences.csv"), newline="") as f:
        return {r["id"]: r["name"] for r in csv.DictReader(f)}


def references_bib(datasets: pd.DataFrame) -> str:
    entries = []
    for dataset, d in datasets.iterrows():
        fields = [("title", "{" + latex(d["name"]) + "}")]                # braces keep its capitals
        if pd.notna(d["year"]):
            fields.append(("year", str(int(d["year"]))))
        if d["doi"]:
            fields.append(("doi", d["doi"]))
        if d["homepage"]:
            fields.append(("howpublished", f"\\url{{{url_text(d['homepage'])}}}"))
        if d["paper_title"]:
            url = f", \\url{{{url_text(d['paper_url'])}}}" if d["paper_url"] else ""
            fields.append(("note", f"Described in: {latex(d['paper_title'])}{url}"))
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields)
        entries.append(f"@misc{{{dataset},\n{body}\n}}\n")
    return "\n".join([*entries, RIRDB_BIB % client.version()])


def latex(text: str) -> str:
    """Text for a BibTeX field: LaTeX's special characters escaped (a name with & breaks a bibliography)."""
    return re.sub(r"([&%$#_{}])", r"\\\1", text)


def url_text(url: str) -> str:
    """A URL inside \\url{}: only % and # need escaping."""
    return url.replace("%", r"\%").replace("#", r"\#")
