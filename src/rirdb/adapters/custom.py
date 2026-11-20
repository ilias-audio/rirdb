"""Adapters for datasets whose layout needs code rather than a filename regex."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from rirdb.adapters.base import IRRecord, slug
from rirdb.adapters.wav_tree import DEFAULT_EXCLUDE, field


# ---------------------------------------------------------------- OpenAIR
class OpenAirAdapter:
    """OpenAIR: files/<space>/<format folder>/<ir>.wav.

    Only IR folders are indexed (B-format, mono, stereo, 5.1, HOA, averaged or
    individual IR takes, scale models); sweeps, auralizations, convolution
    examples, pictures and data tables are skipped. Positions are often
    published in several formats (mono is usually W of the B-format), so one
    format per space is marked `preferred` (B-format > mono > stereo > other)
    for corpus statistics.
    """

    SKIP = re.compile(r"data tables|example|auraliz|sweep|ess|video|pic|image|virtual|recreation", re.I)
    PRIORITY = ("foa_fuma", "mono_omni", "stereo")

    @staticmethod
    def _format(folder: str, n_ch: int) -> tuple[str, list[str], str]:
        f = folder.lower()
        kind = "scale_model" if "scalemodel" in f.replace("_", "").replace("-", "") else "room"
        if n_ch == 4 and ("b-format" in f or "bformat" in f):
            return "foa_fuma", ["W", "X", "Y", "Z"], kind
        if n_ch == 16:
            # 3rd-order Ambisonics; normalisation not documented -> no Y role, so JLF is not computed
            return "hoa_sh", ["W"] + [f"ACN{i}" for i in range(1, 16)], kind
        if n_ch == 1:
            return "mono_omni", ["omni"], kind
        if n_ch == 2:
            return "stereo", ["SL", "SR"], kind
        if n_ch == 6:
            return "array_raw", ["sur_L", "sur_R", "sur_C", "sur_LFE", "sur_Ls", "sur_Rs"], kind
        return "array_raw", [f"ch{i}" for i in range(n_ch)], kind

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        exclude = re.compile(DEFAULT_EXCLUDE)
        recs: list[IRRecord] = []
        for f in sorted(root.glob("*/*/*.wav")) + sorted(root.glob("*/*/*.WAV")):
            rel = f.relative_to(root).as_posix()
            space, folder, _ = rel.split("/", 2)
            if exclude.search(rel) or self.SKIP.search(folder):
                continue
            try:
                info = sf.info(str(f))
            except RuntimeError:
                continue
            if info.frames / info.samplerate > 30:        # not an IR (long recordings)
                continue
            fmt, roles, kind = self._format(folder, info.channels)
            recs.append(IRRecord(
                dataset_id=dataset.id, local_key=rel, room_key=slug(space), capture_format=fmt,
                channel_roles=tuple(roles), fs=int(info.samplerate), n_samples=int(info.frames),
                locator={"relpath": rel, "container": "audio"}, sh_norm="FuMa" if fmt == "foa_fuma" else None,
                orientation_known=False, room_label=space.replace("-", " "), ir_kind=kind,
                condition_key=slug(folder), rcv_key=Path(rel).stem,
                extra={"subtype": info.subtype, "openair_folder": folder,
                       "reference_role": "sur_C" if fmt == "array_raw" and info.channels == 6 else None}))
        by_space = defaultdict(set)
        for r in recs:
            by_space[r.room_key].add(r.capture_format)
        for r in recs:
            fmts = by_space[r.room_key]
            best = next((p for p in self.PRIORITY if p in fmts), sorted(fmts)[0])
            r.extra["preferred"] = r.capture_format == best and r.ir_kind == "room"
            yield r

    def load(self, locator: dict, root: Path):
        x, fs = sf.read(str(root / locator["relpath"]), dtype="float64", always_2d=True)
        return x.T, int(fs)


# ---------------------------------------------------------------- Aachen AIR
class AirMatAdapter:
    """Aachen AIR 1.4 .mat files, one ear per file; pairs are joined into one record.

    air_binaural_{room}_{channel 0=right 1=left}_{head 0/1}_{rir_no}[_{azimuth}][_{x}].mat
    air_phone_[BT_]{room}_{hhp|hfrp}_{channel}.mat (dual-mic phone mock-up)
    """

    BIN = re.compile(r"air_binaural_(?P<room>[a-z_]+?)_(?P<ch>[01])_(?P<head>[01])_(?P<rir>\d+)(?P<rest>(?:_\d+)*)\.mat$")
    PHONE = re.compile(r"air_phone_(?P<bt>BT_)?(?P<room>[a-z0-9_]+?)_(?P<pos>hhp|hfrp)_(?P<ch>[01])\.mat$")

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        groups: dict[tuple, dict] = defaultdict(dict)
        meta: dict[tuple, dict] = {}
        for f in sorted(root.rglob("air_*.mat")):
            rel = f.relative_to(root).as_posix()
            if m := self.BIN.search(f.name):
                key = ("binaural", m["room"], m["head"], m["rir"], m["rest"])
                groups[key]["L" if m["ch"] == "1" else "R"] = rel
                meta[key] = m.groupdict()
            elif m := self.PHONE.search(f.name):
                key = ("phone", m["room"], m["pos"], m["bt"] or "", "")
                groups[key][f"mic_{m['ch']}"] = rel
                meta[key] = m.groupdict()
        for key, chans in sorted(groups.items()):
            kind, room = key[0], key[1]
            g = meta[key]
            if kind == "binaural":
                if set(chans) != {"L", "R"}:
                    continue
                head = g["head"] == "1"
                roles = ("L", "R") if head else ("SL", "SR")
                fmt = "binaural" if head else "stereo"
                files = [chans["L"], chans["R"]]
                rest = [p for p in g["rest"].split("_") if p]
                cond = f"head{g['head']}"
                rcv = f"rir{g['rir']}" + (f"_az{rest[0]}" if rest else "")
            else:
                roles = ("mic_0", "mic_1")
                fmt = "array_raw"
                files = [chans.get("mic_0"), chans.get("mic_1")]
                if None in files:
                    continue
                cond = f"phone_{g['pos']}{'_BT' if g['bt'] else ''}"
                rcv = g["pos"]
            h = self._read(root / files[0])
            yield IRRecord(
                dataset_id=dataset.id, local_key="|".join(files), room_key=slug(room), capture_format=fmt,
                channel_roles=roles, fs=h[1], n_samples=h[0].size,
                locator={"files": files, "container": "air_mat"}, condition_key=cond, rcv_key=rcv,
                room_label=room.replace("_", " "), extra={"air_type": kind})

    @staticmethod
    def _read(path: Path) -> tuple[np.ndarray, int]:
        import scipy.io

        m = scipy.io.loadmat(str(path), squeeze_me=True, struct_as_record=False)
        fs = int(getattr(m["air_info"], "fs", 48000))
        return np.asarray(m["h_air"], dtype=np.float64).ravel(), fs

    def load(self, locator: dict, root: Path):
        chans = [self._read(root / f) for f in locator["files"]]
        n = min(c[0].size for c in chans)
        return np.vstack([c[0][:n] for c in chans]), chans[0][1]


# ---------------------------------------------------------------- OpenSLR 28
class OpenSLR28Adapter:
    """Real RIRs of OpenSLR 28 (16 kHz): REVERB 2014 and RWCP. AIR copies are
    skipped (the full-rate originals are the aachen_air dataset)."""

    RVB = re.compile(r"RVB2014_type(?P<t>\d)_rir_(?P<room>[a-z]+room\d)_(?P<dist>near|far)_angl(?P<ang>[ab])\.wav$")
    RWCP = re.compile(r"RWCP_type(?P<t>\d)_rir_(?P<array>[a-z]+)_(?P<room>[a-z0-9]+)_imp(?P<ang>\d+|_rev)\.wav$")
    RWCP_LR = re.compile(r"RWCP_type4_rir_(?P<ang>[mp]?\d+)(?P<ch>[lr])\.wav$")   # left/right pairs per angle

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        base = root / "RIRS_NOISES" / "real_rirs_isotropic_noises"
        pairs: dict[str, dict] = defaultdict(dict)
        for f in sorted(base.glob("RWCP_type4_rir_*.wav")):
            if m := self.RWCP_LR.search(f.name):
                pairs[m["ang"]][m["ch"]] = f.relative_to(root).as_posix()
        for ang, lr in sorted(pairs.items()):
            if set(lr) != {"l", "r"}:
                continue
            info = sf.info(str(root / lr["l"]))
            yield IRRecord(
                dataset_id=dataset.id, local_key=f"{lr['l']}|{lr['r']}", room_key="rwcp-type4",
                capture_format="stereo", channel_roles=("SL", "SR"), fs=int(info.samplerate),
                n_samples=int(info.frames), locator={"files": [lr["l"], lr["r"]], "container": "audio_pair"},
                condition_key="type4", src_key=f"az{ang}", room_label="rwcp type4", category_hint="rwcp",
                extra={"subtype": info.subtype, "family": "rwcp", "note": "left/right channel pair"})
        for f in sorted(base.glob("*.wav")):
            rel = f.relative_to(root).as_posix()
            if m := self.RVB.search(f.name):
                room, family = m["room"], "reverb2014"
                cond, src, rcv, kind = f"type{m['t']}", f"angle_{m['ang']}", m["dist"], "room"
            elif m := self.RWCP.search(f.name):
                room, family = m["room"], "rwcp"
                cond, src, rcv = f"type{m['t']}_{m['array']}", f"az{m['ang']}", m["array"]
                kind = "anechoic" if room == "ane" else "room"
            else:
                continue
            info = sf.info(str(f))
            roles = ["omni"] if info.channels == 1 else [f"mic_{i}" for i in range(info.channels)]
            yield IRRecord(
                dataset_id=dataset.id, local_key=rel, room_key=slug(f"{family}-{room}"),
                capture_format="mono_omni" if info.channels == 1 else "array_raw",
                channel_roles=tuple(roles), fs=int(info.samplerate), n_samples=int(info.frames),
                locator={"relpath": rel, "container": "audio"}, condition_key=cond, src_key=src, rcv_key=rcv,
                room_label=f"{family} {room}", category_hint=family, ir_kind=kind,
                extra={"subtype": info.subtype, "family": family})

    def load(self, locator: dict, root: Path):
        if "files" in locator:
            chans = [sf.read(str(root / f), dtype="float64", always_2d=True) for f in locator["files"]]
            n = min(c[0].shape[0] for c in chans)
            return np.vstack([c[0][:n, 0] for c in chans]), int(chans[0][1])
        x, fs = sf.read(str(root / locator["relpath"]), dtype="float64", always_2d=True)
        return x.T, int(fs)


def sofa_axes(d) -> str:
    """The axes of a SOFA Data.IR as letters, from its netCDF dimension names: "MRN", "MRNE" or "MREN"."""
    try:
        names = "".join(d.dims[k][0].name.strip("/")[:1].upper() for k in range(d.ndim))
    except Exception:          # no dimension scales attached: the SOFA 1 order
        names = ""
    if sorted(names) == sorted("MRNE"[:d.ndim]) and names[:2] == "MR":
        return names
    return "MRNE"[:d.ndim]


def sofa_shape(d) -> tuple:
    """Data.IR's shape as (M, R, N) or (M, R, N, E), whatever order the file stores its axes in."""
    order = sofa_axes(d)
    return tuple(d.shape[order.index(a)] for a in "MRNE"[:d.ndim])


