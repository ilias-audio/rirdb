"""Source and listener positions of the sofa reader (adapters/custom.py, sofa_positions), and the options that
correct them: emitter_positions, rcv_x/rcv_y/rcv_z pattern groups, ignore_positions, and m_is: src."""

from __future__ import annotations

from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from rirdb.adapters import get_adapter


def write_sofa(path, ir, source=None, listener=None, emitters=None, source_type=None, emitter_type=None,
               source_view=None):
    """A minimal SOFA file: Data.IR (M, R, N) or (M, R, N, E) and the position variables given."""
    with h5py.File(path, "w") as h:
        h["Data.IR"] = ir
        h["Data.SamplingRate"] = np.array([48000.0])
        for name, value, kind in (("SourcePosition", source, source_type), ("ListenerPosition", listener, None),
                                  ("EmitterPosition", emitters, emitter_type), ("SourceView", source_view, None)):
            if value is not None:
                h[name] = np.asarray(value, dtype=float)
                if kind:
                    h[name].attrs["Type"] = kind.encode()


def records(tmp_path, params):
    ds = SimpleNamespace(id="toy", ir_kinds=["room"], adapter=SimpleNamespace(name="sofa", params=params),
                         analysis=SimpleNamespace(reference_role=None))
    return list(get_adapter("sofa", params).iter_records(ds, tmp_path))


def test_sofa_positions_unchanged_without_new_params(tmp_path):
    """The keys and positions the reader gave before sofa_positions: emitters as sources, one listener row for all
    measurements, a spherical source around each listener row but read at row 0 when it has one row."""
    rng = np.random.default_rng(0)
    write_sofa(tmp_path / "a_pos1.sofa", rng.standard_normal((2, 1, 8, 2)),
               source=[[1, 2, 3], [4, 5, 6]], listener=[[0.5, 0.5, 1.5]])
    write_sofa(tmp_path / "b_pos2.sofa", rng.standard_normal((2, 2, 8)),
               source=[[90, 0, 2]], source_type="spherical", listener=[[1, 1, 1], [2, 2, 2]])
    write_sofa(tmp_path / "c_pos3.sofa", rng.standard_normal((3, 1, 8)), source=np.zeros((3, 3)))
    params = {"glob": "*.sofa", "pattern": r"_(?P<rcv>pos\d)\.sofa$"}
    got = {r.local_key: (r.src_key, r.rcv_key, r.src_pos, r.rcv_pos) for r in records(tmp_path, params)}
    assert got["a_pos1.sofa#0#0"] == ("e0", "pos1_m0", (1, 2, 3), (0.5, 0.5, 1.5))
    assert got["a_pos1.sofa#1#1"] == ("e1", "pos1_m1", (4, 5, 6), (0.5, 0.5, 1.5))
    src, key = got["b_pos2.sofa#1"][2], got["b_pos2.sofa#1"][:2]
    assert key == ("m1", "pos2") and got["b_pos2.sofa#1"][3] == (2, 2, 2)
    np.testing.assert_allclose(src, (1, 3, 1), atol=1e-12)                # around listener row 0, as before
    assert got["c_pos3.sofa#2"] == ("m2", "pos3", None, None)              # all-zero SourcePosition: unpublished
    assert len(got) == 4 + 2 + 3


def test_sofa_emitter_positions(tmp_path):
    """emitter_positions: each loudspeaker at SourcePosition + EmitterPosition (here spherical around the source)."""
    ir = np.random.default_rng(1).standard_normal((1, 2, 20, 3))
    emitters = np.array([[0, 0, 2], [90, 0, 2], [180, 0, 2]], dtype=float)[:, :, None]       # (E, 3, 1)
    write_sofa(tmp_path / "ring.sofa", ir, source=[[0, 0, 1]], listener=[[1, 0, 1]], emitters=emitters,
               emitter_type="spherical")
    params = {"glob": "ring.sofa"}
    assert [r.src_pos for r in records(tmp_path, params)] == [(0, 0, 1)] * 3              # the ring's centre
    recs = records(tmp_path, {**params, "emitter_positions": True})
    assert [r.src_key for r in recs] == ["e0", "e1", "e2"] and recs[0].rcv_pos == (1, 0, 1)
    np.testing.assert_allclose([r.src_pos for r in recs], [(2, 0, 1), (0, 2, 1), (-2, 0, 1)], atol=1e-12)


