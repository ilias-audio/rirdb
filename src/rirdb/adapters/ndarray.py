"""Dense IR arrays in HDF5 datasets, .npy files or MATLAB v5 variables (MIRACLE, SRIRACHA, MeshRIR, MP-RIR, ...)."""

from __future__ import annotations

import itertools
import re
from collections import OrderedDict
from pathlib import Path
from typing import Iterator

import numpy as np

from rirdb.adapters.base import IRRecord, slug
from rirdb.adapters.wav_tree import field

SH_NORMS = ("FuMa", "SN3D", "N3D")
KEY_ROLES = ("src", "rcv", "cond")

# Arrays kept in memory between loads (never written to disk). MATLAB v5 variables are read whole, so the last one
# is kept: the records of one file load together. With `cache_arrays`, whole HDF5 and .npy arrays are kept too.
_V5_CACHE: OrderedDict = OrderedDict()
_ARRAY_CACHE: OrderedDict = OrderedDict()


def _remember(cache: OrderedDict, key: tuple, size: int, read):
    """cache[key], read with `read()` the first time. Only the `size` most recently used entries are kept: the
    oldest is dropped before a new one is read, so memory never holds more than `size`."""
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    while cache and len(cache) >= size:
        cache.popitem(last=False)
    cache[key] = read()
    return cache[key]


def _h5py():
    """h5py, with the compression filters of hdf5plugin when it is installed."""
    import h5py

    try:
        import hdf5plugin  # noqa: F401
    except ImportError:
        pass
    return h5py


