---
title: "RIRDB data model: what is stored for every IR, where, and how it is kept correct"
date: 2026-11-14
status: accepted on 2026-11-14 (third draft, plus Ilias's three changes of that day; see section 12)
---

# RIRDB data model

This is the review of the per-IR approach that [release-1.0.md](release-1.0.md) assumes (D1, D2, A4).
It answers four questions:

1. What is one row?
2. Where does each fact live?
3. Which tables are published, and how wide are they?
4. How is the database kept correct by one maintainer?

Section 12 says how the draft was checked and what each check changed. Section 11 records the decisions.

## 1. The problem, in numbers

Measured on 2026-11-14 on the 46 datasets on disk, by a read-only SLURM job
([output and script](../../reports/verify/2026-11-14-table-sizes.txt)).

| Today | Value |
|---|---|
| IR records in the index | 2,634,480 |
| ...of which three dense microphone grids, one room each | 2,438,080 (SRIRACHA 1,327,360; MIRACLE 856,128; MeshGrid3D 254,592) |
| IRs measured (analyser v1) | 256,025 |
| Columns per measured IR | 357: 327 in the metrics table, 30 in a separate audit table |
| Size of the measured table | 275 MB (1,075 bytes per row) |
| Curves kept per IR (HDF5 "features") | 25 kB per IR, 5.1 GB in all |
| Compute | 1.35 s per IR (median); 96 CPU-hours for everything so far |
| Rows in the IR table when only the three grids are sampled (500 per room setup, section 3) | 208,900 |

- **Keep one row per IR.** `rirdb get --format binaural --grade A` cannot work without it.
- **Volume is not the risk, once the three grids are sampled.** The other 43 datasets hold 196,400 IRs,
  and all of them get a row. All 222 datasets will give several hundred thousand rows: under 100 MB.
  Measuring them costs a few hundred CPU-hours.
- **Width and copies are the risk.** 357 columns today, and third-octave bands would triple that. The
  index copies the dataset's licence into every IR row, which is how one licence bug reached 866,458 rows.
  Five stores hold per-IR data (index, metrics, audit, curves, snapshot), and two data roots exist.

So the design limits width and forbids copies. It needs no database server.

## 2. Six rules

**R1. One fact, one home.** Every fact is written in exactly one place. Everything else is rebuilt from it.

**R2. Four kinds of fact, by who writes them and what they cost.**

| Kind | Written by | Lives in | Cost to redo |
|---|---|---|---|
| Curated | a person | `registry/<id>/` in git (YAML and CSV) | — |
| Pinned | the download step | `registry/<id>/` in git (two machine-written files) | a download |
| Read from the files | `index` | `$RIRDB_ROOT/tables/<id>/` | minutes |
| Measured from the audio | `measure` | `$RIRDB_ROOT/tables/<id>/` | CPU-hours |

Everything else users see is **derived** from these four by `build`, in minutes, with no audio: validity,
defects, processing tags, grades, duplicates, permitted uses, per-dataset summaries.

**R3. The audio pass stores numbers, never verdicts.** A defect is a threshold on a stored number, applied
at build time. Section 9 says exactly which numbers, because some settings are fixed while the audio is
read and cannot be changed later without it.

**R4. A published value is valid or empty.** If T30 fails its ISO 3382-2 decay-range rule, the cell is
empty (NULL). There are no `valid_*` columns in what users get. `decay_range_mid_db` sits next to the
mid-band values and says why. `columns.csv` states, for every column, when it applies and what an empty
cell means.

**R5. Three plain files define the tables.** `columns.csv` lists every published column (table, name, type,
unit, allowed values, when it applies, what empty means, meaning, origin). `thresholds.yaml` holds every
number that makes a validity, a defect, a tag or a grade. `checks.sql` holds the rules that span tables,
each a query that must return no rows. Checks on allowed values and ranges are generated from
`columns.csv`, so they are written once.

**R6. Nothing is withheld.** A sample, a grade or a default never stops a user from getting or measuring
data.

- A very dense dataset is sampled for the main table only. Every one of its IRs stays listed,
  downloadable, and measurable with the same code (section 3).
- A grade describes the decay of one IR. It is never a default filter, and it is not a verdict on a
  dataset: a 32 ms response from an anechoic room is the right data for sound-field work.
- A test that compares channels is never applied to Ambisonic signals, where opposite signs encode
  direction. Every channel of an IR is always delivered.

## 3. What is one row

One row of the IR table is **one response that `rirdb get` can deliver as one audio file**: one space in
one condition, one source position, one receiver, one channel layout.

| Case | Rows |
|---|---|
| One device at one position: stereo pair, dummy head, Ambisonic microphone, Eigenmike, SDM array | one row, `channels` 2 to 64 |
| Spaced microphones: a grid, a scanned plane | one row per microphone. This is why three datasets hold 93 % of today's records |
| One response split over two files (Aachen AIR: one file per ear) | one row; `file` names both |
| The same measurement published in several layouts (OpenAIR: B-format and a stereo decode) | one row per layout |
| An audio file that cannot be read | one row, values empty, `defect_corrupt_samples` true |
| Sweeps, noise, speech and music examples inside a dataset | no row; the file list gives each such file a role (section 7) |

**Which IRs get a row.**

- **Every IR is measured and gets a row. Only a very dense dataset is sampled.** "Very dense" means more
  than 100,000 IRs in the pinned files. Today that is three microphone grids: SRIRACHA, MIRACLE and
  MeshGrid3D. They hold 2.44 of the 2.63 million IRs on disk, in three rooms.
- A very dense dataset sets `cap` in its dataset file: how many IRs are measured per space, condition and
  layout. The three grids use 500. SRIRACHA has 20 room setups, so 10,000 of its 1,327,360 IRs are in
  the table; MIRACLE has 2,000 and MeshGrid3D 500. A simulated set with thousands of rooms uses a small
  number (GWA: 2 million IRs in 18,968 rooms). There is one number per dataset and no second rule.
  Every space keeps at least one row.
- The sampled IRs are the `cap` IRs with the smallest `ir_id` in each group. The id is a hash, so this is
  a fixed pseudo-random draw, and **samples are nested**: raising `cap` adds rows and replaces none, and
  only the added IRs are measured.
- `index` lists **every** IR it finds, with a `sampled` flag. So "why is this IR not in the table" always
  has an answer: found but not sampled, or the file's role.
- **A sampled dataset loses nothing else** (R6):
  - `rirdb get --dataset sriracha` delivers every file as published, whatever the sample;
  - the release holds `all_irs/<dataset>.parquet`: every IR found, with its file, its place in the file,
    the coordinates the reader extracts, and `sampled`;
  - `rirdb analyse sriracha --all` measures every IR on the user's machine with the same code and writes
    the same columns. It prints the expected time first (SRIRACHA: about 500 CPU-hours);
  - publishing the full measurement of the three grids is item 15 of
    the deferred list (deferred.md): about 570 CPU-hours, and a table twelve times longer.
- The draw is fixed for a pinned version of a dataset. If the authors add files, a few rows are replaced,
  and the replaced ids are recorded as retired (section 4).
- Each dataset states three counts: `n_irs_stated` (by the authors), `n_irs_found` (in the files) and
  `n_irs` (in the table). For a sampled dataset, shares (grades, defects) are labelled "of sampled IRs".
- **Today.** Analyser v1 measured one IR in sixteen of six datasets: the three grids, MP-RIR, MeshRIR and
  SoundCam. It kept the IRs with the smallest id hash, which is the same draw. So the samples above are
  already inside the v1 measurements, and the 0.9 preview needs no new measurement. For the preview,
  MP-RIR, MeshRIR and SoundCam carry a `cap` that v1 covers; the December run removes it and measures
  them in full.

**`is_primary`.** Per space, one layout is primary: its rows count in statistics, so nothing is counted
twice. Every space has primary rows. `--format binaural` takes binaural rows whether primary or not, and
`--format ambisonic` likewise: being non-primary never hides a row from a search that names its format.
With no `--format`, a selection takes primary rows only. A position published only in a non-primary
layout is not counted in statistics.

## 4. Identifiers

| Id | Form | Changes only when |
|---|---|---|
| `dataset` | `[a-z0-9_]+`, e.g. `openair`; also its folder name | never, once published |
| `space` | `<dataset>/<slug>`, e.g. `openair/york-minster` | spaces are merged or split; IR ids are not affected |
| `ir_id` | `<dataset>-<16 hex>`: the hash of `file#item` | the authors move the file, or the reader slices it differently |
| `audio_hash` | first 32 hex digits of the SHA-256 of the sample rate, the channel count and the decoded samples (float32, channel after channel); for an IR the reader computes from published values (inverse FFT, subband filters), the hash of those values and the reader's settings: the same on any numpy build | the audio changes, whatever the container |

- **The recipe is frozen.** `file` is the path inside the dataset's files (two paths joined by `|` when a
  response spans two files). `item` is the position inside the file, empty when the file holds one IR.
  Both are published columns, and a check recomputes every `ir_id` from them. This is today's hash with
  `-` instead of `:`, because a colon is not allowed in Windows file names.
