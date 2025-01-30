# Install asdf and tools

## Description

Install asdf and all tools listed in the .tool-versions file.
This action will:
  - Check if the tools are present in the cache, otherwise it will:
  - Download and verify the asdf binary
  - Extract and install it in ~/.local/bin
  - Automatically install all plugins listed in .tool-versions
  - Install the required tool versions using asdf
  - Cache the installed tools


## Inputs

| name | description | required | default |
| --- | --- | --- | --- |
| `version` | <p>asdf version</p> | `false` | `v0.16.0` |
| `os` | <p>Target OS (linux or darwin)</p> | `false` | `linux` |
| `arch` | <p>Target architecture (amd64, arm64, etc.)</p> | `false` | `amd64` |
| `asdf_path_tools` | <p>Path where asdf installs tools</p> | `false` | `/home/runner/.asdf` |
| `cache` | <p>Use cache for tools installed</p> | `false` | `true` |


## Runs

This action is a `composite` action.

## Usage

```yaml
- uses: camunda/infraex-common-config/.github/actions/asdf-install-tooling@main
  with:
    version:
    # asdf version
    #
    # Required: false
    # Default: v0.16.0

    os:
    # Target OS (linux or darwin)
    #
    # Required: false
    # Default: linux

    arch:
    # Target architecture (amd64, arm64, etc.)
    #
    # Required: false
    # Default: amd64

    asdf_path_tools:
    # Path where asdf installs tools
    #
    # Required: false
    # Default: /home/runner/.asdf

    cache:
    # Use cache for tools installed
    #
    # Required: false
    # Default: true
```
