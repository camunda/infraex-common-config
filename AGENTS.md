# AGENTS.md

## Role and path map

This repository holds shared configuration for other Camunda repositories: the Renovate preset, reusable GitHub workflows, composite actions and pre-commit hooks. It also runs the cleanup of the AWS and Azure CI regions and the audit of the permanent regions. The [README](README.md) explains how a caller uses each part.

No path needs a review from a specific team: the repository has no `CODEOWNERS` file.

- `default.json5`: the shared Renovate preset. Callers extend `github>camunda/infraex-common-config:default.json5`.
- `.github/workflows/`: the `*-global.yml` files are reusable workflows for callers. The other files run in this repository. See "Architecture".
  - `.github/workflows/crev-review.yml`: written by camunda/infraex-terraform. Source: [crev review](README.md#crev-review). Renovate skips it (`default.json5`).
  - `.github/workflows/scripts/`: the scripts of the cleanup workflows and the two Renovate checkers of `lint-global.yml`.
  - `.github/workflows/scripts/tests/`: test scripts, stubs and fixtures of those scripts.
- `.github/actions/`: the composite actions `asdf-install-tooling`, `report-failure-on-slack` and `report-warning-on-slack`. Each `README.md` there is written by the pre-commit hook `update-action-readmes-docker`.
- `.github/config/`: `cloud_nuke_exclusions.yml` of the AWS cleanup and `permanent_resources_allowlist.yml` of the audit.
- `.github/renovate.json5`: the Renovate config of this repository. It extends `default.json5`.
- `.github/zizmor.yml`: the configuration of the `zizmor` hook in `.pre-commit-config.yaml`.
- `baseline/`: the hook list and the linter configurations that the job `baseline` of `lint-global.yml` runs on each caller. See [Baseline](README.md#baseline).
- `pre_commit_hooks/`: the script of the hook `update-action-readmes-docker`.
- `.pre-commit-hooks.yaml`: the hooks that this repository publishes, `update-action-readmes-docker` and `action-pin-spacing`.
- `.pre-commit-config.yaml`: the lint hooks of this repository.
- `.tool-versions`: asdf versions of `just`, `pre-commit`, Python, `shellcheck` and `yq`.
- `justfile`: `just` recipes that install the tools, run the Renovate annotation checker and run three test scripts.

## Architecture

- Callers extend `default.json5` of `main`, so a preset change reaches them at the merge. The [README](README.md) examples pin the reusable workflows to a release SHA. `release.yml` calls `release-global.yml` on weekdays and publishes a patch release when `main` has new commits. A minor or major release is a `workflow_dispatch` input.
- `lint-self-test.yml` calls `lint-global.yml` of the same commit, so a PR runs its own version. `lint-global.yml` uses `asdf-install-tooling` and `commit-patch-global.yml` of release `3.0.2`, and `.pre-commit-config.yaml` uses the hooks of release `3.0.2`.
- The jobs of `lint-global.yml`: `lint` runs the pre-commit hooks of the caller, `renovate-annotations` and `renovate-lookup` check the Renovate annotations, `baseline` runs `baseline/.pre-commit-config.yaml`, and `autofix-apply` commits the fixes of `lint` for the actors in `autofix-actor`.
- A job that runs repository code holds no write credential. `commit-patch-global.yml` applies the patch of such a job and commits it with an App token. See [CI trust boundary](README.md#ci-trust-boundary).
- `aws_nightly_cleanup.yml` and `azure_nightly_cleanup.yml` run each day and delete old resources in the CI regions and, on Saturday, in the weekly regions. On a PR that changes their files, they log in to the cloud and run with `DRY_RUN=true`. `permanent_resources_audit.yml` lists the resources of the permanent regions each Sunday and posts a summary to Slack. The regions: [README](README.md#region-usage-for-cloud-providers).
- `crev-review.yml` calls `crev-review-global.yml` of `main` when a PR comment is `/crev` or starts with `/crev `.

## Commit rules

- Commit titles and PR titles use Conventional Commits: `type: summary` or `type(scope): summary`, with `!` for a breaking change. Source: the titles of the last 20 commits on `main`. They use the types `feat`, `fix`, `chore` and `ci`, and scopes such as `crev-review-global`, `renovate`, `deps` and `lint`.
- No workflow or hook checks commit titles or PR titles.
- Renovate writes `chore(deps): ...` titles: `default.json5` extends `:semanticCommits`.

## Commands

- `pre-commit run --all-files` with `.pre-commit-config.yaml`, check `lint-self-test / pre-commit`: `lint-self-test.yml` calls `lint-global.yml` on each PR and each push to `main`. The hooks check file hygiene, workflows (`action-pin-spacing`, `actionlint`, `zizmor`), the Renovate config (`renovate-config-validator`), YAML format (`yamlfmt`) and the action READMEs (`update-action-readmes-docker`, needs Docker).
- `pre-commit run --config .infraex-common-config/baseline/.pre-commit-config.yaml --all-files`, check `lint-self-test / baseline`: `lint-global.yml`, called by `lint-self-test.yml`. `.infraex-common-config` is this repository at the commit of the workflow. The hooks `actionlint-docker`, `hadolint`, `terraform-fmt` and `tflint` need Docker.
- `check_renovate_annotations.py --preset .infraex-common-config/default.json5 --root .`, check `lint-self-test / renovate annotations`: `lint-global.yml`, called by `lint-self-test.yml`. Locally: `just check-renovate-annotations`.
- Renovate in lookup mode, then `check_renovate_lookup.py --report <report> --root .`, check `lint-self-test / renovate lookup`: `lint-global.yml`. `lint-self-test.yml` turns it on with `check-renovate-lookup: true`.
- When `renovate[bot]` or `smx-misc[bot]` triggers the run on a same-repository PR, `lint-global.yml` commits the pre-commit fixes with the title `chore: update files from pre-commit run`.
- `todos` on the lines that a PR adds: `todo-checker.yml` calls `todo-checker-global.yml`. It comments on each new TODO and fails while a comment has no thumbs-down reaction.
- `./.github/workflows/scripts/tests/check_renovate_annotations_test.sh` (with `PYTHON` set to a Python that has `json5`), `./.github/workflows/scripts/tests/check_renovate_lookup_test.sh` and `bash .github/actions/asdf-install-tooling/test.sh`, on each PR: `test.yml`. `just test-renovate-annotations` and `just test-renovate-lookup` run the first two.
- `./.github/workflows/scripts/tests/azure_rg_cleanup_test.sh`, on a PR that changes the Azure sweep, its workflow or `.github/workflows/scripts/tests/`: `azure_nightly_cleanup.yml`. `just test-azure-cleanup` runs it.
- `./.github/workflows/scripts/tests/aws_global_db_teardown_test.sh`, on a PR that changes the AWS cleanup files: `aws_nightly_cleanup.yml`. The job logs in to AWS before the test.
- `.tool-versions` pins `pre-commit`, Python, `just`, `shellcheck` and `yq`. The job `lint` installs them with asdf and fails when `pre-commit` does not match `.tool-versions`. `lint-global.yml` and `test.yml` pin `json5`. No file pins Docker.
