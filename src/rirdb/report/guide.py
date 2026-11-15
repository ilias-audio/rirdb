"""Field guide to public reverb datasets: catalogue, landscape statistics and gaps (DMRN+ 2026).

    rirdb dev report guide   -> reports/guide/guide.json, catalog.csv, spaces.csv, index.html

One row per registry dataset, in plain terms: what it holds, what its licence lets you do, where to get
it, what it publishes about its rooms and what the analysis found. One row per indexed space (category,
volume, median T30 / EDT / C80 / DRR of its clean IRs). Corpus-wide validity per octave band.
Facts checked on the web (hosting status, institution, country, licence re-checks, datasets that are not
in the registry yet) come from the web_check blocks of registry/<id>/dataset.yaml (report/survey.py).
"""

from __future__ import annotations

import csv
import gzip
import json
import math
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

import pandas as pd

from rirdb import paths
from rirdb.query import connect
from rirdb.registry import AVAIL_FIELDS, Dataset, load_registry
from rirdb.report.survey import load_survey
from rirdb.rooms import KIND_CATEGORIES, curated_spaces

OUT_DIR = paths.REPORTS_DIR / "guide"
BANDS = (63, 125, 250, 500, 1000, 2000, 4000, 8000)
FLAGS = ("flag_upsampled", "flag_band_limited", "flag_tonal", "flag_sweep_like", "flag_dropout",
         "flag_nonstationary_noise", "flag_quantized_tail", "flag_tail_truncated", "flag_no_noise_floor",
         "flag_short", "flag_low_fs", "flag_clipped", "flag_low_pnr")
OPEN_IDS = ("CC-BY-4.0", "CC-BY-3.0", "CC0", "MIT", "Apache-2.0", "ODC-By", "BSD", "LicenseRef-CC-PublicDomain")
# real spaces (rooms, outdoor, vehicles), one representation per position
SPACE_SQL = "ir_kind NOT IN ('anechoic', 'virtual', 'device', 'scale_model') AND preferred AND dup_of IS NULL"
# clean_rooms without its ir_kind condition: the guide applies the curated space types before choosing rooms
CLEAN_ANY_KIND = ("measured AND preferred AND dup_of IS NULL AND coalesce(error, '') = '' AND NOT "
                  "coalesce(flag_sweep_like OR flag_tonal OR flag_not_decaying OR flag_dropout, false)")


# Spaces corrected by hand, and the same physical room under several datasets, are rows of
# registry/<id>/spaces.csv; a library of modelled IRs sets `space_type` in its dataset.yaml.
# The guide applies them over the index's categories, which keep the old values until re-indexing.
@cache
def curated_spaces_all() -> dict[str, dict]:
    """{room_id: row of spaces.csv} for every dataset."""
    out = {}
    for d in load_registry(catalogued=False):
        for r in curated_spaces(d.id).to_dict("records"):
            out[f"{d.id}/{r['space']}"] = r
    return out


@cache
def dataset_space_types() -> dict[str, str]:
    """{dataset id: space type} for datasets that set one type for every space (Voxengo: modelled)."""
    return {d.id: d.space_type for d in load_registry(catalogued=False) if d.space_type}


def licence_terms(d: Dataset) -> dict:
    """What the licence lets a reader do, in four plain answers plus a class for colour coding."""
    lic = d.license
    spdx = lic.spdx
    if spdx == "LicenseRef-per-space":
        cls = "mixed"
    elif lic.non_commercial or spdx.endswith("NonCommercial") or spdx == "LicenseRef-S3A-academic":
        cls = "non_commercial"
    elif lic.share_alike or spdx.startswith("GPL-"):
        cls = "share_alike"
    elif spdx.startswith(OPEN_IDS):
        cls = "open"
    elif spdx == "LicenseRef-unspecified":
        cls = "unspecified"
    else:
        cls = "custom"
    commercial = {"open": "yes", "share_alike": "yes", "non_commercial": "no"}.get(cls, "unclear")
    if lic.training_use == "prohibited":
        training = "prohibited"
    elif cls == "non_commercial":
        training = "non_commercial_only"
    elif cls in ("open", "share_alike"):
        training = "allowed"
    else:
        training = "unclear"
    return {"class": cls, "commercial": commercial, "training": training,
            "redistribute": lic.uses.redistribute, "share_alike": lic.share_alike or spdx.startswith("GPL-"),
            "no_derivatives": lic.no_derivatives}


