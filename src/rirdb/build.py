"""`build`: the release tables, made from the registry and $RIRDB_ROOT/tables/, with no audio.

    rirdb dev build      -> $RIRDB_ROOT/release/candidate/, then every check of checks.py

Inputs (data-model.md, sections 5 and 8): registry/<id>/ (curated and pinned facts), tables/<id>/index.parquet
(read from the files), tables/<id>/<analyser>/measured.parquet and bands.parquet (measured), and the plain
files next to this one: columns.csv (every published column), thresholds.yaml (every validity, defect, tag and
grade threshold) and licences.csv. Everything users see that is a judgement (validity, defects, processing
tags, grades, duplicates, permitted uses) is decided here, so changing a threshold only needs a new build.

The 0.9 preview is built from analyser v1, with v1's own validity per band and the flags v1 decided while
reading the audio (master-plan.md, step 1). Each place that uses them says so.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rirdb import paths
from rirdb.fetch import sha256_of
from rirdb.registry import AVAIL_FIELDS, Dataset, License, WebCheck, get_dataset, load_registry
from rirdb.rooms import KIND_CATEGORIES, categorize, curated_spaces

HERE = Path(__file__).parent
ANALYSER = "v1"
OCTAVES = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]     # t30_<band> columns of irs
OCTAVE_HZ = {31: 31.5}                                                # column name -> nominal centre
FORMAT_OF_LAYOUT = {"mono_omni": "mono", "stereo": "stereo", "binaural": "binaural", "foa_fuma": "ambisonic",
                    "foa_ambix": "ambisonic", "hoa_sh": "ambisonic"}  # every other layout is an array
DEVICE_KINDS = {"device", "plate", "spring", "digital_reverb", "creative"}


@cache
def thresholds() -> dict:
    return yaml.safe_load((HERE / "thresholds.yaml").read_text())


@cache
def columns(table: str) -> list[str]:
    with open(HERE / "columns.csv", newline="") as f:
        return [r["name"] for r in csv.DictReader(f) if r["table"] == table]


def finite(s: pd.Series) -> pd.Series:
    """The values as floats, with infinities and non-numbers made empty (a published number is finite)."""
    s = pd.to_numeric(s, errors="coerce").astype(float)
    return s.where(np.isfinite(s))


def truth(s: pd.Series) -> pd.Series:
    """True and False, with an empty cell read as False."""
    return s.astype("boolean").fillna(False).astype(bool)


def candidate_dir() -> Path:
    return paths.data_root() / "release" / "candidate"


# ------------------------------------------------------------------------------------------- one dataset

def space_facts(d: Dataset, index: pd.DataFrame) -> pd.DataFrame:
    """One row per space of the index: label, type, IR kind, volume, licence, same_as.

    The type is the space's row in spaces.csv, else the dataset's `space_type`, else the keyword rules of
    rooms.py. (The rules are the 0.9 fallback; the data model has a command draft spaces.csv rows from them.)"""
    first = index.groupby("space").first()
    out = pd.DataFrame({"space": first.index, "label": first["space_label"].to_numpy(),
                        "category_hint": first["category_hint"].to_numpy(), "ir_kind": first["ir_kind"].to_numpy()})
    slug = out["space"].str.split("/", n=1).str[1]
    rules = [categorize(s, label, hint or None) for s, label, hint in zip(slug, out["label"], out["category_hint"])]
    out["space_type"] = [c for c, _ in rules]
    out["ir_kind"] = [k or kind for (_, k), kind in zip(rules, out["ir_kind"])]
    if d.space_type:
        out["space_type"] = d.space_type
    cur = curated_spaces(d.id).set_index("space") if not curated_spaces(d.id).empty else pd.DataFrame()
    for col in ("label", "space_type", "ir_kind", "volume_m3", "dims_m", "note", "same_as", "licence", "redistribute",
                "licence_note"):
        given = slug.map(cur[col]) if col in cur else pd.Series("", index=out.index)
        given = given.fillna("")
        out[col] = given.where(given != "", out[col] if col in out else "")
    kind_from_type = out["space_type"].isin(KIND_CATEGORIES)
    out.loc[kind_from_type, "ir_kind"] = out.loc[kind_from_type, "space_type"]
    out["volume_m3"] = pd.to_numeric(out["volume_m3"], errors="coerce")
    out["origin"] = [origin_of(d, k) for k in out["ir_kind"]]
    out["redistribute_given"] = out["redistribute"]               # as spaces.csv writes it, for uses_of
    out["licence"] = out["licence"].where(out["licence"] != "", d.license.spdx)
    out["redistribute"] = [uses_of(lic, red, d)["redistribute"] for lic, red in zip(out["licence"], out["redistribute_given"])]
    return out.drop(columns="category_hint")


def origin_of(d: Dataset, ir_kind: str) -> str:
    """measured, simulated or device: the dataset's, unless the space is a model or a device."""
    if d.type == "production" or ir_kind in DEVICE_KINDS:
        return "device"
    if d.type == "simulated" or ir_kind == "virtual":
        return "simulated"
    return "measured"


