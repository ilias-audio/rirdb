# RIRDB

Find, select, download and verify room impulse responses (RIRs) from public datasets.

More than 200 RIR datasets are publicly available, but they differ in host, licence, file format and quality, often in undocumented ways. RIRDB is a database and tool that catalogues these datasets, measures and classifies every IR they contain, and lets researchers select, download and verify IRs by licence, format, origin, quality and room diversity.

## Status

Under development. Version 0.0.1 only reserves the package name: it installs a `rirdb` command that prints a notice.

Planned interface:

```
pip install rirdb
rirdb get --dataset sriracha
rirdb get --use research --format binaural --grade A+ --balance space --n 500
rirdb analyse my_irs/
```

## Licence

MIT
