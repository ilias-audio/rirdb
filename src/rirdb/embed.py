"""Learned audio embeddings (CLAP) of every analysed IR (GPU; `-e embed` env).

Per IR (onset-aligned, first analysed channel, 48 kHz, peak-normalised):
  - "ir":            the IR itself, zero-padded / cropped to exactly 10 s
  - "<SOURCE>":      each dry reference signal convolved with the IR (first 10 s, RMS-normalised)
  - "reverb_vector": mean over sources of emb(source * IR) - emb(source)

Inputs are made exactly 10 s long before the feature extractor sees them, so
its default repeat-padding / random-crop paths never run (the defaults would
repeat or randomly crop an IR). Model: laion/clap-htsat-unfused, as FDN2FDN,
pinned to MODEL_REVISION and loaded from the frozen copy in
$RIRDB_ROOT/models/ (fetch/freeze_models.sh); the dry signals come from
$RIRDB_ROOT/reference_signals/dry_sources_v1/. Anything placed on the CLAP map
(rirdb dev map place) goes through `clap_input` + `embed_batch`, the same path.

Output: $RIRDB_ROOT/embeddings/<model>/<dataset>.npz with `ir_id` and one
float16 [n_ir, 512] array per signal.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from rirdb import paths

MODEL = "laion/clap-htsat-unfused"
MODEL_REVISION = "8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a"   # Hub main since 2023-04-24
SR = 48000
N = 10 * SR
SOURCES_VERSION = "dry_sources_v1"
SOURCES = ["SPEECH", "VOCAL", "PIANO", "DRUMS", "AC_GTR", "ELEC_GTR"]
SIGNALS = ["ir", *SOURCES, "reverb_vector"]


def model_dir() -> Path:
    return paths.data_root() / "models" / MODEL.replace("/", "__") / MODEL_REVISION


def sources_dir() -> Path:
    return paths.data_root() / "reference_signals" / SOURCES_VERSION


def _fix(x: np.ndarray) -> np.ndarray:
    y = np.zeros(N, dtype=np.float32)
    y[: min(N, len(x))] = x[:N]
    return y


def load_sources() -> tuple[dict[str, np.ndarray], dict[str, str]]:
    import soundfile as sf
    import soxr

    out, sums = {}, {}
    for name in SOURCES:
        p = sources_dir() / f"{name}.wav"
        if not p.exists():
            raise FileNotFoundError(f"{p} missing: run `sbatch jobs/quick_cpu.sh bash fetch/freeze_models.sh`")
        sums[name] = hashlib.sha256(p.read_bytes()).hexdigest()
        x, fs = sf.read(str(p), dtype="float64", always_2d=True)
        x = x.mean(axis=1)
        if fs != SR:
            x = soxr.resample(x, fs, SR)
        out[name] = _fix(x / (np.sqrt(np.mean(x ** 2)) + 1e-12))
    return out, sums


class Embedder:
    def __init__(self, device: str = "cuda"):
        import torch
        from transformers import ClapModel, ClapProcessor

        local = model_dir()
        src, rev = (str(local), None) if local.exists() else (MODEL, MODEL_REVISION)
        self.torch = torch
        self.device = device
        # pytorch_model.bin: the weights every stored embedding was computed with (main has no safetensors)
        self.model = ClapModel.from_pretrained(src, revision=rev, use_safetensors=False).to(device).eval()
        self.proc = ClapProcessor.from_pretrained(src, revision=rev)
        self.source = src

    def __call__(self, audios: list[np.ndarray]) -> np.ndarray:
        inp = self.proc(audio=audios, sampling_rate=SR, return_tensors="pt")
        inp = {k: v.to(self.device) for k, v in inp.items()}
        with self.torch.no_grad():
            e = self.model.get_audio_features(**inp)
        return e.float().cpu().numpy()


class Reference:
    """Model + dry signals + their embeddings: everything `embed_batch` needs."""

    def __init__(self, device: str = "cuda"):
        import torch

        self.device = device
        self.emb = Embedder(device)
        self.sources, self.sums = load_sources()
        self.dry = dict(zip(SOURCES, self.emb([self.sources[s] for s in SOURCES])))
        self.src_t = {k: torch.from_numpy(v).to(device) for k, v in self.sources.items()}


def clap_input(x: np.ndarray, fs: int, cfg) -> np.ndarray | None:
    """First channel of an IR -> onset-cropped (ISO 3382-1), 48 kHz, peak-normalised float32."""
    import soxr

    from rirdb.analysis.preprocess import prepare

    x = np.atleast_2d(np.asarray(x, dtype=np.float64))
    p = prepare(x[:1], fs, cfg)
    if p.x.shape[1] < 16:
        return None
    ir = p.x[0]
    if fs != SR:
        ir = soxr.resample(ir, fs, SR)
    peak = np.max(np.abs(ir))
    return (ir / peak).astype(np.float32) if peak > 0 else None


def embed_batch(ref: Reference, irs: list[np.ndarray]) -> dict[str, np.ndarray]:
    """CLAP embeddings of prepared IRs (`clap_input`): one [n, 512] array per signal in SIGNALS."""
    torch = ref.emb.torch
    out = {"ir": ref.emb([_fix(s) for s in irs])}
    for name in SOURCES:
        wet = []
        for s in irs:
            h = torch.from_numpy(s[:N]).to(ref.device)
            # power-of-two FFT: arbitrary sizes (~1M samples) hit cuFFT internal errors
            nfft = 1 << int(np.ceil(np.log2(N + h.numel() - 1)))
            y = torch.fft.irfft(torch.fft.rfft(ref.src_t[name], nfft) * torch.fft.rfft(h, nfft), nfft)[:N]
            y = y / (torch.sqrt(torch.mean(y ** 2)) + 1e-12)
            wet.append(y.float().cpu().numpy())
        out[name] = ref.emb(wet)
    out["reverb_vector"] = np.mean([out[n] - ref.dry[n][None, :] for n in SOURCES], axis=0)
    return out


def _ir_signal(row: dict, adapter, root: Path, d, cfg) -> np.ndarray | None:
    from rirdb.run import _select_channels

    x, fs = adapter.load(json.loads(row["locator"]), root)
    roles = tuple(row["channel_roles"].split(","))
    xs, _ = _select_channels(x, roles, row.get("reference_role") or d.analysis.reference_role)
    return clap_input(xs[:1], fs, cfg)


def _rows(dataset_id: str) -> list[dict]:
    from rirdb.run import metrics_dir

    idx = pd.read_parquet(paths.data_root() / "index" / "irs" / f"dataset={dataset_id}" / "part-0.parquet")
    done = pd.read_parquet(metrics_dir(dataset_id) / "wide.parquet", columns=["ir_id", "error"])
    ids = set(done.loc[done["error"].fillna("") == "", "ir_id"])
    return idx[idx["ir_id"].isin(ids)].sort_values("ir_id").to_dict("records")


def embed_dataset(dataset_id: str, batch: int = 16, device: str = "cuda", shard: int = 0, n_shards: int = 1) -> Path:
    from rirdb.adapters import get_adapter
    from rirdb.analysis.config import load_config
    from rirdb.registry import get_dataset

    d = get_dataset(dataset_id)
    adapter = get_adapter(d.adapter.name, d.adapter.params)
    root = paths.files_dir(dataset_id)
    cfg = load_config()
    rows = _rows(dataset_id)[shard::n_shards]

    ref = Reference(device)
    out_ids: list[str] = []
    store: dict[str, list[np.ndarray]] = {k: [] for k in SIGNALS}
    for i in range(0, len(rows), batch):
        irs, bid = [], []
        for r in rows[i:i + batch]:
            try:
                s = _ir_signal(r, adapter, root, d, cfg)
            except Exception:  # noqa: BLE001 - skip unreadable records, keep the batch going
                s = None
            if s is not None:
                irs.append(s)
                bid.append(r["ir_id"])
        if not irs:
            continue
        for k, v in embed_batch(ref, irs).items():
            store[k].append(v)
        out_ids += bid

    out_dir = paths.data_root() / "embeddings" / MODEL.replace("/", "__")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (f"{dataset_id}.npz" if n_shards == 1 else f"{dataset_id}.part{shard:03d}-of-{n_shards:03d}.npz")
    np.savez_compressed(out, ir_id=np.array(out_ids), source_sha256=json.dumps(ref.sums), model_revision=MODEL_REVISION,
                        **{k: np.concatenate(v).astype(np.float16) for k, v in store.items() if v})
    return out


def load_embeddings(ids: set[str] | None = None, signals=("ir", "reverb_vector")) -> tuple[list[str], dict[str, np.ndarray]]:
    """Stored embeddings (float32) of the given ir_ids (all if None), in a stable order."""
    root = paths.data_root() / "embeddings" / MODEL.replace("/", "__")
    got_ids: list[str] = []
    got: dict[str, list[np.ndarray]] = {s: [] for s in signals}
    for f in sorted(root.glob("*.npz")):
        with np.load(f, allow_pickle=False) as z:
            fid = z["ir_id"].astype(str)
            m = np.ones(len(fid), bool) if ids is None else np.isin(fid, list(ids))
            if not m.any() or any(s not in z for s in signals):
                continue
            got_ids += fid[m].tolist()
            for s in signals:
                got[s].append(z[s][m].astype(np.float32))
    order = np.argsort(got_ids, kind="stable")
    return [got_ids[i] for i in order], {s: (np.concatenate(v)[order] if v else np.zeros((0, 512), np.float32))
                                         for s, v in got.items()}


def check_reproducible(dataset_id: str, n: int = 12, device: str = "cpu") -> dict:
    """Re-embed n stored IRs with the frozen model + sources; cosine similarity to the stored vectors."""
    from rirdb.adapters import get_adapter
    from rirdb.analysis.config import load_config
    from rirdb.registry import get_dataset

    d = get_dataset(dataset_id)
    adapter = get_adapter(d.adapter.name, d.adapter.params)
    rows = _rows(dataset_id)[:n]
    ids, stored = load_embeddings({r["ir_id"] for r in rows})
    pos = {i: k for k, i in enumerate(ids)}
    ref = Reference(device)
    cfg = load_config()
    irs, keep = [], []
    for r in rows:
        s = _ir_signal(r, adapter, paths.files_dir(dataset_id), d, cfg)
        if s is not None and r["ir_id"] in pos:
            irs.append(s)
            keep.append(pos[r["ir_id"]])
    new = embed_batch(ref, irs)
    out = {"dataset": dataset_id, "n": len(irs), "model": ref.emb.source, "source_sha256": ref.sums}
    for sig in ("ir", "reverb_vector"):
        a, b = new[sig], stored[sig][keep]
        cos = np.sum(a * b, 1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))
        out[f"cos_min_{sig}"] = float(cos.min())
    return out
