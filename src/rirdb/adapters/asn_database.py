"""Adapter `asn_json` for asn_database (ASN Database v3.2, Zenodo 8320987): CATT-Acoustic RIRs of a 5-room
holiday-apartment model.

The download holds the same RIRs three times (ASN_Database_3.2_WAV, _BIN, _MAT: 32-bit float WAV, raw float32 and
MATLAB; the WAV and BIN samples are identical). This reader reads the WAV copy only.

ASN_Database_3.2_WAV/OUT/RIR.json lists, per room (the room the node is in: living_room, bedroom, toilet, hall,
bathroom), every source-node pair:
    {"pair_4444": {"node_id": 22, "src_id": "A0", "node_position": [x, y, z], "node_orientation": rad,
                   "source_position": [x, y, z], "microphone_positions": [[x0..x3], [y0..y3], [z0..z3]],
                   "rir_paths": {"mic_0": "RIR_1_A0_88_OMNI.WAV", ..., "mic_3": "RIR_1_A0_91_OMNI.WAV"}}}
A node is 4 omni microphones in a square of 5 cm side (3.5 cm from the node centre): one device at one position,
so the 4 files of a pair are one 4-channel IR (array_raw, roles mic_0..mic_3), as data-model.md section 3 asks.
202 sources x 98 nodes = 19,796 IRs from 79,184 files. The file name's first number is the CATT run (1-4) and the
third the receiver inside that run, not the node; RIR.json is the only place that joins them.
The 4 files of a pair differ by a few samples in length: the IR is cut to the shortest.

adapter.params (optional):
  json   the list of pairs (default "ASN_Database_3.2_WAV/OUT/RIR.json"); the WAV paths are relative to its folder
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from rirdb.adapters.base import IRRecord, slug

ROLES = ("mic_0", "mic_1", "mic_2", "mic_3")


class AsnJsonAdapter:
    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        json_path = root / p.get("json", "ASN_Database_3.2_WAV/OUT/RIR.json")
        folder = json_path.parent.relative_to(root).as_posix()
        for room, pairs in json.loads(json_path.read_text()).items():
            for pair_id, v in pairs.items():
                rels = [f"{folder}/{v['rir_paths'][role]}" for role in ROLES]
                infos = [sf.info(str(root / rel)) for rel in rels]
                mics = np.asarray(v["microphone_positions"], dtype=float).T.round(4).tolist()   # 4 x (x, y, z)
                yield IRRecord(
                    dataset_id=dataset.id, local_key="|".join(rels), room_key=slug(room),
                    capture_format="array_raw", channel_roles=ROLES, fs=int(infos[0].samplerate),
                    n_samples=min(int(i.frames) for i in infos),
                    locator={"files": rels, "container": "audio_pair"},
                    src_key=str(v["src_id"]), rcv_key=f"node{int(v['node_id']):02d}",
                    src_pos=tuple(float(c) for c in v["source_position"]),
                    rcv_pos=tuple(float(c) for c in v["node_position"]),
                    room_label=room.replace("_", " "),
                    extra={"pair": pair_id, "node_orientation_rad": float(v["node_orientation"]),
                           "mic_pos": mics, "subtype": infos[0].subtype})

    def load(self, locator: dict, root: Path) -> tuple[np.ndarray, int]:
        chans = [sf.read(str(root / f), dtype="float64", always_2d=True) for f in locator["files"]]
        n = min(c[0].shape[0] for c in chans)
        return np.vstack([c[0][:n].T for c in chans]), int(chans[0][1])
