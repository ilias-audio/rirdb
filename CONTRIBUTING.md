# Contributing to RIRDB

Thank you for helping. The most useful contribution is a dataset that is missing, or a correction to one that
is there: a licence, a count, a space type. Code is welcome too.

## Licences

- **Code** (everything under `src/`, `fetch/`, `jobs/`, `scripts/`, `tests/`): MIT, see [LICENSE](LICENSE).
- **The catalogue and the tables** (`registry/` and the release tables built from it): CC BY 4.0. Cite RIRDB
  when you reuse them.
- **The audio** is not ours. Each dataset keeps its own licence; RIRDB records it, and never relicenses it.

By opening a pull request you agree that your contribution is published under these licences.

## Add or correct a dataset

Everything about a dataset is in one folder, `registry/<id>/`. Adding a dataset is adding a folder.

| File | Who writes it | What it holds |
|---|---|---|
| `dataset.yaml` | you | name, year, licence, sources, counts, how to read the files ([fields](src/rirdb/registry.py)) |
| `spaces.csv` | you, if needed | only the spaces that differ from the dataset's defaults, or that have a volume, a licence or a twin in another dataset |
| `downloads.tsv` | `fetch/resolve.sh` | one row per download, with its checksum and the folder it is unpacked into |
| `files.tsv.gz` | `fetch/inventory.sh` | one row per file, with its sha256 |

1. **Pick an id**: lower case, digits and `_`, short, and never changed once published (e.g. `york_minster`).
2. **Write `registry/<id>/dataset.yaml`.** Copy the folder of a similar dataset and edit it. A dataset you can
   only describe for now (no script can download it yet) needs `status: catalogued`, an id, a name, a type, a
   description (`content`) and a licence; add its homepage if it has one. Everything else can come later.
3. **The licence.** Use an SPDX id (`CC-BY-4.0`) or a `LicenseRef-...` id. It must have a row in
   [src/rirdb/licences.csv](src/rirdb/licences.csv), which says what the licence allows; add a row for new
   terms. Where the record, the files, the README or the paper disagree, list every statement under
   `license.statements` with where you read it; the licence used is then the strictest one, and the tests
   check this. A licence stated in the dataset's paper counts as a statement.
4. **Spaces.** Most datasets need no `spaces.csv`. Add a row only for a space whose type the names do not
   give, a volume the authors state, a licence of its own, or the same room in another dataset (`same_as`).
5. **Open a pull request.** Say how many IRs and spaces the dataset has, its channel format, and where you
   read each fact. A maintainer pins it (`downloads.tsv`, `files.tsv.gz`), measures it, and rebuilds the tables.
   Measurements are never taken from contributors on trust: they are recomputed.

## Change the code

- **Readability comes first.** The repository is kept by one person, who has to explain every part of it.
  Plain names, short functions, and plain files (CSV, YAML, SQL) before clever code. No new dependency without
  a reason.
- **One fact, one home** ([data-model.md](docs/decisions/data-model.md), rule R1). A fact is written in one
  place and everything else is rebuilt from it. A threshold belongs in
  [thresholds.yaml](src/rirdb/thresholds.yaml), a published column in [columns.csv](src/rirdb/columns.csv),
  a rule across tables in [checks.sql](src/rirdb/checks.sql).
- **Tests.** `pixi run pytest -q` runs them all, including a generated dataset taken through every stage
  (`tests/test_end_to_end.py`). They run on GitHub at every push. On a SLURM cluster, run them through a job,
  never on a login node: `sbatch -p computeshort -t 0:20:0 jobs/quick_cpu.sh pixi run pytest -q -n 8`.
- **Small commits**, each with passing tests, and a message that says what changed and why.

## Report a problem

Open an issue: a broken link, a wrong licence, an IR that sounds wrong. Give the dataset id and, for one IR,
its `ir_id`.
