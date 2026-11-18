#!/usr/bin/env bash
# Print every "<tool> <version>" pinned in the comma-separated INPUTS_TOOL_VERSIONS_FILES
# that asdf does not report as installed, or whose Python cannot import ssl. Exit 1 when any is
# missing. With PURGE_BROKEN=1, a Python without ssl is deleted so `asdf install` rebuilds it
# (asdf skips versions whose directory exists).
set -uo pipefail

export PATH="$HOME/.local/bin:$PATH"
missing=()

if ! command -v asdf > /dev/null 2>&1; then
  echo "asdf binary"
  exit 1
fi

IFS=',' read -r -a files <<< "${INPUTS_TOOL_VERSIONS_FILES}"
for file in "${files[@]}"; do
  [ -f "$file" ] || { echo "${file} (file not found)"; missing+=("$file"); continue; }
  dir="$(dirname "$file")"
  while read -r tool versions; do
    # Strip comments; `system` and `path:` pins are not managed by asdf install.
    versions="${versions%%#*}"
    [ -n "$tool" ] && [ "${tool:0:1}" != '#' ] || continue
    for version in $versions; do
      case "$version" in system | path:*) continue ;; esac
      if ! where=$(cd "$dir" && asdf where "$tool" "$version" 2> /dev/null); then
        echo "${tool} ${version}"
        missing+=("${tool} ${version}")
      elif [ "$tool" = python ] && ! "$where/bin/python" -c 'import ssl' > /dev/null 2>&1; then
        # A CPython built without libssl-dev, or restored where libssl is absent, has no
        # ssl module and fails later with `unknown url type: https`.
        echo "${tool} ${version} (no ssl module)"
        missing+=("${tool} ${version}")
        [ "${PURGE_BROKEN:-}" != 1 ] || rm -rf "$where"
      fi
    done
  done < "$file"
done

[ "${#missing[@]}" -eq 0 ]
