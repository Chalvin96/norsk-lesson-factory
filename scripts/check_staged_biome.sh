#!/usr/bin/env bash
set -euo pipefail

files=()
for path in "$@"; do
  case "$path" in
    workbench/*) files+=("${path#workbench/}") ;;
    *) printf 'Unexpected staged path for Biome: %s\n' "$path" >&2; exit 2 ;;
  esac
done

if ((${#files[@]} == 0)); then
  exit 0
fi

if [[ ! -x workbench/node_modules/.bin/biome ]]; then
  printf 'Biome is unavailable; run npm --prefix workbench ci first.\n' >&2
  exit 1
fi

cd workbench
exec ./node_modules/.bin/biome check --no-errors-on-unmatched "${files[@]}"
