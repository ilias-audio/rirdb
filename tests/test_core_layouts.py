"""Every layout (registry.CaptureFormat) has its words wherever a layout is shown, so adding one cannot miss a place.

readme.py raises KeyError at `rirdb dev registry compile` for a layout it has no label for."""

import re
import typing
from pathlib import Path

import pytest

from rirdb import build, readme
from rirdb.registry import CaptureFormat
from rirdb.report.site import core, pages_catalog
from rirdb.site import words

LAYOUTS = list(typing.get_args(CaptureFormat))
GUIDE = Path(__file__).resolve().parents[1] / "src" / "rirdb" / "report" / "guide_template.html"


@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_layout_has_words(layout):
    assert layout in words.LAYOUTS
    assert layout in readme._FORMAT_LABELS
    assert layout in core.FORMAT_LABELS
    assert layout in pages_catalog.FAM
    assert re.search(rf"\b{layout}: '", GUIDE.read_text()), "guide_template.html FAM"


def test_a_directional_microphone_is_published_as_mono():
    assert build.FORMAT_OF_LAYOUT["mono_directional"] == "mono"
    assert pages_catalog.FAM["mono_directional"] == "mono"
