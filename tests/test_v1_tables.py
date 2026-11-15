"""Analyser v1's measurements in tables/<id>/v1/ (step 1, task 4): one row per sampled IR, nothing missing.

Needs the database (RIRDB_ROOT); skipped where tables/<id>/v1/ has not been written."""

import pandas as pd
import pytest

from rirdb import paths


def converted() -> list[str]:
    return sorted(p.parent.parent.name for p in paths.data_root().glob("tables/*/v1/measured.parquet"))


@pytest.mark.skipif(not converted(), reason="needs the database (RIRDB_ROOT) with tables/<id>/v1/ written")
@pytest.mark.parametrize("dataset_id", converted())
def test_every_sampled_ir_has_its_v1_measurement(dataset_id):
    t = paths.tables_dir(dataset_id)
    index = pd.read_parquet(t / "index.parquet", columns=["ir_id", "sampled", "audio_key"])
    measured = pd.read_parquet(t / "v1" / "measured.parquet", columns=["ir_id", "audio_key", "analyser"])
    sampled = index[index["sampled"]].set_index("ir_id")["audio_key"]
    assert measured["ir_id"].is_unique
    assert set(measured["ir_id"]) == set(sampled.index)
    assert (measured.set_index("ir_id")["audio_key"] == sampled.loc[measured["ir_id"]]).all()
    assert set(measured["analyser"]) == {"v1"}


@pytest.mark.skipif(not converted(), reason="needs the database (RIRDB_ROOT) with tables/<id>/v1/ written")
@pytest.mark.parametrize("dataset_id", converted())
def test_the_broadband_row_is_v1s_bb_columns(dataset_id):
    t = paths.tables_dir(dataset_id)
    bands = pd.read_parquet(t / "v1" / "bands.parquet")
    if bands.empty:                       # MIRACLE: 32 ms IRs, nothing measured per band
        return
    bb = bands[bands["series"] == "broadband"].set_index("ir_id")
    assert bb.index.is_unique and bb["band_hz"].isna().all()
    assert set(bands["series"]) <= {"octave", "broadband"}
    wide = pd.read_parquet(paths.data_root() / "metrics" / "v1" / dataset_id / "wide.parquet",
                           columns=["ir_id", "t30_bb", "decay_range_db_bb"])
    wide["ir_id"] = wide["ir_id"].str.replace(":", "-", n=1)
    wide = wide.set_index("ir_id").loc[bb.index]
    pd.testing.assert_series_equal(bb["t30"], wide["t30_bb"], check_names=False)
    pd.testing.assert_series_equal(bb["decay_range_db"], wide["decay_range_db_bb"], check_names=False)
