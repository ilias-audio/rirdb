"""Canonical index of every IR record (Parquet under $RIRDB_ROOT/index/).

  index/irs/dataset=<id>/part-0.parquet    one row per IR record
  index/rooms/dataset=<id>/part-0.parquet  one row per room (with curated category)

Paths inside `locator` are relative to raw/<id>/files, so moving the corpus
only means changing RIRDB_ROOT.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from rirdb import paths
from rirdb.adapters import get_adapter
from rirdb.registry import Dataset
from rirdb.rooms import enrich_rooms


def index_dir() -> Path:
    return paths.data_root() / "index"


def irs_path(dataset_id: str) -> Path:
    return index_dir() / "irs" / f"dataset={dataset_id}" / "part-0.parquet"


def rooms_path(dataset_id: str) -> Path:
    return index_dir() / "rooms" / f"dataset={dataset_id}" / "part-0.parquet"


def _hash_fraction(key: str) -> float:
    return int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "big") / 2 ** 64


def subsample_mask(df: pd.DataFrame, d: Dataset) -> pd.Series:
    """subsample_v1: which records the first analysis pass covers.

    Full scope: everything. Dense datasets: a deterministic hash rule (stable as
    data is added) keeping `analysis.subsample.fraction` (default 1/16 = 6.25 %),
    always keeping at least one record per room/condition.
    """
    if d.analysis.scope != "subsample_v1":
        return pd.Series(True, index=df.index)
    frac = float((d.analysis.subsample or {}).get("fraction", 1 / 16))
    keep = df["local_key"].map(_hash_fraction) < frac
    group = df["room_id"] + "|" + df["condition_key"].fillna("")
    first = df.sort_values("local_key").groupby(group.loc[df.sort_values("local_key").index]).head(1).index
    keep.loc[first] = True
    return keep


def apply_room_licenses(dataset_id: str, df: pd.DataFrame) -> list[str]:
    """Per-space licences (the `licence` column of registry/<id>/spaces.csv) override the dataset licence,
    e.g. OpenAIR, where each contributor chose a licence per space. Returns the keys that match no record."""
    from rirdb.registry import License
    from rirdb.rooms import curated_spaces

    tab = curated_spaces(dataset_id)
    tab = tab[tab["licence"] != ""] if "licence" in tab else tab.iloc[:0]
    for _, r in tab.iterrows():
        m = df["room_key"] == r["space"]
        lic = License(spdx=r["licence"], redistribute_audio=r["redistribute"] or "unknown")
        df.loc[m, ["license_spdx", "redistribute_audio", "training_use", "license_source"]] = [
            lic.spdx, lic.redistribute_audio, lic.training, r.get("licence_source", "")]
    return sorted(set(tab["space"]) - set(df["room_key"]))


def build_index(d: Dataset) -> dict:
    adapter = get_adapter(d.adapter.name, d.adapter.params)
    root = paths.files_dir(d.id)
    rows = [r.to_row() for r in adapter.iter_records(d, root)]
    if not rows:
        raise RuntimeError(f"{d.id}: adapter found no IR records under {root}")
    df = pd.DataFrame(rows)
    import json

    extras = df["extra"].map(json.loads)
    # one preferred representation per position (e.g. OpenAIR mono is usually W of the B-format)
    df["preferred"] = extras.map(lambda e: bool(e.get("preferred", True)))
    df["reference_role"] = extras.map(lambda e: e.get("reference_role")).fillna(d.analysis.reference_role or "")
    df["type"] = d.type
    df["measured"] = d.type == "measured"
    df["license_spdx"] = d.license.spdx
    df["redistribute_audio"] = d.license.redistribute_audio
    df["training_use"] = d.license.training
    df["license_source"] = d.license.url or d.homepage or ""
    unmatched = apply_room_licenses(d.id, df)
    df["wave"] = d.wave
    dup = df["ir_id"].duplicated()
    if dup.any():
        raise RuntimeError(f"{d.id}: duplicate local keys: {df.loc[dup, 'local_key'].head().tolist()}")
    df["subsample_v1"] = subsample_mask(df, d)

    rooms = (df.groupby("room_id")
               .agg(room_key=("room_key", "first"), room_label=("room_label", "first"),
                    category_hint=("category_hint", "first"), ir_kind=("ir_kind", "first"),
                    n_irs=("ir_id", "size"))
               .reset_index())
    rooms["dataset_id"] = d.id
    rooms = enrich_rooms(d.id, rooms)
    # room-level corrections (e.g. an "outdoor" space in a room dataset) apply to its IRs,
    # unless the adapter already set a more specific kind for the record
    kind = rooms.set_index("room_id")["ir_kind"]
    default = d.ir_kinds[0] if d.ir_kinds else "room"
    df["ir_kind"] = [kind.get(rid, k) if k == default else k for rid, k in zip(df["room_id"], df["ir_kind"])]
    for path, frame in ((irs_path(d.id), df), (rooms_path(d.id), rooms)):
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
    return {"dataset": d.id, "n_irs": len(df), "n_rooms": rooms.shape[0], "license_rows_unmatched": unmatched,
            "n_subsample_v1": int(df["subsample_v1"].sum()),
            "fs": sorted(df["fs"].unique().tolist()), "channels": sorted(df["n_channels"].unique().tolist())}


def load_irs(dataset_id: str) -> pd.DataFrame:
    return pd.read_parquet(irs_path(dataset_id))


# --------------------------------------------------------------------------------------------------
# The index table: $RIRDB_ROOT/tables/<id>/index.parquet (data-model.md, sections 3, 4 and 8)
#
# One row per IR found in the pinned files, sampled or not. Nothing copied from the registry (licence,
# origin, space type): `build` joins those in. The old index above stays until step 2 replaces the
# commands that read it.

# Fields of a reader's `extra` that are about rirdb, not the dataset's own metadata.
_OWN_EXTRA = {"preferred", "reference_role", "subtype"}
# soundfile subtypes -> sample_format (columns.csv)
SAMPLE_FORMATS = {"PCM_S8": "int8", "PCM_U8": "int8", "PCM_16": "int16", "PCM_24": "int24", "PCM_32": "int32",
                  "FLOAT": "float32", "DOUBLE": "float64"}


def split_key(local_key: str) -> tuple[str, str]:
    """A reader's key -> (file, item). `file` is the path inside the dataset's files (two paths joined by
    "|" for a response in two files); `item` is the place inside the file, empty when it holds one IR."""
    file, _, item = local_key.partition("#")
    return file, item


def ir_id(dataset_id: str, file: str, item: str) -> str:
    """<dataset>-<16 hex>: the hash of `file#item` (of `file` alone when item is empty).

    Frozen: the same hash as the old `<dataset>:<hex>` ids, with `-` (allowed in Windows file names)."""
    key = f"{file}#{item}" if item else file
    return f"{dataset_id}-{hashlib.blake2b(key.encode(), digest_size=8).hexdigest()}"


def audio_key(file: str, item: str, locator: str, channel_roles: str, reference_role: str, n_samples: int,
              keep_s: float | None = None, start_s: float | None = None) -> str:
    """A hash of everything that decides which samples are analysed. A measurement repeats it, and `build`
    fails where the two differ, so a reader change cannot leave a stale measurement in place. The dataset's
    `analysis.keep_s` and `start_s` count only when set, so the keys of every other dataset stay as they were."""
    fields = [file, item, json.loads(locator), channel_roles, reference_role, int(n_samples)]
    if keep_s:                                   # the part of the file the analyser reads (run.analysed_part)
        fields.append(float(keep_s))
    if start_s:
        fields.append({"start_s": float(start_s)})
    parts = json.dumps(fields, sort_keys=True)
    return hashlib.blake2b(parts.encode(), digest_size=8).hexdigest()


def sample_by_cap(df: pd.DataFrame, cap: int | None) -> pd.Series:
    """True for the `cap` IRs with the smallest ir_id per space, condition and layout (every IR without a cap).
    The id is a hash, so this is a fixed pseudo-random draw, and raising `cap` only adds rows."""
    if not cap:
        return pd.Series(True, index=df.index)
    order = df.sort_values("ir_id")
    rank = order.groupby(["space", "condition", "layout"], dropna=False).cumcount()
    return (rank < cap).reindex(df.index)


def index_table(d: Dataset) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(index, source_meta) for one dataset, read from its files (names and headers; no samples decoded)."""
    adapter = get_adapter(d.adapter.name, d.adapter.params)
    rows = [r.to_row() for r in adapter.iter_records(d, paths.files_dir(d.id))]
    if not rows:
        raise RuntimeError(f"{d.id}: the reader found no IR under {paths.files_dir(d.id)}")
    raw = pd.DataFrame(rows)
    extra = raw["extra"].map(json.loads)
    file_item = raw["local_key"].map(split_key)
    df = pd.DataFrame({
        "file": [f for f, _ in file_item], "item": [i for _, i in file_item],
        "space": raw["room_id"], "condition": raw["condition_key"].fillna(""),
        "source": raw["src_key"].fillna(""), "receiver": raw["rcv_key"].fillna(""),
        "layout": raw["capture_format"], "channels": raw["n_channels"], "channel_roles": raw["channel_roles"],
        "reference_role": extra.map(lambda e: e.get("reference_role")).fillna(d.analysis.reference_role or ""),
        "fs": raw["fs"], "n_samples": raw["n_samples"], "duration_s": raw["duration_s"],
        "sample_format": extra.map(lambda e: SAMPLE_FORMATS.get(e.get("subtype"), "")),
        "is_preferred": extra.map(lambda e: bool(e.get("preferred", True))),
        "space_label": raw["room_label"].fillna(""), "category_hint": raw["category_hint"].fillna(""),
        "ir_kind": raw["ir_kind"], "sh_norm": raw["sh_norm"].fillna(""), "orientation_known": raw["orientation_known"],
        **{c: raw[c] for c in ("src_x", "src_y", "src_z", "rcv_x", "rcv_y", "rcv_z")},
        "locator": raw["locator"],
    })
    df.insert(0, "dataset", d.id)
    df.insert(0, "ir_id", [ir_id(d.id, f, i) for f, i in zip(df["file"], df["item"])])
    if df["ir_id"].duplicated().any():
        raise RuntimeError(f"{d.id}: two IRs with one file#item: {df.loc[df['ir_id'].duplicated(), 'ir_id'].head().tolist()}")
    named = [json.loads(loc).get("relpath") or "|".join(json.loads(loc)["files"]) for loc in raw["locator"]]
    if list(df["file"]) != named:
        raise RuntimeError(f"{d.id}: a key does not start with the path of its file (a \"#\" in a file name?)")
    old = raw["ir_id"].str.split(":", n=1).str[1]
    if not (df["ir_id"].str.split("-", n=1).str[1] == old).all():
        raise RuntimeError(f"{d.id}: an ir_id hex differs from the reader's own id")
    pos = df[["src_x", "src_y", "src_z", "rcv_x", "rcv_y", "rcv_z"]].astype(float).to_numpy()
    df["distance_m"] = ((pos[:, :3] - pos[:, 3:]) ** 2).sum(axis=1) ** 0.5     # NaN where a position is missing
    df["sampled"] = sample_by_cap(df, d.cap)
    window = (d.analysis.keep_s, d.analysis.start_s)
    df["audio_key"] = [audio_key(*r, *window) for r in zip(df["file"], df["item"], df["locator"], df["channel_roles"],
                                                           df["reference_role"], df["n_samples"])]
    own = [{k: (v if v is None or isinstance(v, (str, int, float, bool)) else json.dumps(v, default=str))
            for k, v in e.items() if k not in _OWN_EXTRA} for e in extra]
    meta = pd.DataFrame(own)
    meta.insert(0, "ir_id", df["ir_id"])
    return df.sort_values("ir_id").reset_index(drop=True), meta.sort_values("ir_id").reset_index(drop=True)


def write_index_table(d: Dataset) -> dict:
    """Write tables/<id>/index.parquet (and source_meta.parquet when the reader keeps fields of its own)."""
    df, meta = index_table(d)
    out = paths.tables_dir(d.id)
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "index.parquet", index=False)
    if meta.shape[1] > 1:
        meta.to_parquet(out / "source_meta.parquet", index=False)
    return {"dataset": d.id, "n_found": len(df), "n_sampled": int(df["sampled"].sum()),
            "n_spaces": df["space"].nunique(), "source_meta_fields": meta.shape[1] - 1}
