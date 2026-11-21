"""Dataset registry: one folder per dataset, `registry/<id>/`, the single source of truth.

    registry/<id>/dataset.yaml   by hand: name, licence (every statement found), sources, how to read the files
    registry/<id>/spaces.csv     by hand: the spaces that differ from the dataset's defaults or carry extra facts
    registry/<id>/downloads.tsv  written by `fetch/resolve.sh`: one row per download, with its checksum
    registry/<id>/files.tsv.gz   written by `fetch/inventory.sh`: one row per extracted file, with its sha256

A dataset that is catalogued but not downloaded is a `dataset.yaml` alone (status `catalogued`).
Each `dataset.yaml` is validated against the models below. Python never touches the network;
`compile_sources` writes a flat TSV that the bash fetch layer (`fetch/*.sh`) reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rirdb import paths

DatasetType = Literal["measured", "production", "simulated", "hybrid"]
IRKind = Literal[
    "room",          # enclosed architectural space
    "outdoor",       # forests, streets, canyons, ...
    "vehicle",       # car / train cabins
    "scale_model",   # physical scale models
    "device",        # loudspeakers, telephones, cabinets
    "plate",         # plate reverb units
    "spring",        # spring reverb units
    "digital_reverb",  # hardware / software algorithmic reverbs
    "creative",      # processed / effect IRs
    "anechoic",      # anechoic-chamber measurements (kept, excluded from room statistics)
    "virtual",       # computer models, auralizations, virtual reconstructions inside measured datasets
]
CaptureFormat = Literal[
    "mono_omni",
    "mono_directional",  # one directional microphone (cardioid, supercardioid, ...): the pattern is the channel role
    "stereo",
    "binaural",
    "foa_fuma",      # first-order B-format, FuMa ordering/normalisation
    "foa_ambix",     # first-order, ACN/SN3D
    "hoa_sh",        # higher-order spherical-harmonic signals (order in params)
    "em32_raw",      # Eigenmike em32 capsule signals
    "sma_raw",       # other spherical array capsules (Zylia, Voyage, ...)
    "tetra_raw",     # tetrahedral A-format
    "array_raw",     # linear / planar / circular / distributed arrays
    "sdm_array",     # open arrays for the Spatial Decomposition Method
]
Redistribute = Literal["yes", "unaltered_only", "no", "unknown"]
SourceKind = Literal[
    "zenodo", "depositonce", "figshare", "dataverse", "mendeley", "http", "wget_mirror", "github", "hf", "wayback", "manual"
]
Access = Literal["open", "registration", "manual", "broken"]
Status = Literal["catalogued", "planned", "active", "manual", "broken", "deferred"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def known_licence(v: str) -> str:
    from rirdb.licences import table

    if v not in table():
        raise ValueError(f"licence id {v!r} has no row in src/rirdb/licences.csv: add one")
    return v


class Statement(_Strict):
    """One licence statement found for a dataset, and where it was read."""
    licence: str                    # a licence id with a row in licences.csv
    where: str                      # e.g. "Zenodo record", "LICENSE.txt in the archive"

    @field_validator("licence")
    @classmethod
    def _known(cls, v: str) -> str:
        return known_licence(v)


class License(_Strict):
    spdx: str                       # SPDX id, or LicenseRef-... for custom terms
    # Where the statements found conflict, every one is listed and `spdx` is the strictest
    # (licences.strictest). With a single statement, the list stays empty.
    statements: list[Statement] = Field(default_factory=list)
    url: str | None = None
    redistribute_audio: Redistribute = "unknown"
    # May the audio be used to train ML models? Explicit value wins; otherwise
    # derived from the licence (see `training`): open licences -> allowed
    # (NC: non-commercial research only), unspecified/custom -> unknown.
    training_use: Literal["allowed", "prohibited", "ask", "unknown"] | None = None
    note: str | None = None

    @property
    def uses(self):
        """What the licence allows: research, commercial use, training, redistribution (rirdb.licences)."""
        from rirdb.licences import permitted

        explicit = {"allowed": "yes", "prohibited": "no"}.get(self.training_use or "")
        return permitted(self.spdx, training=explicit, redistribute=self.redistribute_audio)

    @property
    def training(self) -> str:
        """ML training in the words the index and the reports use.

        allowed | non_commercial_only | prohibited | ask | unknown.
        A non-commercial licence never reads as plain "allowed".
        """
        if self.training_use in ("ask", "prohibited"):
            return self.training_use
        uses = self.uses
        if uses.training == "no":
            return "prohibited"
        if uses.training == "yes":
            return "non_commercial_only" if uses.non_commercial else "allowed"
        return "unknown"

    @field_validator("spdx")
    @classmethod
    def _known(cls, v: str) -> str:
        return known_licence(v)

    @model_validator(mode="after")
    def _strictest(self) -> "License":
        from rirdb.licences import strictest

        if len(self.statements) == 1:
            raise ValueError("list licence statements only where they conflict (two or more)")
        if self.statements and self.spdx != strictest([s.licence for s in self.statements]):
            raise ValueError(f"the statements conflict, so the licence must be the strictest of them, "
                             f"{strictest([s.licence for s in self.statements])!r}, not {self.spdx!r}")
        return self

    @field_validator("redistribute_audio", mode="before")
    @classmethod
    def _yaml_bool(cls, v):
        # YAML 1.1 (PyYAML) parses bare yes/no as booleans
        if isinstance(v, bool):
            return "yes" if v else "no"
        return v

    @property
    def non_commercial(self) -> bool:
        return self.uses.non_commercial

    @property
    def share_alike(self) -> bool:
        return self.uses.share_alike

    @property
    def no_derivatives(self) -> bool:
        return self.uses.no_derivatives


class Source(_Strict):
    kind: SourceKind
    # zenodo: record ids; depositonce: item uuids; figshare: article ids;
    # github: "owner/repo@ref"; hf: "org/name@revision"; dataverse: "host/doi:..."; mendeley: "id@version"
    records: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)      # http / wget_mirror / wayback
    include: list[str] = Field(default_factory=list)   # fnmatch globs on file names; empty = all
    exclude: list[str] = Field(default_factory=list)
    accept: list[str] = Field(default_factory=list)    # wget_mirror --accept patterns
    unpack_exclude: list[str] = Field(default_factory=list)  # globs on paths inside tar archives, not unpacked
    instructions: str | None = None                    # manual / registration datasets

    @model_validator(mode="after")
    def _check(self) -> "Source":
        needs_records = {"zenodo", "depositonce", "figshare", "dataverse", "mendeley", "github", "hf"}
        needs_urls = {"http", "wget_mirror", "wayback"}
        if self.kind in needs_records and not self.records:
            raise ValueError(f"source kind {self.kind!r} needs `records`")
        if self.kind in needs_urls and not self.urls:
            raise ValueError(f"source kind {self.kind!r} needs `urls`")
        if self.kind == "manual" and not self.instructions:
            raise ValueError("manual source needs `instructions`")
        return self


class Expected(_Strict):
    n_irs: int | None = None        # single-channel-or-multichannel IR measurements, as the authors count them
    n_rooms: int | None = None
    note: str | None = None


class Adapter(_Strict):
    name: str | None = None         # filled in P3 after `rirdb dev probe`
    params: dict = Field(default_factory=dict)


class Analysis(_Strict):
    scope: Literal["full", "subsample_v1", "none"] = "full"
    reference_role: str | None = None    # channel role analysed for arrays (e.g. "mic_0"); "mean": all channels
    subsample: dict | None = None
    keep_s: float | None = None          # analyse only this long from where the sound starts (run.analysed_part)
    start_s: float | None = None         # analyse from this many seconds into the file: a recording whose impulse comes late


class Paper(_Strict):
    title: str
    url: str


Avail = Literal["yes", "partial", "no"]


class RoomInfo(_Strict):
    """What a dataset publishes about its spaces. None = not checked; partial = some rooms or approximate.

    Room-level values (volume, dimensions) go in registry/<id>/spaces.csv or a rooms.py enricher.
    """
    dimensions: Avail | None = None           # room size (L x W x H) or a dimensioned plan
    volume: Avail | None = None               # volume stated (or exact from simple dimensions)
    geometry: Avail | None = None             # 3-D model: CAD, mesh, point cloud
    materials: Avail | None = None            # surface materials or absorption data
    positions: Avail | None = None            # source and receiver coordinates
    images: Avail | None = None               # photos, panoramas or layout drawings
    reference_acoustics: Avail | None = None  # published T60 / ISO 3382 values
    conditions: Avail | None = None           # variable acoustics: panels, furniture, absorbers, doors
    where: str | None = None                  # files or paper section holding it

    @property
    def checked(self) -> bool:
        return any(getattr(self, f) is not None for f in AVAIL_FIELDS)


AVAIL_FIELDS = ("dimensions", "volume", "geometry", "materials", "positions", "images", "reference_acoustics", "conditions")


class WebCheck(_Strict):
    """What a check of the dataset's web pages found (the survey of 2026-10-14), in plain words for the site."""
    checked: str | None = None             # date of the check
    found_as: str | None = None            # the name it was found under
    availability: Literal["open", "free_registration", "offline", "archived"] | None = None
    families: list[Literal["mono", "stereo", "binaural", "ambisonic", "array"]] = Field(default_factory=list)
    host: str | None = None
    size: str | None = None
    file_formats: str | None = None
    institution: str | None = None
    country: str | None = None
    evidence_urls: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] | None = None
    caveats: list[str] = Field(default_factory=list)
    licence_note: str | None = None        # the licence situation in plain words
    doi_is_paper: bool | None = None       # the registry's doi names the paper, not the data
    n_irs_display: int | None = None       # a count to show instead of the indexed one
    n_rooms_display: int | None = None
    content: str | None = None             # a description to show instead of `content`


