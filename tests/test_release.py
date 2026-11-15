"""The frozen release (release.py) and the client that reads it (client.py), over local HTTP.

The generated dataset's candidate (conftest.py, `toy`) is frozen into a release folder, served by a local web
server, and fetched into a fresh cache, as a user's machine would fetch the GitHub release.
"""

from __future__ import annotations

import json

import pytest

from rirdb import client
from rirdb.release import ReleaseRefused, freeze

pytestmark = pytest.mark.xdist_group("toy")


def test_the_release_folder_is_the_list_of_assets(release):
    out, lock = release
    files = json.loads(lock.read_text())["files"]
    assert sorted(p.name for p in out.iterdir()) == sorted(files)
    assert all("/" not in name for name in files)
    assert {"irs.parquet", "datasets.parquet", "readers.toy.parquet", "files.toy.parquet", "bands.toy.parquet"} <= set(files)
    assert json.loads((out / "release.json").read_text())["version"] == "0.9"


def test_a_release_is_never_overwritten(toy, release):
    root, _ = toy
    out, lock = release
    with pytest.raises(ReleaseRefused, match="never overwritten"):
        freeze("0.9", root / "release" / "candidate", out.parent, "http://placeholder.invalid/", lock)


def test_the_client_fetches_once_and_verifies(user):
    irs = client.table("irs")
    assert len(irs) == 7 and client.version() == "0.9"
    client.table("irs")
    assert user.hits == {"/irs.parquet": 1}                      # the second read uses the cache


def test_the_client_refuses_a_changed_byte(user):
    path = client.table_file("datasets.parquet")
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 1
    path.write_bytes(bytes(data))
    client._verified.clear()                                      # the next command checks it again
    with pytest.raises(client.ReleaseError, match="does not match"):
        client.table_file("datasets.parquet")
    assert not path.exists()                                      # deleted, so the next run fetches it again
    assert len(client.table("datasets")) >= 1


def test_the_client_refuses_a_changed_file_on_the_server(release, serve, tmp_path, monkeypatch):
    out, lock = release
    changed = tmp_path / "served"
    changed.mkdir()
    for p in out.iterdir():
        changed.joinpath(p.name).write_bytes(p.read_bytes())
    irs = changed / "irs.parquet"
    irs.write_bytes(irs.read_bytes()[:-1] + b"\0")
    with serve(changed) as (url, _):
        monkeypatch.setenv("RIRDB_RELEASE_LOCK", str(lock))
        monkeypatch.setenv("RIRDB_RELEASE_URL", url)
        monkeypatch.setenv("RIRDB_CACHE", str(tmp_path / "cache"))
        with pytest.raises(client.ReleaseError):
            client.table("irs")


def test_readers_say_how_to_cut_each_ir(user):
    irs, readers = client.table("irs"), client.table("readers.toy")
    assert sorted(readers["ir_id"]) == sorted(irs["ir_id"])
    stereo = readers.set_index("ir_id").loc[irs.set_index("file").loc["hall/s1_mstereo.wav", "ir_id"]]
    assert json.loads(stereo["locator"])["relpath"] == "hall/s1_mstereo.wav" and stereo["channel_roles"] == "SL,SR"
    settings = client.table("datasets").set_index("dataset").loc["toy", "reader_settings"]
    assert "pattern" in json.loads(settings)


def test_no_lock_says_so(monkeypatch, tmp_path):
    monkeypatch.setenv("RIRDB_RELEASE_LOCK", str(tmp_path / "missing.json"))
    with pytest.raises(client.ReleaseError, match="no release lock"):
        client.lock()
