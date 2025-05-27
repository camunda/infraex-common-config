#!/bin/bash

set -euo pipefail

CLEANUP_OLDER_THAN="${CLEANUP_OLDER_THAN:-}"
date_command="gdate"

if [[ "${DRY_RUN:-}" == "true" ]]; then
  echo "Dry run mode enabled. No changes will be made."
fi

if [[ -n "$CLEANUP_OLDER_THAN" ]]; then
  MIN_HOURS="${CLEANUP_OLDER_THAN%h}"
  [[ "$MIN_HOURS" =~ ^[0-9]+$ ]] || { echo "Invalid CLEANUP_OLDER_THAN: $CLEANUP_OLDER_THAN"; exit 1; }
  LIMIT_DATE=$($date_command -u -d "$MIN_HOURS hours ago" +"%Y-%m-%dT%H:%M:%SZ")
  echo "Filtering RGs to only those with oldest resource created before: $LIMIT_DATE"
fi

for RG in $(az group list --query "[?location=='$AZURE_REGION'].name" -o tsv); do
  [[ -z "$RG" ]] && continue

  # --- PROTECTED TAG FILTER ---
  if [[ -n "${PROTECTED_TAG_KEY:-}" && -n "${PROTECTED_TAG_VALUE:-}" ]]; then
    tag_value=$(az group show --name "$RG" --query "tags.${PROTECTED_TAG_KEY}" -o tsv || true)
    if [[ "$tag_value" == "$PROTECTED_TAG_VALUE" ]]; then
      echo "Skipping RG $RG: protected tag ${PROTECTED_TAG_KEY}=${PROTECTED_TAG_VALUE} present."
      continue
    fi
  fi

  # --- REQUIRED TAG FILTER ---
  if [[ -n "${REQUIRED_TAG_KEY:-}" && -n "${REQUIRED_TAG_VALUE:-}" ]]; then
    tag_value=$(az group show --name "$RG" --query "tags.${REQUIRED_TAG_KEY}" -o tsv || true)
    if [[ "$tag_value" != "$REQUIRED_TAG_VALUE" ]]; then
      echo "Skipping RG $RG: tag ${REQUIRED_TAG_KEY} does not match required value (${REQUIRED_TAG_VALUE})."
      continue
    fi
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
    else
      echo "Failed to initiate deletion for RG: $RG"
    fi
  fi
done
