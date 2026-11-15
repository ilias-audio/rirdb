"""Filesystem layout: the git repo (code + registry) and RIRDB_ROOT (the DB, in home).

Raw audio lives in three places (registry/storage.tsv); raw/<id>/files is a symlink to it.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_DIR = REPO_ROOT / "registry"
# One folder per dataset: registry/<id>/. Jobs read a copy of the registry made at submission
# (RIRDB_REGISTRY), so editing the registry cannot break queued work.
DATASETS_DIR = Path(os.environ.get("RIRDB_REGISTRY") or REGISTRY_DIR)
PROBES_DIR = REGISTRY_DIR / "probes"
COMPILED_DIR = REGISTRY_DIR / "compiled"
README = REPO_ROOT / "README.md"
REPORTS_DIR = REPO_ROOT / "reports"

DEFAULT_DATA_ROOT = str(Path.home() / "rirdb")      # the maintainer's database; .env sets RIRDB_ROOT


def data_root() -> Path:
    """Root of all downloaded and derived data (never in git).

    Every path stored in the index is relative to this, so moving the corpus
    to permanent storage only means changing RIRDB_ROOT.
    """
    return Path(os.environ.get("RIRDB_ROOT", DEFAULT_DATA_ROOT))


def raw_dir(dataset_id: str) -> Path:
    return data_root() / "raw" / dataset_id


def files_dir(dataset_id: str) -> Path:
    """Extracted / directly-downloaded files of a dataset (read-only after fetch)."""
    return raw_dir(dataset_id) / "files"


def archives_dir(dataset_id: str) -> Path:
    return raw_dir(dataset_id) / "_archives"


def state_file(dataset_id: str) -> Path:
    return data_root() / "state" / f"{dataset_id}.json"


def dataset_dir(dataset_id: str) -> Path:
    """registry/<id>/: dataset.yaml, spaces.csv, downloads.tsv, files.tsv.gz."""
    return DATASETS_DIR / dataset_id


def downloads_tsv(dataset_id: str) -> Path:
    """The pinned downloads: one row per file to fetch, with its URL, size and checksum."""
    return dataset_dir(dataset_id) / "downloads.tsv"


def files_tsv(dataset_id: str) -> Path:
    """Every file after extraction, with its size and sha256."""
    return dataset_dir(dataset_id) / "files.tsv.gz"


def spaces_csv(dataset_id: str) -> Path:
    return dataset_dir(dataset_id) / "spaces.csv"


def tables_dir(dataset_id: str) -> Path:
    """$RIRDB_ROOT/tables/<id>/: what was read from the files (index.parquet, source_meta.parquet) and
    the measurements, one folder per analyser (v1/, v2/). Deleting it redoes that dataset only."""
    return data_root() / "tables" / dataset_id
