"""Adapter `hf_parquet`: IRs stored inside the rows of Hugging Face parquet files.

The audio column is either an HF Audio column (a struct {bytes, path}: the bytes of a WAV or FLAC file, which holds
its own rate) or a list of channels, each a list of float samples, with the rate in `fs`. One IR per row, or with
`per_channel` one IR per channel (spaced microphones, data-model.md section 3).

The index reads the small columns, and the audio column one row group at a time, only to learn each IR's channels,
length and rate. A load reads the audio column of the IR's row group from its start up to the IR's row, never the
whole file; the last two rows read are kept, so the channels of one row cost one read.

adapter.params:
  glob          the parquet files (required), e.g. "data/rir_*.parquet"
  audio         the audio column (default "audio")
  fs            the sampling rate, for an audio column of float lists
  room          a template for the space (default "{dir}"); room_label, source, receiver, condition likewise (optional)
  src_pos, rcv_pos   columns holding [x, y, z], as a list or its text; a column holding one position per channel
                gives the channel's (per_channel) or else their mean (the centre of a device)
  per_channel   true: one IR per channel; templates may use {ch}
  layouts       {regex on the file path: {format, roles, sh_norm, orientation_known, preferred}}, the first that
                matches; roles "acn" names Ambisonic channels W, Y, Z, X, ACN4, ... Without one: mono_omni (omni) for
                one channel, array_raw (mic_0 ...) for more
  extra         columns kept as the dataset's own fields
  ir_kind       default room
Templates are Python format strings over the row's columns and {dir} (the file's first folder), {split} (the file
name up to its first "-", the HF split), {row} (in the file), {index} (in the split, over its files in name order),
{file_stem} (the name of an HF Audio file, without its extension) and {ch}. Every record keeps hf_split and
hf_index, which find the row through the `datasets` library.
"""

from __future__ import annotations

import functools
import io
import json
import re
from pathlib import Path
from typing import Iterator

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import soundfile as sf

from rirdb.adapters.base import IRRecord, slug

SUBTYPES = {pa.float32(): "FLOAT", pa.float64(): "DOUBLE"}


def acn_roles(n: int) -> tuple[str, ...]:
    return ("W", "Y", "Z", "X")[:n] + tuple(f"ACN{i}" for i in range(4, n))


def position(value, ch: int | None = None) -> tuple | None:
    """[x, y, z] from a list or its text; from a list of positions, the channel's, or their mean."""
    if value is None:
        return None
    v = np.asarray(json.loads(value) if isinstance(value, str) else value, dtype=float)
    if v.ndim == 2:
        v = v[ch] if ch is not None else v.mean(axis=0)
    return tuple(float(a) for a in v)


def shapes(pf: pq.ParquetFile, g: int, column: str, fs: int | None) -> list[tuple]:
    """(channels, samples, rate, subtype, name) of each IR in row group g."""
    col = pf.read_row_group(g, columns=[column]).column(0).combine_chunks()
    if pa.types.is_struct(col.type):                       # HF Audio: the bytes of an audio file
        out = []
        names = col.field("path").to_pylist() if col.type.get_field_index("path") >= 0 else [None] * len(col)
        for b, name in zip(col.field("bytes"), names):
            info = sf.info(io.BytesIO(b.as_py()))
            out.append((info.channels, info.frames, info.samplerate, info.subtype, name))
        return out
    n_ch = pc.list_value_length(col).to_numpy()            # a list of channels, each a list of samples
    lengths = pc.list_value_length(pc.list_flatten(col)).to_numpy()
    ends = np.cumsum(n_ch)
    subtype = SUBTYPES.get(col.type.value_type.value_type, "")
    out = []
    for c, end in zip(n_ch, ends):
        k = lengths[end - c:end]
        if k.min() != k.max():
            raise ValueError(f"row group {g}: a row whose channels differ in length")
        out.append((int(c), int(k[0]), int(fs), subtype, None))
    return out


@functools.lru_cache(maxsize=64)
def row_group_starts(path: str) -> np.ndarray:
    md = pq.ParquetFile(path).metadata
    return np.cumsum([0] + [md.row_group(g).num_rows for g in range(md.num_row_groups)])


