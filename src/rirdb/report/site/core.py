"""Rendering context shared by every page module of the rirdb website.

A page module exposes `render(site: Site) -> None` and writes its pages with `site.page(...)`
(or `site.write(...)` for non-HTML files). Every URL inside the site is relative, so the output folder
works from GitHub Pages, any static host, or a local file:// browser.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import jinja2
from markupsafe import Markup, escape

from rirdb import paths

HERE = Path(__file__).parent
OUT_DIR = paths.REPORTS_DIR / "site"
SITE_URL = "https://ilias-audio.github.io/room-impulse-responses/"
REPO_URL = "https://github.com/ilias-audio/room-impulse-responses"
REPO_BRANCH = "main"
SITE_NAME = "rirdb"
SITE_TAGLINE = "Room impulse response datasets: where to get them, what is inside, and what the analysis found"

# top navigation: (key, label, path)
NAV = [
    ("datasets", "Datasets", "datasets/index.html"),
    ("spaces", "Spaces", "spaces/index.html"),
    ("analysis", "Analysis", "analysis/index.html"),
    ("get", "Get the data", "get/index.html"),
    ("methods", "Methods", "methods/index.html"),
    ("guide", "Field Guide", "guide/index.html"),
    ("about", "About", "about/index.html"),
]


# ----------------------------------------------------------------------------- formatting filters

def fmt_int(v) -> str:
    if v is None or v == "":
        return "–"
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return str(v)


def fmt_num(v, nd: int = 2) -> str:
    if v is None or v == "":
        return "–"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f:  # NaN
        return "–"
    if round(f, nd) == 0:
        f = 0.0  # no "−0.00"
    s = f"{f:,.{nd}f}"
    return s.replace("-", "−") if s.startswith("-") else s


def fmt_pct(v, nd: int = 0) -> str:
    """Share as a percentage; a no-break space keeps the number and "%" on one line."""
    if v is None:
        return "–"
    p = float(v) * 100
    if 0 < p < 0.5 and nd == 0:
        return "<1 %"
    return f"{p:.{nd}f} %"


def fmt_bytes(v) -> str:
    """Decimal units (GB = 10^9 bytes), as in the hosting plan."""
    if v is None:
        return "–"
    v = float(v)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(v) < 1000 or unit == "TB":
            return f"{v:,.0f} {unit}" if unit == "B" else f"{v:,.1f} {unit}" if v < 100 else f"{v:,.0f} {unit}"
        v /= 1000
    return f"{v:.1f} TB"


def fmt_fs(v) -> str:
    if v is None:
        return "–"
    v = int(v)
    return f"{v / 1000:g} kHz"


def fmt_hz(v) -> str:
    if v is None:
        return "–"
    v = float(v)
    return f"{v / 1000:.1f} kHz" if v >= 1000 else f"{v:.0f} Hz"


def band_label(b) -> str:
    b = str(b)
    if b == "bb":
        return "broadband"
    v = int(b)
    return f"{v // 1000}k" if v >= 1000 else str(v)


FORMAT_LABELS = {
    "mono_omni": "Mono (omni)", "mono_directional": "Mono (directional)", "stereo": "Stereo", "binaural": "Binaural",
    "foa_fuma": "First-order Ambisonics (FuMa)",
    "foa_ambix": "First-order Ambisonics (AmbiX)", "hoa_sh": "Higher-order Ambisonics", "em32_raw": "Eigenmike em32 capsules",
    "sma_raw": "Spherical array capsules", "tetra_raw": "Tetrahedral A-format", "array_raw": "Microphone array",
    "sdm_array": "SDM array",
}
KIND_LABELS = {
    "room": "Rooms", "outdoor": "Outdoor", "vehicle": "Vehicles", "scale_model": "Scale models", "device": "Devices",
    "plate": "Plate reverbs", "spring": "Spring reverbs", "digital_reverb": "Digital reverbs", "creative": "Creative / processed",
    "anechoic": "Anechoic", "virtual": "Virtual / modelled",
}
SOURCE_LABELS = {
    "zenodo": "Zenodo", "depositonce": "DepositOnce (TU Berlin)", "figshare": "Figshare", "http": "Direct download (HTTP)",
    "wget_mirror": "Web directory (mirrored)", "github": "GitHub", "hf": "Hugging Face", "wayback": "Wayback Machine",
    "manual": "Manual download",
}


def record_url(kind: str, rec: str) -> str | None:
    """Landing page of a repository record, from the registry's record id."""
    if kind == "zenodo":
        return f"https://zenodo.org/records/{rec}"
    if kind == "figshare":
        return f"https://figshare.com/articles/dataset/{rec}"
    if kind == "depositonce":
        return f"https://depositonce.tu-berlin.de/items/{rec}"
    if kind == "github":
        repo, _, ref = rec.partition("@")
        return f"https://github.com/{repo}" + (f"/tree/{ref}" if ref else "")
    if kind == "hf":
        repo, _, ref = rec.partition("@")
        return f"https://huggingface.co/datasets/{repo}" + (f"/tree/{ref}" if ref else "")
    return None


