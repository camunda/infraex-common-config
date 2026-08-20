#!/bin/bash
# Exercises check_renovate_annotations.py against synthetic repositories.
#
# The checker's job is to say out loud what Renovate never does: an annotation
# that matches nothing updates nothing, and an annotation that matches twice
# reports the same update twice. Both are invisible in CI, which is exactly why
# the checker's own behaviour is pinned here.
#
# The fixtures are files under fixtures/renovate-annotations rather than heredocs
# in this script: the preset reads `.sh`, so an inline fixture would be a live
# annotation of this repository -- extracted by Renovate, reported by the checker,
# and the suite would fail the repository it is testing.
#
# Run with the same bash major version as the runner. macOS ships bash 3.2,
# which accepts expansions that bash 4.4 and later reject outright, so a green
# run on 3.2 says nothing about CI.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${SCRIPT:-$HERE/../check_renovate_annotations.py}"
PRESET="${PRESET:-$HERE/../../../../default.json5}"
FIXTURES="$HERE/fixtures/renovate-annotations"
PYTHON="${PYTHON:-python3}"
PASS=0
FAIL=0

if ((BASH_VERSINFO[0] < 5)); then
  echo "warning: running on bash $BASH_VERSION; the runner is on 5.x and is stricter." >&2
fi

if ! "$PYTHON" -c 'import json5' 2>/dev/null; then
  echo "error: $PYTHON cannot import json5, which the checker needs. pip install json5" >&2
  exit 1
fi

setup() {
  REPO="$(mktemp -d)"
  USE_PRESET="$PRESET"
}

file() { # file <fixture> <path in the repository under test>
  mkdir -p "$REPO/$(dirname "$2")"
  cp "$FIXTURES/$1" "$REPO/$2"
}

preset() { # preset <fixture>, to use something other than the repository's own preset
  USE_PRESET="$FIXTURES/$1"
}

# Not a git repository unless a test asks for one: the checker falls back to
# walking the tree, which is what most of these exercise.
as_git_repo() {
  git -C "$REPO" init -q
  git -C "$REPO" add -A
}

run() {
  OUT="$("$PYTHON" "$SCRIPT" --preset "$USE_PRESET" --root "$REPO" 2>&1)"
  RC=$?
}

check() { # check <scenario> <what was expected> <result>
  if [[ "$3" == "0" ]]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: $1 — $2"
    echo "--- output (rc=$RC) ---"
    echo "$OUT"
    echo "-----------------------"
  fi
}

# Every helper captures the test result into `rc` before building the message.
# Arguments are expanded before the command runs, so a command substitution in
# the message would overwrite $? with its own status and the assertion would
# pass unconditionally.
expect_rc() {
  local rc
  [[ "$RC" == "$2" ]]
  rc=$?
  check "$1" "rc=$2, got rc=$RC" "$rc"
}
expect_out() {
  local rc
  grep -qF "$2" <<<"$OUT"
  rc=$?
  check "$1" "output to contain: $2" "$rc"
}
expect_noout() {
  local rc
  ! grep -qF "$2" <<<"$OUT"
  rc=$?
  check "$1" "output NOT to contain: $2" "$rc"
}

# 1. The documented shape: annotation, then the version on the next line.
echo "1. annotation followed by its version"
setup
file annotated-image.yml.fixture image.yml
run
expect_rc "1" 0
expect_out "1" "checked 1 Renovate annotation(s)"
expect_out "1" "1 sound, 0 broken, 0 extracting"

# 2. A comment between the annotation and the version. The annotation reads the
#    comment or nothing at all, and the dependency below silently stops being
#    updated -- the case that went unnoticed in camunda-deployment-references.
echo "2. annotation detached from its version by a comment"
setup
file detached-by-comment.yml.fixture detached.yml
run
expect_rc "2" 1
expect_out "2" "extracts nothing"
expect_out "2" "detached.yml:2"
expect_out "2" "0 sound, 1 broken, 0 extracting"

# 3. Two patterns matching the same line: one dependency reported several times,
#    which is what #517 saw on the Dependency Dashboard.
echo "3. overlapping patterns extract the same dependency twice"
setup
preset overlapping-preset.json5
file plain-version.yml.fixture dup.yml
run
expect_rc "3" 1
expect_out "3" "extracts 2 dependencies instead of one"

