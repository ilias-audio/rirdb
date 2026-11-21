"""acousticrooms (AcousticRooms, Meta, CVPR 2025): mono RIRs read inside the 10 per-category zips, positions from JSON.

single_channel_ir.zip unpacks to single_channel_ir/single_channel_ir_1/<Category>.zip: 10 zips (12.6 GB of WAVs,
4.1 GB packed), which are not unpacked further: each IR is read from its zip member, which saves 302,671 files on
scratch. Members are <Category>/<Category>_idx_<n>/S<src>_R<rcv>_hybrid_IR.wav (16-bit mono, 22,050 Hz).
metadata.zip unpacks to metadata/metadata/<Category>/<Category>_idx_<n>/S<src>_R<rcv>.json:
{sim_src_id, sim_rec_id, src_loc [x, y, z], rec_loc [x, y, z], IR_norm} in 129 rooms, {src_loc, rec_loc} only in
the other 131, whose WAV and JSON names pad the numbers differently (WAV S001_R010, JSON S001_R0010). So a WAV is
matched to its JSON by the integer source and receiver numbers. Every WAV has its JSON; 254 JSONs have no WAV.

Per record: the space is the room folder (apartments-idx-0, ...), source S<n> at src_loc, receiver R<n> at rec_loc
(the room model's coordinates, metres); IR_norm, where given, is kept in extra.

Params (optional): zips (glob of the category zips), metadata (folder of the JSONs).
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import soundfile as sf

from rirdb.adapters.base import IRRecord, slug

MEMBER_RE = re.compile(r"^(?P<cat>[A-Za-z]+)/(?P<room>(?P=cat)_idx_(?P<idx>\d+))/S(?P<s>\d+)_R(?P<r>\d+)_hybrid_IR\.wav$")
JSON_RE = re.compile(r"^S(\d+)_R(\d+)\.json$")
LABELS = {"Apartments": "Apartment", "Auditorium": "Auditorium", "Bathrooms": "Bathroom", "Bedrooms": "Bedroom",
          "Cafe": "Cafe", "ListeningRoom": "Listening room", "LivingRoomsWithHallway": "Living room with hallway",
          "MeetingRoom": "Meeting room", "Office": "Office", "Restaurants": "Restaurant"}


@lru_cache(maxsize=16)
def _zip(path: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(path)


@lru_cache(maxsize=2)
def _json_names(folder: str) -> dict:
    """(source, receiver) numbers -> JSON file name, for one room's metadata folder."""
    names = os.listdir(folder) if os.path.isdir(folder) else []
    return {(int(m[1]), int(m[2])): n for n in names if (m := JSON_RE.match(n))}


class AcousticRoomsAdapter:
    NAME = "acousticrooms_zip"

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        meta_root = root / p.get("metadata", "metadata/metadata")
        for z in sorted(root.glob(p.get("zips", "single_channel_ir/single_channel_ir_1/*.zip"))):
            rel = z.relative_to(root).as_posix()
            zf = _zip(str(z))
            for member in sorted(zf.namelist()):
                m = MEMBER_RE.match(member)
                if not m:
                    continue
                s, r = int(m["s"]), int(m["r"])
                folder = meta_root / m["cat"] / m["room"]
                meta = json.loads((folder / _json_names(str(folder))[(s, r)]).read_text())
                with zf.open(member) as fh:
                    info = sf.info(fh)
                yield IRRecord(
                    dataset_id=dataset.id, local_key=f"{rel}#{member}", room_key=slug(m["room"]),
                    capture_format="mono_omni", channel_roles=("omni",),
                    fs=int(info.samplerate), n_samples=int(info.frames),
                    locator={"relpath": rel, "container": "zip_member", "member": member},
                    src_key=f"S{s}", rcv_key=f"R{r}",
                    src_pos=tuple(float(v) for v in meta["src_loc"]),
                    rcv_pos=tuple(float(v) for v in meta["rec_loc"]),
                    room_label=f"{LABELS.get(m['cat'], m['cat'])} {m['idx']} (AcousticRooms model)",
                    category_hint=m["cat"],
                    extra={"subtype": info.subtype, "ir_norm": meta.get("IR_norm")})

    def load(self, locator: dict, root: Path):
        data = _zip(str(root / locator["relpath"])).read(locator["member"])
        x, fs = sf.read(io.BytesIO(data), dtype="float64", always_2d=True)
        return x.T, int(fs)