def doi_url(doi: str | None) -> str | None:
    if not doi:
        return None
    return doi if doi.startswith("http") else f"https://doi.org/{doi}"


def repo_blob(path: str) -> str:
    return f"{REPO_URL}/blob/{REPO_BRANCH}/{path}"


def repo_raw(path: str) -> str:
    return f"{REPO_URL}/raw/{REPO_BRANCH}/{path}"


def tojson_script(o) -> Markup:
    """JSON for a <script type="application/json"> or JS literal, safe inside HTML."""
    return Markup(json.dumps(o, separators=(",", ":"), default=str).replace("</", "<\\/"))


def make_env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(HERE / "templates")),
        autoescape=jinja2.select_autoescape(["html", "xml"]),
        trim_blocks=True, lstrip_blocks=True, undefined=jinja2.StrictUndefined,
    )
    env.filters.update(int=fmt_int, num=fmt_num, pct=fmt_pct, bytes=fmt_bytes, fs=fmt_fs, hz=fmt_hz,
                       band=band_label, fmtlabel=lambda v: FORMAT_LABELS.get(v, v),
                       kindlabel=lambda v: KIND_LABELS.get(v, v), sourcelabel=lambda v: SOURCE_LABELS.get(v, v),
                       jsonscript=tojson_script, doi_url=doi_url)
    from rirdb.report.site import charts

    env.globals.update(record_url=record_url, repo_blob=repo_blob, repo_raw=repo_raw, REPO_URL=REPO_URL,
                       SITE_URL=SITE_URL, SITE_NAME=SITE_NAME, NAV=NAV, Markup=Markup, escape=escape,
                       charts=charts, FORMAT_LABELS=FORMAT_LABELS, KIND_LABELS=KIND_LABELS)
    return env


@dataclass
class Site:
    out: Path
    data: dict
    env: jinja2.Environment
    pages: list[str] = field(default_factory=list)       # site-relative paths, for the sitemap
    _ds_cache: dict = field(default_factory=dict)

    # --- data access
    @property
    def datasets(self) -> list[dict]:
        return self.data["datasets"]

    def dataset(self, dataset_id: str) -> dict:
        """Per-dataset detail (data/ds/<id>.json), cached."""
        if dataset_id not in self._ds_cache:
            from rirdb.report.site.data import load_dataset
            self._ds_cache[dataset_id] = load_dataset(self.out, dataset_id)
        return self._ds_cache[dataset_id]

    # --- writing
    def write(self, rel: str, content: str | bytes, sitemap: bool = False) -> Path:
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content)
        if sitemap:
            self.pages.append(rel)
        return p

    def copy(self, src: Path, rel: str) -> Path | None:
        if not src.exists():
            print(f"[site] missing {src}")
            return None
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, p)
        return p

    def page(self, rel: str, template: str, *, title: str, description: str = "", nav: str | None = None,
             jsonld: dict | list | None = None, sitemap: bool = True, **ctx) -> Path:
        """Render `template` to `rel` (e.g. "d/ace.html"). Templates get `root` (the relative prefix back to
        the site root, "" or "../"), `title`, `description`, `nav` (active top-nav key), `data` (site.json),
        `generated`, and whatever is passed in **ctx."""
        depth = rel.count("/")
        root = "../" * depth
        html = self.env.get_template(template).render(
            root=root, rel=rel, title=title, description=description, nav=nav, jsonld=jsonld,
            data=self.data, generated=self.data.get("generated"), canonical=SITE_URL + rel.removesuffix("index.html"),
            **ctx)
        return self.write(rel, html, sitemap=sitemap)

    def link(self, rel_from: str, target: str) -> str:
        """Relative link from page `rel_from` to site path `target`."""
        return "../" * rel_from.count("/") + target
