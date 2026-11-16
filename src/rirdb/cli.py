"""`rirdb`: find room impulse responses, and get them as one folder.

    rirdb search ...        what a selection holds, and what it costs to download (nothing is downloaded)
    rirdb info <dataset>    one dataset: licence, sizes, spaces, quality, what a cap leaves out
    rirdb get ...           download, verify, and write the output folder

The maintainer commands are under `rirdb dev` (dev_cli.py).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from rirdb.dev_cli import dev_app

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Find room impulse responses across public datasets, and get them as one folder.")
app.add_typer(dev_app, name="dev")
console = Console()

# The filters of `search` and `get` (release-1.0.md, A2), written once.
USE = typer.Option(None, "--use", help="Keep IRs whose licence allows it: research, commercial, training, redistribute.")
FORMAT = typer.Option(None, "--format", help="mono, stereo, binaural or ambisonic. Without it: every format, "
                                             "one layout per space.")
ORIGIN = typer.Option("any", "--origin", help="measured, simulated, device, or any.")
GRADE = typer.Option("any", "--grade", help="The lowest grade kept: S, A, B, C, D, or any (A means A or better).")
DATASET = typer.Option(None, "--dataset", help="Only these datasets (repeat the option for several).")
EXCLUDE = typer.Option(None, "--exclude-dataset", help="Leave these datasets out.")
UNKNOWN = typer.Option(False, "--include-unknown", help="With --use: also keep IRs whose licence does not say.")
FS = typer.Option(None, "--fs", help="The sample rate of the WAV files (default: as published).")
SPACE_TYPE = typer.Option(None, "--space-type", help="Only these kinds of room, e.g. concert_hall or worship "
                                                      "(repeat for several).")
T30 = typer.Option(None, "--t30", help="Reverberation time (rt_mid) range in seconds, e.g. 1.2-2.5, or 2.5- for no "
                                       "upper limit.")
KIND = typer.Option(None, "--kind", help="Only these kinds of space (repeat for several): indoor, outdoor, anechoic, "
                                         "simulated, device. --kind indoor keeps real rooms only.")
PROVIDES = typer.Option(None, "--provides", help="Only datasets whose authors provide this (repeat for several): "
                                                 "dimensions, volume, geometry, materials, positions, images, "
                                                 "reference_acoustics, conditions.")
PER_SPACE = typer.Option(None, "--per-space", help="At most this many IRs of each space (room), spread over its "
                                                     "reverberation times from the shortest to the longest.")
MAX_ARCHIVE = typer.Option(None, "--max-archive", help="Leave out IRs that come in a download (an archive or a big "
                                                         "file, always taken whole) larger than this, e.g. 2GB.")
N = typer.Option(None, "--n", help="Draw this many IRs at random (see --balance).")
BALANCE = typer.Option("none", "--balance", help="What the draw makes equally likely: space, dataset, t30 (short "
                                                 "and long reverbs), or none (every IR).")
YEAR = typer.Option(None, "--year", help="The dataset's year of publication: 2020- (2020 or later), -2015, 2018-2020 "
                                         "or 2024.")
SEED = typer.Option(0, "--seed", help="The seed of the draw, recorded in selection.lock.")


@app.command()
def search(use: str = USE, format: str = FORMAT, origin: str = ORIGIN, grade: str = GRADE,
           dataset: list[str] = DATASET, exclude_dataset: list[str] = EXCLUDE, include_unknown: bool = UNKNOWN,
           fs: int = FS, space_type: list[str] = SPACE_TYPE, t30: str = T30, provides: list[str] = PROVIDES,
           kind: list[str] = KIND, per_space: int = PER_SPACE, max_archive: str = MAX_ARCHIVE, n: int = N,
           balance: str = BALANCE, seed: int = SEED, year: str = YEAR) -> None:
    """What a selection holds: IRs, spaces, datasets, licences, grades, and its download size. Nothing is downloaded.

    The same options as `get`, so `search` shows what `get` would fetch, except `get --dataset X` alone, which gives
    every file of X as published (`rirdb info X` gives its size)."""
    from rirdb import client
    from rirdb.select import by_dataset, cap_of, grade_split, human, plural, size_text, select

    sel = select(use, format, origin, grade, dataset, exclude_dataset, include_unknown, fs, n, balance, seed,
                 space_type, t30, provides, kind, per_space, max_archive, year=year)
    t = sel.table
    console.print(f"[bold]{plural(len(t), 'IR')}[/bold] in {plural(t['space'].nunique(), 'space')} from "
                  f"{plural(t['dataset'].nunique(), 'dataset')} (release {client.version()})")
    if t.empty:
        return
    console.print(grid(grade_split(t), "origin"))
    plan = sel.plan()
    table = Table("dataset", "IRs", "spaces", "formats", "licence", "download", box=None)
    for d, r in by_dataset(t, plan).iterrows():
        table.add_row(d, f"{r['irs']:,}", f"{r['spaces']:,}", r["formats"], r["licence"], r["download"])
    console.print(table)
    s = sel.sizes()
    console.print(f"Download: [bold]{size_text(s.download_bytes, s.unknown_size)}[/bold], in "
                  f"{plural(s.n_downloads, 'download')}, each taken whole. The selected IRs as WAV: "
                  f"[bold]{human(s.output_bytes)}[/bold].")
    copies = (t["duplicate_of"] != "").sum()
    if copies:
        console.print(f"{copies:,} of these IRs are exact copies of another IR (column duplicate_of).")
    for line in sel.known_defects():
        console.print(f"[yellow]Warning[/yellow] {line}")
    for d in sorted(t["dataset"].unique()):
        cap = cap_of(d)
        if cap:
            console.print("\n".join(cap.lines()))


@app.command()
def info(dataset: str = typer.Argument(..., help="A dataset id, e.g. sriracha (`rirdb search` lists them).")) -> None:
    """One dataset: licence, sizes, spaces, formats, quality, and what its cap leaves out."""
    from rirdb import client
    from rirdb.fetch import scripted
    from rirdb.select import GRADES, cap_of, dataset_download_bytes, plural, size_text

    datasets = client.table("datasets").set_index("dataset")
    if dataset not in datasets.index:
        raise typer.Exit(f"rirdb: no dataset {dataset} in release {client.version()}")
    d = datasets.loc[dataset]
    irs = client.table("irs").query("dataset == @dataset")
    console.print(f"[bold]{dataset}[/bold]: {d['name']}" + (f" ({d['year']})" if pd.notna(d["year"]) else ""))
    links = [x for x in (d["homepage"], f"https://doi.org/{d['doi']}" if d["doi"] else "") if x]
    if links:
        console.print("  " + "  ".join(links))
    n_statements = d["licence_statements"] if pd.notna(d["licence_statements"]) else 1
    statements = f"; {n_statements} statements, the strictest used" if n_statements > 1 else ""
    console.print(f"Licence: {d['licence']}{statements}. Research {d['use_research']}, commercial "
                  f"{d['use_commercial']}, training {d['use_training']}, redistribution {d['redistribute']}.")
    licences = irs.groupby("licence")["space"].nunique()
    if len(licences) > 1:
        console.print("  By space: " + ", ".join(f"{lic} {plural(n, 'space')}" for lic, n in licences.items()))
    if irs.empty:
        console.print(f"Status: {d['status']}: no IR of it is in the tables of release {client.version()}.")
        return
    found = f"{int(d['n_irs_found']):,} found in the files"
    stated = f", {int(d['n_irs_stated']):,} stated by the authors" if pd.notna(d["n_irs_stated"]) else ""
    console.print(f"IRs: {len(irs):,} in the table, {found}{stated}. Origin: {d['origin']}.")
    console.print(f"Download: {size_text(*dataset_download_bytes(dataset))}, every file as published.")
    cap = cap_of(dataset)
    if cap:
        console.print("\n".join(cap.lines()))
    formats = ", ".join(f"{f} {n:,}" for f, n in irs["format"].value_counts().items())
    rates = ", ".join(f"{int(r)} Hz" for r in sorted(irs["fs"].unique()))
    console.print(f"Formats: {formats}. Sample rates: {rates}.")
    per_space = irs.groupby("space").size()
    console.print(f"Spaces: {len(per_space):,} ({', '.join(sorted(irs['space_type'].unique()))}); "
                  f"IRs per space: {per_space.min():,} to {per_space.max():,}, median {per_space.median():,.0f}.")
    of = " of the sampled IRs" if cap else ""
    grades = irs["grade"].replace("", "none").value_counts()
    console.print(f"Grades{of}: " + ", ".join(f"{g} {grades.get(g, 0):,}" for g in [*GRADES, "none"] if grades.get(g, 0)))
    flagged = {c.removeprefix("defect_").replace("_", " "): int(irs[c].fillna(False).sum())
               for c in irs if c.startswith("defect_")}
    if any(flagged.values()):
        console.print(f"Defects{of}: " + ", ".join(f"{k} {n:,}" for k, n in flagged.items() if n))
    ranges = []
    for label, col, unit in (("rt_mid", "rt_mid", "s"), ("DRR", "drr_db", "dB"), ("distance", "distance_m", "m")):
        v = irs[col].dropna()
        if len(v):
            ranges.append(f"{label} {v.quantile(0.05):.2f} to {v.quantile(0.95):.2f} {unit}")
    if ranges:
        console.print("Ranges (5th to 95th percentile): " + "; ".join(ranges))
    if d["known_issues"]:
        console.print(f"Known issues: {d['known_issues']}")
    for line in (d["known_defects"].split("\n") if d.get("known_defects") else []):
        console.print(f"[yellow]Known defect[/yellow]: {line}")
    urls = client.table("downloads").query("dataset == @dataset")["url"]
    if urls.map(scripted).all():
        console.print(f"Get it: rirdb get --dataset {dataset}   (every file as published)")
    else:
        console.print(f"Get it: rirdb cannot download it. Fetch the files from {links[0] if links else 'the authors'}, "
                      f"then `rirdb get --dataset {dataset} --from <folder>` checks every file.")


def grid(counts: pd.DataFrame, label: str) -> Table:
    t = Table(label, *counts.columns, box=None)
    for i, r in counts.iterrows():
        t.add_row(i, *(f"{v:,}" for v in r))
    return t


@app.command()
def get(use: str = USE, format: str = FORMAT, origin: str = ORIGIN, grade: str = GRADE,
        dataset: list[str] = DATASET, exclude_dataset: list[str] = EXCLUDE, include_unknown: bool = UNKNOWN,
        fs: int = FS, space_type: list[str] = SPACE_TYPE, t30: str = T30, provides: list[str] = PROVIDES,
        kind: list[str] = KIND, per_space: int = PER_SPACE, max_archive: str = MAX_ARCHIVE, n: int = N,
        balance: str = BALANCE, seed: int = SEED, year: str = YEAR,
        out: Path = typer.Option(Path("rirdb-out"), "--out", help="The output folder."),
        lock: Path = typer.Option(None, "--lock", help="A selection.lock: get the same IRs again."),
        from_dir: Path = typer.Option(None, "--from", help="A folder with the files of a dataset rirdb cannot download."),
        yes: bool = typer.Option(False, "--yes", help="Do not ask before a download above 5 GB."),
        max_download: str = typer.Option(None, "--max-download", help="Stop if the download is larger, e.g. 20GB."),
        keep_archives: bool = typer.Option(False, "--keep-archives", help="Keep downloaded archives in the cache."),
        ) -> None:
    """Download, verify, and write the output folder: audio, irs.csv, LICENSES.md, references.bib, selection.lock.

    `rirdb get --dataset X` alone gives every file of X as published, verified."""
    import json
    import sys

    from rirdb.deliver import write_published, write_selection
    from rirdb.select import (Query, cap_of, dataset_download_bytes, from_lock, human, parse_size, plural, size_text,
                               select)

    recorded = json.loads(lock.read_text()) if lock else {}
    if recorded.get("as_published"):                            # a lock of `get --dataset X` alone
        dataset = recorded["query"]["dataset"]
    whole = recorded.get("as_published") or (
        len(dataset or []) == 1 and not lock
        and not (use or format or exclude_dataset or include_unknown or fs or space_type or t30 or provides or kind)
        and n is None and per_space is None and max_archive is None and year is None   # --per-space 0 is checked
        and origin == "any" and grade == "any" and balance == "none")
    if whole:
        from rirdb import client

        Query(dataset=dataset).check(client.table("datasets"))
        size, unknown = dataset_download_bytes(dataset[0])
        console.print(f"{dataset[0]}: every file as published, {size_text(size, unknown)} to download.")
        known = client.table("datasets").set_index("dataset").get("known_defects")
        for line in (known[dataset[0]].split("\n") if known is not None and known[dataset[0]] else []):
            console.print(f"[yellow]Warning[/yellow] {dataset[0]}: a known defect. {line}")
        capped = dataset
    else:
        sel = from_lock(lock) if lock else select(use, format, origin, grade, dataset, exclude_dataset,
                                                  include_unknown, fs, n, balance, seed, space_type, t30, provides, kind,
                                                  per_space, max_archive, year=year)
        s = sel.sizes()
        size = s.download_bytes
        console.print(f"{plural(len(sel.table), 'IR')} from {plural(sel.table['dataset'].nunique(), 'dataset')}: "
                      f"{size_text(s.download_bytes, s.unknown_size)} to download "
                      f"({plural(s.n_downloads, 'download')}), {human(s.output_bytes)} as WAV.")
        if sel.table.empty:
            return
        for line in sel.known_defects():
            console.print(f"[yellow]Warning[/yellow] {line}")
        capped = sorted(sel.table["dataset"].unique())
    for d in capped:
        cap = cap_of(d)
        if cap:
            console.print("\n".join(cap.lines()))
    if max_download and size > parse_size(max_download):
        raise typer.Exit(f"rirdb: the download is above --max-download {max_download}: narrow the selection")
    if size > 5e9 and not yes:
        if not sys.stdin.isatty():
            raise typer.Exit(f"rirdb: the download is {human(size)}, above 5 GB: pass --yes to go on")
        if not typer.confirm(f"Download {human(size)}?"):
            raise typer.Exit(1)
    done = (write_published(dataset[0], out, from_dir, keep_archives) if whole
            else write_selection(sel, out, from_dir, keep_archives))
    console.print(f"Wrote {done}: irs.csv, LICENSES.md, references.bib, selection.lock.")


def main() -> None:
    """The `rirdb` command: an expected failure (a download, a checksum, a wrong option) is one line, not a
    traceback."""
    from rirdb.fetch import FetchError

    try:
        app()
    except (FetchError, ValueError) as e:
        console.print(f"[red]rirdb:[/red] {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
