"""Download, verify and unpack a dataset's files into the cache, with the standard library only.

    fetch_dataset("rochester")                     every file of the dataset, verified
    fetch_dataset("arni", needed=[...])            only these files (their archives are still downloaded whole)
    fetch_dataset("reverbfx", from_dir=folder)     a dataset rirdb cannot download: the files you fetched

The cache ($RIRDB_CACHE, else ~/.cache/rirdb):

    downloads/<dataset>/<download>      archives, deleted once their files verify (unless keep_archives)
    files/<dataset>/<file>              the dataset's files as published, an archive unpacked into its extract_to

Every file is checked against its sha256 (files.<dataset>.parquet), and every download against its own
checksum (md5 or sha256) where the release has one. A file that does not match is deleted, and says so.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from rirdb import __version__

USER_AGENT = f"rirdb/{__version__}"


class FetchError(RuntimeError):
    pass


def say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# ------------------------------------------------------------------------------------------- checksums

def sha256_of(path: Path) -> str:
    return checksum(path, "sha256")


def checksum(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verified(path: Path, sha256: str) -> bool:
    return path.is_file() and sha256_of(path) == sha256


# ------------------------------------------------------------------------------------------- downloads

def scripted(url: str) -> bool:
    """True for a URL rirdb can fetch; a dataset behind a browser or a registration has another kind
    (`inplace:` where the maintainer used a local copy)."""
    return url.startswith(("https://", "http://", "ftp://", "file://"))


def download(url: str, dest: Path, attempts: int = 3) -> Path:
    """Fetch url into dest. The bytes go to dest.part first, so a cut download is never taken for a whole
    one, and the next attempt asks only for the rest (a server that ignores the request sends it all again)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, attempts + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": USER_AGENT, **({"Range": f"bytes={have}-"} if have else {})}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                resumed = have > 0 and getattr(r, "status", None) == 206
                start = have if resumed else 0
                total = start + int(r.headers.get("Content-Length") or 0)
                with open(part, "ab" if resumed else "wb") as f, progress(dest.name, total, start) as advance:
                    for chunk in iter(lambda: r.read(1 << 20), b""):
                        f.write(chunk)
                        advance(len(chunk))
                if total > start and part.stat().st_size < total:     # closed early, without an error
                    raise ConnectionError(f"the connection closed after {human(part.stat().st_size)} of {human(total)}")
            break
        except OSError as e:                       # urllib's errors, timeouts and cut connections are OSErrors
            if attempt == attempts:
                raise FetchError(f"could not download {url}: {e}. Run the command again to resume.") from e
            say(f"  {e}; trying again ({attempt} of {attempts - 1})")
            time.sleep(2 ** attempt)
    part.replace(dest)
    return dest


@contextmanager
def progress(name: str, total: int, done: int):
    """A progress bar on a terminal for a download above 50 MB; nothing otherwise (logs, tests)."""
    if not sys.stderr.isatty() or total < 50e6:
        yield lambda n: None
        return
    from rich.console import Console
    from rich.progress import BarColumn, DownloadColumn, Progress, TimeRemainingColumn, TransferSpeedColumn

    with Progress("  {task.description}", BarColumn(), DownloadColumn(), TransferSpeedColumn(), TimeRemainingColumn(),
                  console=Console(stderr=True), transient=True) as bar:
        task = bar.add_task(name, total=total, completed=done)
        yield lambda n: bar.advance(task, n)


def check_download(path: Path, algo: str, expected: str) -> None:
    """md5 or sha256 of a whole download; `manifest` (a web folder) and an empty checksum are checked per file."""
    if algo in ("md5", "sha256") and expected and checksum(path, algo) != expected:
        path.unlink()
        raise FetchError(f"{path.name} does not match its {algo}, so it was deleted. Run the command again "
                         "to download it again; if it fails again, the source has changed.")


# ------------------------------------------------------------------------------------------- unpacking