def test_sofa_emitter_positions_with_a_zero_source_and_a_rotated_source(tmp_path):
    ir = np.random.default_rng(2).standard_normal((1, 1, 20, 2))
    write_sofa(tmp_path / "a.sofa", ir, source=[[0, 0, 0]], emitters=[[1, 2, 3], [-1, 0, 2]])
    recs = records(tmp_path, {"glob": "a.sofa", "emitter_positions": True})
    assert [r.src_pos for r in recs] == [(1, 2, 3), (-1, 0, 2)]                           # zero source: the origin
    assert [r.src_pos for r in records(tmp_path, {"glob": "a.sofa"})] == [None, None]      # unpublished, as before
    write_sofa(tmp_path / "b.sofa", ir, source=[[0, 0, 1]], emitters=[[1, 2, 3], [-1, 0, 2]],
               source_view=[[-1, 0, 0]])
    with pytest.raises(ValueError, match="SourceView"):
        records(tmp_path, {"glob": "b.sofa", "emitter_positions": True})
    write_sofa(tmp_path / "c.sofa", ir, source=[[0, 0, 1]], emitters=np.zeros((2, 3, 4)))
    with pytest.raises(ValueError, match="changes with the measurement"):
        records(tmp_path, {"glob": "c.sofa", "emitter_positions": True})


def test_sofa_listener_from_file_name(tmp_path):
    """Pattern groups rcv_x and rcv_y replace the listener coordinates the file states; the receiver key stays."""
    write_sofa(tmp_path / "pos_x-0pt5y1.sofa", np.zeros((2, 2, 20)), source=[[2, 0, 1.5]], listener=[[0, 0.5, 1.2]])
    params = {"glob": "*.sofa", "pattern": r"^pos_(?P<rcv>x(?P<rcv_x>-?[0-9pt]+)y(?P<rcv_y>-?[0-9pt]+))\.sofa$"}
    recs = records(tmp_path, params)
    assert [r.rcv_pos for r in recs] == [(-0.5, 1.0, 1.2)] * 2
    assert {r.rcv_key for r in recs} == {"x-0pt5y1"}


def test_sofa_ignore_positions(tmp_path):
    """Placeholder positions ((1, 0, 0) and (0, 0, 0)) are not published with ignore_positions."""
    write_sofa(tmp_path / "array.sofa", np.zeros((2, 4, 20)), source=[[1, 0, 0]], listener=[[0, 0, 0]])
    assert records(tmp_path, {"glob": "*.sofa"})[0].src_pos == (1, 0, 0)
    recs = records(tmp_path, {"glob": "*.sofa", "ignore_positions": True})
    assert [(r.src_pos, r.rcv_pos) for r in recs] == [(None, None)] * 2
    assert [r.src_key for r in recs] == ["m0", "m1"]                        # keys as before


def test_sofa_m_is_src_with_one_emitter(tmp_path):
    """One loudspeaker moved to 3 positions (E = 1): with m_is: src the measurement names the source."""
    write_sofa(tmp_path / "moved.sofa", np.zeros((3, 2, 20, 1)),
               source=[[1.5, -0.5, 1.25], [2, 0, 1.25], [0.25, 1, 1.25]], listener=[[0, 0, 1.25]])
    recs = records(tmp_path, {"glob": "*.sofa", "m_is": "src"})
    assert [r.local_key for r in recs] == ["moved.sofa#0#0", "moved.sofa#1#0", "moved.sofa#2#0"]
    assert [r.src_key for r in recs] == ["src_1.5_-0.5_1.25", "src_2_0_1.25", "src_0.25_1_1.25"]
    assert {r.rcv_key for r in recs} == {None}
    assert recs[1].src_pos == (2, 0, 1.25)
    before = records(tmp_path, {"glob": "*.sofa"})                         # without m_is: the emitter is the source
    assert [(r.src_key, r.rcv_key) for r in before] == [("e0", "m0"), ("e0", "m1"), ("e0", "m2")]
    write_sofa(tmp_path / "moved.sofa", np.zeros((3, 2, 20, 2)), source=np.ones((3, 3)))
    with pytest.raises(ValueError, match="one emitter"):
        records(tmp_path, {"glob": "*.sofa", "m_is": "src"})
