"""gwa (GWA, Tang et al., SIGGRAPH 2022): the authors' small subset, one mono WAV per source-receiver pair, positions
and reverberation times from stats.csv.

GWA_Dataset_small.zip unpacks to (the authors' download/README.md):
  GWA_Dataset_small/<scene>/L<source>_R<receiver>.wav   one IR, mono, 32-bit float, 48 kHz, 1.5 to 3.0 s; <scene> is
                                                        the house's 3D-FRONT scene uuid
  GWA_Dataset_small/stats.csv   one row per WAV: Path (relative to its folder), T60_125Hz .. T60_8000Hz (the authors'
                                reverberation times per octave band, python-acoustics), Source_Pos and Receiver_Pos
                                (numpy-printed "[x y z]", metres, the house model's frame, y up)
Each house was simulated whole (one house.obj per scene, rooms joined through open doors: tools/README.md and the
paper), on a 1 m grid of points over the house (tools/sample_src_lis.py), with no room named for any point. So the space
is the house, the source L<k> and the receiver R<nnnn> of that house.

One record per WAV, keyed by its relpath, as wav_tree keys a file; the stats.csv row gives the positions and the
seven T60s (extra t60_125hz_s .. t60_8000hz_s, as published: 33 of the 56,773 rows hold a failed fit, 0 s or over
10 s). A WAV without a row would keep empty positions.

Params (optional): csv (default "GWA_Dataset_small/stats.csv"); its Path column is relative to the CSV's folder.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterator

import soundfile as sf

from rirdb.adapters.base import IRRecord, slug
from rirdb.adapters.wav_tree import WavTreeAdapter

NAME_RX = re.compile(r"^(?P<house>[0-9a-f-]{36})/(?P<src>L\d+)_(?P<rcv>R\d+)\.wav$")
BANDS = (125, 250, 500, 1000, 2000, 4000, 8000)


def position(text: str) -> tuple[float, float, float]:
    """'[-0.8079  0.9619 -2.6828]' -> (-0.8079, 0.9619, -2.6828)."""
    x, y, z = (float(v) for v in text.strip().strip("[]").split())
    return x, y, z


class GwaAdapter(WavTreeAdapter):
    NAME = "gwa_stats"

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        table = root / (dataset.adapter.params or {}).get("csv", "GWA_Dataset_small/stats.csv")
        folder = table.parent
        with open(table, newline="") as fh:
            rows = {row["Path"]: row for row in csv.DictReader(fh)}
        for wav in sorted(folder.glob("*/*.wav")):
            name = wav.relative_to(folder).as_posix()
            m = NAME_RX.match(name)
            if m is None:
                continue
            rel = wav.relative_to(root).as_posix()
            info = sf.info(str(wav))
            row = rows.get(name)
            yield IRRecord(
                dataset_id=dataset.id, local_key=rel, room_key=slug(m["house"]),
                capture_format="mono_omni", channel_roles=("omni",),
                fs=int(info.samplerate), n_samples=int(info.frames),
                locator={"relpath": rel, "container": "audio"},
                src_key=m["src"], rcv_key=m["rcv"],
                src_pos=position(row["Source_Pos"]) if row else None,
                rcv_pos=position(row["Receiver_Pos"]) if row else None,
                room_label=f"3D-FRONT house {m['house']} (GWA simulation)",
                extra={"subtype": info.subtype,
                       **{f"t60_{b}hz_s": float(row[f"T60_{b}Hz"]) if row else None for b in BANDS}})

    # load(): WavTreeAdapter.load reads locator["relpath"]
