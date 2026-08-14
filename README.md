# infraex-common-config

Common configurations like Renovate and GitHub actions owned by the InfraEx team.

Required to be public to allow usage in public-facing repositories.

## Usage

Create a file `.github/renovate.json5`:

```json5
{
  $schema: "https://docs.renovatebot.com/renovate-schema.json",
  extends: ["github>camunda/infraex-common-config:default.json5"],
}
```

### Bucket Usage

By default, any bucket not listed in <https://github.com/camunda/infraex-terraform/blob/main/aws/s3-buckets.yml> will be deleted during the daily cleanup.
For temporary tests or work, use `general-purpose-bucket-that-will-not-be-deleted`, but ensure manual cleanup to avoid data accumulation.

### Region Usage for Cloud Providers

#### CI Regions

CI Regions are designated specifically for **Continuous Integration (CI) tests**. Resources in these regions are deleted nightly as part of routine maintenance. Please ensure that these regions are used exclusively for CI-related tests.

##### AWS Regions

| Region     | Identifier | Cleanup Schedule |
|------------|------------|------------------|
| EU (London)| eu-west-2  | Daily @5AM       |
| EU (Paris) | eu-west-3  | Daily @5AM       |
| EU (Zurich)| eu-central-2 | Daily @5AM     |

`eu-central-2` is the third daily region, added for the multi-region reference architectures: three regions is the smallest topology where a Camunda Zeebe cluster keeps its Raft quorum after losing one region.

##### Azure Regions

| Region         | Identifier   | Cleanup Schedule       |
|----------------|--------------|-------------------------|
| Sweden Central | swedencentral| Daily @5AM              |

#### Weekly Work Regions

Weekly Work Regions provide a **temporary environment** for projects that require resources to be retained for the duration of a work week. These regions are ideal for projects that span multiple days without requiring nightly cleanup.

To keep the environment organized, all resources in these regions are automatically cleaned up every week. This ensures that resources do not persist beyond their intended use, making the regions ready for new projects each week.

##### AWS Regions

| Region              | Identifier   | Cleanup Schedule |
|---------------------|--------------|------------------|
| EU (Stockholm)      | eu-north-1   | Saturday @5AM    |
| EU (Milan)          | eu-south-1   | Saturday @5AM    |
| US East (N. Virginia) | us-east-1 | Saturday @5AM    |
| US East (Ohio) | us-east-2 | Saturday @5AM    |

`eu-south-1` gives multi-region work a fourth region that survives a work week.

##### Azure Regions

| Region         | Identifier   | Cleanup Schedule          |
|----------------|--------------|-------------------------|
| Spain Central  | spaincentral | Saturday @5AM     |


#### Opt-in Regions

`eu-central-2` (Zurich) and `eu-south-1` (Milan) are AWS **opt-in** regions: regions launched after 20 March 2019 are disabled by default and must be enabled on the account before anything can be created in them. Every other region above predates that cutoff and is enabled by default, which AWS does not allow to be disabled.