# 4. A file the manager does not read cannot hold a broken annotation.
echo "4. annotation in a file outside managerFilePatterns"
setup
file documentation.md.fixture docs/renovate.md
run
expect_rc "4" 0
expect_out "4" "checked 0 Renovate annotation(s)"

# 5. Prose mentioning a datasource without naming a dependency is not an
#    annotation, and must not be reported as a broken one.
echo "5. datasource mentioned without depName"
setup
file datasource-without-depname.yml.fixture mention.yml
run
expect_rc "5" 0
expect_out "5" "checked 0 Renovate annotation(s)"

# 6. In a git repository only tracked files are read: the workflow checks the
#    preset out inside the workspace, and that checkout is not the caller's code.
echo "6. untracked files are ignored"
setup
file annotated-image.yml.fixture tracked.yml
as_git_repo
file detached-by-comment.yml.fixture untracked.yml
run
expect_rc "6" 0
expect_out "6" "checked 1 Renovate annotation(s)"
expect_noout "6" "untracked.yml"

# 7. Several annotations in one file are counted, and reported, one by one.
echo "7. several annotations in one file"
setup
file several-annotations.yml.fixture many.yml
run
expect_rc "7" 1
expect_out "7" "checked 3 Renovate annotation(s)"
expect_out "7" "2 sound, 1 broken, 0 extracting"
expect_out "7" "many.yml:3"

# 8. The annotation fields are read in any order, so neither order may be
#    reported as broken.
echo "8. registryUrl before and after versioning"
setup
file field-orders.sh.fixture orders.sh
run
expect_rc "8" 0
expect_out "8" "2 sound, 0 broken, 0 extracting"

# 9. A preset with nothing to apply is a broken invocation, not a clean run.
echo "9. preset without custom managers"
setup
preset empty-preset.json5
file annotated-image.yml.fixture image.yml
run
expect_rc "9" 1
expect_out "9" "declares no regex customManagers"

# 10. A version its own regex versioning cannot match is extracted and then dropped by
#     Renovate as invalid, so the dependency never updates. Reported, but as a warning:
#     a chart pinned to a dev tag before a release is a deliberate, temporary state.
echo "10. version rejected by its own versioning regex"
setup
file versioning-mismatch.sh.fixture chart-env.sh
run
expect_rc "10" 0
expect_out "10" "does not match"
expect_out "10" "15-dev-latest"
expect_out "10" "0 sound, 0 broken, 1 extracting"

# 10b. The same annotation, declared parked with the marker `check_renovate_lookup.py`
#      reads. Both checks look at that state from two sides, so they have to agree on how
#      it is declared -- and a warning repeated on every run is how a check stops being
#      read.
echo "10b. version rejected by its own versioning regex, declared parked"
setup
file versioning-mismatch-parked.sh.fixture chart-env.sh
run
expect_rc "10b" 0
expect_noout "10b" "does not match"
expect_out "10b" "0 sound, 0 broken, 0 extracting"
expect_out "10b" "1 parked on purpose"

# 11. A versioning regex that does not compile is reported the same way, rather than
#     taking the checker down with it.
echo "11. versioning regex that does not compile"
setup
file versioning-uncompilable.sh.fixture chart-env.sh
run
expect_rc "11" 0
expect_out "11" "does not compile"

# 12. semver, loose, docker and the rest are Renovate implementations. Guessing at them
#     here would invent failures, so a non-regex versioning is left alone.
echo "12. non-regex versioning is not second-guessed"
setup
file versioning-not-regex.sh.fixture chart-env.sh
run
expect_rc "12" 0
expect_out "12" "1 sound, 0 broken, 0 extracting"

# 13. A shell default inside the quotes. The `:` of `${NAME:-...}` is not an image tag
#     separator, and reading it as one gave package `${CHART_VERSION` at version
#     `-14.0.0}` -- extracted, then dropped by Renovate as invalid, so the pin never
#     moved. Guards the preset, not just the checker.
echo "13. shell default expansion inside the quotes"
setup
file shell-default-expansion.sh.fixture chart-env.sh
run
expect_rc "13" 0
expect_out "13" "1 sound, 0 broken, 0 extracting"

echo
echo "passed=$PASS failed=$FAIL"
[[ "$FAIL" == "0" ]]
