#!/bin/bash
# Shared helpers for the network layer (bash + curl + jq; no Python).
# Sourced by fetch/*.sh. Reads registry/compiled/sources.tsv, which
# `rirdb dev registry compile` generates from registry/<id>/dataset.yaml.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# .env sets the defaults; a RIRDB_ROOT given by the caller wins (the tests run on their own data root).
GIVEN_ROOT="${RIRDB_ROOT:-}"
if [ -f "$REPO_ROOT/.env" ]; then
    set -a; source "$REPO_ROOT/.env"; set +a
fi
RIRDB_ROOT="${GIVEN_ROOT:-${RIRDB_ROOT:-$HOME/rirdb}}"
# The registry: the repository's, or a copy named by RIRDB_REGISTRY (as for the Python side).
REGISTRY_DIR="${RIRDB_REGISTRY:-$REPO_ROOT/registry}"
SOURCES_TSV="$REGISTRY_DIR/compiled/sources.tsv"
# registry/<id>/downloads.tsv: what to download (the pin). registry/<id>/files.tsv.gz: every extracted file.
downloads_tsv() { echo "$REGISTRY_DIR/$1/downloads.tsv"; }
files_tsv() { echo "$REGISTRY_DIR/$1/files.tsv.gz"; }
UA="rirdb/0.1 (+https://github.com/ilias-audio/room-impulse-responses)"
LOCK_HEADER=$'relpath\turl\tsize\talgo\tchecksum\tprovider_version\tresolved_at\textract_to'

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

curl_json() {
    curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 -m 180 -A "$UA" "$1"
}

# Content-Length of the final response after redirects ("-" if unknown).
http_size() {
    local n
    n=$(curl -sIL -m 60 -A "$UA" "$1" | tr -d '\r' | awk 'tolower($1)=="content-length:"{v=$2} END{print v}')
    echo "${n:--}"
}

# ETag / Last-Modified of the final response, as a provenance string.
http_version() {
    curl -sIL -m 60 -A "$UA" "$1" | tr -d '\r' \
        | awk 'tolower($1)=="etag:"{e=$2} tolower($1)=="last-modified:"{sub(/^[^:]*: /,""); m=$0} END{printf "etag=%s;last-modified=%s", e, m}' \
        | tr '\t' ' '
}

urldecode() { local s="${1//+/ }"; printf '%b' "${s//%/\\x}"; }

# load_row <id>: sets D_ID D_WAVE D_STATUS D_ACCESS D_KIND D_RECORDS D_URLS
#                D_INCLUDE D_EXCLUDE D_ACCEPT D_KEEP from sources.tsv
load_row() {
    local line
    [ -f "$SOURCES_TSV" ] || die "$SOURCES_TSV missing: run 'rirdb dev registry compile' (via sbatch) first"
    line=$(awk -F'\t' -v id="$1" 'NR>1 && $1==id' "$SOURCES_TSV")
    [ -n "$line" ] || die "unknown dataset id '$1'"
    IFS=$'\t' read -r D_ID D_WAVE D_STATUS D_ACCESS D_KIND D_RECORDS D_URLS \
        D_INCLUDE D_EXCLUDE D_ACCEPT D_KEEP <<<"$line"
}

# Print the '|'-joined list field one item per line ("-" = empty).
split_list() { [ "$1" = "-" ] || tr '|' '\n' <<<"$1"; }

# selected <file name>: true if it passes the dataset include/exclude globs.
selected() {
    local name=$1 g ok=1
    if [ "$D_INCLUDE" != "-" ]; then
        ok=0
        while IFS= read -r g; do
            # shellcheck disable=SC2053  # $g is a glob on purpose
            if [[ $name == $g ]]; then ok=1; break; fi
        done < <(split_list "$D_INCLUDE")
    fi
    [ $ok -eq 1 ] || return 1
    if [ "$D_EXCLUDE" != "-" ]; then
        while IFS= read -r g; do
            # shellcheck disable=SC2053
            if [[ $name == $g ]]; then return 1; fi
        done < <(split_list "$D_EXCLUDE")
    fi
    return 0
}

