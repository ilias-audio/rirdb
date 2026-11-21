"""Generic adapter for datasets stored as audio files in a directory tree.

Registry parameters (adapter.params), all optional:
  glob:        file pattern relative to files/ (default "**/*.wav")
  exclude:     regex on the relative path (default: macOS junk)
  pattern:     regex with named groups room, src, rcv, cond, label (on the relpath); a key in two parts of the
               path takes two groups, src and src_2 (joined with "_"), and alternatives may each name a group
  room_default: room used when the pattern has no room match
  room_from_regex: {regex: room} on the relpath, over every other rule (first match wins): a file published under
               the wrong room's name (sonic_palimpsest's KM184 pair of the cellar and the billiard room)
  room_from:   "pattern" | "parent" | "file" | "dataset"  (default: pattern if it
               has a room group, else "file": every file is its own space)
  category_from: "parent" | "grandparent" | None  -> category_hint
  roles:       {n_channels: [role, ...]} e.g. {1: [omni], 2: [SL, SR], 4: [W, X, Y, Z]}
  capture_format: overrides the per-channel-count default
  channel_formats: {n_channels: capture_format}
  sh_norm:     FuMa | SN3D | N3D (for B-format files)
  orientation_known: bool (default true)
  ir_kind_from_regex: {regex: ir_kind} applied to the relpath (first match wins)
  channels:    only index files with these channel counts, e.g. [1, 2]
  reference_roles: {n_channels: role or "mean"} analysed reference per channel count
  preferred_regex: relpaths matching it are the preferred representation (others kept, not preferred)
  room_level:  the folder of the path that names the space (0 = the first folder): for a reader written by
               scripts/onboard/guess.py, where a folder names the room and no pattern is needed
  condition_level: the folder that names the condition (a state of the room)
  join:        regex with a group `ch` (on the relpath): files that differ only in `ch` are the channels of
               one IR, in the order of `ch` (numbers by value); a channel named in two parts of the name takes ch and ch_2, e.g. "-(?P<ch>[12])\\.wav$" for ear files
  format_from_regex: {regex: capture_format} on the relpath, before channel_formats (first match wins)
  max_s:       leave out files longer than this (seconds): sweep or scene recordings, not IRs
  tokens:      true: take the source and receiver from the file name where the pattern gives none, as written
               S2, Src2, LS2, Spk2 (source) and R5, Rcv5, Mic5, M5, Pos5, P5 (receiver)
  layout_from_regex: {regex: {n_channels: {roles, format, sh_norm, orientation_known}}}, for conventions that
               differ inside one dataset (farina_collection's B-format in two channel orders, W X + Y Z or
               W Y + X Z; an ORTF pair among ear files). The regex is searched on the relpath of the IR's (first)
               file; the first one that matches and has an entry for the IR's channel count sets what its entry
               gives, over what the parameters above gave. roles must have one name per channel. An entry without
               sh_norm keeps the dataset's sh_norm for a first-order format
  segments:    each file holds this many IRs of equal length, one after the other in time (default 1). Each part
               is a record with the item 0, 1, ... (farina_car_irs: Aurora "2x2" files hold the binaural IR of the
               left loudspeaker, then that of the right one)
  segment_names: the source of each part, in order, e.g. [L, R] (default 1, 2, ...); joined after the pattern's
               source with "_" when the pattern has one
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from rirdb.adapters.base import IRRecord, slug

DEFAULT_EXCLUDE = r"(^|/)(__MACOSX|\._)|/\.DS_Store$"
DEFAULT_ROLES = {1: ["omni"], 2: ["SL", "SR"], 4: ["W", "X", "Y", "Z"]}
DEFAULT_FORMATS = {1: "mono_omni", 2: "stereo", 4: "foa_fuma"}
# File-name tokens for `tokens: true`: a letter code not glued to a word before it, then a number.
SRC_TOKEN = re.compile(r"(?<![a-z])(?:src|source|spk|speaker|ls|s)[ _-]?(\d+)(?![0-9])", re.I)
RCV_TOKEN = re.compile(r"(?<![a-z])(?:rcv|rec|receiver|mic|pos|position|r|m|p)[ _-]?(\d+)(?![0-9])", re.I)


def field(g: dict, name: str) -> str | None:
    """A key from the pattern's groups `name`, `name_2`, ... (those that matched), joined with "_"."""
    parts = [v for k, v in g.items() if v and (k == name or re.fullmatch(rf"{name}_\d", k))]
    return "_".join(parts) or None


def channel_order(ch: str):
    return (0, int(ch), "") if ch.isdigit() else (1, 0, ch.lower())


