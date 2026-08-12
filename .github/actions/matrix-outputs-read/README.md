# Matrix outputs - read

## Description

Collect the per-job outputs written by a matrix step and merge them into a single
JSON object, keyed by output name then by matrix key.

Drop-in replacement for `cloudposse/github-action-matrix-outputs-read`, kept because
that action's own `action.yml` references `dcarbone/install-jq-action@v2.1.0` and
`actions/download-artifact@v4` without pinning them. GitHub evaluates the
`Require actions to be pinned to a full-length commit SHA` setting over the entire
dependency tree at `Set up job`, so a caller with that setting on has its job refused
before any step runs, and cannot work around it by choosing a different revision.
Upstream last released in 2024-02 and its main branch is still unpinned, so there is
no revision to move to.

Pair it with `cloudposse/github-action-matrix-outputs-write`, which is unaffected: at
its current release it contains no `uses:` at all.

The job calling this action does not need `actions/checkout`. It must not have one
either if it can be avoided: the download below places every artifact in the working
directory, which is then walked with `find . -maxdepth 2`, and a checkout would put
repository files inside that search path.


## Inputs

| name | description | required | default |
| --- | --- | --- | --- |
| `matrix-step-name` | <p>Name passed as <code>matrix-step-name</code> to the matching write step. It is also the artifact file name this action looks for.</p> | `true` | `""` |


## Outputs

| name | description |
| --- | --- |
| `result` | <p>Merged JSON object. For a matrix writing <code>{"kube": "..."}</code> under keys <code>c1</code> and <code>c2</code>, the result is <code>{"kube": {"c1": "...", "c2": "..."}}</code>.</p> |


## Runs

This action is a `composite` action.

## Usage

```yaml
- uses: camunda/infraex-common-config/.github/actions/matrix-outputs-read@main
  with:
    matrix-step-name:
    # Name passed as `matrix-step-name` to the matching write step. It is also the
    # artifact file name this action looks for.
    #
    # Required: true
    # Default: ""
```
