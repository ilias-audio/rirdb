"""Adapter `sms_wsj_json` for sms_wsj (SMS-WSJ RIRs, Zenodo 3517889): one simulated shoebox room per example.

The download holds scenarios.json and <split>/<example_id>/h_<k>.wav for the splits train_si284 (33,561 examples),
cv_dev93 (982) and test_eval92 (1,332). The authors' sms_wsj/database/create_rirs.py drew each example's room,
decay time, array and four source positions (scenarios.json), then wrote one file per source k: the RIRs from
source k to the 6 microphones, 6 channels in the order of `sensor_position`'s columns (8 kHz, 8192 samples,
64-bit float, not normalised). scenarios.json, per example:
    room_dimensions [[x], [y], [z]], sound_decay_time (the T60 given to the generator, s),
    source_position [[x0..x3], [y0..y3], [z0..z3]], sensor_position [[x0..x5], [y0..y5], [z0..z5]], example_id

One record per file: the 6-microphone circular array (radius 10 cm) is one device at one position, so h_k.wav is
one 6-channel IR (array_raw, mic_0..mic_5) from source s<k>. The space is the example (<split>-<example_id>); the
receiver is the array, placed at the mean of its 6 microphones, whose positions go in extra with the room size and
the decay time.

adapter.params (optional):
  json   the scenario file (default "scenarios.json"); the WAV paths are relative to its folder
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from rirdb.adapters.base import IRRecord, slug
from rirdb.adapters.wav_channels import WavChannelsAdapter


class SmsWsjAdapter(WavChannelsAdapter):
    NAME = "sms_wsj_json"

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        json_path = root / dataset.adapter.params.get("json", "scenarios.json")
        folder = json_path.parent.relative_to(root)
        for split, examples in json.loads(json_path.read_text())["datasets"].items():
            for example_id, ex in examples.items():
                dims = [float(v[0]) for v in ex["room_dimensions"]]
                sources = np.asarray(ex["source_position"], dtype=float).T     # n_sources x 3
                mics = np.asarray(ex["sensor_position"], dtype=float).T        # 6 x 3
                for k, src_pos in enumerate(sources):
                    rel = (folder / split / example_id / f"h_{k}.wav").as_posix()
                    info = sf.info(str(root / rel))
                    yield IRRecord(
                        dataset_id=dataset.id, local_key=rel, room_key=slug(f"{split}-{example_id}"),
                        capture_format="array_raw", channel_roles=tuple(f"mic_{m}" for m in range(info.channels)),
                        fs=int(info.samplerate), n_samples=int(info.frames),
                        locator={"relpath": rel, "container": "audio"},
                        src_key=f"s{k}", rcv_key="circular6",
                        src_pos=tuple(src_pos), rcv_pos=tuple(mics.mean(axis=0).round(4)),
                        room_label=f"Shoebox {dims[0]:g} x {dims[1]:g} x {dims[2]:g} m, T60 "
                                   f"{ex['sound_decay_time']:g} s (simulated, {split}/{example_id})",
                        extra={"subtype": info.subtype, "split": split, "example_id": example_id,
                               "room_size_m": dims, "t60_s": float(ex["sound_decay_time"]),
                               "mic_positions": mics.tolist()})

    # load(): WavChannelsAdapter.load reads the whole file (no channels in the locator)
