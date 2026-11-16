# RIRDB

Find room impulse responses (RIRs) across public datasets, and get them as one folder.

More than 200 RIR datasets are public, but they differ in host, licence, file format and quality, often in
ways their pages do not say. RIRDB catalogues them, measures every IR they contain with the same analyser
(ISO 3382 decay parameters, per octave band, and a set of defect checks), and lets you select, download and
verify IRs by licence, format, origin, quality and room.

**Website:** https://ilias-audio.github.io/rirdb/ (every dataset, and every column of the tables).

**Status: 0.9 preview.** It covers the 46 datasets measured so far (about 115,000 IRs in the table). Ids, rows
and columns may still change before 1.0.

## Use it

```
pip install rirdb
rirdb search --format binaural --grade A                 # what a selection holds and costs; nothing is downloaded
rirdb info sriracha                                      # one dataset: licence, sizes, quality, what its cap leaves out
rirdb get --dataset rochester                            # every file as published, each checked by its sha256
rirdb get --use research --format binaural --grade A --balance space --n 500
rirdb search --dataset arni --per-space 50              # 50 IRs per room, spread from its driest to its most reverberant
rirdb get --format mono --per-space 20 --max-archive 2GB  # leave out IRs that come in a download above 2 GB
rirdb search --year 2024- --format ambisonic               # datasets published in 2024 or later
```

The [website](https://ilias-audio.github.io/rirdb/) writes these commands as you click: filters (the year of
publication among them), the datasets you pick or leave out, then, under What you get, how many per room and a draw,
with charts of what the command selects: its reverberation times, the IRs of each dataset and of each room. `search`
takes every option of `get`, so you can look before anything is downloaded.

A few datasets publish files with a defect RIRDB cannot repair (a damaged file, a recording where an IR should be).
Their IRs stay in the table with the defect in words (column `known_defect`), and `search`, `get` and the website
say so.

`get` writes one folder (`--out`, default `rirdb-out/`):

```
audio/<dataset>/<ir_id>.wav   float32, in the asked format (ambisonic: AmbiX, ACN and SN3D)
irs.csv                       one row per IR, with every measured value and where it came from
LICENSES.md                   the licence of every dataset used, and what it allows
references.bib                one entry per dataset, and one for RIRDB
selection.lock                rebuilds the same IRs anywhere: rirdb get --lock selection.lock
```

The same selection in Python:

```python
import rirdb.select as rs
sel = rs.select(use="research", format="binaural", grade="A", per_space=20, max_archive="2GB", balance="space", n=500)
sel.table            # pandas DataFrame, one row per IR
sel.known_defects()  # what to know before using them, if anything
sel.download("irs/")
```

The tables (about 30 MB for a search) and the datasets' files are kept in `~/.cache/rirdb` (or
`$RIRDB_CACHE`). Every dataset is downloaded from its authors' site; a dataset behind a registration is taken
from a folder you filled (`--from`). Every file is checked against its sha256, and every IR against the hash of
its decoded samples.

**Grades** S, A, B, C and D describe the decay of each IR: S and A need a valid T30 in every octave from
125 Hz to 4 kHz, and a defect (clipping, dropouts, a sweep left in the response, ...) lowers the grade. An IR
whose files have a known defect as published is graded D.
`--grade A` means A or better. Nothing is filtered unless you ask: `search` always shows the split by origin
and grade.

**Large datasets.** A dataset with more than 100,000 IRs (SRIRACHA, MIRACLE, MeshGrid3D) has a fixed sample in
the table: the 500 IRs with the smallest ids in each space, condition and layout. `rirdb info` says how many
IRs that keeps, what they weigh as WAV, and what the whole dataset weighs; `rirdb get --dataset <id>` still
gives every file as published, with the list of every IR found.

## Licences

- **Code:** MIT, see [LICENSE](LICENSE).
- **The catalogue and the tables** (`registry/` and the release tables built from it): CC BY 4.0.
- **The audio** is not ours. Each dataset keeps its own licence: RIRDB records it, never relicenses it, and
  `--use research|commercial|training|redistribute` keeps only the IRs whose licence allows that use.

## Contribute

A missing dataset, or a correction to one (a licence, a count, a space type), is the most useful contribution:
see [CONTRIBUTING.md](CONTRIBUTING.md). Everything about a dataset is in one folder, `registry/<id>/`.

## Cite

I. Ibnyahya and J. D. Reiss, "RIRDB: A Database to Find and Explore Room Impulse Response Datasets",
abstract submitted to DMRN+21, the Digital Music Research Network one-day workshop (London, December 2026).
