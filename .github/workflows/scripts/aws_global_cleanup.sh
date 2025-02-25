#!/bin/bash

set -euxo pipefail

# Default value for DRY_RUN is false
DRY_RUN=${DRY_RUN:-false}

SKIP_ROLES=(
  "AWS*"
  "AmazonEKS*"
  "lambda_exec_role*"
  "ManagedOpenShift*"
  "OrganizationAccountAccessRole*"
  "aws-ec2-spot-fleet-tagging-role*"
  "ref-arch-*"
  "Wiz*"
  "stacksets-exec-17b1ba2d4b46b1c7ed312b029f146659"
)

SKIP_POLICIES=(
  "*/AWS*"
  "*/AmazonEKS*"
  "*/ManagedOpenShift*"
  "*/OrganizationAccountAccessRole*"
  "*/ref-arch*"
  "*/Wiz*"
)

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --dry-run) DRY_RUN=true ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

# Function to execute a command or simulate it if DRY_RUN is true
execute_or_simulate() {
    local cmd="$1"
    if [ "$DRY_RUN" = true ]; then
        echo "[DRY RUN] Would execute: $cmd"
    else
        eval "$cmd"
    fi
}

# Function to paginate through AWS CLI output
paginate() {
    local command="$1"
    local query="$2"
    local output=""
    local next_token=""

    while : ; do
        # Execute the command with the next token if it exists
        if [ -z "$next_token" ]; then
            output=$($command --output text --query "$query" || true)
        else
            output=$($command --output text --query "$query" --starting-token "$next_token" || true)
        fi

        # If output is empty, break the loop
        if [ -z "$output" ]; then
            break
        fi

        echo "$output"

        # Get the next token from the command output
        next_token=$($command --output text --query 'NextToken' || true)

        if [ "$next_token" == "None" ] || [ -z "$next_token" ]; then
            break
        fi
    done
}

echo "Deleting additional global resources..."

echo "Deleting IAM Users"
# Delete Users
usernames=$(paginate "aws iam list-users" "Users[].UserName")

if [ -n "$usernames" ]; then
    read -r -a usernames_array <<< "$usernames"

    for username in "${usernames_array[@]}"
    do
        if [[ "$username" == "tf-automation-user" ]]; then
            echo "Skipping user: $username (tf-automation-user)"
            continue
        fi

        echo "Processing user: $username"
        attached_policy_arns=$(paginate "aws iam list-attached-user-policies --user-name $username" "AttachedPolicies[].PolicyArn")

        if [ -n "$attached_policy_arns" ]; then
            read -r -a attached_policy_arns_array <<< "$attached_policy_arns"
            for policy_arn in "${attached_policy_arns_array[@]}"
            do
                echo "Detaching policy $policy_arn from user $username"
                execute_or_simulate "aws iam detach-user-policy --user-name $username --policy-arn $policy_arn"
            done
        fi

        inline_policy_names=$(paginate "aws iam list-user-policies --user-name $username" "PolicyNames")
        if [ -n "$inline_policy_names" ]; then
            read -r -a inline_policy_names_array <<< "$inline_policy_names"
            for policy_name in "${inline_policy_names_array[@]}"
            do
                echo "Deleting inline policy $policy_name from user $username"
                execute_or_simulate "aws iam delete-user-policy --user-name $username --policy-name $policy_name"
            done
        fi

        access_key_ids=$(paginate "aws iam list-access-keys --user-name $username" "AccessKeyMetadata[].AccessKeyId")
        if [ -n "$access_key_ids" ]; then
            read -r -a access_key_ids_array <<< "$access_key_ids"
            for access_key_id in "${access_key_ids_array[@]}"
            do
                echo "Deleting access key $access_key_id for user $username"
                execute_or_simulate "aws iam delete-access-key --user-name $username --access-key-id $access_key_id"
            done
        fi

        echo "Deleting user: $username"
        execute_or_simulate "aws iam delete-user --user-name $username"
    done
fi

echo "Deleting IAM Roles"
# Detach permissions and profile instances and delete IAM roles
role_arns=$(paginate "aws iam list-roles" "Roles[].RoleName")

