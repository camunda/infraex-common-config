#!/bin/bash

set -euxo pipefail

# Tears down Aurora Global Databases whose members all live in regions wiped by
# the nightly cleanup. This runs ONCE, before the per-region cloud-nuke matrix,
# so it is the single orchestrator of the cross-region teardown.
#
# Why a dedicated job instead of doing this per region: an Aurora Global Database
# links DB clusters across Regions. A member can't be deleted while it belongs to
# the global database, and the primary (writer) can't be detached until every
# secondary is removed. cloud-nuke runs independently per region and can't
# perform that cross-region teardown. Doing it inside the parallel per-region
# cleanup meant several jobs raced on the same global database; doing it here once
# removes that race. Each member becomes a standalone regional cluster and is then
# deleted by cloud-nuke in its own region.

DRY_RUN=${DRY_RUN:-false}

# Regions fully wiped by this workflow (its whole matrix). A Global Database is
# only torn down when ALL of its members live in these regions, so we never touch
# permanent or reference environments. The workflow passes the matrix-derived
# value; the default is a fallback for local/manual runs.
CLEANUP_REGIONS="${CLEANUP_REGIONS:-eu-west-2 eu-west-3 eu-north-1 us-east-1 us-east-2}"

# Regions actually being cleaned on this run (matrix day logic). A Global Database
# is only torn down when at least one member lives in an active region, matching
# the previous per-region behaviour (teardown happens on the day the member
# regions are wiped). Defaults to all cleanup regions.
ACTIVE_REGIONS="${ACTIVE_REGIONS:-$CLEANUP_REGIONS}"

# Function to execute a command or simulate it if DRY_RUN is true
execute_or_simulate() {
    local cmd="$1"
    if [ "$DRY_RUN" = true ]; then
        echo "[DRY RUN] Would execute: $cmd"
    else
        eval "$cmd"
    fi
}

# Delete a global cluster, surfacing the AWS error instead of swallowing it. A
# concurrent/previous run may already have deleted it, so only warn on failure.
delete_global_cluster() {
    local id="$1" region="$2" delete_err
    echo "Deleting global database: $id"
    if [ "$DRY_RUN" = true ]; then
        execute_or_simulate "aws rds delete-global-cluster --region $region --global-cluster-identifier $id"
    elif ! delete_err=$(aws rds delete-global-cluster --region "$region" --global-cluster-identifier "$id" 2>&1); then
        echo "Warning: delete-global-cluster for $id did not succeed: ${delete_err}"
    fi
}

# Region used to issue the global (account-wide) describe/delete calls.
api_region="${CLEANUP_REGIONS%% *}"

echo "Discovering Aurora Global Databases (cleanup regions: $CLEANUP_REGIONS; active today: $ACTIVE_REGIONS)"

# Global clusters are account-global; query each cleanup region and dedupe to be
# robust against regional endpoint differences.
global_cluster_ids=""
for r in $CLEANUP_REGIONS; do
    ids=$(aws rds describe-global-clusters --region "$r" \
        --query 'GlobalClusters[].GlobalClusterIdentifier' --output text 2>/dev/null || true)
    if [ -n "$ids" ] && [ "$ids" != "None" ]; then
        global_cluster_ids="$global_cluster_ids $ids"
    fi
done
# Dedupe. Use `awk 'NF'` (not `grep -v '^$'`) to drop blank lines: grep exits 1
# when nothing matches, which under `set -o pipefail` would abort the script in
# the normal "no global clusters" case before the empty check below.
global_cluster_ids=$(echo "$global_cluster_ids" | tr ' ' '\n' | awk 'NF' | sort -u | tr '\n' ' ')

if [ -z "${global_cluster_ids// /}" ]; then
    echo "No Aurora Global Databases found."
    exit 0
fi

