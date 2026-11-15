"""The checks a release must pass (data-model.md, section 10): `build` runs them, `release` will refuse without them.

Three kinds, each a query that must return no rows:
  - generated from columns.csv, for every column: allowed values, finite numbers inside their range, and
    cells that are never empty; and from thresholds.yaml, the defects a grade S row must have assessed;
  - checks.sql: the rules that span tables;
  - two that need Python: each table has exactly the columns of columns.csv, and every ir_id is the hash of
    its file and item.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

from rirdb.build import assessed_defects
from rirdb.index import ir_id

HERE = Path(__file__).parent
TABLES = {"irs": "irs.parquet", "spaces": "spaces.parquet", "datasets": "datasets.parquet",
          "downloads": "downloads.parquet", "bands": "bands/*.parquet", "files": "files/*.parquet",
          "all_irs": "all_irs/*.parquet", "readers": "readers/*.parquet"}


@dataclass
class Result:
    name: str
    n_rows: int
    example: str = ""


SQL_TYPES = {"string": "VARCHAR", "int": "BIGINT", "float": "DOUBLE", "bool": "BOOLEAN"}


def connect(release: Path) -> duckdb.DuckDBPyConnection:
    """One view per table of the release folder; an empty one, with the columns of columns.csv, where the
    release has no file for it (all_irs when no dataset has a cap)."""
    con = duckdb.connect()
    types = column_types()
    for table, pattern in TABLES.items():
        if list(release.glob(pattern)):
            con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{release / pattern}', union_by_name = true)")
        else:
            cols = ", ".join(f"NULL::{SQL_TYPES[t]} AS {n}" for n, t in types[table])
            con.execute(f"CREATE VIEW {table} AS SELECT {cols} WHERE false")
    return con


def column_types() -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    with open(HERE / "columns.csv", newline="") as f:
        for c in csv.DictReader(f):
            out.setdefault(c["table"], []).append((c["name"], c["type"]))
    return out


def column_checks() -> list[tuple[str, str]]:
    """(name, query) for every column of columns.csv."""
    out = []
    with open(HERE / "columns.csv", newline="") as f:
        for c in csv.DictReader(f):
            t, n = c["table"], c["name"]
            where = f"{t}.{n}"
            if c["empty_means"].startswith("never empty"):
                empty = f"{n} IS NULL" + (f" OR {n} = ''" if c["type"] == "string" else "")
                out.append((f"{where} is never empty", f"SELECT * FROM {t} WHERE {empty}"))
            if c["allowed"]:
                values = ", ".join(f"'{v}'" for v in c["allowed"].split("|"))
                out.append((f"{where} is one of {c['allowed']}",
                            f"SELECT {n} FROM {t} WHERE {n} IS NOT NULL AND {n} <> '' AND {n} NOT IN ({values})"))
            if c["type"] in ("int", "float"):
                bad = [f"{n} < {c['min']}"] if c["min"] else []
                bad += [f"{n} > {c['max']}"] if c["max"] else []
                if c["type"] == "float":
                    bad.append(f"NOT isfinite({n})")
                if bad:
                    out.append((f"{where} is finite and inside [{c['min']}, {c['max']}]",
                                f"SELECT {n} FROM {t} WHERE {n} IS NOT NULL AND ({' OR '.join(bad)})"))
    return out


def threshold_checks() -> list[tuple[str, str]]:
    """(name, query) for the rules written in thresholds.yaml: a grade S row has every defect assessed that
    the analyser measures (decision D)."""
    empty = " OR ".join(f"{c} IS NULL" for c in assessed_defects())
    return [("a grade S row has every defect assessed", f"SELECT ir_id FROM irs WHERE grade = 'S' AND ({empty})")]


def sql_checks() -> list[tuple[str, str]]:
    """(name, query) for each `-- check:` block of checks.sql."""
    blocks = re.split(r"^-- check: (.+)$", (HERE / "checks.sql").read_text(), flags=re.M)[1:]
    return [(name.strip(), body.strip().rstrip(";")) for name, body in zip(blocks[::2], blocks[1::2])]


def expected_columns() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    with open(HERE / "columns.csv", newline="") as f:
        for c in csv.DictReader(f):
            out.setdefault(c["table"], []).append(c["name"])
    return out


def run(release: Path) -> list[Result]:
    con = connect(release)
    results = []
    for table, cols in expected_columns().items():
        have = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
        diff = f"missing {sorted(set(cols) - set(have))}, extra {sorted(set(have) - set(cols))}" if have != cols else ""
        results.append(Result(f"{table} has the columns of columns.csv, in order", int(bool(diff)), diff))
    for name, query in column_checks() + threshold_checks() + sql_checks():
        rows = con.execute(f"SELECT * FROM ({query}) LIMIT 3").df()
        n = con.execute(f"SELECT count(*) FROM ({query})").fetchone()[0] if len(rows) else 0
        results.append(Result(name, int(n), rows.head(3).to_dict("records").__repr__() if n else ""))
    irs = con.execute("SELECT ir_id, dataset, file, item FROM irs").df()
    again = pd.Series([ir_id(d, f, i) for d, f, i in zip(irs["dataset"], irs["file"], irs["item"])], index=irs.index)
    wrong = irs[again != irs["ir_id"]]
    results.append(Result("every ir_id is the hash of its file and item", len(wrong), wrong.head(3).to_dict("records").__repr__()))
    return results
