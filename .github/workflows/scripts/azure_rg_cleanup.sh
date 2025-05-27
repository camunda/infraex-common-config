#!/bin/bash

set -euo pipefail

if [[ "${DRY_RUN:-}" == "true" ]]; then
  echo "Dry run mode enabled. No changes will be made."
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
