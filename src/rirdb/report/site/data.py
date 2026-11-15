"""Everything the rirdb website shows, collected once into JSON.

    site.json        corpus totals, one row per registry dataset (registry facts, index counts, metric
                     summary, files, survey facts, storage), the survey-only datasets, every space,
                     T30 validity per band, corpus reference distributions
    ds/<id>.json     per registry dataset: parameter percentiles, per-band boxes and validity,
                     histograms (with the corpus reference on the same bins), grades, every quality
                     flag, duplicates, bandwidth, noise, spaces, CLAP-map points, validation,
                     findings, lock rows, manifest summary, storage, probe output

`collect()` needs the DB ($RIRDB_ROOT) and runs on a compute node (sbatch). The page builders only
read the JSON (`load()`), so pages can be rebuilt from a cached copy with `--reuse-data`.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from rirdb import paths
from rirdb.report import guide
from rirdb.report.survey import load_survey

BANDS = (63, 125, 250, 500, 1000, 2000, 4000, 8000)
BAND_KEYS = [str(b) for b in BANDS] + ["bb"]
BAND_METRICS = ("t30", "t20", "edt", "c50", "c80", "d50", "ts")

# (column, label, unit, decimals, what it is)
PARAMS = [
    ("t30_mid", "T30", "s", 2, "Reverberation time from the -5 to -35 dB part of the decay, mean of the 500 Hz and 1 kHz octaves (ISO 3382-1)."),
    ("t20_mid", "T20", "s", 2, "Reverberation time from the -5 to -25 dB part of the decay, 500 Hz / 1 kHz mean."),
    ("edt_mid", "EDT", "s", 2, "Early decay time: from the first 10 dB of the decay, scaled to 60 dB. Tracks perceived reverberance."),
    ("c50_mid", "C50", "dB", 1, "Clarity for speech: early (0-50 ms) to late energy ratio."),
    ("c80_mid", "C80", "dB", 1, "Clarity for music: early (0-80 ms) to late energy ratio."),
    ("d50_mid", "D50", "", 2, "Definition: share of the energy in the first 50 ms (0 to 1)."),
    ("ts_mid", "Ts", "s", 3, "Centre time: the energy-weighted mean arrival time."),
    ("drr_bb", "DRR", "dB", 1, "Direct-to-reverberant ratio, full band, direct window +-8 ms around the direct peak (ACE convention)."),
    ("sti", "STI", "", 2, "Speech transmission index from the IR alone (IEC 60268-16:2020; no background noise, no masking)."),
    ("br", "Bass ratio", "", 2, "Low-frequency reverberation relative to mid frequencies: (T125 + T250) / (T500 + T1k)."),
    ("tr", "Treble ratio", "", 2, "High-frequency reverberation relative to mid frequencies: (T2k + T4k) / (T500 + T1k)."),
    ("t_mix_ned_ms", "Mixing time", "ms", 0, "First time the normalised echo density (Abel and Huang) reaches 1: where the reverb becomes diffuse."),
    ("iacc_e3", "IACC E3", "", 2, "Early interaural cross-correlation, mean of 500 Hz-2 kHz (binaural IRs only)."),
    ("jlf_low", "JLF", "", 2, "Early lateral energy fraction from B-format W and Y (Ambisonic IRs only)."),
    ("pnr_db", "Peak-to-noise", "dB", 0, "Peak level above the noise floor of the recording: the usable decay range."),
    ("bw_eff_hz", "Effective bandwidth", "Hz", 0, "Frequency where the content of the IR really stops (audit); can be far below the file's Nyquist."),
    ("duration_raw_s", "File length", "s", 2, "Length of the IR as published."),
    ("onset_ms", "Pre-delay in file", "ms", 1, "Time before the direct sound in the file (ISO 3382-1 onset, 20 dB below the peak)."),
]
PARAM_KEYS = [p[0] for p in PARAMS]

# histogram name -> (column, bins key in overview._bins)
HISTS = {"t30_mid": "t", "edt_mid": "t", "c80_mid": "c", "d50_mid": "d50", "drr_bb": "drr", "sti": "sti",
         "br": "br", "t_mix_ned_ms": "tmix", "pnr_db": "pnr", "bw_eff_hz": "bw"}

# flag -> (label, group, what it means). group: content = excluded from clean_rooms;
# decay = limits the decay analysis; signal = file/recording property; spatial = channel layout
FLAG_INFO = {
    "flag_sweep_like": ("Sweep residue", "content", "Straight time-frequency ridges left by sweep deconvolution (chirp residue), or an electronic reverb. Excluded from clean rooms."),
    "flag_tonal": ("Tonal content", "content", "Sustained spectral lines (hum, birdsong, whistles) in the tail. Excluded from clean rooms."),
    "flag_not_decaying": ("Not decaying", "content", "The envelope does not decay like a room response. Excluded from clean rooms."),
    "flag_dropout": ("Dropouts", "content", "Runs of exact zeros inside the signal, well above the quantisation step. Excluded from clean rooms."),
    "flag_nonstationary_noise": ("Unsteady noise floor", "decay", "Bursts over 10 dB or drift over 6 dB in the noise after the decay. Kept: filter on it when the floor matters."),
    "flag_quantized_tail": ("Quantised tail", "decay", "The tail rounds to exact zeros (low-level 16-bit recording): limits the usable decay range."),
    "flag_upsampled": ("Upsampled", "signal", "A brick-wall cut-off well below the file's Nyquist: resampled content, or a sweep or filter that ended there."),
    "flag_band_limited": ("Band-limited", "signal", "The content stops below 70\u00a0% of the lower of Nyquist and 20 kHz (below 14 kHz in files at 44.1 kHz or more, 11.2 kHz at 32 kHz). Can coincide with an upsampling cut-off."),
    "flag_clipped": ("Clipped", "signal", "Samples at full scale."),
    "flag_dc_offset": ("DC offset", "signal", "A constant offset in the signal."),
    "flag_short": ("Short file", "signal", "Less than 0.3 s of signal from the onset to the end of the file (trailing digital silence removed), whatever the decay time."),
    "flag_low_fs": ("Low sample rate", "signal", "Sample rate too low for the upper octave bands."),
    "flag_pre_onset_energy": ("Energy before onset", "signal", "Pre-onset power at least 20 dB above the tail noise and above -55 dB re peak (e.g. circular time aliasing)."),
    "flag_onset_low_psnr_warning": ("Weak onset", "signal", "The direct sound is close to the noise, so the onset is uncertain."),
    "flag_nonfinite": ("Non-finite samples", "signal", "Not-a-number or infinite samples in the file."),
    "flag_all_zero": ("All zero", "signal", "The file holds only zeros."),
    "flag_tail_truncated": ("Tail truncated", "decay", "The file ends before the decay reaches the noise floor."),
    "flag_lundeby_failed_any": ("Noise-floor fit failed", "decay", "Lundeby's noise-floor iteration failed in at least one octave band."),
    "flag_no_noise_floor": ("No noise floor", "decay", "The IR decays to the end of the file (faded or cut): plain Schroeder integration is used."),
    "flag_low_pnr": ("Low peak-to-noise", "decay", "Peak-to-noise below 35 dB: too little range even for T20 (T30 needs 45 dB)."),
    "flag_implausible_hf_decay": ("Implausible HF decay", "decay", "A valid high-frequency decay time longer than air absorption allows."),
    "flag_curved_decay": ("Curved decay", "decay", "Broadband decay curvature |C| over 10\u00a0% (ISO 3382-2 Annex B): double slope or coupled spaces."),
    "flag_nonlinear_decay": ("Non-linear decay", "decay", "Broadband non-linearity parameter xi over 10 per mille (ISO 3382-2 Annex B)."),
    "flag_filter_bias_risk": ("Filter bias risk", "decay", "Bandwidth x decay time below 16 in some band: the octave filter may bias the decay time."),
    "flag_edc_method_disagreement": ("Decay methods disagree", "decay", "Broadband T30 from the Lundeby-compensated decay curve and from the Chu (noise-subtracted) curve differ by more than 5\u00a0%."),
    "flag_mic_not_omni": ("Microphone not omni", "spatial", "The analysed channel is not an omnidirectional microphone."),
    "flag_omni_proxy": ("Capsule as omni", "spatial", "Raw array: one capsule stands in for an omnidirectional microphone."),
    "flag_orientation_unknown": ("Orientation unknown", "spatial", "B-format IR with W and Y channels (used for JLF) whose array orientation is not documented. Checked only where JLF is computed (first-order B-format): higher-order and raw-array sets are not checked, even when their orientation is not documented."),
}

GRADES = {
    "A": "T30 valid from 125 Hz to 4 kHz",
    "B": "T20 or T30 valid from 250 Hz to 2 kHz",
    "C": "some decay parameter valid",
    "D": "nothing valid (too short, anechoic, or unreadable)",
}

# rows of docs/decisions/dataset-findings.md -> registry ids (first cell, matched by prefix)
FINDING_IDS = {
    "Rochester": ["rochester"],
    "DRR-scaled BRIRs": ["drr_scaled_brirs"],
    "MIRD": ["mird"],
    "GTU-RIR": ["gtu_rir"],
    "MIT IR Survey": ["mit_survey"],
    "Aachen AIR": ["aachen_air"],
    "ACE (single mic)": ["ace"],
    "ACE": ["ace", "ace_arrays"],
    "C4DM": ["c4dm"],
    "OpenAIR": ["openair"],
    "OpenSLR": ["openslr28"],
    "BUT ReverbDB": ["but_reverb"],
    "Surrey BRIRs": ["surrey_real_room_brirs"],
    "MYRiAD, trajectoRIR": ["myriad", "trajectorir", "miracle", "c4dm", "drr_scaled_brirs"],
    "MYRiAD": ["myriad"],
    "TU Ilmenau robot, BRUDEX": ["tuil_robot_coupled", "brudex", "motus", "openair", "ace"],
    "TU Ilmenau robot": ["tuil_robot_coupled"],
    "SRIRACHA": ["sriracha"],
    "UPV_RIR_DB": ["upv_rir_db"],
    "Multi-Room Transition Dataset": ["aalto_multiroom"],
    "TAU-SRIR": ["tau_srir"],
    "SoundCam": ["soundcam"],
    "Several (audit 1.3": ["aalto_multiroom", "arni", "meshrir", "sriracha", "detmold_srir", "c4dm", "dechorate",
                           "but_reverb", "flair", "tau_srir", "mit_survey", "rochester"],
    "RSoANU": ["rsoanu"],
    "3D meshgrid": ["meshgrid3d"],
    "MeshRIR": ["meshrir"],
    "MIRACLE": ["miracle"],
    "dEchorate": ["dechorate"],
    "MP-RIR": ["mp_rir"],
    "CLAP map": ["meshgrid3d", "tau_srir", "sriracha"],
    "EchoThief": ["echothief"],
    "Voxengo": ["voxengo"],
    "METU SPARG": ["metu_sparg"],
    "ReverbFX": ["reverbfx"],
    "BRUDEX, trajectoRIR": ["brudex", "trajectorir"],
    "Huddersfield 360 BRIR, OpenAIR, MIT IR Survey": ["huddersfield_360_brir", "openair", "mit_survey"],
}

SIGNALS = ("ir", "reverb_vector")
CLAP_CAP = 1200  # the frozen map already keeps at most this many IRs per dataset


# ----------------------------------------------------------------------------- helpers

def clean(o):
    """JSON-safe: NaN/inf -> None, numpy scalars -> Python, floats rounded."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if o is None or o is pd.NA or o is pd.NaT:
        return None
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        o = float(o)
        if not math.isfinite(o):
            return None
        return round(o, 5)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def quant(s: pd.Series, qs=(5, 10, 25, 50, 75, 90, 95)) -> dict:
    v = pd.to_numeric(s, errors="coerce").dropna().to_numpy(dtype=float)
    v = v[np.isfinite(v)]
    if not v.size:
        return {"n": 0}
    return {"n": int(v.size), **{f"p{q}": float(x) for q, x in zip(qs, np.percentile(v, qs))},
            "mean": float(v.mean())}


