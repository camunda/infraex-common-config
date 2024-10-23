#!/bin/bash

set -euxo pipefail

# Default value for DRY_RUN is false
DRY_RUN=false

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

echo "Deleting additional global resources..."

echo "Deleting IAM Users"
# Delete Users
usernames=$(aws iam list-users --query "Users[].UserName" --output text)

if [ -n "$usernames" ]; then
    read -r -a usernames_array <<< "$usernames"

    for username in "${usernames_array[@]}"
    do
        if [[ "$username" == "tf-automation-user" ]]; then
            echo "Skipping user: $username (tf-automation-user)"
            continue
        fi

        echo "Processing user: $username"
        attached_policy_arns=$(aws iam list-attached-user-policies --user-name "$username" --query 'AttachedPolicies[].PolicyArn' --output text)
        if [ -n "$attached_policy_arns" ]; then
            read -r -a attached_policy_arns_array <<< "$attached_policy_arns"
            for policy_arn in "${attached_policy_arns_array[@]}"
            do
                echo "Detaching policy $policy_arn from user $username"
                execute_or_simulate "aws iam detach-user-policy --user-name $username --policy-arn $policy_arn"
            done
        fi

        inline_policy_names=$(aws iam list-user-policies --user-name "$username" --query 'PolicyNames' --output text)
        if [ -n "$inline_policy_names" ]; then
            read -r -a inline_policy_names_array <<< "$inline_policy_names"
            for policy_name in "${inline_policy_names_array[@]}"
            do
                echo "Deleting inline policy $policy_name from user $username"
                execute_or_simulate "aws iam delete-user-policy --user-name $username --policy-name $policy_name"
            done
        fi

        access_key_ids=$(aws iam list-access-keys --user-name "$username" --query 'AccessKeyMetadata[].AccessKeyId' --output text)
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
role_arns=$(aws iam list-roles --query "Roles[].RoleName" --output text)

if [ -n "$role_arns" ]; then
    read -r -a role_arns_array <<< "$role_arns"

    for role_arn in "${role_arns_array[@]}"
    do
        # Skip roles prefixed that we want to keep
        if [[ "$role_arn" == AWS* || "$role_arn" == transformSlackToC8*  || "$role_arn" == rds-monitoring-role* || "$role_arn" == medicAbsenceFormToJsonData* || "$role_arn" == AmazonEKS* || "$role_arn" == lambda_exec_role* || "$role_arn" == ManagedOpenShift* || "$role_arn" == OrganizationAccountAccessRole* || "$role_arn" == aws-ec2-spot-fleet-tagging-role* ]]; then
            echo "Skipping role: $role_arn"
            continue
        fi

        echo "Removing instance profiles and policies of role: $role_arn"
        attached_policy_arns=$(aws iam list-attached-role-policies --role-name "$role_arn" --query 'AttachedPolicies[].PolicyArn' --output text)

        if [ -n "$attached_policy_arns" ]; then
            read -r -a attached_policy_arns_array <<< "$attached_policy_arns"

            for policy_arn in "${attached_policy_arns_array[@]}"
            do
                echo "Removing attached policy: $policy_arn"
                execute_or_simulate "aws iam detach-role-policy --role-name $role_arn --policy-arn $policy_arn"
            done

            policy_arns=$(aws iam list-role-policies --role-name "$role_arn" --query 'PolicyNames' --output text)
            if [ -n "$policy_arns" ]; then
                read -r -a policy_arns_array <<< "$policy_arns"

                for policy_arn in "${policy_arns_array[@]}"
                do
                    echo "Deleting policy: $policy_arn"
                    execute_or_simulate "aws iam delete-role-policy --role-name $role_arn --policy-name $policy_arn"
                done
            fi

            instance_profile_arns=$(aws iam list-instance-profiles-for-role --role-name "$role_arn" --query 'InstanceProfiles[].InstanceProfileName' --output text)

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
iam_policies=$(aws iam list-policies --query "Policies[].Arn" --output text)

if [ -n "$iam_policies" ]; then
    read -r -a iam_policies_array <<< "$iam_policies"

    for iam_policy in "${iam_policies_array[@]}"
    do
        # Skip policies prefixed that we want to keep
        if [[ "$iam_policy" == AWS* || "$iam_policy" == AmazonEKS* || "$iam_policy" == ManagedOpenShift* || "$iam_policy" == OrganizationAccountAccessRole* ]]; then
            echo "Skipping policy: $iam_policy"
            continue
        fi

        echo "Deleting policy: $iam_policy"
        execute_or_simulate "aws iam delete-policy --policy-arn $iam_policy"
    done
fi

echo "Deleting nightly S3 Buckets"
bucket_ids=$(aws s3api list-buckets --query "Buckets[?contains(Name, 'nightly')].Name" --output text)

if [ -n "$bucket_ids" ]; then
    read -r -a buckets <<< "$bucket_ids"

    for bucket in "${buckets[@]}"
    do
        echo "Deleting contents of bucket: $bucket"
        execute_or_simulate "aws s3 rm s3://$bucket --recursive"

        echo "Deleting bucket: $bucket"
        execute_or_simulate "aws s3api delete-bucket --bucket $bucket"
    done
fi

echo "Deleting IAM Identity Providers"
identity_providers=$(aws iam list-open-id-connect-providers --query 'OpenIDConnectProviderList[].Arn' --output text)

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
