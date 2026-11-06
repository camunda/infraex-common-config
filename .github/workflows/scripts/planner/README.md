# Test-selection planner

Decides, for each pull request, which CI workflows must run, records the decision in an
append-only store, lets the expensive suites skip themselves, and learns from the outcomes.

**The decision is fully automatic.** There is no approval step and no required label. The labels
`planner:run-all`, `run_<suite>` and the existing `skip_*` are optional escape hatches.

Standard library only (PyYAML or the `yq` binary is used to read workflow files). Python 3.11+.

## Pieces

| Piece | File | Runs as |
|---|---|---|
| Planner + recorder | `.github/workflows/planner.yml` (reusable) | `pull_request` in the calling repository |
| Gate | `.github/actions/planner-gate` (composite) | a step in each suite's `triage` job |
| Collector | `.github/workflows/planner-collect.yml` (reusable) | hourly, from the calling repository |
| Learner | `.github/workflows/planner-learn.yml` (reusable) | weekly, from the calling repository |
| Package | `.github/workflows/scripts/planner/` | `python3 -m planner <command>` |
| Tests | `.github/workflows/scripts/planner/tests/` | `just test-planner`, workflow `test-planner` |

Commands: `decide` (read-only), `record` (privileged), `gate`, `runs`, `collect`, `learn`, `audit`.

## What it decides

Rules (versioned in `RULES_VERSION`; each decision lists the rules that fired and why):

- **R1** cheap layers (static, offline unit, local) always run when their path filters match.
- **R2** a changed Terraform module runs its *owner suite* (a Terratest suite whose Go sources build
  it), plus the consumers' golden plans when `variables.tf` or `outputs.tf` changed.
- **R3** a module without an owner suite rotates in one consumer.
- **R4** a change inside one reference-architecture root runs that RA's primary suite.
- **R5** a shared action or `.tool-versions` change rotates in one suite that uses it.
- **R6** docs only: cheap checks only.
- **R7** rotation: among the candidates, the suite with the highest `staleness x weight` runs; ties
  within 10 percent go to the cheaper one. Staleness = days since the last full success covering the
  module. A circuit breaker (**R7b**) makes a suite mandatory for a module after two confirmed escapes.
- **R8** an engine major (a `# renovate:`-annotated version pin whose major changed): module owner
  suite plus one rotating RA among those carrying the same pin.
- **R9** a significant module interface change (removed or renamed output, removed variable, new
  required variable, changed type): owner suite, one rotating consumer, consumers' goldens.
- **R10** a change inside an RA root that only touches version pins, goldens or docs does not run
  that RA's own from-scratch suite.
- **R11** the planner config or caller changed, or a label asks for it: nothing is narrowed.
- **R12** gating is off for this PR (fork, base branch not gated).

Only suites listed in `gate_suites` (config) are ever skipped, and only in `gate` mode. Everything
else is recorded as `enforced: false`.

## Modes and switches (all optional)

- `PLANNER_MODE` (repository variable): empty or `gate` = suites skip themselves; `report` =
  record and comment only (use it as a burn-in); any other value = `report`.
- `PLANNER_GATE_SUITES`: comma separated override of the gateable suites, `none` disables gating.
- `PLANNER_WEIGHTS_PIN`: id of a `weights` event to use instead of the newest (rollback).
- Fail open everywhere: no decision within the gate timeout (240 s) means the suite runs as before.

## The store

Branch `ci-decisions` in the repository that runs the planner. One event per file, never edited,
one GitHub-verified commit per write (contents API with `GITHUB_TOKEN`), conflicts retried with
jittered backoff. Layout:

```
decisions/pr/<N>/<head_sha>/<ts>-<labels8>.json     decision events (newest wins for the gate)
events/YYYY/MM/DD/<ts>-<type>-<id>.json              outcome | escape | weights | checkpoint | watermark
```

Every file has `"schema": "planner.event/1"`. The writer validates against a closed schema
(`schema.py`): unknown keys, bad shas, over-long strings, or a PR/sha/repo that does not match the
event context are rejected. Decision ids hash the whole body, so tampering is detectable
(`planner audit verify`); a weekly `checkpoint` hashes the week's files.

Query: `git clone --depth 1 --branch ci-decisions <repo> store`, then

```
python3 -m planner audit --store-dir store --pr 3831
python3 -m planner audit --store-dir store --module aurora --since 2026-10-01
python3 -m planner audit --store-dir store --suite ecs_dual --type outcome,escape
python3 -m planner audit verify --store-dir store
```

## Learning

Rules are code. Learned: per `(module, suite)` weight `clamp(1 + 5 * risk, 1, 4)` with
`risk = (fail + 3 escape + 1) / (n_run + escape + 20)`, decayed with a 60-day half-life over 180
days and moved at most 0.5 per weekly run; per-suite flake rate and median cost. Escapes are
confirmed failures of scheduled full runs (the next run also fails, or it fails again on a
re-attempt) attributed to merged PRs whose plan skipped that suite and touched a module it
consumes. Every learned number carries the ids of the records it came from.

## Security

The planner job reads the PR as data with a read-only token and no secrets. Planner source comes
from the reusable workflow's own commit; the suite registry and the config come from the PR's base
commit. Only the recorder job writes, and it runs no repository code. See the "CI trust boundary"
section of the repository README. Fork PRs run report-only.

## Tests

```
just test-planner                                   # unit tests, no network
PLANNER_REPLAY=1 python3 -m unittest planner.tests.test_replay   # replays real PRs (needs a clone)
python3 -m planner.tests.replay <clone> <runs.jsonl> <out dir>    # decisions for real past PRs
```
