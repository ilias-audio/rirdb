"""Adapter `clarity_scenes_json` for clarity_cec1 (Clarity Enhancement Challenge 1, Zenodo 17068105): one simulated
room per scene.

The metadata archive holds scenes.<split>.json (train 6,000, dev 2,500, eval 1,500 scenes). Each scene has its own
cuboid living room R<nnnnn> (the scene number), a listener, a target talker and one interferer. For each room the
split archive holds, under clarity_CEC1_<split>/clarity_CEC1_data/clarity_data/<split>/rooms/:
    brir/brir_<room>_<t|i1>_CH<k>.wav   RAVEN BRIRs from the target (t) or the interferer (i1), 2 channels (left
                                        and right ear), 44.1 kHz: CH0 at the eardrum, CH1, CH2, CH3 the front,
                                        middle and rear microphones of a behind-the-ear hearing aid on each ear
    rpf/<room>_<t|i1>_CH<k>.rpf         the RAVEN project of each file: positions, source directivity, and the
                                        HRTF set (ProjectPath_HRTFDB, e.g. ...\\VP_E9-ED_RAVEN.daff)
    ac/<room>.ac                        the room model (AC3D: walls, door, windows, curtains, sofa, rug, materials)
scenes.<split>.json, per scene: room {name, dimensions "LxWxH"}, listener/target/interferer Positions (m, RAVEN
frame: x across the room from -W/2 to W/2, y along it from 0 to L, z up) and ViewVectors, hrirfilename (the BTE
head, e.g. "VP_E11-BTE_fr").

Two records per source: the eardrum pair (CH0) is one binaural IR (receiver `eardrum`, roles L, R); the three BTE
pairs (CH1-CH3) are one 6-channel IR, the two 3-microphone hearing aids (receiver `bte`, array_raw), as the challenge
gives them to a hearing-aid algorithm. The eardrum IR is the preferred one, so a space counts its eardrum pairs in
statistics. The two receivers sit at the listener position; in most scenes RAVEN rendered the eardrum pair with
another head than the BTE pairs (the rpf files say which), so each record carries its own head, read from the rpf
of its first file.

adapter.params (optional):
  scenes    glob of the scene files
            (default "clarity_CEC1_metadata/clarity_CEC1_data/clarity_data/metadata/scenes.*.json")
  rooms     the rooms folder of a split, {split} filled in
            (default "clarity_CEC1_{split}/clarity_CEC1_data/clarity_data/{split}/rooms")
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

import soundfile as sf

from rirdb.adapters.base import IRRecord, slug
from rirdb.adapters.wav_tree import WavTreeAdapter

SOURCES = {"t": ("target", "target"), "i1": ("interferer", "interferer")}   # file tag: (key, scene field)
BTE_ROLES = ("BTE_front_L", "BTE_front_R", "BTE_mid_L", "BTE_mid_R", "BTE_rear_L", "BTE_rear_R")
HRTF = re.compile(r"^\s*ProjectPath_HRTFDB\s*=\s*(.*?)\s*$", re.M)
SCENES = "clarity_CEC1_metadata/clarity_CEC1_data/clarity_data/metadata/scenes.*.json"


def head_of(rpf: Path) -> str:
    """The head whose HRTF set a RAVEN project file names: "VP_E9" for ...\\VP_E9-ED_RAVEN.daff (empty if none)."""
    m = HRTF.search(rpf.read_text(errors="replace"))
    return m.group(1).replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".daff").split("-")[0] if m else ""


class ClarityScenesAdapter(WavTreeAdapter):
    NAME = "clarity_scenes_json"

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        rooms_tpl = p.get("rooms", "clarity_CEC1_{split}/clarity_CEC1_data/clarity_data/{split}/rooms")
        for scenes_file in sorted(root.glob(p.get("scenes", SCENES))):
            for sc in json.loads(scenes_file.read_text()):
                split, room = sc["dataset"], sc["room"]["name"]
                rooms = Path(rooms_tpl.format(split=split))
                L, W, H = (float(v) for v in sc["room"]["dimensions"].split("x"))
                listener = tuple(float(v) for v in sc["listener"]["Positions"])
                for tag, (src_key, field) in SOURCES.items():
                    src = sc[field]
                    for rcv, chans, fmt, roles in (("eardrum", [0], "binaural", ("L", "R")),
                                                   ("bte", [1, 2, 3], "array_raw", BTE_ROLES)):
                        rels = [(rooms / "brir" / f"brir_{room}_{tag}_CH{k}.wav").as_posix() for k in chans]
                        info = sf.info(str(root / rels[0]))
                        if info.channels * len(rels) != len(roles):
                            raise ValueError(f"{rels[0]}: {info.channels} channels, expected 2")
                        extra = {"subtype": info.subtype, "preferred": rcv == "eardrum", "split": split,
                                 "scene": sc["scene"], "room_size_m": [L, W, H],
                                 "volume_m3": round(L * W * H, 3),
                                 "head": head_of(root / rooms / "rpf" / f"{room}_{tag}_CH{chans[0]}.rpf"),
                                 "listener_view": sc["listener"]["ViewVectors"],
                                 "source_view": src.get("ViewVectors")}
                        yield IRRecord(
                            dataset_id=dataset.id, local_key="|".join(rels), room_key=slug(room),
                            capture_format=fmt, channel_roles=roles, fs=int(info.samplerate),
                            n_samples=int(info.frames),
                            locator={"relpath": rels[0], "container": "audio"} if len(rels) == 1
                            else {"files": rels, "container": "audio_pair"},
                            src_key=src_key, rcv_key=rcv,
                            src_pos=tuple(float(v) for v in src["Positions"]), rcv_pos=listener,
                            room_label=f"Simulated living room {L:g} x {W:g} x {H:g} m ({split} scene {sc['scene']})",
                            category_hint=split, extra=extra)

    # load(): WavTreeAdapter.load reads the file, or the three BTE files stacked as 6 channels