def uses_of(spdx: str, redistribute: str, d: Dataset) -> dict:
    """The four permitted uses and the conditions, through licences.csv (the space's licence where it has one)."""
    if spdx == d.license.spdx:
        u = d.license.uses
    else:
        u = License(spdx=spdx, redistribute_audio=redistribute or "unknown").uses
    return {"licence": spdx, "licence_conditions": "+".join(u.conditions), "use_research": u.research,
            "use_commercial": u.commercial, "use_training": u.training, "redistribute": u.redistribute}


def download_of(files: pd.Series, downloads: pd.DataFrame) -> pd.Series:
    """The download that holds each file: the file itself, or the archive unpacked into its folder (extract_to)."""
    direct = set(downloads.loc[downloads["extract_to"] == "-", "relpath"])
    folders = [(x + "/", r) for x, r in zip(downloads["extract_to"], downloads["relpath"]) if x not in ("-", ".")]
    into_files = downloads.loc[downloads["extract_to"] == ".", "relpath"].tolist()
    mirror = downloads.loc[downloads["relpath"] == "@mirror", "relpath"].tolist()

    def one(file: str) -> str:
        f = file.split("|")[0]
        if f in direct:
            return f
        for folder, rel in folders:
            if f.startswith(folder):
                return rel
        if len(into_files) == 1:
            return into_files[0]
        return mirror[0] if mirror else ""
    return files.map(one)


def primary_rows(r: pd.DataFrame) -> pd.Series:
    """is_primary: one layout per space, so a space counts once in statistics (data-model.md, section 3).
    Among the layouts the reader prefers (OpenAIR: B-format over its stereo decode), or all of them where it
    prefers none, the layout with the most rows in the space, then the first by name."""
    preferred = truth(r["is_preferred"])
    eligible = preferred | ~preferred.groupby(r["space"]).transform("any")
    counts = r[eligible].groupby(["space", "layout"]).size().rename("n").reset_index()
    best = (counts.sort_values(["space", "n", "layout"], ascending=[True, False, True])
            .drop_duplicates("space").set_index("space")["layout"])
    return eligible & r["layout"].eq(r["space"].map(best))


FIT_SPAN_DB = {"edt": 10, "t20": 20, "t30": 30}   # how many dB of decay each fit covers (ISO 3382-1)


def judged(bands: pd.DataFrame, duration_s: pd.Series) -> pd.DataFrame:
    """The published band values: a value whose v1 validity fails is empty (R4). 0.9 uses v1's `valid_*`.

    A decay time whose fitted line would take longer than the analysed IR (duration_s, by ir_id) to fall its
    10, 20 or 30 dB is not valid either: the line was fitted to something other than the decay.
    """
    out = bands.copy()
    length = out["ir_id"].map(duration_s)
    for q, span_db in FIT_SPAN_DB.items():
        out[f"valid_{q}"] = truth(out[f"valid_{q}"]) & ~(finite(out[q]) * span_db / 60 > length)
    for q in ("edt", "t20", "t30", "c50", "c80", "d50", "ts"):
        out[q] = finite(out[q]).where(truth(out[f"valid_{q}"]))
    out["decay_range_db"] = finite(out["decay_range_db"])
    return out