class NdArrayAdapter:
    """Dense IR tensors in HDF5 datasets or .npy files (MIRACLE, SRIRACHA, MeshRIR, MP-RIR, ...).

    Params (all per `groups` entry, or top level):
      glob                 files to read

      array                HDF5 dataset path (h5) or MATLAB v5 variable (mat)
      array_pattern        regex: every matching top-level HDF5 array becomes a condition
      container            h5 | npy | mat (MATLAB v5, loaded whole)
      time_axis            axis holding samples
      channel_axis         axis holding simultaneous channels of one record (optional)
      record_axes          axes enumerated into records (all remaining axes if omitted); [] when each file holds
                           one IR: its item is then empty, as data-model.md asks (a size-1 axis is not dropped
                           on its own, so files whose item is published as "0" keep it)
      fs                   sampling rate, or fs_attr (HDF5 attribute path "dataset@attr" / "@attr"), fs_dataset
                           (an HDF5 scalar dataset) or fs_var (a MATLAB v5 variable)
      pattern              regex on the relative path (groups room, cond, src, rcv); a key in two parts of the
                           path takes two groups, src and src_2 (joined with "_"), as in wav_tree
      room_default, capture_format, roles, reference_role, ir_kind
      src_default          the source key where neither the pattern nor the record index gives one
      sh_norm              FuMa | SN3D | N3D: the normalisation of Ambisonic channels (empty if not Ambisonic)
      orientation_known    false when the array's x axis is not known to point at the sources (default true)
      channels             indices along channel_axis to keep (e.g. one ear pair of several)
      record_is            rcv (default) | src: what the record index enumerates, for the position keys
      select               {axis: [indices]}: only these indices of a record axis become records; the others are
                           left out (keys keep the original index)
      axis_keys            {axis: {as: src | rcv | cond, names: [one per index]}}, or names_from: an array in the
                           same file and format (e.g. "{:g}deg"): the key that each index of a record axis gives,
                           joined after the pattern's own value with "_". With axis_keys, record_is is not used.
      crop_s               keep only the first crop_s seconds (e.g. long deconvolutions whose tail is non-stationary noise)
      positions            {src: SPEC, rcv: SPEC}: positions in metres, x y z. SPEC is one of
                             array: an HDF5 dataset or MATLAB v5 variable in the same file,
                             npy: the name of an .npy file in the same folder,
                             groups: [gx, gy, gz], pattern groups holding the numbers;
                           with, optionally, axis (the record axis whose index picks the row; default the first),
                           scale (to metres, default 1) and key_from_position: true, which makes the key the
                           position in centimetres, "x_y_z", after key_prefix. Rows are (N, 3) or (3, N) (as
                           MATLAB v7.3 stores them; (N, 3) when N is 3); a single point applies to every record.
      cache_arrays         N (h5 and npy): load reads each array whole, in its stored type, and keeps the N most
                           recently used in memory. For arrays compressed in chunks across records, where reading
                           one record decompresses them all (ertd, aalto_coupled_simulated). Indexing does not use it.
    Records are the Cartesian product of record-axis indices; loading slices lazily. A record's key is
    file#item, the item being the array name (with array_pattern) and the record index, joined with "#".
    """

    def _groups(self, params):
        return params.get("groups") or [params]

    def _open(self, path: Path, g: dict, array: str | None = None, whole: bool = False):
        """(array, open HDF5 file or None). `whole`: for load, where `cache_arrays` keeps whole arrays in memory."""
        array = array or g.get("array")
        container = g.get("container", "h5")
        if container == "mat":                  # MATLAB v5: loaded whole (small files)
            return _remember(_V5_CACHE, (str(path), array), 1, lambda: self._read_v5(path, array)), None
        if whole and g.get("cache_arrays"):
            return _remember(_ARRAY_CACHE, (str(path), array), int(g["cache_arrays"]),
                             lambda: self._read_array(path, container, array)), None
        if container == "npy":
            return np.load(path, mmap_mode="r"), None
        h = _h5py().File(path, "r")
        return h[array], h

    @staticmethod
    def _read_v5(path: Path, name: str) -> np.ndarray:
        import scipy.io

        return np.asarray(scipy.io.loadmat(str(path), variable_names=[name])[name])

    @staticmethod
    def _read_array(path: Path, container: str, array: str | None) -> np.ndarray:
        """A whole HDF5 or .npy array, in the type it is stored in (so slicing it gives the samples h5py would)."""
        if container == "npy":
            return np.load(path)
        with _h5py().File(path, "r") as h:
            return h[array][()]

    def _fs(self, path: Path, g: dict) -> int:
        if "fs" in g:
            return int(g["fs"])
        if "fs_var" in g:                              # MATLAB v5 variable
            import scipy.io

            return int(np.ravel(scipy.io.loadmat(str(path), variable_names=[g["fs_var"]])[g["fs_var"]])[0])
        import h5py

        with h5py.File(path, "r") as h:
            if "fs_dataset" in g:                     # scalar dataset, e.g. metadata/sampling_rate
                return int(np.ravel(h[g["fs_dataset"]][()])[0])
            ds, _, attr = g["fs_attr"].partition("@")
            obj = h[ds] if ds else h
            return int(np.ravel(obj.attrs[attr])[0])

    @staticmethod
    def _axes(g: dict, shape: tuple) -> tuple[int, int | None, list[int]]:
        """(time axis, channel axis or None, record axes), as non-negative axis numbers of an array of `shape`."""
        t_ax = g["time_axis"] % len(shape)
        c_ax = g.get("channel_axis")
        c_ax = None if c_ax is None else c_ax % len(shape)
        rec_axes = g.get("record_axes")
        rec_axes = [a % len(shape) for a in rec_axes] if rec_axes is not None else \
            [a for a in range(len(shape)) if a not in (t_ax, c_ax)]
        return t_ax, c_ax, rec_axes

    @staticmethod
    def _sh_norm(g: dict) -> str | None:
        """The group's sh_norm, checked against the three conventions RIRDB knows."""
        norm = g.get("sh_norm") or None
        if norm is not None and norm not in SH_NORMS:
            raise ValueError(f"sh_norm {norm!r}: expected one of {SH_NORMS}")
        return norm

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        for gi, g in enumerate(self._groups(dataset.adapter.params)):
            pattern = re.compile(g["pattern"]) if g.get("pattern") else None
            for f in sorted(root.glob(g["glob"])):
                m_ = pattern.search(f.relative_to(root).as_posix()) if pattern else None
                if pattern is not None and m_ is None:
                    continue
                gd = m_.groupdict() if m_ else {}
                rel = f.relative_to(root).as_posix()
                arrays = [g.get("array")]
                if g.get("array_pattern"):            # one HDF5 array per condition (e.g. dEchorate)
                    import h5py

                    with h5py.File(f, "r") as hh:
                        arrays = sorted(k for k in hh.keys() if re.fullmatch(g["array_pattern"], k))
                for array in arrays:
                    yield from self._records(dataset, g, gi, f, rel, gd, array)

    @staticmethod
    def _read_whole(path: Path, g: dict, name: str) -> np.ndarray:
        """A small array stored beside the IRs in the same file: an HDF5 dataset or a MATLAB v5 variable."""
        if g.get("container", "h5") == "mat":
            return NdArrayAdapter._read_v5(path, name)
        if g.get("container", "h5") != "h5":
            raise ValueError(f"{name}: arrays named inside a file need container h5 or mat")
        with _h5py().File(path, "r") as h:
            return np.asarray(h[name][()])

    @staticmethod
    def _per_axis(g: dict, name: str, shape: tuple, rec_axes: list[int]) -> dict:
        """The `select` or `axis_keys` entries by non-negative axis number. Each must name a record axis."""
        out = {}
        for axis, value in (g.get(name) or {}).items():
            a = int(axis) % len(shape)
            if a not in rec_axes:
                raise ValueError(f"{name}: axis {axis} is not a record axis (record axes {rec_axes})")
            out[a] = value
        return out

    def _record_indices(self, g: dict, shape: tuple, rec_axes: list[int]) -> Iterator[tuple]:
        """Every record index: each index of every record axis, or on an axis `select` names, those it lists."""
        select = self._per_axis(g, "select", shape, rec_axes)
        for a, indices in select.items():
            if any(not 0 <= int(i) < shape[a] for i in indices):
                raise ValueError(f"select: an index of axis {a} is outside 0..{shape[a] - 1}")
        return itertools.product(*[[int(i) for i in select[a]] if a in select else range(shape[a])
                                   for a in rec_axes])

    def _axis_names(self, path: Path, g: dict, shape: tuple, rec_axes: list[int]) -> dict:
        """{axis: (src | rcv | cond, one name per index of the axis)}, from `axis_keys`."""
        out = {}
        for a, spec in self._per_axis(g, "axis_keys", shape, rec_axes).items():
            if spec.get("as") not in KEY_ROLES:
                raise ValueError(f"axis_keys for axis {a}: `as` must be one of {KEY_ROLES}")
            if spec.get("names_from"):
                values = np.ravel(self._read_whole(path, g, spec["names_from"]))
                names = [spec.get("format", "{}").format(v) for v in values]
            else:
                names = [str(n) for n in spec["names"]]
            if len(names) != shape[a]:
                raise ValueError(f"{path.name}: axis_keys gives {len(names)} names for axis {a} of {shape[a]}")
            out[a] = (spec["as"], names)
        return out

    def _position_table(self, path: Path, g: dict, gd: dict, spec: dict, shape: tuple,
                        rec_axes: list[int]) -> tuple[np.ndarray, int | None]:
        """(one row of x y z per index of a record axis, that axis), or (one point, None) for every record."""
        if spec.get("groups"):
            if any(gd.get(k) is None for k in spec["groups"]):
                raise ValueError(f"{path.name}: the pattern gives no {spec['groups']} for a position")
            xyz = np.array([float(gd[k]) for k in spec["groups"]])
        elif spec.get("npy"):
            xyz = np.load(path.parent / spec["npy"])
        else:
            xyz = self._read_whole(path, g, spec["array"])
        xyz = np.asarray(xyz, dtype=np.float64) * float(spec.get("scale", 1))
        if xyz.size == 3:
            return xyz.ravel(), None
        axis = int(spec["axis"]) % len(shape) if spec.get("axis") is not None else (rec_axes or [None])[0]
        if axis not in rec_axes:
            raise ValueError(f"{path.name}: positions need a record axis to pick their rows (axis {axis})")
        n = shape[axis]
        if xyz.shape == (n, 3):
            return xyz, axis
        if xyz.shape == (3, n):
            return xyz.T, axis
        raise ValueError(f"{path.name}: positions of shape {xyz.shape} for {n} records along axis {axis}")

    def _position_tables(self, path: Path, g: dict, gd: dict, shape: tuple, rec_axes: list[int]) -> dict:
        """{src | rcv: position table} for the file, from `positions`."""
        specs = g.get("positions") or {}
        if set(specs) - {"src", "rcv"}:
            raise ValueError(f"positions: only src and rcv, not {sorted(set(specs) - {'src', 'rcv'})}")
        return {role: self._position_table(path, g, gd, spec, shape, rec_axes) for role, spec in specs.items()}

    @staticmethod
    def _positions(g: dict, tables: dict, idx: tuple, rec_axes: list[int], keys: dict) -> dict:
        """{src | rcv: x y z} of one record. With key_from_position, that position in cm also becomes its key in
        `keys`, so one place has one key in every file."""
        out = {}
        for role, (rows, axis) in tables.items():
            out[role] = tuple(float(v) for v in (rows if axis is None else rows[idx[rec_axes.index(axis)]]))
            spec = g["positions"][role]
            if spec.get("key_from_position"):
                keys[role] = spec.get("key_prefix", "") + "_".join(str(int(round(v * 100))) for v in out[role])
        return out

    @staticmethod
    def _key(rel: str, array: str | None, idx: tuple, g: dict) -> str:
        """The reader's key file#item: the item holds the parts that exist, the array name (with array_pattern)
        and the record index. With no part, as for `record_axes: []`, the key is the bare path (item empty)."""
        parts = ([array] if g.get("array_pattern") else []) + ([",".join(map(str, idx))] if idx else [])
        return "#".join([rel, *parts])

    @staticmethod
    def _keys(g: dict, gd: dict, array: str | None, idx: tuple, rec_axes: list[int], axis_names: dict) -> dict:
        """The record's cond, src and rcv keys: from the pattern, then from axis_keys or the record index."""
        keys = {"cond": field(gd, "cond") or (array if g.get("array_pattern") else None),
                "src": field(gd, "src"), "rcv": field(gd, "rcv")}
        if axis_names:
            for a, i in zip(rec_axes, idx):
                if a in axis_names:
                    role, names = axis_names[a]
                    keys[role] = "_".join(filter(None, [keys[role], names[i]]))
        else:                                   # the record index names the one `record_is` says
            index = ",".join(map(str, idx)) or None
            if g.get("record_is") == "src":
                keys["src"] = "-".join(filter(None, [keys["src"], index]))
            else:
                keys["rcv"] = keys["rcv"] or index
        keys["src"] = keys["src"] or g.get("src_default")
        return keys

    def _records(self, dataset, g, gi, f, rel, gd, array):
        arr, h = self._open(f, g, array)
        shape = arr.shape
        if h is not None:
            h.close()
        t_ax, c_ax, rec_axes = self._axes(g, shape)
        n_ch = len(g["channels"]) if g.get("channels") else (shape[c_ax] if c_ax is not None else 1)
        roles = g.get("roles") or (["omni"] if n_ch == 1 else [f"mic_{i}" for i in range(n_ch)])
        fs = self._fs(f, g)
        room = field(gd, "room") or g.get("room_default") or dataset.id
        sh_norm = self._sh_norm(g)
        axis_names = self._axis_names(f, g, shape, rec_axes)
        tables = self._position_tables(f, g, gd, shape, rec_axes)
        for idx in self._record_indices(g, shape, rec_axes):
            keys = self._keys(g, gd, array, idx, rec_axes, axis_names)
            pos = self._positions(g, tables, idx, rec_axes, keys)
            yield IRRecord(
                dataset_id=dataset.id, local_key=self._key(rel, array, idx, g), room_key=slug(room),
                capture_format=g.get("capture_format", "mono_omni" if n_ch == 1 else "array_raw"),
                channel_roles=tuple(roles), fs=fs,
                n_samples=min(int(shape[t_ax]), int(round(g["crop_s"] * fs))) if g.get("crop_s") else int(shape[t_ax]),
                locator={"relpath": rel, "container": g.get("container", "h5"), "group": gi,
                         "idx": list(idx), "array": array},
                condition_key=keys["cond"], src_key=keys["src"], rcv_key=keys["rcv"],
                src_pos=pos.get("src"), rcv_pos=pos.get("rcv"),
                sh_norm=sh_norm, orientation_known=bool(g.get("orientation_known", True)),
                room_label=str(room).replace("_", " "), ir_kind=g.get("ir_kind", "room"),
                extra={"reference_role": g.get("reference_role")})

    def load(self, locator: dict, root: Path):
        g = self._groups(self._params)[locator.get("group", 0)]
        path = root / locator["relpath"]
        arr, h = self._open(path, g, locator.get("array"), whole=True)
        try:
            shape = arr.shape
            t_ax, c_ax, rec_axes = self._axes(g, shape)
            sl = [slice(None)] * len(shape)
            for a, i in zip(rec_axes, locator["idx"]):
                sl[a] = i
            if g.get("crop_s"):
                sl[t_ax] = slice(0, int(round(g["crop_s"] * self._fs(path, g))))
            x = np.asarray(arr[tuple(sl)], dtype=np.float64)
            # remaining axes keep their relative order: put channels first, time last
            remaining = [a for a in range(len(shape)) if a not in rec_axes]
            if c_ax is None:
                x = x.reshape(1, -1)
            elif remaining.index(c_ax) > remaining.index(t_ax):
                x = x.T
            if g.get("channels"):
                x = np.atleast_2d(x)[list(g["channels"])]
        finally:
            if h is not None:
                h.close()
        return np.atleast_2d(x), self._fs(path, g)
