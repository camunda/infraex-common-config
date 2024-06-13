# Report Failure and Notify Slack

This GitHub composite action imports secrets from HashiCorp Vault and sends a Slack notification in case of a workflow failure.
It helps automate incident reporting and ensures timely notifications to the relevant Slack channel.

## Inputs

- **vault_addr**: (required) The address of the Vault instance.
- **vault_role_id**: (required) The role ID used for authentication with Vault.
- **vault_secret_id**: (required) The secret ID used for authentication with Vault.
- **slack_channel_id**: (optional) The Slack channel ID where the notification will be sent. Default is 'C076N4G1162' (#infraex-alerts).
- **slack_mention_people**: (optional) The Slack people to mention in the notification. Default is '@infraex-medic'.
- **disable_silence_check**: (optional) Disable silence check. By default, alerts can be disabled by creating an issue in the repository with the label `alert-management` and with the title: `silence: name of your workflow`. Default is 'false'.

## Usage

To use this composite action in your workflow, include it as a step and provide the necessary inputs. Below is an example workflow using this action:

```yaml
name: Example Workflow
on: [push, pull_request]

jobs:
  example-job:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v2

      # Other steps of your workflow

      - name: Report Failure and Notify Slack
        if: failure() && github.event_name == 'schedule'
        uses: camunda/infraex-common-config/.github/actions/report-failure-on-slack@main
        with:
          vault_addr: ${{ secrets.VAULT_ADDR }}
          vault_role_id: ${{ secrets.VAULT_ROLE_ID }}
          vault_secret_id: ${{ secrets.VAULT_SECRET_ID }}
          slack_channel_id: 'your-slack-channel-id' # Optional
          slack_mention_people: '@your-mention' # Optional
          disable_silence_check: 'false' # Optional
```
