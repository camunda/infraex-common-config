#!/usr/bin/env bash
# Run from any directory with bash .github/actions/asdf-install-tooling/test.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
mkdir -p "$ROOT/debug"
TEST_DIR="$(mktemp -d "$ROOT/debug/asdf-retry.XXXXXX")"
trap 'rm -rf "$TEST_DIR"' EXIT
export TEST_DIR

# Exercise the actual action step, not a copy of the retry loop.
awk '
  /id: tools-plugins-install/ { step = 1 }
  step && /run: \|/ { script = 1; next }
  script && /^          env:/ { exit }
  script { sub(/^              /, ""); print }
' "$HERE/action.yml" > "$TEST_DIR/install.sh"
printf 'golang 1.27.2\n' > "$TEST_DIR/.tool-versions"

asdf() {
  if [ "$1" != install ]; then
    [ "$*" != 'plugin list' ] || echo golang
    return 0
  fi
  local attempt
  attempt=$(($(wc -l < "$TEST_DIR/attempts") + 1))
  echo "$attempt" >> "$TEST_DIR/attempts"
  if [ "$TEST_MODE" = hang ]; then
    trap '' TERM
    while :; do command sleep 1; done
  fi
  if [ "$attempt" -lt "$SUCCESS_ON" ]; then
    echo 'failed to run download callback: exit status 56' >&2
    return 56
  fi
}
timeout() {
  [ "$*" = '--kill-after=30s 15m asdf install' ] || return 99
  echo "$*" >> "$TEST_DIR/timeouts"
  if [ "$TEST_MODE" = hang ]; then
    # Real process-group termination, with shorter durations only for the test.
    command timeout --kill-after=0.1s 0.1s bash -c 'asdf install'
  else
    asdf install
  fi
}
sleep() { echo "$1" >> "$TEST_DIR/delays"; }
export -f asdf timeout sleep

check() {
  local name="$1" expected_status="$2" expected_attempts="$3" expected_delays="$4" status=0
  : > "$TEST_DIR/attempts"
  : > "$TEST_DIR/timeouts"
  : > "$TEST_DIR/delays"
  INPUTS_TOOL_VERSIONS_FILES="$TEST_DIR/.tool-versions" bash "$TEST_DIR/install.sh" > "$TEST_DIR/output" 2>&1 || status=$?
  [ "$status" -eq "$expected_status" ]
  [ "$(wc -l < "$TEST_DIR/attempts" | tr -d ' ')" -eq "$expected_attempts" ]
  [ "$(wc -l < "$TEST_DIR/timeouts" | tr -d ' ')" -eq "$expected_attempts" ]
  [ "$(tr '\n' ' ' < "$TEST_DIR/delays")" = "$expected_delays" ]
  if [ "$expected_status" -ne 0 ]; then
    grep -q '::error::asdf install failed or timed out on three attempts' "$TEST_DIR/output"
    if grep -q '^+ cd -' "$TEST_DIR/output"; then
      echo 'FAIL: continued after the final install failure' >&2
      exit 1
    fi
  fi
  if [ "$TEST_MODE" = normal ] && [ "$SUCCESS_ON" -gt 1 ]; then
    grep -q 'failed to run download callback: exit status 56' "$TEST_DIR/output"
  fi
  echo "PASS: $name (exit $status, $expected_attempts attempts)"
}

export TEST_MODE=normal SUCCESS_ON=1
check 'first-attempt success' 0 1 ''
export SUCCESS_ON=2
check 'one transient failure' 0 2 '10 '
export SUCCESS_ON=3
check 'two transient failures' 0 3 '10 30 '
export SUCCESS_ON=4
check 'persistent failure preserves error and exit status' 56 3 '10 30 '
export TEST_MODE=hang
check 'stalled process ignoring TERM is killed and retries stay bounded' 137 3 '10 30 '

# The verify-cache step: a restored cache that lacks a pinned version is not complete.
awk '
  /id: verify-cache/ { step = 1 }
  step && /run: \|/ { script = 1; next }
  script && /^          env:/ { exit }
  script { sub(/^              /, ""); print }
' "$HERE/action.yml" > "$TEST_DIR/verify.sh"
mkdir -p "$TEST_DIR/data/installs/python/3.14.8" "$TEST_DIR/data/installs/golang/1.27.2"
printf '# pins\npython 3.14.9\ngolang 1.27.2\n' > "$TEST_DIR/verify.tool-versions"
verify() {
  : > "$TEST_DIR/verify.out"
  ASDF_DATA_DIR="$TEST_DIR/data" GITHUB_OUTPUT="$TEST_DIR/verify.out" \
    INPUTS_TOOL_VERSIONS_FILES="$TEST_DIR/verify.tool-versions" bash "$TEST_DIR/verify.sh" >/dev/null
  cat "$TEST_DIR/verify.out"
}
[ "$(verify)" = 'complete=false' ] || { echo 'FAIL: missing python 3.14.9 not detected' >&2; exit 1; }
echo 'PASS: verify-cache detects a missing pinned version'
mkdir -p "$TEST_DIR/data/installs/python/3.14.9"
[ "$(verify)" = 'complete=true' ] || { echo 'FAIL: complete cache reported incomplete' >&2; exit 1; }
echo 'PASS: verify-cache accepts a complete cache'
printf 'python 3.14.9 3.14.7 # secondary pin\nnodejs system\nkubectl path:/opt/kubectl\nlua ref:main\n' > "$TEST_DIR/verify.tool-versions"
mkdir -p "$TEST_DIR/data/installs/lua/ref-main"
[ "$(verify)" = 'complete=false' ] || { echo 'FAIL: missing secondary pin 3.14.7 not detected' >&2; exit 1; }
echo 'PASS: verify-cache checks every version of a line'
mkdir -p "$TEST_DIR/data/installs/python/3.14.7"
[ "$(verify)" = 'complete=true' ] || { echo 'FAIL: system, path: or ref: selector reported missing' >&2; exit 1; }
echo 'PASS: verify-cache maps ref: and skips system and path:'
