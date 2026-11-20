"""Adapter `kinect_rir_txt` for kinect_rir (Inria Nancy, Zenodo 3520737): simulated 4-channel RIRs of a Kinect-like
linear array.

kinect_rir/<split>/<nnn>/rir/<name>.rir.gz, split tr (50 folders), cv (13) or tt (8). Each file is gzipped text:
one row per sample, 4 tab-separated columns (the 4 microphones), 16 kHz (the companion code's SAMPLING_RATE,
github.com/sunits/Reverberated_WSJ_2MIX). The file name holds every parameter of the simulation:

    <rt60>__<Lx>_<Ly>_<Lz>__<mic0 xyz>__<mic1 xyz>__<mic2 xyz>__<mic3 xyz>__<source xyz>.rir.gz

The generator makes each file floor(rt60 * 16000) samples long, so the length is read from the name, not by
decompressing 84,000 files. Every room (one RT60, one size, one array position) has 3 sources (list.yaml), so a
room is the name up to the source: the room key is <split><nnn>-<rt60>-<Lx>x<Ly>x<Lz>.
One record per file, the 4 microphones as its channels (array_raw); the receiver position is the array centre (the
mean of the 4 microphones), the 4 microphone positions are in extra. One uncompressed duplicate
(tr/000/rir/0.3357__...__1.107_1.716_0.731.rir, byte-identical to its .rir.gz) is not read.

adapter.params (optional):
  glob   the files (default "kinect_rir/*/*/rir/*.rir.gz")
  fs     the sampling rate (default 16000)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import numpy as np

from rirdb.adapters.base import IRRecord, slug

NUM = r"-?\d+(?:\.\d+)?"
XYZ = rf"({NUM})_({NUM})_({NUM})"
NAME_RE = re.compile(rf"^(?P<rt60>{NUM})__{XYZ}__{XYZ}__{XYZ}__{XYZ}__{XYZ}__{XYZ}\.rir\.gz$")
ROLES = ("mic_0", "mic_1", "mic_2", "mic_3")


class KinectRirAdapter:
    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        fs = int(p.get("fs", 16000))
        for f in sorted(root.glob(p.get("glob", "kinect_rir/*/*/rir/*.rir.gz"))):
            m = NAME_RE.match(f.name)
            if not m:
                continue
            rel = f.relative_to(root).as_posix()
            split, folder = f.parts[-4], f.parts[-3]
            g = [float(x) for x in m.groups()[1:]]
            rt60 = m["rt60"]
            dims = g[0:3]
            mics = [tuple(g[3 + 3 * i: 6 + 3 * i]) for i in range(4)]
            src = tuple(g[15:18])
            centre = tuple(float(np.mean([mm[k] for mm in mics])) for k in range(3))
            dims_txt = "x".join(f"{d:g}" for d in dims)
            yield IRRecord(
                dataset_id=dataset.id, local_key=rel,
                room_key=slug(f"{split}{folder}-{rt60}-{dims_txt}"),
                capture_format="array_raw", channel_roles=ROLES, fs=fs,
                n_samples=int(float(rt60) * fs),
                locator={"relpath": rel, "container": "txt.gz"},
                src_key="_".join(f"{v:g}" for v in src), rcv_key="kinect",
                src_pos=src, rcv_pos=centre,
                room_label=f"simulated room {dims_txt.replace('x', ' x ')} m, RT60 {float(rt60):g} s ({split}/{folder})",
                ir_kind="virtual",
                extra={"split": split, "rt60_s": float(rt60), "dims_m": dims, "mic_pos": mics})

    def load(self, locator: dict, root: Path) -> tuple[np.ndarray, int]:
        p = getattr(self, "_params", {}) or {}
        x = np.loadtxt(root / locator["relpath"], dtype=np.float64, ndmin=2)
        return np.ascontiguousarray(x.T), int(p.get("fs", 16000))
