"""Dataset adapters, looked up by the registry's `adapter.name`."""

from __future__ import annotations

from rirdb.adapters.asn_database import AsnJsonAdapter
from rirdb.adapters.base import Adapter, IRRecord, make_ir_id, slug
from rirdb.adapters.computed import H5SpectraAdapter, StftSubbandAdapter, TfMatAdapter
from rirdb.adapters.csv_tree import CsvTreeAdapter
from rirdb.adapters.custom import (AirMatAdapter, AnuMdOmpAdapter, MatArrayAdapter, NpzSceneAdapter, OpenAirAdapter,
                                   OpenSLR28Adapter, OtimpAdapter, SofaAdapter, TauSrirAdapter)
from rirdb.adapters.h5_sets import H5SetsAdapter
from rirdb.adapters.iem_digital_twins import IemDigitalTwinsAdapter
from rirdb.adapters.ita_audio import ItaAudioMatAdapter
from rirdb.adapters.kinect_rir import KinectRirAdapter
from rirdb.adapters.mat_struct import MatStructAdapter
from rirdb.adapters.ndarray import NdArrayAdapter
from rirdb.adapters.rir_mega import RirMegaAdapter
from rirdb.adapters.stonehenge import StonehengeOctaveAdapter
from rirdb.adapters.tugraz_speech_rirs import TugrazSpeechRirAdapter
from rirdb.adapters.wav_channels import WavChannelsAdapter
from rirdb.adapters.wav_tree import WavTreeAdapter

ADAPTERS: dict[str, type] = {
    "wav_tree": WavTreeAdapter,
    "sofa": SofaAdapter,
    "openair": OpenAirAdapter,
    "air_mat": AirMatAdapter,
    "openslr28": OpenSLR28Adapter,
    "mat_array": MatArrayAdapter,
    "ndarray": NdArrayAdapter,
    "mat_struct": MatStructAdapter,
    "ita_audio_mat": ItaAudioMatAdapter,
    "tau_srir": TauSrirAdapter,
    "anu_md_omp": AnuMdOmpAdapter,
    "otimp_mat": OtimpAdapter,
    "npz_scene": NpzSceneAdapter,
    "h5_sets": H5SetsAdapter,
    "h5_freqresp": H5SpectraAdapter,
    "tf_mat": TfMatAdapter,
    "stft_subband": StftSubbandAdapter,
    "wav_channels": WavChannelsAdapter,
    "csv_tree": CsvTreeAdapter,
    "kinect_rir_txt": KinectRirAdapter,
    "asn_json": AsnJsonAdapter,
    "rir_mega_meta": RirMegaAdapter,
    "stonehenge_octave": StonehengeOctaveAdapter,
    "tugraz_rir_bin": TugrazSpeechRirAdapter,
    "iem_digital_twins": IemDigitalTwinsAdapter,
}


def get_adapter(name: str | None, params: dict | None = None) -> Adapter:
    if not name:
        raise KeyError("dataset has no adapter configured yet (run `rirdb dev probe` and set adapter.name)")
    try:
        adapter = ADAPTERS[name]()
    except KeyError as e:
        raise KeyError(f"unknown adapter {name!r}; known: {sorted(ADAPTERS)}") from e
    adapter._params = params or {}      # registry adapter.params, for adapters whose load() needs them
    return adapter


__all__ = ["ADAPTERS", "Adapter", "IRRecord", "get_adapter", "make_ir_id", "slug"]
