# Notify Slack of Urgent Review

## Description

Send an urgent pull request review request to Slack using a token from Vault.

## Example workflow

```yaml
name: Urgent review notification

on:
    pull_request_target:
        types: [labeled]

permissions: {}

jobs:
    notify:
        if: github.event.label.name == 'review:urgent' && !github.event.pull_request.draft
        runs-on: ubuntu-latest
        permissions:
            id-token: write
        concurrency:
            group: review-urgent-${{ github.event.pull_request.number }}
            cancel-in-progress: true
        steps:
            - uses: camunda/infraex-common-config/.github/actions/notify-review-on-slack@<sha>
              with:
                  vault_addr: ${{ secrets.VAULT_ADDR }}
                  vault_jwt_path: ${{ secrets.VAULT_JWT_PATH || vars.VAULT_JWT_PATH }}
                  vault_jwt_role: ${{ secrets.VAULT_JWT_ROLE || vars.VAULT_JWT_ROLE }}
                  vault_jwt_audience: ${{ secrets.VAULT_JWT_AUDIENCE || vars.VAULT_JWT_AUDIENCE }}
                  slack_channel_id: <channel-id>
                  slack_mention: '<!subteam^ID>'
```

`pull_request_target` gives the job access to secrets. Never check out the pull request head in this job.
Do not put embargoed CVE details in the pull request title while it carries `review:urgent`.
Pin the action to a commit SHA as shown above, not the generated `@main` example below.
The Vault role must be able to read the Slack token secret, and the Slack bot needs `chat:write` and access to the channel.


## Inputs

| name | description | required | default |
| --- | --- | --- | --- |
| `vault_addr` | <p>The address of the Vault instance.</p> | `true` | `""` |
| `vault_jwt_path` | <p>The Vault auth backend path for GitHub OIDC (JWT) authentication. The calling job needs the <code>id-token: write</code> permission.</p> | `true` | `""` |
| `vault_jwt_role` | <p>The Vault role for GitHub OIDC (JWT) authentication.</p> | `true` | `""` |
| `vault_jwt_audience` | <p>The GitHub OIDC audience for Vault (JWT) authentication.</p> | `true` | `""` |
| `vault_secret_path` | <p>The Vault path holding the Slack bot token. Callers outside InfraEx must point it at their own team's secret, never at the InfraEx one.</p> | `false` | `secret/data/products/infrastructure-experience/ci/common` |
| `vault_slack_token_key` | <p>The key of the Slack bot token in the Vault secret.</p> | `false` | `SLACK_BOT_TOKEN` |
| `slack_channel_id` | <p>The Slack channel ID where the notification will be sent.</p> | `true` | `""` |
| `slack_mention` | <p>An optional Slack mention, such as a subteam mention.</p> | `false` | `""` |


## Runs

This action is a `composite` action.

## Usage

```yaml
- uses: camunda/infraex-common-config/.github/actions/notify-review-on-slack@main
  with:
    vault_addr:
    # The address of the Vault instance.
    #
    # Required: true
    # Default: ""

    vault_jwt_path:
    # The Vault auth backend path for GitHub OIDC (JWT) authentication.
    # The calling job needs the `id-token: write` permission.
    #
    # Required: true
    # Default: ""

    vault_jwt_role:
    # The Vault role for GitHub OIDC (JWT) authentication.
    #
    # Required: true
    # Default: ""

    vault_jwt_audience:
    # The GitHub OIDC audience for Vault (JWT) authentication.
    #
    # Required: true
    # Default: ""

    vault_secret_path:
    # The Vault path holding the Slack bot token. Callers outside InfraEx must point it
    # at their own team's secret, never at the InfraEx one.
    #
    # Required: false
    # Default: secret/data/products/infrastructure-experience/ci/common

    vault_slack_token_key:
    # The key of the Slack bot token in the Vault secret.
    #
    # Required: false
    # Default: SLACK_BOT_TOKEN

    slack_channel_id:
    # The Slack channel ID where the notification will be sent.
    #
    # Required: true
    # Default: ""

    slack_mention:
    # An optional Slack mention, such as a subteam mention.
    #
    # Required: false
    # Default: ""
```
