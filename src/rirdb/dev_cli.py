"""`rirdb dev`: the maintainer commands. They need the full environment (pixi, see docs/hpc.md) and run through
sbatch on Apocrita. Users see `search`, `info` and `get` (cli.py).

    the 1.0 pipeline (data-model.md, section 8)   index, status, build, release
    the registry                                   registry compile, registry list, verify, probe
    analyser v1 (until step 4 replaces it)         index-v1, analyze, merge, audit, audit-merge, query
    reports, embeddings and maps                   report ..., embed, embed-check, map ..., snapshot

Every import beyond typer and rich is made inside the command, so `rirdb --help` works in the light install.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from rirdb import paths

dev_app = typer.Typer(no_args_is_help=True, help="Maintainer commands: the pipeline, the registry, analyser v1, reports.")
registry_app = typer.Typer(no_args_is_help=True, help="Validate and compile the registry (registry/<id>/dataset.yaml).")
dev_app.add_typer(registry_app, name="registry")
console = Console()
console = Console()


@registry_app.command("compile")
def registry_compile() -> None:
    """Validate the registry, write compiled/sources.tsv and regenerate the README tables."""
    from rirdb.readme import update_readme
    from rirdb.registry import compile_sources, load_registry

    datasets = load_registry()
    out = compile_sources(datasets, paths.COMPILED_DIR / "sources.tsv")
    changed = update_readme(paths.README, [d for d in datasets if d.status != "catalogued"])
    console.print(f"{len(datasets)} datasets valid; wrote {out.relative_to(paths.REPO_ROOT)}; "
                  f"README {'updated' if changed else 'unchanged'}")


@registry_app.command("list")
def registry_list(wave: int | None = typer.Option(None, help="Only this wave.")) -> None:
    """Print the registry as a table."""
    from rirdb.registry import load_registry

    table = Table("id", "type", "wave", "status", "access", "source", "license")
    for d in load_registry():
        if wave is not None and d.wave != wave:
            continue
        table.add_row(d.id, d.type, str(d.wave), d.status, d.access or "", d.source.kind if d.source else "", d.license.spdx)
    console.print(table)


def _select(dataset_ids: list[str] | None, wave: list[int] | None, all_: bool):
    from rirdb.registry import load_registry

    datasets = load_registry()
    if dataset_ids:
        known = {d.id for d in datasets}
        unknown = set(dataset_ids) - known
        if unknown:
            raise typer.BadParameter(f"unknown dataset ids: {sorted(unknown)}")
        return [d for d in datasets if d.id in dataset_ids]
    if wave:
        return [d for d in datasets if d.wave in wave and d.fetchable]
    if all_:
        return [d for d in datasets if d.fetchable]
    raise typer.BadParameter("give dataset ids, --wave N, or --all")


@dev_app.command("index")
def dev_index(dataset_ids: list[str] = typer.Argument(..., help="Dataset ids.")) -> None:
    """Read each dataset's files into $RIRDB_ROOT/tables/<id>/index.parquet: every IR found, with `sampled`."""
    from rirdb.index import write_index_table
    from rirdb.registry import get_dataset

    table = Table("dataset", "IRs found", "sampled", "spaces", "own fields")
    for i in dataset_ids:
        s = write_index_table(get_dataset(i))
        table.add_row(i, f"{s['n_found']:,}", f"{s['n_sampled']:,}", str(s["n_spaces"]), str(s["source_meta_fields"]))
    console.print(table)


@dev_app.command("status")
def dev_status(tsv: Path = typer.Option(None, "--tsv", help="Also write the table to this file.")) -> None:
    """One line per dataset (all of the registry): how far it is, read from the folders."""
    from rirdb.status import status

    df = status()
    table = Table(*df.columns)
    for row in df.astype(str).replace({"<NA>": "", "None": ""}).itertuples(index=False):
        table.add_row(*row)
    console.print(table)
    console.print(", ".join(f"{n} {s}" for s, n in df["stage"].value_counts().items()) + f" ({len(df)} datasets)")
    if tsv:
        df.to_csv(tsv, sep="\t", index=False)


@dev_app.command("build")
def dev_build(workers: int = typer.Option(8, help="Datasets assembled in parallel.")) -> None:
    """Rebuild $RIRDB_ROOT/release/candidate/ from the registry and tables/, then run every check."""
    from rirdb.build import build
    from rirdb.checks import run

    out = build(workers=workers)
    results = run(out)
    failed = [r for r in results if r.n_rows]
    for r in failed:
        console.print(f"[red]FAIL[/red] {r.name}: {r.n_rows:,} rows, e.g. {r.example[:300]}")
    console.print(f"{out}: {len(results) - len(failed)} of {len(results)} checks pass")
    raise typer.Exit(1 if failed else 0)




