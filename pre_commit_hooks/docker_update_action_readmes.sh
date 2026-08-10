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

# This hook writes into the working tree, and on the auto-fix path its output is committed
# back to the pull request by CI. Everything it executes is therefore pinned:
#
# - the image by digest, so the tag cannot be repointed under us;
# - action-docs to an exact version, because `npm install -g action-docs` resolved
#   whatever npm served at run time;
# - --ignore-scripts, so a compromised release cannot execute install hooks.
#
# renovate: datasource=docker depName=node versioning=docker
NODE_IMAGE="node:22"
# The custom managers in default.json5 track the tag, not the digest, so refresh this by
# hand whenever the tag above moves: docker manifest inspect "$NODE_IMAGE"
NODE_IMAGE_DIGEST="sha256:0557ac14e0d45d02ed563067b82856ca5e7aa3437fa28d98d4350ea9c3d9494a"
# renovate: datasource=npm depName=action-docs
ACTION_DOCS_VERSION="2.5.1"

# Run a single Docker container to handle the README.md updates
docker run --rm \
    -e USER_ID="$USER_ID" \
    -e GROUP_ID="$GROUP_ID" \
    -e DOC_ACTION_VERSION="$DOC_ACTION_VERSION" \
    -e OWNER_PROJECT="$OWNER_PROJECT" \
    -e TOP_ACTION_DIR="$TOP_ACTION_DIR" \
    -e ACTION_DOCS_VERSION="$ACTION_DOCS_VERSION" \
    -v "$PWD":/workspace \
    -w /workspace \
    "${NODE_IMAGE}@${NODE_IMAGE_DIGEST}" \
    bash -c '
        set -euxo pipefail
        npm install -g --ignore-scripts "action-docs@${ACTION_DOCS_VERSION}"
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
