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