if [ -n "$role_arns" ]; then
    read -r -a role_arns_array <<< "$role_arns"

    for role_arn in "${role_arns_array[@]}"
    do
        skip=false
        # Skip roles prefixed that we want to keep
        for pattern in "${SKIP_ROLES[@]}"; do
            if [[ "$role_arn" == $pattern ]]; then
                echo "Skipping role: $role_arn"
                skip=true
                break
            fi
        done

        if [ "$skip" = true ]; then
            continue
        fi

        echo "Removing instance profiles and policies of role: $role_arn"
        attached_policy_arns=$(paginate "aws iam list-attached-role-policies --role-name $role_arn" "AttachedPolicies[].PolicyArn")

        if [ -n "$attached_policy_arns" ]; then
            read -r -a attached_policy_arns_array <<< "$attached_policy_arns"

            for policy_arn in "${attached_policy_arns_array[@]}"
            do
                echo "Removing attached policy: $policy_arn"
                execute_or_simulate "aws iam detach-role-policy --role-name $role_arn --policy-arn $policy_arn"
            done

            policy_arns=$(paginate "aws iam list-role-policies --role-name $role_arn" "PolicyNames")

            if [ -n "$policy_arns" ]; then
                read -r -a policy_arns_array <<< "$policy_arns"

                for policy_name in "${policy_arns_array[@]}"
                do
                    echo "Deleting role policy: $policy_name"
                    execute_or_simulate "aws iam delete-role-policy --role-name $role_arn --policy-name $policy_name"
                done
            fi

            instance_profile_arns=$(paginate "aws iam list-instance-profiles-for-role --role-name $role_arn" "InstanceProfiles[].InstanceProfileName")

            if [ -n "$instance_profile_arns" ]; then
                read -r -a instance_profile_arns_array <<< "$instance_profile_arns"

                for instance_profile_arn in "${instance_profile_arns_array[@]}"
                do
                    echo "Removing instance profile: $instance_profile_arn"
                    execute_or_simulate "aws iam remove-role-from-instance-profile --instance-profile-name $instance_profile_arn --role-name $role_arn"
                done
            fi
        fi

        echo "Deleting role: $role_arn"
        execute_or_simulate "aws iam delete-role --role-name $role_arn"
    done
fi

echo "Deleting IAM Policies"
# Delete Policies
iam_policies=$(paginate "aws iam list-policies --scope Local" "Policies[].Arn")

if [ -n "$iam_policies" ]; then
    read -r -a iam_policies_array <<< "$iam_policies"

    for iam_policy in "${iam_policies_array[@]}"
    do
        skip=false
        # Skip policies prefixed that we want to keep
        for pattern in "${SKIP_POLICIES[@]}"; do
            if [[ "$iam_policy" == $pattern ]]; then
                echo "Skipping policy: $iam_policy"
                skip=true
                break
            fi
        done

        if [ "$skip" = true ]; then
            continue
        fi

        echo "Deleting policy: $iam_policy"
        execute_or_simulate "aws iam delete-policy --policy-arn $iam_policy"
    done
fi

echo "Deleting S3 Buckets"

# list of bucket not to be deleted
# please use https://github.com/camunda/infraex-terraform/blob/main/aws/s3-buckets.yml
# to reference the bucket then add it to this list
keeplist_buckets=(
    "camunda.ie"                          # Public bucket to redirect camunda.ie to camunda.com
    "tf-state-multi-reg"                  # used in tests (github.com/camunda/camunda-deployment-references)
    "tests-rosa-tf-state-eu-central-1"     # used in tests (github.com/camunda/camunda-tf-rosa)
    "tests-ra-aws-rosa-hcp-tf-state-eu-central-1" # used in rosa hcp tests (github.com/camunda/camunda-deployment-references)
    "tests-eks-tf-state-eu-central-1"      # used for tests (github.com/camunda/camunda-tf-eks-module)
    "tests-c8-multi-region-es-eu-central-1" # used in tests (github.com/camunda/c8-multi-region)
    "general-purpose-bucket-that-will-not-be-deleted" # general purpose bucket
)

echo "Deleting nightly S3 Buckets"
bucket_ids=$(paginate "aws s3api list-buckets" "Buckets[].Name")

if [ -n "$bucket_ids" ]; then
    read -r -a buckets <<< "$bucket_ids"

    for bucket in "${buckets[@]}"
    do
        if echo "${keeplist_buckets[@]}" | grep -qw "$bucket"; then
            echo "Bucket $bucket is in the keeplist, skipping deletion."
        else
            echo "Deleting contents of bucket: $bucket"
            execute_or_simulate "aws s3 rm s3://$bucket --recursive"

            echo "Deleting bucket: $bucket"
            execute_or_simulate "aws s3api delete-bucket --bucket $bucket"
        fi
    done
fi

echo "Deleting IAM Identity Providers"
identity_providers=$(paginate "aws iam list-open-id-connect-providers" "OpenIDConnectProviderList[].Arn")

if [ -n "$identity_providers" ]; then
    read -r -a identity_providers_array <<< "$identity_providers"

    for provider in "${identity_providers_array[@]}"
    do
        if [[ "$provider" == *DO_NOT_DELETE* ]]; then
            echo "Skipping provider: $provider (marked as DO_NOT_DELETE)"
            continue
        fi

        echo "Deleting identity provider: $provider"
        execute_or_simulate "aws iam delete-open-id-connect-provider --open-id-connect-provider-arn $provider"
    done
fi
