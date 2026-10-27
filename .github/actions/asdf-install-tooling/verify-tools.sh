#!/usr/bin/env bash
# Print every "<tool> <version>" pinned in the comma-separated INPUTS_TOOL_VERSIONS_FILES
# that asdf does not report as installed. Exit 1 when any is missing.
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
  while read -r tool versions; do
    # Strip comments; `system` and `path:` pins are not managed by asdf install.
    versions="${versions%%#*}"
    [ -n "$tool" ] && [ "${tool:0:1}" != '#' ] || continue
    for version in $versions; do
      case "$version" in system | path:*) continue ;; esac
      if ! (cd "$(dirname "$file")" && asdf where "$tool" "$version" > /dev/null 2>&1); then
        echo "${tool} ${version}"
        missing+=("${tool} ${version}")
      fi
    done
  done < "$file"
done

[ "${#missing[@]}" -eq 0 ]
