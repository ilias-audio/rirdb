"""fetch.py: the generated dataset is downloaded from its local site (conftest.py, `toy_site`), resumed, verified,
and taken from a folder; a changed byte fails. No network."""

from __future__ import annotations

import shutil
import subprocess
import zipfile

import pytest

from rirdb import client
from rirdb.fetch import FetchError, fetch_dataset, files_dir, sha256_of, split_part_of, unpack
from tests.toy import DUPLICATE, TOY_FILES

pytestmark = pytest.mark.xdist_group("toy")
ALL = sorted([*TOY_FILES, DUPLICATE[0]])


def cached_files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_a_dataset_is_fetched_once_and_verified(user, toy_site):
    _, _, site = toy_site
    before = site.hits.get("/toy.zip", 0)
    root = fetch_dataset("toy")
    assert cached_files(root) == ALL
    files = client.table("files.toy").set_index("file")["sha256"]
    assert all(sha256_of(root / f) == files[f] for f in ALL)
    assert not (root.parent.parent / "downloads" / "toy" / "toy.zip").exists()       # the archive is deleted
    fetch_dataset("toy")                                                               # all there: no download
    assert site.hits["/toy.zip"] == before + 1


def test_only_the_needed_files_are_unpacked(user):
    root = fetch_dataset("toy", needed=["booth/s1_m1.wav"])
    assert cached_files(root) == ["booth/s1_m1.wav"]
    with pytest.raises(FetchError, match="not files of the release"):
        fetch_dataset("toy", needed=["nope.wav"])


def test_a_cut_download_resumes(user, toy_site):
    folder, _, site = toy_site
    whole = (folder / "toy.zip").read_bytes()
    part = files_dir("toy").parent.parent / "downloads" / "toy" / "toy.zip.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(whole[:1000])                                   # a download cut after 1000 bytes
    fetch_dataset("toy")
    assert site.ranges[-1] == "bytes=1000-"
    assert cached_files(files_dir("toy")) == ALL


def test_a_changed_byte_fails_and_is_deleted(user, toy_site):
    folder, _, _ = toy_site
    archive = files_dir("toy").parent.parent / "downloads" / "toy" / "toy.zip"
    archive.parent.mkdir(parents=True)
    data = bytearray((folder / "toy.zip").read_bytes())
    data[100] ^= 1
    archive.write_bytes(bytes(data))                                 # as if the download had been corrupted
    with pytest.raises(FetchError, match="does not match its sha256"):
        fetch_dataset("toy")
    assert not archive.exists()


def test_from_a_folder_of_files_or_of_downloads(user, toy_site, tmp_path):
    folder, _, site = toy_site
    hits = dict(site.hits)
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(folder / "toy.zip") as z:
        z.extractall(unpacked)
    assert cached_files(fetch_dataset("toy", from_dir=unpacked)) == ALL
    shutil.rmtree(files_dir("toy"))
    archives = tmp_path / "archives"
    archives.mkdir()
    shutil.copy2(folder / "toy.zip", archives / "toy.zip")
    assert cached_files(fetch_dataset("toy", from_dir=archives)) == ALL
    assert site.hits == hits                                          # nothing was downloaded
    shutil.rmtree(files_dir("toy"))
    (unpacked / "booth" / "s1_m1.wav").write_bytes(b"not the file")
    with pytest.raises(FetchError, match="does not match"):
        fetch_dataset("toy", from_dir=unpacked)


@pytest.mark.skipif(shutil.which("zip") is None, reason="needs Info-ZIP's zip to make a split zip")
def test_a_split_zip_is_unpacked(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(bytes(range(256)) * 1000)
    (src / "b.bin").write_bytes(bytes(range(255, -1, -1)) * 1000)
    subprocess.run(["zip", "-q", "-0", "-s", "64k", str(tmp_path / "x.zip"), "a.bin", "b.bin"], cwd=src, check=True)
    assert (tmp_path / "x.z01").exists()
    unpack(tmp_path / "x.zip", tmp_path / "out")
    assert (tmp_path / "out" / "a.bin").read_bytes() == (src / "a.bin").read_bytes()
    assert (tmp_path / "out" / "b.bin").read_bytes() == (src / "b.bin").read_bytes()


def test_the_parts_of_a_split_zip_are_known_by_name():
    assert split_part_of("TAU-SRIR_DB.z01", "TAU-SRIR_DB.zip") and split_part_of("TAU-SRIR_DB.z12", "TAU-SRIR_DB.zip")
    assert not split_part_of("TAU-SRIR_DB.zip", "TAU-SRIR_DB.zip") and not split_part_of("other.z01", "TAU-SRIR_DB.zip")


def test_a_download_cut_short_without_an_error_resumes(toy_site, tmp_path):
    from rirdb.fetch import download

    folder, url, site = toy_site
    dest = tmp_path / "toy.zip"
    site.cut_after = 1000                          # the server closes the connection after 1000 bytes
    try:
        with pytest.raises(FetchError, match="closed after"):
            download(url + "toy.zip", dest, attempts=1)
    finally:
        site.cut_after = None
    assert not dest.exists() and (tmp_path / "toy.zip.part").stat().st_size == 1000
    download(url + "toy.zip", dest)
    assert site.ranges[-1] == "bytes=1000-" and dest.read_bytes() == (folder / "toy.zip").read_bytes()


def test_from_never_changes_the_users_folder(user, toy_site, tmp_path):
    folder, _, site = toy_site
    mine = tmp_path / "mine"
    mine.mkdir()
    bad = bytearray((folder / "toy.zip").read_bytes())
    bad[100] ^= 1
    (mine / "toy.zip").write_bytes(bytes(bad))                     # a cut or changed download of the user's
    with pytest.raises(FetchError, match="does not match"):
        fetch_dataset("toy", from_dir=mine)
    assert (mine / "toy.zip").read_bytes() == bytes(bad)            # still there, unchanged
    hits = dict(site.hits)
    assert cached_files(fetch_dataset("toy", from_dir=tmp_path / "empty")) == ALL     # not in the folder: downloaded
    assert site.hits["/toy.zip"] == hits.get("/toy.zip", 0) + 1
