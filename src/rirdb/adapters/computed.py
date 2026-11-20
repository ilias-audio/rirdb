"""Readers that compute their samples: the dataset publishes spectra, transfer functions or subband filters, and the
reader turns each into an IR (an inverse FFT, an inverse STFT).

The rule for these readers: each has a method published_values(locator, root) -> bytes, the stored values the IR is
computed from, as read from the file, followed by the reader's settings as JSON. The IR's audio_hash is the hash of
those bytes (adapters/base.py, ir_hash), not of the computed samples: numpy builds differ in the last bits of an
inverse FFT, and a hash cannot allow for that. So a delivered WAV of such an IR does not hash to its audio_hash.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import numpy as np

from rirdb.adapters.base import IRRecord, slug
from rirdb.adapters.h5_sets import H5SetsAdapter, _sibling


def settings_bytes(**settings) -> bytes:
    """A reader's settings as published_values appends them: JSON with sorted keys."""
    return json.dumps(settings, sort_keys=True).encode()


# ---------------------------------------------------------------- complex frequency responses in HDF5
def frequency_grid(f) -> tuple[int, int]:
    """(n_samples, fs) of the real IR whose rfft bins are at the frequencies f: 0 Hz to f_max in even steps."""
    f = np.asarray(f, dtype=np.float64).ravel()
    step = f[1] - f[0]
    if f[0] != 0 or not np.allclose(np.diff(f), step):
        raise ValueError(f"the frequency vector must start at 0 Hz in even steps (got {f[:3]} ... {f[-1]})")
    n = 2 * (f.size - 1)
    return n, int(round(n * step))


class H5SpectraAdapter(H5SetsAdapter):
    """The `h5_freqresp` reader: measurement sets of complex frequency responses in HDF5, turned into IRs.

    The params are h5_sets' (sets, set_names, rows, positions, src_position, pos_scale, ...), with freq_axis, the
    axis of the frequency bins, in place of time_axis, and frequency, the path of the frequency vector relative to
    the array's group (default 'frequency'). The vector must start at 0 Hz in even steps; the arrays are 2-D and
    complex. Each response becomes the inverse real FFT of its bins as published (no window, no band limit added):
    n = 2 (bins - 1) samples at fs = n * step. The last bin is read as the Nyquist bin, so its phase is dropped.
    The IR's audio_hash is the hash of the published bins and of n and fs (published_values): a delivered WAV of
    it does not hash to its audio_hash.
    """

    @staticmethod
    def _sample_axis(g: dict) -> int:
        return int(g["freq_axis"])

    def _length_and_rate(self, h, name: str, g: dict) -> tuple[int, int]:
        arr = h[name]
        if arr.dtype.kind != "c":
            raise ValueError(f"{name}: {arr.dtype}, expected complex frequency responses")
        n, fs = frequency_grid(h[_sibling(name, g.get("frequency", "frequency"))][()])
        if arr.shape[self._sample_axis(g)] != n // 2 + 1:
            raise ValueError(f"{name}: {arr.shape[self._sample_axis(g)]} bins, the frequency vector has {n // 2 + 1}")
        return n, fs

    def load(self, locator: dict, root: Path):
        g = self._groups(self._params)[locator.get("group", 0)]
        with self._open(root / locator["relpath"]) as h:
            spectrum = self._read(h, locator, g)
            n, fs = self._length_and_rate(h, locator["array"], g)
        return np.fft.irfft(spectrum.astype(np.complex128), n=n, axis=-1), fs

    def published_values(self, locator: dict, root: Path) -> bytes:
        """The response's bins as stored, then n and fs."""
        g = self._groups(self._params)[locator.get("group", 0)]
        with self._open(root / locator["relpath"]) as h:
            spectrum = self._read(h, locator, g)
            n, fs = self._length_and_rate(h, locator["array"], g)
        return np.ascontiguousarray(spectrum).tobytes() + settings_bytes(n=n, fs=fs)