def split_part_of(download: str, archive: str) -> bool:
    """x.z01, x.z02, ... are the parts of the split zip x.zip (TAU-SRIR): fetched and counted with it."""
    return bool(re.fullmatch(re.escape(archive.removesuffix(".zip")) + r"\.z\d\d", download))


def unpack(archive: Path, into: Path, members: list[str] | None = None) -> None:
    """Unpack the archive into the folder: only `members` where the archive has them all, else everything."""
    into.mkdir(parents=True, exist_ok=True)
    split = any(archive.parent.glob(archive.name.removesuffix(".zip") + ".z[0-9][0-9]"))
    if archive.name.endswith(".zip") and not split:
        try:
            with zipfile.ZipFile(archive) as z:
                z.extractall(into, members=members if members and set(members) <= set(z.namelist()) else None)
            return
        except (zipfile.BadZipFile, OSError) as e:
            say(f"  Python cannot read {archive.name} ({e}); trying the system's 7z or unzip")
    if archive.name.endswith(".zip"):
        unpack_with_system_tools(archive, into)
        return
    with tarfile.open(archive) as t:
        wanted = [t.getmember(m) for m in members] if members and set(members) <= set(t.getnames()) else None
        safe = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}      # no path outside `into`
        t.extractall(into, members=wanted, **safe)


def unpack_with_system_tools(archive: Path, into: Path) -> None:
    """A split zip (x.z01 ... x.zip; TAU-SRIR) or a zip Python cannot read (RSoANU's >4 GB zip without ZIP64
    records): 7-Zip, else Info-ZIP's zip (to join the parts) and unzip."""
    seven = shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")
    if seven:
        subprocess.run([seven, "x", "-y", "-bso0", "-bsp0", f"-o{into}", str(archive)], check=True)
        return
    if shutil.which("zip") and shutil.which("unzip"):
        joined = archive.with_name(archive.stem + ".joined.zip")
        subprocess.run(["zip", "-q", "-s", "0", str(archive), "--out", str(joined)], check=True)
        subprocess.run(["unzip", "-q", "-o", str(joined), "-d", str(into)], check=True)
        joined.unlink()
        return
    raise FetchError(f"{archive.name} needs 7-Zip (7z), or Info-ZIP's zip and unzip, and neither is installed. "
                     "Install one (e.g. `apt install p7zip-full` or `brew install p7zip`) and run again.")


# ------------------------------------------------------------------------------------------- one dataset

def cache_dir() -> Path:
    return Path(os.environ.get("RIRDB_CACHE") or Path.home() / ".cache" / "rirdb")


def files_dir(dataset: str) -> Path:
    return cache_dir() / "files" / dataset


@contextmanager
def locked(name: str):
    """One process at a time on one part of the cache, for jobs that share it (no lock where the system has
    none, on Windows)."""
    try:
        import fcntl
    except ImportError:
        yield
        return
    path = cache_dir() / "locks" / f"{name}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def fetch_dataset(dataset: str, needed: list[str] | None = None, from_dir: Path | None = None,
                  keep_archives: bool = False) -> Path:
    """Make the dataset's files (or only `needed`) present and verified in the cache; return their folder."""
    from rirdb import client

    files = client.table(f"files.{dataset}")
    downloads = client.table("downloads").query("dataset == @dataset").sort_values("source_rank")
    want = files if needed is None else files[files["file"].isin(needed)]
    if needed is not None and len(want) < len(set(needed)):
        raise FetchError(f"{dataset}: {sorted(set(needed) - set(files['file']))[:3]} are not files of the release")
    root = files_dir(dataset)
    with locked(f"files.{dataset}"):
        missing = want[[not verified(root / f, s) for f, s in zip(want["file"], want["sha256"])]]
        for f in missing["file"]:
            (root / f).unlink(missing_ok=True)      # a changed file: never written over in place (hard links)
        no_source = missing[missing["download"] == ""]
        if len(no_source):
            say(f"{dataset}: {len(no_source)} files are in no download (added by hand), and are left out: "
                f"{no_source['file'].head(3).tolist()}")
        for name, group in missing[missing["download"] != ""].groupby("download", sort=False):
            source = downloads[downloads["download"] == name].iloc[0]
            if from_dir is not None and (not scripted(source["url"]) or holds(from_dir, source, group)):
                archives = take_from(from_dir, dataset, source, group, downloads, root)
            elif not scripted(source["url"]):
                raise FetchError(f"{dataset}: rirdb cannot download {name}. Fetch the dataset's files from its "
                                 f"homepage (`rirdb info {dataset}`), then run again with --from <folder>.")
            else:
                archives = fetch_download(dataset, source, group, downloads, root)
            for f, sha in zip(group["file"], group["sha256"]):
                if not verified(root / f, sha):
                    (root / f).unlink(missing_ok=True)
                    raise FetchError(f"{dataset}: {f} does not match its sha256, so it was deleted.")
            if not keep_archives:
                for a in archives:                   # only once their files verify
                    a.unlink(missing_ok=True)
    return root


