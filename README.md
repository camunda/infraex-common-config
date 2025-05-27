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

### Bucket Usage

By default, any bucket not listed in <https://github.com/camunda/infraex-terraform/blob/main/aws/s3-buckets.yml> will be deleted during the daily cleanup.
For temporary tests or work, use `general-purpose-bucket-that-will-not-be-deleted`, but ensure manual cleanup to avoid data accumulation.

### Region Usage for Cloud Providers

#### CI Regions

CI Regions are designated specifically for **Continuous Integration (CI) tests**. Resources in these regions are deleted nightly as part of routine maintenance. Please ensure that these regions are used exclusively for CI-related tests.

##### AWS Regions

| Region     | Identifier | Cleanup Schedule |
|------------|------------|------------------|
| EU (London)| eu-west-2  | Daily @5AM       |
| EU (Paris) | eu-west-3  | Daily @5AM       |

##### Azure Regions

| Region         | Identifier   | Cleanup Schedule       |
|----------------|--------------|-------------------------|
| Sweden Central | swedencentral| Daily @5AM              |

#### Weekly Work Regions

Weekly Work Regions provide a **temporary environment** for projects that require resources to be retained for the duration of a work week. These regions are ideal for projects that span multiple days without requiring nightly cleanup.

To keep the environment organized, all resources in these regions are automatically cleaned up every week. This ensures that resources do not persist beyond their intended use, making the regions ready for new projects each week.

##### AWS Regions

| Region              | Identifier   | Cleanup Schedule |
|---------------------|--------------|------------------|
| EU (Stockholm)      | eu-north-1   | Saturday @5AM    |
| US East (N. Virginia) | us-east-1 | Saturday @5AM    |
| US East (Ohio) | us-east-2 | Saturday @5AM    |


#### Permanent Regions

Permanent Regions are designated for **persistent resources** that fall outside the scope of CI testing. This includes resources such as S3 buckets, Lambda functions, and reference architectures, all of which are critical for ongoing infrastructure and operational requirements.

To facilitate resource management, we distinguish between **reference architectures** and other permanent resources, allowing for easier cleanup and a clearer understanding of resource allocation.

All configurations are maintained in [Terraform](https://github.com/camunda/infraex-terraform/tree/main/aws).

##### AWS Regions

| Region         | Identifier   | Use Case                |
|----------------|--------------|-------------------------|
| EU (Frankfurt) | eu-central-1 | Permanent resources     |
| EU (Ireland)   | eu-west-1    | Reference architectures |

##### Azure Regions

| Region         | Identifier   | Use Case                |
|----------------|--------------|-------------------------|
| West Europe    | westeurope   | Permanent resources     |
