"""Cross-dataset neighbours, leave-one-dataset-out radii, realism and coverage (rirdb.maps)."""

import numpy as np

from rirdb.maps import cross_knn, radius_without, realism_coverage, unit


def _clusters(seed=0, n=40, dim=16):
    rng = np.random.default_rng(seed)
    centres = unit(rng.normal(size=(3, dim)))
    X = unit(np.repeat(centres, n, axis=0) + 0.05 * rng.normal(size=(3 * n, dim)))
    ds = np.repeat(np.array(["a", "b", "c"]), n)
    return X, ds


def test_cross_knn_matches_brute_force_and_skips_own_dataset():
    X, ds = _clusters()
    idx, dist = cross_knn(X, ds, keep=8, chunk=7)
    assert (ds[idx] != ds[:, None]).all()
    assert (np.diff(dist, axis=1) >= -1e-6).all()
    D = 1 - X @ X.T
    D[ds[:, None] == ds[None, :]] = np.inf
    np.testing.assert_allclose(dist, np.sort(D, axis=1)[:, :8], atol=1e-5)


def test_radius_without_held_out_dataset_grows():
    X, ds = _clusters()
    idx, dist = cross_knn(X, ds, keep=60)
    r_all, short = radius_without(idx, dist, ds, None, k=5)
    np.testing.assert_allclose(r_all, dist[:, 4])
    assert short == 0
    m = ds != "b"
    r_wo, short = radius_without(idx[m], dist[m], ds, "b", k=5)
    assert short == 0 and (r_wo >= r_all[m] - 1e-7).all()
    # rows of "a" can only use "c" now: brute force over "c"
    D = 1 - X[ds == "a"] @ X[ds == "c"].T
    np.testing.assert_allclose(r_wo[ds[m] == "a"], np.sort(D, axis=1)[:, 4], atol=1e-5)


def test_realism_coverage_extremes():
    X, ds = _clusters()
    room = np.repeat(np.arange(6), 20)
    r = np.full(len(X), 1e-5, np.float32)
    same = realism_coverage(X[:10], X, r, room)
    assert same["realism"] == 1.0
    assert abs(same["coverage_ir"] - 10 / len(X)) < 1e-4 and abs(same["coverage_room"] - 1 / 6) < 1e-4
    far = realism_coverage(unit(-X[:10]), X, r, room)
    assert far["realism"] == 0.0 and far["coverage_ir"] == 0.0
    # a query never counts against its own dataset (random real baselines)
    own = realism_coverage(X, X, r, room, q_ds=ds, r_ds=ds)
    assert own["realism"] == 0.0
