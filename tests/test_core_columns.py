"""columns.csv ranges that a dataset depends on, so a later edit cannot block its release again."""

import csv
from pathlib import Path

COLUMNS = Path(__file__).resolve().parents[1] / "src" / "rirdb" / "columns.csv"


def column(table: str, name: str) -> dict:
    with open(COLUMNS, newline="") as f:
        return next(c for c in csv.DictReader(f) if (c["table"], c["name"]) == (table, name))


def test_the_lowest_sample_rate_admits_aalborg_low_frequency():
    """aalborg_low_frequency is sampled at 1200 Hz (its low-frequency measurements); build's range check
    would fail every one of its 512 IRs with a higher minimum."""
    assert float(column("irs", "fs")["min"]) <= 1200
