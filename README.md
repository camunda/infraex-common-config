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

### Reviewed automerge (opt-in, not active by default)

[`reviewed-automerge.json5`](reviewed-automerge.json5) lets Renovate perform the final
merge **after human approval and required checks**. It does not auto-approve, bypass
branch protection, restore the old merge workflow, or give CI a merge credential.
This repository does not opt in. Adopting the preset is a separate, reviewed change
in each consuming repository, after the checklist below is satisfied.

The initial allowlist is deliberately narrow: `hashicorp/aws` and
`hashicorp/azurerm` **patch** updates extracted by the native Terraform manager from
`.tf` files outside `.github/` and `scripts/`. Eligible updates leave the shared patch
group and get individual PRs. Every other package, manager, update type, lockfile
maintenance and security-remediation PR stays manual. A provider is executable code,
not a trusted dependency just because its namespace is allowlisted.

Eligible releases wait **seven days**, with missing release timestamps blocking
eligibility. Renovate merges PRs itself (`platformAutomerge: false`), checks test
statuses, and rebases behind-base branches. Native GitHub automerge need not be
enabled. The age window applies before Renovate proposes a new eligible release;
it does not retroactively quarantine an existing PR or stop someone opening one
manually. Review or close pre-existing candidates during rollout.

#### Activation checklist

Do not adopt until a maintainer has verified all of these in the consumer:

1. Require at least one relevant test **by exact check name**, plus the security and
   eligibility checks below. Require an up-to-date branch, human approval of the
   latest push, and dismissal of stale approvals. Renovate must have no ruleset or
   branch-protection bypass. A setting saying "require checks" with an empty check
   list is not sufficient. At investigation time, this repository had that empty
   list; this PR does not change repository settings.
