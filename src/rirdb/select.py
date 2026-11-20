"""Which IRs a query selects, and what they cost to download.

    from rirdb.select import select
    sel = select(use="research", format="binaural", grade="A", per_space=20, max_archive="2GB", n=500, balance="space")
    sel.table           one row per IR (the columns of irs, plus `weight` with a balance)
    sel.sizes()         the download, whole, and the output as WAV
    sel.download("irs/")

The filters are those of `search` and `get` (release-1.0.md, A2). None is applied unless asked for
(decision 8), with two rules from data-model.md section 3:
  - without `format`, only primary rows are taken, so a space published in two layouts counts once;
  - `format="mono"` takes mono rows, and the W channel of an Ambisonic row where the space has no mono row:
    an omni microphone or W, never one ear.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from rirdb import client
from rirdb.fetch import human, split_part_of

GRADES = ["S", "A", "B", "C", "D"]                   # best first; `grade="A"` means A or better
USES = {"research": "use_research", "commercial": "use_commercial", "training": "use_training",
        "redistribute": "redistribute"}
FORMATS = ["mono", "stereo", "binaural", "ambisonic"]
ORIGINS = ["measured", "simulated", "device"]
BALANCES = ["none", "space", "dataset", "t30"]
CHANNELS_OUT = {"mono": 1, "stereo": 2, "binaural": 2}   # ambisonic: as published (4 for first order)
T30_BINS = np.geomspace(0.1, 10, 21)                     # --balance t30: 20 bins of rt_mid, equal in log time
PROVIDES = ["dimensions", "volume", "geometry", "materials", "positions", "images", "reference_acoustics", "conditions"]
KINDS = ["indoor", "outdoor", "anechoic", "simulated", "device"]   # --kind: what kind of space, from type and origin


@dataclass
class Query:
    use: str | None = None
    format: str | None = None
    origin: str = "any"
    grade: str = "any"
    dataset: list[str] = field(default_factory=list)
    exclude_dataset: list[str] = field(default_factory=list)
    include_unknown: bool = False
    fs: int | None = None
    n: int | None = None           # draw this many (all when None)
    balance: str = "none"
    seed: int = 0
    space_type: list[str] = field(default_factory=list)   # any of these room types
    t30: str | None = None                                 # rt_mid from MIN up to MAX seconds, "1.2-2.5"; "2.5-": no top
    provides: list[str] = field(default_factory=list)     # the authors provide all of these (yes or partly)
    kind: list[str] = field(default_factory=list)         # any of these kinds of space (KINDS)
    per_space: int | None = None   # at most this many IRs of each space, spread over its rt_mid (spread_per_space)
    max_archive: str | None = None  # leave out IRs whose download is larger, "2GB" (download_bytes)
    year: str | None = None         # the dataset's year of publication: "2020-", "-2015", "2018-2020" or "2024"

    def check(self, datasets: pd.DataFrame) -> None:
        for name, value, allowed in (("use", self.use, [None, *USES]), ("format", self.format, [None, *FORMATS]),
                                     ("origin", self.origin, ["any", *ORIGINS]), ("grade", self.grade, ["any", *GRADES]),
                                     ("balance", self.balance, BALANCES)):
            if value not in allowed:
                raise ValueError(f"{name} {value!r} is not one of {', '.join(str(a) for a in allowed if a)}")
        unknown = sorted(set(self.dataset + self.exclude_dataset) - set(datasets["dataset"]))
        if unknown:
            raise ValueError(f"no dataset {', '.join(unknown)} in release {client.version()} (`rirdb search` lists them)")
        if self.n is not None and self.n < 1:
            raise ValueError("n must be at least 1")
        if self.per_space is not None and self.per_space < 1:
            raise ValueError("per_space must be at least 1")
        if self.max_archive is not None:
            parse_size(self.max_archive)
        bad = sorted(set(self.space_type) - set(allowed_values("irs", "space_type")))
        if bad:
            raise ValueError(f"space type {', '.join(bad)} is not one of {', '.join(allowed_values('irs', 'space_type'))}")
        bad = sorted(set(self.kind) - set(KINDS))
        if bad:
            raise ValueError(f"kind {', '.join(bad)} is not one of {', '.join(KINDS)}")
        bad = sorted(set(self.provides) - set(PROVIDES))
        if bad:
            raise ValueError(f"provides {', '.join(bad)} is not one of {', '.join(PROVIDES)}")
        if self.t30 is not None:
            t30_range(self.t30)
        if self.year is not None:
            year_range(self.year)


def matching(irs: pd.DataFrame, q: Query, datasets: pd.DataFrame | None = None) -> pd.DataFrame:
    """The rows of irs that the query keeps. `datasets` (the datasets table) is needed only for `provides`."""
    keep = pd.Series(True, index=irs.index)
    if q.dataset:
        keep &= irs["dataset"].isin(q.dataset)
    if q.exclude_dataset:
        keep &= ~irs["dataset"].isin(q.exclude_dataset)
    if q.use:
        allowed = ["yes", "unknown"] if q.include_unknown else ["yes"]
        keep &= irs[USES[q.use]].isin(allowed)
    if q.origin != "any":
        keep &= irs["origin"].eq(q.origin)
    if q.grade != "any":
        keep &= irs["grade"].isin(GRADES[:GRADES.index(q.grade) + 1])
    if q.space_type:
        keep &= irs["space_type"].isin(q.space_type)
    if q.kind:
        keep &= kind_of(irs["space_type"], irs["origin"]).isin(q.kind)
    if q.t30:
        lo, hi = t30_range(q.t30)
        rt = irs["rt_mid"]
        keep &= rt.notna() & (rt >= lo) & (rt < hi if hi is not None else True)
    ds = client.table("datasets") if datasets is None and (q.provides or q.year) else datasets
    if q.provides:
        ok = pd.Series(True, index=ds.index)
        for p in q.provides:
            ok &= ds[f"provides_{p}"].isin(["yes", "partial"])
        keep &= irs["dataset"].isin(ds.loc[ok, "dataset"])
    if q.year:                                   # a dataset whose year is not known is left out
        lo, hi = year_range(q.year)
        y = pd.to_numeric(ds["year"], errors="coerce")
        ok = y.notna() & (y >= (lo or 0)) & (y <= (hi or 9999))
        keep &= irs["dataset"].isin(ds.loc[ok, "dataset"])
    if q.max_archive:                            # a download of unknown size (no file listed) is left out too
        keep &= download_bytes(irs) <= parse_size(q.max_archive)
    if q.format == "mono":                     # last: "has a mono row" among the rows the other filters keep
        has_mono = irs.loc[keep & irs["format"].eq("mono"), "space"].unique()
        keep &= irs["format"].eq("mono") | (irs["format"].eq("ambisonic") & ~irs["space"].isin(has_mono))
    elif q.format:
        keep &= irs["format"].eq(q.format)
    else:
        keep &= irs["is_primary"].astype(bool)
    return irs[keep]


def kind_of(space_type: pd.Series, origin: pd.Series) -> pd.Series:
    """The kind of each space: a device or plugin, a simulated room (or a scale model), an anechoic room, outdoors,
    or indoors (every other real space: rooms and halls, but also caves, tunnels and vehicle cabins)."""
    kind = pd.Series("indoor", index=space_type.index)
    kind[space_type.eq("outdoor")] = "outdoor"
    kind[space_type.eq("anechoic")] = "anechoic"
    kind[origin.eq("simulated") | space_type.isin(["virtual", "scale_model"])] = "simulated"
    kind[origin.eq("device") | space_type.eq("device")] = "device"
    return kind


def t30_range(text: str) -> tuple[float, float | None]:
    """"1.2-2.5" -> (1.2, 2.5); "2.5-" -> (2.5, None): rt_mid from the first number up to, not including, the second."""
    lo, sep, hi = text.partition("-")
    try:
        out = (float(lo), float(hi) if hi.strip() else None)
    except ValueError:
        out = None
    if not sep or out is None or (out[1] is not None and out[1] <= out[0]):
        raise ValueError(f"t30 {text!r} is not a range in seconds such as 1.2-2.5 (or 2.5- for no upper limit)")
    return out


def year_range(text: str) -> tuple[int | None, int | None]:
    """"2020-" -> (2020, None), "-2015" -> (None, 2015), "2018-2020" -> (2018, 2020), "2024" -> (2024, 2024): years
    of publication, both ends kept."""
    lo, sep, hi = str(text).strip().partition("-")
    try:
        out = (int(lo) if lo.strip() else None, int(hi) if hi.strip() else None) if sep else (int(lo), int(lo))
    except ValueError:
        out = (None, None)
    if out == (None, None) or (None not in out and out[1] < out[0]):
        raise ValueError(f"year {text!r} is not a year or a range of years such as 2020-, -2015 or 2018-2020")
    return out


def parse_size(text: str) -> float:
    """'20GB' -> 20e9 (also kB, MB, TB; powers of 1000)."""
    units = {"TB": 1e12, "GB": 1e9, "MB": 1e6, "KB": 1e3, "B": 1}
    t = str(text).strip().upper()
    try:
        for unit, factor in units.items():
            if t.endswith(unit):
                return float(t.removesuffix(unit)) * factor
        return float(t)
    except ValueError:
        raise ValueError(f"{text!r} is not a size such as 500MB or 2GB") from None


def download_sizes() -> dict[tuple[str, str], float]:
    """Each download of the release, whole: (dataset, download) -> bytes, NaN when the release does not know.
    A split zip or tar counts with all its parts; a web folder (`@mirror`) counts all its files; the one download of a
    dataset whose host gives no size (a GitHub tarball) counts the files it unpacks to."""
    d = client.table("downloads").sort_values("source_rank").drop_duplicates(["dataset", "download"])
    out = {}
    for ds, g in d.groupby("dataset"):
        size = dict(zip(g["download"], g["size_bytes"].astype(float)))
        for z, s in size.items():
            # a files table is read only here (each is a download); a dataset pinned but not measured yet has none
            if z == "@mirror" or (np.isnan(s) and len(size) == 1):
                s = (float(client.table(f"files.{ds}")["size_bytes"].sum(min_count=1))
                     if client.has(f"files.{ds}.parquet") else np.nan)
            else:                                     # a split zip or tar counts with its parts
                s += sum(v for p, v in size.items() if split_part_of(p, z))
            out[(ds, z)] = s
    return out


def download_bytes(rows: pd.DataFrame) -> pd.Series:
    """What each IR costs to download on its own: the whole download that holds it (downloads are taken whole),
    or, in a web folder fetched file by file, its own files. NaN when the release does not know the size."""
    whole = download_sizes()
    out = pd.Series([whole.get((d, z), np.nan) for d, z in zip(rows["dataset"], rows["download"])],
                    index=rows.index, dtype=float)
    for ds in rows.loc[rows["download"] == "@mirror", "dataset"].unique():
        files = client.table(f"files.{ds}").set_index("file")["size_bytes"]
        mine = (rows["dataset"] == ds) & (rows["download"] == "@mirror")
        out[mine] = [float(files.reindex(f.split("|")).sum(min_count=1)) for f in rows.loc[mine, "file"]]
    return out


def spread_per_space(rows: pd.DataFrame, n: int) -> pd.DataFrame:
    """At most n IRs of each space, spread over its reverberation times: the n whose rt_mid are as evenly spaced
    as can be, on a log scale, from the space's shortest to its longest. A room measured in many settings (ARNI:
    5,246 panel settings) so gives n settings from its driest to its most reverberant. A space with n IRs or fewer
    keeps them all; IRs without rt_mid come last, by id. With n = 1, the IR nearest the space's median rt_mid. The same rows give the same IRs on every machine."""
    big = rows.groupby("space")["space"].transform("size") > n
    keep = [rows.index[~big]]
    for _, g in rows[big].groupby("space"):
        g = g.sort_values(["rt_mid", "ir_id"], na_position="last")
        rt = g["rt_mid"].dropna().to_numpy()
        if len(rt) <= n:                                    # every IR with an rt_mid, then the rest by id
            keep.append(g.index[:n])
            continue
        log = np.log(np.clip(rt, 1e-3, None))
        free = np.ones(len(rt), dtype=bool)
        targets = np.linspace(log[0], log[-1], n) if n > 1 else [np.median(log)]   # one IR: the room's typical one
        for target in targets:                              # the nearest IR not taken yet
            i = int(np.searchsorted(log, target))
            lo, hi = i - 1, i
            while lo >= 0 and not free[lo]:
                lo -= 1
            while hi < len(log) and not free[hi]:
                hi += 1
            j = lo if hi >= len(log) or (lo >= 0 and target - log[lo] <= log[hi] - target) else hi
            free[j] = False
        keep.append(g.index[:len(rt)][~free])
    return rows.loc[rows.index.isin(np.concatenate(keep))]


