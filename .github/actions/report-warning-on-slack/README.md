# Report Warning and Notify Slack

## Description

Companion to report-failure-on-slack for NON-blocking findings.

Imports the Slack bot token from HashiCorp Vault and posts a clearly
warning-styled Slack notification. Unlike report-failure-on-slack, it does
not frame the message as a failure: there is no :rotating_light: severity
frame, no "failed!" wording, no STABLE BRANCH FAILURE / REPEATED FAILURE
escalation and no consecutive-failure tracking.

Use it for advisory signals (for example Helm deprecation or unknown-key
configuration drift) that must be surfaced but must not look like a red CI
failure. Typically called from a step guarded with `if: always()` once the
caller has detected findings.


## Inputs

| name | description | required | default |
| --- | --- | --- | --- |
| `vault_addr` | <p>The address of the Vault instance.</p> | `true` | `""` |
| `vault_jwt_path` | <p>The Vault auth backend path for GitHub OIDC (JWT) authentication. The calling job needs the <code>id-token: write</code> permission.</p> | `true` | `""` |
| `vault_jwt_role` | <p>The Vault role for GitHub OIDC (JWT) authentication.</p> | `true` | `""` |
| `vault_jwt_audience` | <p>The GitHub OIDC audience for Vault (JWT) authentication.</p> | `true` | `""` |
| `vault_secret_path` | <p>The Vault path holding the Slack bot token. Callers outside InfraEx must point it at their own team's secret, never at the InfraEx one.</p> | `false` | `secret/data/products/infrastructure-experience/ci/common` |
| `vault_slack_token_key` | <p>The key of the Slack bot token in the Vault secret.</p> | `false` | `SLACK_BOT_TOKEN` |
| `slack_channel_id` | <p>The Slack channel ID where the warning will be sent.</p> | `false` | `C076N4G1162` |
| `slack_mention_people` | <p>Optional Slack handles or group mentions to cc on the warning (for example <code>@smx-medic</code>). Left empty by default because warnings are advisory and should not page people. Broadcast mentions (<code>&lt;!here&gt;</code>, <code>&lt;!channel&gt;</code>, <code>&lt;!everyone&gt;</code>) are defused so a warning cannot page a whole channel; user and usergroup mentions still work.</p> | `false` | `""` |
| `disable_silence_check` | <p>Disable the silence check. By default the warning can be muted by opening an issue in the repository with the label <code>alert-management</code> and the title <code>silence: &lt;name of your workflow&gt;</code>.</p> | `false` | `false` |
| `branch` | <p>The branch the workflow is testing, shown for context. Defaults to auto-detection: <code>github.base_ref</code> for pull requests (the target branch), <code>github.ref_name</code> otherwise.</p> | `false` | `""` |
| `title` | <p>Short headline shown after the WARNING marker.</p> | `false` | `Non-blocking warning` |
| `message` | <p>Optional extra context line rendered under the header. Slack metacharacters (<code>&amp;</code>, <code>&lt;</code>, <code>&gt;</code>) in the value are escaped, so it cannot inject links or broadcast mentions (for example <code>&lt;!here&gt;</code>).</p> | `false` | `""` |
| `github_token` | <p>Token used only for the silence-issue lookup. Needs read access to issues on the current repository. Defaults to the workflow token.</p> | `false` | `${{ github.token }}` |


## Outputs

| name | description |
| --- | --- |
| `ts` | <p>Slack message timestamp (thread id) of the posted warning. Empty when the notification was skipped (silenced).</p> |
| `silenced` | <p><code>true</code> when a matching silence issue suppressed the warning.</p> |


## Runs

This action is a `composite` action.

## Usage

```yaml
- uses: camunda/infraex-common-config/.github/actions/report-warning-on-slack@main
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
    # The Slack channel ID where the warning will be sent.
    #
    # Required: false
    # Default: C076N4G1162

    slack_mention_people:
    # Optional Slack handles or group mentions to cc on the warning
    # (for example `@smx-medic`). Left empty by default because
    # warnings are advisory and should not page people. Broadcast
    # mentions (`<!here>`, `<!channel>`, `<!everyone>`) are defused so a
    # warning cannot page a whole channel; user and usergroup mentions
    # still work.
    #
    # Required: false
    # Default: ""

    disable_silence_check:
    # Disable the silence check. By default the warning can be muted by
    # opening an issue in the repository with the label `alert-management`
    # and the title `silence: <name of your workflow>`.
    #
    # Required: false
    # Default: false

    branch:
    # The branch the workflow is testing, shown for context.
    # Defaults to auto-detection: `github.base_ref` for pull requests
    # (the target branch), `github.ref_name` otherwise.
    #
    # Required: false
    # Default: ""

    title:
    # Short headline shown after the WARNING marker.
    #
    # Required: false
    # Default: Non-blocking warning

    message:
    # Optional extra context line rendered under the header. Slack
    # metacharacters (`&`, `<`, `>`) in the value are escaped, so it
    # cannot inject links or broadcast mentions (for example `<!here>`).
    #
    # Required: false
    # Default: ""

    github_token:
    # Token used only for the silence-issue lookup. Needs read access to
    # issues on the current repository. Defaults to the workflow token.
    #
    # Required: false
    # Default: ${{ github.token }}
```