def mid(octave: pd.DataFrame, q: str) -> pd.Series:
    """Mean of the 500 Hz and 1 kHz octaves, where both are valid (validity.mid.bands_hz)."""
    lo, hi = (octave[octave["band_hz"] == b].set_index("ir_id")[q] for b in thresholds()["validity"]["mid"]["bands_hz"])
    return (lo + hi.reindex(lo.index)) / 2


def decay_grade(octave: pd.DataFrame, any_valid: pd.Series) -> pd.Series:
    """A, B, C or D from decay validity alone (thresholds.yaml, grades)."""
    g = thresholds()["grades"]

    def all_valid(bands: list, ok) -> pd.Series:
        sel = octave[octave["band_hz"].isin(bands)]
        hits = sel[ok(sel)].groupby("ir_id")["band_hz"].nunique()
        return hits.reindex(any_valid.index, fill_value=0) == len(bands)

    a = all_valid(g["s_and_a_bands_hz"], lambda s: truth(s["valid_t30"]))
    b = all_valid(g["b_bands_hz"], lambda s: truth(s["valid_t30"]) | truth(s["valid_t20"]))
    return pd.Series(np.select([a, b, any_valid], ["A", "B", "C"], "D"), index=any_valid.index)


def assessed_defects() -> list[str]:
    """The defect columns the analyser measures (all but grades.not_measured in thresholds.yaml)."""
    g = thresholds()["grades"]
    return [f"defect_{k}" for k in thresholds()["defects"] if k not in g["not_measured"]]


def all_assessed(dfx: pd.DataFrame) -> pd.Series:
    """True where every defect the analyser measures has a value (decision D: S needs it)."""
    return dfx[assessed_defects()].notna().all(axis=1)


def grade_of(decay: np.ndarray, n_major: np.ndarray, n_minor: np.ndarray, assessed: np.ndarray,
             space_type: np.ndarray) -> np.ndarray:
    """S to D (thresholds.yaml, grades): the decay grade, raised to S where nothing is wrong and everything was
    checked, capped where a defect is major, and empty for the space types without a decay to grade."""
    g = thresholds()["grades"]
    grade = np.where((decay == "A") & (n_major + n_minor == 0) & assessed, "S",
                     np.where(n_major > 0, np.where(decay == "D", "D", g["major_defect_caps_at"]), decay))
    return np.where(np.isin(space_type, g["empty_for_space_types"]), "", grade)


def defects(m: pd.DataFrame, fmt: pd.Series) -> pd.DataFrame:
    """The eleven defect columns: True, False, or empty where not assessed (thresholds.yaml, defects)."""
    t = thresholds()["defects"]
    audited = m["v1_audit_error"].eq("")          # no audit row (ReverbFX, TH Koln) or an audit error: not assessed
    read = m["v1_error"].fillna("").eq("")

    def where(ok: pd.Series, value: pd.Series) -> pd.Series:
        return truth(value).astype("boolean").where(ok)

    top = np.minimum(m["fs"] / 2, t["band_limited"]["top_hz"])
    out = pd.DataFrame(index=m.index)
    out["defect_clipping"] = where(read, m["v1_clipped"])                                   # v1's flag
    out["defect_dropouts"] = where(audited, m["dropout_ms"] >= t["dropouts"]["min_run_ms"])
    out["defect_no_decay"] = where(audited, (m["active_s"] > t["no_decay"]["min_active_s"])
                                   & (m["env_slope_db_s"] > t["no_decay"]["max_slope_db_per_s"]))
    out["defect_sweep_residue"] = where(audited, m["glide_db"] >= t["sweep_residue"]["min_glide_db"])
    out["defect_tonal_interference"] = where(audited, m["tonal_frac"] > t["tonal_interference"]["min_tonal_fraction"])
    out["defect_corrupt_samples"] = truth(m["v1_nonfinite"]) | truth(m["v1_all_zero"])
    out["defect_band_limited"] = where(audited, m["bw_eff_hz"] < t["band_limited"]["below_fraction_of_top"] * top)
    out["defect_quantisation_floor"] = where(audited, m["v1_quantized_tail"])                 # v1's flag
    out["defect_dc_offset"] = where(read, m["dc_offset_db"] > t["dc_offset"]["min_level_db"])
    pnr = pd.to_numeric(m["peak_to_noise_db"], errors="coerce")       # +inf: no noise floor at all, so no defect
    out["defect_high_noise_floor"] = where(pnr.notna(), pnr < t["high_noise_floor"]["below_peak_to_noise_db"])
    out["defect_inverted_channel"] = pd.Series(pd.NA, index=m.index, dtype="boolean")      # not measured by v1
    return out