- **A path never moves by accident.** Today an archive is unpacked into its own folder only when the pin
  lists several archives, so a second archive added later would move every file. Each download's
  `extract_to` folder is now written into the pin and kept.
- `ir_id` is not built from the samples, because identical IRs exist inside one dataset (TAU-SRIR,
  Rochester). It does not contain the space, so correcting a space never breaks an id.
- **The promise starts at 1.0.** From then on, `build` fails if an `ir_id` of the previous release is
  missing, unless `registry/retired.csv` lists it with a reason (a command writes those rows). The 0.9
  preview says that ids, rows and columns may still change.
- The same room in two datasets keeps two `space` ids. The curated column `same_as` points one at the
  other, and space counts use it. It is filled for obvious cases only.

## 5. The published tables

A release is a folder of Parquet files. Users read it with pandas, R or DuckDB. There is no server.

```
release/<version>/
  release.json               version, date, git commit, analyser, row counts, sha256 of each file
  columns.csv                the column reference
  irs.parquet                one row per IR, 91 scalar columns        <- the table
  spaces.parquet             one row per space
  datasets.parquet           one row per catalogued dataset (222)
  downloads.parquet          one row per download and source: URL, size, checksum, order to try
  references.bib             the papers to cite
  licence_texts/             the licence notices and attribution lines that get writes into LICENSES.md
  bands/<dataset>.parquet    one row per IR and band: octave, third-octave and broadband values   (fetched when asked)
  files/<dataset>.parquet    one row per published file: path, size, sha256, download, role   (fetched by get)
  all_irs/<dataset>.parquet  sampled datasets only: every IR found, with its file and position   (fetched by get)
  source_meta/<dataset>.parquet   the dataset's own per-IR fields, where it has any    (fetched when asked)
```

