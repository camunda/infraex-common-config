# this file is a recipe file for the project

# Install all the tooling
install-tooling: asdf-install

# Install asdf plugins
asdf-plugins:
    #!/bin/sh
    echo "Installing asdf plugins"
    for plugin in $(awk '{print $1}' .tool-versions); do \
      asdf plugin add ${plugin} 2>&1 | (grep "already added" && exit 0); \
    done

    echo "Update all asdf plugins"
    asdf plugin update --all

# Install tools using asdf
asdf-install: asdf-plugins
    asdf install

# Run the Azure resource group sweep against a stubbed CLI
test-azure-cleanup:
    ./.github/workflows/scripts/tests/azure_rg_cleanup_test.sh

# Check that every Renovate version annotation in this repository extracts one dependency
check-renovate-annotations:
    ./.github/workflows/scripts/check_renovate_annotations.py --preset default.json5 --root .

# Run the Renovate annotation checker against its own fixtures
test-renovate-annotations:
    ./.github/workflows/scripts/tests/check_renovate_annotations_test.sh