def dataset_irs(dataset_id: str) -> dict:
    """irs rows, published bands, spaces and files of one dataset. Runs in a worker process."""
    d = get_dataset(dataset_id)
    t = paths.tables_dir(d.id)
    index = pd.read_parquet(t / "index.parquet")
    measured = pd.read_parquet(t / ANALYSER / "measured.parquet")
    bands = pd.read_parquet(t / ANALYSER / "bands.parquet")
    sampled = index[index["sampled"]].reset_index(drop=True)
    # a stale measurement cannot pass: the same sampled ids, with the same audio_key (data-model.md, section 8)
    ok = sampled.merge(measured[["ir_id", "audio_key"]], on="ir_id", how="outer", suffixes=("", "_m"), indicator=True)
    bad = ok[(ok["_merge"] != "both") | (ok["audio_key"] != ok["audio_key_m"])]
    if len(bad):
        raise RuntimeError(f"{d.id}: {len(bad)} IRs to measure again, e.g. {bad['ir_id'].head(5).tolist()}")

    sp = space_facts(d, index)
    r = sampled.merge(measured.drop(columns=["audio_key", "fs", "channels"]), on="ir_id", how="left")
    r = r.merge(sp[["space", "space_type", "ir_kind", "volume_m3", "origin", "licence", "redistribute_given"]]
                .rename(columns={"ir_kind": "space_ir_kind"}), on="space", how="left")
    fmt = pd.Series(np.where(r["channels"] == 1, "mono", r["layout"].map(FORMAT_OF_LAYOUT).fillna("array")), index=r.index)
    downloads = read_downloads(d.id)
    out = pd.DataFrame({
        "ir_id": r["ir_id"], "dataset": d.id, "space": r["space"], "condition": r["condition"],
        "source": r["source"], "receiver": r["receiver"], "is_primary": primary_rows(r),
        "duplicate_of": "",                                          # set across datasets in build()
        "file": r["file"], "item": r["item"], "download": download_of(r["file"], downloads),
        # 0.9: v1's hash of the decoded samples (SHA-1, float32), first 32 hex digits; 1.0 uses the recipe
        "audio_hash": r["v1_content_sha1"].fillna("").str[:32],
        "fs": r["fs"], "channels": r["channels"], "duration_s": r["duration_s"], "sample_format": r["sample_format"],
        "measured_on": r["measured_on"].fillna(""),
        "origin": r["origin"], "format": fmt, "layout": r["layout"], "space_type": r["space_type"],
        "volume_m3": r["volume_m3"], "distance_m": r["distance_m"],
        **{c: r[c] for c in ("src_x", "src_y", "src_z", "rcv_x", "rcv_y", "rcv_z")},
    })
    lic = [uses_of(s, red, d) for s, red in zip(r["licence"], r["redistribute_given"].fillna(""))]
    out = pd.concat([out, pd.DataFrame(lic, index=out.index)], axis=1)

    # bands: judged values, one octave row per band and one broadband row
    pub = judged(bands, r.set_index("ir_id")["duration_analysed_s"])
    octave, broadband = pub[pub["series"] == "octave"], pub[pub["series"] == "broadband"].set_index("ir_id")
    raw_oct = bands[bands["series"] == "octave"]
    raw_bb = bands[bands["series"] == "broadband"].set_index("ir_id")

    # processing (thresholds.yaml, processing)
    p = thresholds()["processing"]
    audited = r["v1_audit_error"].eq("")
    bb_mode = r["ir_id"].map(raw_bb["edc_mode"]) if len(raw_bb) else pd.Series(np.nan, index=r.index)
    bb_cut = r["ir_id"].map(raw_bb["intersection_s"]) if len(raw_bb) else pd.Series(np.nan, index=r.index)
    truncated = (bb_mode == "lundeby") & (bb_cut >= p["truncated"]["min_intersection_fraction_of_duration"]
                                          * r["duration_analysed_s"])
    out["proc_windowed"] = ""
    out["proc_truncated"] = np.where(bb_mode.isna(), "", np.where(truncated, "detected", "no"))
    out["proc_denoised"] = ""
    out["proc_equalised"] = ""
    resampled = r["resampled_from_hz"].notna() & (r["bw_cliff_db"] >= p["resampled"]["min_cliff_db"])
    out["proc_resampled"] = np.where(audited, np.where(resampled, "detected", "no"), "")
    out["proc_bit_depth_reduced"] = ""
    out["proc_format_derived"] = ""
    proc = [c for c in out if c.startswith("proc_")]
    out.insert(out.columns.get_loc("proc_windowed"), "is_processed",
               out[proc].isin(["documented", "detected", "both"]).any(axis=1))

    # quality
    dfx = defects(r, fmt)
    majors = [f"defect_{k}" for k, v in thresholds()["defects"].items() if v["major"]]
    minors = [f"defect_{k}" for k, v in thresholds()["defects"].items() if not v["major"]]
    n_major = dfx[majors].fillna(False).sum(axis=1).astype(int)
    n_minor = (dfx[minors].fillna(False).sum(axis=1)
               + sum((out[f"proc_{k}"].isin(["detected", "both"])) for k, v in p.items() if v["counts_as_minor_defect"])).astype(int)
    any_valid = ((pub["valid_edt"] | pub["valid_t20"] | pub["valid_t30"])
                 .groupby(pub["ir_id"]).any().reindex(r["ir_id"], fill_value=False))
    decay = decay_grade(octave, any_valid).to_numpy()
    out["grade"] = grade_of(decay, n_major.to_numpy(), n_minor.to_numpy(),
                            all_assessed(dfx).to_numpy(), r["space_type"].to_numpy())
    out["defects_major"] = n_major.to_numpy()
    out["defects_minor"] = n_minor.to_numpy()
    out = pd.concat([out, dfx], axis=1)
    out["usable_top_hz"] = np.nan                                  # measured from step 4
    out["effective_bits"] = np.nan                                 # measured from step 4
    out["peak_to_noise_db"] = finite(r["peak_to_noise_db"])

    # acoustics
    ids = r["ir_id"]
    for q in ("edt", "t20", "t30"):
        out[f"{q}_mid"] = ids.map(mid(octave, q)) if len(octave) else np.nan
    out["rt_mid"] = out["t30_mid"].where(out["t30_mid"].notna(), out["t20_mid"])
    out["rt_mid_from"] = np.where(out["t30_mid"].notna(), "T30", np.where(out["t20_mid"].notna(), "T20", ""))
    if len(raw_oct):
        lo_hi = [raw_oct[raw_oct["band_hz"] == b].set_index("ir_id")["decay_range_db"] for b in (500, 1000)]
        dr_mid = np.fmin(finite(ids.map(lo_hi[0])), finite(ids.map(lo_hi[1])))
    else:
        dr_mid = pd.Series(np.nan, index=r.index)
    out["decay_range_mid_db"] = finite(dr_mid)
    bb_t30 = ids.map(broadband["t30"]) if len(broadband) else pd.Series(np.nan, index=r.index)
    bb_t20 = ids.map(broadband["t20"]) if len(broadband) else pd.Series(np.nan, index=r.index)
    out["rt_broadband"] = bb_t30.where(bb_t30.notna(), bb_t20)
    out["rt_broadband_from"] = np.where(bb_t30.notna(), "T30", np.where(bb_t20.notna(), "T20", ""))
    out["decay_range_broadband_db"] = ids.map(broadband["decay_range_db"]) if len(broadband) else np.nan
    for q in ("c50", "c80", "d50", "ts"):
        out[f"{q}_mid"] = ids.map(mid(octave, q)) if len(octave) else np.nan
    out["drr_db"] = finite(r["drr_db"])
    out["sti"] = finite(r["sti"]).where(truth(r["valid_sti"]))                 # v1's validity
    out["iacc_early"] = finite(r["iacc_early"]).where(fmt == "binaural")
    orient = r["orientation_known"].fillna(True).astype(bool)
    out["lateral_fraction"] = finite(r["lateral_fraction"]).where((fmt == "ambisonic") & orient)
    out["bass_ratio"] = finite(r["bass_ratio"])
    out["treble_ratio"] = finite(r["treble_ratio"])
    out["mixing_time_ms"] = finite(r["mixing_time_ms"])
    for b in OCTAVES:
        col = octave[octave["band_hz"] == OCTAVE_HZ.get(b, b)].set_index("ir_id")["t30"] if len(octave) else pd.Series(dtype=float)
        out[f"t30_{b}"] = ids.map(col)

    missing = set(columns("irs")) - set(out.columns)
    if missing:
        raise RuntimeError(f"build does not make these irs columns: {sorted(missing)}")
    pub_bands = pub[pub["ir_id"].isin(set(out["ir_id"]))].reindex(columns=columns("bands"))
    pub_bands = pub_bands.merge(out[["ir_id"]].assign(fmt=fmt.to_numpy(), orient=orient.to_numpy()), on="ir_id")
    pub_bands["iacc_early"] = pub_bands["iacc_early"].where((pub_bands["fmt"] == "binaural") & (pub_bands["series"] == "octave"))
    pub_bands["lateral_fraction"] = pub_bands["lateral_fraction"].where(
        (pub_bands["fmt"] == "ambisonic") & pub_bands["orient"] & (pub_bands["series"] == "octave"))
    sp["n_irs"] = sp["space"].map(out["space"].value_counts()).fillna(0).astype(int)
    sp["n_irs_found"] = sp["space"].map(index["space"].value_counts()).fillna(0).astype(int)
    readers = sampled[columns("readers")].reset_index(drop=True)
    return {"dataset": d.id, "irs": out[columns("irs")], "bands": pub_bands[columns("bands")], "spaces": sp,
            "readers": readers,
            "files": files_table(d.id, index, downloads), "index": index, "n_found": len(index)}


