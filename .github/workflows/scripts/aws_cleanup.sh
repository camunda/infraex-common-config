#!/bin/bash

set -euxo pipefail

# This script deletes additional AWS resources based on specified criteria.

# Check if the region argument is provided
if [ -z "$1" ]; then
    echo "Please provide the AWS region as the first argument."
    exit 1
fi

region="$1"

echo "Deleting additional resources in the $region region..."


echo "Deleting additional resources..."
# KMS keys can't be deleted due to resource policies, requires manual intervention

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

echo "Deleting OIDC Providers"
# Delete OIDC Provider
oidc_providers=$(aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[?contains(Arn, 'eu-west-2') || contains(Arn, 'eu-west-3')].Arn" --output text)

read -r -a oidc_providers_array <<< "$oidc_providers"

for oidc_provider in "${oidc_providers_array[@]}"
do
    echo "Deleting OIDC Provider: $oidc_provider"
    aws iam delete-open-id-connect-provider --open-id-connect-provider-arn "$oidc_provider"
done

echo "Deleting VPC Peering Connections"
# Delete VPC Peering Connection
peering_connection_ids=$(aws ec2 describe-vpc-peering-connections --region "$region" --query "VpcPeeringConnections[?Status.Code == 'active' && Tags[?contains(Value, 'nightly')]]".VpcPeeringConnectionId --output text)

read -r -a peering_connection_ids_array <<< "$peering_connection_ids"

for peering_connection_id in "${peering_connection_ids_array[@]}"
do
    echo "Deleting VPC Peering Connection: $peering_connection_id"
    aws ec2 delete-vpc-peering-connection --region "$region" --vpc-peering-connection-id "$peering_connection_id"
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