# ids_from_args [--wave N]... [--all] [id...]: dataset ids to process, one per
# line. Waves select datasets that are open and planned/active.
ids_from_args() {
    [ -f "$SOURCES_TSV" ] || die "$SOURCES_TSV missing: run 'rirdb dev registry compile' first"
    local waves=() ids=() all=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --wave) waves+=("$2"); shift 2 ;;
            --all) all=1; shift ;;
            -*) die "unknown option $1" ;;
            *) ids+=("$1"); shift ;;
        esac
    done
    for w in "${waves[@]+"${waves[@]}"}"; do
        awk -F'\t' -v w="$w" 'NR>1 && $2==w && $4=="open" && ($3=="planned" || $3=="active") {print $1}' "$SOURCES_TSV"
    done
    if [ $all -eq 1 ]; then
        awk -F'\t' 'NR>1 && $4=="open" && ($3=="planned" || $3=="active") {print $1}' "$SOURCES_TSV"
    fi
    printf '%s\n' "${ids[@]+"${ids[@]}"}" | sed '/^$/d'
}

raw_dir() { echo "$RIRDB_ROOT/raw/$1"; }

is_archive() {     # any case: PRAIRE publishes .ZIP
    case "${1,,}" in
        *.zip|*.z[0-9][0-9]|*.tar|*.tar.gz|*.tgz|*.tbz2|*.tar.bz2|*.tar.xz|*.rar|*.tar.part.[a-z][a-z].gz) return 0 ;;
        *) return 1 ;;
    esac
}

# state_set <id> <key> <json value>: merge one key into $RIRDB_ROOT/state/<id>.json
state_set() {
    local f="$RIRDB_ROOT/state/$1.json" tmp
    mkdir -p "$RIRDB_ROOT/state"
    [ -s "$f" ] || echo '{}' >"$f"
    tmp=$(mktemp "$f.XXXX")
    jq --arg k "$2" --argjson v "$3" '.[$k] = $v' "$f" >"$tmp" && mv "$tmp" "$f"
}

now_json() { printf '"%s"' "$(date -u +%FT%TZ)"; }

# The folder each archive is unpacked into (under raw/<id>/files) is part of the pin, so a download
# added later can never move the paths of the files already there (the ir_id is built from them).
#   .     unpacked straight into files/
#   name  unpacked into files/name/ (the archive's name without its extension)
#   -     not an archive
# pin_extract_to <lock> [<old lock>]: add or complete the extract_to column, keeping every value the
# old lock already pins. A new archive goes to files/ when it is the only one, else to its own folder.
pin_extract_to() {
    local new=$1 old pinned
    pinned=$(mktemp)
    old=${2:-}
    if [ -n "$old" ] && [ -f "$old" ]; then cp "$old" "$pinned"; fi
    awk -F'\t' -v OFS='\t' -v pinned_file="$pinned" '
        function is_archive(p) { return tolower(p) ~ /\.(zip|tar|tar\.gz|tgz|tbz2|tar\.bz2|tar\.xz|rar|tar\.part\.aa\.gz)$/ }
        function stem(p) { sub(/\.([Tt][Aa][Rr]\.part\.aa\.gz|[Tt][Aa][Rr]\.[Gg][Zz]|[Tt][Aa][Rr]\.[Bb][Zz]2|[Tt][Aa][Rr]\.[Xx][Zz]|[Tt][Gg][Zz]|[Tt][Bb][Zz]2|[Tt][Aa][Rr]|[Zz][Ii][Pp]|[Rr][Aa][Rr])$/, "", p); return p }
        FILENAME == pinned_file { if (FNR > 1 && NF >= 8 && $8 != "") pin[$1] = $8; next }
        FNR == 1 { next }
        { n++; rel[n] = $1; line[n] = $1; for (j = 2; j <= 7; j++) line[n] = line[n] OFS $j; if (is_archive($1)) n_arch++ }
        END {
            print "relpath", "url", "size", "algo", "checksum", "provider_version", "resolved_at", "extract_to"
            for (i = 1; i <= n; i++) {
                if (rel[i] in pin) x = pin[rel[i]]
                else if (!is_archive(rel[i])) x = "-"
                else x = (n_arch > 1 ? stem(rel[i]) : ".")
                print line[i], x
            }
        }' "$pinned" "$new" >"$new.extract_to"
    mv "$new.extract_to" "$new"
    rm -f "$pinned"
}
