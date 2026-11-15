"""`rirdb dev release <version>`: freeze release/candidate/ and write the package's lock.

    rirdb dev release 0.9   ->  $RIRDB_ROOT/release/0.9/        the files of the GitHub release `tables-0.9`
                                src/rirdb/release.lock.json      the version, the base URL, every file's sha256

Refused unless the working tree is clean, the candidate was built from this commit, and every check passes.
A release folder that exists is never overwritten.

The release folder holds exactly the assets of the GitHub release (decision A). A release asset has no
folders, so a file in a sub-folder of the candidate (bands/aachen_air.parquet) becomes one name with a dot
(bands.aachen_air.parquet). The same folder is then uploaded as it is, served in the tests, or read in place
on the maintainer's machine (RIRDB_RELEASE_URL=file:///path/to/rirdb/release/0.9/).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from rirdb import paths
from rirdb.checks import run as run_checks
from rirdb.fetch import sha256_of

LOCK = Path(__file__).with_name("release.lock.json")
REPO = "ilias-audio/rirdb"


class ReleaseRefused(RuntimeError):
    pass


def asset_name(rel: str) -> str:
    """bands/aachen_air.parquet -> bands.aachen_air.parquet"""
    return rel.replace("/", ".")


def base_url(version: str, repo: str = REPO) -> str:
    return f"https://github.com/{repo}/releases/download/tables-{version}/"


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(paths.REPO_ROOT), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def refuse_unless_ready(candidate: Path) -> None:
    """The tree is committed, and the candidate was built from this commit."""
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ReleaseRefused("the working tree has changes that are not committed")
    built = json.loads((candidate / "release.json").read_text())
    if built["git_commit"] != git("rev-parse", "HEAD") or built["git_dirty"]:
        raise ReleaseRefused("the candidate was not built from this commit: run `rirdb dev build` first")


def freeze(version: str, candidate: Path, releases: Path, url: str, lock: Path = LOCK) -> Path:
    """Copy the candidate into releases/<version>/ with flat names, and write the lock. Returns the folder."""
    out = releases / version
    if out.exists():
        raise ReleaseRefused(f"{out} exists, and a release is never overwritten")
    failed = [r.name for r in run_checks(candidate) if r.n_rows]
    if failed:
        raise ReleaseRefused(f"{len(failed)} checks fail, e.g. {failed[:3]}")
    partial = releases / f".{version}.partial"
    if partial.exists():
        shutil.rmtree(partial)                       # what an interrupted freeze left: never published
    partial.mkdir(parents=True)
    for p in sorted(candidate.rglob("*")):
        if p.is_file():
            shutil.copy2(p, partial / asset_name(p.relative_to(candidate).as_posix()))
    meta = json.loads((partial / "release.json").read_text())
    (partial / "release.json").write_text(json.dumps({**meta, "version": version}, indent=1) + "\n")
    files = {p.name: {"sha256": sha256_of(p), "size": p.stat().st_size} for p in sorted(partial.iterdir())}
    partial.rename(out)
    lock.write_text(json.dumps({"version": version, "base_url": url, "files": files}, indent=1) + "\n")
    return out
