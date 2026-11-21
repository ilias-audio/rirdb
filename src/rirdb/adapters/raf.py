"""raf (Real Acoustic Fields, Meta Reality Labs, CVPR 2024): one mono WAV per microphone, positions from metadata.

raf_emptyroom/EmptyRoom/ and raf_furnishedroom/FurnishedRoom/ (one room, measured empty and furnished) each hold
  data/<nnnnnn>/rir.wav      one microphone of the 36-microphone "Earful Tower" (omni), 32-bit float, 48 kHz, 1.5 s
  data/<nnnnnn>/rx_pos.txt   that microphone's position x,y,z
  data/<nnnnnn>/tx_pos.txt   the loudspeaker's orientation (quaternion x,y,z,w) and position x,y,z
  metadata/all_rx_pos.txt, all_tx_pos.txt   the same rows, row i for folder i (README), so the 173,232 small files
                             need not be opened; all_rx_pos.txt ends with one extra row nan,nan,nan
Metres, the authors' frame: X front, Y up, Z left, y = 0 the floor (README).

The folders come in blocks of 36: folders 36b .. 36b+35 share one tracked loudspeaker pose and hold the tower's 36
microphones at one tower placement, in the same order in every block (each index keeps its height within 1 cm over
all 2,406 blocks: mic00-03 at about 0.6 m, mic04-09 1.0 m, mic10-21 1.4 m, mic22-35 1.8 m; 1.1 m across).
One record per WAV (spaced microphones, one row each): the space is the dataset's one room, the condition empty or
furnished, the source the block's loudspeaker pose (p<b>), the receiver the tower microphone (mic<k>, k = folder
mod 36), both at their tracked positions; the loudspeaker quaternion is kept in extra.

Params (optional): conditions ({folder name: condition}, default EmptyRoom: empty, FurnishedRoom: furnished).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import soundfile as sf

from rirdb.adapters.base import IRRecord, slug
from rirdb.adapters.wav_tree import WavTreeAdapter

CONDITIONS = {"EmptyRoom": "empty", "FurnishedRoom": "furnished"}
MICS = 36


def numbers(path: Path) -> list[list[float]]:
    return [[float(v) for v in line.split(",")] for line in path.read_text().splitlines() if line.strip()]


class RafAdapter(WavTreeAdapter):
    NAME = "raf_tree"

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        conditions = (dataset.adapter.params or {}).get("conditions") or CONDITIONS
        for base in sorted(d for d in root.glob("raf_*/*") if d.name in conditions):
            rx, tx = numbers(base / "metadata/all_rx_pos.txt"), numbers(base / "metadata/all_tx_pos.txt")
            for folder in sorted((base / "data").iterdir()):
                i = int(folder.name)
                rel = f"{base.relative_to(root).as_posix()}/data/{folder.name}/rir.wav"
                info = sf.info(str(root / rel))
                yield IRRecord(
                    dataset_id=dataset.id, local_key=rel, room_key=slug(dataset.id),
                    capture_format="mono_omni", channel_roles=("omni",),
                    fs=int(info.samplerate), n_samples=int(info.frames),
                    locator={"relpath": rel, "container": "audio"},
                    condition_key=conditions[base.name],
                    src_key=f"p{i // MICS:04d}", rcv_key=f"mic{i % MICS:02d}",
                    src_pos=tuple(tx[i][4:7]), rcv_pos=tuple(rx[i]),
                    room_label="Real Acoustic Fields room (Meta Reality Labs)",
                    extra={"subtype": info.subtype, "src_quat_xyzw": tx[i][:4]})

    # load(): WavTreeAdapter.load reads locator["relpath"]
