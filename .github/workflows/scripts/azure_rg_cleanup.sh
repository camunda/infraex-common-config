#!/bin/bash

set -euo pipefail

if [[ "${DRY_RUN:-}" == "true" ]]; then
  echo "Dry run mode enabled. No changes will be made."
fi

az group list --query "[?location=='$AZURE_REGION'].name" -o tsv | while read -r RG; do
  [[ -z "$RG" ]] && continue

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
