"""The registry: one folder per dataset (registry/<id>/), all 222 in one format."""

import csv

import pandas as pd
import pytest

from rirdb import paths
from rirdb.index import apply_room_licenses
from rirdb.licences import strictest
from rirdb.registry import License, load_registry
from rirdb.rooms import curated_spaces

DATASETS = load_registry()
SPACE_COLUMNS = ["space", "label", "space_type", "ir_kind", "volume_m3", "dims_m", "same_as",
                 "licence", "redistribute", "licence_source", "note", "licence_note"]


def allowed_space_types() -> set[str]:
    with open(paths.REPO_ROOT / "src" / "rirdb" / "columns.csv", newline="") as f:
        row = next(r for r in csv.DictReader(f) if r["table"] == "irs" and r["name"] == "space_type")
    return set(row["allowed"].split("|"))


def test_all_222_datasets_are_in_the_registry():
    assert len(DATASETS) == 222
    # 160 were only catalogued on 14 November 2026; step 3 registers them one by one, so the number only falls.
    assert sum(d.status == "catalogued" for d in DATASETS) <= 160


def test_every_folder_is_a_dataset():
    """Besides the dataset folders, the registry holds only these (generated, or about storage)."""
    others = {p.name for p in paths.REGISTRY_DIR.iterdir() if not (p / "dataset.yaml").exists()}
    assert others <= {"compiled", "probes", "storage.tsv", "jnd.yaml"}, others


def test_pinned_files_belong_to_downloaded_datasets():
    for d in DATASETS:
        if paths.downloads_tsv(d.id).exists() or paths.files_tsv(d.id).exists():
            assert d.status != "catalogued", d.id


def test_active_datasets_say_where_room_information_is():
    missing = [d.id for d in DATASETS if d.status == "active" and not d.room_info.where]
    assert not missing, missing


def test_conflicting_licences_list_every_statement_and_use_the_strictest():
    conflicts = {d.id: d.license for d in DATASETS if d.license.statements}
    assert len(conflicts) == 12
    for i, lic in conflicts.items():
        assert len(lic.statements) >= 2, i
        assert lic.spdx == strictest([s.licence for s in lic.statements]), i


def test_strictest_prefers_the_licence_that_allows_least():
    assert strictest(["CC-BY-4.0", "CC-BY-NC-SA-4.0"]) == "CC-BY-NC-SA-4.0"
    assert strictest(["CC-BY-4.0", "GPL-3.0-only"]) == "GPL-3.0-only"      # same uses, one more condition
    assert strictest(["CC-BY-4.0", "LicenseRef-unspecified"]) == "LicenseRef-unspecified"


def test_a_licence_that_ignores_its_statements_is_refused():
    with pytest.raises(ValueError, match="strictest"):
        License(spdx="CC-BY-4.0", statements=[{"licence": "CC-BY-4.0", "where": "record"},
                                              {"licence": "CC-BY-NC-4.0", "where": "readme"}])


def test_only_very_dense_datasets_have_a_cap():
    caps = {d.id: d.cap for d in DATASETS if d.cap}
    assert {i: caps[i] for i in ("sriracha", "miracle", "meshgrid3d")} == {"sriracha": 500, "miracle": 500,
                                                                           "meshgrid3d": 500}
    # for the 0.9 preview only: what analyser v1 measured (master-plan.md, step 1)
    assert set(caps) == {"sriracha", "miracle", "meshgrid3d", "mp_rir", "meshrir", "soundcam"}


@pytest.mark.parametrize("dataset_id", sorted(p.parent.name for p in paths.DATASETS_DIR.glob("*/spaces.csv")))
def test_spaces_tables_validate(dataset_id):
    tab = curated_spaces(dataset_id)
    assert list(tab.columns) == SPACE_COLUMNS
    assert tab["space"].is_unique
    assert set(tab["space_type"]) - {""} <= allowed_space_types()
    for _, r in tab[tab["licence"] != ""].iterrows():
        License(spdx=r["licence"], redistribute_audio=r["redistribute"] or "unknown")
    ids = {d.id for d in DATASETS}
    for same in tab["same_as"][tab["same_as"] != ""]:
        assert same.split("/", 1)[0] in ids, f"same_as {same!r} names no dataset"


def test_room_licences_override_the_dataset_licence():
    df = pd.DataFrame({"room_key": ["theatre41", "elveden-hall-suffolk-england", "not-in-table"],
                       "license_spdx": "LicenseRef-per-space", "redistribute_audio": "unknown",
                       "training_use": "unknown", "license_source": ""})
    unmatched = apply_room_licenses("openair", df)
    assert df["license_spdx"].tolist() == ["CC-BY-4.0", "CC-BY-NC-ND-3.0", "LicenseRef-per-space"]
    assert df["redistribute_audio"].tolist() == ["yes", "unaltered_only", "unknown"]
    assert df.loc[0, "training_use"] == "allowed" and df.loc[0, "license_source"].startswith("https://web.archive.org/")
    assert "theatre41" not in unmatched