def token(rx: re.Pattern, stem: str) -> str | None:
    """The first token of a file name, as written without separators: "LS 16" -> "LS16"."""
    m = rx.search(stem)
    return re.sub(r"[ _-]", "", m.group(0)) if m else None


def with_layouts(records: Iterator[IRRecord], layouts: dict, sh_norm: str | None) -> Iterator[IRRecord]:
    """Roles, format, sh_norm and orientation_known from the first entry of `layout_from_regex` that fits the IR.
    `sh_norm` is the dataset's own (the parameter), kept for a first-order format when the entry gives none."""
    rules = [(re.compile(rx), {int(n): entry for n, entry in by_n.items()}) for rx, by_n in layouts.items()]
    for r in records:
        first = r.locator.get("relpath") or r.locator["files"][0]
        n = len(r.channel_roles)
        entry = next((by_n[n] for rx, by_n in rules if rx.search(first) and n in by_n), None)
        if entry is None:
            yield r
            continue
        roles = tuple(entry.get("roles") or r.channel_roles)
        if len(roles) != n:
            raise ValueError(f"{first}: {n} channels, but layout_from_regex gives the roles {list(roles)}")
        fmt = entry.get("format", r.capture_format)
        norm = entry["sh_norm"] if "sh_norm" in entry else (sh_norm if fmt.startswith("foa") else None)
        yield replace(r, capture_format=fmt, channel_roles=roles, sh_norm=norm,
                      orientation_known=bool(entry.get("orientation_known", r.orientation_known)))


def in_segments(records: Iterator[IRRecord], k: int, names: list | None) -> Iterator[IRRecord]:
    """Each record becomes k records, one per equal part of the file in time (the `segments` parameter)."""
    names = [str(n) for n in (names or range(1, k + 1))]
    if len(names) != k:
        raise ValueError(f"segments is {k}, but segment_names has {len(names)} names")
    for r in records:
        for i, name in enumerate(names):
            yield replace(r, local_key=f"{r.local_key}#{i}", n_samples=r.n_samples // k,
                          src_key=f"{r.src_key}_{name}" if r.src_key else name,
                          locator={**r.locator, "segment": i, "segments": k})


