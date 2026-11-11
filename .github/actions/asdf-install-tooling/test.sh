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

# shellcheck disable=SC2329 # install.sh calls it through export -f
asdf() {
  if [ "$*" = 'plugin update --all' ]; then
    local update_attempt
    update_attempt=$(($(wc -l < "$TEST_DIR/update_attempts") + 1))
    echo "$update_attempt" >> "$TEST_DIR/update_attempts"
    if [ "$update_attempt" -lt "$UPDATE_SUCCESS_ON" ]; then
      echo "fatal: unable to access 'https://github.com/asdf-community/asdf-golang.git/': Could not resolve host: github.com" >&2
      return 128
    fi
    return 0
  fi
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
# shellcheck disable=SC2329 # install.sh calls it through export -f
timeout() {
  if [ "$*" = '--kill-after=30s 5m asdf plugin update --all' ]; then
    echo "$*" >> "$TEST_DIR/update_timeouts"
    asdf plugin update --all
    return
  fi
  [ "$*" = '--kill-after=30s 15m asdf install' ] || return 99
  echo "$*" >> "$TEST_DIR/timeouts"
  if [ "$TEST_MODE" = hang ]; then
    # Real process-group termination, with shorter durations only for the test.
    command timeout --kill-after=0.1s 0.1s bash -c 'asdf install'
  else
    asdf install
  fi
}
# shellcheck disable=SC2329 # install.sh calls it through export -f
sleep() { echo "$1" >> "$TEST_DIR/delays"; }
export -f asdf timeout sleep

check() {
  local name="$1" expected_status="$2" expected_attempts="$3" expected_delays="$4" status=0
  : > "$TEST_DIR/attempts"
  : > "$TEST_DIR/timeouts"
  : > "$TEST_DIR/delays"
  : > "$TEST_DIR/update_attempts"
  : > "$TEST_DIR/update_timeouts"
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

export TEST_MODE=normal SUCCESS_ON=1 UPDATE_SUCCESS_ON=1
check 'first-attempt success' 0 1 ''
[ "$(wc -l < "$TEST_DIR/update_attempts" | tr -d ' ')" -eq 1 ]
[ "$(wc -l < "$TEST_DIR/update_timeouts" | tr -d ' ')" -eq 1 ]

# asdf plugin update --all: retried and bounded like asdf install.
check_update() {
  local name="$1" expected_status="$2" expected_updates="$3" expected_delays="$4" expected_installs="$5" status=0
  : > "$TEST_DIR/attempts"
  : > "$TEST_DIR/timeouts"
  : > "$TEST_DIR/delays"
  : > "$TEST_DIR/update_attempts"
  : > "$TEST_DIR/update_timeouts"
  INPUTS_TOOL_VERSIONS_FILES="$TEST_DIR/.tool-versions" bash "$TEST_DIR/install.sh" > "$TEST_DIR/output" 2>&1 || status=$?
  [ "$status" -eq "$expected_status" ]
  [ "$(wc -l < "$TEST_DIR/update_attempts" | tr -d ' ')" -eq "$expected_updates" ]
  [ "$(wc -l < "$TEST_DIR/update_timeouts" | tr -d ' ')" -eq "$expected_updates" ]
  [ "$(wc -l < "$TEST_DIR/attempts" | tr -d ' ')" -eq "$expected_installs" ]
  [ "$(tr '\n' ' ' < "$TEST_DIR/delays")" = "$expected_delays" ]
  if [ "$expected_status" -ne 0 ]; then
    grep -q '::error::asdf plugin update failed or timed out on three attempts' "$TEST_DIR/output"
  else
    grep -q '::warning::asdf plugin update failed or timed out (attempt 1/3' "$TEST_DIR/output"
  fi
  echo "PASS: $name (exit $status, $expected_updates update attempts)"
}
export UPDATE_SUCCESS_ON=2
check_update 'plugin update: one transient failure' 0 2 '10 ' 1
export UPDATE_SUCCESS_ON=3
check_update 'plugin update: two transient failures' 0 3 '10 30 ' 1
export UPDATE_SUCCESS_ON=4
check_update 'plugin update: persistent failure stops before install' 128 3 '10 30 ' 0
export UPDATE_SUCCESS_ON=1
export SUCCESS_ON=2
check 'one transient failure' 0 2 '10 '
export SUCCESS_ON=3
check 'two transient failures' 0 3 '10 30 '
export SUCCESS_ON=4
check 'persistent failure preserves error and exit status' 56 3 '10 30 '
export TEST_MODE=hang
check 'stalled process ignoring TERM is killed and retries stay bounded' 137 3 '10 30 '

# verify-tools.sh: a restored toolchain missing one pinned version must be reported.
unset -f asdf timeout sleep
mkdir -p "$TEST_DIR/bin" "$TEST_DIR/data/installs/python/3.14.8"
cat > "$TEST_DIR/bin/asdf" <<'FAKE'
#!/usr/bin/env bash
[ "$1" = where ] && [ -d "$TEST_DIR/data/installs/$2/$3" ]
FAKE
chmod +x "$TEST_DIR/bin/asdf"
printf '# comment\npython 3.14.9\ngolang system\n' > "$TEST_DIR/.tool-versions"
verify() { PATH="$TEST_DIR/bin:$PATH" HOME="$TEST_DIR/home" INPUTS_TOOL_VERSIONS_FILES="$TEST_DIR/.tool-versions" bash "$HERE/verify-tools.sh"; }
out=$(verify) && { echo 'FAIL: missing python 3.14.9 not detected' >&2; exit 1; }
[ "$out" = 'python 3.14.9' ]
mkdir -p "$TEST_DIR/data/installs/python/3.14.9"
verify > /dev/null
echo 'PASS: verify-tools reports missing pinned versions and accepts a complete toolchain'
