"""The generated dataset of the tests: seven impulse responses with known reverberation times, one stereo file,
and one exact copy (conftest.py takes it through the pipeline)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from rirdb.synth import exp_decay_ir

REPO = Path(__file__).resolve().parents[1]

FS = 48000
# relative path -> (reverberation time in seconds, random seeds: one per channel)
TOY_FILES = {
    "hall/s1_m1.wav": (1.2, [1]),
    "hall/s1_m2.wav": (1.2, [2]),
    "hall/s1_mstereo.wav": (1.2, [3, 4]),
    "booth/s1_m1.wav": (0.3, [5]),
    "booth/s1_m2.wav": (0.3, [6]),
    "stairs/s1_m1.wav": (2.0, [7]),
}
DUPLICATE = ("hall/s2_m1.wav", "hall/s1_m1.wav")      # (copy, original): identical samples

DATASET_YAML = r"""
id: toy
name: Toy dataset (generated for the tests)
year: 2021
type: simulated
ir_kinds: [room]
capture_formats: [mono_omni, stereo]
content: Generated exponential decays with known reverberation times.
license: {spdx: CC0-1.0, redistribute_audio: "yes"}
access: open
source: {kind: http, urls: ["https://example.invalid/toy.zip"]}
adapter:
  name: wav_tree
  params: {pattern: '(?P<room>[^/]+)/s(?P<src>\d+)_m(?P<rcv>\w+)\.wav$'}
status: active
defects:
- {what: "The booth files are too short to trust.", files: "^booth/"}
"""


def write_toy_dataset(files_dir: Path) -> None:
    for rel, (t60, seeds) in TOY_FILES.items():
        channels = [exp_decay_ir(fs=FS, t60=t60, pnr_db=75.0, seed=s).x for s in seeds]
        path = files_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), np.stack(channels, axis=1), FS, subtype="FLOAT")
    copy, original = DUPLICATE
    (files_dir / copy).write_bytes((files_dir / original).read_bytes())
