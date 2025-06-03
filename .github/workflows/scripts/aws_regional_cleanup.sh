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