### `irs.parquet`

This is what `search`, `get` and `select()` filter, and what `irs.csv` holds for a selection. Every cell is
a scalar: no lists, no JSON. So the CSV in the output folder is the same table.

| Group (row of Table 1) | Columns | Comes from |
|---|---|---|
| Identity (8) | `ir_id`, `dataset`, `space`, `condition`, `source`, `receiver`, `is_primary`, `duplicate_of` | index; `duplicate_of` derived |
| Audio (9) | `file`, `item`, `download`, `audio_hash`, `fs`, `channels`, `duration_s`, `sample_format`, `measured_on` (the channels the values describe: `L+R`, `W`, `ch0`) | index, measure |
| Description (12) | `origin` (measured, simulated, device), `format` (mono, stereo, binaural, ambisonic, array), `layout` (the 12 capture formats), `space_type`, `volume_m3`, `distance_m`, `src_x`, `src_y`, `src_z`, `rcv_x`, `rcv_y`, `rcv_z` (metres, in the dataset's own frame) | registry, index |
| Permitted use (6) | `licence`, `licence_conditions` (attribution, share_alike, non_commercial, no_derivatives), `use_research`, `use_commercial`, `use_training` (yes, no, unknown), `redistribute` (yes, unaltered_only, no, unknown) | the space's licence, else the dataset's, read through `licences.csv` at build time |
| Processing (8) | `is_processed`; `proc_windowed`, `proc_truncated`, `proc_denoised`, `proc_equalised`, `proc_resampled`, `proc_bit_depth_reduced`, `proc_format_derived` (`no`, `documented`, `detected`, `both`; empty = not assessed) | registry, derived |
| Quality (18) | `grade` (S, A, B, C, D; empty for anechoic spaces), `defects_major`, `defects_minor` (counts), eleven true/false columns (`defect_clipping`, `defect_dropouts`, `defect_no_decay`, `defect_sweep_residue`, `defect_tonal_interference`, `defect_corrupt_samples`, `defect_band_limited`, `defect_quantisation_floor`, `defect_dc_offset`, `defect_high_noise_floor`, `defect_inverted_channel`), `known_defect` (a defect of the files as published, in words, from the registry: the IR stays, rirdb warns), `usable_top_hz`, `effective_bits`, `peak_to_noise_db` | derived; the three numbers measured; known_defect from the registry |
| Acoustics (20) | `edt_mid`, `t20_mid`, `t30_mid`, `rt_mid`, `rt_mid_from`, `decay_range_mid_db`, `rt_broadband`, `rt_broadband_from`, `decay_range_broadband_db`, `c50_mid`, `c80_mid`, `d50_mid`, `ts_mid`, `drr_db`, `sti`, `iacc_early`, `lateral_fraction`, `bass_ratio`, `treble_ratio`, `mixing_time_ms` | measured; empty when not valid |
| Octave T30 (10) | `t30_31`, `t30_63`, `t30_125`, ... `t30_16000` | derived from `bands` |

- `condition` is the state of the space during the measurement, as the dataset names it: a panel setting,
  a furniture layout, a door open or closed. It is empty when the dataset has one state.
- Dataset and space facts (`origin`, `space_type`, `volume_m3`, the licence columns) are **joined in at
  build time**. `origin` is the dataset's, unless a space states its own (a scale model inside a measured
  dataset). Users get one flat table; the facts still live only in the registry (R1).
