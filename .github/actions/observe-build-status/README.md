# Observe Build Status

## Description

Reports a job's outcome, duration and runner resource usage to CI Analytics (BigQuery),
for the InfraEx repository fleet.

Pair it with `camunda/infra-global-github-actions/start-build-monitor`, which must be the
**first** step of the job: it starts the resource sampler and drops the `/tmp/_monitor-start.pid`
timestamp file this action reads to compute the duration. Call this action as the **last**
step, with `if: always()` so failed and cancelled jobs are reported too.

Every step here is best-effort: the action reports what it can and never fails the job it is
measuring, so callers do not have to remember `continue-on-error: true`.

The CI Analytics service-account key is read from Vault at
`secret/data/products/infrastructure-experience/ci/ci-analytics`, which is a constant for the
whole InfraEx fleet — callers only supply credentials, never the path. Authenticate with
either JWT/OIDC (preferred: no long-lived credential) or AppRole. Leave `vault_addr` empty to
disable reporting entirely, so forks and local runs are unaffected.


## Inputs

| name | description | required | default |
| --- | --- | --- | --- |
| `build_status` | <p>Outcome of the job, normally <code>${{ job.status }}</code>: one of <code>success</code>, <code>failure</code>, <code>cancelled</code>.</p> | `true` | `""` |
| `job_name` | <p>Name recorded in the <code>job_name</code> column. Defaults to <code>$GITHUB_JOB</code> when omitted, which is ambiguous for matrix jobs — pass <code>${{ github.job }}/${{ matrix.&lt;key&gt; }}</code> so each matrix leg is distinguishable. Use <code>/</code> as the separator consistently: matrix values often contain <code>-</code>, which makes the field unsplittable otherwise.</p> | `false` | `""` |
| `user_reason` | <p>Optional string (200 chars max) categorising why the job ended this way, e.g. <code>flaky-tests</code>.</p> | `false` | `""` |
| `user_description` | <p>Optional string (1000 chars max) detailing <code>user_reason</code>, e.g. the list of flaky tests.</p> | `false` | `""` |
| `vault_addr` | <p>Vault server URL. Required for either authentication method; leave empty to disable reporting entirely.</p> | `false` | `""` |
| `vault_jwt_path` | <p>Vault JWT auth mount path. JWT/OIDC authentication, preferred over AppRole.</p> | `false` | `""` |
| `vault_jwt_role` | <p>Vault JWT auth role. Setting it selects JWT authentication, and together with <code>vault_addr</code> it is the complete JWT credential set. The calling job needs <code>permissions: id-token: write</code> for GitHub to mint the OIDC token.</p> | `false` | `""` |
| `vault_jwt_audience` | <p>Vault JWT GitHub audience.</p> | `false` | `""` |
| `vault_role_id` | <p>Vault AppRole role ID. Only used when <code>vault_jwt_role</code> is empty, and only together with <code>vault_secret_id</code> — both are required for the AppRole set to count as complete. Prefer JWT, which needs no long-lived credential stored as a repository secret.</p> | `false` | `""` |
| `vault_secret_id` | <p>Vault AppRole secret ID. See <code>vault_role_id</code>.</p> | `false` | `""` |


## Runs

This action is a `composite` action.

## Usage

```yaml
- uses: camunda/infraex-common-config/.github/actions/observe-build-status@main
  with:
    build_status:
    # Outcome of the job, normally `${{ job.status }}`: one of `success`, `failure`, `cancelled`.
    #
    # Required: true
    # Default: ""

    job_name:
    # Name recorded in the `job_name` column. Defaults to `$GITHUB_JOB` when omitted, which is
    # ambiguous for matrix jobs — pass `${{ github.job }}/${{ matrix.<key> }}` so each matrix
    # leg is distinguishable. Use `/` as the separator consistently: matrix values often
    # contain `-`, which makes the field unsplittable otherwise.
    #
    # Required: false
    # Default: ""

    user_reason:
    # Optional string (200 chars max) categorising why the job ended this way, e.g. `flaky-tests`.
    #
    # Required: false
    # Default: ""

    user_description:
    # Optional string (1000 chars max) detailing `user_reason`, e.g. the list of flaky tests.
    #
    # Required: false
    # Default: ""

    vault_addr:
    # Vault server URL. Required for either authentication method; leave empty to disable
    # reporting entirely.
    #
    # Required: false
    # Default: ""

    vault_jwt_path:
    # Vault JWT auth mount path. JWT/OIDC authentication, preferred over AppRole.
    #
    # Required: false
    # Default: ""

    vault_jwt_role:
    # Vault JWT auth role. Setting it selects JWT authentication, and together with
    # `vault_addr` it is the complete JWT credential set. The calling job needs
    # `permissions: id-token: write` for GitHub to mint the OIDC token.
    #
    # Required: false
    # Default: ""

    vault_jwt_audience:
    # Vault JWT GitHub audience.
    #
    # Required: false
    # Default: ""

    vault_role_id:
    # Vault AppRole role ID. Only used when `vault_jwt_role` is empty, and only together with
    # `vault_secret_id` — both are required for the AppRole set to count as complete. Prefer
    # JWT, which needs no long-lived credential stored as a repository secret.
    #
    # Required: false
    # Default: ""

    vault_secret_id:
    # Vault AppRole secret ID. See `vault_role_id`.
    #
    # Required: false
    # Default: ""
```