for global_cluster_id in $global_cluster_ids; do
    # Describe members; on failure surface the error and skip this cluster rather
    # than mistaking it for "no members".
    if ! members=$(aws rds describe-global-clusters --region "$api_region" \
        --global-cluster-identifier "$global_cluster_id" \
        --query 'GlobalClusters[0].GlobalClusterMembers[].[DBClusterArn,IsWriter]' \
        --output text 2>&1); then
        echo "Warning: could not describe global database $global_cluster_id; skipping its teardown this run: ${members}"
        continue
    fi

    # An empty member list (or the literal "None" when GlobalClusters[0] is null)
    # means there is nothing to detach, so the global database can go.
    if [ -z "$members" ] || [ "$members" = "None" ]; then
        echo "Global database $global_cluster_id has no members, deleting it"
        delete_global_cluster "$global_cluster_id" "$api_region"
        continue
    fi

    # Sort members into secondaries and primary while enforcing the guards.
    secondary_arns=()
    primary_arn=""
    primary_region=""
    active_member=false
    skip_global_cluster=false

    while IFS=$'\t' read -r member_arn is_writer; do
        [ -z "$member_arn" ] && continue
        # ARN format: arn:aws:rds:<region>:<account-id>:cluster:<name>
        member_region=$(echo "$member_arn" | cut -d: -f4)

        # Safety guard: never touch a global database with a member outside the
        # regions this workflow fully wipes.
        if ! grep -qwF "$member_region" <<< "$CLEANUP_REGIONS"; then
            echo "Skipping global database $global_cluster_id (member in protected region: $member_region)"
            skip_global_cluster=true
            break
        fi

        # Only tear down on a run where at least one member region is being cleaned.
        if grep -qwF "$member_region" <<< "$ACTIVE_REGIONS"; then
            active_member=true
        fi

        if [ "$is_writer" = "True" ]; then
            primary_arn="$member_arn"
            primary_region="$member_region"
        else
            secondary_arns+=("$member_arn")
        fi
    done <<< "$members"

    if [ "$skip_global_cluster" = true ]; then
        continue
    fi

    if [ "$active_member" != true ]; then
        echo "Skipping global database $global_cluster_id (no member in an active region today: $ACTIVE_REGIONS)"
        continue
    fi

    echo "Tearing down global database: $global_cluster_id"

    # Detach secondaries first; the primary can't be removed while any secondary
    # member is still attached.
    for member_arn in "${secondary_arns[@]}"; do
        member_region=$(echo "$member_arn" | cut -d: -f4)
        echo "Detaching secondary cluster $member_arn (region $member_region)"
        execute_or_simulate "aws rds remove-from-global-cluster --region $member_region --global-cluster-identifier $global_cluster_id --db-cluster-identifier $member_arn" || true
    done

    if [ -n "$primary_arn" ]; then
        echo "Detaching primary cluster $primary_arn (region $primary_region)"
        if [ "$DRY_RUN" = true ]; then
            execute_or_simulate "aws rds remove-from-global-cluster --region $primary_region --global-cluster-identifier $global_cluster_id --db-cluster-identifier $primary_arn"
        else
            # Secondary removal is asynchronous, so the primary detach can
            # transiently fail with InvalidGlobalClusterStateFault until the
            # secondaries are gone. Re-check membership and retry (capped ~5 min)
            # instead of swallowing the error; stop once the primary is gone.
            primary_detached=false
            for _ in $(seq 1 30); do
                still_member=$(aws rds describe-global-clusters --region "$primary_region" \
                    --global-cluster-identifier "$global_cluster_id" \
                    --query "length(GlobalClusters[0].GlobalClusterMembers[?DBClusterArn=='${primary_arn}'])" \
                    --output text 2>/dev/null || echo "unknown")
                if [ "$still_member" = "0" ] || [ "$still_member" = "None" ]; then
                    primary_detached=true
                    break
                fi
                if detach_err=$(aws rds remove-from-global-cluster --region "$primary_region" \
                    --global-cluster-identifier "$global_cluster_id" \
                    --db-cluster-identifier "$primary_arn" 2>&1); then
                    primary_detached=true
                    break
                fi
                echo "Primary detach not ready yet (secondaries still detaching): ${detach_err}"
                sleep 10
            done
            if [ "$primary_detached" != true ]; then
                echo "Warning: could not detach primary $primary_arn from $global_cluster_id after ~5 min; leaving it for the next run"
            fi
        fi
    fi

    # Detaching is asynchronous; wait for the global database to be empty before
    # deleting it (best-effort, capped at ~5 minutes).
    delete_region="${primary_region:-$api_region}"
    # Default to empty so dry-run (which skips the wait loop) still shows the
    # intended deletion.
    remaining=0
    if [ "$DRY_RUN" != true ]; then
        remaining="unknown"
        for _ in $(seq 1 30); do
            # A describe failure must stay "unknown" (not be read as 0), otherwise
            # we could delete while members are still attached.
            if ! remaining=$(aws rds describe-global-clusters --region "$delete_region" \
                --global-cluster-identifier "$global_cluster_id" \
                --query 'length(GlobalClusters[0].GlobalClusterMembers)' \
                --output text 2>&1); then
                echo "Warning: could not check members of $global_cluster_id: ${remaining}"
                remaining="unknown"
                break
            fi
            if [ "$remaining" = "0" ] || [ "$remaining" = "None" ] || [ -z "$remaining" ]; then
                remaining=0
                break
            fi
            echo "Waiting for $remaining member(s) to detach from $global_cluster_id..."
            sleep 10
        done
    fi

    if [ "$remaining" = "0" ]; then
        delete_global_cluster "$global_cluster_id" "$delete_region"
    else
        echo "Warning: global database $global_cluster_id not confirmed empty (remaining=$remaining); leaving it for the next run"
    fi
done
