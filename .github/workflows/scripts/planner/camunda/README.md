# Camunda artifacts of the test-selection planner

Executable Camunda 8.10 models and the Elasticsearch setup behind the planner. `build_bpmn.py` and
`build_dmn.py` generate the `.bpmn` and `.dmn` files; edit the generators and re-run them
(`python3 build_dmn.py && python3 build_bpmn.py`), not the XML.

| File | What it is |
|---|---|
| `ci-test-planner.bpmn` | Process started by the PR workflow: validate facts, read memory, decide, optional AI advisor (add only), record, return the plan |
| `ci-test-record.bpmn` | Process started by the collector: writes outcome and escape documents to Elasticsearch |
| `ci-test-learn.bpmn` | Weekly timer: aggregates outcomes and escapes, computes bounded weights, writes `ci-planner-weights` |
| `ci-test-planner.dmn` | Rules R1-R12 (decision `plan`), same answer as `policy.py` on 550 generated cases |
| `ci-test-memory.dmn`, `ci-test-learn.dmn` | Memory reduction and the learner formula |
| `es/*.mapping.json`, `es/ilm-*.json` | Index mappings (strict) and the retention policy |
| `queries.py` | The Elasticsearch requests and FEEL expressions used by the generators |

## Status: not run on an engine

The files are valid XML, parse with `bpmn-moddle` / `dmn-moddle` with the Zeebe extension schema (0 problems),
and every FEEL expression was evaluated offline with `feelin` on realistic data. **They have not been deployed
or executed on a Camunda cluster** (no Java, Docker or cluster in the workbench, and the C8 Run download was
not permitted). Element-template input names of the AI Agent task are not verified against the 8.10 connector.
Before relying on it, deploy to a test cluster and run `ci-test-planner` with a facts document from
`python3 -m planner facts`.

Offline checks: `npm ci && cd .. && python3 -m unittest planner.tests.test_dmn_conformance planner.tests.test_bpmn_feel planner.tests.test_dmn_memory_learn`.

## Deploy

```
# one deployment with every resource (process, decisions)
curl -sS -X POST "$CAMUNDA_BASE_URL/deployments" -H "Authorization: Bearer $TOKEN" \
  -F resources=@ci-test-planner.bpmn -F resources=@ci-test-record.bpmn -F resources=@ci-test-learn.bpmn \
  -F resources=@ci-test-planner.dmn -F resources=@ci-test-memory.dmn -F resources=@ci-test-learn.dmn
```

`CAMUNDA_BASE_URL` is the Orchestration Cluster REST API base (`https://<host>/v2`; deployments use the same base).
Redeploy on every change of a generated file; process instances keep the version they started with.

## Client, secrets, permissions

- **Cluster:** an existing 8.10 Orchestration Cluster reachable over HTTPS. Who owns it is an open decision.
- **GitHub caller (planner workflow):** OAuth client credentials in `CAMUNDA_CLIENT_ID`/`CAMUNDA_CLIENT_SECRET`; the client
  has `CREATE_PROCESS_INSTANCE` on process `ci-test-planner` only. The URLs are workflow inputs, not secrets.
- **Collector:** a second client with `CREATE_PROCESS_INSTANCE` on `ci-test-record` only.
- **Connector secrets (cluster side):** `PLANNER_ES_URL`, `PLANNER_ES_APIKEY` (an Elasticsearch API key limited to
  `ci-planner-*`: read and write), `PLANNER_LLM_API_KEY` (only if the advisor is on). Connector secrets are
  configured on the connector runtime and never reach GitHub.
- Elasticsearch is reachable from the connector runtime only, not from GitHub.

## Elasticsearch and retention

```
curl -X PUT "$ES/_ilm/policy/ci-planner-retention" -H 'Content-Type: application/json' -d @es/ilm-ci-planner-retention.json
for i in decisions outcomes escapes weights; do
  # first backing index, with the write alias the connector uses (the aliases are what the process writes to and reads from)
  jq --arg a "ci-planner-$i" '. + {aliases: {($a): {is_write_index: true}}}' es/ci-planner-$i.mapping.json \
    | curl -X PUT "$ES/ci-planner-$i-000001" -H 'Content-Type: application/json' -d @-
done
```

The policy rolls over every 90 days, force-merges after 90 and deletes after 730 days (age counts from rollover, so
adjust `delete.min_age` to the retention you want plus 90). Writes with a stable `_id` go to the write index through
the alias; an overlapping collector window is idempotent except across a rollover boundary, where a duplicate document is possible
(rare: a window overlap that straddles a rollover) and would count twice in the aggregations; the effect on weights is bounded
by the weekly step limit. Reads search the alias, which covers all backing indices.
Camunda's own history (process and decision instances) is a secondary audit trail whose
retention is configured separately on the cluster; the 8.10 docs do not agree on its default, so set it
explicitly and do not rely on it for months of history.

## Not covered by these files

Operate/Optimize dashboards, alerting on a failing process (a stuck incident means the PR workflow already failed
open), and the AI provider decision.