@dev_app.command("release")
def dev_release(
    version: str = typer.Argument(..., help="The tables' version, e.g. 0.9."),
    repo: str = typer.Option("ilias-audio/rirdb", help="The GitHub repository that holds the release."),
) -> None:
    """Freeze release/candidate/ into release/<version>/ (the GitHub release's files) and write the package's lock."""
    from rirdb import release
    from rirdb.build import candidate_dir

    candidate = candidate_dir()
    try:
        release.refuse_unless_ready(candidate)
        out = release.freeze(version, candidate, candidate.parent, release.base_url(version, repo))
    except release.ReleaseRefused as e:
        console.print(f"[red]refused[/red]: {e}")
        raise typer.Exit(1)
    files = sorted(out.iterdir())
    console.print(f"wrote {out}: {len(files)} files, {sum(p.stat().st_size for p in files) / 1e6:.0f} MB, "
                  f"and {release.LOCK.relative_to(paths.REPO_ROOT)} (commit it)")
    console.print("To publish (Ilias):\n"
                  f"  gh release create tables-{version} --repo {repo} --title 'RIRDB tables {version}' "
                  f"--notes 'The release tables of RIRDB {version}.' {out}/*")


@dev_app.command("site")
def dev_site(out: Path = typer.Option(Path("site"), "--out", help="The folder to write.")) -> None:
    """The public website, from the release tables alone: home, datasets and rooms (with a search), a page per
    dataset, guide, about and data reference (src/rirdb/site/)."""
    from rirdb.site import build

    out = build(out)
    console.print(f"wrote {out}: {len(list(out.rglob('*.html')))} pages")


@dev_app.command()
def verify(
    dataset_ids: list[str] = typer.Argument(None, help="Dataset ids (default: use --wave/--all)."),
    wave: list[int] = typer.Option(None, "--wave", help="Waves to verify (repeatable)."),
    all_: bool = typer.Option(False, "--all", help="All fetchable datasets."),
    full: bool = typer.Option(False, help="Re-hash every file against the manifest."),
    repair: bool = typer.Option(False, help="Clear markers of datasets with missing files so fetch/run.sh re-fetches them."),
    workers: int = typer.Option(8, help="Hashing processes (--full)."),
) -> None:
    """Check locks, downloads and extracted files; write reports/verify/<date>.{md,json}."""
    from rirdb import verify as v

    reports = v.verify(_select(dataset_ids, wave, all_), full=full, workers=workers)
    md, _ = v.write_report(reports, full=full)
    table = Table("dataset", "wave", "stage", "status", "files", "problems")
    for r in reports:
        n_bad = len(r.missing) + len(r.size_mismatch) + len(r.hash_mismatch)
        table.add_row(r.id, str(r.wave), r.stage, r.status, str(r.manifest_files or r.lock_files),
                      f"{n_bad} bad, {len(r.unverified_downloads)} not fetched" if (n_bad or r.unverified_downloads) else "")
    console.print(table)
    console.print(f"report: {md.relative_to(paths.REPO_ROOT)}")
    bad = [r for r in reports if r.status in ("MISSING", "CORRUPT")]
    if repair:
        for r in bad:
            v.repair(r.id)
            console.print(f"repaired markers for {r.id}: now run `sbatch jobs/download.sh fetch/run.sh {r.id}`")
    if bad:
        raise typer.Exit(code=1)


@dev_app.command()
def probe(dataset_ids: list[str] = typer.Argument(..., help="Datasets to probe.")) -> None:
    """Summarise fetched files into registry/probes/<id>.txt (commit it)."""
    from rirdb.probe import probe as run_probe

    for d in dataset_ids:
        out = run_probe(d)
        console.print(f"wrote {out.relative_to(paths.REPO_ROOT)}")


@dev_app.command("index-v1")
def index_v1(
    dataset_ids: list[str] = typer.Argument(None),
    wave: list[int] = typer.Option(None, "--wave"),
    all_: bool = typer.Option(False, "--all"),
) -> None:
    """Analyser v1's index ($RIRDB_ROOT/index/): what `analyze` reads. Step 4 replaces it with `index`."""
    from rirdb.index import build_index

    table = Table("dataset", "IRs", "rooms", "subsample_v1", "fs", "channels", "expected")
    for d in _select(dataset_ids, wave, all_):
        if not d.adapter.name:
            console.print(f"[yellow]skip {d.id}: no adapter configured[/yellow]")
            continue
        s = build_index(d)
        exp = d.expected.n_irs
        table.add_row(d.id, str(s["n_irs"]), str(s["n_rooms"]), str(s["n_subsample_v1"]),
                      ",".join(map(str, s["fs"])), ",".join(map(str, s["channels"])), str(exp or "?"))
    console.print(table)


