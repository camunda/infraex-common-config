#!/usr/bin/env bash
# Orchestration Cluster authorizations of the planner's three OAuth clients, one permission each:
#   planner   CREATE_PROCESS_INSTANCE on process ci-test-planner   (GitHub planner workflow, decide job)
#   recorder  CREATE_PROCESS_INSTANCE on process ci-test-record    (GitHub collector workflow)
#   deployer  CREATE on RESOURCE *                                 (GitHub planner-deploy workflow only;
#             effectively remote code execution on the cluster, the API only accepts the id `*` for it)
#
# The clients themselves are created in the identity provider (Keycloak, Microsoft Entra ID, ...), which is
# not covered here. Run by a cluster administrator, never from GitHub. The same authorizations can be declared
# in the Helm values instead (camunda.security.initialization.authorizations, see the README).
#
#   CAMUNDA_BASE_URL     https://<host>/v2 (required)
#   ADMIN_TOKEN_FILE     file holding an access token of an administrator (required; the token is not printed)
#   PLANNER_CLIENT_ID, RECORDER_CLIENT_ID, DEPLOYER_CLIENT_ID   the OAuth client ids (each one optional)
set -euo pipefail

: "${CAMUNDA_BASE_URL:?CAMUNDA_BASE_URL is required}"
: "${ADMIN_TOKEN_FILE:?ADMIN_TOKEN_FILE is required}"
case "${CAMUNDA_BASE_URL}" in https://*) ;; *) echo "CAMUNDA_BASE_URL must be an https URL" >&2; exit 2 ;; esac
BASE="${CAMUNDA_BASE_URL%/}"

hdr="$(mktemp)"
trap 'rm -f "${hdr}"' EXIT
chmod 600 "${hdr}"
printf 'Authorization: Bearer %s\nContent-Type: application/json\n' "$(tr -d '\r\n' < "${ADMIN_TOKEN_FILE}")" > "${hdr}"

authorize() {  # owner, resource type, resource id, permission
  [ -n "$1" ] || return 0
  jq -nc --arg o "$1" --arg t "$2" --arg r "$3" --arg p "$4" \
    '{ownerId: $o, ownerType: "CLIENT", resourceType: $t, resourceId: $r, permissionTypes: [$p]}' \
    | curl -sS --fail-with-body -H @"${hdr}" -X POST "${BASE}/authorizations" --data-binary @- \
    | jq -c --arg o "$1" --arg r "$2:$3" --arg p "$4" '{owner: $o, resource: $r, permission: $p, authorizationKey}'
}

authorize "${PLANNER_CLIENT_ID:-}" PROCESS_DEFINITION ci-test-planner CREATE_PROCESS_INSTANCE
authorize "${RECORDER_CLIENT_ID:-}" PROCESS_DEFINITION ci-test-record CREATE_PROCESS_INSTANCE
authorize "${DEPLOYER_CLIENT_ID:-}" RESOURCE '*' CREATE