- **Two reverberation times.** `rt_mid` is the mean of the 500 Hz and 1 kHz octave bands: T30 where it is
  valid, otherwise T20 (`rt_mid_from` says which). T30 alone is empty for 70 % of today's IRs.
  `rt_broadband` is the same, measured on the whole signal (`rt_broadband_from`). Each has its decay
  range next to it.
  - `--balance t30` and the documented examples use `rt_mid`. It is the usual single number in room
    acoustics, it is what papers and datasets report, and it does not follow the spectrum of the
    loudspeaker.
  - `rt_broadband` is published because it costs nothing (analyser v1 already measures it) and many
    users expect it. Filter on it with `--where "rt_broadband < 0.5"`.
  - They are two quantities, not a spare for each other. Measured on today's 256,025 IRs
    ([output and script](../../reports/verify/2026-11-14-rt-mid-vs-broadband.txt)): each exists for 57 % of
    the IRs, and almost always for the same ones. Where both exist, they differ by more than 10 % for
    43 % of the IRs. Below 0.5 s the broadband value is typically 25 % longer; between 1 and 2 s it is
    6 % shorter. Per dataset, the typical ratio runs from 0.65 (MeshRIR) to 1.53 (Rochester).
- `grade` is empty for anechoic spaces: there is no decay to grade. The defect columns still apply.
- Table 1 of the abstract lists "upsampled" and "padded bit depth" as defects, and "resampled" and "bit
  depth reduced" as processing. Each pair is one test, stored once (`proc_resampled`,
  `proc_bit_depth_reduced`). It counts as a minor defect only when detected, not when the authors
  document a deliberate resampling.
- `duplicate_of` names the original of an IR with the same `audio_hash`: the IR from the earliest dataset
  by year, then the smallest id. Only sampled IRs are compared.
- Sampling weights are **not stored**. A stored weight is wrong as soon as a filter is applied: if a filter
  keeps 10 of 1,000 IRs in one space and 5 of 5 in another, the stored weights draw the second space 100
  times as often. `--balance` computes the weights on the selection and writes them to its `irs.csv`.

### `bands/<dataset>.parquet`

One row per IR and band: `ir_id`, `series` (`octave`, `third_octave`, `broadband`), `band_hz`, `edt`, `t20`,
`t30`, `c50`, `c80`, `d50`, `ts`, `decay_range_db`, `floor` (acoustic, quantisation, digital zero, none),
and for octave rows `iacc_early` and `lateral_fraction`. Octaves 31.5 Hz to 16 kHz, third-octaves 20 Hz
to 20 kHz, and one broadband row per IR (`band_hz` empty) share the table.

- A new band adds rows, not columns. Both band series have a 125 Hz band, so every query names the
  series: `WHERE series = 'octave' AND band_hz = 125 AND t30 > 1`.
- The broadband row is measured on the whole signal, limited to the analysed range (63 Hz to 8 kHz in
  analyser v1). The range for 1.0 is a setting of `measure.yaml`, fixed at step 4.
- The published values are judged: an invalid value is empty, as in `irs`. The raw values stay in
  `tables/`.
- `irs.t30_<band>` and `irs.rt_broadband` are **computed from this table** by `build`, and a check
  compares them, so they cannot disagree.
- `--where` filters `irs`, and may use `bands` in a subquery. `rirdb.bands(selection, "c80")` returns one
  column per band in one call.

### The other tables

- **`spaces.parquet`**: `space`, `dataset`, `label`, `space_type`, `origin`, `volume_m3`, `licence`,
  `redistribute`, `attribution`, `same_as`, `n_irs`, `n_irs_found`.
- **`datasets.parquet`**: every field of the dataset's registry file, the four permitted uses, the reader
  and its settings, the pinned version, the analyser, the three counts, known issues, and the result of
  the reference check (`checked`, with the median ratio; `published, not checked`; `none published`).
- **`readers/<dataset>.parquet`** (added in step 2): one row per row of `irs`, with the reader's `locator`,
  `channel_roles` and `reference_role`. With `datasets.reader_settings`, it is what `get` needs to cut one IR
  out of a published file.
- **`downloads.parquet`** and **`files/<dataset>.parquet`**: what `get` needs. Each file names its
  download. Each download lists its sources in the order to try (original, mirror, archive copy), each
  with its own checksum, since a re-packed mirror differs. **Verification is per file**: whatever the
  source, every file must match its sha256. So `search` and `get` can print the download size first.

## 6. The datasets' own metadata (D2)

Datasets ship their own fields: file-name parts, SOFA attributes, CSV and JSON sidecars. They are kept in
three ways, and never as a blob in the main table.

1. **The original files, unchanged.** Sidecars, READMEs and the SOFA or HDF5 files are pinned like the
   audio, and `get --original` delivers them. This is the "original metadata" of Table 1.
2. **Columns of `irs`**, where a field means the same thing across datasets: positions, distance,
   condition. The reader fills them, as today.