def allowed_values(table: str, column: str) -> list[str]:
    """The allowed values of a column, from columns.csv."""
    import csv

    with open(Path(__file__).with_name("columns.csv"), newline="") as f:
        row = next(r for r in csv.DictReader(f) if r["table"] == table and r["name"] == column)
    return row["allowed"].split("|")


def weights(rows: pd.DataFrame, balance: str) -> pd.Series:
    """The chance of each row in a draw (summing to 1): every space, every dataset, or every bin of rt_mid
    equally likely, or every IR (`none`). With `t30`, rt_mid outside 0.1 to 10 s counts in the end bins, and
    a row without rt_mid is never drawn."""
    if balance == "none":
        w = pd.Series(1.0, index=rows.index)
    elif balance in ("space", "dataset"):
        w = 1.0 / rows.groupby(balance)[balance].transform("size")
    else:
        bins = pd.cut(rows["rt_mid"].clip(T30_BINS[0], T30_BINS[-1]), T30_BINS, include_lowest=True)
        w = (1.0 / rows.groupby(bins, observed=True)["ir_id"].transform("size")).reindex(rows.index).fillna(0.0)
    return w / w.sum() if w.sum() > 0 else w


def draw(rows: pd.DataFrame, q: Query) -> pd.DataFrame:
    """The rows, with `weight`; with `n`, a weighted draw of n of them without replacement (`seed`), from the
    rows that can be drawn (weight above 0)."""
    out = rows.assign(weight=weights(rows, q.balance))
    if q.n is None:
        return out
    if q.n >= (out["weight"] > 0).sum():
        return out[out["weight"] > 0]
    picked = np.random.default_rng(q.seed).choice(len(out), size=q.n, replace=False, p=out["weight"].to_numpy())
    return out.iloc[np.sort(picked)]


