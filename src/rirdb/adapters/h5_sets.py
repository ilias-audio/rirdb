"""The `h5_sets` reader: IRs kept as measurement sets, 2-D arrays in the groups of HDF5 files.

The DTU ACT room files keep each set as an array of its own (dataset_plane/impulse_response,
dataset_sphere/impulse_response, ...) beside the sweeps, the noise and the positions; the Jabra file keeps rirs and
ref_rirs; the Niels Bohr auditorium files keep one eigenmike recording per group and a line array's rows in others.
The ndarray reader keys a record by file and index only, so two arrays in one file would share keys. This reader
keys each IR by file, array and index, names the receiver after the set, and reads the positions the file holds.

Params, at the top level or per entry of a list `groups`:
  glob            files to read, relative to files/
  pattern         regex on the relative path; named groups room, cond, src
  sets            regex fullmatched against every HDF5 dataset path in the file; its named group `set` names the
                  measurement set, e.g. 'dataset_(?P<set>plane|sphere)/impulse_response'. The arrays are 2-D.
  set_names       {set: name} for the receiver key (default: the set as matched)
  rows            records (default): each row (or column) is one IR | channels: the array is one IR, whose rows
                  (or columns) are its channels
  time_axis       the axis of the samples, 0 or 1; it must be the longer one
  fs | fs_attr    the sampling rate, or the name of an attribute holding it (on the array, else on the file's
                  root); a string attribute is read up to its first non-number character ('48000 samples/second')
  positions       receiver positions: a path relative to the array's group (e.g. 'position'), or {set: path} with
                  paths from the root. Stored (3, n) or (n, 3), one per IR.
  src_position    the file's one source position: an HDF5 dataset path, or '@name' for an attribute of the root
  pos_scale       multiplies every position (0.001 for millimetres); default 1
  src_default     the source key when the pattern gives none
  room_default, capture_format, roles (one per channel), reference_role, ir_kind
Keys: file#array#k for records, file#array for channels. The receiver key is <set name>-<k>, or the set name.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import numpy as np

from rirdb.adapters.base import IRRecord, slug


def _number(value) -> float:
    """A number from an HDF5 attribute: a scalar, an array, or a string that starts with the number."""
    if isinstance(value, np.ndarray) and value.dtype.kind in "SUO":
        value = value.ravel()[0]
    if isinstance(value, bytes):
        value = value.decode()
    if isinstance(value, str):
        m = re.match(r"\s*([0-9]+(?:\.[0-9]*)?)", value)
        if not m:
            raise ValueError(f"no number at the start of {value!r}")
        return float(m.group(1))
    return float(np.ravel(value)[0])


def _xyz(a, n: int, what: str) -> np.ndarray:
    """Positions as (n, 3), from an array stored (n, 3) or (3, n). `what` names the array in the error raised when
    it holds another number of positions."""
    a = np.asarray(a, dtype=np.float64)
    if a.ndim == 1 and a.size == 3 and n == 1:
        return a.reshape(1, 3)
    if a.shape == (n, 3):
        return a
    if a.shape == (3, n):
        return a.T
    raise ValueError(f"{what}: shape {a.shape}, expected the positions of {n} IRs as ({n}, 3) or (3, {n})")


def _sibling(name: str, path: str) -> str:
    """The HDF5 path of `path`, relative to the group of the array `name`."""
    return "/".join(filter(None, [name.rpartition("/")[0], path]))


class H5SetsAdapter:
    """The h5_sets reader (params in the module docstring). h5_freqresp (computed.py) reuses it, replacing the
    methods that read the length, the rate and the values of an IR."""

    @staticmethod
    def _groups(params: dict) -> list[dict]:
        return params.get("groups") or [params]

    @staticmethod
    def _open(path: Path):
        import h5py

        return h5py.File(path, "r")

    @staticmethod
    def _arrays(h, sets: re.Pattern) -> list[str]:
        """The paths of the HDF5 datasets in h that `sets` fullmatches, sorted."""
        import h5py

        found: list[str] = []
        h.visititems(lambda name, obj: found.append(name)
                     if isinstance(obj, h5py.Dataset) and sets.fullmatch(name) else None)
        return sorted(found)

    @staticmethod
    def _fs(h, arr, g: dict) -> int:
        if "fs" in g:
            return int(g["fs"])
        name = g["fs_attr"]
        value = arr.attrs[name] if name in arr.attrs else h.attrs[name]
        return int(round(_number(value)))

    @staticmethod
    def _sample_axis(g: dict) -> int:
        """The axis of the array that holds an IR's values."""
        return int(g["time_axis"])

    def _length_and_rate(self, h, name: str, g: dict) -> tuple[int, int]:
        """(n_samples, fs) of the IRs in the array `name`. The time axis must be the longer one: read the other way,
        every sample would become an IR."""
        arr = h[name]
        t_ax = self._sample_axis(g)
        if arr.shape[t_ax] < arr.shape[1 - t_ax]:
            raise ValueError(f"{name}: shape {arr.shape}: time_axis {t_ax} is the shorter axis")
        return int(arr.shape[t_ax]), self._fs(h, arr, g)

    @staticmethod
    def _positions(h, name: str, set_name: str, g: dict, n: int) -> np.ndarray | None:
        """The receiver positions of the n IRs of the array `name` (set `set_name`), (n, 3) in metres, or None."""
        spec = g.get("positions")
        path = spec.get(set_name) if isinstance(spec, dict) else _sibling(name, spec) if spec else None
        if not path:
            return None
        return _xyz(h[path][()], n, path) * float(g.get("pos_scale", 1))

    @staticmethod
    def _src_pos(h, g: dict) -> tuple | None:
        """The file's one source position (src_position), in metres, or None."""
        spec = g.get("src_position")
        if not spec:
            return None
        value = np.asarray(h.attrs[spec[1:]] if spec.startswith("@") else h[spec][()], dtype=np.float64)
        if value.size != 3:
            raise ValueError(f"src_position {spec}: shape {value.shape}, expected one position")
        return tuple(float(v) * float(g.get("pos_scale", 1)) for v in value.ravel())

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        for gi, g in enumerate(self._groups(dataset.adapter.params)):
            pattern = re.compile(g["pattern"]) if g.get("pattern") else None
            for f in sorted(root.glob(g["glob"])):
                rel = f.relative_to(root).as_posix()
                m = pattern.search(rel) if pattern else None
                if pattern is not None and m is None:
                    continue
                yield from self._file_records(dataset, gi, g, f, rel, m.groupdict() if m else {})

    def _file_records(self, dataset, gi: int, g: dict, f: Path, rel: str, gd: dict) -> Iterator[IRRecord]:
        """The records of one file f (path rel), read with the params g (entry gi of groups); gd holds the
        pattern's groups."""
        sets = re.compile(g["sets"])
        as_channels = g.get("rows", "records") == "channels"
        room = gd.get("room") or g.get("room_default") or dataset.id
        with self._open(f) as h:
            src_pos = self._src_pos(h, g)
            for name in self._arrays(h, sets):
                arr = h[name]
                if arr.ndim != 2:
                    raise ValueError(f"{rel}#{name}: shape {arr.shape}, expected a 2-D array")
                matched = sets.fullmatch(name).group("set")
                set_name = (g.get("set_names") or {}).get(matched, matched)
                n, fs = self._length_and_rate(h, name, g)
                n_rows = arr.shape[1 - self._sample_axis(g)]
                n_rec, n_ch = (1, n_rows) if as_channels else (n_rows, 1)
                roles = g.get("roles") or (["omni"] if n_ch == 1 else [f"mic_{i}" for i in range(n_ch)])
                if len(roles) != n_ch:
                    raise ValueError(f"{rel}#{name}: {len(roles)} roles for {n_ch} channels")
                pos = self._positions(h, name, matched, g, n_rec)
                for k in range(n_rec):
                    yield IRRecord(
                        dataset_id=dataset.id, local_key=f"{rel}#{name}" + ("" if as_channels else f"#{k}"),
                        room_key=slug(room), room_label=str(room).replace("_", " "),
                        capture_format=g.get("capture_format", "mono_omni" if n_ch == 1 else "array_raw"),
                        channel_roles=tuple(roles), fs=fs, n_samples=n,
                        locator={"relpath": rel, "container": "h5", "group": gi, "array": name,
                                 "k": None if as_channels else k},
                        condition_key=gd.get("cond"), src_key=gd.get("src") or g.get("src_default"),
                        rcv_key=set_name if as_channels else f"{set_name}-{k}",
                        src_pos=src_pos, rcv_pos=None if pos is None else tuple(float(v) for v in pos[k]),
                        ir_kind=g.get("ir_kind", "room"), extra={"reference_role": g.get("reference_role")})

    def _read(self, h, locator: dict, g: dict) -> np.ndarray:
        """The stored values of one IR, as stored (no type conversion), (channels, values): one row of the array,
        or the whole array for rows: channels."""
        arr = h[locator["array"]]
        if self._sample_axis(g) == 0:
            arr = arr[()].T if locator.get("k") is None else arr[:, int(locator["k"])]
        else:
            arr = arr[()] if locator.get("k") is None else arr[int(locator["k"]), :]
        return np.atleast_2d(np.asarray(arr))

    def load(self, locator: dict, root: Path):
        g = self._groups(self._params)[locator.get("group", 0)]
        with self._open(root / locator["relpath"]) as h:
            x = self._read(h, locator, g)
            _, fs = self._length_and_rate(h, locator["array"], g)
        return x.astype(np.float64), fs