3. **`source_meta/<dataset>.parquet`**, where the reader already extracts more: one row per IR, with
   `ir_id` and the dataset's own fields under their own names, kept as read.

For 1.0 the third table holds what readers extract today (file-name fields, container attributes).
Joining every sidecar table for every dataset is in the deferred list (deferred.md). This narrows D2
("everything, per IR"); the reviewer advised dropping the table altogether, and I kept it because you
asked for the datasets' own metadata to be kept.

## 7. The registry: one folder per dataset

```
registry/
  <id>/
    dataset.yaml        hand     name, licence and every statement found, citation keys, sources, origin,
                                 default space type, cap, documented processing, how to read the files,
                                 known issues
    spaces.csv          hand     the spaces that differ from the dataset's defaults or carry extra facts:
                                 type, origin, volume, same_as, licence, where it was read, attribution
    reference.csv       hand     optional: values the authors publish (space, band, parameter, value, source)
    downloads.tsv       pin      one row per download: URL, size, checksum, version, extract_to
    files.tsv.gz        pin      one row per file: path, size, sha256, download, role, IRs found
  references.bib        hand     the citations the dataset files name
  licence_texts/        hand     custom licence notices and attribution lines, unchanged
  retired.csv           tool     ir_ids that left a release, with the reason
src/rirdb/
  licences.csv          hand     one row per licence id: research, commercial, training, redistribute
  columns.csv           hand     every published column
  thresholds.yaml       hand     every validity, defect, tag and grade threshold (applied by build)
  measure.yaml          hand     every setting fixed while the audio is read (section 9)
  checks.sql            hand     rules that span tables
```

- Everything about a dataset is in its folder. Adding a dataset is adding a folder. A contribution is one
  folder in a pull request, plus reader code for the few datasets that need their own (4 of 46 today).
- All 222 datasets use this one format. A dataset that is catalogued but not downloaded is a
  `dataset.yaml` alone. This replaces `datasets.yaml` (1,437 lines), `survey.json` (a second vocabulary),
  `registry/rooms/`, `registry/licenses/`, `registry/locks/`, `registry/manifests/` and four dictionaries
  in `report/guide.py`.
- **Spaces.** The dataset file gives the default type and origin. `spaces.csv` lists only the spaces that
  differ or carry a volume, a licence or a `same_as`. The keyword rules of `rooms.py` no longer run at
  build time; a command uses them to draft rows, which a person corrects. A key in `spaces.csv` that
  matches no space is an error (today it is silent).
- **Licences.** Where statements conflict, the dataset file lists every statement with where it was read,
  and the licence used is the strictest. Today four of the five conflicts in the registry record the more
  permissive one. A licence id with no row in `licences.csv` is an error (done on 2026-11-14). A licence
  stated in the dataset's paper counts as a statement and may be used (Ilias, 14 November;
  he checks the licences himself later).
- **File roles.** Every pinned file has a role: `ir`, `metadata`, `document`, `other audio` (sweeps, speech,
  noise), `excluded` with a reason, or `unreadable`. Today readers skip files silently. An unreadable
  audio file becomes a row with `defect_corrupt_samples`.

## 8. The pipeline: five stages

```
registry/<id>/ (git)   $RIRDB_ROOT/raw/<id>/   $RIRDB_ROOT/tables/<id>/                       $RIRDB_ROOT/release/
  dataset.yaml     ->  pin  ->  files  ->  index  ->  index.parquet  ->  measure  ->  <analyser>/measured.parquet  ->  build  ->  candidate/
  spaces.csv                                          source_meta.parquet             <analyser>/bands.parquet         release  ->  <version>/
```

| Stage | Reads | Writes | Redo when |
|---|---|---|---|
| `pin` | `dataset.yaml` | raw files, `downloads.tsv`, `files.tsv.gz` | a source changes |
| `index` | raw files (names and headers; no samples decoded) | `index.parquet` (every IR found, with `sampled`), `source_meta.parquet` | a reader or its settings change |
| `measure` | the sampled index rows and their audio | `<analyser>/measured.parquet`, `<analyser>/bands.parquet` (numbers only), `run.json` | the measurement code or `measure.yaml` changes: rare, by design |
| `build` | registry, all `tables/`, the plain files | `release/candidate/` | anything curated or any threshold changes; it always rebuilds everything |
| `release` | `release/candidate/` | `release/<version>/`, frozen | when you decide to publish |

- Each of the first three stages works on one dataset and writes that dataset's files. Deleting
  `tables/<id>/` redoes that dataset and touches no other.
- Only `measure` decodes audio. Only `build` judges. The signal audit becomes part of `measure`, so the
  audio is read once (today it is read twice).
- **Measurements are kept per analyser** (`tables/<id>/v1/`, `tables/<id>/v2/`). The December run does not
  overwrite the v1 numbers that the 0.9 preview is built from.
