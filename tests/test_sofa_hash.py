"""audio_hash: the hash of the samples for most readers, and of the published values for a reader that computes its
samples (adapters/base.py, ir_hash), so that a computed IR hashes the same on any numpy build."""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from rirdb.adapters.base import audio_hash, ir_hash
from rirdb.deliver import audio_hash as delivered_hash
from rirdb.deliver import check_audio_hash
from rirdb.fetch import FetchError


class PlainReader:
    """A reader that returns samples as stored: no published_values."""


class ComputedReader:
    """A reader that computes its samples from stored values (here a spectrum) with one setting, n."""

    def __init__(self, spectrum: np.ndarray, n: int):
        self.spectrum, self.n = spectrum, n

    def published_values(self, locator: dict, root) -> bytes:
        return self.spectrum.tobytes() + json.dumps({"n": self.n}, sort_keys=True).encode()


def test_ir_hash_plain_reader_is_the_v1_hash():
    """Every active dataset's reader has no published_values: its hash stays the SHA-1 of the float32 samples."""
    x = np.random.default_rng(0).standard_normal((2, 100))
    v1 = hashlib.sha1(np.ascontiguousarray(x, dtype=np.float32).tobytes()).hexdigest()
    assert audio_hash(x) == v1
    assert ir_hash(PlainReader(), {}, None, x) == v1
    assert delivered_hash(x) == ir_hash(PlainReader(), {}, None, x)[:32]


def test_ir_hash_of_a_computed_reader_ignores_last_bit_noise():
    spectrum = np.fft.rfft(np.random.default_rng(1).standard_normal(64))
    reader = ComputedReader(spectrum, 64)
    x = np.fft.irfft(spectrum, 64)[None, :]
    h = ir_hash(reader, {}, None, x)
    assert ir_hash(reader, {}, None, x + 1e-12) == h
    assert ir_hash(ComputedReader(spectrum * 2, 64), {}, None, x) != h          # other stored values
    assert ir_hash(ComputedReader(spectrum, 66), {}, None, x) != h              # another setting


def test_get_accepts_a_computed_ir_with_other_last_bits_and_rejects_a_changed_source():
    spectrum = np.fft.rfft(np.random.default_rng(2).standard_normal(64))
    x = np.fft.irfft(spectrum, 64)[None, :]
    published = ir_hash(ComputedReader(spectrum, 64), {}, None, x)[:32]
    check_audio_hash(ComputedReader(spectrum, 64), {}, None, x * (1 + 1e-9), published, "toy-1")
    with pytest.raises(FetchError, match="toy-1"):
        check_audio_hash(ComputedReader(spectrum + 1, 64), {}, None, x, published, "toy-1")
    with pytest.raises(FetchError):                                               # a plain reader checks samples
        check_audio_hash(PlainReader(), {}, None, x * 1.001, delivered_hash(x), "toy-1")
    check_audio_hash(PlainReader(), {}, None, x * 1.001, "", "toy-1")              # no hash in the release
