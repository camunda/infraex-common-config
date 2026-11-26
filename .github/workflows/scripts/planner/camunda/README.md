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

## Status: run once on a real engine (2026-11-26)

Deployed and run end to end on Camunda 8.10.10 (chart 15.4.3, Kind on one EC2 instance, Keycloak OIDC, a separate
Elasticsearch 9.5.6 for `ci-planner-*`): see `kind-test/` in the notes of this work and its report. The engine found
problems that the offline checks (XML parse, `feelin`) did not, all fixed in the generators:

- BPMN: `incoming`/`outgoing` must come before event definitions and loop characteristics; script task expressions must start with `=`.
- DMN: in a business knowledge model `knowledgeRequirement` comes after `encapsulatedLogic`. Validate with the OMG XSDs (the engine's error is just "Unable to parse model").
- `string(now())` is `2026-11-26T11:50:34.347@GMT`, which Elasticsearch rejects; the generators cut it at `@` and add `Z` (right for a UTC cluster).
- REST connector: `resultVariable` stores the whole response; the ES answers are now mapped with `resultExpression = {name: response.body}`.
  Before that fix the memory read silently returned nothing, and every plan still matched the empty-memory policy.
- DMN: an ops-layer workflow that changes itself was dropped from the plan (policy.py includes it); a conformance case now covers it.
- `ci-test-learn` could not be started through the API (timer start only); it has a second, manual start event.

Result on that cluster: 8 facts files gave the same plan as `policy.py` (empty memory), 8 more with seeded memory (3 rotating), the decision
documents were indexed, the planner client was refused (403) on `ci-test-record`, `ci-test-record` is idempotent, `ci-test-learn` wrote a weights
document (weight 1.5 for 12 runs and 3 failures, bounded step). Fail-open held for: process not deployed (404), wrong secret (401), not configured, and a process that
runs 45 s (504 after 15 s: the Contour ingress of the Kind setup cuts requests at 15 s, so the 30 s await is only reachable through an ingress with a longer route timeout).

Not verified: the AI Agent task (its element template is not in the connectors runtime jar; the input names are still unchecked), an LLM call, GitHub.

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
