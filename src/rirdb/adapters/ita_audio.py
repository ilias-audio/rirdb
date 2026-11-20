"""ITA-Toolbox itaAudio objects saved in MATLAB v7.3 files (written for sweet_area, the Sweet Area IR database).

The ITA-Toolbox (RWTH Aachen) stores audio as itaAudio class objects (MCOS). In a v7.3 (HDF5) file their saved
form is a struct in #refs#, reached through the cells of #subsystem#/MCOS. Each struct has `data` (channels x
samples or bins), `domain` (time | freq), `samplingRate`, `dataFactor` (a scale on `data`) and `comment`, which
in sweet_area reads "Impulse Response: <loudspeaker> -> <channels>" and so names the loudspeaker. A
frequency-domain struct becomes time samples through an inverse real FFT of 2 x (bins - 1) points, ITA's
convention for energy signals.

sweet_area: each file is one position of the robot arm (circ_<radius>_<azimuth>_... or cart_<x>_<y>_...) and
holds result.<field>.ir_1 ... ir_42, one itaAudio per loudspeaker. One record per file and loudspeaker. The
receiver position is the head tracker's x y z (result.headtrackingMessage); the source position is
loudspeaker.ls_<k>.Pos in the speakers file (one frame for both, metres).

Params:
  groups             a list of {glob, field (the struct under result holding ir_1 ...), capture_format, roles,
                     sh_norm, orientation_known, device}
  room_default       the space (default: the dataset id)
  speakers           the MATLAB v5 file of loudspeaker positions (default speakerPositions.mat)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import numpy as np

from rirdb.adapters.base import IRRecord, slug

COMMENT = re.compile(r"Impulse Response:\s*(\d+)\s*->")
# the position part of a file name such as circ_<radius>_<azimuth>_<...>$<...>_KU100.mat (as the review trialled it)
POSITION_NAME = re.compile(r"^(?P<pos>(?:circ|cart)_[-0-9p]+_[-0-9p]+)_(?P<rest>[^$]*)\$(?P<tail>[^_]*)_")


def _text(ds) -> str:
    """A MATLAB char array stored in HDF5 (one uint16 per character) as text."""
    return "".join(chr(c) for c in np.ravel(ds[()]))


def _structs(h) -> dict[int, str]:
    """Loudspeaker number -> HDF5 path of its itaAudio struct, from the comments of the MCOS objects."""
    import h5py

    out = {}
    for ref in h["#subsystem#/MCOS"][()].ravel():
        o = h[ref]
        if isinstance(o, h5py.Group) and "data" in o and "comment" in o:
            m = COMMENT.match(_text(o["comment"]))
            if m:
                out[int(m[1])] = o.name
    return out


def _speakers(path: Path) -> dict[int, tuple]:
    """Loudspeaker number -> x y z, from loudspeaker.ls_<k>.Pos; empty when the file is missing."""
    import scipy.io

    if not path.exists():
        return {}
    ls = scipy.io.loadmat(str(path), squeeze_me=True, struct_as_record=False)["loudspeaker"]
    return {int(k.split("_")[1]): tuple(float(v) for v in np.ravel(getattr(ls, k).Pos))
            for k in ls._fieldnames if k.startswith("ls_")}


def _head_tracker(result) -> list[float]:
    """The numbers of result.headtrackingMessage ("...: x y z yaw pitch roll"), or none."""
    text = _text(result["headtrackingMessage"]) if "headtrackingMessage" in result else ""
    return [float(v) for v in text.split(":", 1)[1].split()] if ":" in text else []


class ItaAudioMatAdapter:
    def _structs_of(self, h, g: dict, rel: str) -> dict[int, tuple]:
        """Loudspeaker -> (struct path, channels, samples, fs), checked against the fields result.<field>.ir_k."""
        fields = [k for k in h["result"][g["field"]].keys() if k.startswith("ir_")]
        structs = _structs(h)
        if sorted(structs) != sorted(int(k[3:]) for k in fields):
            raise RuntimeError(f"{rel}: result.{g['field']} has {len(fields)} IRs, the MCOS structs name "
                               f"loudspeakers {sorted(structs)}")
        info = {}
        for k, path in structs.items():
            s = h[path]
            n_ch, n = s["data"].shape
            if _text(s["domain"]) == "freq":
                n = 2 * (n - 1)
            info[k] = (path, int(n_ch), int(n), int(np.ravel(s["samplingRate"][()])[0]))
        return info

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        import h5py

        p = dataset.adapter.params
        room = p.get("room_default", dataset.id)
        speakers = _speakers(root / p.get("speakers", "speakerPositions.mat"))
        for gi, g in enumerate(p["groups"]):
            for f in sorted(root.glob(g["glob"])):
                rel = f.relative_to(root).as_posix()
                m = POSITION_NAME.match(f.name)
                with h5py.File(f, "r") as h:
                    info = self._structs_of(h, g, rel)
                    tracker = _head_tracker(h["result"])
                for k in sorted(info):
                    path, n_ch, n, fs = info[k]
                    roles = g.get("roles") or [f"mic_{i}" for i in range(n_ch)]
                    if len(roles) != n_ch:
                        raise ValueError(f"{rel}: loudspeaker {k} has {n_ch} channels, the roles name {len(roles)}")
                    yield IRRecord(
                        dataset_id=dataset.id, local_key=f"{rel}#ls{k}", room_key=slug(room),
                        capture_format=g["capture_format"], channel_roles=tuple(roles), fs=fs, n_samples=n,
                        locator={"relpath": rel, "container": "ita_mat", "group": gi, "struct": path, "ls": k},
                        src_key=f"ls{k:02d}", rcv_key=m["pos"] if m else f.stem, src_pos=speakers.get(k),
                        rcv_pos=tuple(tracker[:3]) if len(tracker) >= 3 else None,
                        sh_norm=g.get("sh_norm"), orientation_known=bool(g.get("orientation_known", True)),
                        room_label=str(room).replace("-", " "),
                        extra={"device": g.get("device"), "tracker_yaw_pitch_roll": tracker[3:6] or None})

    def load(self, locator: dict, root: Path):
        import h5py

        with h5py.File(root / locator["relpath"], "r") as h:
            s = h[locator["struct"]]
            m = COMMENT.match(_text(s["comment"]))
            if not m or int(m[1]) != locator["ls"]:
                raise RuntimeError(f"{locator}: the struct is not loudspeaker {locator['ls']}")
            d = s["data"][()]
            fs = int(np.ravel(s["samplingRate"][()])[0])
            freq = _text(s["domain"]) == "freq"
            factor = float(np.ravel(s["dataFactor"][()])[0]) if "dataFactor" in s else 1.0
        if d.dtype.names:                                  # MATLAB complex: compound (real, imag)
            d = d["real"] + 1j * d["imag"]
        d = np.asarray(d) * factor
        if freq:
            d = np.fft.irfft(d, n=2 * (d.shape[1] - 1), axis=1)
        return np.asarray(d, dtype=np.float64), fs