def read_downloads(dataset_id: str) -> pd.DataFrame:
    f = paths.downloads_tsv(dataset_id)
    if not f.exists():
        return pd.DataFrame(columns=["relpath", "url", "size", "algo", "checksum", "provider_version", "extract_to"])
    return pd.read_csv(f, sep="\t", dtype=str, keep_default_na=False)


AUDIO_EXT = {"wav", "flac", "aif", "aiff", "mp3", "ogg", "w64", "rf64", "caf"}
DOC_EXT = {"pdf", "txt", "md", "html", "htm", "doc", "docx", "rtf", "bib", "tex", "png", "jpg", "jpeg", "svg"}


def files_table(dataset_id: str, index: pd.DataFrame, downloads: pd.DataFrame) -> pd.DataFrame:
    """One row per pinned file, with its role. 0.9: `ir` where the reader found IRs; the other roles by file type
    (readers do not name the files they skip yet)."""
    f = paths.files_tsv(dataset_id)
    if not f.exists():
        return pd.DataFrame(columns=columns("files"))
    files = pd.read_csv(f, sep="\t", dtype={"relpath": str, "sha256": str}, keep_default_na=False)
    per_file = index["file"].str.split("|").explode().value_counts()
    n = files["relpath"].map(per_file).fillna(0).astype(int)
    name = files["relpath"].str.rsplit("/", n=1).str[-1]
    ext = name.str.rsplit(".", n=1).str[-1].str.lower().where(name.str.contains(r"\."), "")
    mac = name.str.startswith("._") | name.str.lower().eq(".ds_store") | files["relpath"].str.contains("__MACOSX/")
    role = np.select([n > 0, mac, ext.isin(AUDIO_EXT), ext.isin(DOC_EXT)],
                     ["ir", "excluded", "excluded", "document"], "metadata")
    reason = np.select([mac, (role == "excluded")], ["macOS metadata", "audio the reader does not list as an IR"], "")
    return pd.DataFrame({"dataset": dataset_id, "file": files["relpath"],
                         "size_bytes": pd.to_numeric(files["size"], errors="coerce"),
                         "sha256": files["sha256"], "download": download_of(files["relpath"], downloads),
                         "role": role, "role_reason": reason, "n_irs_found": n})


