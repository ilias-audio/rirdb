#!/bin/bash
# Write $RIRDB_ROOT/tables/<id>/index.parquet for each dataset: one job per dataset, all in parallel.
#   jobs/index_tables.sh <id> ...
#   jobs/index_tables.sh $(ls "$RIRDB_ROOT/metrics/v1")     # the datasets analyser v1 measured
# The jobs read a copy of the registry made now (RIRDB_REGISTRY). Logs: qlogs/index_<id>_<job>.out
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -gt 0 ] || { echo "give dataset ids"; exit 1; }
snap="$PWD/qlogs/registry_$(date +%Y%m%dT%H%M%S)_index"
rsync -a --exclude=files.tsv.gz registry/ "$snap/"
for id in "$@"; do
    sbatch --parsable -p computeshort -t 1:0:0 -c 2 --mem-per-cpu=16G --export=ALL,RIRDB_REGISTRY="$snap" \
        -J "ix_$id" -o "qlogs/index_${id}_%j.out" -e "qlogs/index_${id}_%j.err" \
        jobs/quick_cpu.sh pixi run rirdb dev index "$id"
done
