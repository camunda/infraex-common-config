#!/bin/bash

set -euo pipefail

PROTECTED_RG_LIST="NetworkWatcherRG Default-ActivityLogAlertsRG rg-infraex-global-permanent"

CLEANUP_OLDER_THAN="${CLEANUP_OLDER_THAN:-}"

FAILED=false
REMAINING=()

# Detect operating system and set the appropriate date command
if [[ "$(uname)" == "Darwin" ]]; then
    date_command="gdate"
else
    date_command="date"
fi

if [[ "${DRY_RUN:-}" == "true" ]]; then
  echo "Dry run mode enabled. No changes will be made."
fi

if [[ -n "$CLEANUP_OLDER_THAN" ]]; then
  MIN_HOURS="${CLEANUP_OLDER_THAN%h}"
  [[ "$MIN_HOURS" =~ ^[0-9]+$ ]] || { echo "Invalid CLEANUP_OLDER_THAN: $CLEANUP_OLDER_THAN"; exit 1; }
  LIMIT_DATE=$($date_command -u -d "$MIN_HOURS hours ago" +"%Y-%m-%dT%H:%M:%SZ")
  echo "Filtering RGs to only those with oldest resource created before: $LIMIT_DATE"
fi

# Deletion timeout, in seconds, for the verification pass at the end. Validated
# here rather than where it is used: by then groups have already been deleted,
# and the arithmetic would fail with "abc: unbound variable" instead of naming
# the offending setting.
DELETION_TIMEOUT="${DELETION_TIMEOUT:-1800}"
[[ "$DELETION_TIMEOUT" =~ ^[0-9]+$ ]] || { echo "Invalid DELETION_TIMEOUT: $DELETION_TIMEOUT"; exit 1; }

# ── Pass 1: decide what is eligible ──────────────────────────────────────────
ELIGIBLE=()

for RG in $(az group list --query "[?location=='$AZURE_REGION'].name" -o tsv); do
  [[ -z "$RG" ]] && continue

  # Protected RG name check (skip if in protected list)
  if [[ " $PROTECTED_RG_LIST " =~ " $RG " ]]; then
    echo "Skipping $RG: protected"
    continue
  fi

  # --- MINIMUM AGE FILTER ---
  if [[ -n "$CLEANUP_OLDER_THAN" ]]; then
  RESOURCES=$(az resource list --resource-group "$RG" --query "[].{created:createdTime}" -o json)
  OLDEST_DATE=$(echo "$RESOURCES" | jq -r '.[].created' | sort | head -n 1)

  if [[ -z "$OLDEST_DATE" || "$OLDEST_DATE" == "null" ]]; then
      echo "No resource timestamps found in $RG - will proceed to delete"
  else
      if [[ "$OLDEST_DATE" > "$LIMIT_DATE" ]]; then
      echo "Skipping $RG - oldest resource is too new ($OLDEST_DATE)"
      continue
      fi
      echo "Eligible: $RG - oldest resource created $OLDEST_DATE"
    fi
  fi

  ELIGIBLE+=("$RG")
done

# ── Pass 2: leave AKS node resource groups to AKS ────────────────────────────
# An AKS cluster owns its `MC_<parent>_<cluster>_<region>` node resource group
# and deletes it as part of its own teardown. Deleting it directly while the
# parent is also being deleted races that teardown and can leave the cluster
# wedged in a Failed state, which then blocks the parent for good. Only delete
# such a group when its parent is not in this run, i.e. when it is genuinely
# orphaned.
TARGETS=()

for RG in ${ELIGIBLE[@]+"${ELIGIBLE[@]}"}; do
  if [[ "$RG" == MC_* ]]; then
    OWNED=false
    for PARENT in ${ELIGIBLE[@]+"${ELIGIBLE[@]}"}; do
      [[ "$PARENT" == MC_* ]] && continue
      if [[ "$RG" == "MC_${PARENT}_"* ]]; then
        OWNED=true
        break
      fi
    done
    if [[ "$OWNED" == "true" ]]; then
      echo "Skipping $RG: node resource group, deleted with its parent"
      continue
    fi
    echo "Keeping $RG: node resource group whose parent is not being deleted"
  fi
  TARGETS+=("$RG")
done

# ── Pass 3: delete ───────────────────────────────────────────────────────────
INITIATED=()

for RG in ${TARGETS[@]+"${TARGETS[@]}"}; do
  az lock list --resource-group "$RG" --query "[].id" -o tsv | while read -r LOCK_ID; do
    [[ -z "$LOCK_ID" ]] && continue

    if [[ "${DRY_RUN:-}" == "true" ]]; then
      echo "Would remove lock: $LOCK_ID"
    else
      if az lock delete --ids "$LOCK_ID"; then
        echo "Successfully deleted lock: $LOCK_ID"
      else
        echo "Failed to delete lock: $LOCK_ID"
      fi
    fi
  done

  if [[ "${DRY_RUN:-}" == "true" ]]; then
    echo "Would delete RG: $RG"
  else
    if az group delete --name "$RG" --yes --no-wait; then
      echo "Initiated deletion of RG: $RG"
      INITIATED+=("$RG")
    else
      echo "Failed to initiate deletion for RG: $RG"
      FAILED=true
    fi
  fi
done

# ── Pass 4: verify ───────────────────────────────────────────────────────────
# `--no-wait` only reports that the request was accepted. A deletion that fails
# afterwards, which is the usual outcome for a cluster stuck in a Failed state,
# leaves the group running and billing with nothing to signal it. Wait for the
# groups to actually disappear and fail the job if they do not, so a wedged
# group is visible the same morning instead of being rediscovered by hand.
if [[ ${#INITIATED[@]} -gt 0 ]]; then
  echo "Waiting up to ${DELETION_TIMEOUT}s for ${#INITIATED[@]} resource group(s) to disappear..."
  DEADLINE=$(( $(date +%s) + DELETION_TIMEOUT ))

  while true; do
    REMAINING=()
    for RG in ${INITIATED[@]+"${INITIATED[@]}"}; do
      if [[ "$(az group exists --name "$RG")" == "true" ]]; then
        REMAINING+=("$RG")
      fi
    done

    [[ ${#REMAINING[@]} -eq 0 ]] && break
    [[ $(date +%s) -ge $DEADLINE ]] && break

    echo "  still present: ${REMAINING[*]}"
    sleep 30
  done

  if [[ ${#REMAINING[@]} -gt 0 ]]; then
    echo "Resource groups still present after ${DELETION_TIMEOUT}s: ${REMAINING[*]}" >&2
    echo "Their deletion was accepted but did not complete; they are still billing." >&2
    FAILED=true
  else
    echo "All ${#INITIATED[@]} resource group(s) deleted."
  fi
fi

if [[ "$FAILED" == "true" ]]; then
  exit 1
fi