- **A stale measurement cannot pass.** Each index row carries `audio_key`, a hash of everything that
  decides which samples are analysed: file, item, crop, channel choice. `measured` repeats it, and `build`
  fails where the two differ, naming the IRs to measure again.
- `build` can run as often as needed; it only ever writes the candidate. `release` freezes a candidate
  under a version, and refuses unless every check passes and the commit is on GitHub.
- `rirdb dev status` shows how far each dataset is (pinned, indexed, measured with which analyser, in the
  release). It reads the folders; there is no separate progress file.
- Decay curves are not kept. `rirdb analyse --curves` writes them locally.

## 9. What the audio pass stores

"Numbers, never verdicts" needs a precise list, because analyser v1 breaks it today. The audit stores
`tonal_frac`, the share of spectral lines more than 15 dB above their surroundings, and it counts only
zero runs of at least 5 ms as dropouts (`THRESH` in `src/rirdb/audit.py`). Those two thresholds are baked
into the stored numbers. After the one run, changing either would mean reading all the audio again.

So every test in the measurement spec is written as one row of this table, before the pilot:

| Test | Stored number | Setting fixed while reading (`measure.yaml`) | Threshold applied by `build` (`thresholds.yaml`) |
|---|---|---|---|
| Dropouts | longest run of exact zeros inside the signal, in ms; number of runs | where the signal starts and ends | the run length that counts as a dropout |
| Tonal interference | the largest prominence of a sustained spectral line, in dB; its frequency | the analysis window and the definition of "sustained" | the prominence that counts as tonal |
| Decay time (T30) | the fitted time; the decay range in dB | the filter bank, the noise-floor method, the fit range | the decay range that makes it valid (45 dB) |
| ... | one row per test | ... | ... |

- **The rule:** store the least-processed number that turns the decision into a build-time threshold.
- Each row also states **which layouts the test applies to**. A test that compares channels (opposite
  polarity) applies to stereo and binaural pairs. It never applies to Ambisonic signals (R6).
- `measure.yaml` holds every setting of the middle column: today's `configs/analyzer_v1.yaml` and the
  measure-time half of `THRESH`. `run.json` stores its hash and the code version. Together they are the
  analyser version; nobody bumps it by hand.
- This table is a deliverable of step 4 (new measures and pilot). The pilot is not accepted without it.
- Step 1 builds the 0.9 tables from the v1 measurements as they are, with v1's flags. That is a stated
  limit of the preview.

## 10. Keeping it correct

**Checks** (`build` fails if one fails). They do the job of keys and constraints in a database server:

- every `ir_id` is unique and equals the hash of its `file#item`; every IR has a dataset, a space, a file
  and a download; every pinned audio file has a role;
- every value of a closed list is allowed; every number is finite and inside its physical range
  (generated from `columns.csv`);
- outside corrupt rows, a cell that applies is empty only when its validity rule fails;
- no licence with a non-commercial term has `use_commercial = 'yes'`; a grade S row has no defect and every defect assessed; a
  row with a major defect has grade C or D;
- `duplicate_of` points to a row with the same `audio_hash`; `same_as` and every key of `spaces.csv` point
  to an existing space; every space has primary rows;
- `irs.t30_<band>` equals the octave row of `bands`, and `irs.rt_broadband` its broadband row;
- every IR found in a dataset without a `cap` is in `irs`; every dataset with a `cap` has its `all_irs`
  table, and that table holds every row the dataset has in `irs`;
- the index and the measurements hold the same sampled ids with the same `audio_key`, from one analyser;
- from 1.0 on: no `ir_id` of the previous release is missing unless it is in `retired.csv`.

**Versions.**

- `X.Y` names a set of tables. A third digit is a code-only fix that uses the same tables (`1.0.3` reads
  the tables of `1.0`). Each package version ships the checksums of the tables it uses by default.
- Released tables never change. A fix to the data is a new `X.Y`, and `CHANGES.md` is written by comparing
  with the previous one: rows added and removed per dataset, cells changed per column. Two builds from
  the same inputs are compared cell by cell, not byte by byte (`release.json` holds a date).
- Within a major version, columns are only added. A removed or redefined column is a new major version.
- `selection.lock` records the version, the command and seed, and each `ir_id` with its `audio_hash`.
  `get --lock` fetches the tables of that version, then those ids, and fails loudly on a hash mismatch.

**What a change costs.**

| You change | You edit | You re-run | Reads audio |
|---|---|---|---|
| a licence, a citation, a known issue | one `dataset.yaml` | `build` | no |
| the type or volume of a space | one `spaces.csv` | `build` | no |
| a threshold or a grade rule | `thresholds.yaml` | `build` | no |
| how a dataset's files are read | its `dataset.yaml` | `index`; `build` then names the IRs to measure again, if any | only those IRs |
| a new dataset | a new folder | `pin`, `index`, `measure` for it, then `build` | that dataset |
| a setting of `measure.yaml`, or the measurement code | the file or the code | `measure` for all, then `build` | all |