def dataset_rows() -> list[dict]:
    rows = []
    for d in load_registry(catalogued=False):
        ri = d.room_info
        rows.append({
            "id": d.id, "name": d.name, "year": d.year, "type": d.type, "status": d.status, "access": d.access,
            "wave": d.wave, "ir_kinds": d.ir_kinds, "capture_formats": d.capture_formats, "spatial": d.spatial,
            "fs_hz": d.fs_hz, "content": d.content,
            "expected_irs": d.expected.n_irs, "expected_rooms": d.expected.n_rooms, "expected_note": d.expected.note,
            "room_info": {f: getattr(ri, f) for f in AVAIL_FIELDS}, "room_info_where": ri.where,
            "licence": {"spdx": d.license.spdx, "url": d.license.url, "note": d.license.note, **licence_terms(d)},
            "source_kind": d.source.kind, "records": d.source.records, "homepage": d.homepage,
            "paper": d.paper.model_dump() if d.paper else None, "doi": d.doi, "notes": d.notes,
            "analysis_scope": d.analysis.scope,
        })
    return rows


def index_stats(con) -> dict[str, dict]:
    df = con.execute("""
        SELECT dataset_id, count(*) AS n_records, count(DISTINCT room_id) AS n_spaces,
               sum(n_channels) AS n_channels,
               count(*) FILTER (WHERE ir_kind = 'room') AS n_room_records,
               count(DISTINCT room_id) FILTER (WHERE ir_kind = 'room') AS n_room_spaces,
               count(DISTINCT condition_key) AS n_conditions,
               avg(CASE WHEN src_x IS NOT NULL AND rcv_x IS NOT NULL THEN 1 ELSE 0 END) AS share_positions,
               median(duration_s) AS duration_s_p50, max(duration_s) AS duration_s_max,
               count(*) FILTER (WHERE subsample_v1) AS n_in_subsample
        FROM irs GROUP BY 1""").df()
    out = {r["dataset_id"]: {k: v for k, v in r.items() if k != "dataset_id"} for r in df.to_dict("records")}
    for name, col in (("formats_found", "capture_format"), ("fs_found", "fs"), ("kinds_found", "ir_kind")):
        g = con.execute(f"SELECT dataset_id, {col} AS v, count(*) AS n FROM irs GROUP BY 1, 2 ORDER BY 1, 3 DESC").df()
        for ds, sub in g.groupby("dataset_id"):
            out.setdefault(ds, {})[name] = {str(v): int(n) for v, n in zip(sub["v"], sub["n"])}
    return out


def metric_stats(con) -> dict[str, dict]:
    flag_cols = {r[0] for r in con.execute("DESCRIBE corpus_dedup").fetchall()}
    flags = ", ".join(f"avg(coalesce({f}, false)::INTEGER) AS {f}" for f in FLAGS if f in flag_cols)
    room = f"FILTER (WHERE {SPACE_SQL})"
    df = con.execute(f"""
        SELECT dataset_id, count(*) AS n_analysed,
               count(*) FILTER (WHERE grade = 'A') AS grade_a, count(*) FILTER (WHERE grade = 'B') AS grade_b,
               count(*) FILTER (WHERE grade = 'C') AS grade_c, count(*) FILTER (WHERE grade = 'D') AS grade_d,
               quantile_cont(t30_mid, 0.1) {room} AS t30_p10, quantile_cont(t30_mid, 0.5) {room} AS t30_p50,
               quantile_cont(t30_mid, 0.9) {room} AS t30_p90,
               quantile_cont(t30_mid_or_t20, 0.1) {room} AS t30_or_t20_p10,
               quantile_cont(t30_mid_or_t20, 0.5) {room} AS t30_or_t20_p50,
               quantile_cont(t30_mid_or_t20, 0.9) {room} AS t30_or_t20_p90,
               avg(CASE WHEN valid_t30_500 AND valid_t30_1000 THEN 1 ELSE 0 END) {room} AS valid_t30_mid_rooms,
               median(edt_mid) {room} AS edt_p50, median(c80_mid) {room} AS c80_p50,
               median(drr_bb) {room} AS drr_p50, median(sti) {room} AS sti_p50,
               median(pnr_db) AS pnr_p50, median(bw_eff_hz) AS bw_eff_p50, median(duration_file_s) AS file_s_p50,
               avg(CASE WHEN valid_t30_500 AND valid_t30_1000 THEN 1 ELSE 0 END) AS valid_t30_mid,
               avg(CASE WHEN dup_of IS NOT NULL THEN 1 ELSE 0 END) AS share_duplicates,
               count(*) FILTER (WHERE dup_of IS NOT NULL) AS n_duplicates,
               {flags}
        FROM corpus_dedup WHERE coalesce(error, '') = '' GROUP BY 1""").df()
    return {r["dataset_id"]: {k: v for k, v in r.items() if k != "dataset_id"} for r in df.to_dict("records")}


