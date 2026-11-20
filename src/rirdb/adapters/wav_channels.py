"""Adapter `wav_channels`: WAV files whose channels are separate receivers, one IR record per group of channels.

Written for munich_underground: its omni files hold one microphone per channel, each at its own receiver position
(IR_OMNI_aux_*: R2-R5; IR_OMNI_*: R1-R5), so one file is four or five mono IRs, while its artificial-head files
hold one 6-channel IR.

A file read as one record of all its channels, in order, holds one IR: its key is the file alone (an empty item,
data-model.md section 4). A file split into several records gets the item "<channels>", e.g. "a.wav#2".

adapter.params:
  glob           files to consider (default "**/*.wav")
  exclude        regex on the relative path (default: macOS junk)
  room_default   the space (default: the dataset id)
  files          rules, tried in order; the first whose `pattern` matches a file's relative path reads it:
                   pattern   regex; named groups src, src_2 ... (joined with "_") and cond become the keys
                   records   one record per entry: {channels: [i, ...] (0-based, in the record's order), rcv: key,
                             roles: [...], capture_format: ..., reference_role: ...}
                   ir_kind   (default "room")
  src_positions  {src key: [x, y, z]} in metres (optional)
  rcv_positions  {rcv key: [x, y, z]} in metres (optional)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from rirdb.adapters.base import IRRecord, slug
from rirdb.adapters.wav_tree import DEFAULT_EXCLUDE, field


def file_and_item(rel: str, channels: list[int], n_records: int, n_channels: int) -> tuple[str, dict]:
    """The key and locator of one record: the file alone when the record is the whole file, else file#channels."""
    if n_records == 1 and channels == list(range(n_channels)):
        return rel, {"relpath": rel, "container": "audio"}
    key = f"{rel}#" + ",".join(map(str, channels))
    return key, {"relpath": rel, "container": "audio_channels", "channels": channels}


class WavChannelsAdapter:
    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        exclude = re.compile(p.get("exclude", DEFAULT_EXCLUDE))
        rules = [(re.compile(rule["pattern"]), rule) for rule in p["files"]]
        room = p.get("room_default") or dataset.id
        src_pos, rcv_pos = p.get("src_positions") or {}, p.get("rcv_positions") or {}
        for f in sorted(root.glob(p.get("glob", "**/*.wav"))):
            rel = f.relative_to(root).as_posix()
            if exclude.search(rel):
                continue
            hit = next(((m, rule) for rx, rule in rules if (m := rx.search(rel))), None)
            if hit is None:
                continue
            m, rule = hit
            info = sf.info(str(f))
            src = field(m.groupdict(), "src")
            for rec in rule["records"]:
                chans = [int(c) for c in rec["channels"]]
                if max(chans) >= info.channels:
                    raise ValueError(f"{rel}: {info.channels} channels, a rule asks for channel {max(chans)}")
                roles = rec.get("roles") or (["omni"] if len(chans) == 1 else [f"ch{c}" for c in chans])
                if len(roles) != len(chans):
                    raise ValueError(f"{rel}: {len(chans)} channels but {len(roles)} roles")
                key, locator = file_and_item(rel, chans, len(rule["records"]), info.channels)
                rcv = rec.get("rcv")
                yield IRRecord(
                    dataset_id=dataset.id, local_key=key, room_key=slug(room),
                    capture_format=rec.get("capture_format") or ("mono_omni" if len(chans) == 1 else "array_raw"),
                    channel_roles=tuple(roles), fs=int(info.samplerate), n_samples=int(info.frames),
                    locator=locator, condition_key=m.groupdict().get("cond"), src_key=src, rcv_key=rcv,
                    src_pos=tuple(src_pos[src]) if src in src_pos else None,
                    rcv_pos=tuple(rcv_pos[rcv]) if rcv in rcv_pos else None,
                    room_label=str(room).replace("_", " "), ir_kind=rule.get("ir_kind", "room"),
                    extra={"subtype": info.subtype, "reference_role": rec.get("reference_role")})

    def load(self, locator: dict, root: Path) -> tuple[np.ndarray, int]:
        x, fs = sf.read(str(root / locator["relpath"]), dtype="float64", always_2d=True)
        if "channels" in locator:
            return x.T[list(locator["channels"])], int(fs)
        return x.T, int(fs)