def spherical_to_xyz(p: np.ndarray) -> np.ndarray:
    """SOFA spherical positions (azimuth and elevation in degrees, distance in metres) -> x, y, z in metres."""
    az, el, r = np.radians(p[:, 0]), np.radians(p[:, 1]), p[:, 2]
    return np.stack([r * np.cos(el) * np.cos(az), r * np.cos(el) * np.sin(az), r * np.sin(el)], axis=1)


def sofa_coordinate_type(h, name: str) -> str:
    """The Type attribute of a SOFA position variable: "cartesian" (the default) or "spherical"."""
    value = h[name].attrs.get("Type", b"cartesian")
    return value.decode() if isinstance(value, bytes) else str(value)


def sofa_source_position(h) -> np.ndarray | None:
    """SourcePosition as stored, or None when the file has none or only zeros (source positions not published)."""
    if "SourcePosition" not in h:
        return None
    src = np.asarray(h["SourcePosition"])
    return src if np.any(src) else None


def sofa_positions(h, p: dict, g: dict) -> tuple[np.ndarray | None, np.ndarray | None]:
    """The source and listener positions of one SOFA file, in metres: (src, lis).

    h is the open file, p the reader's params for it and g the groups its file-name pattern matched. src has shape
    (M or 1, E or 1, 3) and lis (M or 1, 3): a row per measurement, or one row for all (and for sources a column
    per emitter, or one column for all). Either is None when the file publishes none. An all-zero SourcePosition
    is unpublished; a spherical SourcePosition is around the listener. With p["emitter_positions"], each emitter
    is placed at SourcePosition + EmitterPosition (emitter_positions). The pattern groups rcv_x, rcv_y and rcv_z
    replace those coordinates of the listener (listener_from_name). With p["ignore_positions"], both are None.
    """
    if p.get("ignore_positions"):
        return None, None
    M = sofa_shape(h["Data.IR"])[0]
    lis = np.asarray(h["ListenerPosition"]) if "ListenerPosition" in h else None
    if lis is not None and lis.ndim != 2:
        lis = None
    lis = listener_from_name(lis, g)
    src = emitter_positions(h, lis) if p.get("emitter_positions") else source_positions(h, lis)
    if src is not None and len(src) != M:
        src = src[:1]
    if lis is not None and len(lis) != M:
        lis = lis[:1]
    return src, lis