@dev_app.command()
def analyze(
    dataset_id: str = typer.Option(..., "--dataset"),
    shard: str = typer.Option("0", help="Shard index, or 'auto' inside a SLURM array."),
    n_shards: int = typer.Option(1),
    workers: int = typer.Option(0, help="Processes (0: SLURM_CPUS_PER_TASK or 4)."),
    full: bool = typer.Option(False, help="Analyse all records, not only subsample_v1."),
    limit: int = typer.Option(0, help="Only the first N records of the shard (smoke tests)."),
) -> None:
    """Analyse one shard of a dataset (metrics parquet + feature h5)."""
    import os

    from rirdb.run import analyze_shard, auto_shard

    if shard == "auto":
        s, n = auto_shard(n_shards if n_shards > 1 else None)
    else:
        s, n = int(shard), n_shards
    w = workers or int(os.environ.get("SLURM_CPUS_PER_TASK", 4))
    out = analyze_shard(dataset_id, s, n, w, full=full, limit=limit or None)
    console.print(f"wrote {out}")


@dev_app.command()
def merge(dataset_ids: list[str] = typer.Argument(...)) -> None:
    """Merge metric shards into metrics/v1/<id>/wide.parquet."""
    from rirdb.run import merge as run_merge

    for d in dataset_ids:
        console.print(f"wrote {run_merge(d)}")


@dev_app.command()
def audit(
    dataset_id: str = typer.Option(..., "--dataset"),
    shard: str = typer.Option("0", help="Shard index, or 'auto' inside a SLURM array."),
    n_shards: int = typer.Option(1),
    workers: int = typer.Option(0, help="Processes (0: SLURM_CPUS_PER_TASK or 4)."),
) -> None:
    """Signal-integrity audit of one shard (sweep / non-IR content, noise-floor stationarity)."""
    from rirdb.audit import audit_shard, workers_default
    from rirdb.run import auto_shard

    s, n = auto_shard(n_shards if n_shards > 1 else None) if shard == "auto" else (int(shard), n_shards)
    console.print(f"wrote {audit_shard(dataset_id, s, n, workers or workers_default())}")


@dev_app.command("audit-merge")
def audit_merge(dataset_ids: list[str] = typer.Argument(...)) -> None:
    """Merge audit shards into audit/v1/<id>/audit.parquet."""
    from rirdb.audit import merge_audit

    for d in dataset_ids:
        console.print(f"wrote {merge_audit(d)}")


@dev_app.command("query")
def query_cmd(
    where: str = typer.Argument(..., help="SQL predicate over the view (default `corpus`)."),
    columns: str = typer.Option("", help="Comma-separated columns (default: a core set)."),
    limit: int = typer.Option(50),
    order_by: str = typer.Option("", help="ORDER BY clause, e.g. 't30_mid DESC'."),
    csv: str = typer.Option("", help="Also write the result to this CSV path."),
    view: str = typer.Option("corpus", help="corpus | corpus_dedup (adds dup_of) | clean_rooms | datasets | rooms | irs."),
) -> None:
    """Pick IRs by constraints (DuckDB over the metrics Parquet)."""
    import pandas as pd

    from rirdb.query import query

    df = query(where, [c.strip() for c in columns.split(",") if c.strip()] or None, limit, order_by or None, view)
    with pd.option_context("display.width", 200, "display.max_columns", 40, "display.max_colwidth", 40):
        console.print(df.to_string(index=False))
    console.print(f"{len(df)} rows")
    if csv:
        df.to_csv(csv, index=False)


report_app = typer.Typer(no_args_is_help=True, help="Generate reports.")
dev_app.add_typer(report_app, name="report")


@report_app.command("corpus")
def report_corpus() -> None:
    """Per-dataset tables + IR-space figures -> reports/corpus/README.md."""
    from rirdb.report.corpus import build

    console.print(f"wrote {build().relative_to(paths.REPO_ROOT)}")


@report_app.command("validation")
def report_validation() -> None:
    """Compare with values published with the datasets -> reports/validation/."""
    from rirdb.validate import build

    console.print(f"wrote {build().relative_to(paths.REPO_ROOT)}")


@report_app.command("cards")
def report_cards() -> None:
    """One card per registry dataset -> docs/datasets/<id>.md."""
    from rirdb.report.cards import build

    console.print(f"wrote {len(build())} dataset cards to docs/datasets/")


