#!/bin/bash

set -euxo pipefail

# This script deletes additional AWS resources based on specified criteria.

# Default value for DRY_RUN is false
DRY_RUN=${DRY_RUN:-false}

# Check if the region argument is provided
if [ -z "$1" ]; then
    echo "Please provide the AWS region as the first argument."
    exit 1
fi

region="$1"

echo "Deleting additional resources in the $region region..."

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

echo "Deleting OIDC Providers"
# Delete OIDC Provider
oidc_providers=$(paginate "aws iam list-open-id-connect-providers" "OpenIDConnectProviderList[?contains(Arn, '$region')].Arn")

if [ -n "$oidc_providers" ]; then
    read -r -a oidc_providers_array <<< "$oidc_providers"

    for oidc_provider in "${oidc_providers_array[@]}"
    do
        echo "Deleting OIDC Provider: $oidc_provider"
        execute_or_simulate "aws iam delete-open-id-connect-provider --open-id-connect-provider-arn $oidc_provider"
    done
fi

echo "Deleting VPC Peering Connections"
# Delete VPC Peering Connection
peering_connection_ids=$(paginate "aws ec2 describe-vpc-peering-connections --region $region" "VpcPeeringConnections[?Status.Code == 'active'].VpcPeeringConnectionId")

if [ -n "$peering_connection_ids" ]; then
    read -r -a peering_connection_ids_array <<< "$peering_connection_ids"

    for peering_connection_id in "${peering_connection_ids_array[@]}"
    do
        echo "Deleting VPC Peering Connection: $peering_connection_id"
        execute_or_simulate "aws ec2 delete-vpc-peering-connection --region $region --vpc-peering-connection-id $peering_connection_id"
    done
fi

echo "Deleting Client VPN Endpoints"
# List all Client VPN endpoints
client_vpn_endpoint_ids=$(paginate "aws ec2 describe-client-vpn-endpoints --region $region" "ClientVpnEndpoints[].ClientVpnEndpointId")

if [ -n "$client_vpn_endpoint_ids" ]; then
    read -r -a client_vpn_ids_array <<< "$client_vpn_endpoint_ids"

    for cvpn_id in "${client_vpn_ids_array[@]}"
    do
        echo "Processing Client VPN Endpoint: $cvpn_id"

        # Disassociate target networks
        associations=$(aws ec2 describe-client-vpn-target-networks \
            --region "$region" \
            --client-vpn-endpoint-id "$cvpn_id" \
            --query 'ClientVpnTargetNetworks[].AssociationId' \
            --output text)

        if [ -n "$associations" ]; then
            read -r -a assoc_ids <<< "$associations"
            for assoc_id in "${assoc_ids[@]}"
            do
                echo "Disassociating target network: $assoc_id"
                execute_or_simulate "aws ec2 disassociate-client-vpn-target-network --region $region --client-vpn-endpoint-id $cvpn_id --association-id $assoc_id"
            done
        fi

        # Revoke all authorization rules
        auth_rules=$(aws ec2 describe-client-vpn-authorization-rules \
            --region "$region" \
            --client-vpn-endpoint-id "$cvpn_id" \
            --query 'AuthorizationRules[].AuthorizationRuleId' \
            --output text)

        if [ -n "$auth_rules" ]; then
            read -r -a rule_ids <<< "$auth_rules"
            for rule_id in "${rule_ids[@]}"
            do
                echo "Revoking authorization rule: $rule_id"
                execute_or_simulate "aws ec2 revoke-client-vpn-authorization-rule --region $region --client-vpn-endpoint-id $cvpn_id --authorization-rule-id $rule_id"
            done
        fi

        # Delete the Client VPN endpoint
        echo "Deleting Client VPN Endpoint: $cvpn_id"
        execute_or_simulate "aws ec2 delete-client-vpn-endpoint --region $region --client-vpn-endpoint-id $cvpn_id"
    done
fi

echo "Deleting Cognito User Pools"
# Delete Cognito User Pools (must delete domains first)
user_pool_ids=$(aws cognito-idp list-user-pools --region "$region" --max-results 60 --query 'UserPools[].Id' --output text || true)

if [ -n "$user_pool_ids" ]; then
    read -r -a user_pool_ids_array <<< "$user_pool_ids"

    for user_pool_id in "${user_pool_ids_array[@]}"
    do
        echo "Processing Cognito User Pool: $user_pool_id"

        # Get and delete the domain first (required before deleting the User Pool)
        domain=$(aws cognito-idp describe-user-pool --region "$region" --user-pool-id "$user_pool_id" --query 'UserPool.Domain' --output text 2>/dev/null || true)

        if [ -n "$domain" ] && [ "$domain" != "None" ]; then
            echo "Deleting Cognito User Pool Domain: $domain"
            execute_or_simulate "aws cognito-idp delete-user-pool-domain --region $region --user-pool-id $user_pool_id --domain $domain"
        fi

        echo "Deleting Cognito User Pool: $user_pool_id"
        execute_or_simulate "aws cognito-idp delete-user-pool --region $region --user-pool-id $user_pool_id"
    done
