"""Which IRs a query selects, and what they cost to download.

    sel = rirdb.select(use="research", format="binaural", grade="A", balance="space", n=500)
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


def matching(irs: pd.DataFrame, q: Query) -> pd.DataFrame:
    """The rows of irs that the query keeps."""
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
    if q.format == "mono":                       # last: "has a mono row" among the rows the other filters keep
        has_mono = irs.loc[keep & irs["format"].eq("mono"), "space"].unique()
        keep &= irs["format"].eq("mono") | (irs["format"].eq("ambisonic") & ~irs["space"].isin(has_mono))
    elif q.format:
        keep &= irs["format"].eq(q.format)
    else:
        keep &= irs["is_primary"].astype(bool)
    return irs[keep]


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
    parts = [(d, p) for d, z in zip(need["dataset"], need["download"]) if z.endswith(".zip")    # a split zip
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

    def sizes(self) -> Sizes:
        p = self.plan()
        return Sizes(int(p["size_bytes"].fillna(0).sum()), len(p), int(p["size_bytes"].isna().sum()),
                     int(wav_bytes(self.table, self.query.format, self.query.fs).sum()))

    def download(self, out: str | Path, from_dir: str | Path | None = None, keep_archives: bool = False) -> Path:
        """Fetch, verify, and write the output folder (deliver.py)."""
        from rirdb.deliver import write_selection

        return write_selection(self, Path(out), Path(from_dir) if from_dir else None, keep_archives)


def select(use: str | None = None, format: str | None = None, origin: str = "any", grade: str = "any",
           dataset: list[str] | str | None = None, exclude_dataset: list[str] | str | None = None,
           include_unknown: bool = False, fs: int | None = None, n: int | None = None, balance: str = "none",
           seed: int = 0) -> Selection:
    """The IRs of the release that match, one row each, drawn to n with a balance (Python's `rirdb get`)."""
    as_list = lambda v: [v] if isinstance(v, str) else list(v or [])
    q = Query(use, format, origin, grade, as_list(dataset), as_list(exclude_dataset), include_unknown, fs,
              n, balance, seed)
    q.check(client.table("datasets"))
    return Selection(q, draw(matching(client.table("irs"), q), q).reset_index(drop=True))



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
