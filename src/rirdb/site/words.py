"""Words for the codes of the tables, in one place: the website says "First-order Ambisonics (FuMa)", never
`foa_fuma`. The codes stay in the data reference and in tooltips, for people who use the tables."""

from __future__ import annotations

import csv
from pathlib import Path

FORMATS = {"mono": "Mono", "stereo": "Stereo", "binaural": "Binaural", "ambisonic": "Ambisonic",
           "array": "Microphone array"}
SEARCH_FORMATS = ["mono", "stereo", "binaural", "ambisonic"]          # what `rirdb search --format` takes

LAYOUTS = {"mono_omni": "Omni microphone", "stereo": "Stereo pair", "binaural": "Binaural (dummy head or ears)",
           "foa_fuma": "First-order Ambisonics (FuMa)", "foa_ambix": "First-order Ambisonics (AmbiX)",
           "hoa_sh": "Higher-order Ambisonics", "em32_raw": "Eigenmike em32, raw capsules",
           "sma_raw": "Spherical array, raw capsules", "tetra_raw": "Tetrahedral microphone, raw capsules (A-format)",
           "array_raw": "Microphone array", "sdm_array": "Array for the spatial decomposition method (SDM)",
           "mono_directional": "Directional microphone (cardioid or similar)"}

ORIGINS = {"measured": "Measured", "simulated": "Simulated", "device": "Effects and devices"}

ROOM_TYPES = {
    "concert_hall": "Concert hall", "theatre_auditorium": "Theatre or auditorium", "worship": "Church or place of worship",
    "lecture_classroom": "Classroom or lecture room", "office_meeting": "Office or meeting room",
    "studio_booth": "Studio or booth", "domestic": "Home", "laboratory": "Laboratory",
    "public_interior": "Public building", "hospitality_retail": "Restaurant, bar or shop",
    "circulation": "Corridor, hall or lobby", "stairwell": "Stairwell", "sports_leisure": "Sports or leisure",
    "transport_hub": "Station or terminal", "industrial": "Industrial building", "large_structure": "Large structure",
    "underground": "Underground: cave, cellar, tunnel", "tunnel_underpass": "Tunnel or underpass",
    "outdoor": "Outdoor", "vehicle": "Vehicle", "anechoic": "Anechoic room", "scale_model": "Scale model",
    "virtual": "Simulated room", "device": "Device or plugin", "unknown": "Not stated",
}

# The kind of a space, for the main filter and the colours of the plots (select.kind_of, `--kind`, for one space):
# a device or plugin, a simulated room, an anechoic room, outdoors, or indoors (rooms and halls, caves, tunnels,
# vehicle cabins).
def kind_of(space_type: str, origin: str) -> str:
    if origin == "device" or space_type == "device":
        return "device"
    if origin == "simulated" or space_type in ("virtual", "scale_model"):
        return "simulated"
    if space_type in ("anechoic", "outdoor"):
        return space_type
    return "indoor"


KINDS = {"indoor": "Indoor rooms", "outdoor": "Outdoor", "anechoic": "Anechoic", "simulated": "Simulated",
         "device": "Devices and plugins"}

PROVIDES = {"positions": "Source and receiver positions", "dimensions": "Room dimensions", "volume": "Room volume",
            "geometry": "Geometry: a 3D model or plans", "materials": "Surface materials", "images": "Photos",
            "reference_acoustics": "The authors' own room values (T60…)", "conditions": "Room conditions they vary"}
PROVIDES_SHORT = {"positions": "positions", "dimensions": "dimensions", "volume": "volume", "geometry": "geometry",
                  "materials": "materials", "images": "photos", "reference_acoustics": "reference values",
                  "conditions": "conditions"}
AVAIL = {"yes": "Yes", "partial": "Partly", "no": "No", "": "Not checked"}

USES = {"research": "Research", "commercial": "Commercial use", "training": "ML training",
        "redistribute": "Redistribution"}
