"""Adapter `csv_tree`: IRs stored as text tables, one file per IR, one column per channel, no header.

Written for TUNI-RCAIR (7 columns). The sample count is the number of non-blank lines, as load() reads them; the
channel count is the number of columns of the first line.

adapter.params:
  glob                files to read (relative to files/)
  pattern             regex on the relpath; groups room, src, rcv, cond (files that do not match are skipped)
  fs                  sampling rate (the files do not hold it)
  delimiter           column separator (default ",")
  roles               channel roles, one per column (default mic_1 ... mic_N, or omni for one column)
  capture_format      default array_raw (mono_omni for one column)
  room_default        the space when the pattern has no room group (default: the dataset id)
  ir_kind             the kind of every IR (default "room")
  ir_kind_from_regex  {regex: ir_kind} on the relpath, first match wins (before ir_kind)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import numpy as np

from rirdb.adapters.base import IRRecord, slug


def table_shape(path: Path, delimiter: str) -> tuple[int, int]:
    """(channels, samples) of a text table: the columns of its first line, and its non-blank lines."""
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    return len(lines[0].strip().split(delimiter)), len(lines)


class CsvTreeAdapter:
    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        pattern = re.compile(p["pattern"])
        delimiter = p.get("delimiter", ",")
        kinds = [(re.compile(rx), kind) for rx, kind in (p.get("ir_kind_from_regex") or {}).items()]
        for f in sorted(root.glob(p["glob"])):
            rel = f.relative_to(root).as_posix()
            m = pattern.search(rel)
            if not m:
                continue
            g = m.groupdict()
            n_ch, n = table_shape(f, delimiter)
            roles = p.get("roles") or (["omni"] if n_ch == 1 else [f"mic_{i + 1}" for i in range(n_ch)])
            if len(roles) != n_ch:
                raise ValueError(f"{rel}: {n_ch} columns, {len(roles)} roles")
            room = g.get("room") or p.get("room_default") or dataset.id
            yield IRRecord(
                dataset_id=dataset.id, local_key=rel, room_key=slug(room),
                capture_format=p.get("capture_format", "mono_omni" if n_ch == 1 else "array_raw"),
                channel_roles=tuple(roles), fs=int(p["fs"]), n_samples=n,
                locator={"relpath": rel, "container": "csv"}, condition_key=g.get("cond"),
                src_key=g.get("src"), rcv_key=g.get("rcv"), room_label=str(room),
                ir_kind=next((kind for rx, kind in kinds if rx.search(rel)), p.get("ir_kind", "room")))

    def load(self, locator: dict, root: Path) -> tuple[np.ndarray, int]:
        p = self._params
        x = np.loadtxt(root / locator["relpath"], delimiter=p.get("delimiter", ","), dtype=np.float64, ndmin=2)
        return np.ascontiguousarray(x.T), int(p["fs"])
