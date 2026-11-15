"""The numbers the website shows, all computed from the release tables.

  search_data(irs, spaces)   the spaces and the IR counts behind the search on the datasets and spaces pages
  acoustics(rows)            per dataset: the distributions drawn on its page (T30, T30 per octave, DRR, C50)
  metadata(rows, own)        per dataset: which metadata it fills, and for what share of its IRs

The axes are the same for every dataset, so two pages can be compared at a glance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from rirdb.select import GRADES, USES

# T30 bins: the R10 preferred numbers (ten per decade, equal in log time), 0.05 to 25 s.
# A value outside counts in the first or last bin.
T30_EDGES = [0.05, 0.063, 0.08, 0.1, 0.125, 0.16, 0.2, 0.25, 0.315, 0.4, 0.5, 0.63, 0.8, 1, 1.25, 1.6, 2, 2.5,
             3.15, 4, 5, 6.3, 8, 10, 12.5, 16, 20, 25]
# DRR and C50 bins: 2 dB wide, -20 to +30 dB; a value outside counts in the first or last bin.
DB_EDGES = list(range(-20, 32, 2))
# The octaves drawn on a dataset page (the tables also hold 31.5 Hz and 16 kHz, rarely valid).
BANDS = [63, 125, 250, 500, 1000, 2000, 4000, 8000]
SEARCH_FORMATS = ["mono", "stereo", "binaural", "ambisonic"]   # the formats `rirdb search --format` takes
OCTAVE_NAMES = {31: "31.5 Hz", 63: "63 Hz", 125: "125 Hz", 250: "250 Hz", 500: "500 Hz", 1000: "1 kHz", 2000: "2 kHz",
                4000: "4 kHz", 8000: "8 kHz", 16000: "16 kHz"}


def bin_index(values: pd.Series, edges: list[float]) -> pd.Series:
    """The bin of each value (0 = first), clipped into the range; -1 where the value is missing."""
    idx = np.searchsorted(edges, values.to_numpy(dtype=float), side="right") - 1
    idx = np.clip(idx, 0, len(edges) - 2)
    return pd.Series(np.where(values.isna(), -1, idx), index=values.index)


def histogram(values: pd.Series, edges: list[float]) -> list[int]:
    b = bin_index(values.dropna(), edges)
    return [int(n) for n in np.bincount(b, minlength=len(edges) - 1)]


def five_numbers(values: pd.Series) -> dict | None:
    """5th, 25th, 50th, 75th and 95th percentiles, and the count; None without values."""
    v = values.dropna()
    if v.empty:
        return None
    q = v.quantile([0.05, 0.25, 0.5, 0.75, 0.95]).to_numpy()
    return {"p5": q[0], "p25": q[1], "p50": q[2], "p75": q[3], "p95": q[4], "n": len(v)}


# ------------------------------------------------------------------------------------- the search

def search_data(irs: pd.DataFrame, spaces: pd.DataFrame) -> dict:
    """What the search on the datasets and spaces pages filters, small enough to sit in the page.

    `spaces`: one entry per space (its dataset, name, type, origin, volume, and medians over its primary rows).
    `counts`: how many IRs share one space, format, grade, T30 bin and licence uses. A filter keeps or drops
    whole count rows, so the page can add up the IRs and spaces a query keeps, as `rirdb search` does:
    without a format, primary rows only; with `mono`, mono rows and the W channel of an Ambisonic row where the
    space has no mono row. (The command line decides "has no mono row" after the other filters; the page decides
    it on the whole table. The two differ only for a space whose mono rows another filter removes.)
    """
    sp = spaces[spaces["n_irs"] > 0].sort_values(["dataset", "space"]).reset_index(drop=True)
    index = {s: i for i, s in enumerate(sp["space"])}
    rows = irs[irs["space"].isin(index)]
    primary = rows[rows["is_primary"].astype(bool)]
    medians = primary.groupby("space")[["rt_mid", "drr_db"]].median()
    formats = rows.groupby("space")["format"].agg(lambda f: sorted(set(f)))
    has_mono = set(rows.loc[rows["format"].eq("mono"), "space"])

    def num(x, digits=2):
        return None if pd.isna(x) else round(float(x), digits)

    space_list = [{
        "id": s["space"], "dataset": s["dataset"], "label": s["label"] or s["space"].split("/", 1)[1],
        "type": s["space_type"], "origin": s["origin"], "volume": num(s["volume_m3"], 1),
        "t30": num(medians["rt_mid"].get(s["space"])), "drr": num(medians["drr_db"].get(s["space"]), 1),
        "formats": formats.get(s["space"], []), "licence": s["licence"], "n": int(s["n_irs"]),
    } for _, s in sp.iterrows()]

    keyed = pd.DataFrame({
        "space": rows["space"].map(index),
        "format": rows["format"],
        "grade": rows["grade"].fillna("").replace("", "-"),
        "t30": bin_index(rows["rt_mid"], T30_EDGES),
        "primary": rows["is_primary"].astype(bool).astype(int),
        "mono_w": (rows["format"].eq("ambisonic") & ~rows["space"].isin(has_mono)).astype(int),
        **{use: rows[col].eq("yes").astype(int) for use, col in USES.items()},
    })
    counts = keyed.groupby(list(keyed.columns), sort=True).size().reset_index(name="n")
    return {
        "t30_edges": T30_EDGES, "grades": GRADES, "formats": SEARCH_FORMATS, "uses": list(USES),
        "columns": list(counts.columns), "counts": counts.to_numpy().tolist(), "spaces": space_list,
    }


# ------------------------------------------------------------------------------------- one dataset

def acoustics(rows: pd.DataFrame) -> dict:
    """The distributions drawn on a dataset page, over its primary rows (a space in two layouts counts once)."""
    return {
        "t30": histogram(rows["rt_mid"], T30_EDGES),
        "t30_n": int(rows["rt_mid"].notna().sum()),
        "bands": {b: five_numbers(rows[f"t30_{b}"]) for b in BANDS},
        "drr": histogram(rows["drr_db"], DB_EDGES),
        "drr_n": int(rows["drr_db"].notna().sum()),
        "c50": histogram(rows["c50_mid"], DB_EDGES),
        "c50_n": int(rows["c50_mid"].notna().sum()),
        "ranges": {c: five_numbers(rows[c]) for c in ("rt_mid", "edt_mid", "drr_db", "c50_mid", "sti",
                                                      "distance_m", "volume_m3")},
    }


# What the metadata panel lists: (group, what it is, the columns that must all be filled for an IR to count).
METADATA = [
    ("Positions and room", "source position", ["src_x", "src_y", "src_z"]),
    ("Positions and room", "receiver position", ["rcv_x", "rcv_y", "rcv_z"]),
    ("Positions and room", "source-receiver distance", ["distance_m"]),
    ("Positions and room", "room volume", ["volume_m3"]),
    ("Setup", "condition (state of the room)", ["condition"]),
    ("Setup", "source label", ["source"]),
    ("Setup", "receiver label", ["receiver"]),
    ("Room values", "T30 / T20 (rt_mid)", ["rt_mid"]),
    ("Room values", "T30 over the whole band", ["rt_broadband"]),
    ("Room values", "EDT", ["edt_mid"]),
    ("Room values", "DRR", ["drr_db"]),
    ("Room values", "C50 and C80", ["c50_mid", "c80_mid"]),
    ("Room values", "D50 and centre time", ["d50_mid", "ts_mid"]),
    ("Room values", "STI", ["sti"]),
    ("Room values", "bass and treble ratio", ["bass_ratio", "treble_ratio"]),
    ("Room values", "mixing time", ["mixing_time_ms"]),
    ("Room values", "early IACC (binaural)", ["iacc_early"]),
    ("Room values", "lateral fraction", ["lateral_fraction"]),
    *[("T30 per octave", OCTAVE_NAMES[b], [f"t30_{b}"]) for b in [31, *BANDS, 16000]],
    ("Signal", "usable top frequency", ["usable_top_hz"]),
    ("Signal", "effective bit depth", ["effective_bits"]),
    ("Signal", "peak-to-noise ratio", ["peak_to_noise_db"]),
]


def filled(rows: pd.DataFrame, col: str) -> pd.Series:
    """True where the column holds a value (an empty string is no value)."""
    v = rows[col]
    return v.notna() & v.astype(str).ne("")


def metadata(rows: pd.DataFrame, own: pd.DataFrame | None) -> list[dict]:
    """For each entry of METADATA, and for each of the dataset's own fields (source_meta), the IRs that have it."""
    out = []
    for group, label, cols in METADATA:
        have = pd.Series(True, index=rows.index)
        for c in cols:
            have &= filled(rows, c)
        out.append({"group": group, "label": label, "columns": cols, "n": int(have.sum()), "of": len(rows)})
    if own is not None and len(own):
        mine = own[own["ir_id"].isin(rows["ir_id"])]
        for c in own.columns.drop("ir_id"):
            out.append({"group": "The dataset's own fields", "label": c, "columns": [],
                        "n": int(filled(mine, c).sum()), "of": len(rows)})
    return out


def space_bands(rows: pd.DataFrame) -> pd.DataFrame:
    """Median T30 per octave for each space (primary rows), for the small curve in the spaces table."""
    return rows.groupby("space")[[f"t30_{b}" for b in BANDS]].median()