def name_number(text: str) -> float:
    """A number as a file name writes it, with "pt" for the decimal point: "-0pt5" -> -0.5."""
    return float(text.replace("pt", "."))


def listener_from_name(lis: np.ndarray | None, g: dict) -> np.ndarray | None:
    """ListenerPosition with the coordinates that the file name states put in its place: the pattern groups
    rcv_x, rcv_y and rcv_z (written with name_number's "pt"). Without ListenerPosition, the name must state all
    three, or the listener stays unknown."""
    stated = {axis: name_number(g[f"rcv_{name}"]) for axis, name in enumerate("xyz") if g.get(f"rcv_{name}")}
    if not stated:
        return lis
    if lis is None:
        if len(stated) < 3:
            return None
        lis = np.zeros((1, 3))
    lis = np.array(lis, dtype=float)
    for axis, value in stated.items():
        lis[:, axis] = value
    return lis


def source_positions(h, lis: np.ndarray | None) -> np.ndarray | None:
    """SourcePosition in metres, shape (rows of SourcePosition, 1, 3), or None when not published (missing, all
    zero or not 2-D). A spherical SourcePosition is around the listener lis."""
    src = sofa_source_position(h)
    if src is None or src.ndim != 2:
        return None
    xyz = src
    if sofa_coordinate_type(h, "SourcePosition") == "spherical":
        xyz = (spherical_to_xyz(src) + (lis if lis is not None else 0))[:len(src)]
    return xyz[:, None, :]


