#!/bin/bash
# shellcheck disable=SC2207

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
    usernames_array=($(echo "$usernames" | tr '\n' ' '))

    for username in "${usernames_array[@]}"
    do
        if [[ "$username" == "tf-automation-user" ]]; then
            echo "Skipping user: $username (tf-automation-user)"
            continue
        fi

        echo "Processing user: $username"
        attached_policy_arns=$(paginate "aws iam list-attached-user-policies --user-name $username" "AttachedPolicies[].PolicyArn")

        if [ -n "$attached_policy_arns" ]; then
            attached_policy_arns_array=($(echo "$attached_policy_arns" | tr '\n' ' '))
            for policy_arn in "${attached_policy_arns_array[@]}"
            do
                echo "Detaching policy $policy_arn from user $username"
                execute_or_simulate "aws iam detach-user-policy --user-name $username --policy-arn $policy_arn"
            done
        fi

        inline_policy_names=$(paginate "aws iam list-user-policies --user-name $username" "PolicyNames")
        if [ -n "$inline_policy_names" ]; then
            inline_policy_names_array=($(echo "$inline_policy_names" | tr '\n' ' '))
            for policy_name in "${inline_policy_names_array[@]}"
            do
                echo "Deleting inline policy $policy_name from user $username"
                execute_or_simulate "aws iam delete-user-policy --user-name $username --policy-name $policy_name"
            done
        fi

        access_key_ids=$(paginate "aws iam list-access-keys --user-name $username" "AccessKeyMetadata[].AccessKeyId")
        if [ -n "$access_key_ids" ]; then
            access_key_ids_array=($(echo "$access_key_ids" | tr '\n' ' '))
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
    role_arns_array=($(echo "$role_arns" | tr '\n' ' '))

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
            attached_policy_arns_array=($(echo "$attached_policy_arns" | tr '\n' ' '))

            for policy_arn in "${attached_policy_arns_array[@]}"
            do
                echo "Removing attached policy: $policy_arn"
                execute_or_simulate "aws iam detach-role-policy --role-name $role_arn --policy-arn $policy_arn"
            done

            policy_arns=$(paginate "aws iam list-role-policies --role-name $role_arn" "PolicyNames")

            if [ -n "$policy_arns" ]; then
                policy_arns_array=($(echo "$policy_arns" | tr '\n' ' '))

                for policy_name in "${policy_arns_array[@]}"
                do
                    echo "Deleting role policy: $policy_name"
                    execute_or_simulate "aws iam delete-role-policy --role-name $role_arn --policy-name $policy_name"
                done
            fi

            instance_profile_arns=$(paginate "aws iam list-instance-profiles-for-role --role-name $role_arn" "InstanceProfiles[].InstanceProfileName")

            if [ -n "$instance_profile_arns" ]; then
                instance_profile_arns_array=($(echo "$instance_profile_arns" | tr '\n' ' '))

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
    iam_policies_array=($(echo "$iam_policies" | tr '\n' ' '))

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
S3_BUCKETS_URL="https://raw.githubusercontent.com/camunda/infraex-terraform/refs/heads/main/aws/s3-buckets.yml"
keeplist_buckets=($(curl -s -H "Authorization: token $GITHUB_TOKEN" "$S3_BUCKETS_URL" | yq eval '.buckets | keys | .[]' -))

echo "Deleting S3 Buckets"
bucket_ids=$(paginate "aws s3api list-buckets" "Buckets[].Name")

if [ -n "$bucket_ids" ]; then
    buckets=($(echo "$bucket_ids" | tr '\n' ' '))

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
    identity_providers_array=($(echo "$identity_providers" | tr '\n' ' '))

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

echo "Deleting orphaned Route53 Hosted Zones"
# Delete Route53 Hosted Zones that appear to be orphaned from deleted clusters
# Patterns: *.hypershift.local, rosa.*.openshiftapps.com (but NOT camunda.ie or other legitimate zones)
# Skip zones with DO_NOT_DELETE in their name
hosted_zones=$(paginate "aws route53 list-hosted-zones" "HostedZones[].{Id:Id,Name:Name}" || true)

if [ -n "$hosted_zones" ]; then
    echo "$hosted_zones" | while read -r zone; do
        zone_id=$(echo "$zone" | jq -r '.Id' | sed 's|/hostedzone/||')
        zone_name=$(echo "$zone" | jq -r '.Name')

        # Skip zones that don't match orphan patterns
        if [[ ! "$zone_name" =~ \.hypershift\.local\.$ ]] && [[ ! "$zone_name" =~ ^rosa\..+\.openshiftapps\.com\.$ ]]; then
            echo "Skipping hosted zone: $zone_name (not matching orphan patterns)"
            continue
        fi

        # Skip protected zones
        if [[ "$zone_name" == *DO_NOT_DELETE* ]]; then
            echo "Skipping hosted zone: $zone_name (protected)"
            continue
        fi

        echo "Processing orphaned hosted zone: $zone_name ($zone_id)"

        # Delete all record sets except NS and SOA (required before zone deletion)
        record_sets=$(aws route53 list-resource-record-sets --hosted-zone-id "$zone_id" --query "ResourceRecordSets[?Type != 'NS' && Type != 'SOA']" --output json || true)

        if [ -n "$record_sets" ] && [ "$record_sets" != "[]" ]; then
            # Create a change batch to delete all non-NS/SOA records
            change_batch=$(echo "$record_sets" | jq '{Changes: [.[] | {Action: "DELETE", ResourceRecordSet: .}]}')

            if [ "$(echo "$change_batch" | jq '.Changes | length')" -gt 0 ]; then
                echo "Deleting record sets in hosted zone: $zone_name"
                if [ "$DRY_RUN" = true ]; then
                    echo "[DRY RUN] Would delete $(echo "$change_batch" | jq '.Changes | length') record sets"
                else
                    aws route53 change-resource-record-sets --hosted-zone-id "$zone_id" --change-batch "$change_batch" || true
                fi
            fi
        fi

        echo "Deleting hosted zone: $zone_name"
        execute_or_simulate "aws route53 delete-hosted-zone --id $zone_id"
    done
fi
