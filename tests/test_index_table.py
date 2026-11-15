"""The index table, tables/<id>/index.parquet (data-model.md, sections 3, 4 and 8)."""

import os
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from rirdb import paths
from rirdb.adapters.base import make_ir_id
from rirdb.index import ir_id, sample_by_cap, split_key

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("key", ["Room1/Speak1_Mic2.wav", "a.wav|b.wav", "SR1-C1.h5#12,40", "f.sofa#3#1",
                                 "x.mat#tau/1,2,3"])
def test_the_id_keeps_the_old_hex(key):
    file, item = split_key(key)
    assert (f"{file}#{item}" if item else file) == key
    assert ir_id("ds", file, item).split("-", 1)[1] == make_ir_id("ds", key).split(":", 1)[1]


def test_the_sample_is_nested_and_per_group():
    df = pd.DataFrame({"ir_id": [f"d-{i:016x}" for i in range(10)], "space": ["a"] * 6 + ["b"] * 4,
                       "condition": "", "layout": "mono_omni"})
    two, three = sample_by_cap(df, 2), sample_by_cap(df, 3)
    assert two.sum() == 4 and three.sum() == 6
    assert (three | ~two).all()                       # raising the cap adds rows and replaces none
    assert sample_by_cap(df, None).all()


def pin(tmp_path: Path, lines: list[str], old: Path | None = None) -> list[str]:
    lock = tmp_path / "downloads.tsv"
    header = "relpath\turl\tsize\talgo\tchecksum\tprovider_version\tresolved_at"
    lock.write_text("\n".join([header, *lines]) + "\n")
    cmd = f'source "{REPO}/fetch/lib_common.sh"; pin_extract_to "{lock}"' + (f' "{old}"' if old else "")
    subprocess.run(["bash", "-c", cmd], check=True, env={**os.environ, "RIRDB_ROOT": str(tmp_path)})
    return [ln.split("\t")[-1] for ln in lock.read_text().splitlines()[1:]]


def test_a_new_archive_never_moves_the_files_already_pinned(tmp_path):
    row = "{}\thttps://x/{}\t1\tmd5\tabc\tv1\tnow"
    assert pin(tmp_path, [row.format("a.zip", "a.zip"), row.format("readme.txt", "readme.txt")]) == [".", "-"]
    old = tmp_path / "old.tsv"
    (tmp_path / "downloads.tsv").rename(old)
    # a second archive arrives: the first stays where it was unpacked, the new one gets its own folder
    assert pin(tmp_path, [row.format("a.zip", "a.zip"), row.format("b.tar.gz", "b.tar.gz")], old) == [".", "b"]


def indexed() -> list[str]:
    old = paths.data_root() / "index" / "irs"
    return sorted(p.parent.name for p in paths.data_root().glob("tables/*/index.parquet")
                  if (old / f"dataset={p.parent.name}" / "part-0.parquet").exists())


@pytest.mark.skipif(not indexed(), reason="needs the database (RIRDB_ROOT) with tables/ written")
@pytest.mark.parametrize("dataset_id", indexed())
def test_every_ir_keeps_the_hex_of_its_old_id(dataset_id):
    old = pd.read_parquet(paths.data_root() / "index" / "irs" / f"dataset={dataset_id}" / "part-0.parquet",
                          columns=["ir_id"])["ir_id"]
    new = pd.read_parquet(paths.tables_dir(dataset_id) / "index.parquet", columns=["ir_id", "file", "item"])
    assert set(new["ir_id"].str.split("-", n=1).str[1]) == set(old.str.split(":", n=1).str[1])
    recomputed = [ir_id(dataset_id, f, i) for f, i in zip(new["file"], new["item"])]
    assert recomputed == new["ir_id"].tolist()