Both are enabled, declared in [infraex-terraform `aws/regions.tf`](https://github.com/camunda/infraex-terraform/blob/main/aws/regions.tf) rather than clicked in the console, so the nightly drift detection notices if one is turned off again.

Enable an opt-in region there **before** adding it to a cleanup schedule. The cleanup does not tolerate a region it cannot reach: it fails loudly, which is deliberate. A region that stops being reachable is not being swept, and a disabled region keeps incurring charges for resources that are no longer visible — that must page someone, not degrade quietly.

#### Permanent Regions

Permanent Regions are designated for **persistent resources** that fall outside the scope of CI testing. This includes resources such as S3 buckets, Lambda functions, and reference architectures, all of which are critical for ongoing infrastructure and operational requirements.

To facilitate resource management, we distinguish between **reference architectures** and other permanent resources, allowing for easier cleanup and a clearer understanding of resource allocation.

All configurations are maintained in [Terraform](https://github.com/camunda/infraex-terraform/tree/main/aws).

##### AWS Regions

| Region         | Identifier   | Use Case                |
|----------------|--------------|-------------------------|
| EU (Frankfurt) | eu-central-1 | Permanent resources     |
| EU (Ireland)   | eu-west-1    | Reference architectures |

##### Azure Regions

| Region         | Identifier   | Use Case                |
|----------------|--------------|-------------------------|
| West Europe    | westeurope   | Permanent resources     |

##### Weekly Permanent Resources Audit

A weekly audit runs every **Sunday at 22:00 UTC** to identify all resources in permanent regions. This audit:
- Runs cleanup tools in **dry-run mode** (no resources are deleted)
- Posts a summary of discovered resources to Slack channel `#internal` (C05S0M7KG6A)
- Helps track resource growth and identify potential orphaned resources

This provides visibility into the permanent infrastructure without affecting any resources.

###### S3 buckets (global audit)

S3 uses a single, account-wide global namespace and every bucket is pinned to one home region. The per-region audit therefore **excludes S3** and a dedicated `aws-s3-global-audit` job lists all buckets once (`aws s3api list-buckets`) and compares them against the allowlist.

This avoids a blind spot: auditing S3 per-region would silently skip any bucket whose home region is excluded from the per-region matrix (e.g. `us-east-1`, see `AWS_EXCLUDED_REGIONS`). Approved buckets are listed under `aws.global.s3` in [`.github/config/permanent_resources_allowlist.yml`](.github/config/permanent_resources_allowlist.yml), regardless of region.

###### ELBv2 target groups (quota headroom)

cloud-nuke does not report ELBv2 target groups. Target groups created for Kubernetes Services of type `LoadBalancer` (OpenShift router, ingress-nginx, Contour, Submariner, ...) are left behind when their load balancer or cluster is deleted, and they silently accumulate until they reach the regional **Target Groups per Region** quota (default 3000). Hitting the cap breaks new ingress load balancer creation with a `TooManyTargetGroups` error.

The audit therefore counts, per permanent region, the total and orphaned (unattached) target groups and compares them against the regional quota. It posts a Slack warning when utilization reaches `ELBV2_TG_USAGE_WARN_PERCENT` (default 80%) of the quota, or when the number of orphaned target groups reaches `ELBV2_TG_ORPHAN_WARN` (default 50). In the CI regions, these orphaned target groups are deleted automatically by the [nightly cleanup](.github/workflows/aws_nightly_cleanup.yml).

## CI trust boundary

A job that runs repository-supplied code must not hold a GitHub write credential.

"Repository-supplied code" is broader than it looks. It is not only the scripts in the
tree: it is asdf plugins named by `.tool-versions`, pre-commit hooks named by
`.pre-commit-config.yaml`, terraform providers, `go mod tidy`, anything a build resolves
from a lockfile. On a `pull_request` run all of it comes from the contributor's branch,
which for a Renovate pull request means it comes from whatever the dependency update
pulled in. INC-7027 was a malicious payload in a dependency update; it only had to be
proposed, not merged.

Pinning actions to a SHA does not address this. Pinning fixes *which* code runs, not what
that code can reach once it runs.

### The rule

- A job that runs repository code holds no App token, no `secrets.*` it does not strictly
  need, and sets `persist-credentials: false` so its own `GITHUB_TOKEN` is not left in
  `.git/config` for that code to find.
- The credential lives in a separate job that runs only SHA-pinned third-party actions.
- Installation tokens name `repositories:` explicitly rather than relying on the token
  action's default.
- The token is minted last, after any untrusted input has been handled, so it is not alive
  while that happens and no hook can have planted a `PATH` shim or `BASH_ENV` first.
- Work crosses the boundary as data, not as execution: the unprivileged job emits a patch
  artifact, the privileged one applies it. Applying a patch does not execute it, and
  `git apply` refuses paths outside the work tree.

### The shared implementation

[`commit-patch-global.yml`](.github/workflows/commit-patch-global.yml) is the privileged
half. It downloads the patch, refuses what it should not commit, applies it, mints a
repository-scoped token and commits through the API.

```yaml
jobs:
    build:
        # runs the repository's code; no write credential, no App token
        # uploads its result as a patch artifact
        ...

    commit:
        needs: build
        if: needs.build.outputs.has_changes == 'true'
        uses: camunda/infraex-common-config/.github/workflows/commit-patch-global.yml@<sha>
        with:
            artifact-name: ${{ needs.build.outputs.artifact_name }}
            head-sha: ${{ github.event.pull_request.head.sha }}
            head-ref: ${{ github.event.pull_request.head.ref }}
            commit-message: 'chore: apply automated fixes'
        secrets:
            vault-addr: ${{ secrets.VAULT_ADDR }}
            vault-role-id: ${{ secrets.VAULT_ROLE_ID }}
            vault-secret-id: ${{ secrets.VAULT_SECRET_ID }}
```

Tune `max-patch-bytes`, `max-changed-lines` and `refuse-paths-regex` to the producing job:
a formatter's output should stay small and human-readable, a generator rewriting a fixture
corpus legitimately runs to megabytes. The guards bound a runaway and refuse patches the
commit step would silently corrupt; they are not an injection defence, since a hostile
patch needs three lines rather than a large one.

### What this does not fix

Two residuals, worth stating so the boundary is not read as stronger than it is.

On `pull_request`, GitHub evaluates the workflow file **from the head branch**. Anyone
with push access can therefore edit the privileged job itself. That is irreducible for any
workflow holding a secret, and is bounded by fork pull requests receiving no secrets at
all, plus review of anything touching `.github/`.

If the unprivileged job keeps a Vault approle for cloud credentials, and that approle can
read the path where the GitHub App private key lives, branch code that exfiltrates it can
mint a token regardless. Splitting the jobs removes the token sitting next to the code; it
does not remove that path. Where it applies, scope the Vault role away from the App key.
