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

echo "Deleting OIDC Providers"
# Delete OIDC Provider
oidc_providers=$(aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[?contains(Arn, '$region')].Arn" --output text --no-paginate)

read -r -a oidc_providers_array <<< "$oidc_providers"

for oidc_provider in "${oidc_providers_array[@]}"
do
    echo "Deleting OIDC Provider: $oidc_provider"
    aws iam delete-open-id-connect-provider --open-id-connect-provider-arn "$oidc_provider"
done

echo "Deleting VPC Peering Connections"
# Delete VPC Peering Connection
peering_connection_ids=$(aws ec2 describe-vpc-peering-connections --region "$region" --query "VpcPeeringConnections[?Status.Code == 'active']]".VpcPeeringConnectionId --output text --no-paginate)

read -r -a peering_connection_ids_array <<< "$peering_connection_ids"

for peering_connection_id in "${peering_connection_ids_array[@]}"
do
    echo "Deleting VPC Peering Connection: $peering_connection_id"
    aws ec2 delete-vpc-peering-connection --region "$region" --vpc-peering-connection-id "$peering_connection_id"
done
