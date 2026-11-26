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

TWO_MEMBERS='arn:aws:rds:eu-west-2:1:cluster:r0\tTrue\narn:aws:rds:eu-west-3:1:cluster:r1\tFalse\n'

run() { # run <detaches: true|false> <member ClusterCreateTime, or fail> [<members> <first-seen tag>]; prints the output, returns the exit code
  local stub
  stub="$(mktemp -d)"
  cat >"$stub/aws" <<STUB
#!/bin/bash
case "\$*" in
  *describe-db-clusters*) [ "$2" = fail ] && exit 1; echo "$2" ;;
  *list-tags-for-resource*) echo "${4:-None}" ;;
  *add-tags-to-resource*) echo tagged >&2 ;;
  *remove-from-global-cluster*) touch "$stub/detached"; exit 0 ;;
  *delete-global-cluster*) echo deleted >&2; exit 0 ;;
  *"length(GlobalClusters[0].GlobalClusterMembers[?"*|*"length(GlobalClusters[0].GlobalClusterMembers)"*)
    if [ "$1" = true ] && [ -f "$stub/detached" ]; then echo 0; else echo 2; fi ;;
  *GlobalClusterArn*) echo arn:aws:rds::1:global-cluster:e2e-x-global-db ;;
  *"--global-cluster-identifier"*) printf '${3-$TWO_MEMBERS}' ;;
  *describe-global-clusters*) echo e2e-x-global-db ;;
esac
STUB
  chmod +x "$stub/aws"
  PATH="$stub:$PATH" DRY_RUN=false CLEANUP_REGIONS="eu-west-2 eu-west-3" GLOBAL_DB_POLL_SECONDS=0 \
    CLEANUP_OLDER_THAN_BY_REGION="eu-west-2=12h eu-west-3=12h" bash "$SCRIPT" 2>&1
}

check() { # check <name> <status of the condition>
  if [ "$2" -eq 0 ]; then PASS=$((PASS + 1)); echo "ok   $1"; else FAIL=$((FAIL + 1)); echo "FAIL $1"; echo "$out" | tail -20; fi
}

old=2020-01-01T00:00:00.000000+00:00

out=$(run false "$old"); rc=$?
[ "$rc" -ne 0 ] && grep -q "::error" <<<"$out"
check "a member that never detaches fails the run" $?

out=$(run true "$old"); rc=$?
[ "$rc" -eq 0 ] && grep -q deleted <<<"$out"
check "a global database that empties is deleted and the run passes" $?

young=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)

for created in "$young" None fail; do
  out=$(run true "$created"); rc=$?
  [ "$rc" -eq 0 ] && grep -q "Skipping global database" <<<"$out" && ! grep -qE 'remove-from-global-cluster|deleted' <<<"$out"
  check "a global database is kept when the creation time of its members is '$created'" $?
done

out=$(run true "$old" "" None); rc=$?
[ "$rc" -eq 0 ] && grep -q tagged <<<"$out" && ! grep -q deleted <<<"$out"
check "an empty global database without a first-seen tag is tagged and kept" $?

out=$(run true "$old" "" "$young"); rc=$?
[ "$rc" -eq 0 ] && grep -q "Skipping global database" <<<"$out" && ! grep -qE 'tagged|deleted' <<<"$out"
check "an empty global database first seen less than the cleanup age ago is kept" $?

out=$(run true "$old" "" 2020-01-01T00:00:00Z); rc=$?
[ "$rc" -eq 0 ] && grep -q deleted <<<"$out" && ! grep -q tagged <<<"$out"
check "an empty global database first seen longer than the cleanup age ago is deleted" $?

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
