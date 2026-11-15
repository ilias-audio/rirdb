"""The website (site.py), built from the generated dataset's release over local HTTP (conftest.py, `user`)."""

from __future__ import annotations

import pytest

from rirdb.site import build

pytestmark = pytest.mark.xdist_group("toy")


def test_the_site_has_a_page_per_measured_dataset(user, tmp_path):
    out = build(tmp_path / "site")
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*.html")) == [
        "columns.html", "d/toy.html", "datasets.html", "index.html"]
    toy = (out / "d" / "toy.html").read_text()
    assert "Toy dataset (generated for the tests)" in toy and "CC0-1.0" in toy and "rirdb get --dataset toy" in toy
    assert 'href="../style.css"' in toy and (out / "style.css").exists()
    assert "<b>7</b>" in (out / "index.html").read_text()                     # 7 IRs in the tables
    assert "<code>ir_id</code>" in (out / "columns.html").read_text()
