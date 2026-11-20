"""Adapter `rir_mega_meta` for rir_mega (RIR-Mega v1.2.1): the 8-channel WAVs that each part's metadata.csv lists,
nothing else.

Two parts, each <part>/<part>/{train,val,test}/wavs/rir_NNNNNN.wav with <part>/<part>/metadata.csv:
  rir_output_50k      50,000 rows, 8-microphone linear array ("linear8", 5 cm spacing)
  rir_output_8k_circ   8,000 rows, 8-microphone circular array ("circular8", 10 cm radius)
The 50k folders also hold 7,731 WAVs that metadata.csv does not list under that path: every one is a byte-for-byte
copy of a listed file (the same id under another split folder), so only the listed path is read.

Every row is its own simulated shoebox room (pyroomacoustics image method), drawn from `seed_room`. The circular
part reuses seeds 1000-8999: its 8,000 rows have the same room, absorption, source and array centre as linear
rir_000958 .. rir_008957, so the space is keyed by the seed and those rooms hold one IR per array.

Per record: space room-<seed_room>; source s1 at `source` and receiver <array preset> at `microphone` (the array
centre), room coordinates in metres; channels mic_0 .. mic_7 in the order of the array's `mics`; the room
family (classroom, corridor, living_room, office_small, warehouse) is the label and the category hint.

adapter.params (optional):
  parts   {folder: array preset} (default: the two parts above)
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from rirdb.adapters.base import IRRecord

PARTS = {"rir_output_50k/rir_output_50k": "linear8", "rir_output_8k_circ/rir_output_8k_circ": "circular8"}


class RirMegaAdapter:
    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        for part, preset in (p.get("parts") or PARTS).items():
            with open(root / part / "metadata.csv", newline="") as fh:
                for row in csv.DictReader(fh):
                    rel = f"{part}/{row['wav']}"
                    info = sf.info(str(root / rel))
                    seed = int(row["seed_room"])
                    yield IRRecord(
                        dataset_id=dataset.id, local_key=rel, room_key=f"room-{seed}",
                        capture_format="array_raw", channel_roles=tuple(f"mic_{i}" for i in range(info.channels)),
                        fs=int(info.samplerate), n_samples=int(info.frames),
                        locator={"relpath": rel, "container": "audio"},
                        src_key="s1", rcv_key=preset,
                        src_pos=tuple(float(v) for v in json.loads(row["source"])),
                        rcv_pos=tuple(float(v) for v in json.loads(row["microphone"])),
                        room_label=f"{row['family'].replace('_', ' ')} (simulated, seed {seed})",
                        category_hint=row["family"],
                        extra={"subtype": info.subtype, "split": row["split"], "rir_id": row["id"],
                               "room_size_m": json.loads(row["room_size"]), "array": preset})

    def load(self, locator: dict, root: Path) -> tuple[np.ndarray, int]:
        x, fs = sf.read(str(root / locator["relpath"]), dtype="float64", always_2d=True)
        return x.T, int(fs)
