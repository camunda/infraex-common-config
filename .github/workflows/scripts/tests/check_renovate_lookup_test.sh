#!/bin/bash
# Exercises check_renovate_lookup.py against synthetic Renovate reports.
#
# The checker's job is to fail on what Renovate reports without complaining
# about: a dependency it read and then dropped, because the value the annotation
# points at is not a version under the versioning it was given. Renovate calls
# that `skipReason: invalid-value` and moves on, so the annotation looks
# maintained and updates nothing.
#
# Reports are built here rather than produced by running Renovate: the shape this
# reads is a handful of fields, and a suite that needed a lookup would need the
# network, a token, and the registries to be up.
#
# The annotated files are written under fixtures/renovate-lookup with a
# `.fixture` suffix and copied into place, for the same reason the sibling suite
# does it: the preset reads `.sh` and `.yml`, so an inline annotation would be a
# live annotation of this repository.
#
# Run with the same bash major version as the runner. macOS ships bash 3.2,
# which accepts expansions that bash 4.4 and later reject outright, so a green
# run on 3.2 says nothing about CI.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${SCRIPT:-$HERE/../check_renovate_lookup.py}"
FIXTURES="$HERE/fixtures/renovate-lookup"
PYTHON="${PYTHON:-python3}"
PASS=0
FAIL=0

if ((BASH_VERSINFO[0] < 5)); then
  echo "warning: running on bash $BASH_VERSION; the runner is on 5.x and is stricter." >&2
fi

setup() {
  REPO="$(mktemp -d)"
  REPORT="$REPO/report.json"
  DEPS=""
}

file() { # file <fixture> <path in the repository under test>
  mkdir -p "$REPO/$(dirname "$2")"
  cp "$FIXTURES/$1" "$REPO/$2"
}

# dep <packageFile> <depName> <currentValue> <versioning> <skipReason|-> <replaceString>
#
# Collected as tab-separated records and encoded by python: the values carry the
# backslashes of a regex versioning, and quoting those into JSON from bash is a
# source of bugs the suite would then be testing instead of the checker.
dep() {
  DEPS="$DEPS$1	$2	$3	$4	$5	$6
"
}

report() {
  # Through a file rather than a pipe: the python program itself arrives on stdin.
  printf '%s' "$DEPS" >"$REPO/deps.tsv"
  "$PYTHON" - "$REPORT" "$REPO/deps.tsv" <<'PY'
import json, sys

files = {}
for line in open(sys.argv[2], encoding="utf-8").read().splitlines():
    if not line:
        continue
    package_file, dep_name, current, versioning, skip, replace = line.split("\t")
    files.setdefault(package_file, []).append(
        {
            "depName": dep_name,
            "currentValue": current,
            "versioning": versioning,
            "skipReason": None if skip == "-" else skip,
            "replaceString": replace,
        }
    )
json.dump(
    {
        "repositories": {
            "local": {
                "packageFiles": {
                    "regex": [
                        {"packageFile": name, "deps": deps} for name, deps in files.items()
                    ]
                }
            }
        }
    },
    open(sys.argv[1], "w"),
)
PY
}

run() {
  report
  OUT="$("$PYTHON" "$SCRIPT" --report "$REPORT" --root "$REPO" 2>&1)"
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

ANNOTATION='datasource=helm depName=camunda-platform versioning=regex:^15(\.(?<minor>\d+))?$'
MARKED="$ANNOTATION renovate-inert-ok parked until 8.10 GA"

# 1. A dependency Renovate could read and resolve is not a finding.
echo "1. usable dependency"
setup
file chart-env.sh.fixture chart-env.sh
dep chart-env.sh camunda-platform 15.2.0 semver - "$ANNOTATION"
run
expect_rc "1" 0
expect_out "1" "1 usable, 0 parked on purpose, 0 unusable"

# 2. The case the check exists for: read, then dropped, and silent in Renovate.
echo "2. dependency dropped as invalid-value"
setup
file chart-env.sh.fixture chart-env.sh
dep chart-env.sh camunda-platform 15-dev-latest 'regex:^15$' invalid-value "$ANNOTATION"
run
expect_rc "2" 1
expect_out "2" "0 usable, 0 parked on purpose, 1 unusable"
expect_out "2" "is not a version under"

# 3. A pin parked on a development version says so on the annotation itself.
echo "3. exemption marker on the annotation"
setup
file chart-env-marked.sh.fixture chart-env.sh
dep chart-env.sh camunda-platform 15-dev-latest 'regex:^15$' invalid-value "$MARKED"
run
expect_rc "3" 0
expect_out "3" "0 usable, 1 parked on purpose, 0 unusable"

# 4. A marker excuses the annotation carrying it and no other, even though the
#    unmarked one is a prefix of it.
echo "4. marked and unmarked annotation in the same file"
setup
file chart-env-both.sh.fixture chart-env.sh
dep chart-env.sh camunda-platform 15-dev-latest 'regex:^15$' invalid-value "$MARKED"
dep chart-env.sh camunda-platform 15-dev-latest 'regex:^15$' invalid-value "$ANNOTATION"
run
expect_rc "4" 1
expect_out "4" "0 usable, 1 parked on purpose, 1 unusable"
expect_out "4" "chart-env.sh:5"
expect_noout "4" "chart-env.sh:2"

# 5. Skip reasons that are not about the value -- a datasource that failed, a
#    dependency disabled on purpose -- must not turn the job red.
echo "5. unrelated skip reason"
setup
file chart-env.sh.fixture chart-env.sh
dep chart-env.sh camunda-platform 15.2.0 semver disabled "$ANNOTATION"
run
expect_rc "5" 0
expect_out "5" "1 usable, 0 parked on purpose, 0 unusable"

# 6. A finding whose annotation is no longer in the file is still reported,
#    without a line number, rather than swallowed.
echo "6. annotation not found in the working tree"
setup
dep gone.sh camunda-platform 15-dev-latest 'regex:^15$' invalid-value "$ANNOTATION"
run
expect_rc "6" 1
expect_out "6" "gone.sh:?"

# 7. Renovate writes the report only when it completes, so a missing one is a
#    broken run and not an empty result.
echo "7. missing report"
setup
OUT="$("$PYTHON" "$SCRIPT" --report "$REPO/absent.json" --root "$REPO" 2>&1)"
RC=$?
expect_rc "7" 1
expect_out "7" "does not exist"

echo
echo "passed=$PASS failed=$FAIL"
[[ "$FAIL" == "0" ]]
