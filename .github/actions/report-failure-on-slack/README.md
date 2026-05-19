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
| `repeat_threshold` | <p>Number of consecutive failures at or above which the alert is prefixed with a "REPEATED FAILURE (Nx)" escalation marker. Set to <code>0</code> to disable the prefix (the count is still tracked and exposed as the <code>consecutive_failures</code> output).</p> | `false` | `3` |
| `stale_failure_days` | <p>Window (in days) used to decide whether a stored failure count is still "consecutive". If the previous failure is older than this, the counter resets to 1. This is a coarse stand-in for an on-success reset, which we cannot implement from a failure-only action.</p> | `false` | `7` |
| `disable_consecutive_failure_tracking` | <p>Skip the GitHub Actions cache round-trip used to track consecutive failures. Useful for repos or workflows where cache writes are undesirable, or for debugging. When disabled, <code>consecutive_failures</code> is reported as <code>1</code>.</p> | `false` | `false` |


## Outputs

| name | description |
| --- | --- |
| `consecutive_failures` | <p>Number of consecutive failures observed for this workflow + branch combination within the <code>stale_failure_days</code> window (always <code>1</code> if tracking is disabled or no prior cache entry exists).</p> |


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

    repeat_threshold:
    # Number of consecutive failures at or above which the alert is prefixed with a
    # "REPEATED FAILURE (Nx)" escalation marker. Set to `0` to disable the prefix
    # (the count is still tracked and exposed as the `consecutive_failures` output).
    #
    # Required: false
    # Default: 3

    stale_failure_days:
    # Window (in days) used to decide whether a stored failure count is still
    # "consecutive". If the previous failure is older than this, the counter resets
    # to 1. This is a coarse stand-in for an on-success reset, which we cannot
    # implement from a failure-only action.
    #
    # Required: false
    # Default: 7

    disable_consecutive_failure_tracking:
    # Skip the GitHub Actions cache round-trip used to track consecutive failures.
    # Useful for repos or workflows where cache writes are undesirable, or for
    # debugging. When disabled, `consecutive_failures` is reported as `1`.
    #
    # Required: false
    # Default: false
```