@report_app.command("overview")
def report_overview() -> None:
    """Filtering guide, signal integrity, distributions and the CLAP map -> reports/overview/."""
    from rirdb.report.overview import build

    console.print(f"wrote {build()}")


@report_app.command("guide")
def report_guide() -> None:
    """Field guide to the public datasets (catalogue, licences, landscape, gaps) -> reports/guide/."""
    from rirdb.report.guide import build

    console.print(f"wrote {build()}")


@report_app.command("site")
def report_site(
    only: list[str] = typer.Option(None, "--only", help="Sections to rebuild: datasets, catalog, analysis, get, docs."),
    reuse_data: bool = typer.Option(False, "--reuse-data", help="Use reports/site/data/ instead of querying the DB."),
) -> None:
    """The website: every dataset, where to get it, and its analysis -> reports/site/."""
    from rirdb.report.site import build

    console.print(f"wrote {build(only=only or None, reuse_data=reuse_data)}")


@report_app.command("site-check")
def report_site_check() -> None:
    """Check the built website: links, anchors, headings, JSON-LD, leaked values, fetch-script syntax."""
    from rirdb.report.site.check import run

    raise typer.Exit(run())


@dev_app.command()
def embed(
    dataset_ids: list[str] = typer.Argument(...),
    batch: int = typer.Option(16),
    shard: str = typer.Option("0", help="Shard index, or 'auto' inside a SLURM array (one dataset)."),
    n_shards: int = typer.Option(1),
) -> None:
    """CLAP embeddings of analysed IRs (GPU job, `pixi run -e embed`)."""
    from rirdb.embed import embed_dataset
    from rirdb.run import auto_shard

    s, n = auto_shard(n_shards if n_shards > 1 else None) if shard == "auto" else (int(shard), n_shards)
    for d in dataset_ids:
        console.print(f"wrote {embed_dataset(d, batch=batch, shard=s, n_shards=n)}")


@dev_app.command("embed-check")
def embed_check(dataset_id: str = typer.Option("rochester", "--dataset"), n: int = typer.Option(12),
                device: str = typer.Option("cpu")) -> None:
    """Re-embed n stored IRs with the frozen CLAP copy; print the cosine similarity to the stored vectors."""
    from rirdb.embed import check_reproducible

    console.print_json(data=check_reproducible(dataset_id, n, device))


map_app = typer.Typer(no_args_is_help=True, help="Frozen CLAP maps: fit once, place new IRs on them.")
dev_app.add_typer(map_app, name="map")


@map_app.command("fit")
def map_fit(cap: int = typer.Option(1200, help="IRs per dataset (every room kept)."), seed: int = typer.Option(0)) -> None:
    """Fit the UMAP maps of the `ir` and `reverb_vector` embeddings once -> $RIRDB_ROOT/maps/clap_v1/."""
    from rirdb.maps import fit

    console.print(f"wrote {fit(cap, seed)}")


@map_app.command("place")
def map_place(
    name: str = typer.Option(..., help="Name of the placed set, e.g. fdn_v3."),
    wav_dir: str = typer.Option("", help="Folder of IR files (analysed + embedded here; GPU job for many files)."),
    pattern: str = typer.Option("**/*.wav", help="Glob inside --wav-dir."),
    dataset_id: str = typer.Option("", "--dataset", help="Or: an indexed, analysed and embedded dataset."),
    device: str = typer.Option("auto"),
    workers: int = typer.Option(0, help="Analysis processes (0: SLURM_CPUS_PER_TASK or 4)."),
) -> None:
    """Place IRs on the frozen maps; report realism and coverage against real rooms."""
    import json
    import os
    from pathlib import Path

    from rirdb import maps

    if bool(wav_dir) == bool(dataset_id):
        raise typer.BadParameter("give exactly one of --wav-dir or --dataset")
    if wav_dir:
        w = workers or int(os.environ.get("SLURM_CPUS_PER_TASK", 4))
        ids, E, info = maps.from_wavs(name, Path(wav_dir), pattern, device, w)
    else:
        ids, E, info = maps.from_dataset(dataset_id)
    out = maps.place(name, ids, E, info)
    console.print(f"wrote {out}")
    console.print_json(json.dumps(json.loads((out / "summary.json").read_text())["signals"]))


@dev_app.command()
def snapshot() -> None:
    """Write snapshots/metrics_core/<dataset>.parquet (committed; purge insurance)."""
    from rirdb.export import snapshot as run_snapshot

    outs = run_snapshot()
    total = sum(p.stat().st_size for p in outs)
    console.print(f"wrote {len(outs)} files to snapshots/metrics_core/ ({total / 1e6:.1f} MB; "
                  f"largest {max(p.stat().st_size for p in outs) / 1e6:.1f} MB)")
