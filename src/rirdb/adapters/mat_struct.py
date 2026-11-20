"""IRs inside MATLAB v5 structs: a field of a struct, a cell array of loudspeakers, or a struct array.

Params, at the top level or per entry of a list `groups`:
  glob               files to read (default **/*.mat)
  exclude            regex on the relative path: files left out (default: macOS junk)
  pattern            regex on the relative path; groups room, cond, src, rcv (a key in two parts of the path takes
                     two groups, src and src_2, joined with "_", as in wav_tree). Files that do not match are left out.
  var                the top-level MATLAB variable
  field              dotted path to the IR inside `var` (or inside each element, with records: elements)
  records            what one record is:
                       file      (default) the field is one samples x channels matrix: one IR per file, item empty
                       cells     the field is a cell array with one row per loudspeaker: one record per row (item k);
                                 cell_column is the column of the matrix, name_column the column naming the source
                       elements  `var` is a struct array, one record per element (item k); src_field names the
                                 field holding the source's name, pos_fields [az, el, r] the fields of its direction
                                 (degrees) and distance (m) from the receiver, which is then at the origin
  time_axis          axis of the matrix holding samples (default 0); the other axis holds the channels
  fs                 sampling rate, or fs_field: dotted path to it inside `var` (or inside each element)
  roles              channel roles: a list, or {n_channels: list}; roles_var: a cell variable naming the channels
  capture_format     the layout, or channel_formats: {n_channels: capture_format}
  sh_norm            FuMa | SN3D | N3D, set on Ambisonic layouts (foa_*, hoa_*) only
  orientation_known  false when the array's x axis is not known to point at the sources (default true)
  reference_role, rcv (a receiver key for every record), room_default, ir_kind (or ir_kind_from_regex:
  {regex on the relative path: ir_kind}, first match wins), preferred (default true)
Indexing reads each file once; load reads the file again and takes one record.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import numpy as np

from rirdb.adapters.base import IRRecord, slug
from rirdb.adapters.custom import spherical_to_xyz
from rirdb.adapters.wav_tree import DEFAULT_EXCLUDE, field

AMBISONIC = ("foa_", "hoa_")


def _get(obj, dotted: str):
    """A field of a MATLAB struct read by scipy (struct_as_record=False), following a dotted path."""
    for part in [p for p in dotted.split(".") if p]:
        obj = getattr(obj, part)
    return obj


def _scalar(value) -> float:
    return float(np.ravel(np.asarray(value))[0])


class MatStructAdapter:
    def _groups(self, params: dict) -> list[dict]:
        return params.get("groups") or [params]

    @staticmethod
    def _read(path: Path, g: dict) -> dict:
        """The file's `var` (and `roles_var`), structs as objects with attributes."""
        import scipy.io

        names = [g["var"]] + ([g["roles_var"]] if g.get("roles_var") else [])
        return scipy.io.loadmat(str(path), variable_names=names, squeeze_me=True, struct_as_record=False)

    @staticmethod
    def _items(top, g: dict) -> list[tuple[str | None, object, np.ndarray]]:
        """(source name or None, the struct that `fs_field` is read from, the IR matrix) for each record."""
        how = g.get("records", "file")
        if how == "file":
            return [(None, top, np.asarray(_get(top, g["field"])))]
        if how == "cells":
            cells = np.asarray(_get(top, g["field"]), dtype=object)
            if cells.ndim == 1:                         # a single row, squeezed to 1-d
                cells = cells.reshape(1, -1)
            name = g.get("name_column")
            return [(None if name is None else str(row[name]).strip(), top, np.asarray(row[g["cell_column"]]))
                    for row in cells]
        if how == "elements":
            src = g.get("src_field")
            return [(None if src is None else str(_get(e, src)).strip(), e, np.asarray(_get(e, g["field"])))
                    for e in np.atleast_1d(top)]
        raise ValueError(f"records {how!r}: expected file, cells or elements")

    @staticmethod
    def _fs(g: dict, struct) -> int:
        return int(g["fs"]) if g.get("fs") else int(_scalar(_get(struct, g["fs_field"])))

    @staticmethod
    def _roles(g: dict, d: dict, n_ch: int, rel: str) -> list[str]:
        """The channel roles: `roles` (a list, or one per channel count), `roles_var`, or omni / mic_0 ...."""
        roles = g.get("roles")
        if isinstance(roles, dict):
            roles = roles.get(n_ch) or roles.get(str(n_ch))
        if roles is None and g.get("roles_var") in d:
            roles = [str(r).strip() for r in np.atleast_1d(d[g["roles_var"]])]
        if roles is None:
            roles = ["omni"] if n_ch == 1 else [f"mic_{i}" for i in range(n_ch)]
        if len(roles) != n_ch:
            raise ValueError(f"{rel}: {n_ch} channels, but the roles name {len(roles)}")
        return list(roles)

    @staticmethod
    def _format(g: dict, n_ch: int) -> str:
        formats = {int(k): v for k, v in (g.get("channel_formats") or {}).items()}
        return g.get("capture_format") or formats.get(n_ch) or ("mono_omni" if n_ch == 1 else "array_raw")

    @staticmethod
    def _ir_kind(g: dict, dataset, rel: str) -> str:
        for rx, kind in (g.get("ir_kind_from_regex") or {}).items():
            if re.search(rx, rel):
                return kind
        return g.get("ir_kind") or (dataset.ir_kinds[0] if dataset.ir_kinds else "room")

    @staticmethod
    def _source_position(g: dict, struct) -> tuple | None:
        """x y z of the source from the element's direction and distance, the receiver being at the origin."""
        if g.get("records") != "elements" or not g.get("pos_fields"):
            return None
        az_el_r = np.array([[_scalar(_get(struct, name)) for name in g["pos_fields"]]])
        return tuple(float(v) for v in spherical_to_xyz(az_el_r)[0])

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        for gi, g in enumerate(self._groups(dataset.adapter.params)):
            pattern = re.compile(g["pattern"]) if g.get("pattern") else None
            exclude = re.compile(g.get("exclude", DEFAULT_EXCLUDE))
            for f in sorted(root.glob(g.get("glob", "**/*.mat"))):
                rel = f.relative_to(root).as_posix()
                m = pattern.search(rel) if pattern else None
                if exclude.search(rel) or (pattern is not None and m is None):
                    continue
                yield from self._file_records(dataset, g, gi, f, rel, m.groupdict() if m else {})

    def _file_records(self, dataset, g: dict, gi: int, path: Path, rel: str, gd: dict) -> Iterator[IRRecord]:
        d = self._read(path, g)
        room = field(gd, "room") or g.get("room_default") or dataset.id
        t_ax = int(g.get("time_axis", 0))
        one_per_file = g.get("records", "file") == "file"
        for k, (name, struct, x) in enumerate(self._items(d[g["var"]], g)):
            n_ch = 1 if x.ndim == 1 else int(x.shape[1 - t_ax])
            fmt = self._format(g, n_ch)
            src_pos = self._source_position(g, struct)
            yield IRRecord(
                dataset_id=dataset.id, local_key=rel if one_per_file else f"{rel}#{k}", room_key=slug(room),
                capture_format=fmt, channel_roles=tuple(self._roles(g, d, n_ch, rel)), fs=self._fs(g, struct),
                n_samples=int(x.shape[t_ax] if x.ndim > 1 else x.size),
                locator={"relpath": rel, "container": "mat_struct", "group": gi, "k": k},
                condition_key=field(gd, "cond"), src_key="_".join(filter(None, [field(gd, "src"), name])) or None,
                rcv_key=field(gd, "rcv") or g.get("rcv"), src_pos=src_pos,
                rcv_pos=(0.0, 0.0, 0.0) if src_pos else None,
                sh_norm=g.get("sh_norm") if fmt.startswith(AMBISONIC) else None,
                orientation_known=bool(g.get("orientation_known", True)),
                room_label=str(room).replace("_", " "), ir_kind=self._ir_kind(g, dataset, rel),
                extra={"reference_role": g.get("reference_role"), "preferred": bool(g.get("preferred", True))})

    def load(self, locator: dict, root: Path):
        g = self._groups(self._params)[locator.get("group", 0)]
        d = self._read(root / locator["relpath"], g)
        _, struct, x = self._items(d[g["var"]], g)[locator["k"]]
        x = np.asarray(x, dtype=np.float64)
        x = x.reshape(1, -1) if x.ndim == 1 else np.moveaxis(x, int(g.get("time_axis", 0)), -1)
        return np.ascontiguousarray(x), self._fs(g, struct)
