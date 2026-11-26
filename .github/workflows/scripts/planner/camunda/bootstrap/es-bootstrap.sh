#!/usr/bin/env bash
# Planner memory in Elasticsearch: the retention policy, the four indices behind their write aliases and,
# optionally, the API key the connectors runtime uses (read and write on ci-planner-* only).
#
# Run by the owner of the Elasticsearch cluster, from a machine that can reach it; never from GitHub.
# Idempotent: an existing alias is left as it is, the policy is updated in place, a new key is only made
# when KEY_FILE is set.
#
#   ES_URL            https://<elasticsearch>:<port>  (required)
#   ES_ADMIN_API_KEY  an API key allowed to manage ILM, indices and API keys (required, read from the environment)
#   REPLICAS          number_of_replicas for the first indices (default: the mapping's value, 1)
#   KEY_FILE          if set, create the connector API key and write its encoded value to this file (mode 0600).
#                     The key is never printed: hand it to the connectors runtime as SECRET_PLANNER_ES_APIKEY.
set -euo pipefail

: "${ES_URL:?ES_URL is required}"
: "${ES_ADMIN_API_KEY:?ES_ADMIN_API_KEY is required}"
case "${ES_URL}" in https://*) ;; *) echo "ES_URL must be an https URL" >&2; exit 2 ;; esac
ES_URL="${ES_URL%/}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../es" && pwd)"

# the admin key goes through a header file, not the command line
hdr="$(mktemp)"
trap 'rm -f "${hdr}"' EXIT
chmod 600 "${hdr}"
printf 'Authorization: ApiKey %s\nContent-Type: application/json\n' "${ES_ADMIN_API_KEY}" > "${hdr}"
es() { curl -sS --fail-with-body -H @"${hdr}" "$@"; }

echo "ILM policy ci-planner-retention"
es -X PUT "${ES_URL}/_ilm/policy/ci-planner-retention" --data-binary @"${DIR}/ilm-ci-planner-retention.json" > /dev/null

for i in decisions outcomes escapes weights; do
  alias="ci-planner-${i}"
  code="$(curl -sS -o /dev/null -w '%{http_code}' -H @"${hdr}" -I "${ES_URL}/_alias/${alias}")"
  if [ "${code}" = 200 ]; then
    echo "${alias}: alias exists, left as it is"
    continue
  fi
  echo "${alias}: creating ${alias}-000001 with the write alias"
  jq --arg a "${alias}" --arg r "${REPLICAS:-}" \
    '(if $r == "" then . else .settings.number_of_replicas = ($r | tonumber) end) | . + {aliases: {($a): {is_write_index: true}}}' \
    "${DIR}/${alias}.mapping.json" | es -X PUT "${ES_URL}/${alias}-000001" --data-binary @- > /dev/null
done

if [ -n "${KEY_FILE:-}" ]; then
  umask 077
  es -X POST "${ES_URL}/_security/api_key" --data-binary @- <<'JSON' | jq -jer .encoded > "${KEY_FILE}"
{"name": "ci-planner-connectors",
 "role_descriptors": {"ci_planner": {"indices": [{"names": ["ci-planner-*"], "privileges": ["read", "write", "view_index_metadata"]}]}}}
JSON
  echo "connector API key written to ${KEY_FILE} (not printed)"
fi
