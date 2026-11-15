"""Frozen CLAP maps: fit once on the corpus, place new IRs (FDN renders, simulations) on them later.

    rirdb dev map fit                                   -> $RIRDB_ROOT/maps/clap_v1/, snapshots/clap_map_v1/
    rirdb dev map place --name fdn_v3 --wav-dir DIR     embed + analyse a folder of WAVs, then place it
    rirdb dev map place --name treble --dataset ID      an indexed, analysed and embedded dataset

One map per CLAP signal (rirdb.embed):
  ir             the IR itself
  reverb_vector  what the IR does to six dry recordings (the convolved audio), averaged over them

Distances are cosine distances in the full 512-d space; the 2-D UMAP is only for looking. UMAP's
`transform` puts points unlike anything in the map next to their nearest reference points, so a
placed set can look closer than it is: read the numbers, not the picture.

Realism and coverage are measured against the clean measured rooms of the map sample, in the spirit
of precision/recall (Kynkäänniemi et al. 2019) and coverage (Naeem et al. 2020), with one change:
the radius of a reference IR is the distance to its k-th nearest neighbour from *other* datasets.
CLAP separates datasets by their recording chain (an IR's nearest neighbour comes from its own
dataset 99 % of the time), so same-dataset radii would call every new set unrealistic. A placed set
is judged as one more dataset would be, and the yardstick is how real datasets score when each is
held out in turn.
  realism   share of placed IRs inside the radius of at least one reference IR
  coverage  share of reference IRs (and rooms) with at least one placed IR inside their radius
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd

from rirdb import paths

MAP_VERSION = "clap_v1"
SIGNALS = ("ir", "reverb_vector")
LABELS = {"ir": "IR embedding", "reverb_vector": "Reverb effect on 6 dry sources"}
K = 5            # radius = distance to the k-th nearest neighbour from another dataset
KEEP = 64        # cross-dataset neighbours kept per reference IR (leave-one-dataset-out radii)
CAP = 1200       # IRs per dataset in the map sample; every room keeps at least one
UMAP_PARAMS = {"n_neighbors": 30, "min_dist": 0.15, "metric": "cosine"}
META_COLS = ["ir_id", "dataset_id", "room_id", "room_label", "category", "ir_kind", "measured", "grade", "local_key",
             "t30_mid", "edt_mid", "c80_mid", "drr_bb"]


def map_dir() -> Path:
    return paths.data_root() / "maps" / MAP_VERSION


SNAPSHOT_DIR = paths.REPO_ROOT / "snapshots" / "clap_map_v1"


def unit(X: np.ndarray) -> np.ndarray:
    X = np.asarray(X, dtype=np.float32)
    return X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-12)


# ---------------------------------------------------------------- sample
def reference_sample(cap: int = CAP, seed: int = 0) -> pd.DataFrame:
    """Preferred, de-duplicated, error-free IRs; at most `cap` per dataset, stratified by room."""
    from rirdb.query import connect

    con = connect()
    have = {r[0] for r in con.execute("DESCRIBE corpus_dedup").fetchall()}
    cols = ", ".join(c for c in META_COLS if c in have)
    df = con.execute(f"""SELECT {cols}, ir_id IN (SELECT ir_id FROM clean_rooms) AS clean FROM corpus_dedup
                         WHERE coalesce(error, '') = '' AND preferred AND dup_of IS NULL""").df()
    rng = np.random.default_rng(seed)
    parts = []
    for _, g in df.sort_values("ir_id").groupby("dataset_id"):
        if len(g) > cap:
            g = g.sample(frac=1.0, random_state=int(rng.integers(1 << 31)))
            first = g.groupby("room_id").head(1)
            g = pd.concat([first, g.drop(first.index).head(max(cap - len(first), 0))])
        parts.append(g)
    return pd.concat(parts).sort_values("ir_id").reset_index(drop=True)


# ---------------------------------------------------------------- neighbours
def cross_knn(X: np.ndarray, ds: np.ndarray, keep: int = KEEP, chunk: int = 2048) -> tuple[np.ndarray, np.ndarray]:
    """Nearest `keep` neighbours of every row among rows of other datasets: (index, cosine distance)."""
    keep = min(keep, len(X) - 1)
    idx = np.empty((len(X), keep), np.int32)
    dist = np.empty((len(X), keep), np.float32)
    for s in range(0, len(X), chunk):
        sim = X[s:s + chunk] @ X.T
        sim[ds[s:s + chunk, None] == ds[None, :]] = -np.inf
        top = np.argpartition(-sim, keep - 1, axis=1)[:, :keep]
        d = 1 - np.take_along_axis(sim, top, axis=1)
        o = np.argsort(d, axis=1)
        idx[s:s + chunk] = np.take_along_axis(top, o, axis=1)
        dist[s:s + chunk] = np.take_along_axis(d, o, axis=1)
    return idx, dist


def radius_without(nbr_idx: np.ndarray, nbr_dist: np.ndarray, ds: np.ndarray, held_out, k: int = K) -> tuple[np.ndarray, int]:
    """k-th cross-dataset neighbour distance ignoring dataset `held_out` (None: ignore nothing).

    Falls back to the last kept neighbour (an upper bound) when fewer than k remain; returns the count."""
    d = nbr_dist if held_out is None else np.where(ds[nbr_idx] == held_out, np.inf, nbr_dist)
    r = np.sort(d, axis=1)[:, k - 1]
    short = ~np.isfinite(r)
    r[short] = nbr_dist[short, -1]
    return r, int(short.sum())


def realism_coverage(Q: np.ndarray, R: np.ndarray, radius: np.ndarray, room: np.ndarray,
                     q_ds: np.ndarray | None = None, r_ds: np.ndarray | None = None, chunk: int = 2048) -> dict:
    """Q placed (unit rows), R reference (unit rows) with per-row radius. Optional dataset labels keep
    a query from counting against its own dataset (random real baselines)."""
    inside_any = np.zeros(len(Q), bool)
    nearest = np.full(len(Q), np.inf, np.float32)
    covered = np.zeros(len(R), bool)
    for s in range(0, len(Q), chunk):
        d = 1 - Q[s:s + chunk] @ R.T
        if q_ds is not None:
            d[q_ds[s:s + chunk, None] == r_ds[None, :]] = np.inf
        inside = d <= radius[None, :]
        inside_any[s:s + chunk] = inside.any(axis=1)
        nearest[s:s + chunk] = d.min(axis=1)
        covered |= inside.any(axis=0)
    rooms = pd.Series(covered).groupby(room).any()
    return {"n": int(len(Q)), "realism": round(float(inside_any.mean()), 4) if len(Q) else None,
            "coverage_ir": round(float(covered.mean()), 4), "coverage_room": round(float(rooms.mean()), 4),
            "nearest_q": np.percentile(nearest, [5, 25, 50, 75, 95]).round(4).tolist() if len(Q) else None,
            "_inside": inside_any, "_nearest": nearest}


def _public(r: dict) -> dict:
    return {k: v for k, v in r.items() if not k.startswith("_")}


# ---------------------------------------------------------------- fit
def fit(cap: int = CAP, seed: int = 0) -> Path:
    import joblib
    import umap

    from rirdb.embed import MODEL, MODEL_REVISION, SOURCES_VERSION, load_embeddings

    out = map_dir()
    out.mkdir(parents=True, exist_ok=True)
    sel = reference_sample(cap, seed)
    ids, E = load_embeddings(set(sel["ir_id"]), SIGNALS)
    sel = sel.set_index("ir_id").loc[ids].reset_index()
    clean = sel["clean"].to_numpy(bool)
    ds = sel["dataset_id"].to_numpy()
    meta = {"map_version": MAP_VERSION, "created": f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
            "model": MODEL, "model_revision": MODEL_REVISION, "dry_sources": SOURCES_VERSION,
            "n": int(len(sel)), "n_clean": int(clean.sum()), "cap_per_dataset": cap, "seed": seed, "k": K, "keep": KEEP,
            "umap": UMAP_PARAMS, "versions": {p: version(p) for p in ("umap-learn", "pynndescent", "numba", "numpy", "scikit-learn")},
            "git": _git_sha(), "xy_range": {}, "radius_fallbacks": {}}
    baseline = {}
    for sig in SIGNALS:
        X = unit(E[sig])
        red = umap.UMAP(random_state=seed, **UMAP_PARAMS).fit(X)
        xy = red.embedding_
        lo, hi = xy.min(0), xy.max(0)
        meta["xy_range"][sig] = {"lo": lo.tolist(), "hi": hi.tolist()}
        sel[f"x_{sig}"], sel[f"y_{sig}"] = ((xy - lo) / (hi - lo)).T.astype(np.float32)
        joblib.dump(red, out / f"umap_{sig}.joblib")
        # cross-dataset neighbours among the clean measured rooms
        Xc, dsc, roomc = X[clean], ds[clean], sel.loc[clean, "room_id"].to_numpy()
        nbr, nd = cross_knn(Xc, dsc)
        np.savez_compressed(out / f"knn_{sig}.npz", idx=nbr, dist=nd)
        r_all, short = radius_without(nbr, nd, dsc, None)
        meta["radius_fallbacks"][sig] = short
        sel[f"radius_{sig}"] = np.nan
        sel.loc[clean, f"radius_{sig}"] = r_all
        # yardstick: each real dataset placed against the others (leave-one-dataset-out radii)
        rows = []
        for D in sorted(set(dsc)):
            m = dsc == D
            r_wo, _ = radius_without(nbr[~m], nd[~m], dsc, D)
            res = realism_coverage(Xc[m], Xc[~m], r_wo, roomc[~m])
            rows.append({"dataset": D, **_public(res)})
        baseline[sig] = rows
    np.savez_compressed(out / "embeddings.npz", ir_id=np.array(ids), **{s: E[s].astype(np.float16) for s in SIGNALS})
    sel.to_parquet(out / "reference.parquet", index=False)
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    (out / "baseline.json").write_text(json.dumps(baseline, indent=1))
    # small, committed copy: coordinates, radii, metadata (embeddings + reducers stay in RIRDB_ROOT)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    keep = ["ir_id", "dataset_id", "room_id", "clean"] + [f"{a}_{s}" for s in SIGNALS for a in ("x", "y", "radius")]
    sel[keep].to_parquet(SNAPSHOT_DIR / "reference.parquet", index=False, compression="zstd")
    for f in ("meta.json", "baseline.json"):
        (SNAPSHOT_DIR / f).write_text((out / f).read_text())
    return out


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "-C", str(paths.REPO_ROOT), "rev-parse", "--short=12", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


# ---------------------------------------------------------------- load / place
class Bundle:
    def __init__(self, root: Path | None = None):
        root = root or map_dir()
        if not (root / "meta.json").exists():
            raise FileNotFoundError(f"no map in {root}: run `sbatch jobs/quick_cpu.sh pixi run rirdb dev map fit`")
        self.root = root
        self.meta = json.loads((root / "meta.json").read_text())
        self.baseline = json.loads((root / "baseline.json").read_text())
        self.ref = pd.read_parquet(root / "reference.parquet")
        with np.load(root / "embeddings.npz") as z:
            assert (z["ir_id"].astype(str) == self.ref["ir_id"].to_numpy()).all(), "reference / embeddings out of sync"
            self.X = {s: unit(z[s]) for s in SIGNALS}
        self._red = {}

    def reducer(self, sig: str):
        import joblib

        if sig not in self._red:
            self._red[sig] = joblib.load(self.root / f"umap_{sig}.joblib")
        return self._red[sig]

    def to_xy(self, sig: str, X: np.ndarray) -> np.ndarray:
        r = self.meta["xy_range"][sig]
        lo, hi = np.array(r["lo"]), np.array(r["hi"])
        return (self.reducer(sig).transform(unit(X)) - lo) / (hi - lo)


def place(name: str, ids: list[str], E: dict[str, np.ndarray], info: pd.DataFrame | None = None) -> Path:
    """Place embedded IRs on the frozen maps; write maps/clap_v1/placed/<name>/{points.parquet, embeddings.npz, summary.json}."""
    b = Bundle()
    clean = b.ref["clean"].to_numpy(bool)
    pts = pd.DataFrame({"ir_id": ids})
    if info is not None:
        pts = pts.merge(info, on="ir_id", how="left")
    summary = {"name": name, "n": len(ids), "map_version": MAP_VERSION, "model_revision": b.meta["model_revision"],
               "created": f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "signals": {}}
    for sig in SIGNALS:
        Q = unit(E[sig])
        xy = b.to_xy(sig, Q)
        pts[f"x_{sig}"], pts[f"y_{sig}"] = xy.T.astype(np.float32)
        R, radius = b.X[sig][clean], b.ref.loc[clean, f"radius_{sig}"].to_numpy(np.float32)
        room, rds = b.ref.loc[clean, "room_id"].to_numpy(), b.ref.loc[clean, "dataset_id"].to_numpy()
        res = realism_coverage(Q, R, radius, room)
        j = np.argmax(Q @ R.T, axis=1)
        pts[f"inside_{sig}"] = res["_inside"]
        pts[f"nearest_dist_{sig}"] = res["_nearest"]
        pts[f"nearest_ir_{sig}"] = b.ref.loc[clean, "ir_id"].to_numpy()[j]
        pts[f"nearest_dataset_{sig}"] = rds[j]
        # yardstick: each real dataset placed against the others (from fit), judged exactly like this set
        # (radii computed without it). Coverage grows with the number of IRs: compare like with like.
        held = pd.DataFrame(b.baseline[sig])
        summary["signals"][sig] = {
            "placed": _public(res),
            "real_datasets_held_out": {
                "n_datasets": int(len(held)),
                "realism_q25_50_75": held["realism"].quantile([.25, .5, .75]).round(4).tolist(),
                "coverage_room_q25_50_75": held["coverage_room"].quantile([.25, .5, .75]).round(4).tolist(),
                "median_nearest": float(np.median([r[2] for r in held["nearest_q"]])),
                "per_dataset": held[["dataset", "n", "realism", "coverage_room"]].to_dict("records")},
        }
    out = map_dir() / "placed" / name
    out.mkdir(parents=True, exist_ok=True)
    pts.to_parquet(out / "points.parquet", index=False)
    np.savez_compressed(out / "embeddings.npz", ir_id=np.array(ids), **{s: np.asarray(E[s], np.float16) for s in SIGNALS})
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    return out


def placed_sets() -> list[Path]:
    return sorted(p for p in (map_dir() / "placed").glob("*") if (p / "summary.json").exists())


# ---------------------------------------------------------------- inputs
def from_dataset(dataset_id: str) -> tuple[list[str], dict[str, np.ndarray], pd.DataFrame]:
    from rirdb.embed import load_embeddings
    from rirdb.query import connect

    info = connect().execute(f"SELECT {', '.join(META_COLS)} FROM corpus_dedup WHERE dataset_id = ?", [dataset_id]).df()
    ids, E = load_embeddings(set(info["ir_id"]), SIGNALS)
    if not ids:
        raise ValueError(f"no embeddings for {dataset_id}: run `rirdb dev embed {dataset_id}` (GPU job) first")
    return ids, E, info


def _analyze_wav(path: str) -> dict:
    import soundfile as sf

    from rirdb.analysis.config import load_config
    from rirdb.analysis.pipeline import analyze_ir

    x, fs = sf.read(path, dtype="float64", always_2d=True)
    try:
        s = analyze_ir(x.T[:1], fs, roles=("omni",), capture_format="mono_omni", cfg=load_config(), want_features=False).scalars
    except Exception as e:  # noqa: BLE001 - placement still works without metrics
        s = {"error": f"{type(e).__name__}: {e}"}
    keep = ("t30_mid", "edt_mid", "c80_mid", "drr_bb", "grade", "error")
    return {"fs": fs, "n_channels": x.shape[1], **{k: s.get(k) for k in keep}}


def from_wavs(name: str, wav_dir: Path, pattern: str = "**/*.wav", device: str = "auto", workers: int = 4,
              batch: int = 16) -> tuple[list[str], dict[str, np.ndarray], pd.DataFrame]:
    """Analyse (ISO metrics, first channel) and embed a folder of IR files exactly like the corpus."""
    from concurrent.futures import ProcessPoolExecutor

    import soundfile as sf

    from rirdb.analysis.config import load_config
    from rirdb.embed import Reference, clap_input, embed_batch

    files = sorted(p for p in Path(wav_dir).glob(pattern) if p.is_file())
    if not files:
        raise FileNotFoundError(f"no files matching {pattern} in {wav_dir}")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        info = pd.DataFrame(list(ex.map(_analyze_wav, [str(f) for f in files], chunksize=4)))
    keys = [str(f.relative_to(wav_dir)) for f in files]
    info.insert(0, "ir_id", [f"{name}:{k}" for k in keys])
    info.insert(1, "local_key", keys)
    if device == "auto":
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    ref, cfg = Reference(device), load_config()
    ids, store = [], {s: [] for s in SIGNALS}
    for i in range(0, len(files), batch):
        irs, bid = [], []
        for f, rid in zip(files[i:i + batch], info["ir_id"].iloc[i:i + batch]):
            x, fs = sf.read(str(f), dtype="float64", always_2d=True)
            s = clap_input(x.T, fs, cfg)
            if s is not None:
                irs.append(s)
                bid.append(rid)
        if irs:
            e = embed_batch(ref, irs)
            for s in SIGNALS:
                store[s].append(e[s])
            ids += bid
    return ids, {s: np.concatenate(v) for s, v in store.items()}, info
