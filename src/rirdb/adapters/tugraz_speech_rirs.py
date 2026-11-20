"""Adapter `tugraz_rir_bin` for tugraz_speech_rirs (TU Graz repository, DOI 10.3217/f6q9h-fc803): 16-channel
circular-array RIRs in the authors' own binary format.

data/<room>/index, data/<room>/data001 ... and data/<room>/meta.json, room classroom or corridor (Fragner et al.,
Data in Brief 2024; the authors' reader is fileio.py in the download). The index is b"IDX1", a uint32 entry count,
then 36-byte entries of nine uint32 words:

    time (ms, low word, high word), x (float32, mm), y (float32, mm), angle (float32, rad),
    id (mic_id << 16 | sample_id), data file number, byte offset, byte length

Each entry points at one array in data<NNN>: uint8 dtype character, uint8 ndim, ndim uint32 dimensions, then the
samples, here float32 of shape (16 channels, 240000 samples): 5 s at 48 kHz. One record per entry: the robot-moved
loudspeaker at (x, y) facing `angle` (sample_id 0..3 = 0, 90, 180, 270 degrees), the array of meta.json as the
receiver. The authors give 97 x 4 = 388 RIRs in the classroom and 210 x 4 = 840 in the corridor.
The source height is the paper's "about 121 cm"; the array centre (x, y, z), radius and rotation are in meta.json.

adapter.params (all optional):
  rooms    the folders under data/ (default [classroom, corridor])
  fs       the sampling rate (default 48000; the files do not hold it)
  src_z    the source height in metres (default 1.21)
  labels   {room: label}
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Iterator

import numpy as np

from rirdb.adapters.base import IRRecord, slug

ENTRY = 36          # bytes per index entry


def read_index(path: Path) -> list[tuple]:
    """The entries of an IDX1 index: (time ms, x m, y m, angle rad, mic_id, sample_id, file number, offset, length)."""
    raw = path.read_bytes()
    if raw[:4] != b"IDX1":
        raise ValueError(f"{path}: unknown index version {raw[:4]!r}")
    n = struct.unpack("<I", raw[4:8])[0]
    body = raw[8:]
    if len(body) != n * ENTRY:
        raise ValueError(f"{path}: {n} entries announced, {len(body) / ENTRY} found")
    w = np.frombuffer(body, dtype="<u4").reshape(n, 9)
    f = w.view("<f4")
    out = []
    for i in range(n):
        t = int(w[i, 0]) | (int(w[i, 1]) << 32)
        out.append((t, float(f[i, 2]) / 1000, float(f[i, 3]) / 1000, float(f[i, 4]),
                    int(w[i, 5]) >> 16, int(w[i, 5]) & 0xFFFF, int(w[i, 6]), int(w[i, 7]), int(w[i, 8])))
    return out


def read_header(fh, offset: int) -> tuple[np.dtype, tuple, int]:
    """(dtype, dimensions, header length in bytes) of the array that starts at `offset`."""
    fh.seek(offset)
    code, ndim = struct.unpack("<BB", fh.read(2))
    dims = struct.unpack(f"<{ndim}I", fh.read(4 * ndim))
    return np.dtype(chr(code)), dims, 2 + 4 * ndim


class TugrazSpeechRirAdapter:
    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        fs = int(p.get("fs", 48000))
        src_z = p.get("src_z", 1.21)
        labels = p.get("labels", {})
        for room in p.get("rooms", ["classroom", "corridor"]):
            folder = root / "data" / room
            meta = json.loads((folder / "meta.json").read_text())
            mics = {m["id"]: m for m in meta["microphones"]}
            handles: dict[int, object] = {}
            try:
                for (t, x, y, a, mic_id, sample_id, num, off, length) in read_index(folder / "index"):
                    rel = f"data/{room}/data{num:03d}"
                    if num not in handles:
                        handles[num] = open(root / rel, "rb")
                    dtype, dims, head = read_header(handles[num], off)
                    if len(dims) != 2 or head + int(np.prod(dims)) * dtype.itemsize != length:
                        raise ValueError(f"{rel}@{off}: dims {dims} do not fill {length} bytes")
                    n_ch, n = (dims if dims[0] < dims[1] else dims[::-1])
                    mic = mics[mic_id]
                    deg = int(round(np.degrees(a))) % 360
                    yield IRRecord(
                        dataset_id=dataset.id, local_key=f"{rel}#{mic_id},{x:.4f},{y:.4f},{deg}",
                        room_key=slug(room), capture_format="array_raw",
                        channel_roles=tuple(f"mic_{i}" for i in range(n_ch)), fs=fs, n_samples=int(n),
                        locator={"relpath": rel, "container": "tugraz_bin", "offset": off, "length": length},
                        src_key=f"{x:.2f}_{y:.2f}_{deg}", rcv_key=f"array{mic_id}",
                        src_pos=(x, y, src_z),
                        rcv_pos=(mic["position"]["x"], mic["position"]["y"], mic["position"].get("z")),
                        room_label=labels.get(room, room),
                        extra={"src_azimuth_deg": deg, "sample_id": sample_id, "time_ms": t,
                               "array_radius_m": mic.get("radius"), "array_rotation_rad": mic.get("rotation")})
            finally:
                for h in handles.values():
                    h.close()

    def load(self, locator: dict, root: Path) -> tuple[np.ndarray, int]:
        p = getattr(self, "_params", {}) or {}
        with open(root / locator["relpath"], "rb") as fh:
            dtype, dims, head = read_header(fh, locator["offset"])
            x = np.frombuffer(fh.read(locator["length"] - head), dtype=dtype).reshape(dims)
        if x.shape[0] > x.shape[1]:
            x = x.T
        return np.asarray(x, dtype=np.float64), int(p.get("fs", 48000))