**Safety.**

| What | Where | If lost |
|---|---|---|
| Registry, pins, code | git, pushed to GitHub | nothing is lost |
| `tables/` (hundreds of CPU-hours) | home (backed up nightly), and archived with every release | restore from the release archive |
| Releases | Zenodo (DOI) and Hugging Face | nothing is lost |
| Raw audio | group store, scratch, Ceph copy | re-fetch from the pinned sources; if a source has vanished, from the Ceph copy or a mirror |

## 11. Decisions (taken on 2026-11-14)

These change what the 1.0 spec or the abstract says. Ilias set 1, 2 and 4 himself. The others stand as
recommended, since he asked to finalise the plan.

1. **Grades are S, A, B, C, D.** The draft said A+, which was confusing.

   | Grade | Rule |
   |---|---|
   | S | T30 valid in every octave from 125 Hz to 4 kHz, no defect at all, and every defect assessed (decision D, 15 Nov) |
   | A | the same decay rule; minor defects allowed |
   | B | T20 or T30 valid in every octave from 250 Hz to 2 kHz; minor defects allowed |
   | C | some decay value valid; or the decay of S, A or B with a major defect |
   | D | no decay value valid, or a known defect of the files as published (registry `defects`) |

   `--grade A` means A or better, so the abstract's command still works. It no longer returns clipped
   IRs: by D9, an IR with a valid decay and clipping had grade A. The grade is empty for anechoic
   spaces. Table 1 of the submitted abstract says "A+ to D"; the abstract is not changed.
2. **Only very dense datasets are sampled** (section 3, R6). The draft applied a cap of 500 to every
   dataset. That would have cut 13 of today's 46 datasets, among them TAU-SRIR (Ambisonic: 38,530 to
   28,536 IRs) and BRUDEX (1,512 to 1,500), and saved nothing. D4's "spread over source–receiver
   distance" is dropped: positions exist for 7 of 46 datasets, and a hash draw already spreads over a
   grid.
3. **Sampling weights are computed, not stored** (changes D11 and A4).
4. **Two reverberation times, `rt_mid` and `rt_broadband`** (section 5). `--balance t30` uses `rt_mid`.
5. **What `rirdb get --dataset sriracha` delivers.** The abstract says it "downloads one dataset". With
   `--dataset` and no other filter: the files as published, and `irs.csv` for the rows in the table. For
   a sampled dataset it adds `all_irs.parquet` and one line such as "10,000 of 1,327,360 IRs measured;
   `rirdb analyse sriracha --all` measures the rest". Converting to one WAV per IR happens when a filter
   or `--format` is given.
6. **The id promise starts at 1.0.** The 0.9 preview shown at DMRN says that ids, rows and columns may
   change.
7. **"Every IR" in the abstract.** The table holds every IR, except in datasets above 100,000 IRs, where
   it holds a fixed sample and says so. The site and the poster should word it that way.
8. **The download size of a cross-dataset selection.** 500 IRs from 100 datasets can mean 100 archives,
   and one SoundCam IR sits in a 51.7 GB file. Proposed for 1.0: `search` and `get` print the size first,
   `get` asks before a large download, `--max-download` keeps a selection within a budget. Fetching single
   files out of remote archives is in the deferred list (deferred.md).

## 12. How this draft was checked

**Two independent designs.** Two engineers designed the data model from scratch without seeing my first
draft. All three designs agreed on: one row per IR; the four stages; one fact one home with the release
as build output; empty cells instead of `valid_*`; Parquet and DuckDB with no server; one column list;
the audit folded into the audio pass; cutting the Postgres schema, CLAP and the stored copies.

| Topic | My first draft | Independent designs | Chosen | Why |
|---|---|---|---|---|
| Per-band values | arrays in a per-dataset detail file | A: 70 octave columns in the main table, 124 third-octave columns in a second file. B: one long table, plus six T30 columns in the main table | long `bands` table, plus ten octave T30 columns | Both rejected arrays: the band hides behind a position that starts at 1 in DuckDB and 0 in pandas, and v1 has 8 octaves where 1.0 has 10 |
| The datasets' own metadata | a JSON cell per IR | A: none per IR. B: one table per dataset | one table per dataset, minimal | No blob; each dataset keeps its own column names |
| Registry layout | one folder per kind of file | A: one folder per dataset. B: as the first draft | one folder per dataset | Adding, removing or contributing a dataset is one folder |
| Licence readings | rules in code | both: a lookup table | lookup table | Readable and editable without code; an unknown id is an error |
| Sampling weights | stored, with a doubt | both: computed at query time | computed | A stored weight is wrong after any filter |
| `ir_id` separator | `/` | both: `-` | `-` | One token, safe as a file name and in a URL |
| Column definitions | a Python module | A: Python. B: `columns.csv`, `thresholds.yaml`, `checks.sql` | the plain files | Readable without Python; the CSV is also the published column reference |
| The IR list (index) | in the data root | A: committed to git. B: in the data root | data root | It is rebuilt from the pinned files in minutes |
| Coordinates | in the main table | A: out (each dataset has its own frame). B: in | in, labelled "the dataset's own frame" | Cheap, and spatial-audio users need them |