USE_VALUES = {"yes": "allowed", "no": "not allowed", "unknown": "not stated", "unaltered_only": "unaltered only"}

GRADES = {"S": "S: valid decay in every octave from 125 Hz to 4 kHz, no defect found",
          "A": "A: valid decay in every octave from 125 Hz to 4 kHz, minor defects at most",
          "B": "B: valid decay from 250 Hz to 2 kHz",
          "C": "C: some decay value valid, or a major defect",
          "D": "D: no decay value valid, or a known defect of the files as published"}

STATUS = {"measured": "Measured by RIRDB", "indexed": "Downloaded and listed; measured in the next release",
          "pinned": "Its files are pinned; downloaded in the next run",
          "catalogued": "Described from its authors' pages; not downloaded yet",
          "manual": "Needs a manual download (a form or a licence to accept)",
          "planned": "Planned for a later release", "deferred": "Left for a later release",
          "broken": "Its download does not work at the moment"}

# The reverberation-time ranges of the filters; the edges are those of the T30 bins (data.T30_EDGES), so each
# range is exact on the page and on the command line (`--t30 0.63-1.25`).
REVERB = [("dry", "Dry, under 0.3 s", 0.0, 0.315), ("room", "Small rooms, 0.3 to 0.6 s", 0.315, 0.63),
          ("class", "Classrooms, 0.6 to 1.25 s", 0.63, 1.25), ("hall", "Halls and churches, 1.25 to 2.5 s", 1.25, 2.5),
          ("live", "Very live, over 2.5 s", 2.5, None)]


def octave(hz: float) -> str:
    return {31: "31.5 Hz"}.get(int(hz), f"{hz:g} Hz" if hz < 1000 else f"{hz / 1000:g} kHz")


# ------------------------------------------------------------------------------------------- licences

_LICENCES = None


def licence_name(spdx: str) -> str:
    """The licence's full name from licences.csv, or words for the RIRDB placeholders."""
    global _LICENCES
    if _LICENCES is None:
        with open(Path(__file__).parents[1] / "licences.csv", newline="") as f:
            _LICENCES = {r["id"]: r["name"] for r in csv.DictReader(f)}
    special = {"LicenseRef-per-space": "Each room has its own licence", "LicenseRef-unspecified": "No licence stated",
               "LicenseRef-custom": "The authors' own terms", "LicenseRef-assumed-free": "No licence stated; assumed free",
               "LicenseRef-research-only": "Research use only"}
    return special.get(spdx) or _LICENCES.get(spdx) or spdx


def licence_short(spdx: str) -> str:
    """CC-BY-NC-SA-4.0 -> CC BY-NC-SA 4.0; placeholders in words."""
    if not spdx:
        return "Unknown"
    if spdx.startswith("LicenseRef-"):
        return {"LicenseRef-per-space": "Varies per room", "LicenseRef-unspecified": "No licence stated",
                "LicenseRef-custom": "Own terms", "LicenseRef-assumed-free": "No licence stated",
                "LicenseRef-research-only": "Research only"}.get(spdx, spdx[11:].replace("-", " ") + " licence")
    names = {"CC0-1.0": "CC0", "CC-PDM-1.0": "Public domain", "Apache-2.0": "Apache 2.0", "BSD-3-Clause": "BSD 3-Clause",
             "GPL-3.0-only": "GPL 3.0", "ODC-By-1.0": "ODC-By 1.0", "MIT": "MIT"}
    if spdx in names:
        return names[spdx]
    head, _, version = spdx.rpartition("-")
    return head.replace("CC-BY", "CC BY") + " " + version


# ------------------------------------------------------------------------------------------- labels

def room_label(label: str, space: str) -> str:
    """A space's name as published, tidied: underscores to spaces, a capital first letter."""
    text = (label or space.split("/", 1)[-1]).replace("_", " ").strip(" -")
    return text[:1].upper() + text[1:]