# ---------------------------------------------------------------- room transfer functions in a MATLAB file
class TfMatAdapter:
    """The `tf_mat` reader: room transfer functions in a MATLAB file (the EPFL rectangular room, Zenodo 1169161).

    TF_data.mat holds txy{1..G}, each (n_bins, n_mics) complex: one-sided transfer functions at f = f_res,
    2 f_res, ..., f_max (variable f). Group g, column i is microphone (g - 1) * n_mics + i of the positions file
    (variables x, y, z). The IR is the inverse real FFT of [0, H(f_res) / 2, ..., H(f_max - f_res) / 2, H(f_max)]
    over 2 n_bins samples at fs = 2 f_max, as the authors' RIR_maker.m builds it, with the bins up to low_cut_hz set
    to zero as RIR_maker.m does by default. Its optional low-pass filter, downsampling and truncation are not
    applied. The IR's audio_hash is the hash of the transfer function's bins and of low_cut_hz, n and fs
    (published_values): a delivered WAV of it does not hash to its audio_hash.

    Params: file (default TF_data.mat), var (txy), freq_var (f), positions (a .mat file with x, y, z), src_pos
    [x, y, z], src_key (S1), room_default, low_cut_hz (10).
    """

    _cache: dict = {}                     # the last file read: a few hundred MB, read once per process

    def _mat(self, path: Path, names: list[str]) -> dict:
        import scipy.io

        key = (str(path), tuple(names))
        if key not in self._cache:
            self._cache.clear()
            self._cache[key] = scipy.io.loadmat(str(path), variable_names=names)
        return self._cache[key]

    def _tf_and_frequencies(self, locator: dict, root: Path) -> tuple[np.ndarray, np.ndarray]:
        """(the transfer function of one microphone as stored, the frequency vector f)."""
        p = self._params
        var, fvar = p.get("var", "txy"), p.get("freq_var", "f")
        m = self._mat(root / locator["relpath"], [var, fvar])
        return m[var].ravel()[locator["g"]][:, locator["i"]], np.ravel(m[fvar])

    @staticmethod
    def _check_frequencies(f: np.ndarray) -> None:
        """f must be f_res, 2 f_res, ..., in even steps: the bins the IR recipe assumes."""
        f_res = f[1] - f[0]
        if not np.isclose(f[0], f_res) or not np.allclose(np.diff(f), f_res):
            raise ValueError(f"the frequency vector must be f_res, 2 f_res, ... in even steps (got {f[:3]} ...)")

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        rel = p.get("file", "TF_data.mat")
        var, fvar = p.get("var", "txy"), p.get("freq_var", "f")
        m = self._mat(root / rel, [var, fvar])
        groups = m[var].ravel()
        f = np.ravel(m[fvar])
        self._check_frequencies(f)
        fs = int(round(2 * f[-1]))
        pos = None
        if p.get("positions"):
            import scipy.io

            q = scipy.io.loadmat(str(root / p["positions"]))
            pos = np.column_stack([np.ravel(q[a]) for a in "xyz"])
            n_records = sum(tf.shape[1] for tf in groups)
            if len(pos) != n_records:
                raise ValueError(f"{p['positions']}: {len(pos)} positions for {n_records} microphones")
        src_pos = tuple(p["src_pos"]) if p.get("src_pos") else None
        room = p.get("room_default", dataset.id)
        k = 0
        for g, tf in enumerate(groups):
            n_bins, n_mics = tf.shape
            for i in range(n_mics):
                k += 1                                        # microphone number, 1-based, as the positions file
                yield IRRecord(
                    dataset_id=dataset.id, local_key=f"{rel}#{g + 1},{i + 1}", room_key=slug(room),
                    capture_format="mono_omni", channel_roles=("omni",), fs=fs, n_samples=2 * n_bins,
                    locator={"relpath": rel, "container": "tf_mat", "g": g, "i": i},
                    src_key=p.get("src_key", "S1"), rcv_key=f"mic{k}", src_pos=src_pos,
                    rcv_pos=tuple(float(v) for v in pos[k - 1]) if pos is not None else None,
                    room_label=str(room).replace("_", " "), extra={"group": g + 1})

    def load(self, locator: dict, root: Path):
        tf, f = self._tf_and_frequencies(locator, root)
        h = np.array(tf, dtype=np.complex128)
        h[: int(round(self._params.get("low_cut_hz", 10) / (f[1] - f[0])))] = 0
        spectrum = np.concatenate([[0], h[:-1] / 2, h[-1:]])
        return np.fft.irfft(spectrum, n=2 * h.size)[None, :], int(round(2 * f[-1]))

    def published_values(self, locator: dict, root: Path) -> bytes:
        """The microphone's transfer function as stored, then low_cut_hz, n and fs."""
        tf, f = self._tf_and_frequencies(locator, root)
        return np.ascontiguousarray(tf).tobytes() + settings_bytes(
            low_cut_hz=self._params.get("low_cut_hz", 10), n=2 * tf.size, fs=int(round(2 * f[-1])))


