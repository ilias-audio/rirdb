"""foa_meir (NTT, Zenodo 6088574): FOA-MEIR, first-order Ambisonic microphone IRs in over 100 places.

FOA_MEIR_Dataset/FOA_MEIR_Dataset/IRrecordings/<subset>/imp_d<cm>_azi<deg>_ele<deg>_pid<place>.bin, subset anechoic,
reverb-s, reverb-c, test, or echo/<anechoic|reverb-s|test>. Each file is raw float64, little-endian, interleaved by
sample: (48000 samples, 4 capsules), 1 s at 48 kHz (the authors' utils.fetch_imp: struct 'd', reshape(-1, 4).T).
The capsules are the A-format of a Sennheiser AMBEO VR Mic, in the order of the authors' utils.convertAB
(W = a0+a1+a2+a3, Y = a0-a1+a2-a3, Z = a0-a1-a2+a3, X = a0+a1-a2-a3): FLU, FRD, BLD, BRU.
The name gives the loudspeaker's distance, azimuth (anticlockwise from the front) and elevation from the
microphone, which is the origin of the positions; the place id (placeinfo.csv) names the microphone's place.
One record per file. The echo/anechoic and echo/test files are byte copies of anechoic/ and test/ files and are
not read (`exclude`).

Params:
  glob     the IR files (default "**/IRrecordings/**/imp_*.bin")
  exclude  regex on the relative path of files not read (default: the echo copies)
  rooms    {room label: [place ids]}: the room of each place (several places, i.e. microphone positions, per room)
  anechoic the room labels whose IRs are anechoic (default ["Anechoic"])
  fs       the sampling rate (default 48000; the files do not hold it)
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Iterator

import numpy as np

from rirdb.adapters.base import IRRecord, slug

NAME_RE = re.compile(r"^imp_d(?P<d>\d+)_azi(?P<az>-?\d+)_ele(?P<el>-?\d+)_pid(?P<pid>\d+)\.bin$")
ROLES = ("FLU", "FRD", "BLD", "BRU")
N_CH = 4


class FoaMeirBinAdapter:
    NAME = "foa_meir_bin"

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        fs = int(p.get("fs", 48000))
        exclude = re.compile(p.get("exclude", r"/echo/(anechoic|test)/"))
        room_of = {int(pid): label for label, pids in p["rooms"].items() for pid in pids}
        anechoic = set(p.get("anechoic", ["Anechoic"]))
        for f in sorted(root.glob(p.get("glob", "**/IRrecordings/**/imp_*.bin"))):
            rel = f.relative_to(root).as_posix()
            m = NAME_RE.match(f.name)
            if not m or exclude.search(rel):
                continue
            pid = int(m["pid"])
            label = room_of[pid]                       # KeyError: a place id missing from params.rooms
            d, az, el = int(m["d"]) / 100, math.radians(int(m["az"])), math.radians(int(m["el"]))
            src = (d * math.cos(el) * math.cos(az), d * math.cos(el) * math.sin(az), d * math.sin(el))
            subset = rel.split("IRrecordings/", 1)[1].rsplit("/", 1)[0]
            yield IRRecord(
                dataset_id=dataset.id, local_key=rel, room_key=slug(label), room_label=label,
                capture_format="tetra_raw", channel_roles=ROLES, fs=fs,
                n_samples=f.stat().st_size // (8 * N_CH),
                locator={"relpath": rel, "container": "f64le"},
                src_key=f"d{m['d']}_azi{m['az']}_ele{m['el']}", rcv_key=f"pid{pid:04d}",
                src_pos=tuple(round(v, 4) for v in src), rcv_pos=(0.0, 0.0, 0.0),
                orientation_known=False, category_hint=subset,
                ir_kind="anechoic" if label in anechoic else "room",
                extra={"subset": subset, "place_id": pid, "distance_m": d,
                       "azimuth_deg": int(m["az"]), "elevation_deg": int(m["el"])})

    def load(self, locator: dict, root: Path) -> tuple[np.ndarray, int]:
        p = getattr(self, "_params", {}) or {}
        x = np.fromfile(root / locator["relpath"], dtype="<f8").reshape(-1, N_CH)
        return np.ascontiguousarray(x.T), int(p.get("fs", 48000))
