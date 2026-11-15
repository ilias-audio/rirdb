"""The release tables on the user's machine.

Each package version reads the tables named in its lock, release.lock.json (written by `rirdb dev release`):
the version, the base URL of the GitHub release, and the sha256 of every file. A table is downloaded once,
into the cache, and checked against the lock the first time a command uses it. A file that does not match
is deleted.

    $RIRDB_CACHE (else ~/.cache/rirdb)/tables/<version>/<file>

Two settings, for the tests and for the maintainer:
    RIRDB_RELEASE_LOCK   another lock file
    RIRDB_RELEASE_URL    another place that holds the same files (file:///path/to/rirdb/release/0.9/)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from rirdb.fetch import FetchError, cache_dir, download, locked, sha256_of

LOCK = Path(__file__).with_name("release.lock.json")
_verified: set[Path] = set()                 # files checked in this process


class ReleaseError(FetchError):
    """The release tables cannot be read (a missing lock, an unknown file, a changed byte)."""


def lock() -> dict:
    path = Path(os.environ.get("RIRDB_RELEASE_LOCK") or LOCK)
    if not path.exists():
        raise ReleaseError(f"no release lock at {path}: this copy of rirdb names no release of the tables")
    return json.loads(path.read_text())


def table_file(name: str) -> Path:
    """The local copy of one file of the release, downloaded if missing, and checked against the lock."""
    lk = lock()
    want = lk["files"].get(name)
    if want is None:
        raise ReleaseError(f"release {lk['version']} has no file {name}")
    local = cache_dir() / "tables" / lk["version"] / name
    if local in _verified and local.exists():
        return local
    with locked(f"tables.{lk['version']}.{name}"):
        if not local.exists():
            download((os.environ.get("RIRDB_RELEASE_URL") or lk["base_url"]) + name, local)
        if sha256_of(local) != want["sha256"]:
            local.unlink()
            raise ReleaseError(f"{name} does not match release {lk['version']} (sha256), so it was deleted. "
                               "Run the command again to download it again.")
    _verified.add(local)
    return local


def has(name: str) -> bool:
    return name in lock()["files"]


def table(name: str) -> pd.DataFrame:
    """One table of the release: irs, datasets, spaces, downloads; or a per-dataset one, `readers.<dataset>`."""
    return pd.read_parquet(table_file(f"{name}.parquet"))


def version() -> str:
    return lock()["version"]