# ---------------------------------------------------------------- STFT-domain (subband) filters in a MATLAB file
_CACHE: dict = {}                         # the last array read: about 1.4 GB for Tietotalo, read once per process


def _array(path: Path, var: str) -> np.ndarray:
    key = (str(path), var)
    if key not in _CACHE:
        import scipy.io

        _CACHE.clear()
        _CACHE[key] = np.asarray(scipy.io.loadmat(str(path), variable_names=[var])[var])
    return _CACHE[key]


def subband_to_time(A: np.ndarray, hop: int | None = None, n_positions: int = 8) -> np.ndarray:
    """A: (bins, taps, channels) subband filters -> (channels, (taps + 1) * hop) time response from lag 0.

    A unit impulse goes through the STFT (sine window of N = 2 (bins - 1) samples, hop N / 2 by default), every bin
    is filtered by its taps, and the inverse STFT (same window, overlap-add) gives the response. The result is
    averaged over n_positions impulse positions within one hop, since the STFT is not exactly time-invariant."""
    nbin, ntap, nch = A.shape
    N = 2 * (nbin - 1)
    hop = hop or N // 2
    n = np.arange(N)
    w = np.sin(np.pi * (n + 0.5) / N)
    w = w / np.sqrt((w ** 2).reshape(N // hop, hop).sum(0).mean())      # analysis x synthesis windows sum to 1
    post = (ntap + 1) * hop
    k = np.arange(nbin)
    out = np.zeros((nch, post))
    for o in np.linspace(0, hop, n_positions, endpoint=False).astype(int):
        t0 = 2 * N + int(o)
        T = t0 + post + 2 * N
        nfr = (T - N) // hop + 1
        X = np.zeros((nbin, nfr), complex)
        for m in range(nfr):
            if m * hop <= t0 < m * hop + N:                                  # frames holding the impulse
                X[:, m] = w[t0 - m * hop] * np.exp(-2j * np.pi * k * (t0 - m * hop) / N)
        Y = np.zeros((nbin, nfr, nch), complex)
        for tap in range(ntap):
            Y[:, tap:, :] += A[:, tap, None, :] * X[:, :nfr - tap, None]
        y = np.zeros((nch, T))
        for m in range(nfr):
            y[:, m * hop:m * hop + N] += w * np.fft.irfft(Y[:, m, :].T, n=N, axis=-1)
        out += y[:, t0:t0 + post]
    return out / n_positions


class StftSubbandAdapter:
    """The `stft_subband` reader: STFT-domain (subband) filters turned into IRs (TUT Tietotalo).

    Tietotalo_RIR.mat holds one complex array, rir_DB, of shape (distance 2, elevation 9, frequency bin 1025,
    azimuth 36, tap 4, em32 capsule 32). The authors estimated the responses with the CHiME-3 simulation tool:
    least squares per frequency bin of a convolutive subband filter, Y(f, n) = sum_k A(f, k) X(f, n - k), on an STFT
    with half-overlapping sine windows (N = 2048, hop 1024). So each (distance, elevation, azimuth) holds, per capsule,
    a 4-tap filter in each of 1025 bins, not a time signal. load rebuilds the time response the filter applies
    (subband_to_time). It starts at the impulse (lag 0) and has (taps + 1) * hop samples (5120), after which the
    filter's response is zero; what comes out before lag 0 (about -24 dB of the energy, the estimate's non-causal
    error) is left out. The IR's audio_hash is the hash of the slot's filters and of hop, n_positions and fs
    (published_values): a delivered WAV of it does not hash to its audio_hash.

    One record per filled (distance, elevation, azimuth) slot, with all capsules; empty (all-zero) slots are skipped.

    Params (adapter.params):
      file            the .mat file (default Tietotalo_RIR.mat)
      var             the variable (default rir_DB)
      fs              sampling rate in Hz (not stated with the data)
      hop             STFT hop in samples (default N / 2)
      n_positions     impulse positions averaged (default 8)
      room, label     the space (room_key) and its label
      distances_m     metres per distance index (default [1, 2])
      elevation0_deg, elevation_step_deg   elevation of slot 0 and the step (default -40, 10)
      azimuth0_deg, azimuth_step_deg       azimuth of slot 0 and the step (default -180, 10)
      roles           channel roles (default c1 ... c32), capture_format (default em32_raw), reference_role (mean)
    Positions: the array's centre at the origin; the source at (r cos el cos az, r cos el sin az, r sin el), azimuth
    counter-clockwise from the array's x axis.
    """

    def _settings(self, x: np.ndarray) -> dict:
        """hop, n_positions and fs, as load uses them for the array x."""
        p = self._params
        return {"hop": int(p.get("hop") or x.shape[2] - 1), "n_positions": int(p.get("n_positions", 8)),
                "fs": int(p["fs"])}

    def iter_records(self, dataset, root: Path) -> Iterator[IRRecord]:
        p = dataset.adapter.params
        rel = p.get("file", "Tietotalo_RIR.mat")
        x = _array(root / rel, p.get("var", "rir_DB"))
        n_dist, n_el, nbin, n_az, ntap, nch = x.shape
        N = 2 * (nbin - 1)
        hop = int(p.get("hop") or N // 2)
        roles = p.get("roles") or [f"c{i + 1}" for i in range(nch)]
        dists = p.get("distances_m", [1, 2])
        el0, del_ = float(p.get("elevation0_deg", -40)), float(p.get("elevation_step_deg", 10))
        az0, daz = float(p.get("azimuth0_deg", -180)), float(p.get("azimuth_step_deg", 10))
        room = p.get("room") or dataset.id
        filled = np.abs(x).max(axis=(2, 4, 5)) > 0                         # (dist, elev, az)
        for d in range(n_dist):
            for e in range(n_el):
                for a in range(n_az):
                    if not filled[d, e, a]:
                        continue
                    r, el, az = float(dists[d]), el0 + del_ * e, az0 + daz * a
                    ce, se = np.cos(np.radians(el)), np.sin(np.radians(el))
                    src = (r * ce * np.cos(np.radians(az)), r * ce * np.sin(np.radians(az)), r * se)
                    yield IRRecord(
                        dataset_id=dataset.id, local_key=f"{rel}#{d},{e},{a}", room_key=slug(room),
                        capture_format=p.get("capture_format", "em32_raw"), channel_roles=tuple(roles),
                        fs=int(p["fs"]), n_samples=(ntap + 1) * hop,
                        locator={"relpath": rel, "container": "stft_subband", "idx": [d, e, a]},
                        src_key=f"r{r:g}m_el{el:+.0f}_az{az:+.0f}", rcv_key="em32",
                        src_pos=tuple(round(v, 4) for v in src), rcv_pos=(0.0, 0.0, 0.0),
                        orientation_known=bool(p.get("orientation_known", False)),
                        room_label=p.get("label") or str(room).replace("_", " "),
                        extra={"reference_role": p.get("reference_role", "mean"), "distance_m": r,
                               "elevation_deg": el, "azimuth_deg": az})

    def _filters(self, locator: dict, root: Path) -> tuple[np.ndarray, np.ndarray]:
        """(the whole array, the slot's filters (bins, taps, capsules) as stored)."""
        x = _array(root / locator["relpath"], self._params.get("var", "rir_DB"))
        d, e, a = locator["idx"]
        return x, x[d, e, :, a, :, :]

    def load(self, locator: dict, root: Path):
        x, A = self._filters(locator, root)
        s = self._settings(x)
        return subband_to_time(np.asarray(A), hop=s["hop"], n_positions=s["n_positions"]), s["fs"]

    def published_values(self, locator: dict, root: Path) -> bytes:
        """The slot's filters as stored, then hop, n_positions and fs."""
        x, A = self._filters(locator, root)
        return np.ascontiguousarray(A).tobytes() + settings_bytes(**self._settings(x))
