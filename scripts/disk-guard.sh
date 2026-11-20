#!/usr/bin/env bash
# Refuse to build when the repository filesystem is nearly full.
# Below TCORD_MIN_FREE_GIB (default 10) stale caches are cleaned; if free space is
# still below TCORD_HARD_MIN_FREE_GIB (default 5) the build is aborted.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
min_free_gib="${TCORD_MIN_FREE_GIB:-10}"
hard_min_free_gib="${TCORD_HARD_MIN_FREE_GIB:-5}"

free_gib() {
    df -Pk "$repo_root" | awk 'NR == 2 { printf "%d", $4 / 1048576 }'
}

free="$(free_gib)"
echo "disk-guard: ${free} GiB free on $(df -P "$repo_root" | awk 'NR == 2 { print $6 }')"
if (( free >= min_free_gib )); then
    exit 0
fi

echo "disk-guard: below ${min_free_gib} GiB, cleaning stale build caches:"
echo "  - uv cache prune"
echo "  - cargo clean (${repo_root}/target)"
echo "  - dist/, build/, *.egg-info"
if command -v uv > /dev/null; then
    uv cache prune || echo "disk-guard: uv cache prune failed" >&2
fi
if command -v cargo > /dev/null; then
    (cd "$repo_root" && cargo clean) || echo "disk-guard: cargo clean failed" >&2
fi
(
    cd "$repo_root"
    rm -rf dist build
    find . -path ./.venv -prune -o -name '*.egg-info' -type d -prune -exec rm -rf {} +
)

free="$(free_gib)"
echo "disk-guard: ${free} GiB free after cleanup"
if (( free < hard_min_free_gib )); then
    echo "disk-guard: only ${free} GiB free after cleanup" >&2
    exit 1
fi
