#!/bin/bash
# Extract fetched archives of a dataset into raw/<id>/files/.
#
#   fetch/extract.sh --wave 0
#   fetch/extract.sh tau_srir
#
# unzip -DD / tar --touch give extracted files fresh mtimes (scratch purges by
# mtime). Split zips (x.z01, x.z02, ..., x.zip) are joined first. Archives are
# deleted after a successful extraction when keep_archive is 0, or when "auto"
# and the dataset's archives exceed 5 GB (the lock is enough to re-fetch).

source "$(dirname "${BASH_SOURCE[0]}")/lib_common.sh"

# Archives are checksum-verified against the provider before extraction, so Info-ZIP's
# zip-bomb heuristic (false positives on large ZIP64 archives, e.g. MYRiAD v1) is disabled;
# genuinely truncated archives still fail on their missing bytes.
export UNZIP_DISABLE_ZIPBOMB_DETECTION=TRUE
SEVENZIP="$REPO_ROOT/.pixi/envs/default/bin/7z"

AUTO_KEEP_MAX_BYTES=$((5 * 1024 * 1024 * 1024))

extract_archive() { # archive files_dir
    local a=$1 files=$2 base
    case "$a" in
        *.z[0-9][0-9]) return 0 ;;  # parts of a split zip; handled with the .zip
        *.zip)
            base=${a%.zip}
            if compgen -G "$base.z[0-9][0-9]" >/dev/null; then
                log "  joining split zip $(basename "$a")"
                local joined; joined=$(mktemp -d "$(dirname "$a")/join.XXXX")
                zip -q -s 0 "$a" --out "$joined/joined.zip"
                unzip -DD -q -o "$joined/joined.zip" -d "$files"
                rm -rf "$joined"
            else
                # Some >4 GB archives are written without proper ZIP64 records (offsets wrap at
                # 4 GiB; e.g. RSoANU em32, checksum-verified). Fallbacks: 7-Zip, then rebuild the
                # central directory from the local headers with `zip -FF` and unzip the repair.
                if ! unzip -DD -q -o "$a" -d "$files"; then
                    log "  unzip failed; retrying with 7-Zip"
                    if ! "$SEVENZIP" x -y -bso0 -bsp0 -o"$files" "$a"; then
                        local repaired="${a%.zip}.repaired.zip"
                        if [ ! -s "$repaired" ]; then
                            log "  7-Zip failed; rebuilding the zip directory with zip -FF"
                            yes | zip -FF "$a" --out "$repaired" >/dev/null
                        fi
                        unzip -DD -q -o "$repaired" -d "$files"
                        rm -f "$repaired"
                    fi
                    find "$files" -newer "$a" -prune -o -exec touch {} + 2>/dev/null || true
                fi
            fi ;;
        *.tar|*.tar.gz|*.tgz|*.tbz2|*.tar.bz2|*.tar.xz)
            tar --touch --no-same-owner -xf "$a" -C "$files" ;;
        *) die "don't know how to extract $a" ;;
    esac
}

archive_stem() {
    local b=$1   # keep any record prefix directory
    for ext in .tar.gz .tar.bz2 .tar.xz .tgz .tbz2 .tar .zip; do
        case "$b" in *"$ext") echo "${b%"$ext"}"; return ;; esac
    done
    echo "$b"
}

# The folder an archive is unpacked into: extract_to of its row in downloads.tsv (see pin_extract_to).
extract_dir() {
    local x
    x=$(awk -F'\t' -v r="$1" 'NR > 1 && $1 == r { print $8 }' "$(downloads_tsv "$D_ID")")
    case "$x" in
        .) echo "$files" ;;
        ""|-) if [ "$n_arch" -gt 1 ]; then echo "$files/$(archive_stem "$1")"; else echo "$files"; fi ;;
        *) echo "$files/$x" ;;
    esac
}

# Every archive the pin lists that was downloaded and verified (.ok) must be unpacked: else the dataset fails,
# never "ok"
# (in November 2026 a symlinked _archives folder made find see nothing, and extraction reported ok).
check_all_extracted() { # okdir
    local missing
    missing=$(tail -n +2 "$(downloads_tsv "$D_ID")" | cut -f1 \
        | { grep -E '\.(zip|tar|tar\.gz|tgz|tbz2|tar\.bz2|tar\.xz)$' || true; } \
        | while IFS= read -r rel; do [ ! -f "$1/$rel.ok" ] || [ -f "$1/$rel.extracted" ] || echo "$rel"; done)
    [ -z "$missing" ] || die "extract $D_ID: downloaded but not extracted: $(echo $missing)"
}

extract_one() {
    load_row "$1"
    local raw files okdir arch total keep n_arch
    raw=$(raw_dir "$D_ID"); files="$raw/files"; okdir="$raw/.ok"
    mkdir -p "$files"
    arch="$raw/_archives"
    # Several archives of one dataset may contain identical paths (e.g. C4DM's
    # three Omni.zip files all hold Omni/00x00y.wav): each gets its own subfolder.
    # The folder is pinned (extract_to in downloads.tsv), so a re-fetch lays files out
    # identically and a download added later moves nothing. n_arch is for pins without it.
    n_arch=$(tail -n +2 "$(downloads_tsv "$D_ID")" | cut -f1 | { grep -cE '\.(zip|tar|tar\.gz|tgz|tbz2|tar\.bz2|tar\.xz)$' || true; })
    if [ ! -d "$arch" ] || [ -z "$(ls -A "$arch" 2>/dev/null)" ]; then
        log "extract $D_ID: nothing to extract"
        check_all_extracted "$okdir"
        state_set "$D_ID" extracted_at "$(now_json)"
        return 0
    fi
    total=$(du -sbD "$arch" | cut -f1)   # -D: _archives may be a symlink to scratch
    case "$D_KEEP" in
        1) keep=1 ;;
        0) keep=0 ;;
        *) keep=$([ "$total" -lt "$AUTO_KEEP_MAX_BYTES" ] && echo 1 || echo 0) ;;
    esac
    log "extract $D_ID ($(numfmt --to=iec "$total") of archives; keep=$keep)"
    local a rel dest
    while IFS= read -r a; do
        rel=${a#"$arch/"}
        [ -f "$okdir/$rel.ok" ] || { log "  skip unverified $rel"; continue; }
        [ -f "$okdir/$rel.extracted" ] && continue
        log "  $rel"
        dest=$(extract_dir "$rel")
        mkdir -p "$dest"
        extract_archive "$a" "$dest"
        # some archives store directories without read/execute permission (e.g. Aalto robot set)
        chmod -R u+rwX "$files" 2>/dev/null || true
        date -u +%FT%TZ >"$okdir/$rel.extracted"
    done < <(find -H "$arch" -type f ! -name '*.part' | sort)
    if [ "$keep" -eq 0 ]; then
        find -H "$arch" -type f ! -name '*.part' -print0 | while IFS= read -r -d '' a; do
            rel=${a#"$arch/"}
            case "$rel" in *.z[0-9][0-9]) rel="${rel%.z[0-9][0-9]}.zip" ;; esac
            [ -f "$okdir/$rel.extracted" ] && rm -f "$a"
        done
    fi
    check_all_extracted "$okdir"
    state_set "$D_ID" extracted_at "$(now_json)"
    log "extract $D_ID: ok"
}

main() {
    local ids; ids=$(ids_from_args "$@")
    [ -n "$ids" ] || die "no datasets selected"
    local id
    for id in $ids; do extract_one "$id"; done
}

main "$@"