class KnownDefect(_Strict):
    """A defect of the files as published that RIRDB cannot repair. The IRs stay in the table, with the defect in
    irs.known_defect; rirdb search, rirdb get and the website warn about it."""
    what: str                      # one or two sentences, said to the user as they are
    files: str | None = None       # a regex searched in each IR's file path; None: every IR of the dataset


class Dataset(_Strict):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    year: int | None = None
    type: DatasetType
    ir_kinds: list[IRKind] = Field(default_factory=list)
    capture_formats: list[CaptureFormat] = Field(default_factory=list)
    fs_hz: list[int] = Field(default_factory=list)   # empty = to be confirmed by probe
    content: str
    expected: Expected = Field(default_factory=Expected)
    room_info: RoomInfo = Field(default_factory=RoomInfo)
    # The type of every space, where the naming rules must not decide (a library of modelled IRs).
    # A space's own row in spaces.csv still wins.
    space_type: str | None = None
    license: License
    access: Access | None = None          # empty for a catalogued dataset
    source: Source | None = None          # empty for a catalogued dataset
    keep_archive: bool | None = None      # None: keep only if archives total < 5 GB
    wave: int | None = Field(default=None, ge=0, le=4)
    adapter: Adapter = Field(default_factory=Adapter)
    analysis: Analysis = Field(default_factory=Analysis)
    # Only for datasets above 100,000 IRs: how many IRs are measured per space, condition and layout
    # (the `cap` with the smallest ir_id). Every IR stays listed and downloadable (data-model.md, section 3).
    cap: int | None = Field(default=None, ge=1)
    homepage: str | None = None
    paper: Paper | None = None
    doi: str | None = None
    status: Status
    notes: str | None = None
    defects: list[KnownDefect] = Field(default_factory=list)
    web_check: WebCheck | None = None

    @field_validator("content")
    @classmethod
    def _one_line(cls, v: str) -> str:
        return " ".join(v.split())

    @model_validator(mode="after")
    def _catalogued_or_sourced(self) -> "Dataset":
        if self.status != "catalogued" and (self.source is None or self.access is None):
            raise ValueError(f"{self.id}: a dataset with status {self.status!r} needs `access` and `source`")
        return self

    @property
    def spatial(self) -> bool:
        return any(
            f in self.capture_formats
            for f in ("binaural", "foa_fuma", "foa_ambix", "hoa_sh", "em32_raw", "sma_raw", "tetra_raw", "sdm_array")
        )

    @property
    def fetchable(self) -> bool:
        return self.access == "open" and self.status in ("planned", "active")