def box(s: pd.Series) -> dict:
    v = pd.to_numeric(s, errors="coerce").dropna().to_numpy(dtype=float)
    v = v[np.isfinite(v)]
    if not v.size:
        return {"n": 0}
    return {"n": int(v.size), "q": np.percentile(v, [5, 25, 50, 75, 95]).tolist()}


def hist(s: pd.Series, edges: np.ndarray) -> dict:
    v = pd.to_numeric(s, errors="coerce").dropna().to_numpy(dtype=float)
    v = v[np.isfinite(v)]
    h, _ = np.histogram(np.clip(v, edges[0], edges[-1] - 1e-9), bins=edges) if v.size else (np.zeros(len(edges) - 1), None)
    return {"edges": np.round(edges, 5).tolist(), "counts": np.asarray(h, dtype=int).tolist(), "n": int(v.size),
            "below": int((v < edges[0]).sum()), "above": int((v >= edges[-1]).sum())}


def bins() -> dict:
    from rirdb.report.overview import _bins
    return _bins()


def valid(df: pd.DataFrame, metric: str, band: str) -> pd.Series:
    col, vcol = f"{metric}_{band}", f"valid_{metric}_{band}"
    if col not in df:
        return pd.Series(dtype=float)
    v = df[col]
    return v.where(df[vcol].fillna(False).astype(bool)) if vcol in df else v


