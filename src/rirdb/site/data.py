"""The numbers the website shows, all computed from the release tables.

  search_data(irs, spaces, datasets)  what the datasets and rooms pages search (written once, as search-data.js)
  acoustics(rows)                     per dataset: the distributions drawn on its page
  labels(rows)                        per dataset: which per-IR labels it fills, and for how many IRs
  space_bands(rows)                   per room: the median T30 in each octave

The axes are the same for every dataset, so two pages can be compared at a glance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from rirdb.select import GRADES, PROVIDES, USES, dataset_download_bytes, size_text
from rirdb.site.words import kind_of, room_label

# T30 bins: the R10 preferred numbers (ten per decade, equal in log time), 0.05 to 25 s.
# A value outside counts in the first or last bin.
T30_EDGES = [0.05, 0.063, 0.08, 0.1, 0.125, 0.16, 0.2, 0.25, 0.315, 0.4, 0.5, 0.63, 0.8, 1, 1.25, 1.6, 2, 2.5,
             3.15, 4, 5, 6.3, 8, 10, 12.5, 16, 20, 25]
# DRR and C50 bins: 2 dB wide, -20 to +30 dB; a value outside counts in the first or last bin.
DB_EDGES = list(range(-20, 32, 2))
# The octaves drawn on a dataset page (the tables also hold 31.5 Hz and 16 kHz, rarely valid).
BANDS = [63, 125, 250, 500, 1000, 2000, 4000, 8000]


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


MANY_ROOMS = 1000   # a dataset with more rooms than this generates them (one room per IR): shown per room type


def shown_space(spaces: pd.DataFrame) -> pd.Series:
    """The entry the site shows each room as: the room itself or, in a dataset of more than MANY_ROOMS rooms, one
    entry per room type (`<dataset>/all-<type>-<origin>`). The pages stay small; every filter still counts right."""
    many = spaces.groupby("dataset")["space"].transform("size") > MANY_ROOMS
    group = spaces["dataset"] + "/all-" + spaces["space_type"].fillna("") + "-" + spaces["origin"].fillna("")
    return spaces["space"].where(~many, group)


def primary(irs: pd.DataFrame) -> pd.DataFrame:
    """One layout per space (a space published in two layouts counts once), as `rirdb search` counts."""
    return irs[irs["is_primary"].astype(bool)]


def num(x, digits: int = 2):
    return None if x is None or pd.isna(x) else round(float(x), digits)


def text(x) -> str:
    """A cell as text; a missing value is an empty string."""
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)


# ------------------------------------------------------------------------------------------- the search

def search_data(irs: pd.DataFrame, spaces: pd.DataFrame, datasets: pd.DataFrame) -> dict:
    """What the datasets and rooms pages search, in one object (site.js reads it).

    `datasets`: all of them, measured or not, with what a card shows and what the dataset-level filters read.
    `spaces`: one entry per room of the measured datasets.
    `counts`: how many IRs share one room, format, grade, T30 bin and licence uses. A filter keeps or drops whole
    count rows, so the page adds up the IRs a query keeps as `rirdb search` does: without a format, primary rows
    only; with `mono`, mono rows and the W channel of an Ambisonic row where the room has no mono row. (The command
    line decides "has no mono row" after the other filters, the page on the whole table; the two differ only for a
    room whose mono rows another filter removes.)
    """
    ds = datasets.sort_values("name", key=lambda s: s.str.lower()).reset_index(drop=True)
    d_index = {d: i for i, d in enumerate(ds["dataset"])}
    sp = spaces[spaces["n_irs"] > 0].sort_values(["dataset", "space"]).reset_index(drop=True)
    rows = irs[irs["space"].isin(set(sp["space"]))]
    has_mono = set(rows.loc[rows["format"].eq("mono"), "space"])          # per room, as the command line decides
    mono_w = rows["format"].eq("ambisonic") & ~rows["space"].isin(has_mono)
    n_rooms = primary(rows).groupby("dataset")["space"].nunique()
    # a dataset of thousands of generated rooms: one entry per room type (shown_space)
    shown = shown_space(sp)
    rows = rows.assign(space=rows["space"].map(dict(zip(sp["space"], shown))))
    one = shown.map(shown.value_counts()) == 1
    sp = sp.assign(space=shown, label=sp["label"].where(one, shown.map(shown.value_counts()).map("{:,} rooms".format)),
                   volume_m3=sp["volume_m3"].where(one), dims_m=sp["dims_m"].where(one),
                   same_as=sp["same_as"].where(one)).drop_duplicates("space").reset_index(drop=True)
    s_index = {s: i for i, s in enumerate(sp["space"])}
    prim = primary(rows)
    medians = prim.groupby("space")[["rt_mid", "drr_db"]].median()
    space_formats = rows.groupby("space")["format"].agg(lambda f: sorted(set(f)))
    room_words = (sp["label"].fillna("") + " " + sp["space_type"]).groupby(sp["dataset"]).agg(" ".join)

    def card(d) -> dict:
        i = d["dataset"]
        mine, mine_p = rows[rows["dataset"] == i], prim[prim["dataset"] == i]
        measured = d["status"] == "measured"
        formats = sorted(mine["format"].unique()) if measured else \
            [f.strip() for f in (d["formats_stated"] or "").split(",") if f.strip()]
        size, unknown = dataset_download_bytes(i) if measured else (0, 0)
        words = " ".join(str(x) for x in (d["name"], i, d["description"], d["institution"], d["country"], d["host"],
                                         d["file_formats"], room_words.get(i, "")) if x)
        return {
            "id": i, "name": d["name"], "year": None if pd.isna(d["year"]) else int(d["year"]),
            "by": ", ".join(x for x in (d["institution"], d["country"]) if x), "status": d["status"],
            "origin": d["origin"], "description": d["description"], "licence": d["licence"],
            "uses": {u: d[c] for u, c in USES.items()}, "formats": formats,
            "provides": [p for p in PROVIDES if d[f"provides_{p}"] in ("yes", "partial")],
            "size": size_text(size, unknown) if measured else (d["size_stated"] or ""),
            "n": int(len(mine_p)), "rooms": int(n_rooms.get(i, 0)),
            "t30": histogram(mine_p["rt_mid"], T30_EDGES) if measured else [],
            "t30_median": num(mine_p["rt_mid"].median()) if measured else None,
            "grades": {g: int(n) for g, n in mine["grade"].replace("", "none").value_counts().items()},
            "text": words.lower(),
        }

    space_list = [{
        "id": s["space"], "d": d_index[s["dataset"]], "label": room_label(s["label"], s["space"]),
        "type": s["space_type"], "origin": s["origin"], "kind": kind_of(s["space_type"], s["origin"]),
        "volume": num(s["volume_m3"], 0), "dims": text(s.get("dims_m")),
        "t30": num(medians["rt_mid"].get(s["space"])), "drr": num(medians["drr_db"].get(s["space"]), 1),
        "formats": space_formats.get(s["space"], []), "same_as": text(s["same_as"]),
    } for _, s in sp.iterrows()]

    keyed = pd.DataFrame({
        "space": rows["space"].map(s_index),
        "format": rows["format"],
        "grade": rows["grade"].fillna("").replace("", "-"),
        "t30": bin_index(rows["rt_mid"], T30_EDGES),
        "primary": rows["is_primary"].astype(bool).astype(int),
        "mono_w": mono_w.astype(int),
        **{use: rows[col].eq("yes").astype(int) for use, col in USES.items()},
    })
    counts = keyed.groupby(list(keyed.columns), sort=True).size().reset_index(name="n")
    return {
        "t30_edges": T30_EDGES, "grades": GRADES, "columns": list(counts.columns),
        "counts": counts.to_numpy().tolist(), "spaces": space_list,
        "datasets": [card(d) for _, d in ds.iterrows()],
    }


# ------------------------------------------------------------------------------------------- one dataset

def acoustics(rows: pd.DataFrame) -> dict:
    """The distributions drawn on a dataset page, over its primary rows."""
    return {
        "t30": histogram(rows["rt_mid"], T30_EDGES), "t30_n": int(rows["rt_mid"].notna().sum()),
        "bands": {b: five_numbers(rows[f"t30_{b}"]) for b in BANDS},
        "drr": histogram(rows["drr_db"], DB_EDGES), "drr_n": int(rows["drr_db"].notna().sum()),
        "c50": histogram(rows["c50_mid"], DB_EDGES), "c50_n": int(rows["c50_mid"].notna().sum()),
        "ranges": {c: five_numbers(rows[c]) for c in ("rt_mid", "edt_mid", "drr_db", "c50_mid", "sti",
                                                      "distance_m", "volume_m3")},
    }


def filled(rows: pd.DataFrame, col: str) -> pd.Series:
    """True where the column holds a value (an empty string is no value)."""
    v = rows[col]
    return v.notna() & v.astype(str).ne("")


# The per-IR labels a dataset may fill: (what it is, the columns that must all be filled).
LABELS = [("room condition", ["condition"]), ("source name", ["source"]), ("receiver name", ["receiver"]),
          ("source position", ["src_x", "src_y", "src_z"]), ("receiver position", ["rcv_x", "rcv_y", "rcv_z"]),
          ("source-receiver distance", ["distance_m"]), ("room volume", ["volume_m3"])]


def labels(rows: pd.DataFrame) -> list[tuple[str, int, int]]:
    """For each per-IR label: (what it is, IRs that have it, IRs)."""
    out = []
    for what, cols in LABELS:
        have = pd.Series(True, index=rows.index)
        for c in cols:
            have &= filled(rows, c)
        out.append((what, int(have.sum()), len(rows)))
    return out


def own_fields(own: pd.DataFrame | None, ids: pd.Series) -> list[tuple[str, int, list[str]]]:
    """The dataset's own per-IR fields (source_meta): (name, distinct values, up to three examples).
    Container boilerplate (the SOFA convention's name) is left out."""
    if own is None or own.empty:
        return []
    mine = own[own["ir_id"].isin(ids)]
    out = []
    for c in mine.columns.drop("ir_id"):
        if c == "sofa_conventions":
            continue
        v = mine[c][filled(mine, c)].astype(str)
        if not v.empty:
            out.append((c, int(v.nunique()), list(dict.fromkeys(v))[:3]))
    return out


def space_bands(rows: pd.DataFrame) -> pd.DataFrame:
    """Median T30 per octave for each space (primary rows), for the small curve in the rooms list."""
    return rows.groupby("space")[[f"t30_{b}" for b in BANDS]].median()
