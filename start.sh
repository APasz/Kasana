#!/usr/bin/env bash

set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

process_ids=()

cleanup() {
    local process_id

    trap - EXIT INT TERM
    for process_id in "${process_ids[@]}"; do
        kill -TERM "$process_id" 2>/dev/null || true
    done
    for process_id in "${process_ids[@]}"; do
        wait "$process_id" 2>/dev/null || true
    done
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'Upgrading the Katalog database.\n'
uv run kasana-katalog database upgrade

printf 'Starting Katalog and Kanvas. Press Ctrl+C to stop both.\n'

uv run kasana-katalog-api &
process_ids+=("$!")

uv run kasana-kanvas &
process_ids+=("$!")

if wait -n "${process_ids[@]}"; then
    exit 0
else
    exit "$?"
fi