def param_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col == "sti":
        return df["sti"].where(df["valid_sti"].fillna(False).astype(bool)) if "valid_sti" in df else df.get("sti")
    if col == "onset_ms":
        return df["onset_s"] * 1e3 if "onset_s" in df else pd.Series(dtype=float)
    return df[col] if col in df else pd.Series(dtype=float)


def git_sha() -> str | None:
    """Short commit of the checkout, with "-dirty" when the code, registry, docs or configs differ from it
    (changed or untracked files), so a build from uncommitted work says so. The site output itself
    (reports/) is not looked at."""
    try:
        git = ["git", "-C", str(paths.REPO_ROOT)]
        sha = subprocess.run(git + ["rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        changed = subprocess.run(git + ["status", "--porcelain", "--", "src", "registry", "docs", "configs", "snapshots"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        return sha + ("-dirty" if changed else "")
    except Exception:  # noqa: BLE001 - informational only
        return None


# ----------------------------------------------------------------------------- inputs

def load_frame(con) -> pd.DataFrame:
    have = {r[0] for r in con.execute("DESCRIBE corpus_dedup").fetchall()}
    want = ["ir_id", "dataset_id", "room_id", "grade", "capture_format", "fs", "analysed_role", "ir_kind", "type",
            "measured", "preferred", "dup_of", "error", "duration_raw_s", "duration_s", "onset_s", "content_sha1",
            "category", "resampled_from_hz", "bw_cliff_db", "noise_burst_db", "noise_drift_db", "edc_mode_bb",
            "license_spdx", "training_use", "valid_sti"]
    want += [c for c in PARAM_KEYS if c != "onset_ms"]
    want += [f"{p}{m}_{b}" for m in BAND_METRICS for b in BAND_KEYS for p in ("", "valid_")]
    want += [c for c in have if c.startswith("flag_")]
    cols = [c for c in dict.fromkeys(want) if c in have]
    df = con.execute(f"SELECT {', '.join(cols)} FROM corpus_dedup").df()
    df["error"] = df["error"].fillna("")
    reg = registry_licences()
    if "license_spdx" in df:
        new = df["dataset_id"].map(lambda i: reg[i][0] if i in reg else None)
        stale = new.notna() & (new != df["license_spdx"])
        if stale.any():
            print("[site] licence taken from the registry, the index predates it: "
                  + ", ".join(f"{k} ({v:,} IRs)" for k, v in df.loc[stale, "dataset_id"].value_counts().items()))
            df.loc[stale, "license_spdx"] = new[stale]
            if "training_use" in df:
                df.loc[stale, "training_use"] = df.loc[stale, "dataset_id"].map(lambda i: reg[i][1])
    return df


def registry_licences() -> dict[str, tuple[str, str]]:
    """{dataset_id: (spdx, training)} for every dataset with one licence: the values `rirdb dev index-v1` copies to each
    of its IRs (index.build_index). The site uses them over the index's, so a licence corrected in the registry
    shows before the dataset is re-indexed (C4DM: 3.0 to 4.0). Datasets with a licence per space
    (the `licence` column of registry/<id>/spaces.csv, OpenAIR) keep the index's per-IR values."""
    from rirdb.registry import load_registry
    from rirdb.rooms import curated_spaces

    def per_space(i: str) -> bool:
        s = curated_spaces(i)
        return "licence" in s and (s["licence"] != "").any()

    return {d.id: (d.license.spdx, d.license.training) for d in load_registry(catalogued=False) if not per_space(d.id)}


def clean_mask(df: pd.DataFrame) -> pd.Series:
    """The clean_rooms view, applied to the frame."""
    content = np.zeros(len(df), dtype=bool)
    for f in ("flag_sweep_like", "flag_tonal", "flag_not_decaying", "flag_dropout"):
        if f in df:
            content |= df[f].fillna(False).astype(bool).to_numpy()
    return (df["measured"].fillna(False).astype(bool) & (df["ir_kind"] == "room") & df["preferred"].fillna(False).astype(bool)
            & df["dup_of"].isna() & (df["error"] == "") & ~content)


def stats_mask(df: pd.DataFrame) -> pd.Series:
    """One representation per measured position, duplicates removed, analysed without error."""
    return df["preferred"].fillna(False).astype(bool) & df["dup_of"].isna() & (df["error"] == "")


# the kind condition of guide.SPACE_SQL (keep the two equal): IRs that are not of a real space
NOT_SPACE_KINDS = ("anechoic", "virtual", "device", "scale_model")
# file and recording properties, summarised over every IR used for statistics whatever its kind (as the
# catalogue does); the room-acoustic parameters only over IRs of real spaces
SIGNAL_PARAMS = {"pnr_db", "bw_eff_hz", "duration_raw_s", "onset_ms"}


def space_mask(df: pd.DataFrame) -> pd.Series:
    """IRs of real spaces (rooms, outdoor, vehicles): the kind part of guide.SPACE_SQL."""
    return df["ir_kind"].notna() & ~df["ir_kind"].isin(NOT_SPACE_KINDS)


def read_tsv(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def locks() -> dict[str, list[dict]]:
    out = {}
    for f in sorted(paths.DATASETS_DIR.glob("*/downloads.tsv")):
        rows = read_tsv(f)
        for r in rows:
            r["size"] = int(r["size"]) if (r.get("size") or "").isdigit() else None
        out[f.parent.name] = rows
    return out


def is_macos_metadata(relpath: str) -> bool:
    """AppleDouble files ("._name", usually under __MACOSX/) and Finder's .DS_Store: macOS archive debris.
    An AppleDouble file shadows a real one ("._x.wav"), so counting it by extension doubles the .wav count."""
    name = relpath.rsplit("/", 1)[-1]
    return name.startswith("._") or name.lower() == ".ds_store" or relpath.startswith("__MACOSX/") or "/__MACOSX/" in relpath


def manifests() -> dict[str, dict]:
    """Per manifest: file count and bytes, file types (macOS metadata counted apart, not as a type), and the
    top-level folders."""
    out = {}
    for f in sorted(paths.DATASETS_DIR.glob("*/files.tsv.gz")):
        rows = read_tsv(f)
        ext, top, total, mac = {}, {}, 0, 0
        for r in rows:
            name = r["relpath"].rsplit("/", 1)[-1]
            if is_macos_metadata(r["relpath"]):
                mac += 1
            else:
                e = name.rsplit(".", 1)[-1].lower() if "." in name else "(none)"
                ext[e] = ext.get(e, 0) + 1
            t = r["relpath"].split("/", 1)[0] if "/" in r["relpath"] else "(top level)"
            top.setdefault(t, [0, 0])
            top[t][0] += 1
            sz = int(r["size"]) if (r.get("size") or "").isdigit() else 0
            top[t][1] += sz
            total += sz
        out[f.parent.name] = {
            "n_files": len(rows), "bytes": total,
            "extensions": dict(sorted(ext.items(), key=lambda kv: -kv[1])),
            "n_macos_metadata": mac,
            "top_dirs": [{"name": k, "n_files": v[0], "bytes": v[1]}
                         for k, v in sorted(top.items(), key=lambda kv: -kv[1][1])[:40]],
            "n_top_dirs": len(top),
            "file": f"{f.parent.name}.files.tsv.gz",   # its name under data/manifests/ on the site
        }
    return out


def storage() -> dict[str, dict]:
    f = paths.REGISTRY_DIR / "storage.tsv"
    if not f.exists():
        return {}
    out = {}
    for r in read_tsv(f):
        for k in ("n_files", "bytes"):
            r[k] = int(r[k]) if (r.get(k) or "").isdigit() else None
        out[r["id"]] = r
    return out


def probes() -> dict[str, str]:
    return {f.stem: f.read_text(errors="replace") for f in sorted(paths.PROBES_DIR.glob("*.txt"))}


def findings() -> dict[str, list[dict]]:
    """Per-dataset rows of docs/decisions/dataset-findings.md (both tables)."""
    f = paths.REPO_ROOT / "docs" / "decisions" / "dataset-findings.md"
    out: dict[str, list[dict]] = {}
    if not f.exists():
        return out
    section = None
    unmatched = []
    for line in f.read_text().splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if not line.startswith("| ") or line.startswith("| Dataset") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in re.split(r"\s*\|\s*", line.strip().strip("|"))]
        label = cells[0]
        ids = next((v for k, v in sorted(FINDING_IDS.items(), key=lambda kv: -len(kv[0])) if label.startswith(k)), None)
        if ids is None:
            unmatched.append(label)
            continue
        if section == "Licence findings":
            row = {"section": "licence", "subject": label, "finding": cells[1], "evidence": None,
                   "handling": cells[2] if len(cells) > 2 else None}
        else:
            row = {"section": "data", "subject": label, "finding": cells[1], "evidence": cells[2] if len(cells) > 2 else None,
                   "handling": cells[3] if len(cells) > 3 else None}
        for i in ids:
            out.setdefault(i, []).append(row)
    if unmatched:
        print(f"[site] findings rows without a dataset: {unmatched}")
    return out


def validation() -> dict[str, dict]:
    """Agreement with values published by the dataset authors (reports/validation/)."""
    vdir = paths.REPORTS_DIR / "validation"
    out = {}
    f = vdir / "openair_comparison.csv"
    if f.exists():
        d = pd.read_csv(f)
        rows = []
        for (m, b), g in d.dropna(subset=["published", "ours"]).groupby(["metric", "band"]):
            r = {"metric": m, "band": str(b), "n": int(len(g))}
            if m in ("t30", "edt", "t20"):
                ratio = g["ours"] / g["published"]
                r |= {"median_ratio": float(ratio.median()), "within_10pct": float((ratio.sub(1).abs() <= 0.1).mean())}
            else:
                diff = g["ours"] - g["published"]
                r |= {"median_diff": float(diff.median()), "mae": float(diff.abs().mean())}
            rows.append(r)
        out["openair"] = {"source": "OpenAIR per-space data tables (archived pages)", "unit": "space",
                          "n_spaces": int(d["space"].nunique()), "summary": rows,
                          "points": d.dropna(subset=["published", "ours"]).to_dict("records")}
    f = vdir / "ace_comparison.csv"
    if f.exists():
        d = pd.read_csv(f)
        rows = []
        for m, g in d.dropna(subset=["published", "ours"]).groupby("metric"):
            r = {"metric": m, "n": int(len(g))}
            if m.startswith("t30"):
                ratio = g["ours"] / g["published"]
                r |= {"median_ratio": float(ratio.median()), "within_10pct": float((ratio.sub(1).abs() <= 0.1).mean())}
            else:
                diff = g["ours"] - g["published"]
                r |= {"median_diff": float(diff.median()), "mae": float(diff.abs().mean()),
                      "within_2db": float((diff.abs() <= 2).mean())}
            rows.append(r)
        entry = {"source": "ACE challenge ground truth (measurement CSV)", "unit": "IR", "summary": rows,
                 "points": d.dropna(subset=["published", "ours"]).to_dict("records")}
        out["ace_arrays"] = entry
    return out


# ----------------------------------------------------------------------------- CLAP map

def clap(df: pd.DataFrame, out_assets: Path) -> tuple[dict, dict]:
    """Per-dataset map points (unit square) and realism scores; draws the corpus background once."""
    snap = paths.REPO_ROOT / "snapshots" / "clap_map_v1"
    f = snap / "reference.parquet"
    if not f.exists():
        return {}, {}
    ref = pd.read_parquet(f)
    meta = json.loads((snap / "meta.json").read_text()) if (snap / "meta.json").exists() else {}
    base = json.loads((snap / "baseline.json").read_text()) if (snap / "baseline.json").exists() else {}
    if "dataset_id" not in ref:
        ref = ref.merge(df[["ir_id", "dataset_id"]], on="ir_id", how="left")
    per_ds: dict[str, dict] = {}
    corpus = {"meta": {k: meta.get(k) for k in ("created", "model", "model_revision", "n", "n_clean", "cap_per_dataset",
                                                  "k", "umap", "dry_sources", "map_version")},
              "signals": {}}
    for s in SIGNALS:
        xc, yc = f"x_{s}", f"y_{s}"
        if xc not in ref:
            continue
        x, y = ref[xc].to_numpy(float), ref[yc].to_numpy(float)
        lo_x, hi_x, lo_y, hi_y = np.nanmin(x), np.nanmax(x), np.nanmin(y), np.nanmax(y)
        ux, uy = (x - lo_x) / (hi_x - lo_x), (y - lo_y) / (hi_y - lo_y)
        corpus["signals"][s] = {"n": int(np.isfinite(ux).sum()), "background": f"assets/clap_{s}.png",
                                "label": {"ir": "the IR itself", "reverb_vector": "its effect on dry recordings"}[s]}
        _draw_background(ux, uy, out_assets / f"clap_{s}.png")
        for ds, idx in ref.groupby("dataset_id").indices.items():
            pts = np.stack([ux[idx], uy[idx]], axis=1)
            pts = pts[np.isfinite(pts).all(axis=1)][:CLAP_CAP]
            per_ds.setdefault(ds, {"signals": {}})["signals"][s] = {
                "xy": np.round(pts * 1000).astype(int).tolist(),
                "score": next((r for r in base.get(s, []) if r.get("dataset") == ds), None),
            }
    corpus["baseline"] = base
    return corpus, per_ds


def _draw_background(ux: np.ndarray, uy: np.ndarray, out: Path) -> None:
    """All map points in a neutral grey on a transparent square; the SVG overlays use the same unit square
    with PAD margin (y up)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(6, 6), dpi=150)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.scatter(ux, uy, s=0.6, c="#8a92a6", alpha=0.35, linewidths=0, rasterized=True)
    ax.set_xlim(-MAP_PAD, 1 + MAP_PAD)
    ax.set_ylim(-MAP_PAD, 1 + MAP_PAD)
    ax.axis("off")
    fig.savefig(out, transparent=True)
    plt.close(fig)


MAP_PAD = 0.03


# ----------------------------------------------------------------------------- per dataset

def dataset_detail(ds: str, d: pd.DataFrame, B: dict, ref_bands: dict) -> dict:
    ok = d[d["error"] == ""]
    st = d[stats_mask(d)]
    out: dict = {"n_analysed": int(len(d)), "n_errors": int((d["error"] != "").sum()), "n_stats": int(len(st)),
                 "errors": d.loc[d["error"] != "", "error"].str.slice(0, 160).value_counts().head(8).to_dict()}
    out["grades"] = ok["grade"].value_counts().reindex(list("ABCD"), fill_value=0).astype(int).to_dict()
    out["grades_stats"] = st["grade"].value_counts().reindex(list("ABCD"), fill_value=0).astype(int).to_dict()
    # room-acoustic parameters, bands and validity over the IRs of real spaces, as the catalogue computes them
    # (an anechoic-chamber or modelled IR has no room decay); a dataset without any keeps all its IRs
    in_space = space_mask(st)
    ac = st[in_space] if in_space.any() else st
    out["n_stats_space"] = int(len(ac))
    out["stats_left_out"] = (st.loc[~in_space, "ir_kind"].fillna("unknown").value_counts().astype(int).to_dict()
                             if in_space.any() else {})
    out["params"] = []
    for col, label, unit, nd, _ in PARAMS:
        s = param_series(st if col in SIGNAL_PARAMS else ac, col)
        q = quant(s)
        if q["n"]:
            out["params"].append({"key": col, "label": label, "unit": unit, "nd": nd, **q})
    # per-band boxes (valid values only) and validity share
    out["bands"], out["validity"] = {}, {}
    for m in BAND_METRICS:
        out["bands"][m] = {b: box(valid(ac, m, b)) for b in BAND_KEYS if f"{m}_{b}" in ac}
        out["validity"][m] = {b: float(ac[f"valid_{m}_{b}"].fillna(False).astype(bool).mean()) if len(ac) else None
                              for b in BAND_KEYS if f"valid_{m}_{b}" in ac}
    out["ref_bands"] = ref_bands
    out["hists"] = {}
    for col, bk in HISTS.items():
        s = param_series(st if col in SIGNAL_PARAMS else ac, col)
        if s is not None and pd.to_numeric(s, errors="coerce").notna().any():
            out["hists"][col] = hist(s, B[bk])
    # every flag: share over the analysed IRs
    flags = []
    jlf_computed = "jlf_low" in ok and bool(pd.to_numeric(ok["jlf_low"], errors="coerce").notna().any())
    for f, (label, group, info) in FLAG_INFO.items():
        if f in ok:
            v = ok[f].fillna(False).astype(bool)
            if f == "flag_orientation_unknown" and not v.any() and not jlf_computed:
                continue  # only evaluated where JLF is computed: a 0 % here would read as "orientation known"
            flags.append({"flag": f, "label": label, "group": group, "n": int(v.sum()),
                          "share": float(v.mean()) if len(v) else 0.0})
    out["flags"] = flags
    out["roles"] = d["analysed_role"].fillna("(none)").value_counts().head(12).astype(int).to_dict() if "analysed_role" in d else {}
    out["formats"] = d["capture_format"].fillna("?").value_counts().astype(int).to_dict()
    out["fs"] = {str(int(k)): int(v) for k, v in d["fs"].dropna().value_counts().items()}
    out["kinds"] = d["ir_kind"].fillna("?").value_counts().astype(int).to_dict()
    out["categories"] = d["category"].fillna("unknown").value_counts().astype(int).to_dict()
    # duplicates (identical decoded samples)
    out["duplicates"] = {"n_copies": int(d["dup_of"].notna().sum())}
    if "resampled_from_hz" in ok:
        out["resampled_from"] = {str(int(k)): int(v) for k, v in ok["resampled_from_hz"].dropna().value_counts().sort_index().items()}
    if "edc_mode_bb" in ok:
        out["edc_mode_bb"] = ok["edc_mode_bb"].fillna("n/a").value_counts().astype(int).to_dict()
    out["licences"] = d["license_spdx"].fillna("?").value_counts().astype(int).to_dict() if "license_spdx" in d else {}
    out["training_use"] = d["training_use"].fillna("?").value_counts().astype(int).to_dict() if "training_use" in d else {}
    return out


def duplicate_stats(df: pd.DataFrame) -> dict[str, dict]:
    """Per dataset: IRs in a group of identical files, and how many of those groups span datasets."""
    d = df.dropna(subset=["content_sha1"])[["ir_id", "dataset_id", "content_sha1"]]
    g = d.groupby("content_sha1")["dataset_id"].agg(["size", "nunique"])
    g = g[g["size"] > 1]
    d = d[d["content_sha1"].isin(g.index)].join(g, on="content_sha1")
    out = {}
    for ds, sub in d.groupby("dataset_id"):
        cross = sub[sub["nunique"] > 1]
        others = df[df["content_sha1"].isin(cross["content_sha1"]) & (df["dataset_id"] != ds)]["dataset_id"].value_counts()
        out[ds] = {"n_in_groups": int(len(sub)), "n_groups": int(sub["content_sha1"].nunique()),
                   "n_cross": int(len(cross)), "cross_with": others.astype(int).to_dict()}
    return out


def reference_bands(df: pd.DataFrame) -> dict:
    """Corpus reference per band: median over clean measured rooms of each room's median (valid values)."""
    c = df[clean_mask(df)]
    out = {}
    for m in ("t30", "t20", "edt", "c50", "c80", "d50", "ts"):
        out[m] = {}
        for b in BAND_KEYS:
            if f"{m}_{b}" in c:
                s = valid(c, m, b).groupby(c["room_id"]).median().dropna()
                out[m][b] = {"n_rooms": int(s.size), **({"q": np.percentile(s, [5, 25, 50, 75, 95]).tolist()} if s.size else {})}
    return out


def reference_hists(df: pd.DataFrame, B: dict) -> dict:
    """Corpus reference distributions on the dataset bins: one value per space (its median), over clean
    measured rooms; peak-to-noise and bandwidth over every space's analysed IRs."""
    c = df[clean_mask(df)]
    a = df[stats_mask(df)]
    out = {}
    for col, bk in HISTS.items():
        src = a if col in ("pnr_db", "bw_eff_hz") else c
        s = param_series(src, col)
        if s is None or not len(s):
            continue
        out[col] = hist(s.groupby(src["room_id"]).median(), B[bk]) | {
            "subset": "every space, analysed IRs" if src is a else "clean measured rooms", "per": "space median"}
    return out


# ----------------------------------------------------------------------------- spaces

def spaces(con) -> list[dict]:
    rows = guide.space_rows(con)
    extra = con.execute("""
        SELECT r.room_id, any_value(r.room_key) AS room_key, any_value(r.dims_m) AS dims_m,
               any_value(r.description) AS description, any_value(r.materials) AS materials,
               any_value(r.bg_noise_dba) AS bg_noise_dba, any_value(r.note) AS note
        FROM rooms r GROUP BY 1""").df()
    lic = con.execute("""
        SELECT room_id, mode(license_spdx) AS licence, count(*) AS n_records,
               count(DISTINCT capture_format) AS n_formats, string_agg(DISTINCT capture_format, ',' ORDER BY capture_format) AS formats
        FROM irs GROUP BY 1""").df()
    ex = {r["room_id"]: r for r in extra.to_dict("records")}
    lx = {r["room_id"]: r for r in lic.to_dict("records")}
    reg = registry_licences()
    for r in rows:
        e, l = ex.get(r["room_id"], {}), lx.get(r["room_id"], {})
        for k in ("room_key", "dims_m", "description", "materials", "bg_noise_dba", "note"):
            r[k] = e.get(k)
        for k in ("licence", "n_records", "formats"):
            r[k] = l.get(k)
        if r.get("licence") and r.get("dataset_id") in reg:
            r["licence"] = reg[r["dataset_id"]][0]    # as load_frame: the registry's, where it has one licence
    return rows


# ----------------------------------------------------------------------------- build

def collect(out_dir: Path) -> dict:
    from rirdb.analysis import ANALYZER_VERSION
    from rirdb.query import connect

    data_dir = out_dir / "data"
    (data_dir / "ds").mkdir(parents=True, exist_ok=True)
    con = connect()
    print("[site] loading the corpus frame")
    df = load_frame(con)
    B = bins()
    print(f"[site] {len(df):,} analysed IRs")

    # registry rows, as the Field Guide computes them
    idx, met, files = guide.index_stats(con), guide.metric_stats(con), guide.file_stats()
    rows = guide.dataset_rows()
    survey = load_survey()
    stor, lk, mf, pr, fnd, val = storage(), locks(), manifests(), probes(), findings(), validation()
    dups = duplicate_stats(df)
    ref_b = reference_bands(df)
    ref_h = reference_hists(df, B)
    clap_corpus, clap_ds = clap(df, out_dir / "assets")
    space_rows = spaces(con)

    # STI median over valid values only, as on the dataset pages (guide.metric_stats takes every STI value)
    if "valid_sti" in df:
        sti = df[stats_mask(df) & space_mask(df) & df["valid_sti"].fillna(False).astype(bool)].groupby("dataset_id")["sti"].median()
        for i, m in met.items():
            v = sti.get(i)
            m["sti_p50"] = float(v) if v is not None and np.isfinite(v) else None

    by_ds = dict(tuple(df.groupby("dataset_id")))
    for r in rows:
        i = r["id"]
        r["index"] = idx.get(i)
        r["metrics"] = met.get(i)
        r["files"] = files.get(i)
        if r["files"] is not None and i in mf:  # file types without the macOS metadata (guide.file_stats counts it)
            r["files"] |= {"extensions": mf[i]["extensions"], "n_macos_metadata": mf[i]["n_macos_metadata"]}
        r["survey"] = (survey.get("registry") or {}).get(i)
        r["storage"] = stor.get(i)
        r["manifest"] = mf.get(i)
        r["lock_summary"] = ({"n": len(lk[i]), "bytes": sum(x["size"] or 0 for x in lk[i]),
                              "algos": sorted({x["algo"] for x in lk[i]}),
                              "resolved_at": max((x.get("resolved_at") or "" for x in lk[i]), default=None)}
                             if i in lk else None)
        r["analysed"] = i in by_ds
        r["n_spaces_indexed"] = sum(1 for s in space_rows if s["dataset_id"] == i)
        r["n_findings"] = len(fnd.get(i, []))
        detail = {"id": i, "lock": lk.get(i, []), "manifest": mf.get(i), "storage": stor.get(i),
                  "probe": pr.get(i), "findings": fnd.get(i, []), "validation": val.get(i),
                  "duplicates_detail": dups.get(i), "clap": clap_ds.get(i),
                  "spaces": [s for s in space_rows if s["dataset_id"] == i]}
        if i in by_ds:
            detail["analysis"] = dataset_detail(i, by_ds[i], B, ref_b)
            detail["analysis"]["duplicates"] |= dups.get(i, {})
        (data_dir / "ds" / f"{i}.json").write_text(json.dumps(clean(detail), separators=(",", ":"), allow_nan=False, default=str))

    analysed = df[df["error"] == ""]
    corpus = {
        "n_irs_analysed": int(len(df)), "n_irs_ok": int(len(analysed)),
        "n_irs_indexed": int(sum((v or {}).get("n_records", 0) for v in idx.values())),
        "n_datasets_registry": len(rows), "n_datasets_analysed": int(df["dataset_id"].nunique()),
        "n_datasets_survey": len(survey.get("new") or []),
        "n_spaces": len(space_rows),
        "n_spaces_physical": len({s["physical_id"] for s in space_rows}),
        "n_clean_rooms_irs": int(clean_mask(df).sum()),
        "grades": analysed["grade"].value_counts().reindex(list("ABCD"), fill_value=0).astype(int).to_dict(),
        "n_duplicate_copies": int(df["dup_of"].notna().sum()),
        "download_bytes": int(sum(x["size"] or 0 for v in lk.values() for x in v)),
        "manifest_bytes": int(sum(v["bytes"] for v in mf.values())),
        "manifest_files": int(sum(v["n_files"] for v in mf.values())),
    }
    data = clean({
        "generated": f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        "git": git_sha(),
        "analyzer_version": ANALYZER_VERSION,
        "survey_date": survey.get("date"),
        "survey_note": survey.get("note"),
        "corpus": corpus,
        "datasets": rows,
        "survey": survey.get("new") or [],
        "spaces": space_rows,
        "validity_t30": guide.validity(con),
        "openair_licences": guide.openair_licences(),
        "reference_hists": ref_h,
        "reference_bands": ref_b,
        "clap": clap_corpus,
        "validation": {k: {kk: vv for kk, vv in v.items() if kk != "points"} for k, v in val.items()},
        "params": [{"key": k, "label": l, "unit": u, "nd": n, "info": i} for k, l, u, n, i in PARAMS],
        "flags": [{"flag": f, "label": l, "group": g, "info": i} for f, (l, g, i) in FLAG_INFO.items()],
        "grades": GRADES,
    })
    (data_dir / "site.json").write_text(json.dumps(data, separators=(",", ":"), allow_nan=False, default=str))
    print(f"[site] wrote {data_dir / 'site.json'} and {len(rows)} dataset files")
    return data


def load(out_dir: Path) -> dict:
    return json.loads((out_dir / "data" / "site.json").read_text())


def load_dataset(out_dir: Path, dataset_id: str) -> dict:
    f = out_dir / "data" / "ds" / f"{dataset_id}.json"
    return json.loads(f.read_text()) if f.exists() else {"id": dataset_id}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
