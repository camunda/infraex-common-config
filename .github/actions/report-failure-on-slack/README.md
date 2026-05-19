# Report Failure and Notify Slack

## Description

This GitHub composite action imports secrets from HashiCorp Vault and sends a Slack notification in case of a workflow failure.
It helps automate incident reporting and ensures timely notifications to the relevant Slack channel.
Use it with `if: failure()`


## Inputs

| name | description | required | default |
| --- | --- | --- | --- |
| `vault_addr` | <p>The address of the Vault instance</p> | `true` | `""` |
| `vault_role_id` | <p>The role ID used for authentication with Vault</p> | `true` | `""` |
| `vault_secret_id` | <p>The secret ID used for authentication with Vault</p> | `true` | `""` |
| `slack_channel_id` | <p>The Slack channel ID where the notification will be sent.</p> | `false` | `C076N4G1162` |
| `slack_mention_people` | <p>The Slack people to mention in the notification.</p> | `false` | `@infraex-medic` |
| `disable_silence_check` | <p>Disable silence check. By default, alerts can be disabled by creating an issue in the repository with the label alert-management and with the title: silence: name of your workflow</p> | `false` | `false` |
| `branch` | <p>The branch the workflow is testing. Used to add severity context to the Slack alert. Defaults to auto-detection: <code>github.base_ref</code> for PRs (target branch), <code>github.ref_name</code> for push/schedule events. Alerts on <code>stable/*</code> branches include a prominent severity marker.</p> | `false` | `""` |


## Runs

This action is a `composite` action.

## Usage

```yaml
- uses: camunda/infraex-common-config/.github/actions/report-failure-on-slack@main
  with:
    vault_addr:
    # The address of the Vault instance
    #
    # Required: true
    # Default: ""

    vault_role_id:
    # The role ID used for authentication with Vault
    #
    # Required: true
    # Default: ""

    vault_secret_id:
    # The secret ID used for authentication with Vault
    #
    # Required: true
    # Default: ""

    slack_channel_id:
    # The Slack channel ID where the notification will be sent.
    #
    # Required: false
    # Default: C076N4G1162

    slack_mention_people:
    # The Slack people to mention in the notification.
    #
    # Required: false
    # Default: @infraex-medic

    disable_silence_check:
    # Disable silence check.
    # By default, alerts can be disabled by creating an issue in the repository
    # with the label alert-management and with the title:
    # silence: name of your workflow
    #
    # Required: false
    # Default: false

    branch:
    # The branch the workflow is testing. Used to add severity context to the Slack alert.
    # Defaults to auto-detection: `github.base_ref` for PRs (target branch),
    # `github.ref_name` for push/schedule events.
    # Alerts on `stable/*` branches include a prominent severity marker.
    #
    # Required: false
    # Default: ""
```