class WavTreeAdapter:
    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        records = self._file_records(dataset, root)
        if p.get("layout_from_regex"):
            records = with_layouts(records, p["layout_from_regex"], p.get("sh_norm"))
        if int(p.get("segments", 1)) > 1:
            records = in_segments(records, int(p["segments"]), p.get("segment_names"))
        return records

    def _file_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        """One record per file, or per set of files joined by `join`."""
        p = dataset.adapter.params
        exclude = re.compile(p.get("exclude", DEFAULT_EXCLUDE))
        pattern = re.compile(p["pattern"]) if p.get("pattern") else None
        room_from = p.get("room_from") or ("pattern" if pattern is not None and "room" in pattern.groupindex
                                           else "level" if p.get("room_level") is not None else "file")
        roles_map = {int(k): v for k, v in (p.get("roles") or {}).items()} or DEFAULT_ROLES
        fmt_map = {int(k): v for k, v in (p.get("channel_formats") or {}).items()}
        kinds = [(re.compile(rx), kind) for rx, kind in (p.get("ir_kind_from_regex") or {}).items()]
        room_rx = [(re.compile(rx), r) for rx, r in (p.get("room_from_regex") or {}).items()]
        fmt_rx = [(re.compile(rx), f_) for rx, f_ in (p.get("format_from_regex") or {}).items()]
        default_kind = dataset.ir_kinds[0] if dataset.ir_kinds else "room"
        files = sorted(root.glob(p.get("glob", "**/*.wav")))
        groups = self.joined(files, root, exclude, p.get("join"))
        for rels in groups:
            rel = rels[0]
            try:
                info = sf.info(str(root / rel))
            except RuntimeError:
                continue
            if p.get("max_s") and info.duration > p["max_s"]:
                continue
            m = pattern.search(rel) if pattern else None
            if pattern is not None and m is None:
                continue
            g = m.groupdict() if m else {}
            stem = Path(rel).stem
            if room_from == "pattern":
                room = field(g, "room") or p.get("room_default") or "unknown"
            elif room_from == "parent":
                room = Path(rel).parent.name
            elif room_from == "dataset":
                room = dataset.id
            elif room_from == "level":
                folders = Path(rel).parts[:-1]
                room = folders[p["room_level"]] if p["room_level"] < len(folders) else p.get("room_default") or dataset.id
            else:
                room = stem
            room = next((r for rx, r in room_rx if rx.search(rel)), room)
            n_ch = info.channels * len(rels)
            if p.get("channels") and n_ch not in p["channels"]:
                continue
            if len(rels) > 1:                       # joined files: the roles given, else the channel names
                roles = (p.get("roles") or {}).get(str(n_ch)) or (p.get("roles") or {}).get(n_ch) \
                    or self.channel_names(rels, p["join"])
            else:
                roles = roles_map.get(n_ch) or [f"ch{i}" for i in range(n_ch)]
            if len(roles) != n_ch:
                roles = [f"ch{i}" for i in range(n_ch)]
            fmt = next((f_ for rx, f_ in fmt_rx if rx.search(rel)), None) or p.get("capture_format") or fmt_map.get(n_ch) \
                or ("array_raw" if len(rels) > 1 and n_ch > 2 else DEFAULT_FORMATS.get(n_ch, "array_raw"))   # joined: an array
            cat_from = p.get("category_from")
            category = None
            if cat_from == "parent":
                category = Path(rel).parent.name
            elif cat_from == "grandparent":
                category = Path(rel).parent.parent.name
            kind = next((k for rx, k in kinds if rx.search(rel)), default_kind)
            cond, src, rcv = field(g, "cond"), field(g, "src"), field(g, "rcv")
            folders = Path(rel).parts[:-1]
            if cond is None and p.get("condition_level") is not None and p["condition_level"] < len(folders):
                cond = folders[p["condition_level"]]
            if p.get("tokens"):
                src, rcv = src or token(SRC_TOKEN, Path(rel).stem), rcv or token(RCV_TOKEN, Path(rel).stem)
            yield IRRecord(
                dataset_id=dataset.id,
                local_key="|".join(rels),
                room_key=slug(room),
                capture_format=fmt,
                channel_roles=tuple(roles),
                fs=int(info.samplerate),
                n_samples=int(info.frames),
                locator={"relpath": rel, "container": "audio"} if len(rels) == 1
                else {"files": rels, "container": "audio_pair"},
                condition_key=cond,
                src_key=src,
                rcv_key=rcv,
                sh_norm=p.get("sh_norm") if fmt.startswith("foa") else None,
                orientation_known=bool(p.get("orientation_known", True)),
                room_label=field(g, "label") or room,
                category_hint=category,
                ir_kind=kind,
                extra={"subtype": info.subtype,
                       "reference_role": (p.get("reference_roles") or {}).get(n_ch) or (p.get("reference_roles") or {}).get(str(n_ch)),
                       "preferred": bool(re.search(p["preferred_regex"], rel)) if p.get("preferred_regex") else True},
            )

    @staticmethod
    def channel_names(rels: list[str], join: str) -> list[str]:
        rx = re.compile(join)
        return ["_".join(v for k, v in rx.search(r).groupdict().items() if v and re.fullmatch(r"ch(_\d)?", k))
                for r in rels]

    @staticmethod
    def joined(files: list[Path], root: Path, exclude: re.Pattern, join: str | None) -> list[list[str]]:
        """The IRs as lists of files: one file each, or (with `join`) the files that differ only in the channel."""
        rels = [f.relative_to(root).as_posix() for f in files if f.is_file()]
        rels = [r for r in rels if not exclude.search(r)]
        if not join:
            return [[r] for r in rels]
        rx, sets = re.compile(join), {}
        for r in rels:
            m = rx.search(r)
            key = r
            if m:                                   # every group ch, ch_2 ... becomes "{ch}"
                for k in sorted((k for k in rx.groupindex if re.fullmatch(r"ch(_\d)?", k) and m[k]), key=m.start,
                                reverse=True):
                    key = key[:m.start(k)] + "{ch}" + key[m.end(k):]
            first = next((m[k] for k in rx.groupindex if m and m[k] and re.fullmatch(r"ch(_\d)?", k)), "")
            sets.setdefault(key, []).append((channel_order(first), r))
        return [[r for _, r in sorted(v)] for v in sets.values()]

    def load(self, locator: dict, root: Path) -> tuple[np.ndarray, int]:
        x, fs = self.read(locator, root)
        if "segments" in locator:                   # one part of the file in time
            n = x.shape[1] // locator["segments"]
            x = x[:, locator["segment"] * n:(locator["segment"] + 1) * n]
        return x, fs

    @staticmethod
    def read(locator: dict, root: Path) -> tuple[np.ndarray, int]:
        """The whole file, or the joined files, as (channels, time)."""
        if "files" in locator:
            chans = [sf.read(str(root / f), dtype="float64", always_2d=True) for f in locator["files"]]
            n = min(c[0].shape[0] for c in chans)
            return np.vstack([c[0][:n].T for c in chans]), int(chans[0][1])
        x, fs = sf.read(str(root / locator["relpath"]), dtype="float64", always_2d=True)
        return x.T, int(fs)