**One adversarial review** of the second draft, against the code. Its verdict was "do not code from this
draft yet": three blockers and nine should-fix points. I checked its main claims in the code (they hold)
and accepted all twelve.

| # | Finding | What changed |
|---|---|---|
| 1 | Blocker. The stored numbers already contain thresholds (dropout length, tonal prominence), so "numbers, never verdicts" was not yet true | Section 9: one table per test, `measure.yaml`, the hash in `run.json` |
| 2 | Blocker. The cap was unstable when data is added, and a per-dataset ceiling would leave about a fifth of GWA's rooms without a row | Section 3: the index lists every IR with `sampled`; one cap number per dataset; draw per space, condition and layout |
| 3 | Blocker. The `ir_id` recipe was not frozen: a second archive added later would move every path; two-file IRs did not fit `file` | Section 4: `file#item`, published and checked; `extract_to` pinned |
| 4 | A reader setting can change the audio analysed without changing the id | Section 8: `audio_key`; measurements kept per analyser |
| 5 | Per-space licences lost their source and note; a mistyped space key was silent | Sections 5, 7 |
| 6 | An empty cell had four meanings | R4, `columns.csv`, `decay_range_mid_db`, an explicit `no` for tags |
| 7 | "Why is this IR missing" had no answer: readers skip files silently | Section 7: file roles |
| 8 | `is_primary` had no defined group | Section 3 |
| 9 | "The same checksum for every source" fails for re-packed mirrors | Section 5: verification per file |
| 10 | One version number contradicted replaying an old lock; `build` froze nothing; 160,000 ids would be "retired" between 0.9 and 1.0 | Sections 4, 8, 10: `X.Y` and a code digit; `release` as its own step; the promise from 1.0 |
| 11 | Octave T30 had two homes; `band_hz = 125` matched two rows | Section 5 |
| 12 | `tables/` lived only in home; "re-fetch" fails when a source vanishes | Section 10 |

Not done: the review was one reviewer with three lenses. Three separate reviewers and a judge were
planned and did not run, because the account's usage limit stopped them.

**Ilias's review (14 November).** Three changes, all folded in above.

| His point | What changed |
|---|---|
| "A+ is confusing" | Grades are S, A, B, C, D (section 11, decision 1) |
| "We can have a T broadband as well" | `rt_broadband` next to `rt_mid`, from a broadband row in `bands`. `rt_mid` stays the default; the measured comparison is in section 5 |
| "Don't make it punitive" for Ambisonic or dense datasets | R6. Only datasets above 100,000 IRs are sampled, and they stay listed, downloadable and measurable in full (`all_irs`, `analyse --all`). No channel-comparison test on Ambisonic signals. The grade is empty for anechoic spaces |

## 13. What changes from today

| Today | Proposed |
|---|---|
| Index rows carry licence, type and wave copied from the registry | Removed. Joined at build time |
| `metrics` (327 columns) and `audit` (30 columns), joined by DuckDB views | `measured.parquet` and `bands.parquet` per dataset; defects and grades computed at build time |
| `valid_*` columns | An empty cell means not valid |
| Grades A to D, from decay validity only | S to D, from decay validity and defects; empty for anechoic spaces |
| Six datasets measured at one IR in sixteen | Three grids sampled at 500 per room setup and listed in full; every other dataset measured in full |
| A broadband T30 kept in the metrics table, not shown | `rt_broadband` next to `rt_mid` |
| One column per band and quantity | Ten octave T30 columns in `irs`; everything per band in `bands` |
| `ir_id` = `<dataset>:<hex>` | `<dataset>-<hex>` |
| `datasets.yaml`, `survey.json`, `rooms/`, `licenses/`, `locks/`, `manifests/` | One folder per dataset, one format, all 222 |
| Licence read by string tests in four places | `licences.csv` and one function (done) |
| Space types from keyword rules at build time, corrected in report code | A default per dataset and listed exceptions in `spaces.csv` |
| Readers skip unreadable or unwanted files silently | Every file has a role |
| Thresholds inside the audit code | `measure.yaml` (fixed while reading) and `thresholds.yaml` (applied by build) |
| Two data roots | One: `$RIRDB_ROOT` (done for this checkout) |
| Decay curves, mel and EDR features, CLAP embeddings and maps in the main pass | Curves on request (`analyse --curves`); the rest parked until after 1.0 |
| Postgres schema, never used | Deleted |