# ------------------------------------------------------------------------------------------- all datasets

def measured_ids() -> list[str]:
    return sorted(p.parent.parent.name for p in paths.data_root().glob(f"tables/*/{ANALYSER}/measured.parquet"))


def mark_duplicates(irs: pd.DataFrame, year: dict[str, int | None]) -> pd.Series:
    """duplicate_of: the original of an IR with the same audio_hash, the IR from the earliest dataset by year,
    then the smallest id. Empty for originals and IRs without a hash."""
    h = irs[irs["audio_hash"] != ""]
    key = h.assign(y=h["dataset"].map(year).fillna(9999)).sort_values(["y", "ir_id"])
    first = key.groupby("audio_hash")["ir_id"].first()
    dup = irs["audio_hash"].map(first).fillna("")
    return dup.where(dup != irs["ir_id"], "")


def what_it_is(d: Dataset) -> dict:
    """What the registry says about a dataset in words, for the site and `rirdb info`: its description, who
    published it, where, and what its authors provide about the rooms (room_info: yes, partial, no; empty if not
    checked). The caveats are one item per line."""
    w = d.web_check or WebCheck()
    return {"description": " ".join((w.content or d.content).split()), "institution": w.institution or "",
            "country": w.country or "", "host": w.host or "", "size_stated": w.size or "",
            "file_formats": w.file_formats or "", "formats_stated": ", ".join(w.families),
            "facts_checked_on": w.checked or "",
            **{f"provides_{f}": getattr(d.room_info, f) or "" for f in AVAIL_FIELDS},
            "provides_where": d.room_info.where or "", "caveats": "\n".join(" ".join(c.split()) for c in w.caveats)}


