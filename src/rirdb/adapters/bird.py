"""bird (BIRD, Zenodo 4139416 v1.0): each FLAC is one simulated shoebox room, 4 sources x a 2-microphone pair.

fold<NN>/fold<NN>/fold<NN>.csv lists the fold's 10,000 rooms, one row each:
  id, Lx, Ly, Lz (room size, m), alpha (one absorption coefficient for every surface), c (speed of sound, m/s),
  m1x .. m2z (the two microphones), s1x .. s4z (the four sources)
and fold<NN>/fold<NN>/<id[0]>/<id[1]>/<id>.flac holds the room's 8 RIRs (16 kHz, 1 s), in the order of the authors'
code (bird.py, mix.py, tools/visualize.py): channel 2(i-1) is source i at microphone 1, channel 2(i-1)+1 is source i
at microphone 2.

One record per source: the two channels of that source (array_raw, mic_1 and mic_2), so a file gives 4 IRs, keyed
<file>#<channels> as wav_channels keys them. The space is the room id; the receiver is the microphone pair, placed
at its midpoint, with both microphone positions, the room size, alpha and c in extra.

Params (optional): csv (glob of the fold CSVs).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

import soundfile as sf

from rirdb.adapters.base import IRRecord, slug
from rirdb.adapters.wav_channels import WavChannelsAdapter


class BirdAdapter(WavChannelsAdapter):
    NAME = "bird_csv"

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        for table in sorted(root.glob(p.get("csv", "fold*/fold*/fold*.csv"))):
            folder = table.parent.relative_to(root).as_posix()
            with open(table, newline="") as fh:
                for row in csv.DictReader(fh):
                    key = row["id"]
                    rel = f"{folder}/{key[0]}/{key[1]}/{key}.flac"
                    info = sf.info(str(root / rel))
                    xyz = lambda name: tuple(float(row[f"{name}{a}"]) for a in "xyz")  # noqa: E731
                    m1, m2 = xyz("m1"), xyz("m2")
                    dims = [float(row[k]) for k in ("Lx", "Ly", "Lz")]
                    for i in range(4):
                        chans = [2 * i, 2 * i + 1]
                        yield IRRecord(
                            dataset_id=dataset.id, local_key=f"{rel}#{chans[0]},{chans[1]}", room_key=slug(key),
                            capture_format="array_raw", channel_roles=("mic_1", "mic_2"),
                            fs=int(info.samplerate), n_samples=int(info.frames),
                            locator={"relpath": rel, "container": "audio_channels", "channels": chans},
                            src_key=f"s{i + 1}", rcv_key="pair",
                            src_pos=xyz(f"s{i + 1}"), rcv_pos=tuple((a + b) / 2 for a, b in zip(m1, m2)),
                            room_label=f"Shoebox {dims[0]:g} x {dims[1]:g} x {dims[2]:g} m (simulated, {key})",
                            extra={"subtype": info.subtype, "fold": table.stem, "room_size_m": dims,
                                   "alpha": float(row["alpha"]), "c_m_s": float(row["c"]), "mic_1": m1, "mic_2": m2})

    # load(): WavChannelsAdapter.load reads the file and keeps the locator's channels
