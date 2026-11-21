"""wav_tree, plus files whose channels hold more than one device: one record per device.

REVERBDATA (reverbdata) publishes each stereo and binaural measurement as one 4-channel WAV: channels 1-2 are the
A-B stereo pair, channels 3-4 the dummy head (README.txt of each space). data-model.md section 3 makes one row per
device, so the file gives two records. They are keyed <file>#<channels> as wav_channels keys them, and each loads
only its own channels. In the ESML Great Hall and Small Hall channels 1 and 2 hold the same samples (one microphone
written twice), so there the first record is channel 1 alone, read as mono.

Adapter `wav_tree_split`: every parameter of wav_tree, plus
  split: {regex: [{channels: [i, ...], format: capture_format, roles: [...], preferred: bool}, ...]} on the
         relpath. The first regex that matches splits the file into one record per entry (channels 0-based, in
         the record's order). An entry without `format` or `roles` keeps the file's; one without `preferred`
         keeps the file's (preferred_regex). A file that no regex matches is read whole, as wav_tree reads it.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Iterator

import numpy as np

from rirdb.adapters.base import IRRecord
from rirdb.adapters.wav_tree import WavTreeAdapter


class WavTreeSplitAdapter(WavTreeAdapter):
    NAME = "wav_tree_split"

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        rules = [(re.compile(rx), parts) for rx, parts in (dataset.adapter.params.get("split") or {}).items()]
        for r in super().iter_records(dataset, root):
            rel = r.locator.get("relpath")
            parts = next((parts for rx, parts in rules if rel and rx.search(rel)), None)
            if parts is None:
                yield r
                continue
            for part in parts:
                chans = [int(c) for c in part["channels"]]
                if max(chans) >= len(r.channel_roles):
                    raise ValueError(f"{rel}: {len(r.channel_roles)} channels, a split asks for channel {max(chans)}")
                roles = tuple(part.get("roles") or [r.channel_roles[c] for c in chans])
                if len(roles) != len(chans):
                    raise ValueError(f"{rel}: {len(chans)} channels but {len(roles)} roles")
                fmt = part.get("format", r.capture_format)
                yield replace(
                    r, local_key=f"{rel}#" + ",".join(map(str, chans)), capture_format=fmt, channel_roles=roles,
                    locator={**r.locator, "container": "audio_channels", "channels": chans},
                    sh_norm=r.sh_norm if fmt.startswith(("foa", "hoa")) else None,
                    extra={**r.extra, "preferred": bool(part.get("preferred", r.extra.get("preferred", True)))})

    def load(self, locator: dict, root: Path) -> tuple[np.ndarray, int]:
        x, fs = super().load(locator, root)
        if "channels" in locator:                   # one device of a file that holds several
            x = x[list(locator["channels"])]
        return x, fs
