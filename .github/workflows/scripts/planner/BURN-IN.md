# Burn-in: the planner end to end on GitHub, in report mode

Report mode runs the whole chain (facts, Camunda decision, check run, status, comment, planner-gate, collector)
and **skips nothing**. It is how the planner is compared with reality before anything is gated.

Why report mode cannot skip, in three independent places (each covered by a unit test):

1. The planner caller passes `mode: ${{ vars.PLANNER_MODE || 'report' }}`; an empty or unknown mode is `report`
   in the facts (`test_camunda_flow.test_report_mode_never_skips`).
2. A plan is only `gate` when the facts asked for `gate`; in a report plan no suite is `enforced`, whatever the
   process answers (same test).
3. `planner-gate` has its own `mode` input (default `report`, the suites pass the same variable); a skip needs
   `gate` there too. In report mode it logs `planner-gate: report mode, nothing is skipped (plan <id> says skip|run)`
   and outputs `should_skip=false`.

`python3 -m planner burnin` checks the result on real runs (`tests/test_burnin.py` covers its verdicts).

## 0. Before the first pull request

Done once, by the people who own the cluster and the repositories (the checklist with every value is in the task
notes; nothing here creates them):

- [ ] Elasticsearch: `camunda/bootstrap/es-bootstrap.sh` (policy, indices, aliases, connector API key).
- [ ] Connectors runtime: `SECRET_PLANNER_ES_URL`, `SECRET_PLANNER_ES_APIKEY` (`camunda/bootstrap/connectors-secrets.values.yaml`).
- [ ] Clients planner, recorder, deployer in the identity provider; `camunda/bootstrap/authorizations.sh`.
- [ ] Route timeout above `await-seconds + 5`, or `PLANNER_AWAIT_SECONDS` below the existing limit (`camunda/README.md`).
- [ ] Engine clock zone UTC.
- [ ] Models deployed with `planner-deploy.yml` (dry run, then for real); the job summary lists 3 processes and 3 graphs.
- [ ] Calling repository: variables `CAMUNDA_BASE_URL`, `CAMUNDA_TOKEN_URL` (and `CAMUNDA_AUDIENCE`, `CAMUNDA_SCOPE`,
      `PLANNER_AWAIT_SECONDS` if needed), **`PLANNER_MODE` unset or `report`**; secrets `CAMUNDA_CLIENT_ID`,
      `CAMUNDA_CLIENT_SECRET`, `CAMUNDA_COLLECTOR_CLIENT_ID`, `CAMUNDA_COLLECTOR_CLIENT_SECRET`.

## 1. Smoke test against the cluster (before GitHub)

From a clone of the calling repository and of this one, with the planner client's credentials in the environment
(`CAMUNDA_BASE_URL`, `CAMUNDA_TOKEN_URL`, `CAMUNDA_CLIENT_ID`, `CAMUNDA_CLIENT_SECRET`):

```
cd .github/workflows/scripts
python3 -m planner facts --repo <RA clone> --slug camunda/camunda-deployment-references --pr 1 \
  --head-sha <a commit> --base-sha <its base> --mode report --out /tmp/facts.json
time python3 -m planner camunda-plan --facts /tmp/facts.json --out /tmp/plan.json --await-seconds 30
jq '{source, mode, reason, decision_id, rotation_picked, enforced: [.decision[] | select(.enforced)] | length}' /tmp/plan.json
```

Expected: `source: "camunda"`, `mode: "report"`, `enforced: 0`, well under the await. A `fail-open` reason names the
problem: `401` secret, `403` authorization, `404` not deployed, `504` the route timeout. Then, once, a cross check:
the planner client starting `ci-test-record`, and the recorder client starting `ci-test-planner`, must both get 403.

## 2. The first pull requests

Merge the planner caller and the gates (report mode) into the calling repository. Then, for each of a few pull
requests (a docs-only change, a change to one Terraform module, a version bump), check:

- the check run `planner plan` on the head commit: source `camunda`, mode `report`; the status `planner / decision`;
  one sticky comment that says "Report only: nothing is skipped by this decision";
- the `decide` job log: `plan: source=camunda ...` (a `fail-open` line says why);
- each gated suite's `triage` job: `planner-gate: report mode, nothing is skipped (plan <id> says ...)` and
  `should_skip=false`; every triggered suite runs its jobs;
- the comparison, with a read-only token (`actions: read`, `checks: read`, `pull-requests: read`):

```
cd .github/workflows/scripts
GH_TOKEN=<read-only> python3 -m planner burnin --repo camunda/camunda-deployment-references --pr <n> --out burnin-<n>.json
```

Exit 0: no suite was skipped against the mode. Exit 1: a violation (planner-gate said skip in report mode, or skipped
a suite the plan runs, or a skipped suite with no label and no readable gate answer): stop and look before going on.
Exit 2: no plan or no API access.

## 3. Cluster-side checks (cluster owner)

- The decision document of a pull request is in `ci-planner-decisions` (search by `pr`); its `ts` is within a minute
  of the check run's `completed_at`. An offset of whole hours means the engine's clock zone is not UTC.
- After the hourly collector ran: outcome documents in `ci-planner-outcomes` for the finished suite runs.
- Operate shows the `ci-test-planner` instances without incidents.

## 4. Over the burn-in (one to two weeks)

Run `burnin` for every pull request that had a plan (for example each morning for the PRs updated the day before)
and keep the JSON files. Then:

```
jq -s '{prs: length, violations: [.[].violations[]] | length,
        would_skip_ran: [.[].rows[] | select(.plan == "skip" and .execution == "ran")] | length,
        would_skip_failed: [.[].rows[] | select(.plan == "skip" and .execution == "ran" and .conclusion == "failure") | .suite],
        fail_open: [.[] | select(.source != "camunda")] | length,
        rotation: [.[] | .rotation_picked] | map(select(. != null)) | group_by(.) | map({(.[0]): length}) | add}' burnin-*.json
```

- `violations` must be 0.
- `would_skip_failed` lists suites the plan would have skipped that then failed: each one is a would-be escape. Look at
  the failure: a real regression from that PR means a rule is missing; infrastructure noise does not count.
- `fail_open` is how often Camunda could not answer (route timeout, outage); the plans still skipped nothing.
- `rotation` shows which consumer suites the rotation picked and how evenly.

Moving any suite to `gate` (`PLANNER_MODE=gate`) is a separate decision for the repository owners, taken on these
numbers. Back to report at any time: set `PLANNER_MODE=report` or delete it. Turn the planner off entirely: delete
`CAMUNDA_BASE_URL` (every plan then fails open).
