"""Static checks of the built website (run after `rirdb dev report site`).

    rirdb dev report site-check            -> prints a report, exits 1 on errors

Errors: internal links or assets that do not exist, links to missing #anchors, pages without exactly one <h1>
or without a <title>, invalid JSON-LD, Python/JS values leaking into text ("None", "nan", "undefined",
"[object Object]"), e-mail addresses, fetch scripts that `bash -n` rejects, sitemap entries without a file.
Warnings: pages missing from the sitemap, very large pages, the repository or the site URL not answering
(every GitHub link on the pages is dead until the repository is public), a registry dataset whose Zenodo DOI or
homepage names another record than the one it is fetched from (source.records).
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from rirdb.report.site.core import OUT_DIR, REPO_URL, SITE_URL


def public_status(url: str) -> str | None:
    """None if `url` answers 200 to an anonymous request, else what it answered."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "rirdb-site-check"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return None if r.status == 200 else f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 - no network on the node, DNS, timeout: report, do not fail
        return f"not reachable ({type(e).__name__})"

ZENODO_ID = re.compile(r"(?:zenodo\.org/(?:records?|deposit)/|10\.5281/zenodo\.)(\d+)", re.I)


def zenodo_concept(rec: str) -> str | None:
    """The concept record id of a Zenodo record (shared by all its versions), or None if the API does not answer."""
    try:
        req = urllib.request.Request(f"https://zenodo.org/api/records/{rec}", headers={"User-Agent": "rirdb-site-check"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return str(json.load(r).get("conceptrecid") or "") or None
    except Exception:  # noqa: BLE001 - offline or rate-limited: the caller still warns
        return None


def record_mismatches() -> list[str]:
    """Registry datasets fetched from Zenodo whose DOI or homepage names another Zenodo record than the one in
    source.records (MYRiAD: the DOI and homepage gave version 1.0, rirdb fetched version 2.0). A concept DOI of
    the fetched record (it names every version) is not reported."""
    from rirdb.registry import load_registry

    out = []
    for ds in load_registry(catalogued=False):
        if ds.source.kind != "zenodo" or not ds.source.records:
            continue
        recs = {r.split("@")[0] for r in ds.source.records}
        concepts = None
        for what, val in (("DOI", ds.doi), ("homepage", ds.homepage)):
            m = ZENODO_ID.search(val or "")
            if not m or m.group(1) in recs:
                continue
            if concepts is None:
                concepts = {zenodo_concept(r) for r in recs}
            if m.group(1) in concepts:
                continue
            out.append(f"registry {ds.id}: {what} {val} names Zenodo record {m.group(1)}, but rirdb fetches "
                       f"{', '.join(sorted(recs))} (source.records); the page cites a record other than the files")
    return out

# a text node that is only a Python/JS null, or a value glued to a unit; "None of the files" is fine
LEAKS = re.compile(r">\s*(None|nan|NaN|null|undefined|inf|-inf|Infinity)\s*<"
                   r"|>[^<]*?(\bnan\b|\bNaN\b|\bundefined\b|\[object Object\]|\bNone ?(?:s|ms|dB|Hz|kHz|%|m³)(?![A-Za-z]))[^<]*<")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(?:ac\.uk|com|org|edu|net|de|fi|uk|eu|io)\b")


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.ids: set[str] = set()
        self.h1 = 0
        self.title = False
        self._in_title = False
        self._in_ld = False
        self.ld: list[str] = []
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a and a["id"]:
            self.ids.add(a["id"])
        if tag == "a" and a.get("name"):
            self.ids.add(a["name"])
        for k in ("href", "src"):
            if a.get(k) and not (tag == "link" and a.get("rel") in ("preconnect", "canonical")) and tag != "use":
                self.links.append((tag, a[k]))
        if tag == "image" and a.get("href"):
            self.links.append((tag, a["href"]))
        if tag == "h1":
            self.h1 += 1
        if tag == "title":
            self._in_title = True
        if tag == "script" and a.get("type") == "application/ld+json":
            self._in_ld, self._buf = True, []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._in_ld:
            self._in_ld = False
            self.ld.append("".join(self._buf))

    def handle_data(self, data):
        if self._in_title and data.strip():
            self.title = True
        if self._in_ld:
            self._buf.append(data)


def run(out_dir: Path = OUT_DIR) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    pages = sorted(p for p in out_dir.rglob("*.html"))
    parsed: dict[Path, _Page] = {}
    # the two copied standalone reports are checked only as link targets
    own = [p for p in pages if p.relative_to(out_dir).parts[0] not in ("guide", "overview")]
    for p in own:
        text = p.read_text(errors="replace")
        pg = _Page()
        pg.feed(text)
        parsed[p] = pg
        rel = p.relative_to(out_dir).as_posix()
        if rel not in ("404.html",) and pg.h1 != 1:
            errors.append(f"{rel}: {pg.h1} <h1> elements")
        if not pg.title:
            errors.append(f"{rel}: no <title>")
        for raw in pg.ld:
            try:
                json.loads(raw)
            except json.JSONDecodeError as e:
                errors.append(f"{rel}: invalid JSON-LD ({e})")
        body = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", "", text, flags=re.S)
        for m in LEAKS.finditer(body):
            errors.append(f"{rel}: leaked value {(m.group(1) or m.group(2))!r}: {m.group(0)[:100]!r}")
            break
        if EMAIL.search(body):
            errors.append(f"{rel}: e-mail address {EMAIL.search(body).group(0)!r}")
        if p.stat().st_size > 3_000_000:
            warnings.append(f"{rel}: {p.stat().st_size / 1e6:.1f} MB")
    ids_cache: dict[Path, set[str]] = {}
    # the copied reports: only their links back into the site (the bar on top)
    for p in pages:
        if p not in parsed:
            pg = _Page()
            pg.feed(p.read_text(errors="replace"))
            pg.links = [(t, u) for t, u in pg.links if u.startswith("../")]
            parsed[p] = pg
    for p, pg in parsed.items():
        rel = p.relative_to(out_dir).as_posix()
        for tag, link in pg.links:
            if link.startswith(("http://", "https://", "mailto:", "data:", "javascript:", "//")) or link.startswith(SITE_URL):
                continue
            u = urlsplit(link)
            target = p if not u.path else (p.parent / unquote(u.path)).resolve()
            if u.path and not target.exists():
                errors.append(f"{rel}: broken {tag} -> {link}")
                continue
            if target.is_dir():
                if not (target / "index.html").exists():
                    errors.append(f"{rel}: {link} is a folder without index.html")
                    continue
                target = target / "index.html"
            if u.fragment and target.suffix == ".html":
                if target not in ids_cache:
                    if target in parsed:
                        ids_cache[target] = parsed[target].ids
                    else:
                        tp = _Page()
                        tp.feed(target.read_text(errors="replace"))
                        ids_cache[target] = tp.ids
                if unquote(u.fragment) not in ids_cache[target]:
                    errors.append(f"{rel}: missing anchor #{u.fragment} in {target.relative_to(out_dir.resolve()) if target.is_relative_to(out_dir.resolve()) else target}")
    for sh in sorted((out_dir / "get").glob("*.sh")):
        r = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True)
        if r.returncode:
            errors.append(f"get/{sh.name}: bash -n: {r.stderr.strip()[:200]}")
    sm = out_dir / "sitemap.xml"
    if sm.exists():
        locs = re.findall(r"<loc>(.*?)</loc>", sm.read_text())
        listed = set()
        for loc in locs:
            rel = loc.removeprefix(SITE_URL)
            f = out_dir / (rel + "index.html" if rel.endswith("/") or rel == "" else rel)
            listed.add(f.resolve())
            if not f.exists():
                errors.append(f"sitemap: {loc} has no file")
        for p in own:
            if p.resolve() not in listed and p.name != "404.html":
                warnings.append(f"not in sitemap: {p.relative_to(out_dir)}")
    else:
        warnings.append("no sitemap.xml")
    try:
        warnings += record_mismatches()
    except Exception as e:  # noqa: BLE001 - the registry must load for the rest of rirdb anyway
        warnings.append(f"registry record check skipped: {type(e).__name__}: {e}")
    for what, url in (("repository", REPO_URL), ("site", SITE_URL)):
        if (st := public_status(url)) is not None:
            warnings.append(f"{what} {url}: {st}; the links into it are dead for readers until it is public")
    print(f"checked {len(own)} pages ({len(pages) - len(own)} copied reports as link targets), "
          f"{sum(len(pg.links) for pg in parsed.values()):,} links, {len(list((out_dir / 'get').glob('*.sh')))} fetch scripts")
    for w in warnings[:60]:
        print("WARN ", w)
    if len(warnings) > 60:
        print(f"WARN  ... {len(warnings) - 60} more")
    for e in errors[:300]:
        print("ERROR", e)
    if len(errors) > 300:
        print(f"ERROR ... {len(errors) - 300} more")
    print(f"{len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0
