"""`rirdb dev status`: how far each dataset is, read from the folders (no progress file to keep up to date).

    stage        catalogued  only registry/<id>/dataset.yaml
                 registered  in the registry with its sources, not pinned yet (planned, manual, broken)
                 pinned      registry/<id>/downloads.tsv (what to download, with checksums)
                 indexed     $RIRDB_ROOT/tables/<id>/index.parquet (every IR found in the files)
                 measured    $RIRDB_ROOT/tables/<id>/<analyser>/measured.parquet
                 candidate   in $RIRDB_ROOT/release/candidate/ (the last `rirdb dev build`)
"""

from __future__ import annotations

import gzip

import pandas as pd
import pyarrow.parquet as pq

from rirdb import paths
from rirdb.registry import load_registry


def count_lines(path) -> int:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as f:
        return sum(1 for _ in f) - 1


def status() -> pd.DataFrame:
    """One row per dataset of the registry."""
    candidate = paths.data_root() / "release" / "candidate" / "datasets.parquet"
    in_release = (pd.read_parquet(candidate, columns=["dataset", "n_irs"]).dropna().set_index("dataset")["n_irs"]
                  if candidate.exists() else pd.Series(dtype="Int64"))
    rows = []
    for d in load_registry():
        t = paths.tables_dir(d.id)
        pin, files, index = paths.downloads_tsv(d.id), paths.files_tsv(d.id), t / "index.parquet"
        analysers = sorted(p.parent.name for p in t.glob("*/measured.parquet"))
        row = {"dataset": d.id, "registry": d.status,
               "downloads": count_lines(pin) if pin.exists() else None,
               "files": count_lines(files) if files.exists() else None,
               "irs_found": pq.ParquetFile(index).metadata.num_rows if index.exists() else None,
               "irs_sampled": int(pd.read_parquet(index, columns=["sampled"])["sampled"].sum()) if index.exists() else None,
               "measured": ",".join(analysers),
               "irs_measured": (pq.ParquetFile(t / analysers[-1] / "measured.parquet").metadata.num_rows
                                if analysers else None),
               "irs_in_candidate": in_release.get(d.id)}
        row["stage"] = ("candidate" if row["irs_in_candidate"] is not None else "measured" if analysers
                        else "indexed" if index.exists() else "pinned" if pin.exists()
                        else "catalogued" if d.status == "catalogued" else "registered")
        rows.append(row)
    out = pd.DataFrame(rows)
    for c in ("downloads", "files", "irs_found", "irs_sampled", "irs_measured", "irs_in_candidate"):
        out[c] = out[c].astype("Int64")
    return out[["dataset", "stage", "registry", "downloads", "files", "irs_found", "irs_sampled", "measured",
                "irs_measured", "irs_in_candidate"]]
