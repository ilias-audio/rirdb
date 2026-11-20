"""Adapter `stonehenge_octave` for stonehenge: the Stonehenge 1:12 scale model (Cox, Fazenda, Greaney 2020), one IR
per measurement folder.

Each folder [configurations/]<group>/<source>-<receiver>/ holds six octave-band IRs IR_125.wav ... IR_4000.wav
(16 kHz, each normalised to a peak of 1) and gains.txt. gains.txt has, per band, a header line and a line
"<band Hz> <amplifier gain dB> <peak magnitude>"; process_impulse_responses.m (in the download) restores each band
as x * peak / 10**(gain/20), which gives the bands their relative levels again.

The record has six channels, one per octave band (roles oct125 ... oct4000), with the levels restored. The bands
are 3rd-order Butterworth band-passes of one measurement, so their mean is the band-limited (about 88 Hz to
5.6 kHz) broadband response: analyse it with analysis.reference_role "mean".

The six factors are read from gains.txt while indexing and kept in the locator ({"125": factor, ...}), so load()
never opens gains.txt: `rirdb get` fetches only the files of the IR (the six WAVs), not the side file.

adapter.params (all optional):
  exclude         regex on the folder path (default: none); e.g. the free-field calibration and a folder copied
                  elsewhere
  room            the space key (default "stonehenge-scale-model")
  label           the space label
  capture_format  default mono_omni (one omni microphone, filtered into six bands)
  ir_kind         default scale_model
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from rirdb.adapters.base import IRRecord, slug

BANDS = (125, 250, 500, 1000, 2000, 4000)


def read_gains(path: Path) -> dict[int, float]:
    """{band Hz: linear factor peak / 10**(gain/20)} from gains.txt (lines of numbers; header lines are skipped)."""
    out = {}
    for line in path.read_text().splitlines():
        try:
            band, gain_db, peak = (float(v) for v in line.split()[:3])
        except ValueError:
            continue
        out[int(round(band))] = peak / 10 ** (gain_db / 20)
    return out


def band_gains(path: Path) -> dict[str, float]:
    """The factor of each of the six bands, keyed by the band as text (as JSON keeps it). A band missing from
    gains.txt raises KeyError, naming the band, rather than leaving that band at its normalised level."""
    factor = read_gains(path)
    missing = [b for b in BANDS if b not in factor]
    if missing:
        raise KeyError(f"{path}: no gain for the band(s) {missing}")
    return {str(b): factor[b] for b in BANDS}


class StonehengeOctaveAdapter:
    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        exclude = re.compile(p["exclude"]) if p.get("exclude") else None
        room = p.get("room", "stonehenge-scale-model")
        for gains in sorted(root.glob("**/gains.txt")):
            folder = gains.parent.relative_to(root).as_posix()
            if exclude is not None and exclude.search(folder + "/"):
                continue
            files = [f"{folder}/IR_{b}.wav" for b in BANDS]
            if not all((root / f).is_file() for f in files):
                continue
            info = sf.info(str(root / files[0]))
            group, position = folder.split("/")[-2:]       # "2200BC model" or the configuration; "s1-m1"
            src, _, rcv = position.partition("-")
            yield IRRecord(
                dataset_id=dataset.id,
                local_key="|".join(files),
                room_key=slug(room),
                capture_format=p.get("capture_format", "mono_omni"),
                channel_roles=tuple(f"oct{b}" for b in BANDS),
                fs=int(info.samplerate),
                n_samples=int(info.frames),
                locator={"files": files, "container": "octave_bands", "gains": band_gains(gains)},
                condition_key=group,
                src_key=src or None,
                rcv_key=rcv or None,
                room_label=p.get("label", room),
                ir_kind=p.get("ir_kind", "scale_model"),
                extra={"subtype": info.subtype, "reference_role": "mean"},
            )

    def load(self, locator: dict, root: Path) -> tuple[np.ndarray, int]:
        bands, fs = [], None
        for f in locator["files"]:
            band = f.rsplit("_", 1)[-1].removesuffix(".wav")          # .../IR_125.wav -> "125"
            x, fs = sf.read(str(root / f), dtype="float64", always_2d=True)
            bands.append(x[:, 0] * locator["gains"][band])
        n = min(len(x) for x in bands)
        return np.vstack([x[:n] for x in bands]), int(fs)