@functools.lru_cache(maxsize=2)
def read_row(path: str, row: int, column: str):
    """One row's audio value, reading its row group from the start up to the row only."""
    starts = row_group_starts(path)
    g = int(np.searchsorted(starts, row, side="right")) - 1
    k = row - int(starts[g])
    pf = pq.ParquetFile(path, pre_buffer=False, buffer_size=1 << 20)
    for batch in pf.iter_batches(batch_size=4, row_groups=[g], columns=[column]):
        if k < batch.num_rows:
            return batch.column(0)[k]
        k -= batch.num_rows
    raise IndexError(f"{path} has no row {row}")


def decode(value, fs: int | None) -> tuple[np.ndarray, int]:
    """(channels, samples) as float64, and the rate."""
    if isinstance(value, pa.StructScalar):
        x, rate = sf.read(io.BytesIO(value["bytes"].as_py()), dtype="float64", always_2d=True)
        return x.T, int(rate)
    channels = value.values
    x = channels.flatten().to_numpy(zero_copy_only=False).astype(np.float64)
    return x.reshape(len(channels), -1), int(fs)


class HfParquetAdapter:
    NAME = "hf_parquet"

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        column = p.get("audio", "audio")
        layouts = [(re.compile(k), v) for k, v in (p.get("layouts") or {}).items()]
        index: dict[tuple, int] = {}                       # (dir, split) -> rows seen
        for f in sorted(root.glob(p["glob"])):
            rel = f.relative_to(root).as_posix()
            layout = next((v for rx, v in layouts if rx.search(rel)), {})
            here = {"dir": rel.split("/")[0], "split": f.name.split("-")[0]}
            pf = pq.ParquetFile(f)
            names = [n for n in pf.schema_arrow.names if n != column]
            row = 0
            for g in range(pf.metadata.num_row_groups):
                values = pf.read_row_group(g, columns=names).to_pylist()
                for v, shape in zip(values, shapes(pf, g, column, p.get("fs"))):
                    i = index.get((here["dir"], here["split"]), 0)
                    index[(here["dir"], here["split"])] = i + 1
                    yield from self._records(dataset, rel, {**v, **here, "row": row, "index": i}, shape, layout, p)
                    row += 1

    @staticmethod
    def _records(dataset, rel: str, ctx: dict, shape: tuple, layout: dict, p: dict) -> Iterator[IRRecord]:
        n_ch, n, fs, subtype, name = shape
        ctx["file_stem"] = Path(name).stem if name else ""      # an HF Audio file's name (CNAM: room, source, receiver)
        fill = lambda key: p[key].format_map(ctx) if p.get(key) else None  # noqa: E731
        for ch in (range(n_ch) if p.get("per_channel") else [None]):
            ctx["ch"] = ch
            k = n_ch if ch is None else 1
            roles = layout.get("roles")
            roles = (acn_roles(k) if roles == "acn" else tuple(roles[:k]) if roles
                     else ("omni",) if k == 1 else tuple(f"mic_{j}" for j in range(k)))
            extra = {"subtype": subtype, "hf_split": ctx["split"], "hf_index": ctx["index"],
                     **({"file_name": name} if name else {}),
                     **({"preferred": layout["preferred"]} if "preferred" in layout else {}),
                     **{c: ctx[c] for c in p.get("extra", [])}}
            yield IRRecord(
                dataset_id=dataset.id, local_key=f"{rel}#{ctx['row']}" + ("" if ch is None else f",{ch}"),
                room_key=slug(p.get("room", "{dir}").format_map(ctx)),
                capture_format=layout.get("format") or ("mono_omni" if k == 1 else "array_raw"),
                channel_roles=roles, fs=fs, n_samples=n,
                locator={"relpath": rel, "container": "parquet", "row": ctx["row"], **({} if ch is None else {"ch": ch})},
                condition_key=fill("condition"), src_key=fill("source"), rcv_key=fill("receiver"),
                src_pos=position(ctx.get(p.get("src_pos"))), rcv_pos=position(ctx.get(p.get("rcv_pos")), ch),
                sh_norm=layout.get("sh_norm"), orientation_known=layout.get("orientation_known", True),
                room_label=fill("room_label"), ir_kind=p.get("ir_kind", "room"), extra=extra)

    def load(self, locator: dict, root: Path) -> tuple[np.ndarray, int]:
        p = getattr(self, "_params", {}) or {}
        value = read_row(str(root / locator["relpath"]), int(locator["row"]), p.get("audio", "audio"))
        x, fs = decode(value, p.get("fs"))
        if "ch" in locator:
            x = x[locator["ch"]:locator["ch"] + 1]
        return x, fs
