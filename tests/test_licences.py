"""Licence readings (rirdb.licences): what each licence allows, from licences.csv."""

import csv

import pytest
import yaml

from rirdb import paths
from rirdb.licences import UnknownLicence, permitted, table
from rirdb.registry import License


def ids_in_use() -> set[str]:
    """Every licence id written anywhere in the registry: dataset files (and their statements), spaces tables.
    Read as plain YAML and CSV, so an unknown id shows here and not as a validation error."""
    ids = set()
    for f in paths.DATASETS_DIR.glob("*/dataset.yaml"):
        lic = yaml.safe_load(f.read_text())["license"]
        ids |= {lic["spdx"]} | {s["licence"] for s in lic.get("statements") or []}
    for f in paths.DATASETS_DIR.glob("*/spaces.csv"):
        with open(f, newline="") as fh:
            ids |= {row["licence"] for row in csv.DictReader(fh) if row["licence"]}
    return ids


def test_every_licence_id_in_the_catalogue_has_a_row():
    missing = sorted(ids_in_use() - set(table()))
    assert not missing, f"add these ids to licences.csv: {missing}"


def test_the_table_agrees_with_the_licence_names():
    """The id says NC, SA or ND: the row must say the same. This is the check the old prefix test lacked."""
    for spdx, u in table().items():
        if not spdx.startswith("CC-"):
            continue
        parts = spdx.split("-")
        assert u.non_commercial == ("NC" in parts), spdx
        assert u.share_alike == ("SA" in parts), spdx
        assert u.no_derivatives == ("ND" in parts), spdx
        assert u.commercial == ("no" if "NC" in parts else "yes"), spdx
        assert u.redistribute == ("unaltered_only" if "ND" in parts else "yes"), spdx
        assert u.research == "yes" and u.training == "yes", spdx


def test_open_licence_allows_everything():
    u = permitted("CC-BY-4.0")
    assert (u.research, u.commercial, u.training, u.redistribute) == ("yes", "yes", "yes", "yes")
    assert u.conditions == ("attribution",)


@pytest.mark.parametrize("spdx", ["CC0-1.0", "CC-PDM-1.0", "LicenseRef-CC-PublicDomain"])
def test_public_domain_has_no_conditions(spdx):
    u = permitted(spdx)
    assert (u.research, u.commercial, u.training, u.redistribute) == ("yes", "yes", "yes", "yes")
    assert u.conditions == ()


@pytest.mark.parametrize("spdx", ["CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-NC-SA-3.0", "CC-BY-NC-ND-3.0"])
def test_non_commercial_is_never_commercial(spdx):
    u = permitted(spdx)
    assert u.commercial == "no" and u.non_commercial
    assert u.training == "yes"      # training is allowed, non-commercially


@pytest.mark.parametrize("spdx", ["LicenseRef-unspecified", "LicenseRef-per-space", "LicenseRef-custom",
                                  "LicenseRef-EchoThief"])
def test_unknown_terms_are_unknown(spdx):
    u = permitted(spdx)
    assert (u.research, u.commercial, u.training, u.redistribute) == ("unknown",) * 4


def test_an_id_without_a_row_is_an_error():
    with pytest.raises(UnknownLicence):
        permitted("CC-BY-NC-5.0")
    with pytest.raises(ValueError):
        License(spdx="CC-BY-NC-5.0")


def test_explicit_answers_win():
    u = permitted("CC-BY-4.0", training="no", redistribute="no")
    assert u.training == "no" and u.redistribute == "no" and u.commercial == "yes"
    assert permitted("LicenseRef-custom", commercial="yes").commercial == "yes"
    assert permitted("LicenseRef-TAU-NonCommercial", redistribute="unaltered_only").redistribute == "unaltered_only"
    assert permitted("CC-BY-4.0", redistribute="unknown").redistribute == "yes"   # "unknown" is not an answer
    with pytest.raises(ValueError):
        permitted("CC-BY-4.0", training="maybe")


def test_registry_training_reading():
    """The bug this replaces: every CC-BY-NC-* licence used to read as training "allowed"."""
    assert License(spdx="CC-BY-4.0").training == "allowed"
    for spdx in ("CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-NC-SA-3.0", "CC-BY-NC-ND-3.0"):
        assert License(spdx=spdx).training == "non_commercial_only", spdx
    assert License(spdx="CC-BY-NC-4.0", training_use="allowed").training == "non_commercial_only"
    assert License(spdx="LicenseRef-EchoThief", training_use="prohibited").training == "prohibited"
    assert License(spdx="LicenseRef-unspecified").training == "unknown"
    assert License(spdx="LicenseRef-unspecified", training_use="ask").training == "ask"