def emitter_positions(h, lis: np.ndarray | None) -> np.ndarray:
    """The position of every emitter (loudspeaker) of a SOFA file, shape (rows of SourcePosition, E, 3), in metres.

    An emitter is at SourcePosition + EmitterPosition. EmitterPosition is (E, 3, 1) or (E, 3), cartesian or
    spherical around the source. Here an all-zero (or missing) SourcePosition is the origin, not unpublished; a
    spherical SourcePosition is around the listener lis. The source must not be rotated (SourceView (1, 0, 0) or
    none) and the emitters must not move with the measurement: either raises ValueError.
    """
    if "SourceView" in h:
        view = np.asarray(h["SourceView"], dtype=float).reshape(-1, 3)
        if sofa_coordinate_type(h, "SourceView") == "spherical":
            view = spherical_to_xyz(view)
        if not np.allclose(view / np.linalg.norm(view, axis=1, keepdims=True), [1, 0, 0]):
            raise ValueError(f"SourceView {view[0]} is rotated: emitter_positions reads unrotated sources only")
    emitters = np.asarray(h["EmitterPosition"], dtype=float)
    if emitters.ndim == 3:
        if emitters.shape[2] > 1:
            raise ValueError(f"EmitterPosition {emitters.shape} changes with the measurement: not read")
        emitters = emitters[:, :, 0]
    if sofa_coordinate_type(h, "EmitterPosition") == "spherical":
        emitters = spherical_to_xyz(emitters)
    src = np.asarray(h["SourcePosition"], dtype=float) if "SourcePosition" in h else np.zeros((1, 3))
    if np.any(src) and sofa_coordinate_type(h, "SourcePosition") == "spherical":
        src = (spherical_to_xyz(src) + (lis if lis is not None else 0))[:len(src)]
    return src[:, None, :] + emitters[None, :, :]


def sofa_room(p: dict, g: dict, rel: str, dataset_id: str) -> str:
    """The room of one SOFA file, as the params' room_from says (pattern, dataset, parent folder or file name)."""
    room_from = p.get("room_from") or ("pattern" if field(g, "room") else "file")
    if room_from == "pattern":
        return field(g, "room")
    if room_from == "dataset":
        return p.get("room_default") or dataset_id
    if room_from == "parent":
        return Path(rel).parent.name
    return Path(rel).stem


def first_per_source(src: np.ndarray) -> list[int]:
    """The first measurement m at each distinct source position (rounded to the millimetre)."""
    firsts: dict = {}
    for m in range(len(src)):
        firsts.setdefault(tuple(np.round(src[m], 3)), m)
    return sorted(firsts.values())


def source_position_name(position) -> str:
    """A source key from its position, rounded to the centimetre: src_<x>_<y>_<z>."""
    return "src_" + "_".join(f"{v:g}" for v in np.round(position, 2))


def _position(a: np.ndarray, m: int, e: int | None = None) -> tuple:
    """The position of measurement m (and emitter e), from positions with a row per measurement or one row for all
    (and, for sources, a column per emitter or one column for all)."""
    a = a[m] if len(a) > 1 else a[0]
    if a.ndim == 2:
        a = a[e] if e is not None and len(a) > 1 else a[0]
    return tuple(a)