def fetch_download(dataset: str, source: pd.Series, group: pd.DataFrame, downloads: pd.DataFrame,
                   root: Path) -> list[Path]:
    """One download of the release, for the files of `group` (rows of files.<dataset>). Returns the archives
    it unpacked, to delete once the files verify."""
    name, url = source["download"], source["url"]
    if name == "@mirror":                                       # a web folder (OpenAIR): file by file
        for f in group["file"]:
            download(url + urllib.parse.quote(f), root / f)
        return []
    if not source["extract_to"]:                                # a file, published as it is
        say(f"{dataset}: downloading {name} ({size_of(source)})")
        download(url, root / name)
        check_download(root / name, source["algo"], source["checksum"])
        return []
    archives = cache_dir() / "downloads" / dataset
    parts = [row for _, row in downloads.iterrows() if split_part_of(row["download"], name)]
    for part in [*parts, source]:                               # a split zip's parts first, then the archive
        path = archives / part["download"]
        if not path.exists():
            say(f"{dataset}: downloading {part['download']} ({size_of(part)})")
            download(part["url"], path)
        check_download(path, part["algo"], part["checksum"])      # also an archive kept from an earlier run
    into = root if source["extract_to"] == "." else root / source["extract_to"]
    prefix = "" if source["extract_to"] == "." else source["extract_to"] + "/"
    members = None if parts else [f.removeprefix(prefix) for f in group["file"]]
    say(f"{dataset}: unpacking {name}")
    unpack(archives / name, into, members)
    return [archives / p["download"] for p in [*parts, source]]


def holds(folder: Path, source: pd.Series, group: pd.DataFrame) -> bool:
    """True when a --from folder has the files of this download, or the download itself."""
    return all((folder / f).is_file() for f in group["file"]) or (folder / source["download"]).is_file()


def take_from(folder: Path, dataset: str, source: pd.Series, group: pd.DataFrame, downloads: pd.DataFrame,
              root: Path) -> list[Path]:
    """--from: the files as published (folder/<file>), else the download itself (folder/<download>, with the
    parts of a split zip). They are copied into the cache and checked there: nothing in the folder is changed."""
    if all((folder / f).is_file() for f in group["file"]):
        for f in group["file"]:
            (root / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(folder / f, root / f)
        return []
    if not (folder / source["download"]).is_file():
        raise FetchError(f"{dataset}: neither the files of {source['download']} (e.g. {group['file'].iloc[0]}) nor "
                         f"{source['download']} itself are in {folder}")
    archives = cache_dir() / "downloads" / dataset
    archives.mkdir(parents=True, exist_ok=True)
    for name in downloads["download"]:
        if (name == source["download"] or split_part_of(name, source["download"])) and (folder / name).is_file():
            shutil.copy2(folder / name, archives / name)
    return fetch_download(dataset, source, group, downloads, root)


def human(n: float) -> str:
    """1234567 -> '1.2 MB' (powers of 1000)."""
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def size_of(download: pd.Series) -> str:
    return "size unknown" if pd.isna(download["size_bytes"]) else human(download["size_bytes"])
