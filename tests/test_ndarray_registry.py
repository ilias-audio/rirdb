"""Every reader the registry names exists, so `rirdb dev index` cannot meet an unknown adapter."""

from rirdb.adapters import ADAPTERS
from rirdb.registry import load_registry


def test_every_adapter_in_the_registry_is_known():
    unknown = {d.id: d.adapter.name for d in load_registry() if d.adapter.name and d.adapter.name not in ADAPTERS}
    assert not unknown, unknown


def test_the_matlab_and_numpy_readers_are_registered():
    assert {"ndarray", "mat_struct", "ita_audio_mat", "anu_md_omp", "otimp_mat",
            "npz_scene"} <= set(ADAPTERS)
