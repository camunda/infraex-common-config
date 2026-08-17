#!/bin/bash
# Exercises azure_rg_cleanup.sh against a stubbed Azure CLI.
#
# The sweep runs on pull requests with DRY_RUN=true, which means CI never
# deletes anything, which means the deletion and verification paths cannot be
# covered there. They are covered here instead.
#
# Run with the same bash major version as the runner. macOS ships bash 3.2,
# which accepts expansions that bash 4.4 and later reject outright, so a green
# run on 3.2 says nothing about CI.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${SCRIPT:-$HERE/../azure_rg_cleanup.sh}"
STUB_DIR="$HERE/stub"
PASS=0
FAIL=0

if ((BASH_VERSINFO[0] < 5)); then
  echo "warning: running on bash $BASH_VERSION; the runner is on 5.x and is stricter." >&2
fi

setup() {
  STATE="$(mktemp -d)"
  export AZ_STUB_STATE="$STATE"
  mkdir -p "$STATE/created" "$STATE/locks"
  : >"$STATE/groups"
  : >"$STATE/log"
  : >"$STATE/undeletable"
}

group() { # group <name> [created-iso]
  echo "$1" >>"$STATE/groups"
  if [[ $# -gt 1 ]]; then echo "$2" >"$STATE/created/$1"; fi
}

aged() { # aged <hours-ago>, mirroring the date command the sweep itself picks
  if [[ "$(uname)" == "Darwin" ]]; then
    gdate -u -d "$1 hours ago" +%Y-%m-%dT%H:%M:%SZ
  else
    date -u -d "$1 hours ago" +%Y-%m-%dT%H:%M:%SZ
  fi
}

wedge() { echo "$1" >>"$STATE/undeletable"; }

run() {
  local start=$SECONDS
  OUT="$(PATH="$STUB_DIR:$PATH" AZURE_REGION=testregion "$SCRIPT" 2>&1)"
  RC=$?
  DURATION=$((SECONDS - start))
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
expect_log() {
  local rc actual
  actual="$(cat "$STATE/log")"
  [[ "$actual" == "$2" ]]
  rc=$?
  check "$1" "az calls [$2], got [$actual]" "$rc"
}
expect_faster_than() {
  local rc
  [[ "$DURATION" -lt "$2" ]]
  rc=$?
  check "$1" "run to take less than ${2}s, took ${DURATION}s" "$rc"
}
# The failure report must state how long the sweep actually waited, not the
# timeout it was configured with. Allow a second of slack for slow polls.
expect_reported_elapsed() {
  local rc reported drift
  reported="$(sed -n 's/.*still present after \([0-9]*\)s.*/\1/p' <<<"$OUT")"
  if [[ -z "$reported" ]]; then
    check "$1" "a reported wait in the failure output, found none" 1
    return
  fi
  drift=$((reported > DURATION ? reported - DURATION : DURATION - reported))
  [[ "$drift" -le 1 ]]
  rc=$?
  check "$1" "reported wait to match the measured ${DURATION}s, got ${reported}s" "$rc"
}

echo "bash $BASH_VERSION"
echo

# 1. Nothing eligible: the age filter rejects every group, so no deletion is
#    ever initiated and the verification pass must be skipped rather than
#    tripping over an empty array.
echo "1. nothing eligible"
setup
group hci-new-rg "$(aged 1)"
CLEANUP_OLDER_THAN=12h run
expect_rc "1" 0
expect_out "1" "oldest resource is too new"
expect_log "1" ""

# 2. Dry run: groups are eligible but nothing is deleted, so the verification
#    pass is skipped for the same reason. This is the shape every PR runs in.
echo "2. dry run"
setup
group hci-old-rg "$(aged 20)"
group MC_hci-old-rg_hci-old-aks_testregion "$(aged 20)"
DRY_RUN=true CLEANUP_OLDER_THAN=12h run
expect_rc "2" 0
expect_out "2" "Would delete RG: hci-old-rg"
expect_out "2" "Skipping MC_hci-old-rg_hci-old-aks_testregion: node resource group"
expect_noout "2" "Would delete RG: MC_"
expect_log "2" ""

# 3. Parent and its node group are both eligible: only the parent is deleted,
#    and the sweep waits for it to actually disappear.
echo "3. parent and its node group"
setup
group hci-old-rg "$(aged 20)"
group MC_hci-old-rg_hci-old-aks_testregion "$(aged 20)"
CLEANUP_OLDER_THAN=12h run
expect_rc "3" 0
expect_log "3" "group delete hci-old-rg"
expect_out "3" "All 1 resource group(s) deleted."

# 4. A node group whose parent is not in the run is genuinely orphaned: nothing
#    else will reclaim it, so the sweep must.
echo "4. orphaned node group"
setup
group MC_hci-gone-rg_hci-gone-aks_testregion "$(aged 20)"
CLEANUP_OLDER_THAN=12h run
expect_rc "4" 0
expect_out "4" "Keeping MC_hci-gone-rg_hci-gone-aks_testregion: node resource group whose parent is not being deleted"
expect_log "4" "group delete MC_hci-gone-rg_hci-gone-aks_testregion"

# 5. A parent name containing underscores must still match its node group,
#    which is why the match is a prefix test and not a split on "_".
echo "5. underscore in the parent name"
setup
group hci_odd_name-rg "$(aged 20)"
group MC_hci_odd_name-rg_hci-odd-aks_testregion "$(aged 20)"
CLEANUP_OLDER_THAN=12h run
expect_rc "5" 0
expect_log "5" "group delete hci_odd_name-rg"

# 6. Deletion accepted but never completing is the case the whole verification
#    pass exists for: the group keeps billing, so the job must go red.
echo "6. deletion that never completes"
setup
group hci-wedged-rg "$(aged 20)"
wedge hci-wedged-rg
CLEANUP_OLDER_THAN=12h DELETION_TIMEOUT=0 run
expect_rc "6" 1
expect_out "6" "Resource groups still present after"
expect_out "6" "hci-wedged-rg"
expect_out "6" "they are still billing"

# 7. Without an age filter every unprotected group is eligible.
echo "7. no age filter"
setup
group hci-a-rg
group hci-b-rg
run
expect_rc "7" 0
expect_out "7" "All 2 resource group(s) deleted."

# 8. Protected groups are never touched.
echo "8. protected groups"
setup
group NetworkWatcherRG
group rg-infraex-global-permanent
run
expect_rc "8" 0
expect_out "8" "Skipping NetworkWatcherRG: protected"
expect_log "8" ""

# 9. Locks are removed before the group they block.
echo "9. locks"
setup
group hci-locked-rg
echo "/subscriptions/x/locks/keep" >"$STATE/locks/hci-locked-rg"
run
expect_rc "9" 0
expect_log "9" "lock delete /subscriptions/x/locks/keep
group delete hci-locked-rg"

# 10. A bad DELETION_TIMEOUT must be rejected before anything is deleted. It is
#     read long before it is used, and validating it late means failing with an
#     arithmetic error after the groups are already gone.
echo "10. invalid DELETION_TIMEOUT"
setup
group hci-a-rg
DELETION_TIMEOUT=abc run
expect_rc "10" 1
expect_out "10" "Invalid DELETION_TIMEOUT: abc"
expect_log "10" ""

# 11. A short timeout must be honoured. The poll interval is 30s, so a loop
#     that sleeps a flat interval before rechecking the deadline overshoots a
#     2s budget fifteen times over and then reports the budget, not the wait.
echo "11. short timeout is not overshot"
setup
group hci-wedged-rg
wedge hci-wedged-rg
DELETION_TIMEOUT=2 run
expect_rc "11" 1
expect_faster_than "11" 15
expect_reported_elapsed "11"

echo
echo "passed=$PASS failed=$FAIL"
[[ "$FAIL" == "0" ]]
