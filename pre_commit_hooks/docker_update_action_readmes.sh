#!/bin/bash

set -euxo pipefail

# due to an open bug in the node image https://github.com/nodejs/docker-node/issues/740
# we can't map the user and group at the docker level, therefore we chown the files
USER_ID=$(id -u)
GROUP_ID=$(id -g)

# define doc variables based on the git repository as this does not work correctly using
# the built-in method
REPO_URL=$(git config --get remote.origin.url)
REPO_URL_CLEAN=$(echo "$REPO_URL" | sed 's/.git$//')
OWNER_PROJECT=$(echo "$REPO_URL_CLEAN" | awk -F'[:/]' '{print $(NF-1)"/"$NF}')
DOC_ACTION_VERSION=${DOC_ACTION_VERSION:-main}
# directory where your actions are located, use "." if it's top directory
TOP_ACTION_DIR=".github/actions"

# Run a single Docker container to handle the README.md updates
docker run --rm \
    -e USER_ID="$USER_ID" \
    -e GROUP_ID="$GROUP_ID" \
    -e DOC_ACTION_VERSION="$DOC_ACTION_VERSION" \
    -e OWNER_PROJECT="$OWNER_PROJECT" \
    -e TOP_ACTION_DIR="$TOP_ACTION_DIR" \
    -v "$PWD":/workspace \
    -w /workspace \
    node:22 \
    bash -c '
        set -euxo pipefail
        npm install -g action-docs
        find "$TOP_ACTION_DIR" -name "*.yml" -o -name "*.yaml" | while read -r action_file; do
            action_dir=$(dirname "$action_file")
            action_dir_top=$(basename "$action_dir")
            echo "Updating README.md in $action_dir"
            action-docs -t 1 --no-banner -n -s "$action_file" > "$action_dir/README.md.tmp"

            # Ensure that only a single empty line is left at the end of the file
            sed -e :a -e "/^\n*\$/{\$d;N;};/\n\$/ba" "$action_dir/README.md.tmp" > "$action_dir/README.md"

            # Add TOP_ACTION_DIR to the path if it is not "."
            if [ "$TOP_ACTION_DIR" != "." ]; then
                PROJECT_PATH="$OWNER_PROJECT/$TOP_ACTION_DIR/$action_dir_top@$DOC_ACTION_VERSION"
            else
                PROJECT_PATH="$OWNER_PROJECT/$action_dir_top@$DOC_ACTION_VERSION"
            fi

            # Replace the placeholder in README.md
            sed -i "s|\*\*\*PROJECT\*\*\*@\*\*\*VERSION\*\*\*|$PROJECT_PATH|g" "$action_dir/README.md"


            chown "$USER_ID:$GROUP_ID" "$action_dir/README.md"
            rm -f "$action_dir/README.md.tmp"
        done
    '

DOCKER_EXIT_CODE=$?

if [ $DOCKER_EXIT_CODE -ne 0 ]; then
    echo "Docker action readme generation command failed with exit code $DOCKER_EXIT_CODE, please use verbose mode"
    exit 1
fi
