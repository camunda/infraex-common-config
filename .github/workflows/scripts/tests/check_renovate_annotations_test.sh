#!/bin/bash
# Exercises check_renovate_annotations.py against synthetic repositories.
#
# The checker's job is to be the thing that notices what Renovate never says out
# loud: an annotation that matches nothing updates nothing, and an annotation
# that matches twice reports the same update twice. Both are invisible in CI,
# which is precisely why the checker's own behaviour is pinned here.
#
# Run with the same bash major version as the runner. macOS ships bash 3.2,
# which accepts expansions that bash 4.4 and later reject outright, so a green
# run on 3.2 says nothing about CI.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${SCRIPT:-$HERE/../check_renovate_annotations.py}"
PRESET="${PRESET:-$HERE/../../../../default.json5}"
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

setup() { # setup, then `file <path>` and `preset <path>` build the repository
  REPO="$(mktemp -d)"
  USE_PRESET="$PRESET"
}

file() { # file <path>, contents on stdin
  mkdir -p "$REPO/$(dirname "$1")"
  cat >"$REPO/$1"
}

preset() { # preset <path>, use a preset other than the repository's own
  USE_PRESET="$1"
}

# Not a git repository unless a test asks for one: the checker falls back to
# walking the tree, and that fallback is what most of these exercise.
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

# A minimal preset whose two patterns both match `key: value`, standing in for the
# overlapping pairs the real preset used to carry.
OVERLAPPING_PRESET="$(mktemp)"
cat >"$OVERLAPPING_PRESET" <<'EOF'
{
  customManagers: [
    {
      customType: 'regex',
      managerFilePatterns: ['/\\.yml$/'],
      matchStrings: [
        'datasource=(?<datasource>\\S+) depName=(?<depName>\\S+)\\n[^\\n]*: (?<currentValue>\\S+)',
        'datasource=(?<datasource>\\S+) depName=(?<depName>\\S+)\\n[^\\n]*:[ \\t]*(?<currentValue>\\S+)',
      ],
    },
  ],
}
EOF

EMPTY_PRESET="$(mktemp)"
echo '{ customManagers: [] }' >"$EMPTY_PRESET"

trap 'rm -f "$OVERLAPPING_PRESET" "$EMPTY_PRESET"' EXIT

# 1. The documented shape: annotation, then the version on the next line.
echo "1. annotation followed by its version"
setup
file image.yml <<'EOF'
spec:
    # renovate: datasource=docker depName=camunda/keycloak versioning=regex:^quay-optimized-(?<major>\d+)$
    image: docker.io/camunda/keycloak:quay-optimized-26.7.0
EOF
run
expect_rc "1" 0
expect_out "1" "checked 1 Renovate annotation(s)"
expect_out "1" "1 extract exactly one dependency, 0 do not"

# 2. A comment between the annotation and the version. Renovate matches the
#    comment or nothing at all, so the dependency silently stops being updated —
#    the case that went unnoticed in camunda-deployment-references.
echo "2. annotation detached from its version by a comment"
setup
file detached.yml <<'EOF'
env:
    # renovate: datasource=github-releases depName=camunda/camunda
    # TODO: pinned until the next release
    CAMUNDA_VERSION: 8.10.0
EOF
run
expect_rc "2" 1
expect_out "2" "extracts nothing"
expect_out "2" "detached.yml:2"
expect_out "2" "0 extract exactly one dependency, 1 do not"

# 3. Two patterns matching the same line: one dependency reported several times,
#    which is what #517 saw on the Dependency Dashboard.
echo "3. overlapping patterns extract the same dependency twice"
setup
preset "$OVERLAPPING_PRESET"
file dup.yml <<'EOF'
# renovate: datasource=docker depName=camunda/keycloak
version: 26.7.0
EOF
run
expect_rc "3" 1
expect_out "3" "extracts 2 dependencies instead of one"

# 4. A file the manager does not read cannot hold a broken annotation.
echo "4. annotation in a file outside managerFilePatterns"
setup
file docs/renovate.md <<'EOF'
Annotate with `# renovate: datasource=docker depName=camunda/keycloak`, then the version.
EOF
run
expect_rc "4" 0
expect_out "4" "checked 0 Renovate annotation(s)"

# 5. Prose that mentions a datasource without naming a dependency is not an
#    annotation, and must not be reported as a broken one.
echo "5. datasource mentioned without depName"
setup
file mention.yml <<'EOF'
# The preset reads datasource=docker annotations; see the README.
image: docker.io/camunda/keycloak:quay-optimized-26.7.0
EOF
run
expect_rc "5" 0
expect_out "5" "checked 0 Renovate annotation(s)"

# 6. In a git repository, only tracked files are read: the workflow checks the
#    preset out inside the workspace, and that checkout is not the caller's code.
echo "6. untracked files are ignored"
setup
file tracked.yml <<'EOF'
# renovate: datasource=docker depName=camunda/keycloak
image: docker.io/camunda/keycloak:quay-optimized-26.7.0
EOF
as_git_repo
file untracked.yml <<'EOF'
# renovate: datasource=github-releases depName=camunda/camunda
# a comment where the version should be
CAMUNDA_VERSION: 8.10.0
EOF
run
expect_rc "6" 0
expect_out "6" "checked 1 Renovate annotation(s)"
expect_noout "6" "untracked.yml"

# 7. Several annotations in one file are counted, and reported, one by one.
echo "7. several annotations in one file"
setup
file many.yml <<'EOF'
# renovate: datasource=docker depName=camunda/keycloak
image: docker.io/camunda/keycloak:quay-optimized-26.7.0
# renovate: datasource=github-releases depName=camunda/camunda
# the version is not here
CAMUNDA_VERSION: 8.10.0
# renovate: datasource=helm depName=camunda-platform registryUrl=https://helm.camunda.io
CHART_VERSION="15.0.0"
EOF
run
expect_rc "7" 1
expect_out "7" "checked 3 Renovate annotation(s)"
expect_out "7" "2 extract exactly one dependency, 1 do not"
expect_out "7" "many.yml:3"

# 8. The annotation fields are read in any order, so neither order may be
#    reported as broken.
echo "8. registryUrl before and after versioning"
setup
file orders.sh <<'EOF'
# renovate: datasource=helm depName=camunda-platform registryUrl=https://helm.camunda.io versioning=regex:^15$
export A_VERSION="15.0.0"
# renovate: datasource=helm depName=camunda-platform versioning=regex:^15$ registryUrl=https://helm.camunda.io
export B_VERSION="15.0.0"
EOF
run
expect_rc "8" 0
expect_out "8" "2 extract exactly one dependency, 0 do not"

# 9. A preset with nothing to apply is a broken invocation, not a clean run.
echo "9. preset without custom managers"
setup
preset "$EMPTY_PRESET"
file image.yml <<'EOF'
# renovate: datasource=docker depName=camunda/keycloak
image: docker.io/camunda/keycloak:quay-optimized-26.7.0
EOF
run
expect_rc "9" 1
expect_out "9" "declares no regex customManagers"

echo
echo "passed=$PASS failed=$FAIL"
[[ "$FAIL" == "0" ]]
