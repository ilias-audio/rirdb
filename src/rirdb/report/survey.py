"""The catalogue in the shape of the old registry/survey.json, for the guide and the site.

Until 14 November 2026 the 160 datasets that rirdb had not downloaded lived in registry/survey.json,
with the web-check facts of the other 62 next to them. Both now live in registry/<id>/dataset.yaml
(status `catalogued`, and the `web_check` block). The guide and the site still read the old shape;
this module rebuilds it, so they keep working until the site is rewritten for the release (step 6).

The licence words come from the registry licence and licences.csv (through guide.licence_terms),
so a dataset reads the same on every page.
"""

from __future__ import annotations

import re

from rirdb.registry import Dataset, load_registry

NOTE = ("Facts checked on the web for the Reverb Dataset Field Guide (licence terms, availability, hosts, institutions, "
        "countries) and plain-language notes from the dataset audit (docs/decisions/dataset-findings.md). "
        "Kept in the web_check block of each registry/<id>/dataset.yaml.")


def short_label(spdx: str) -> str:
    """CC-BY-NC-SA-4.0 -> CC BY-NC-SA 4.0; LicenseRef-research-only -> research only."""
    m = re.match(r"^CC-(.*?)(?:-(\d\.\d))?$", spdx)
    if m:
        return "CC " + m.group(1) + (" " + m.group(2) if m.group(2) else "")
    return spdx.removeprefix("LicenseRef-").replace("-", " ")


def licence_words(d: Dataset) -> dict:
    """The survey's licence fields, read from the registry licence."""
    from rirdb.report.guide import licence_terms

    t = licence_terms(d)
    st = d.license.statements
    label = " vs ".join(f"{short_label(s.licence)} ({s.where})" for s in st) if st else short_label(d.license.spdx)
    return {"licence_label": label, "licence_class": "conflict" if st else t["class"],
            "commercial_use": t["commercial"], "ml_training": t["training"],
            "redistribution": {"unknown": "unclear"}.get(t["redistribute"], t["redistribute"])}


def catalogued_record(d: Dataset) -> dict:
    """One record of the old `new` list: a dataset catalogued from its web pages only."""
    wc = d.web_check.model_dump() if d.web_check else {}
    return {"id": d.id, "name": d.name, "found_as": wc.get("found_as"), "year": d.year, "type": d.type,
            "availability": wc.get("availability"), "fams": wc.get("families") or [], "fs_hz": d.fs_hz,
            "n_irs": d.expected.n_irs, "n_rooms": d.expected.n_rooms,
            "licence_spdx": d.license.spdx, **licence_words(d), "licence_note": d.license.note,
            "host": wc.get("host"), "landing_url": d.homepage, "doi": d.doi, "description": d.content,
            "room_info": d.room_info.model_dump(exclude={"where"}), "size": wc.get("size"),
            "file_formats": wc.get("file_formats"), "institution": wc.get("institution"),
            "country": wc.get("country"), "caveats": wc.get("caveats") or [],
            "evidence_urls": wc.get("evidence_urls") or [], "confidence": wc.get("confidence")}


def registry_overlay(d: Dataset) -> dict:
    """The old `registry` entry: web-check facts about a dataset rirdb downloads."""
    wc = d.web_check.model_dump(exclude={"checked"}, exclude_none=True) if d.web_check else {}
    wc = {k: v for k, v in wc.items() if v != []}
    if d.license.statements:
        words = licence_words(d)
        wc |= {"licence_label": words["licence_label"], "licence_class": "conflict"}
    return wc


def load_survey() -> dict:
    datasets = load_registry()
    checked = [d.web_check.checked for d in datasets if d.web_check and d.web_check.checked]
    return {"date": max(checked) if checked else None, "note": NOTE,
            "registry": {d.id: registry_overlay(d) for d in datasets if d.status != "catalogued"},
            "new": [catalogued_record(d) for d in datasets if d.status == "catalogued"]}
