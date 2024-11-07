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

### Permanent Regions

These regions are designated for so-called permanent resources like S3 buckets, lambda functions, reference architectures... anything that is out of the scope of testing.

These are not cleaned up by the mentioned CI test above.

Here we differentiate between the use cases and have reference architectures outside of other permanent resources. This allows to potentially clean things up easier and have a better understanding on what belongs where.

All of this is kept in [Terraform](https://github.com/camunda/infraex-terraform/tree/main/aws).

#### AWS Regions

| Region        | Identifier    | Use Case                |
|---------------|---------------|-------------------------|
| EU (Frankfurt)| eu-central-1  | permanent resources     |
| EU (Ireland)  | eu-west-1     | reference architectures |
