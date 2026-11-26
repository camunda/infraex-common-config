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

Not verified: the AI Agent task (its element template is not in the connectors runtime jar; the input names are still unchecked), an LLM call, GitHub
(the burn-in in `../BURN-IN.md` is the first GitHub end-to-end run), the `planner-deploy.yml` workflow against a real cluster.

Offline checks: `npm ci && cd .. && python3 -m unittest planner.tests.test_dmn_conformance planner.tests.test_bpmn_feel planner.tests.test_dmn_memory_learn`.

## Cluster requirements

- **Camunda 8.10 Orchestration Cluster** over HTTPS with OIDC client credentials, and a connectors runtime that can
  reach the planner's Elasticsearch. Which cluster, and who owns it, is decided by the humans running it.
- **Request timeout above the await.** The planner waits for the process (`await-seconds`, 30 s by default, at most 60).
  Every proxy between GitHub and the cluster (ingress controller, load balancer, gateway) must allow a request a few
  seconds longer, otherwise the plan fails open at the proxy's limit (the Kind test's Contour ingress cut at 15 s: a 504).
  Either raise the route timeout to at least `await-seconds + 5` (for example NGINX `nginx.ingress.kubernetes.io/proxy-read-timeout`,
  Contour `projectcontour.io/response-timeout`, AWS ALB idle timeout; check the controller's documentation) or set the
  repository variable `PLANNER_AWAIT_SECONDS` deliberately below the existing limit (for example 12 behind a 15 s route).
  The process itself answered in 0.2 to 0.5 s on the test cluster.
- **UTC.** The timestamps the process writes (`string(now())`, cut at `@` and marked `Z`) are only right when the engine's
  clock zone is UTC. Do not set `TZ` or `-Duser.timezone` to another zone on the Orchestration Cluster. The burn-in
  procedure checks it (`../BURN-IN.md`, step 3).
- **Authorizations enabled**, so each client can only do its one thing (next section).

## Clients, secrets, permissions

Three OAuth clients, created in the identity provider by the cluster owner, one permission each
(`bootstrap/authorizations.sh` creates the authorizations through `POST /v2/authorizations`; the same can be declared
with Identity as Code, `camunda.security.initialization.authorizations`):

| Client | Permission | Used by | GitHub secrets |
|---|---|---|---|
| planner | `CREATE_PROCESS_INSTANCE` on `PROCESS_DEFINITION` `ci-test-planner` | `planner.yml`, decide job | repository secrets `CAMUNDA_CLIENT_ID`, `CAMUNDA_CLIENT_SECRET` of the calling repository |
| recorder | `CREATE_PROCESS_INSTANCE` on `PROCESS_DEFINITION` `ci-test-record` | `planner-collect.yml` | `CAMUNDA_COLLECTOR_CLIENT_ID`, `CAMUNDA_COLLECTOR_CLIENT_SECRET` of the calling repository |
| deployer | `CREATE` on `RESOURCE` `*` (the only id the API accepts for it) | `planner-deploy.yml` only | environment secrets `CAMUNDA_DEPLOYER_CLIENT_ID`, `CAMUNDA_DEPLOYER_CLIENT_SECRET` of the environment `camunda-planner-deploy` in this repository |

The deployer permission is effectively remote code execution on the cluster (a deployed process runs whatever it
describes): it never goes to the planner or the collector, never to a pull request workflow, and the deploy workflow only
sends it to the URLs the environment allows. Test once that each client gets 403 on the other process.

Connector secrets, on the connectors runtime only (never in GitHub): `PLANNER_ES_URL`, `PLANNER_ES_APIKEY`, read by the
BPMN as `{{secrets.PLANNER_ES_URL}}` and `{{secrets.PLANNER_ES_APIKEY}}`. The runtime exposes environment variables with
the prefix `SECRET_` as connector secrets. `PLANNER_LLM_API_KEY` only if the AI advisor is ever turned on (it is off).
With the Helm chart (as on the 8.10 test cluster), from a Kubernetes secret holding the URL and the key from `es-bootstrap.sh`:

```
kubectl create secret generic planner-es-credentials -n <namespace> \
  --from-literal=url=https://<elasticsearch>:<port> --from-file=apikey=<KEY_FILE>
```

```yaml
# `connectors.env` replaces the chart's list: merge these two entries into the env list the release already has
connectors:
    env:
        - name: SECRET_PLANNER_ES_URL
          valueFrom: {secretKeyRef: {name: planner-es-credentials, key: url}}
        - name: SECRET_PLANNER_ES_APIKEY
          valueFrom: {secretKeyRef: {name: planner-es-credentials, key: apikey}}
```

## Elasticsearch and retention

Run by the owner of the Elasticsearch cluster, from a machine that can reach it (GitHub never talks to Elasticsearch):

```
ES_URL=https://<elasticsearch>:<port> ES_ADMIN_API_KEY=... KEY_FILE=./planner-es.key ./bootstrap/es-bootstrap.sh
```

It creates the policy `ci-planner-retention` (`es/ilm-ci-planner-retention.json`), the first backing index of each of
`ci-planner-decisions`, `-outcomes`, `-escapes`, `-weights` with its write alias (strict mappings, `es/*.mapping.json`;
`REPLICAS=0` on a one-node cluster), and an API key limited to `ci-planner-*` (read, write, view_index_metadata) written
to `KEY_FILE` and never printed. Existing aliases are left alone, so it can be re-run.

The policy rolls over every 90 days, force-merges after 90 and deletes after 730 days (age counts from rollover, so
adjust `delete.min_age` to the retention you want plus 90). Writes with a stable `_id` go to the write index through
the alias; an overlapping collector window is idempotent except across a rollover boundary, where a duplicate document is possible
(rare: a window overlap that straddles a rollover) and would count twice in the aggregations; the effect on weights is bounded
by the weekly step limit. Reads search the alias, which covers all backing indices.
Camunda's own history (process and decision instances) is a secondary audit trail whose
retention is configured separately on the cluster; the 8.10 docs do not agree on its default, so set it
explicitly and do not rely on it for months of history.

## Deploy

All six files go in **one** `POST /v2/deployments` (atomic: all or none), from the manual workflow
`.github/workflows/planner-deploy.yml`, never from a pull request:

1. Once, in this repository: environment `camunda-planner-deploy` with required reviewers and a deployment branch rule
   for `main`; environment secrets `CAMUNDA_DEPLOYER_CLIENT_ID`, `CAMUNDA_DEPLOYER_CLIENT_SECRET`; environment variables
   `PLANNER_DEPLOY_ALLOWED_BASE_URL` (`https://<host>/v2`) and `PLANNER_DEPLOY_ALLOWED_TOKEN_URL`.
2. Run the workflow from `main` with the cluster's base and token URL (and audience, scope or tenant if the cluster
   needs them), `dry-run: true` first. The `check` job refuses another ref and any file that differs from its generator;
   a dry run checks the URLs against the environment and gets no deployer secret.
3. Run it again with `dry-run: false`. The job summary lists each process and decision requirements graph with its
   version and key; the run fails unless all three processes, all three graphs and the called decisions
   (`plan`, `memory`, `learned`) are in the answer.

Redeploy on every change of a generated file; running instances keep the version they started with, and an unchanged
resource keeps its version. To run the same thing by hand (an administrator, for a first test):

```
cd .github/workflows/scripts
CAMUNDA_BASE_URL=https://<host>/v2 CAMUNDA_TOKEN_URL=... CAMUNDA_CLIENT_ID=... CAMUNDA_CLIENT_SECRET=... \
PLANNER_DEPLOY_ALLOWED_BASE_URL=https://<host>/v2 PLANNER_DEPLOY_ALLOWED_TOKEN_URL=... \
python3 -m planner deploy-models --dir planner/camunda [--dry-run]
```

## Not covered by these files

Operate/Optimize dashboards, alerting on a failing process (a stuck incident means the PR workflow already failed
open), and the AI provider decision.