class SofaAdapter:
    """Generic SOFA (netCDF4/HDF5) adapter.

    Data.IR is (M, R, N) or (M, R, N, E): one record per measurement m (and
    emitter e), with all R receivers as channels. Settings come from
    `adapter.params`, or from a list `params.groups` of such dicts when one
    dataset mixes file types (e.g. BRIR and SRIR files):
      glob (default **/*.sofa), pattern (regex on the file name: groups room, rcv, src, cond),
      room_from (pattern|file|parent), roles (list of R), capture_format, reference_role,
      sh_norm, orientation_known, m_select (list of m indices, e.g. [0] = frontal head
      orientation for rotation-dense BRIR sets), first_per_source (keep only the first
      measurement at each distinct SourcePosition: drops repeated measurements), m_is (src
      (default) | rcv: what the measurement index enumerates; with a pattern's rcv, the two are
      joined, e.g. a position and a head orientation), pattern_on (name (default) | path: the
      relative path, so folders can name the room or source), ir_kind. An all-zero
      SourcePosition is treated as unpublished.
    Options that correct the positions a file states (sofa_positions):
      emitter_positions: true   each emitter's source is SourcePosition + EmitterPosition (cartesian or
                                spherical around the source), not the SourcePosition alone (the centre of a
                                loudspeaker ring or orchestra). A zero SourcePosition is then the origin.
      pattern groups rcv_x, rcv_y, rcv_z   the listener coordinates the file name states, with "pt" for the
                                decimal point (x-0pt5 is -0.5), replace those of ListenerPosition. They do
                                not change the receiver key, which joins only rcv and rcv_<digit>.
      ignore_positions: true    the file's positions are placeholders: no source or listener position.
    m_is: src, stated on a file with an E axis of length 1 (several measurements of one loudspeaker
    moved round the room): the measurement names the source, src_<x>_<y>_<z> from SourcePosition, and the
    receiver key is the pattern's rcv alone. Without it, an E axis names the source by the emitter (e0, e1, ...)
    and m goes into the receiver key.
    The axes of Data.IR come from its netCDF dimension names (M, R, N, E in any order): SOFA 2
    conventions such as MultiSpeakerBRIR store (M, R, E, N).
    Positions come from sofa_positions. Loading reads one slice lazily via h5py.
    """

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        params = dataset.adapter.params
        exclude = re.compile(params.get("exclude", DEFAULT_EXCLUDE))
        for p in params.get("groups") or [params]:
            pattern = re.compile(p["pattern"]) if p.get("pattern") else None
            for f in sorted(root.glob(p.get("glob", "**/*.sofa"))):
                rel = f.relative_to(root).as_posix()
                if exclude.search(rel):
                    continue
                g = {}
                if pattern is not None:
                    m_ = pattern.search(rel if p.get("pattern_on") == "path" else f.name)
                    if m_ is None:
                        continue
                    g = m_.groupdict()
                yield from self._file_records(dataset, p, f, rel, g)

    def _file_records(self, dataset, p: dict, f: Path, rel: str, g: dict) -> Iterator[IRRecord]:
        """The records of one SOFA file f (path rel), read with the params p; g holds the pattern's groups."""
        import h5py

        try:
            with h5py.File(f, "r") as h:
                shape = sofa_shape(h["Data.IR"])
                fs = int(np.ravel(h["Data.SamplingRate"][()])[0])
                src = sofa_source_position(h)
                src_xyz, lis = sofa_positions(h, p, g)
                conv = h.attrs.get("SOFAConventions", b"")
                conv = conv.decode() if isinstance(conv, bytes) else str(conv)
        except OSError:
            return            # not HDF5 (e.g. a git-LFS pointer)
        M, R, N = shape[:3]
        E = shape[3] if len(shape) == 4 else None
        roles = p.get("roles") or [f"mic_{i}" for i in range(R)]
        if len(roles) != R:
            roles = [f"mic_{i}" for i in range(R)]
        room = sofa_room(p, g, rel, dataset.id)
        rcv_m = p.get("m_is") == "rcv"
        src_per_m = src is not None and src.ndim == 2 and src.shape[0] == M
        m_is_src = p.get("m_is") == "src" and E is not None     # stated on a file with an emitter axis
        if m_is_src and E != 1:
            raise ValueError(f"{rel}: m_is: src reads files with one emitter; this one has {E}")
        ms = p.get("m_select") or range(M)
        by_source = bool(p.get("first_per_source")) and src_per_m
        if by_source:        # repeated measurements: the first m at each SourcePosition
            ms = first_per_source(src)
        for m in ms:
            if m >= M:
                continue
            for e in range(E) if E else [None]:
                sp = None if src_xyz is None else _position(src_xyz, m, e)
                lp = None if lis is None else _position(lis, m)
                key = f"{rel}#{m}" + (f"#{e}" if e is not None else "")
                if m_is_src:         # the measurement names the source, by its position
                    src_key = field(g, "src") or (source_position_name(src[m]) if src_per_m else f"m{m}")
                    rcv_key = field(g, "rcv")
                else:
                    src_key = field(g, "src") or (f"e{e}" if e is not None else None) or (
                        source_position_name(src[m]) if by_source else None if rcv_m else f"m{m}")
                    rcv_key = "_".join(k for k in (field(g, "rcv"), f"m{m}" if e is not None or rcv_m else None)
                                       if k) or None
                yield IRRecord(
                    dataset_id=dataset.id, local_key=key, room_key=slug(room),
                    room_label=g.get("label") or str(room).replace("_", " "),
                    capture_format=p.get("capture_format", "array_raw"), channel_roles=tuple(roles), fs=fs,
                    n_samples=int(N), locator={"relpath": rel, "container": "sofa", "m": int(m), "e": e},
                    src_key=src_key, rcv_key=rcv_key, condition_key=field(g, "cond"), src_pos=sp, rcv_pos=lp,
                    sh_norm=p.get("sh_norm"),
                    orientation_known=bool(p.get("orientation_known", True)),
                    ir_kind=p.get("ir_kind", "room"),
                    extra={"sofa_conventions": conv, "reference_role": p.get("reference_role"),
                           "preferred": bool(p.get("preferred", True))})

    def load(self, locator: dict, root: Path):
        import h5py

        with h5py.File(root / locator["relpath"], "r") as h:
            d = h["Data.IR"]
            order = sofa_axes(d)
            if locator.get("e") is None:
                x = d[locator["m"]]
            elif order.index("E") == 2:                 # (M, R, E, N)
                x = d[locator["m"], :, locator["e"], :]
            else:                                       # (M, R, N, E)
                x = d[locator["m"], :, :, locator["e"]]
            fs = int(np.ravel(h["Data.SamplingRate"][()])[0])
        return np.atleast_2d(np.asarray(x, dtype=np.float64)), fs


