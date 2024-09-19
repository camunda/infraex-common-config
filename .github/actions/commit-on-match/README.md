# Commit Changes If Actor Matches

## Description

Checks if the pull request is from a specific actor, generates a GitHub token,
and commits changes if the actor matches.


## Inputs

| name | description | required | default |
| --- | --- | --- | --- |
| `actor` | <p>The GitHub actor to check (e.g., renovate).</p> | `true` | `""` |
| `commit_message` | <p>The commit message to use.</p> | `true` | `chore: update files from gha` |
| `github_app_id_vault_key` | <p>Vault key for GitHub App ID.</p> | `false` | `GITHUB_APP_ID` |
| `github_app_id_vault_path` | <p>Vault path for GitHub App ID.</p> | `false` | `secret/data/products/infrastructure-experience/ci/common` |
| `github_app_private_key_vault_key` | <p>Vault key for GitHub App Private Key.</p> | `false` | `GITHUB_APP_PRIVATE_KEY` |
| `github_app_private_key_vault_path` | <p>Vault path for GitHub App Private Key.</p> | `false` | `secret/data/products/infrastructure-experience/ci/common` |
| `vault_auth_method` | <p>Vault authentication method.</p> | `false` | `approle` |
| `vault_auth_role_id` | <p>Vault role ID for authentication.</p> | `true` | `""` |
| `vault_auth_secret_id` | <p>Vault secret ID for authentication.</p> | `true` | `""` |
| `vault_url` | <p>Vault URL.</p> | `true` | `""` |


## Runs

This action is a `composite` action.

## Usage

```yaml
- uses: camunda/infraex-common-config/.github/actions/commit-on-match@main
  with:
    actor:
    # The GitHub actor to check (e.g., renovate).
    #
    # Required: true
    # Default: ""

    commit_message:
    # The commit message to use.
    #
    # Required: true
    # Default: chore: update files from gha

    github_app_id_vault_key:
    # Vault key for GitHub App ID.
    #
    # Required: false
    # Default: GITHUB_APP_ID

    github_app_id_vault_path:
    # Vault path for GitHub App ID.
    #
    # Required: false
    # Default: secret/data/products/infrastructure-experience/ci/common

    github_app_private_key_vault_key:
    # Vault key for GitHub App Private Key.
    #
    # Required: false
    # Default: GITHUB_APP_PRIVATE_KEY

    github_app_private_key_vault_path:
    # Vault path for GitHub App Private Key.
    #
    # Required: false
    # Default: secret/data/products/infrastructure-experience/ci/common

    vault_auth_method:
    # Vault authentication method.
    #
    # Required: false
    # Default: approle

    vault_auth_role_id:
    # Vault role ID for authentication.
    #
    # Required: true
    # Default: ""

    vault_auth_secret_id:
    # Vault secret ID for authentication.
    #
    # Required: true
    # Default: ""

    vault_url:
    # Vault URL.
    #
    # Required: true
    # Default: ""
```
