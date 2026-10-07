#!/bin/bash
# Exercises aws_global_db_teardown.sh against a stubbed AWS CLI.
#
# A global database whose secondary never leaves (an Aurora replica stuck in
# `creating` accepts remove-from-global-cluster and stays a member) must fail
# the job, so notify-on-failure reports it instead of a green nightly run.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../aws_global_db_teardown.sh"
PASS=0
FAIL=0

run() { # run <detaches: true|false>; prints the output, returns the exit code
  local stub
  stub="$(mktemp -d)"
  cat >"$stub/aws" <<STUB
#!/bin/bash
case "\$*" in
  *remove-from-global-cluster*) touch "$stub/detached"; exit 0 ;;
  *delete-global-cluster*) echo deleted >&2; exit 0 ;;
  *"length(GlobalClusters[0].GlobalClusterMembers[?"*|*"length(GlobalClusters[0].GlobalClusterMembers)"*)
    if [ "$1" = true ] && [ -f "$stub/detached" ]; then echo 0; else echo 2; fi ;;
  *"--global-cluster-identifier"*)
    printf 'arn:aws:rds:eu-west-2:1:cluster:r0\tTrue\narn:aws:rds:eu-west-3:1:cluster:r1\tFalse\n' ;;
  *describe-global-clusters*) echo e2e-x-global-db ;;
esac
STUB
  chmod +x "$stub/aws"
  PATH="$stub:$PATH" CLEANUP_REGIONS="eu-west-2 eu-west-3" GLOBAL_DB_POLL_SECONDS=0 \
    bash "$SCRIPT" 2>&1
}

check() { # check <name> <status of the condition>
  if [ "$2" -eq 0 ]; then PASS=$((PASS + 1)); echo "ok   $1"; else FAIL=$((FAIL + 1)); echo "FAIL $1"; echo "$out" | tail -20; fi
}

out=$(run false); rc=$?
[ "$rc" -ne 0 ] && grep -q "::error" <<<"$out"
check "a member that never detaches fails the run" $?

out=$(run true); rc=$?
[ "$rc" -eq 0 ] && grep -q deleted <<<"$out"
check "a global database that empties is deleted and the run passes" $?

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
