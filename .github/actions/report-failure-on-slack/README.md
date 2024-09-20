# Report Failure and Notify Slack

## Description

This GitHub composite action imports secrets from HashiCorp Vault and sends a Slack notification in case of a workflow failure.
It helps automate incident reporting and ensures timely notifications to the relevant Slack channel.
Use it with `if: failure()`