2. Audit **every** PR job before enabling automation. Dependency installs, Terraform
   providers, hooks and scripts must run on disposable runners without write tokens,
   persistent checkout credentials, cloud secrets, or Vault roles that can retrieve
   the App key. Protect privileged workflows and their inputs. See the
   [CI trust boundary](#ci-trust-boundary); passing a scan does not repair a credential
   boundary. Do not reuse untrusted artifacts or executable caches in privileged jobs.
3. Add a required, trusted-base eligibility check for the exact PR head SHA. Read the
   complete diff as **data**, without checking out or executing PR code. Verify the
   Renovate App identity, same-repository head, allowlisted provider/version changes,
   and the complete file set, including generated `.terraform.lock.hcl` changes.
   Reject extra HCL logic, new sources/registries, scripts, workflows, binaries,
   symlinks, truncated diffs and fetch errors. A Renovate `matchFileNames` rule matches
   the dependency's manifest, **not every changed file**. Recheck after every push;
   never authorize from a branch prefix, PR title, label or earlier SHA's result.
4. Require fresh scans of the proposed artifacts with supported vulnerability and
   malware coverage, plus workflow/static checks where relevant. Fail on scanner
   errors, missing reports or missing coverage rather than converting them to green.
   The repository's existing `zizmor` hook checks Actions configuration, not Terraform
   provider binaries. Do not claim an OSV/Trivy/Semgrep result covers artifacts or
   ecosystems it did not inspect. If provider artifact coverage cannot be supplied,
   keep these updates manual. Confirm the incident owner's acceptance before rollout.

After those checks, add the overlay **after** the default preset:

```json5
{
  $schema: "https://docs.renovatebot.com/renovate-schema.json",
  extends: [
    "github>camunda/infraex-common-config:default.json5",
    "github>camunda/infraex-common-config:reviewed-automerge.json5",
  ],
}
```

Audit the consumer's resolved configuration: local/package rules can override a
shared preset. Do not append broad `automerge: true` rules. Pilot one repository;
confirm a recent release and missing timestamp are held, unrelated changes stay
manual, failing/missing checks block merging, and a new push needs fresh approval.
To roll back, remove the overlay, inspect already-open candidates and disable any
separately enabled platform automerge. No workflow or repository setting is changed
by removing this preset.

#### Why not restore `automerge-global`?

[PR #542](https://github.com/camunda/infraex-common-config/pull/542) removed the App-token
approval/merge steps for INC-7027. [Commit a236223](https://github.com/camunda/infraex-common-config/commit/a236223e4c63b67b404333351095901869a55767)
later deleted the leftover checkout and three-hour waiter, which could no longer merge.
Its removal did not disable an active merge path. The commit records two archived,
SHA-pinned callers, `camunda-tf-eks-module` and `camunda-tf-rosa`; deleting a file on
`main` does not revoke those historical versions. Audit and replace those pins
**before** re-enabling their Actions if either repository is unarchived.

The incident involved malicious dependency code executing **before merge**. Small
diffs, trusted bot authors, patch versions, release age and high merge confidence are
useful triage signals, not authorization or proof of safety. Malware can fit in one
line or arrive through a signed upstream release. Vulnerability databases lag new
attacks; a clean scan cannot prove absence. Text from PRs, release notes and dependency
sources is also untrusted input to any LLM reviewer: its verdict must never grant
approval, tokens or a merge. Human review plus enforced isolation remains necessary.

Policy tests run Renovate's actual rule matcher, Terraform extractor and release-age
logic, with the same exact Renovate version as the schema validator:

```shell
just test-reviewed-automerge
```

These tests verify configuration behavior, not live GitHub protections, malware
detection, or an end-to-end merge. Those are mandatory consumer rollout checks.
See Renovate's [automerge](https://docs.renovatebot.com/key-concepts/automerge/),
[release-age](https://docs.renovatebot.com/configuration-options/#minimumreleaseage)
and [Terraform provider datasource](https://docs.renovatebot.com/modules/datasource/terraform-provider/)
documentation for platform and timestamp limitations.

### Version Annotations

The preset ships a custom manager that updates versions written anywhere -- shell scripts,
Terraform, Go, YAML, `justfile`, `.tool-versions` -- when they are annotated with a comment
naming the datasource and the dependency:

```yaml
# renovate: datasource=docker depName=camunda/keycloak versioning=regex:^quay-optimized-(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)$
image: docker.io/camunda/keycloak:quay-optimized-26.7.0
```

Two rules:

- **The version must be on the line immediately after the annotation.** A comment in between
  breaks the match, and the annotation then updates nothing.
- **The version must be the last quoted run on its line, or the last thing on the line.**
  `default = "1.36" # major.minor` works, `VERSION=1.36 extra` does not.

Beyond `datasource` and `depName`, the annotation accepts `versioning`, `registryUrl`,
`extractVersion` and `depType`, in any order. Unknown `key=value` pairs are ignored.

The value can be quoted (`VERSION="1.2.3"`, `default = "~> 1.15.0"`,
`helpers.GetEnv("HELM_CHART_VERSION", "14.8.0")`), unquoted behind a `=` or `:`
(`VERSION=1.2.3`, `version: 1.2.3`, `image: repo/name:tag`, with an `@sha256:` digest
updated alongside the tag), or separated by whitespace only (`.tool-versions` entries,
YAML list items). A leading `release-` is stripped from the version, since it belongs to
the file rather than to what the datasource publishes.

Neither failure announces itself -- an annotation that matches nothing looks exactly like one
that works, and one that matches twice only shows up as a duplicate line on the Dependency
Dashboard -- so `lint-global` checks them. The `renovate annotations` job applies this
preset's own `customManagers` to the calling repository and fails on any annotation that
does not extract exactly one dependency.

It also warns, without failing, when the version an annotation extracts cannot match its own
`versioning=regex:` -- `15-dev-latest` against `^15(\.(?<minor>\d+))?(\.(?<patch>\d+))?$`,
for instance. Renovate drops such a value as invalid, so the annotation updates nothing; that
is sometimes deliberate and temporary, which is why it warns rather than blocks. Locally:

```shell
just check-renovate-annotations   # this repository
python3 .github/workflows/scripts/check_renovate_annotations.py --preset default.json5 --root ../some-other-repo
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
