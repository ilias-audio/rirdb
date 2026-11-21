"""fetch.py: the generated dataset is downloaded from its local site (conftest.py, `toy_site`), resumed, verified,
and taken from a folder; a changed byte fails. No network."""

from __future__ import annotations

import gzip
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


def test_a_tar_cut_into_gzipped_pieces_is_unpacked(tmp_path):
    """MB-RIRs publishes mb.tar as mb.tar.part.aa.gz, ab, ...: the first piece stands for the archive."""
    src = tmp_path / "src" / "mb"
    src.mkdir(parents=True)
    (src / "a.bin").write_bytes(bytes(range(256)) * 1000)
    (src / "b.bin").write_bytes(bytes(range(255, -1, -1)) * 1000)
    subprocess.run(["tar", "-cf", str(tmp_path / "mb.tar"), "mb"], cwd=tmp_path / "src", check=True)
    data = (tmp_path / "mb.tar").read_bytes()
    for k, name in enumerate(("aa", "ab", "ac")):              # three pieces, each gzipped on its own
        (tmp_path / f"mb.tar.part.{name}.gz").write_bytes(gzip.compress(data[k * 200_000:(k + 1) * 200_000]))
    unpack(tmp_path / "mb.tar.part.aa.gz", tmp_path / "out")
    assert (tmp_path / "out" / "mb" / "a.bin").read_bytes() == (src / "a.bin").read_bytes()
    assert (tmp_path / "out" / "mb" / "b.bin").read_bytes() == (src / "b.bin").read_bytes()
    assert split_part_of("mb.tar.part.ab.gz", "mb.tar.part.aa.gz") and split_part_of("mb.tar.part.ah.gz", "mb.tar.part.aa.gz")
    assert not split_part_of("mb.tar.part.aa.gz", "mb.tar.part.aa.gz")
    assert not split_part_of("src_rec_mb.tar.part.ab.gz", "mb.tar.part.aa.gz")


def test_a_gzip_stream_cut_into_pieces_is_unpacked(tmp_path):
    """SMS-WSJ publishes sms_wsj.tar.gz as sms_wsj.tar.gz.partaa, partab, ...: one gzip stream cut in pieces."""
    src = tmp_path / "src" / "rirs"
    src.mkdir(parents=True)
    (src / "a.bin").write_bytes(bytes(range(256)) * 2000)
    subprocess.run(["tar", "-czf", str(tmp_path / "x.tar.gz"), "rirs"], cwd=tmp_path / "src", check=True)
    data = (tmp_path / "x.tar.gz").read_bytes()
    (tmp_path / "x.tar.gz").unlink()
    half = len(data) // 2
    (tmp_path / "x.tar.gz.partaa").write_bytes(data[:half])
    (tmp_path / "x.tar.gz.partab").write_bytes(data[half:])
    unpack(tmp_path / "x.tar.gz.partaa", tmp_path / "out")
    assert (tmp_path / "out" / "rirs" / "a.bin").read_bytes() == (src / "a.bin").read_bytes()
    assert split_part_of("x.tar.gz.partab", "x.tar.gz.partaa") and not split_part_of("x.tar.gz.partaa", "x.tar.gz.partaa")


def test_a_gzip_stream_in_numbered_pieces_gives_only_the_files_asked_for(tmp_path):
    """Clarity CEC1 publishes its train tar.gz as x.tar.gz.part-00, part-01, ...; the BRIRs share the tar with
    speech, so only the files of the release are written."""
    src = tmp_path / "src" / "train"
    (src / "rooms").mkdir(parents=True)
    (src / "targets").mkdir()
    (src / "rooms" / "brir_R1.wav").write_bytes(bytes(range(256)) * 2000)
    (src / "targets" / "speech.wav").write_bytes(b"s" * 1000)
    subprocess.run(["tar", "-czf", str(tmp_path / "x.tar.gz"), "./train"], cwd=tmp_path / "src", check=True)
    data = (tmp_path / "x.tar.gz").read_bytes()
    (tmp_path / "x.tar.gz").unlink()
    third = len(data) // 3 + 1
    for k in range(3):
        (tmp_path / f"x.tar.gz.part-{k:02d}").write_bytes(data[k * third:(k + 1) * third])
    unpack(tmp_path / "x.tar.gz.part-00", tmp_path / "out", members=["train/rooms/brir_R1.wav"])
    assert (tmp_path / "out" / "train" / "rooms" / "brir_R1.wav").read_bytes() == bytes(range(256)) * 2000
    assert not (tmp_path / "out" / "train" / "targets").exists()          # the speech is not written
    assert split_part_of("x.tar.gz.part-02", "x.tar.gz.part-00") and not split_part_of("x.tar.gz.part-00", "x.tar.gz.part-00")
    assert not split_part_of("x.tar.gz.partab", "x.tar.gz.part-00")


def test_a_tar_gz_gives_its_files_in_one_pass_and_names_a_missing_one(tmp_path):
    """Clarity's eval tar.gz (11 GB) holds its BRIRs out of name order: the members are written as the tar goes (a
    step back would decompress it again from the start), and a file of the release that it lacks is named."""
    from rirdb.fetch import FetchError
    src = tmp_path / "src"
    src.mkdir()
    names = [f"brir_R{k:02d}.wav" for k in (7, 2, 9, 4)]                 # stored out of order
    for n in names:
        (src / n).write_bytes(n.encode() * 100)
    subprocess.run(["tar", "-czf", str(tmp_path / "e.tar.gz"), *names], cwd=src, check=True)
    unpack(tmp_path / "e.tar.gz", tmp_path / "out", members=["brir_R02.wav", "brir_R09.wav"])
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["brir_R02.wav", "brir_R09.wav"]
    with pytest.raises(FetchError, match="does not hold 1 files"):
        unpack(tmp_path / "e.tar.gz", tmp_path / "out2", members=["brir_R02.wav", "brir_R99.wav"])


def test_a_zip_cut_into_numbered_pieces_is_unpacked(tmp_path):
    """FOA-MEIR publishes its zip as x.zip.001, x.zip.002, ...: joined into the zip, then unpacked."""
    with zipfile.ZipFile(tmp_path / "x.zip", "w") as z:
        z.writestr("ir.wav", bytes(range(256)) * 1000)
    data = (tmp_path / "x.zip").read_bytes()
    (tmp_path / "x.zip").unlink()
    for k in range(3):
        (tmp_path / f"x.zip.{k + 1:03d}").write_bytes(data[k * 100_000:(k + 1) * 100_000])
    unpack(tmp_path / "x.zip.001", tmp_path / "out")
    assert (tmp_path / "out" / "ir.wav").read_bytes() == bytes(range(256)) * 1000
    assert not (tmp_path / "x.zip").exists()                    # the joined copy is removed
    assert split_part_of("x.zip.003", "x.zip.001") and not split_part_of("y.zip.002", "x.zip.001")


def test_an_upper_case_zip_is_unpacked(tmp_path):
    """PRAIRE publishes some archives as .ZIP."""
    with zipfile.ZipFile(tmp_path / "C023.ZIP", "w") as z:
        z.writestr("ir.flac", b"x" * 10)
    unpack(tmp_path / "C023.ZIP", tmp_path / "out")
    assert (tmp_path / "out" / "ir.flac").read_bytes() == b"x" * 10


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