# ---------------------------------------------------------------- MATLAB arrays
class MatArrayAdapter:
    """IRs stored as N-d arrays in .mat files (e.g. IoSR BRIRs: brir[N, 2 ears, 24 speakers]).

    Params: glob, file_pattern (regex on file name; groups become keys), var,
    time_axis, channel_axis, record_axis (one record per index; None = one per file),
    roles, capture_format, fs, room_default, record_prefix.
    """

    def _arr(self, path: Path, var: str):
        import scipy.io

        try:
            return np.asarray(scipy.io.loadmat(str(path), variable_names=[var])[var])
        except NotImplementedError:          # v7.3 (HDF5)
            import h5py

            with h5py.File(path, "r") as h:
                return np.asarray(h[var]).T

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        fpat = re.compile(p.get("file_pattern", r".*\.mat$"))
        for f in sorted(root.glob(p.get("glob", "**/*.mat"))):
            m_ = fpat.search(f.name)
            if not m_:
                continue
            g = m_.groupdict()
            rel = f.relative_to(root).as_posix()
            a = self._arr(f, p["var"])
            n = a.shape[p.get("time_axis", 0)]
            n_rec = a.shape[p["record_axis"]] if p.get("record_axis") is not None else 1
            roles = p.get("roles") or [f"mic_{i}" for i in range(a.shape[p.get("channel_axis", 1)])]
            for k in range(n_rec):
                yield IRRecord(
                    dataset_id=dataset.id, local_key=f"{rel}#{k}", room_key=slug(g.get("room") or p.get("room_default", "unknown")),
                    capture_format=p.get("capture_format", "array_raw"), channel_roles=tuple(roles), fs=int(p["fs"]),
                    n_samples=int(n), locator={"relpath": rel, "container": "mat", "k": k},
                    src_key=f"{p.get('record_prefix', 'rec')}{k + 1}", rcv_key=g.get("rcv"), condition_key=g.get("cond"),
                    room_label=str(g.get("room") or p.get("room_default", "")).replace("_", " "), extra={})

    def load(self, locator: dict, root: Path):
        p = self._params
        a = self._arr(root / locator["relpath"], p["var"])
        t_ax, c_ax, r_ax = p.get("time_axis", 0), p.get("channel_axis", 1), p.get("record_axis")
        if r_ax is not None:
            a = np.take(a, locator["k"], axis=r_ax)
            if c_ax > r_ax:
                c_ax -= 1
            if t_ax > r_ax:
                t_ax -= 1
        return np.moveaxis(a, (c_ax, t_ax), (0, 1)).astype(np.float64), int(p["fs"])


# ---------------------------------------------------------------- TAU-SRIR DB
class TauSrirAdapter:
    """TAU-SRIR DB (MATLAB v7.3): rirs_<nn>_<room>.mat holds rirs(traj, height).{foa,mic},
    each [samples x 4 x DOAs] (HDF5: object references to (DOAs, 4, 7200) arrays, 24 kHz).
    One record per height x trajectory x DOA, FOA format (ACN W,Y,Z,X; SN3D)."""

    FS = 24000
    NAME = re.compile(r"rirs_(?P<n>\d+)_(?P<room>.+)\.mat$")

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        import h5py

        fmt = (self._params or {}).get("format", "foa")
        for f in sorted(root.glob("**/rirs_*.mat")):
            m_ = self.NAME.search(f.name)
            if not m_:
                continue
            rel = f.relative_to(root).as_posix()
            with h5py.File(f, "r") as h:
                refs = h["rirs"][fmt]
                for (hi, ti), ref in np.ndenumerate(refs[()]):
                    n_doa, n_ch, n = h[ref].shape
                    for k in range(n_doa):
                        yield IRRecord(
                            dataset_id=dataset.id, local_key=f"{rel}#{fmt}/{hi},{ti},{k}", room_key=slug(m_["room"]),
                            capture_format="foa_ambix" if fmt == "foa" else "tetra_raw",
                            channel_roles=("W", "Y", "Z", "X") if fmt == "foa" else ("m1", "m2", "m3", "m4"),
                            fs=self.FS, n_samples=int(n),
                            locator={"relpath": rel, "container": "tau_mat", "fmt": fmt, "hi": int(hi), "ti": int(ti), "k": k},
                            condition_key=f"height{hi + 1}", src_key=f"traj{ti + 1}_doa{k}", sh_norm="SN3D" if fmt == "foa" else None,
                            orientation_known=False, room_label=m_["room"].replace("_", " "), extra={})

    def load(self, locator: dict, root: Path):
        import h5py

        with h5py.File(root / locator["relpath"], "r") as h:
            ref = h["rirs"][locator["fmt"]][locator["hi"], locator["ti"]]
            x = np.asarray(h[ref][locator["k"]], dtype=np.float64)      # (4, 7200)
        return x, self.FS