def space_rows(con) -> list[dict]:
    """One row per indexed space; medians over its clean measured IRs (production libraries: over all
    preferred IRs, since they are not `measured`)."""
    df = con.execute(f"""
        WITH c AS (SELECT room_id, count(*) AS n_clean, median(t30_mid) AS t30, median(t30_mid_or_t20) AS t30_or_t20,
                          avg(CASE WHEN valid_t30_500 AND valid_t30_1000 THEN 1 ELSE 0 END) AS share_valid_t30,
                          median(edt_mid) AS edt, median(c80_mid) AS c80, median(drr_bb) AS drr
                   FROM corpus_dedup WHERE {CLEAN_ANY_KIND} GROUP BY 1),
             p AS (SELECT room_id, median(t30_mid) AS t30_any FROM corpus_dedup
                   WHERE preferred AND dup_of IS NULL AND coalesce(error, '') = '' GROUP BY 1),
             a AS (SELECT room_id, count(*) AS n_analysed FROM corpus GROUP BY 1)
        SELECT r.dataset_id, r.room_id, r.room_label, r.category, r.ir_kind, r.volume_m3, r.n_irs,
               c.n_clean, c.t30, c.t30_or_t20, c.share_valid_t30, c.edt, c.c80, c.drr, p.t30_any, a.n_analysed
        FROM rooms r LEFT JOIN c USING (room_id) LEFT JOIN p USING (room_id) LEFT JOIN a USING (room_id)
        ORDER BY r.dataset_id, r.room_id""").df()
    rows = df.to_dict("records")
    curated, defaults = curated_spaces_all(), dataset_space_types()
    for r in rows:
        c = curated.get(r["room_id"], {})
        r["physical_id"] = c.get("same_as") or r["room_id"]
        before = (r["category"], r["ir_kind"])
        if r["dataset_id"] in defaults:
            r["category"] = defaults[r["dataset_id"]]
            if r["category"] in KIND_CATEGORIES:
                r["ir_kind"] = r["category"]
        if c.get("space_type"):
            r["category"] = c["space_type"]
        if c.get("ir_kind"):
            r["ir_kind"] = c["ir_kind"]
        r["category_fixed"] = (r["category"], r["ir_kind"]) != before
    return rows


def validity(con) -> dict:
    """T30 validity per octave band over clean measured rooms: per IR, per dataset (median), per space
    (share of spaces with at least one valid value)."""
    cols = ", ".join(f"avg(coalesce(valid_t30_{b}, false)::INTEGER) AS b{b}" for b in BANDS)
    anyv = ", ".join(f"max(coalesce(valid_t30_{b}, false)::INTEGER) AS b{b}" for b in BANDS)
    per_ir = con.execute(f"SELECT {cols} FROM clean_rooms").df().iloc[0]
    per_ds = con.execute(f"SELECT dataset_id, {cols} FROM clean_rooms GROUP BY 1").df()
    per_space = con.execute(f"SELECT room_id, {anyv} FROM clean_rooms GROUP BY 1").df()
    return {str(b): {"ir": float(per_ir[f"b{b}"]), "dataset_median": float(per_ds[f"b{b}"].median()),
                     "space": float(per_space[f"b{b}"].mean())} for b in BANDS} | {
        "n_irs": int(con.execute("SELECT count(*) FROM clean_rooms").fetchone()[0]),
        "n_datasets": int(len(per_ds)), "n_spaces": int(len(per_space))}


def file_stats() -> dict[str, dict]:
    """File types from registry/<id>/files.tsv.gz; download size and checksum origin from registry/<id>/downloads.tsv."""
    out: dict[str, dict] = {}
    for f in sorted(paths.DATASETS_DIR.glob("*/files.tsv.gz")):
        ds = f.parent.name
        ext: dict[str, int] = {}
        with gzip.open(f, "rt") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                name = r["relpath"].rsplit("/", 1)[-1]
                e = name.rsplit(".", 1)[-1].lower() if "." in name else "(none)"
                ext[e] = ext.get(e, 0) + 1
        out.setdefault(ds, {})["extensions"] = dict(sorted(ext.items(), key=lambda kv: -kv[1]))
    for f in sorted(paths.DATASETS_DIR.glob("*/downloads.tsv")):
        ds = f.parent.name
        with open(f, newline="") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
        algos = {r["algo"] for r in rows}
        out.setdefault(ds, {}).update({
            "download_bytes": sum(int(r["size"]) for r in rows if r["size"].isdigit()), "n_download_files": len(rows),
            # md5 comes from the repository (Zenodo, DepositOnce, figshare); sha256 was computed here
            "provider_checksum": bool(algos & {"md5"}),
        })
    for ds in out:
        st = paths.state_file(ds)
        if st.exists():
            inv = json.loads(st.read_text()).get("inventory") or {}
            out[ds]["disk_bytes"] = inv.get("bytes")
            out[ds]["n_files"] = inv.get("n_files")
    return out


