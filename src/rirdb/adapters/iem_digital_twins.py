"""Adapter `iem_digital_twins` for the IEM Audio-Visual Digital Twins: Graz Cathedral (WAV) and the IEM
Experimentalstudio (SOFA). It takes no parameters.

Dom/IR_denoised/<folder>/h<S>_<M>[_45grad_rHochaltar]_<sweep>[_<take>].wav
    64-channel Eigenmike em64 capsule IRs, 48 kHz, 10 s. Source S1, S3 or S4; receiver M1 to M10, with
    "_45grad_rHochaltar" (the array turned 45 degrees towards the high altar) kept in the receiver key.
    Each path was deconvolved from two sweeps: Sweep2entzerrt (equalised) is the preferred IR, Sweep1original
    (and its two extra takes _001, _002) are kept, not preferred. ImpulseResponses_002/..._002.wav is named
    hS1_M1_45grad but is the Sweep1 IR of hS1_M4_45grad (correlation 0.97 with that Sweep2 file, the same direct-sound
    delay; 0.02 with hS1_M1_45grad): the reader files it under M4.

Experimentalstudio/EM_Line_<line>_<Direct|Diffuse>_<Left|Center|Right>.sofa
    SingleRoomSRIR, Data.IR (11 positions, 36 channels, 33600 samples): 5th-order Ambisonics (ACN, SN3D) from the
    em64 along three lines of 11 positions, for the loudspeakers Left, Center and Right. Each response is published
    in two parts, a sparse direct part (non-zero only around the direct sound and early reflections) and the
    diffuse rest; the reader adds the two files, so one IR per line, loudspeaker and position (3 x 3 x 11 = 99).
    Indexing checks that the two files can be added: the same Data.IR shape and sampling rate, and Data.Delay 0.

Not read: the MATLAB files of the meeting room and the lecture hall (IRuntreated, 35000 x 12 x 25 x 12 and
35000 x 12 x 32 x 12): which axis is loudspeaker, microphone or repetition is in the I/O spreadsheets next to them
on the server, which the mirror did not fetch.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from rirdb.adapters.base import IRRecord

DOM = re.compile(r"^Dom/IR_denoised/[^/]+/h(?P<src>S\d+)_(?P<rcv>M\d+)(?P<turn>_45grad_rHochaltar)?_"
                 r"(?P<sweep>Sweep1original|Sweep2entzerrt)(?:_(?P<take>\d+))?\.wav$")
STUDIO = re.compile(r"^Experimentalstudio/EM_Line_(?P<line>\d+)_Direct_(?P<spk>Left|Center|Right)\.sofa$")
MISNAMED = {"Dom/IR_denoised/ImpulseResponses_002/hS1_M1_45grad_rHochaltar_Sweep1original_002.wav": "M4"}
HOA5_ROLES = ("W", "Y", "Z", "X") + tuple(f"ACN{i}" for i in range(4, 36))


def sofa_facts(path: Path) -> tuple[tuple, int, bool]:
    """(Data.IR shape, sampling rate, whether every Data.Delay is 0) of a SOFA file."""
    import h5py

    with h5py.File(path, "r") as h:
        delay_zero = "Data.Delay" not in h or not np.any(h["Data.Delay"][()])
        return h["Data.IR"].shape, int(np.ravel(h["Data.SamplingRate"][()])[0]), bool(delay_zero)


class IemDigitalTwinsAdapter:
    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        yield from self._dom(dataset, root)
        yield from self._studio(dataset, root)

    def _dom(self, dataset, root: Path) -> Iterator[IRRecord]:
        for f in sorted(root.glob("Dom/IR_denoised/*/*.wav")):
            rel = f.relative_to(root).as_posix()
            m = DOM.match(rel)
            if m is None:
                continue
            info = sf.info(str(f))
            yield IRRecord(
                dataset_id=dataset.id, local_key=rel, room_key="dom", room_label="Graz Cathedral",
                capture_format="sma_raw", channel_roles=tuple(f"mic_{i}" for i in range(info.channels)),
                fs=int(info.samplerate), n_samples=int(info.frames),
                locator={"relpath": rel, "container": "audio"},
                src_key=m["src"], rcv_key=MISNAMED.get(rel, m["rcv"]) + ("_turned45" if m["turn"] else ""),
                ir_kind="room",
                extra={"subtype": info.subtype, "reference_role": "mean",
                       "preferred": m["sweep"] == "Sweep2entzerrt", "sweep": m["sweep"], "take": m["take"]})

    def _studio(self, dataset, root: Path) -> Iterator[IRRecord]:
        for f in sorted(root.glob("Experimentalstudio/EM_Line_*_Direct_*.sofa")):
            rel = f.relative_to(root).as_posix()
            m = STUDIO.match(rel)
            if m is None:
                continue
            diffuse = rel.replace("_Direct_", "_Diffuse_")
            if not (root / diffuse).exists():
                continue
            shape, fs, direct_delay_zero = sofa_facts(f)
            d_shape, d_fs, diffuse_delay_zero = sofa_facts(root / diffuse)
            if (d_shape, d_fs) != (shape, fs) or not (direct_delay_zero and diffuse_delay_zero):
                raise ValueError(f"{rel} and {diffuse} cannot be added: Data.IR {shape} and {d_shape}, "
                                 f"{fs} and {d_fs} Hz, Data.Delay zero: {direct_delay_zero} and {diffuse_delay_zero}")
            n_pos, n_ch, n = shape
            for k in range(n_pos):
                yield IRRecord(
                    dataset_id=dataset.id, local_key=f"{rel}|{diffuse}#{k}", room_key="experimentalstudio",
                    room_label="IEM Experimentalstudio", capture_format="hoa_sh",
                    channel_roles=HOA5_ROLES[:n_ch], fs=fs, n_samples=int(n),
                    locator={"files": [rel, diffuse], "container": "sofa_sum", "m": k},
                    src_key=m["spk"], rcv_key=f"line{m['line']}_p{k + 1:02d}", sh_norm="SN3D",
                    orientation_known=False, ir_kind="room",
                    extra={"subtype": "DOUBLE", "sofa_conventions": "SingleRoomSRIR"})

    def load(self, locator: dict, root: Path) -> tuple[np.ndarray, int]:
        if locator.get("container") == "sofa_sum":
            import h5py

            parts = []
            for rel in locator["files"]:
                with h5py.File(root / rel, "r") as h:
                    parts.append(np.asarray(h["Data.IR"][locator["m"]], dtype=np.float64))
                    fs = int(np.ravel(h["Data.SamplingRate"][()])[0])
            return parts[0] + parts[1], fs
        x, fs = sf.read(str(root / locator["relpath"]), dtype="float64", always_2d=True)
        return x.T, int(fs)
