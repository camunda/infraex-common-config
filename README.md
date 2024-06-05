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

### Test Regions of Cloud Providers

Test regions are designated for Continuous Integration (CI) tests and are deleted nightly as part of routine maintenance. Please ensure that you utilize these regions for CI tests.

#### AWS Regions

| Region     | Identifier |
|------------|------------|
| EU (London)| eu-west-2  |
| EU (Paris) | eu-west-3  |