def openair_licences() -> dict[str, int]:
    lic = curated_spaces("openair").get("licence")
    return {} if lic is None else {k: int(v) for k, v in lic[lic != ""].value_counts().items()}


def _clean(o):
    """JSON-safe: NaN/inf -> None, numpy scalars -> Python."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if o is pd.NA or o is pd.NaT:
        return None
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        o = o.item()
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, float):
        return round(o, 4)
    return o


def build(out_dir: Path = OUT_DIR) -> Path:
    from rirdb.analysis import ANALYZER_VERSION

    out_dir.mkdir(parents=True, exist_ok=True)
    con = connect()
    idx, met, files = index_stats(con), metric_stats(con), file_stats()
    rows = dataset_rows()
    for r in rows:
        r["index"] = idx.get(r["id"])
        r["metrics"] = met.get(r["id"])
        r["files"] = files.get(r["id"])
    survey = load_survey()
    for r in rows:
        r["survey"] = (survey.get("registry") or {}).get(r["id"])
    data = _clean({
        "generated": f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        "analyzer_version": ANALYZER_VERSION,
        "datasets": rows,
        "new_datasets": survey.get("new") or [],
        "survey_date": survey.get("date"),
        "spaces": space_rows(con),
        "validity_t30": validity(con),
        "openair_licences": openair_licences(),
    })
    (out_dir / "guide.json").write_text(json.dumps(data, separators=(",", ":"), allow_nan=False, default=str))
    write_csv(data, out_dir)
    tpl_f = Path(__file__).parent / "guide_template.html"
    if tpl_f.exists():
        # the template is a page body (as claude.ai artifacts expect); index.html wraps it into a
        # standalone document for GitHub Pages or a local browser
        js = json.dumps(data, separators=(",", ":"), default=str).replace("</", "<\\/")
        page = tpl_f.read_text().replace("/*GUIDE_JSON*/null", js)
        head, end, body = page.partition("</style>")
        (out_dir / "index.html").write_text(
            '<!doctype html>\n<html lang="en-GB">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            f"{head}{end}\n</head>\n<body>{body}</body>\n</html>\n")
        return out_dir / "index.html"
    return out_dir / "guide.json"


def write_csv(data: dict, out_dir: Path) -> None:
    """catalog.csv (one row per dataset, registry and not yet registered, with the survey's plain-language
    licence reading applied, as on the page) and spaces.csv: the guide's tables as plain files."""
    cols = ["id", "name", "year", "type", "in_registry", "analysed", "availability", "capture_formats", "fs_hz",
            "n_irs", "n_spaces", "licence", "licence_class", "commercial_use", "ml_training", "redistribute_audio",
            "host", "homepage", "doi", "institution", "country", "content"]
    with open(out_dir / "catalog.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in data["datasets"]:
            ix, sv, lic = r.get("index") or {}, r.get("survey") or {}, r["licence"]
            avail = sv.get("availability") or {"broken": "broken", "manual": "manual"}.get(r["status"], "open")
            w.writerow([r["id"], r["name"], r["year"], r["type"], "yes", "yes" if r.get("metrics") else "no", avail,
                        "|".join(r["capture_formats"]), "|".join(str(f) for f in r["fs_hz"]),
                        sv.get("n_irs_display", ix.get("n_records", r["expected_irs"])),
                        sv.get("n_rooms_display", ix.get("n_spaces", r["expected_rooms"])),
                        sv.get("licence_label", lic["spdx"]), sv.get("licence_class", lic["class"]),
                        sv.get("commercial", lic["commercial"]), sv.get("training", lic["training"]),
                        sv.get("redistribute", lic["redistribute"]), sv.get("host", r["source_kind"]),
                        r["homepage"], None if sv.get("doi_is_paper") else r["doi"],
                        sv.get("institution"), sv.get("country"), r["content"]])
        for r in data["new_datasets"]:
            w.writerow([r.get("id"), r.get("name"), r.get("year"), r.get("type"), "no", "no", r.get("availability"),
                        "|".join(r.get("fams") or []), "|".join(str(f) for f in r.get("fs_hz") or []),
                        r.get("n_irs"), r.get("n_rooms"), r.get("licence_label"), r.get("licence_class"),
                        r.get("commercial_use"), r.get("ml_training"), r.get("redistribution"), r.get("host"),
                        r.get("landing_url"), r.get("doi"), r.get("institution"), r.get("country"),
                        r.get("description")])
    pd.DataFrame(data["spaces"]).to_csv(out_dir / "spaces.csv", index=False)