fi

echo "Deleting Cognito Identity Pools"
# Delete Cognito Identity Pools
identity_pool_ids=$(aws cognito-identity list-identity-pools --region "$region" --max-results 60 --query 'IdentityPools[].IdentityPoolId' --output text || true)

if [ -n "$identity_pool_ids" ]; then
    read -r -a identity_pool_ids_array <<< "$identity_pool_ids"

    for identity_pool_id in "${identity_pool_ids_array[@]}"
    do
        echo "Deleting Cognito Identity Pool: $identity_pool_id"
        execute_or_simulate "aws cognito-identity delete-identity-pool --region $region --identity-pool-id $identity_pool_id"
    done
fi

echo "Deleting ACM Private Certificate Authorities"
# Delete ACM Private CAs (must disable first, then delete)
pca_arns=$(aws acm-pca list-certificate-authorities --region "$region" --query 'CertificateAuthorities[?Status!=`DELETED`].Arn' --output text || true)

if [ -n "$pca_arns" ]; then
    read -r -a pca_arns_array <<< "$pca_arns"

    for pca_arn in "${pca_arns_array[@]}"
    do
        echo "Processing Private CA: $pca_arn"

        # Get the current status
        pca_status=$(aws acm-pca describe-certificate-authority --region "$region" --certificate-authority-arn "$pca_arn" --query 'CertificateAuthority.Status' --output text || true)

        # Disable the CA first if it's active (required before deletion)
        if [ "$pca_status" == "ACTIVE" ]; then
            echo "Disabling Private CA: $pca_arn"
            execute_or_simulate "aws acm-pca update-certificate-authority --region $region --certificate-authority-arn $pca_arn --status DISABLED"
        fi

        # Delete the CA (permanently after 7-30 day waiting period, or immediately with --permanent-deletion-time-in-days 7)
        echo "Deleting Private CA: $pca_arn"
        execute_or_simulate "aws acm-pca delete-certificate-authority --region $region --certificate-authority-arn $pca_arn --permanent-deletion-time-in-days 7" || true
    done
fi

echo "Deleting ACM Certificates"
# Delete ACM certificates (public and private)
cert_arns=$(aws acm list-certificates --region "$region" --query 'CertificateSummaryList[].CertificateArn' --output text || true)

if [ -n "$cert_arns" ]; then
    read -r -a cert_arns_array <<< "$cert_arns"

    for cert_arn in "${cert_arns_array[@]}"
    do
        echo "Deleting ACM Certificate: $cert_arn"
        # Note: This will fail if the certificate is in use by another AWS resource
        execute_or_simulate "aws acm delete-certificate --region $region --certificate-arn $cert_arn" || true
    done
fi

echo "Deleting unattached Elastic IPs"
# Delete Elastic IPs that are not associated with any instance or network interface
# Unattached EIPs cost ~$3.65/month each
eip_allocations=$(aws ec2 describe-addresses --region "$region" --query 'Addresses[?AssociationId==`null`].AllocationId' --output text || true)

if [ -n "$eip_allocations" ]; then
    read -r -a eip_allocations_array <<< "$eip_allocations"

    for allocation_id in "${eip_allocations_array[@]}"
    do
        echo "Releasing Elastic IP: $allocation_id"
        execute_or_simulate "aws ec2 release-address --region $region --allocation-id $allocation_id"
    done
fi

echo "Deleting CloudWatch Log Groups"
# Delete CloudWatch Log Groups (can accumulate storage costs)
# Skip log groups that contain 'DO_NOT_DELETE' or are AWS-managed
log_groups=$(aws logs describe-log-groups --region "$region" --query 'logGroups[].logGroupName' --output text || true)

if [ -n "$log_groups" ]; then
    read -r -a log_groups_array <<< "$log_groups"

    for log_group in "${log_groups_array[@]}"
    do
        # Skip AWS-managed log groups and those marked as DO_NOT_DELETE
        if [[ "$log_group" == /aws/* ]] || [[ "$log_group" == *DO_NOT_DELETE* ]]; then
            echo "Skipping log group: $log_group (AWS-managed or protected)"
            continue
        fi

        echo "Deleting CloudWatch Log Group: $log_group"
        execute_or_simulate "aws logs delete-log-group --region $region --log-group-name $log_group"
    done
fi
