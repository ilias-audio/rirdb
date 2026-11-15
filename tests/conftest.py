"""Shared fixtures.

    toy      the generated dataset of toy.py taken through the whole pipeline once, from the pin to the
             candidate release: (data root, registry). Tests that use it run on one xdist worker
             (`xdist_group("toy")`), so the pipeline runs once per test session.
    serve    a local web server for a folder, in a thread, that answers HTTP Range requests (as GitHub does)
    release  the toy candidate frozen as release 0.9: (release folder, lock file)
    user     a user's machine: an empty cache, and the toy release served over local HTTP
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import zipfile
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from rirdb.registry import Dataset, compile_sources
from rirdb.release import freeze
from tests.toy import DATASET_YAML, REPO, write_toy_dataset


@pytest.fixture(scope="session")
def toy_site(tmp_path_factory):
    """The authors' web site of the toy dataset: a folder served over local HTTP for the whole session.
    Yields (folder, url, server)."""
    folder = tmp_path_factory.mktemp("site")
    with serving(folder) as (url, server):
        yield folder, url, server


@pytest.fixture(scope="session")
def toy(tmp_path_factory, toy_site):
    """Run the pipeline once, from the pin to the candidate release; return (data root, registry)."""
    base = tmp_path_factory.mktemp("toy")
    root, registry = base / "db", base / "registry"
    (registry / "toy").mkdir(parents=True)
    (registry / "toy" / "dataset.yaml").write_text(DATASET_YAML)
    compile_sources([Dataset.model_validate(yaml.safe_load(DATASET_YAML))], registry / "compiled" / "sources.tsv")
    # the authors publish one zip on their site (toy_site); the maintainer downloads it into the inbox
    write_toy_dataset(base / "made")
    inbox = root / "raw" / "_inbox" / "toy"
    inbox.mkdir(parents=True)
    with zipfile.ZipFile(inbox / "toy.zip", "w") as z:
        for f in sorted((base / "made").rglob("*.wav")):
            z.write(f, f.relative_to(base / "made").as_posix())
    site, site_url, _ = toy_site
    shutil.copy2(inbox / "toy.zip", site / "toy.zip")
    env = {**os.environ, "RIRDB_ROOT": str(root), "RIRDB_REGISTRY": str(registry)}

    def run(*cmd: str) -> None:
        done = subprocess.run(list(cmd), env=env, capture_output=True, text=True, cwd=REPO)
        assert done.returncode == 0, f"{' '.join(cmd)} failed:\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}"

    def rirdb(*args: str) -> None:
        run(sys.executable, "-m", "rirdb.cli", *args)

    run("bash", "fetch/adopt_manual.sh", "toy")                      # pin, unpack, hash every file
    pin = registry / "toy" / "downloads.tsv"                         # the URL resolve.sh would have pinned
    pin.write_text(pin.read_text().replace("toy.zip\tmanual:\t", f"toy.zip\t{site_url}toy.zip\t"))
    rirdb("dev", "index", "toy")                                      # index
    rirdb("dev", "index-v1", "toy")                                   # measure, with analyser v1
    rirdb("dev", "analyze", "--dataset", "toy", "--workers", "2")
    rirdb("dev", "merge", "toy")
    rirdb("dev", "audit", "--dataset", "toy", "--workers", "2")
    rirdb("dev", "audit-merge", "toy")
    run(sys.executable, "scripts/one_off/v1_to_tables.py", "toy")
    rirdb("dev", "build", "--workers", "1")                           # build: exits non-zero if a check fails
    return root, registry



class RangeHandler(SimpleHTTPRequestHandler):
    """Serves the files of one folder, the part from a Range request's first byte when it asks for one.
    Records the requests per path in `server.hits`, and each Range header in `server.ranges`. With
    `server.cut_after = n`, it sends n bytes of the file and closes the connection."""

    def do_GET(self) -> None:
        path = Path(self.translate_path(self.path))
        self.server.hits[self.path] = self.server.hits.get(self.path, 0) + 1
        self.server.ranges.append(self.headers.get("Range", ""))
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        rng = self.headers.get("Range", "")
        start = int(rng.removeprefix("bytes=").split("-")[0]) if rng.startswith("bytes=") else 0
        self.send_response(206 if start else 200)
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        self.send_header("Content-Length", str(len(data) - start))
        self.end_headers()
        cut = getattr(self.server, "cut_after", None)          # a connection that closes early, without an error
        self.wfile.write(data[start:start + cut] if cut else data[start:])

    def log_message(self, *args) -> None:
        pass


@contextmanager
def serving(folder: Path):
    """http://127.0.0.1:<port>/ for the files of `folder`, while the block runs; yields (url, server)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: RangeHandler(*a, directory=str(folder)))
    server.hits, server.ranges = {}, []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def serve():
    return serving


@pytest.fixture(scope="session")
def release(toy, tmp_path_factory):
    """(release folder, lock file) of the toy candidate, frozen as version 0.9 under a placeholder URL."""
    root, _ = toy
    base = tmp_path_factory.mktemp("release")
    lock = base / "release.lock.json"
    out = freeze("0.9", root / "release" / "candidate", base / "releases", "http://placeholder.invalid/", lock)
    return out, lock


@pytest.fixture
def user(release, serve, tmp_path, monkeypatch):
    """A user's machine: an empty cache, and the release served over HTTP. Yields the server."""
    out, lock = release
    with serve(out) as (url, server):
        monkeypatch.setenv("RIRDB_RELEASE_LOCK", str(lock))
        monkeypatch.setenv("RIRDB_RELEASE_URL", url)
        monkeypatch.setenv("RIRDB_CACHE", str(tmp_path / "cache"))
        yield server