def datasets_table(registry: list[Dataset], built: dict[str, dict]) -> pd.DataFrame:
    rows = []
    for d in registry:
        b = built.get(d.id)
        u = d.license.uses
        if b:
            status = "measured"
        elif paths.tables_dir(d.id).joinpath("index.parquet").exists():
            status = "indexed"
        elif paths.downloads_tsv(d.id).exists():
            status = "pinned"
        else:
            status = "catalogued" if d.status == "catalogued" else d.status
        rows.append({
            "dataset": d.id, "name": d.name, "year": d.year,
            "origin": {"production": "device", "hybrid": "simulated"}.get(d.type, d.type), "status": status,
            "licence": d.license.spdx, "licence_statements": len(d.license.statements),
            "use_research": u.research, "use_commercial": u.commercial, "use_training": u.training,
            "redistribute": u.redistribute, "citation_keys": "", "homepage": d.homepage or "", "doi": d.doi or "",
            "paper_title": d.paper.title if d.paper else "", "paper_url": d.paper.url if d.paper else "",
            **what_it_is(d),
            "reader": d.adapter.name or "", "reader_settings": json.dumps(d.adapter.params or {}, sort_keys=True) if b else "",
            "pinned_version": pinned_version(d.id), "analyser": ANALYSER if b else "",
            "cap": d.cap, "n_irs_stated": d.expected.n_irs,
            "n_irs_found": b["n_found"] if b else None, "n_irs": len(b["irs"]) if b else None,
            "known_issues": d.notes or "", "reference_check": "none_published", "reference_median_ratio": None,
        })
    out = pd.DataFrame(rows)
    for c in ("year", "cap", "n_irs_stated", "n_irs_found", "n_irs"):
        out[c] = out[c].astype("Int64")
    return out[columns("datasets")]


