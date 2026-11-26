# Planner gate

## Description

Reads the plan the test-selection planner published for this pull request (a check run named
"planner plan" on the head commit) and outputs `should_skip`
for the calling workflow. Put it in a suite's `triage` job next to `internal-triage-skip` and
combine the two outputs with OR.

Fully automatic: the planner decides, nobody approves. The step never fails a run and fails
open: if there is no decision within the timeout (planner not installed, plan not published,
Camunda down, planner crashed), `should_skip` is `false` and the suite runs exactly as it does today. Only
same-repository `pull_request` runs are gated; every other event outputs `false` at once. The calling job
needs `checks: read`.

`mode` must be `gate` as well as the plan for a skip: in `report` mode (the default) the step logs what the
plan says and always outputs `false`.


## Inputs

| name | description | required | default |
| --- | --- | --- | --- |
| `mode` | <p><code>gate</code> lets an enforced skip of the plan through. <code>report</code> (default) and any other value never skip.</p> | `false` | `report` |
| `timeout-seconds` | <p>How long to wait for the decision before running as before.</p> | `false` | `240` |
| `github-token` | <p>Token used to read the check run (checks: read is enough).</p> | `false` | `${{ github.token }}` |


## Outputs

| name | description |
| --- | --- |
| `should_skip` | <p><code>true</code> when the planner decided this suite is not needed for this pull request.</p> |


## Runs

This action is a `composite` action.

## Usage

```yaml
- uses: camunda/infraex-common-config/.github/actions/planner-gate@main
  with:
    mode:
    # `gate` lets an enforced skip of the plan through. `report` (default) and any other value never skip.
    #
    # Required: false
    # Default: report

    timeout-seconds:
    # How long to wait for the decision before running as before.
    #
    # Required: false
    # Default: 240

    github-token:
    # Token used to read the check run (checks: read is enough).
    #
    # Required: false
    # Default: ${{ github.token }}
```
