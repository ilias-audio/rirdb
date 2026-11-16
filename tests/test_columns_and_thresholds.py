"""The two plain files that define the release tables: columns.csv and thresholds.yaml (data-model.md, R5)."""

import csv
import re
import typing
from pathlib import Path

import yaml

from rirdb import licences
from rirdb.registry import CaptureFormat

SRC = Path(__file__).resolve().parents[1] / "src" / "rirdb"
HEADER = ["table", "name", "type", "unit", "allowed", "min", "max", "applies_when", "empty_means", "meaning", "origin"]
TABLES = {"irs", "bands", "spaces", "datasets", "downloads", "files", "all_irs", "readers"}
TYPES = {"string", "int", "float", "bool"}
ORIGINS = {"registry", "pin", "index", "measure", "build"}


def columns() -> list[dict]:
    with open(SRC / "columns.csv", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == HEADER
        return list(reader)


def thresholds() -> dict:
    return yaml.safe_load((SRC / "thresholds.yaml").read_text())


def irs() -> dict[str, dict]:
    return {c["name"]: c for c in columns() if c["table"] == "irs"}


def test_every_row_is_complete_and_unique():
    rows = columns()
    keys = [(c["table"], c["name"]) for c in rows]
    assert len(keys) == len(set(keys)), "a column is listed twice"
    for c in rows:
        where = f'{c["table"]}.{c["name"]}'
        assert c["table"] in TABLES, where
        assert re.fullmatch(r"[a-z0-9_]+", c["name"]), where
        assert c["type"] in TYPES, where
        assert c["origin"] in ORIGINS, where
        assert c["applies_when"] and c["empty_means"] and c["meaning"], where
        if c["allowed"]:
            assert c["type"] == "string", f"{where}: a list of allowed values needs type string"
        if c["min"] or c["max"]:
            assert c["type"] in ("int", "float"), f"{where}: a range needs a number"
        if c["min"] and c["max"]:
            assert float(c["min"]) < float(c["max"]), where


def test_the_ir_table_has_91_columns():
    assert len(irs()) == 91


def test_closed_lists_agree_with_the_code():
    cols = irs()
    assert cols["layout"]["allowed"].split("|") == list(typing.get_args(CaptureFormat))
    for name in ("use_research", "use_commercial", "use_training"):
        assert cols[name]["allowed"].split("|") == list(licences.ANSWERS)
    assert cols["redistribute"]["allowed"].split("|") == list(licences.REDISTRIBUTE_ANSWERS)


def test_each_defect_column_has_its_thresholds_and_the_other_way_round():
    defects = thresholds()["defects"]
    in_table = {n.removeprefix("defect_") for n in irs() if n.startswith("defect_")}
    assert in_table == set(defects)
    assert all(isinstance(d["major"], bool) for d in defects.values())
    major = sum(d["major"] for d in defects.values())
    minor = sum(not d["major"] for d in defects.values())
    minor += sum(p["counts_as_minor_defect"] for p in thresholds()["processing"].values())
    assert irs()["defects_major"]["max"] == str(major)
    assert irs()["defects_minor"]["max"] == str(minor)


def test_each_detected_tag_has_its_column():
    for tag in thresholds()["processing"]:
        assert f"proc_{tag}" in irs(), tag


def test_the_grade_bands_are_octaves_of_the_table():
    g = thresholds()["grades"]
    for band in g["s_and_a_bands_hz"] + g["b_bands_hz"] + thresholds()["validity"]["mid"]["bands_hz"]:
        assert f"t30_{band}" in irs(), band
    assert g["major_defect_caps_at"] in irs()["grade"]["allowed"].split("|")
    assert set(g["empty_for_space_types"]) <= set(irs()["space_type"]["allowed"].split("|"))


def test_every_reference_to_thresholds_yaml_resolves():
    """columns.csv names thresholds by path, e.g. (validity.t30); each path must exist."""
    t = thresholds()
    refs = set()
    for c in columns():
        refs |= set(re.findall(r"\b((?:validity|defects|processing|grades)\.[a-z0-9_.]+)", c["empty_means"] + " " + c["meaning"]))
    assert refs, "columns.csv refers to no threshold"
    for ref in refs:
        node = t
        for part in ref.split("."):
            assert isinstance(node, dict) and part in node, f"columns.csv refers to {ref}, which thresholds.yaml lacks"
            node = node[part]


def test_every_threshold_is_a_number_or_a_list_of_them():
    """A threshold is data. Text belongs in comments, so `build` never has to parse a rule."""
    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            assert all(isinstance(v, (int, float, str)) for v in node), path
        else:
            assert isinstance(node, (int, float, bool)) or path.endswith("caps_at"), path
    walk(thresholds(), "")