def wav_bytes(rows: pd.DataFrame, fmt: str | None = None, fs: int | None = None) -> pd.Series:
    """Size of each IR as a float32 WAV, in the asked format and rate (44 bytes of header)."""
    channels = rows["channels"] if fmt not in CHANNELS_OUT else CHANNELS_OUT[fmt]
    samples = rows["duration_s"] * (fs or rows["fs"])
    return (samples * channels * 4 + 44).fillna(0).round().astype("int64")


def download_plan(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per download the IRs need: (dataset, download, url, size_bytes, n_irs). A download is taken
    whole, except a web folder (`@mirror`), which is fetched file by file: its size is that of the files."""
    if rows.empty:
        return pd.DataFrame(columns=["dataset", "download", "url", "size_bytes", "n_irs"])
    downloads = client.table("downloads")
    need = rows.groupby(["dataset", "download"]).size().rename("n_irs").reset_index()
    parts = [(d, p) for d, z in zip(need["dataset"], need["download"])                       # a split zip or tar
             for p in downloads.loc[downloads["dataset"] == d, "download"] if split_part_of(p, z)]
    need = pd.concat([need, pd.DataFrame(parts, columns=["dataset", "download"]).assign(n_irs=0)], ignore_index=True)
    plan = need.merge(downloads, on=["dataset", "download"], how="left")
    plan = plan.sort_values("source_rank").drop_duplicates(["dataset", "download"])
    for d in plan.loc[plan["download"] == "@mirror", "dataset"].unique():
        files = client.table(f"files.{d}").set_index("file")["size_bytes"]
        used = rows.loc[rows["dataset"] == d, "file"].str.split("|").explode().unique()
        plan.loc[(plan["dataset"] == d) & (plan["download"] == "@mirror"), "size_bytes"] = files.reindex(used).sum()
    return plan[["dataset", "download", "url", "size_bytes", "n_irs"]].reset_index(drop=True)

@dataclass
class Sizes:
    download_bytes: int            # every download the selection touches, whole
    n_downloads: int
    unknown_size: int              # downloads whose size the release does not know
    output_bytes: int              # the selected IRs, as WAV


@dataclass
class Selection:
    query: Query
    table: pd.DataFrame            # one row per IR, the columns of irs (plus `weight`)

    def plan(self) -> pd.DataFrame:
        return download_plan(self.table)

    def known_defects(self) -> list[str]:
        """What to know before using these IRs: one line per known defect they have (irs.known_defect)."""
        return defect_lines(self.table)

    def sizes(self) -> Sizes:
        p = self.plan()
        return Sizes(int(p["size_bytes"].fillna(0).sum()), len(p), int(p["size_bytes"].isna().sum()),
                     int(wav_bytes(self.table, self.query.format, self.query.fs).sum()))

    def download(self, out: str | Path, from_dir: str | Path | None = None, keep_archives: bool = False) -> Path:
        """Fetch, verify, and write the output folder (deliver.py)."""
        from rirdb.deliver import write_selection

        return write_selection(self, Path(out), Path(from_dir) if from_dir else None, keep_archives)


def defect_lines(t: pd.DataFrame) -> list[str]:
    """One line per dataset and known defect of the rows (irs.known_defect): how many IRs, and what it is."""
    if "known_defect" not in t:                   # tables made before 0.9.6
        return []
    hit = t[t["known_defect"].fillna("").ne("")]
    return [f"{d}: {plural(len(g), 'IR')} with a known defect, graded D. {w}"
            for (d, w), g in hit.groupby(["dataset", "known_defect"])]


def select(use: str | None = None, format: str | None = None, origin: str = "any", grade: str = "any",
           dataset: list[str] | str | None = None, exclude_dataset: list[str] | str | None = None,
           include_unknown: bool = False, fs: int | None = None, n: int | None = None, balance: str = "none",
           seed: int = 0, space_type: list[str] | str | None = None, t30: str | None = None,
           provides: list[str] | str | None = None, kind: list[str] | str | None = None,
           per_space: int | None = None, max_archive: str | None = None, year: str | None = None) -> Selection:
    """The IRs of the release that match, one row each, at most `per_space` of each space (spread over its
    reverberation times), then drawn to n with a balance (Python's `rirdb get`)."""
    as_list = lambda v: [v] if isinstance(v, str) else list(v or [])
    q = Query(use=use, format=format, origin=origin, grade=grade, dataset=as_list(dataset),
              exclude_dataset=as_list(exclude_dataset), include_unknown=include_unknown, fs=fs, n=n, balance=balance,
              seed=seed, space_type=as_list(space_type), t30=t30, provides=as_list(provides), kind=as_list(kind),
              per_space=per_space, max_archive=max_archive, year=year)
    datasets = client.table("datasets")
    q.check(datasets)
    rows = matching(client.table("irs"), q, datasets)
    if per_space:
        rows = spread_per_space(rows, per_space)
    return Selection(q, draw(rows, q).reset_index(drop=True))



def from_lock(path: str | Path) -> Selection:
    """The selection a selection.lock records: the same ids, from the same tables, checked by audio_hash."""
    import json

    lock = json.loads(Path(path).read_text())
    if lock["tables"] != client.version():
        raise ValueError(f"{path} was made with tables {lock['tables']}, and this rirdb reads {client.version()}: "
                         f"install the rirdb that reads tables {lock['tables']}")
    weighted = bool(lock["irs"]) and "weight" in lock["irs"][0]          # a draw with a balance
    ids = pd.DataFrame(lock["irs"], columns=["ir_id", "audio_hash", *(["weight"] if weighted else [])])
    rows = ids.merge(client.table("irs"), on="ir_id", how="left", suffixes=("_lock", ""))
    if rows["dataset"].isna().any() or (rows["audio_hash"] != rows["audio_hash_lock"]).any():
        raise ValueError(f"{path} names IRs that are not in tables {client.version()} as it recorded them")
    return Selection(Query(**lock["query"]), rows.drop(columns="audio_hash_lock"))
# ------------------------------------------------------------------------------------------- one dataset

@dataclass
class Cap:
    """What a dataset's cap keeps and leaves out (master-plan, Ilias's review of step 1)."""
    dataset: str
    cap: int
    n_in_table: int
    n_found: int
    table_wav_bytes: int           # the IRs in the table, as WAV (as published)
    all_wav_bytes: int             # every IR found, as WAV: the table's mean size times the IRs found
    download: str                  # every file of the dataset, as published (size_text)

    def lines(self) -> list[str]:
        return [
            f"{self.dataset} has a cap: {self.n_in_table:,} of the {self.n_found:,} IRs found are in the table, "
            f"the {self.cap:,} with the smallest ids in each space, condition and layout (a fixed draw, the same "
            "on every machine).",
            f"  In the table, as WAV: {human(self.table_wav_bytes)}. Every IR, as WAV: about "
            f"{human(self.all_wav_bytes)}. Every file as published: {self.download} to download.",
            f"  The rest: `rirdb get --dataset {self.dataset}` gives every file as published, with all_irs.csv, "
            "the list of every IR found and its file.",
        ]


def cap_of(dataset: str) -> Cap | None:
    d = client.table("datasets").set_index("dataset").loc[dataset]
    if pd.isna(d["cap"]):
        return None
    rows = client.table("irs").query("dataset == @dataset")
    wav = wav_bytes(rows)
    return Cap(dataset, int(d["cap"]), int(d["n_irs"]), int(d["n_irs_found"]), int(wav.sum()),
               int(wav.mean() * d["n_irs_found"]), size_text(*dataset_download_bytes(dataset)))


def dataset_download_bytes(dataset: str) -> tuple[int, int]:
    """Every file of the dataset, as published: (bytes, downloads of unknown size). Downloads are taken whole;
    a web folder counts its files."""
    downloads = client.table("downloads").query("dataset == @dataset")
    whole = downloads[downloads["download"] != "@mirror"]
    mirror = client.table(f"files.{dataset}")["size_bytes"].sum() if (downloads["download"] == "@mirror").any() else 0
    return int(whole["size_bytes"].fillna(0).sum() + mirror), int(whole["size_bytes"].isna().sum())


def size_text(n_bytes: int, n_unknown: int = 0) -> str:
    """'127.4 GB', '2.1 GB and 3 downloads of unknown size', or 'unknown'."""
    if n_unknown and not n_bytes:
        return "unknown"
    return human(n_bytes) + (f" and {plural(n_unknown, 'download')} of unknown size" if n_unknown else "")


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def grade_split(rows: pd.DataFrame) -> pd.DataFrame:
    """IRs per origin (rows) and grade (columns, with `none` for anechoic spaces): what `search` always prints."""
    g = rows["grade"].replace("", "none").fillna("none")
    out = pd.crosstab(rows["origin"], g).reindex(columns=[*GRADES, "none"], fill_value=0)
    return out.reindex([o for o in ORIGINS if o in out.index])


def licences_text(licences: pd.Series) -> str:
    """The licences of some IRs: one or two by name, more as a count (OpenAIR has one per space)."""
    names = sorted(licences.unique())
    return ", ".join(names) if len(names) <= 2 else f"{len(names)} licences, one per space"


def by_dataset(rows: pd.DataFrame, plan: pd.DataFrame) -> pd.DataFrame:
    """One line per dataset of a selection: IRs, spaces, formats, licence, and its share of the download."""
    out = rows.groupby("dataset").agg(irs=("ir_id", "size"), spaces=("space", "nunique"),
                                      formats=("format", lambda s: ", ".join(sorted(s.unique()))),
                                      licence=("licence", licences_text))
    known = plan.groupby("dataset")["size_bytes"].sum()
    unknown = plan["size_bytes"].isna().groupby(plan["dataset"]).sum()
    out["download"] = [size_text(int(known.get(d, 0)), int(unknown.get(d, 0))) for d in out.index]
    return out.sort_values("irs", ascending=False)
