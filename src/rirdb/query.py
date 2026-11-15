"""DuckDB views over the Parquet index and metrics: the prototype of the IR picker.

    rirdb dev query "t30_mid BETWEEN 1.2 AND 1.8 AND c80_mid > 0 AND measured" --columns ir_id,room_id,t30_mid

Views (all read straight from $RIRDB_ROOT, nothing copied):
  irs           one row per IR record (index: locator, capture format, positions, per-room licence)
  rooms         one row per room (curated category, volume where published)
  metrics       one row per analysed IR (metrics/v1/*/wide.parquet)
  audit         signal-integrity audit (sweep/chirp, tonal, dropout, noise floor)
  datasets      one row per registry entry: type, formats, licence, room information published
  corpus        metrics joined with rooms, index (licence, locator) and audit
  corpus_dedup  corpus + dup_of (exact duplicates)
  clean_rooms   measured room IRs, preferred, de-duplicated, without audit content flags
"""

from __future__ import annotations

import duckdb

from rirdb import paths

DEFAULT_COLUMNS = ["ir_id", "dataset_id", "room_id", "grade", "t30_mid", "edt_mid", "c80_mid", "d50_mid",
                   "drr_bb", "sti", "br", "tr"]


def connect() -> duckdb.DuckDBPyConnection:
    root = paths.data_root()
    con = duckdb.connect()
    # signal-integrity audit (rirdb dev audit), when it has been run
    if any((root / "audit" / "v1").glob("*/audit.parquet")):
        con.execute(f"""CREATE VIEW audit AS SELECT COLUMNS(c -> c <> 'audit_traceback')
                        FROM read_parquet('{root}/audit/v1/*/audit.parquet', union_by_name = true)""")
    else:
        con.execute("CREATE VIEW audit AS SELECT NULL::VARCHAR AS ir_id WHERE false")
    con.execute(f"""
        CREATE VIEW irs AS SELECT * FROM read_parquet('{root}/index/irs/*/*.parquet', hive_partitioning = true,
                                                      union_by_name = true);
        CREATE VIEW rooms AS SELECT * FROM read_parquet('{root}/index/rooms/*/*.parquet', hive_partitioning = true,
                                                        union_by_name = true);
        CREATE VIEW metrics AS SELECT * FROM read_parquet('{root}/metrics/v1/*/wide.parquet', union_by_name = true);
    """)
    _datasets_view(con)
    icols = {r[0] for r in con.execute("DESCRIBE irs").fetchall()}
    src = "i.license_source" if "license_source" in icols else "NULL::VARCHAR AS license_source"
    con.execute(f"""
        CREATE VIEW corpus AS
            -- ir_kind / preferred / licence come from the current index, so curation needs no re-analysis
            SELECT m.* EXCLUDE (ir_kind, preferred, license_spdx, redistribute_audio), i.ir_kind, i.preferred,
                   i.license_spdx, i.redistribute_audio, i.training_use, {src}, r.category, r.room_label,
                   r.volume_m3, i.locator, i.fs AS fs_file, i.duration_s AS duration_file_s,
                   a.* EXCLUDE (ir_id)
            FROM metrics m
            LEFT JOIN rooms r USING (room_id)
            LEFT JOIN irs i USING (ir_id)
            LEFT JOIN audit a USING (ir_id);
        -- exact duplicates: identical decoded samples (float32 sha1) under different records
        CREATE VIEW duplicates AS
            SELECT content_sha1, count(*) AS n, list(ir_id ORDER BY ir_id) AS ir_ids,
                   list(DISTINCT dataset_id) AS datasets, min(ir_id) AS keep_ir_id
            FROM metrics WHERE content_sha1 IS NOT NULL
            GROUP BY content_sha1 HAVING count(*) > 1;
        CREATE VIEW corpus_dedup AS
            SELECT c.*, d.keep_ir_id AS dup_of FROM corpus c
            LEFT JOIN duplicates d ON c.content_sha1 = d.content_sha1 AND c.ir_id <> d.keep_ir_id;
    """)
    # measured room IRs, one representation per position, no duplicates, and (once audited) no
    # sweeps, tonal or non-decaying content or dropouts; add grade or noise conditions on top as needed
    audited = "flag_sweep_like" in {r[0] for r in con.execute("DESCRIBE audit").fetchall()}
    content_ok = (" AND NOT coalesce(flag_sweep_like OR flag_tonal OR flag_not_decaying OR flag_dropout, false)"
                  if audited else "")
    con.execute("CREATE VIEW clean_rooms AS SELECT * FROM corpus_dedup WHERE measured AND ir_kind = 'room' "
                f"AND preferred AND dup_of IS NULL AND coalesce(error, '') = ''{content_ok}")
    return con


def _datasets_view(con: duckdb.DuckDBPyConnection) -> None:
    """`datasets`: one row per registry entry (type, formats, licence, what is published about the rooms)."""
    import pandas as pd

    from rirdb.registry import AVAIL_FIELDS, load_registry

    rows = []
    for d in load_registry():
        ri = d.room_info
        rows.append({"dataset_id": d.id, "name": d.name, "type": d.type, "status": d.status, "year": d.year,
                     "ir_kinds": ",".join(d.ir_kinds), "capture_formats": ",".join(d.capture_formats),
                     "spatial": d.spatial, "license_spdx": d.license.spdx, "redistribute_audio": d.license.redistribute_audio,
                     "training_use": d.license.training, "non_commercial": d.license.non_commercial,
                     "share_alike": d.license.share_alike,
                     **{f"room_{f}": getattr(ri, f) for f in AVAIL_FIELDS}, "room_info_where": ri.where,
                     "homepage": d.homepage, "doi": d.doi})
    con.register("_datasets_df", pd.DataFrame(rows))
    con.execute("CREATE VIEW datasets AS SELECT * FROM _datasets_df")


VIEWS = ("corpus", "corpus_dedup", "clean_rooms", "datasets", "rooms", "irs")


def query(where: str, columns: list[str] | None = None, limit: int = 50, order_by: str | None = None,
          view: str = "corpus"):
    if view not in VIEWS:
        raise ValueError(f"view must be one of {VIEWS}")
    con = connect()
    cols = ", ".join(columns or (DEFAULT_COLUMNS if view.startswith(("corpus", "clean")) else ["*"]))
    sql = f"SELECT {cols} FROM {view} WHERE {where}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return con.execute(sql).df()
