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
| `version` | <p>asdf version</p> | `false` | `v0.16.1` |
| `os` | <p>Target OS (linux or darwin)</p> | `false` | `linux` |
| `arch` | <p>Target architecture (amd64, arm64, etc.). Default will try to detect runner arch.</p> | `false` | `auto` |
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
    # Default: v0.16.1

    os:
    # Target OS (linux or darwin)
    #
    # Required: false
    # Default: linux

    arch:
    # Target architecture (amd64, arm64, etc.). Default will try to detect runner arch.
    #
    # Required: false
    # Default: auto

    cache:
    # Use cache for tools installed
    #
    # Required: false
    # Default: true
```