def load_registry(registry_dir: Path = paths.DATASETS_DIR, catalogued: bool = True) -> list[Dataset]:
    """Every registry/<id>/dataset.yaml, sorted by id. The folder name must be the id.

    `catalogued=False` leaves out the datasets known from their web pages only (nothing to fetch or index).
    """
    datasets = []
    for f in sorted(registry_dir.glob("*/dataset.yaml")):
        d = Dataset.model_validate(yaml.safe_load(f.read_text()))
        if d.id != f.parent.name:
            raise ValueError(f"{f}: id {d.id!r} differs from its folder name")
        if catalogued or d.status != "catalogued":
            datasets.append(d)
    return datasets


def get_dataset(dataset_id: str, datasets: list[Dataset] | None = None) -> Dataset:
    if datasets is None:
        f = paths.dataset_dir(dataset_id) / "dataset.yaml"
        if not f.exists():
            raise KeyError(f"unknown dataset id {dataset_id!r}")
        return Dataset.model_validate(yaml.safe_load(f.read_text()))
    for d in datasets:
        if d.id == dataset_id:
            return d
    raise KeyError(f"unknown dataset id {dataset_id!r}")


# ---------------------------------------------------------------------------
# Compile for the bash fetch layer
# ---------------------------------------------------------------------------

SOURCES_TSV_COLUMNS = (
    "id", "wave", "status", "access", "kind", "records", "urls",
    "include", "exclude", "accept", "keep_archive", "unpack_exclude",
)


def compile_sources(datasets: list[Dataset], out: Path) -> Path:
    """Flatten the registry into a TSV (lists joined with '|') for fetch/*.sh."""

    def cell(v) -> str:
        if isinstance(v, list):
            v = "|".join(v)
        s = "" if v is None else str(v)
        if "\t" in s or "\n" in s:
            raise ValueError(f"tab/newline in registry value {s!r}")
        return s if s else "-"

    lines = ["\t".join(SOURCES_TSV_COLUMNS)]
    for d in datasets:
        if d.source is None:          # catalogued only: nothing to fetch
            continue
        keep = "auto" if d.keep_archive is None else str(int(d.keep_archive))
        s = d.source
        lines.append("\t".join(cell(v) for v in (
            d.id, d.wave, d.status, d.access, s.kind, s.records, s.urls,
            s.include, s.exclude, s.accept, keep, s.unpack_exclude,
        )))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    return out
