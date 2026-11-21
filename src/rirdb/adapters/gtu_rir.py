"""Adapter `gtu_rir_pickle` for gtu_rir (GTU-RIR, Gebze Technical University; github.com/mehmetpekmezci/gtu-rir).

The data are one Python pickle, RIR.pickle.dat (5.4 GB, protocol 4): a list of 15,202 records, one per microphone
recording, each a list of 44 fields named in the authors' 02.data/data_reader/read_data.py. Positions are strings in
centimetres; the room's width, height and depth (39-41) are floats in centimetres; field 42 is the authors' RT60 (a
numpy float64); field 43 is the IR, a float32 array of 88,200 samples (2 s at 44.1 kHz; the authors' rir.py puts the
peak 50 ms in).

Loading a pickle can run code, so this reader never unpickles it freely. It reads the file twice:
  1. pickletools.genops, which only parses opcodes, gives the offset of every long byte string: the IRs' samples;
  2. a restricted Unpickler reads the metadata. Its find_class allows only the four names the file imports
     (numpy.dtype, numpy.ndarray, numpy.core.multiarray.scalar and _reconstruct) and raises for any other; each
     array becomes a stub that keeps its shape, dtype and length and drops the samples.
The k-th array is the k-th long byte string; the counts and lengths must agree. An IR is loaded by reading its samples
at that offset (np.fromfile), after checking the opcode and length just before them: nothing is unpickled then.
The metadata pass holds the file's byte strings until it ends (the pickle memoizes them): about 5.5 GB, once per index.

Per record (the authors' field numbers):
  space      roomId (36): room-207, room-sport01, ...
  source     the loudspeaker: the stand set-up (37, by its time stamp), the speaker motor position within one turn
             (field 14 mod 200: 200 steps are 360 degrees, Record.py) and the speaker number; the stand reverses direction
             at each step and its motor drifts, so the step number does not name a position
  receiver   the microphone: the set-up, the microphone motor position within one turn (field 13 mod 200) and the
             microphone number (38)
  positions  stand origin + relative coordinates for x and y, the relative z as height (as read_data.py), in metres
  extra      the authors' RT60 (42), the room's width, height and depth in metres (39-41), essFilePath (35)
One channel per record (mono_omni, role omni).

adapter.params (optional): file (default "RIR.pickle.dat"), fs (default 44100).
"""

from __future__ import annotations

import pickle
import pickletools
import re
from pathlib import Path
from typing import Iterator

import numpy as np

from rirdb.adapters.base import IRRecord, slug

FILE = "RIR.pickle.dat"
LONG = 1024                       # byte strings longer than this are IR samples
STEP = re.compile(r"micstep-(?P<mic>\d+)-spkstep-(?P<spk>\d+)-spkno-(?P<no>\d+)")
OPCODE = {"BINBYTES": (b"B", 4), "BINBYTES8": (b"\x8e", 8)}   # opcode byte and length bytes before the samples


class _ArrayStub:
    """Stands in for a numpy array while unpickling: keeps its shape, dtype and length, drops the samples."""

    def __init__(self, *args):                    # _reconstruct(ndarray, (0,), b"b")
        self.shape, self.dtype, self.nbytes = None, None, 0

    def __setstate__(self, state):                # (version, shape, dtype, Fortran order, samples)
        _, self.shape, self.dtype, fortran, raw = state
        if fortran or not isinstance(raw, bytes):
            raise pickle.UnpicklingError("not a C-ordered array of bytes")
        self.nbytes = len(raw)


def _scalar(dtype, raw: bytes):                   # numpy.core.multiarray.scalar(dtype, bytes) -> a Python number
    return np.frombuffer(raw, dtype=dtype)[0].item()


ALLOWED = {("numpy", "dtype"): np.dtype, ("numpy", "ndarray"): _ArrayStub,
           ("numpy.core.multiarray", "_reconstruct"): _ArrayStub, ("numpy.core.multiarray", "scalar"): _scalar}


class _RestrictedUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) not in ALLOWED:
            raise pickle.UnpicklingError(f"{module}.{name} is not allowed")
        return ALLOWED[(module, name)]


def sample_offsets(path: Path) -> list[tuple[int, int, str]]:
    """(offset, length, opcode) of every long byte string in the pickle, in file order, from its opcodes alone."""
    out = []
    with open(path, "rb") as f:
        for op, arg, pos in pickletools.genops(f):
            if op.name in OPCODE and len(arg) > LONG:
                out.append((pos + 1 + OPCODE[op.name][1], len(arg), op.name))
    return out


def read_metadata(path: Path) -> list[list]:
    """The records, each array replaced by an _ArrayStub."""
    with open(path, "rb") as f:
        rows = _RestrictedUnpickler(f).load()
    if not isinstance(rows, list) or not all(isinstance(r, list) and len(r) == 44 for r in rows):
        raise ValueError(f"{path.name}: not a list of 44-field records")
    return rows


class GtuRirPickleAdapter:
    NAME = "gtu_rir_pickle"

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        rel = p.get("file", FILE)
        fs = int(p.get("fs", 44100))
        offsets = sample_offsets(root / rel)
        rows = read_metadata(root / rel)
        arrays = [r[43] for r in rows]
        if len(arrays) != len(offsets) or any(
                not isinstance(a, _ArrayStub) or a.nbytes != n or len(a.shape) != 1 or a.dtype.kind != "f"
                for a, (_, n, _) in zip(arrays, offsets)):
            raise ValueError(f"{rel}: the arrays and the byte strings do not match")
        for i, (r, (offset, _, op)) in enumerate(zip(rows, offsets)):
            room, setup, step, _ = r[35].split("/")
            s = STEP.fullmatch(step)
            when = setup.rsplit("-", 1)[-1]                 # the set-up's time stamp, e.g. 2022.08.01_13.43.15
            cm = lambda k: float(r[k]) / 100              # noqa: E731  (centimetres -> metres)
            yield IRRecord(
                dataset_id=dataset.id, local_key=f"{rel}#{i}", room_key=slug(r[36]), room_label=r[36],
                capture_format="mono_omni", channel_roles=("omni",), fs=fs, n_samples=int(r[43].shape[0]),
                locator={"relpath": rel, "container": "pickle_bytes", "offset": offset, "opcode": op,
                         "n": int(r[43].shape[0]), "dtype": r[43].dtype.str},
                src_key=f"{when}_spkpos{int(float(r[14])) % 200}_spk{s['no']}",
                rcv_key=f"{when}_micpos{int(float(r[13])) % 200}_mic{r[38]}",
                src_pos=(cm(10) + cm(20), cm(11) + cm(21), cm(22)), rcv_pos=(cm(7) + cm(27), cm(8) + cm(28), cm(29)),
                extra={"rt60_authors_s": float(r[42]), "room_whd_m": [cm(39), cm(40), cm(41)], "ess_path": r[35]})

    def load(self, locator: dict, root: Path) -> tuple[np.ndarray, int]:
        p = getattr(self, "_params", {}) or {}
        dtype = np.dtype(locator["dtype"])
        code, width = OPCODE[locator["opcode"]]
        with open(root / locator["relpath"], "rb") as f:
            f.seek(locator["offset"] - 1 - width)
            head = f.read(1 + width)
            if head[:1] != code or int.from_bytes(head[1:], "little") != locator["n"] * dtype.itemsize:
                raise ValueError(f"{locator['relpath']}: no {locator['n']}-sample array at {locator['offset']}")
            x = np.fromfile(f, dtype=dtype, count=locator["n"])
        return x[None, :].astype(np.float64), int(p.get("fs", 44100))