# ---------------------------------------------------------------- MD-OMP (ANU)
class AnuMdOmpAdapter:
    """anu_md_omp (MD-OMP Monte Carlo, ANU): rir/rir_rt0<T>_<trial>.mat (MATLAB v5) holds rir_s1 ... rir_s4, each
    [28 microphones x 16000 samples] at 16 kHz, and `source` [4 x 3] (the four source positions, metres).

    One record per file x source x microphone (mono, omni): the 28 microphones are spread along the walls of one
    simulated 3.36 x 4.48 x 4.1 m room (pos.perimetermic() in github.com/weiting-lai/MD-OMP_Localization), so each
    is its own receiver. The condition is the reverberation time (rt02 = 0.2 s ... rt08 = 0.8 s). The sources are
    drawn anew for each file, so the source key names the file's condition and trial: rt03-trial007-s2.

    Params (all optional): glob (default "rir/rir_rt*.mat"), fs (16000), room_default ("md-omp-room"),
    n_sources (4), n_mics (28), n_samples (16000; the IR variables are not opened to list them), ir_kind
    (default the dataset's first ir_kind).
    """

    # pos.perimetermic() (+function/+pos/perimetermic.m, v1.00 01-04-24): x, y, z in metres
    MICS = np.array([
        [0, 0, 1.3854], [0, 4.48, 1.9806], [0.56, 0, 1.0087], [0.56, 4.48, 1.6220], [1.12, 0, 1.5358],
        [1.12, 4.48, 1.8099], [1.68, 0, 1.5769], [1.68, 4.48, 1.4509], [2.24, 0, 1.5879], [2.24, 4.48, 1.3812],
        [2.8, 0, 1.8362], [2.8, 4.48, 1.9144], [3.36, 0, 1.6767], [3.36, 4.48, 1.8149], [0, 0.56, 1.7422],
        [3.36, 0.56, 1.2596], [0, 1.12, 1.7434], [3.36, 1.12, 1.6351], [0, 1.68, 1.9738], [3.36, 1.68, 1.8074],
        [0, 2.24, 1.4605], [3.36, 2.24, 1.3502], [0, 2.8, 1.5407], [3.36, 2.8, 1.2149], [0, 3.36, 1.5759],
        [3.36, 3.36, 1.9231], [0, 3.92, 1.7195], [3.36, 3.92, 1.0305]])
    NAME = re.compile(r"rir_rt0?(?P<rt>\d+)_(?P<trial>\d+)\.mat$")

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        import scipy.io

        p = dataset.adapter.params
        fs = int(p.get("fs", 16000))
        n_src, n_mic, n = int(p.get("n_sources", 4)), int(p.get("n_mics", 28)), int(p.get("n_samples", 16000))
        room = p.get("room_default", "md-omp-room")
        kind = p.get("ir_kind") or (dataset.ir_kinds[0] if dataset.ir_kinds else "room")
        for f in sorted(root.glob(p.get("glob", "rir/rir_rt*.mat"))):
            m = self.NAME.search(f.name)
            if not m:
                continue
            rel = f.relative_to(root).as_posix()
            rt, trial = int(m["rt"]), int(m["trial"])
            src = np.asarray(scipy.io.loadmat(str(f), variable_names=["source"])["source"], dtype=float)
            for s in range(n_src):
                for k in range(n_mic):
                    yield IRRecord(
                        dataset_id=dataset.id, local_key=f"{rel}#rir_s{s + 1}#{k}", room_key=slug(room),
                        capture_format="mono_omni", channel_roles=("omni",), fs=fs, n_samples=n,
                        locator={"relpath": rel, "container": "mat", "var": f"rir_s{s + 1}", "k": k},
                        condition_key=f"rt60-{rt / 10:.1f}s", src_key=f"rt{rt:02d}-trial{trial:03d}-s{s + 1}",
                        rcv_key=f"mic{k + 1:02d}", src_pos=tuple(src[s]), rcv_pos=tuple(self.MICS[k]),
                        room_label=str(room).replace("-", " "), ir_kind=kind,
                        extra={"rt60_s": rt / 10, "trial": trial})

    def load(self, locator: dict, root: Path):
        import scipy.io

        a = scipy.io.loadmat(str(root / locator["relpath"]), variable_names=[locator["var"]])[locator["var"]]
        return np.asarray(a[locator["k"]], dtype=np.float64)[None, :], int(self._params.get("fs", 16000))


