#!/bin/bash
# Measure datasets with analyser v1 and write tables/<id>/v1/, which `rirdb dev build` reads (data-model.md, section 8).
# One chain of SLURM jobs per dataset, every dataset at once:
#   index-v1 -> analyze (array, every IR) -> merge -> audit (array) -> audit-merge -> scripts/one_off/v1_to_tables.py
#
#   jobs/measure_tables.sh <id> ...        # IR counts (for the shards) from reports/onboarding/review.tsv
#   IRS_TSV=other.tsv jobs/measure_tables.sh <id> ...
#
# Bash only (login node); every Python step runs inside sbatch, on a copy of the registry made now. A dataset that
# v1 measured already ($RIRDB_ROOT/metrics/v1/<id>) is skipped, so this never overwrites a measurement.
# Logs: qlogs/ms_<id>_<step>_<job>.out
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -gt 0 ] || { echo "give dataset ids"; exit 1; }
set -a; [ -f .env ] && source .env; set +a
counts=${IRS_TSV:-reports/onboarding/review.tsv}
snap="$PWD/qlogs/registry_$(date +%Y%m%dT%H%M%S)_measure"
rsync -a --exclude=files.tsv.gz registry/ "$snap/"
run() { sbatch --parsable --export=ALL,RIRDB_REGISTRY="$snap" "$@"; }
for id in "$@"; do
    if [ -e "$RIRDB_ROOT/metrics/v1/$id" ]; then echo "$id: measured already, skipped"; continue; fi
    n=$(awk -F'\t' -v id="$id" '$1 == id {print $3}' "$counts")
    [ -n "$n" ] && [ "$n" != "-" ] || { echo "$id: no IR count in $counts, skipped"; continue; }
    [ "$n" -le 100000 ] || { echo "$id: $n IRs, above the 100,000 cap: measure its sample by hand"; continue; }
    shards=$(( (n + 299) / 300 )); [ "$shards" -gt 200 ] && shards=200
    log="qlogs/ms_${id}"
    ix=$(run -p computeshort -t 1:0:0 -c 2 --mem-per-cpu=8G -J "mi_$id" -o "${log}_index_%j.out" -e "${log}_index_%j.err" \
         jobs/quick_cpu.sh pixi run rirdb dev index-v1 "$id")
    an=$(run --dependency=afterok:"$ix" --array=0-$((shards - 1))%50 -J "ma_$id" -o "${log}_an_%A_%a.out" \
         -e "${log}_an_%A_%a.err" jobs/cpu_array.sh pixi run rirdb dev analyze --dataset "$id" --shard auto \
         --n-shards "$shards" --full)
    mg=$(run --dependency=afterok:"$an" -p computeshort -t 0:30:0 -J "mm_$id" -o "${log}_merge_%j.out" \
         -e "${log}_merge_%j.err" jobs/quick_cpu.sh pixi run rirdb dev merge "$id")
    au=$(run --dependency=afterok:"$mg" --array=0-$((shards - 1))%50 -J "mu_$id" -o "${log}_au_%A_%a.out" \
         -e "${log}_au_%A_%a.err" jobs/cpu_array.sh pixi run rirdb dev audit --dataset "$id" --shard auto --n-shards "$shards")
    am=$(run --dependency=afterok:"$au" -p computeshort -t 0:20:0 -J "mx_$id" -o "${log}_auditmerge_%j.out" \
         -e "${log}_auditmerge_%j.err" jobs/quick_cpu.sh pixi run rirdb dev audit-merge "$id")
    cv=$(run --dependency=afterok:"$am" -p computeshort -t 0:20:0 -J "mt_$id" -o "${log}_tables_%j.out" \
         -e "${log}_tables_%j.err" jobs/quick_cpu.sh pixi run python scripts/one_off/v1_to_tables.py "$id")
    echo "$id: $n IRs in $shards shards; index $ix, analyze $an, merge $mg, audit $au, audit-merge $am, tables $cv"
done
