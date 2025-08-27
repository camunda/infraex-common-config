# GHA Failure Log Analyzer

## Description

Analyzes GitHub Actions failure logs and posts results to a Slack thread.


## Inputs

| name | description | required | default |
| --- | --- | --- | --- |
| `run_url` | <p>GitHub Actions run URL to analyze</p> | `true` | `""` |
| `slack_thread_ts` | <p>Slack thread ts to post the analysis result</p> | `false` | `""` |
| `slack_channel_id` | <p>Slack channel ID to post the analysis result</p> | `false` | `""` |
| `slack_token` | <p>Slack Token for posting messages</p> | `false` | `""` |
| `gh_token` | <p>GitHub Token for authentication</p> | `true` | `""` |
| `max_tokens` | <p>Maximum tokens for AI model response</p> | `false` | `600` |
| `model` | <p>AI model to use for analysis</p> | `false` | `openai/gpt-5` |


## Outputs

| name | description |
| --- | --- |
| `response` | <p>AI analysis response</p> |


## Runs

This action is a `composite` action.

## Usage

```yaml
- uses: camunda/infraex-common-config/.github/actions/gha-failure-log-analyzer@main
  with:
    run_url:
    # GitHub Actions run URL to analyze
    #
    # Required: true
    # Default: ""

    slack_thread_ts:
    # Slack thread ts to post the analysis result
    #
    # Required: false
    # Default: ""

    slack_channel_id:
    # Slack channel ID to post the analysis result
    #
    # Required: false
    # Default: ""

    slack_token:
    # Slack Token for posting messages
    #
    # Required: false
    # Default: ""

    gh_token:
    # GitHub Token for authentication
    #
    # Required: true
    # Default: ""

    max_tokens:
    # Maximum tokens for AI model response
    #
    # Required: false
    # Default: 600

    model:
    # AI model to use for analysis
    #
    # Required: false
    # Default: openai/gpt-5
```
