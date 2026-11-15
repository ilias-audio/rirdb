"""One-off (step 1, task 4 of docs/plans/master-plan.md): analyser v1's measurements into tables/<id>/v1/.

Reads, for one dataset, and changes none of them:
    $RIRDB_ROOT/metrics/v1/<id>/wide.parquet     the v1 metrics (one row per measured IR, 287-327 columns)
    $RIRDB_ROOT/audit/v1/<id>/audit.parquet      the v1 signal audit (30 columns)
    $RIRDB_ROOT/index/irs/dataset=<id>/          the old index, for the reading v1 measured
    $RIRDB_ROOT/tables/<id>/index.parquet        the new index (task 3)
Writes:
    $RIRDB_ROOT/tables/<id>/v1/measured.parquet  one row per sampled IR: v1's numbers
    $RIRDB_ROOT/tables/<id>/v1/bands.parquet     one row per IR and band; the broadband row from v1's *_bb columns
    $RIRDB_ROOT/tables/<id>/v1/run.json          where the numbers come from, and what was left out

Numbers, not verdicts (data-model.md, R3), with two exceptions kept for the 0.9 preview (master-plan, step 1):
v1's validity per band (`valid_*`: for a binaural pair v1 asked both ears to be valid, which the averaged decay
range cannot tell), and the v1 flags that only a new reading of the audio could recompute (clipping,
non-finite and all-zero samples, quantised tail). Step 4 replaces both.

    jobs: for i in $(ls $RIRDB_ROOT/metrics/v1); do sbatch ... pixi run python scripts/one_off/v1_to_tables.py $i; done
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from rirdb import paths  # noqa: E402

# v1 column -> measured.parquet column (per IR)
SCALARS = {
    "error": "v1_error", "analysed_role": "measured_on", "fs": "fs", "n_channels": "channels",
    "duration_raw_s": "duration_raw_s", "duration_s": "duration_analysed_s", "peak_abs": "peak_abs",
    "dc_offset_db": "dc_offset_db", "onset_s": "onset_s", "leading_silence_s": "leading_silence_s",
    "trailing_zeros_s": "trailing_zeros_s", "pre_onset_excess_db": "pre_onset_excess_db",
    "pre_onset_level_db": "pre_onset_level_db", "pnr_db": "peak_to_noise_db", "t30_bb_chu": "t30_bb_chu",
    "drr_bb": "drr_db", "direct_peak_ms": "direct_peak_ms", "sti": "sti", "valid_sti": "valid_sti",
    "t_mix_ned_ms": "mixing_time_ms", "br": "bass_ratio", "tr": "treble_ratio", "iacc_e3": "iacc_early",
    "jlf_low": "lateral_fraction", "spectral_tilt_db_per_oct": "spectral_tilt_db_per_oct",
    "content_sha1": "v1_content_sha1",
    # flags v1 decided while reading the audio, which its stored numbers cannot reproduce
    "flag_clipped": "v1_clipped", "flag_nonfinite": "v1_nonfinite", "flag_all_zero": "v1_all_zero",
}
AUDIT = {
    "tonal_frac": "tonal_frac", "tonal_prom_db": "tonal_prom_db", "glide_db": "glide_db",
    "glide_oct_s": "glide_oct_s", "glide_span_oct": "glide_span_oct", "env_slope_db_s": "env_slope_db_s",
    "active_s": "active_s", "crest_db": "crest_db", "zero_frac": "zero_frac", "quant_range_db": "quant_range_db",
    "dropout_ms": "dropout_ms", "n_dropouts": "n_dropouts", "bw_eff_hz": "bw_eff_hz", "bw_cliff_db": "bw_cliff_db",
    "resampled_from_hz": "resampled_from_hz", "noise_seg_s": "noise_seg_s", "noise_burst_db": "noise_burst_db",
    "noise_drift_db": "noise_drift_db", "flag_quantized_tail": "v1_quantized_tail", "audit_error": "v1_audit_error",
}
# per band: v1 name -> bands.parquet column
PER_BAND = {"edt": "edt", "t20": "t20", "t30": "t30", "c50": "c50", "c80": "c80", "d50": "d50", "ts": "ts",
            "decay_range_db": "decay_range_db", "intersection_s": "intersection_s", "edc_mode": "edc_mode",
            "curvature": "curvature_pct", "xi": "nonlinearity_permille", "level": "level_db",
            "iacc_e": "iacc_early", "jlf": "lateral_fraction",
            **{f"valid_{q}": f"valid_{q}" for q in ("edt", "t20", "t30", "c50", "c80", "d50", "ts")}}
BAND_COLUMN = re.compile(r"^(?P<q>" + "|".join(sorted(PER_BAND, key=len, reverse=True)) + r")_(?P<band>\d+|bb)$")


def new_id(v1_id: str) -> str:
    return v1_id.replace(":", "-", 1)


def same_reading(old_locator: str, new_locator: str) -> bool:
    """The two locators name the same samples. The old index predates the `array` key of the HDF5/NumPy
    reader; without it the reader used the array of the dataset's settings, which is what the new key names
    (only dEchorate picks arrays by pattern, and its ids already contain the array)."""
    old, new = json.loads(old_locator), json.loads(new_locator)
    if "array" not in old:
        new.pop("array", None)
    return old == new


def bands_long(wide: pd.DataFrame) -> pd.DataFrame:
    """One row per IR and band: octave rows, and one broadband row (band_hz empty) from the *_bb columns."""
    by_band: dict[str, dict[str, str]] = {}
    for c in wide.columns:
        m = BAND_COLUMN.match(c)
        if m:
            by_band.setdefault(m["band"], {})[PER_BAND[m["q"]]] = c
    parts = []
    for band, cols in by_band.items():
        part = wide[["ir_id", *cols.values()]].rename(columns={v: k for k, v in cols.items()})
        part.insert(1, "series", "broadband" if band == "bb" else "octave")
        part.insert(2, "band_hz", np.nan if band == "bb" else float(band))
        parts.append(part)
    order = ["ir_id", "series", "band_hz", "edt", "t20", "t30", "c50", "c80", "d50", "ts", "decay_range_db",
             "intersection_s", "edc_mode", "floor", "iacc_early", "lateral_fraction", "curvature_pct",
             "nonlinearity_permille", "level_db", *[f"valid_{q}" for q in ("edt", "t20", "t30", "c50", "c80", "d50", "ts")]]
    if not parts:                            # nothing measured per band (MIRACLE: 32 ms IRs)
        return pd.DataFrame(columns=order)
    out = pd.concat(parts, ignore_index=True)
    out["floor"] = None                      # not measured by v1 (step 4)
    out = out.reindex(columns=order)
    return out.sort_values(["ir_id", "series", "band_hz"], na_position="last").reset_index(drop=True)


def convert(dataset_id: str) -> dict:
    root = paths.data_root()
    wide = pd.read_parquet(root / "metrics" / "v1" / dataset_id / "wide.parquet")
    audit_f = root / "audit" / "v1" / dataset_id / "audit.parquet"
    audit = pd.read_parquet(audit_f) if audit_f.exists() else pd.DataFrame(columns=["ir_id"])
    old = pd.read_parquet(root / "index" / "irs" / f"dataset={dataset_id}" / "part-0.parquet",
                          columns=["ir_id", "locator", "channel_roles"])
    new = pd.read_parquet(paths.tables_dir(dataset_id) / "index.parquet",
                          columns=["ir_id", "sampled", "audio_key", "locator", "channel_roles"])

    for f in (wide, audit, old):
        f["ir_id"] = f["ir_id"].map(new_id)
    sampled = new[new["sampled"]]
    # v1 measured the reading of the old index: it must be the reading of the new one
    same = sampled.merge(old, on="ir_id", suffixes=("", "_v1"))
    ok = [same_reading(o, n) for o, n in zip(same["locator_v1"], same["locator"])]
    same_reading_ids = same["ir_id"][pd.Series(ok, index=same.index) & (same["channel_roles"] == same["channel_roles_v1"])]
    keep = sampled[sampled["ir_id"].isin(set(same_reading_ids) & set(wide["ir_id"]))]
    report = {"dataset": dataset_id, "n_sampled": len(sampled), "n_v1_rows": len(wide), "n_written": len(keep),
              "sampled_not_measured_by_v1": sorted(set(sampled["ir_id"]) - set(wide["ir_id"]))[:20],
              "n_sampled_not_measured_by_v1": len(set(sampled["ir_id"]) - set(wide["ir_id"])),
              "n_reading_changed": len(set(sampled["ir_id"]) & set(wide["ir_id"]) - set(same_reading_ids)),
              "n_v1_rows_not_sampled": len(set(wide["ir_id"]) - set(sampled["ir_id"])),
              "n_without_audit": len(set(keep["ir_id"]) - set(audit["ir_id"]))}

    w = wide[wide["ir_id"].isin(keep["ir_id"])].reset_index(drop=True)
    measured = w[["ir_id", *[c for c in SCALARS if c in w]]].rename(columns=SCALARS)
    measured = measured.reindex(columns=["ir_id", *SCALARS.values()])
    a = audit[["ir_id", *[c for c in AUDIT if c in audit]]].rename(columns=AUDIT).reindex(columns=["ir_id", *AUDIT.values()])
    measured = measured.merge(a, on="ir_id", how="left").merge(keep[["ir_id", "audio_key"]], on="ir_id")
    measured.insert(1, "audio_key", measured.pop("audio_key"))
    measured.insert(2, "analyser", "v1")
    bands = bands_long(w)

    out = paths.tables_dir(dataset_id) / "v1"
    out.mkdir(parents=True, exist_ok=True)
    measured.sort_values("ir_id").to_parquet(out / "measured.parquet", index=False)
    bands.to_parquet(out / "bands.parquet", index=False)
    meta = {"analyser": "v1", "converted_at": f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
            "converter": "scripts/one_off/v1_to_tables.py",
            "analyzer_version": sorted(wide["analyzer_version"].dropna().unique().tolist()),
            "config_sha256": sorted(wide["config_sha256"].dropna().unique().tolist()),
            "code_sha": sorted(wide["code_sha"].dropna().unique().tolist()),
            "audit_version": sorted(audit["audit_version"].dropna().unique().tolist()) if "audit_version" in audit else [],
            "bands": sorted(bands.loc[bands["series"] == "octave", "band_hz"].unique().tolist()),
            **report}
    (out / "run.json").write_text(json.dumps(meta, indent=1) + "\n")
    return meta


if __name__ == "__main__":
    for i in sys.argv[1:]:
        r = convert(i)
        print(json.dumps({k: v for k, v in r.items() if k != "sampled_not_measured_by_v1"}))
