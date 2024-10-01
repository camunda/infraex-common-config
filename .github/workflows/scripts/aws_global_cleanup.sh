#!/bin/bash

set -euxo pipefail

# This script deletes additional AWS resources based on specified criteria.

echo "Deleting additional global resources..."

echo "Deleting IAM Users"
# Delete Users
usernames=$(aws iam list-users --query "Users[?contains(UserName, 'nightly')].UserName" --output text)

read -r -a usernames_array <<< "$usernames"

for username in "${usernames_array[@]}"
do
    echo "Processing user: $username"

    attached_policy_arns=$(aws iam list-attached-user-policies --user-name "$username" --query 'AttachedPolicies[].PolicyArn' --output text)
    if [ -n "$attached_policy_arns" ]; then
        read -r -a attached_policy_arns_array <<< "$attached_policy_arns"
        for policy_arn in "${attached_policy_arns_array[@]}"
        do
            echo "Detaching policy $policy_arn from user $username"
            aws iam detach-user-policy --user-name "$username" --policy-arn "$policy_arn"
        done
    fi

    inline_policy_names=$(aws iam list-user-policies --user-name "$username" --query 'PolicyNames' --output text)
    if [ -n "$inline_policy_names" ]; then
        read -r -a inline_policy_names_array <<< "$inline_policy_names"
        for policy_name in "${inline_policy_names_array[@]}"
        do
            echo "Deleting inline policy $policy_name from user $username"
            aws iam delete-user-policy --user-name "$username" --policy-name "$policy_name"
        done
    fi

    # Delete access keys for the user
    access_key_ids=$(aws iam list-access-keys --user-name "$username" --query 'AccessKeyMetadata[].AccessKeyId' --output text)
    if [ -n "$access_key_ids" ]; then
        read -r -a access_key_ids_array <<< "$access_key_ids"
        for access_key_id in "${access_key_ids_array[@]}"
        do
            echo "Deleting access key $access_key_id for user $username"
            aws iam delete-access-key --user-name "$username" --access-key-id "$access_key_id"
        done
    fi

    echo "Deleting user: $username"
    aws iam delete-user --user-name "$username"
done

echo "Deleting IAM Roles"
# Detach permissions and profile instances and delete IAM roles
role_arns=$(aws iam list-roles --query "Roles[?contains(RoleName, 'nightly')].RoleName" --output text)

read -r -a role_arns_array <<< "$role_arns"

for role_arn in "${role_arns_array[@]}"
do
    echo "Removing instance profiles and policies of role: $role_arn"
    attached_policy_arns=$(aws iam list-attached-role-policies --role-name "$role_arn" --query 'AttachedPolicies[].PolicyArn' --output text)
    read -r -a attached_policy_arns_array <<< "$attached_policy_arns"

    for policy_arn in "${attached_policy_arns_array[@]}"
    do
        echo "Removing attached policy: $policy_arn"
        aws iam detach-role-policy --role-name "$role_arn" --policy-arn "$policy_arn"
    done

    policy_arns=$(aws iam list-role-policies --role-name "$role_arn" --query 'PolicyNames' --output text)
    read -r -a policy_arns_array <<< "$policy_arns"

    for policy_arn in "${policy_arns_array[@]}"
    do
        echo "Deleting policy: $policy_arn"
        aws iam delete-role-policy --role-name "$role_arn" --policy-name "$policy_arn"
    done

    instance_profile_arns=$(aws iam list-instance-profiles-for-role --role-name "$role_arn" --query 'InstanceProfiles[].InstanceProfileName' --output text)
    read -r -a instance_profile_arns_array <<< "$instance_profile_arns"

    for instance_profile_arn in "${instance_profile_arns_array[@]}"
    do
        echo "Removing instance profile: $instance_profile_arn"
        aws iam remove-role-from-instance-profile --instance-profile-name "$instance_profile_arn" --role-name "$role_arn"
    done

    echo "Deleting role: $role_arn"
    aws iam delete-role --role-name "$role_arn"

done

echo "Deleting IAM Policies"
# Delete Policies
iam_policies=$(aws iam list-policies --query "Policies[?contains(PolicyName, 'nightly')].Arn" --output text)

read -r -a iam_policies_array <<< "$iam_policies"

for iam_policy in "${iam_policies_array[@]}"
do
    echo "Deleting policy: $iam_policy"
    aws iam delete-policy --policy-arn "$iam_policy"
done

echo "Deleting nightly S3 Buckets"
bucket_ids=$(aws s3api list-buckets --query "Buckets[?contains(Name, 'nightly')].Name" --output text)

read -r -a buckets <<< "$bucket_ids"

for bucket in "${buckets[@]}"
do
    echo "Deleting contents of bucket: $bucket"
    aws s3 rm "s3://$bucket" --recursive

    echo "Deleting bucket: $bucket"
    aws s3api delete-bucket --bucket "$bucket"
done