def pinned_version(dataset_id: str) -> str:
    v = read_downloads(dataset_id)
    return "|".join(sorted(set(v["provider_version"]) - {"", "-"})) if len(v) else ""


def downloads_table(ids: list[str]) -> pd.DataFrame:
    parts = []
    for i in ids:
        v = read_downloads(i)
        if v.empty:
            continue
        parts.append(pd.DataFrame({"dataset": i, "download": v["relpath"], "source_rank": 1, "url": v["url"],
                                   "size_bytes": pd.to_numeric(v["size"], errors="coerce").astype("Int64"),
                                   "checksum": v["checksum"].replace("-", ""), "algo": v["algo"].replace("-", ""),
                                   "version": v["provider_version"].replace("-", ""),
                                   "extract_to": v["extract_to"].replace("-", "")}))
    return pd.concat(parts, ignore_index=True)[columns("downloads")]


def build(out: Path | None = None, workers: int = 8) -> Path:
    """Rebuild release/candidate/ from scratch."""
    out = out or candidate_dir()
    ids = measured_ids()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        built = {b["dataset"]: b for b in ex.map(dataset_irs, ids)}
    registry = load_registry()
    year = {d.id: d.year for d in registry}

    if out.exists():
        shutil.rmtree(out)
    for sub in ("bands", "files", "all_irs", "source_meta", "readers"):
        (out / sub).mkdir(parents=True)
    irs = pd.concat([b["irs"] for b in built.values()], ignore_index=True)
    irs["duplicate_of"] = mark_duplicates(irs, year)
    irs.sort_values("ir_id").to_parquet(out / "irs.parquet", index=False)
    spaces = pd.concat([b["spaces"] for b in built.values()], ignore_index=True)
    spaces["dataset"] = spaces["space"].str.split("/", n=1).str[0]
    spaces["attribution"] = ""
    spaces[columns("spaces")].to_parquet(out / "spaces.parquet", index=False)
    datasets_table(registry, built).to_parquet(out / "datasets.parquet", index=False)
    downloads_table([d.id for d in registry]).to_parquet(out / "downloads.parquet", index=False)
    for i, b in built.items():
        b["bands"].to_parquet(out / "bands" / f"{i}.parquet", index=False)
        b["files"].to_parquet(out / "files" / f"{i}.parquet", index=False)
        b["readers"].to_parquet(out / "readers" / f"{i}.parquet", index=False)
        if get_dataset_cached(i).cap:
            idx = b["index"].rename(columns={})
            idx[columns("all_irs")].to_parquet(out / "all_irs" / f"{i}.parquet", index=False)
        sm = paths.tables_dir(i) / "source_meta.parquet"
        if sm.exists():
            shutil.copyfile(sm, out / "source_meta" / f"{i}.parquet")
    shutil.copyfile(HERE / "columns.csv", out / "columns.csv")
    write_release_json(out, irs, built)
    return out


@cache
def get_dataset_cached(dataset_id: str) -> Dataset:
    return get_dataset(dataset_id)


def write_release_json(out: Path, irs: pd.DataFrame, built: dict) -> None:
    commit = subprocess.run(["git", "-C", str(paths.REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(paths.REPO_ROOT), "status", "--porcelain", "--", "src", "registry"],
                           capture_output=True, text=True).stdout.strip() != ""
    files = sorted(p for p in out.rglob("*") if p.is_file())
    meta = {"version": "candidate", "built_at": f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
            "git_commit": commit, "git_dirty": dirty, "analyser": ANALYSER,
            "rows": {"irs": len(irs), "datasets": len(built)},
            "files": {p.relative_to(out).as_posix(): sha256_of(p) for p in files}}
    (out / "release.json").write_text(json.dumps(meta, indent=1) + "\n")