# ---------------------------------------------------------------- OTIMP (Oticon-Imperial hearing aids)
class OtimpAdapter:
    """otimp (Oticon-Imperial hearing-aid impulse responses): the calibrated database only.

    02_default_calibrated/.../mat/subject_<nn>.mat (MATLAB v5) holds ha_rir_0, ha_rir_n15 and ha_rir_n30, each
    [32133 samples x 6 microphones x 29 loudspeakers] at 44.1 kHz (fs), with doa_az_deg, doa_el_deg and doa_unit_vec
    (X front, Y left, Z up). The 6 microphones are the front and rear behind-the-ear mics of the left and right
    hearing aids (1 3 left, 2 4 right) and an in-ear mic in each ear canal (5 left, 6 right).

    One record per subject x chair rotation (0, -15, -30 degrees yaw) x loudspeaker, with the 6 microphones as its
    channels. The loudspeakers are on a sphere of radius 1.9 m (the one overhead at 1.6 m; Moore et al., JASA 2019,
    section II), the head at its centre. metadata.csv beside the files gives the subject type (m, f, x = mannequin)
    and in_ear_valid (subject 40: the in-ear channels are NaN, as published).

    01_original_study (the same measurement processed for the paper) and 03_merged_direct_path (the first 10.7 ms
    only: head-related, not room, responses) are not read.

    Params (optional): glob, room_default ("otimp-listening-room"), radius_m (1.9), radius_top_m (1.6).
    """

    ROLES = ("BTE_L_front", "BTE_R_front", "BTE_L_rear", "BTE_R_rear", "ear_L", "ear_R")
    ROTATIONS = {"ha_rir_0": 0, "ha_rir_n15": -15, "ha_rir_n30": -30}
    NAME = re.compile(r"subject_(?P<n>\d+)\.mat$")
    _cache: dict = {}                           # the last variable read: (samples, mics, loudspeakers), and fs

    @staticmethod
    def _subject(folder: Path, n: int) -> dict:
        """The subject's row of metadata.csv (empty when it has none)."""
        import csv

        with open(folder / "metadata.csv", newline="") as fh:
            return next((row for row in csv.DictReader(fh) if int(row["subj_id"]) == n), {})

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        import scipy.io

        p = dataset.adapter.params
        room = p.get("room_default", "otimp-listening-room")
        r_side, r_top = float(p.get("radius_m", 1.9)), float(p.get("radius_top_m", 1.6))
        for f in sorted(root.glob(p.get("glob", "02_default_calibrated/02_default_calibrated/mat/subject_*.mat"))):
            m = self.NAME.search(f.name)
            if not m:
                continue
            n = int(m["n"])
            meta = self._subject(f.parent, n)
            rel = f.relative_to(root).as_posix()
            shapes = {name: shape for name, shape, _ in scipy.io.whosmat(str(f))}
            v = scipy.io.loadmat(str(f), variable_names=["fs", "doa_az_deg", "doa_el_deg", "doa_unit_vec"])
            fs = int(np.ravel(v["fs"])[0])
            az, el, u = np.ravel(v["doa_az_deg"]), np.ravel(v["doa_el_deg"]), np.asarray(v["doa_unit_vec"], float)
            for var, yaw in self.ROTATIONS.items():
                n_samples, n_mic, n_doa = shapes[var]
                if n_mic != len(self.ROLES):
                    raise ValueError(f"{rel}: {var} has {n_mic} microphones, expected {len(self.ROLES)}")
                for d in range(n_doa):
                    radius = r_top if el[d] >= 89.9 else r_side
                    yield IRRecord(
                        dataset_id=dataset.id, local_key=f"{rel}#{var}#{d}", room_key=slug(room),
                        capture_format="array_raw", channel_roles=self.ROLES, fs=fs, n_samples=int(n_samples),
                        locator={"relpath": rel, "container": "mat", "var": var, "d": d},
                        src_key=f"ls{d + 1:02d}_az{az[d]:g}_el{el[d]:g}", rcv_key=f"subject{n:02d}_yaw{yaw}",
                        src_pos=tuple(radius * u[:, d]), rcv_pos=(0.0, 0.0, 0.0),
                        room_label=str(room).replace("-", " "),
                        extra={"subject": n, "subject_kind": {"m": "male", "f": "female", "x": "mannequin"}.get(
                            meta.get("gender", ""), ""), "chair_yaw_deg": yaw,
                            "in_ear_valid": meta.get("in_ear_valid") == "1"})

    def load(self, locator: dict, root: Path):
        import scipy.io

        key = (locator["relpath"], locator["var"])
        if key not in self._cache:
            self._cache.clear()
            v = scipy.io.loadmat(str(root / locator["relpath"]), variable_names=[locator["var"], "fs"])
            self._cache[key] = (np.asarray(v[locator["var"]]), int(np.ravel(v["fs"])[0]))
        a, fs = self._cache[key]
        return np.ascontiguousarray(a[:, :, locator["d"]].T, dtype=np.float64), fs


# ---------------------------------------------------------------- One simulated scene per .npz (Tampere)
class NpzSceneAdapter:
    """tampere_distance (Zenodo 23096979): one simulated RIR per NumPy .npz file.

    shared_rir/shared_rir/sample_<NNNNN>_rir.npz, one per acoustic scene (a shoebox room of its own,
    pyroomacoustics image-source method). Each file holds h_full (N,), the complete RIR read here; h_direct,
    h_no_late and h_no_early, the same RIR cut by the authors for the paper (not read); and room_dim (3,) in m,
    distance_m and absorption_coeff. No sampling rate is stored: 16 kHz, the rate of the corpus convolved with
    these RIRs (DATASET.md).

    One record per file, its item empty: the room key is the scene (sample-NNNNN), the label gives the room size,
    extra holds the size, the source-microphone distance and the absorption coefficient. Positions are not
    published, only the distance.

    Params (all optional): glob (default "shared_rir/shared_rir/sample_*_rir.npz"), array ("h_full"), fs (16000).
    """

    NAME = re.compile(r"sample_(?P<scene>\d{5})_rir\.npz$")

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        fs = int(p.get("fs", 16000))
        array = p.get("array", "h_full")
        for f in sorted(root.glob(p.get("glob", "shared_rir/shared_rir/sample_*_rir.npz"))):
            m = self.NAME.search(f.name)
            if not m:
                continue
            rel = f.relative_to(root).as_posix()
            with np.load(f) as z:
                n = int(z[array].shape[-1])
                dims = [float(v) for v in z["room_dim"]]
                dist = float(z["distance_m"])
                alpha = float(z["absorption_coeff"])
            scene = m["scene"]
            dims_txt = " x ".join(f"{d:.2f}" for d in dims)
            yield IRRecord(
                dataset_id=dataset.id, local_key=rel, room_key=slug(f"sample-{scene}"),
                capture_format="mono_omni", channel_roles=("omni",), fs=fs, n_samples=n,
                locator={"relpath": rel, "container": "npz", "array": array},
                room_label=f"simulated shoebox room {dims_txt} m, absorption {alpha:.3f} (scene {scene})",
                extra={"dims_m": dims, "distance_m": dist, "absorption_coeff": alpha,
                       "volume_m3": float(np.prod(dims))})

    def load(self, locator: dict, root: Path):
        p = getattr(self, "_params", {}) or {}
        with np.load(root / locator["relpath"]) as z:
            x = np.asarray(z[locator.get("array", "h_full")], dtype=np.float64)
        return x.reshape(1, -1), int(p.get("fs", 16000))
