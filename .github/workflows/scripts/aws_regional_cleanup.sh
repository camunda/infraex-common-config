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
        next_token=$($command --output text --query 'NextToken' 2>/dev/null | head -1 || true)

        if [ "$next_token" = "None" ] || [ -z "$next_token" ]; then
            break
        fi
    done
}

echo "Tearing down Aurora Global Databases with a member in $region"
# Aurora Global Databases link regional DB clusters across AWS Regions. A member
# cluster can't be deleted while it belongs to a global database, and the
# primary (writer) member can't be detached until all secondaries are removed.
# cloud-nuke runs independently per region and can't perform this cross-region
# teardown, so it fails with InvalidGlobalClusterStateFault / "this cluster is a
# part of a global cluster". We handle it here: for every global database that
# has a member in the current region, detach all members (secondaries first,
# primary last, each in its own region) and delete the now-empty global
# database. Each regional cluster becomes standalone and is then removed by
# cloud-nuke in its own region.
#
# Safety guard: a global database is only torn down when ALL of its members live
# in regions fully wiped by this workflow, so we never detach clusters that
# belong to permanent or reference environments. Overridable via the
# CLEANUP_REGIONS env var so the workflow can pass the matrix-derived list and
# avoid drift; the default below must otherwise stay in sync with the matrix in
# aws_nightly_cleanup.yml.
CLEANUP_REGIONS="${CLEANUP_REGIONS:-eu-west-2 eu-west-3 eu-north-1 us-east-1 us-east-2}"

# Capture stderr (2>&1) so a real failure — transient AWS error or missing
# permissions — is surfaced as a warning instead of being silently swallowed and
# treated as "no global clusters". Skipping teardown silently here would let
# cloud-nuke fail later with the harder-to-action global-cluster errors.
if ! global_cluster_ids=$(aws rds describe-global-clusters --region "$region" \
    --query 'GlobalClusters[].GlobalClusterIdentifier' --output text 2>&1); then
    echo "Warning: could not list global clusters in $region; skipping global database teardown: ${global_cluster_ids}"
    global_cluster_ids=""
fi

# `--output text` yields the literal "None" (not an empty string) when the query
# resolves to null, so treat it as "no global clusters" like elsewhere in this script.
if [ -n "$global_cluster_ids" ] && [ "$global_cluster_ids" != "None" ]; then
    read -r -a global_cluster_ids_array <<< "$global_cluster_ids"

    for global_cluster_id in "${global_cluster_ids_array[@]}"
    do
        # Capture stderr so a describe failure (throttling / AccessDenied) is not
        # mistaken for "no members" — which would log a misleading message and
        # attempt a delete that hides the real problem. Skip this cluster on error.
        if ! members=$(aws rds describe-global-clusters --region "$region" \
            --global-cluster-identifier "$global_cluster_id" \
            --query 'GlobalClusters[0].GlobalClusterMembers[].[DBClusterArn,IsWriter]' \
            --output text 2>&1); then
            echo "Warning: could not describe global database $global_cluster_id; skipping its teardown this run: ${members}"
            continue
        fi

        # An empty member list (or the literal "None" when GlobalClusters[0] is
        # null) means there is nothing to detach, so the global database can go.
        if [ -z "$members" ] || [ "$members" = "None" ]; then
            echo "Global database $global_cluster_id has no members, deleting it"
            if [ "$DRY_RUN" = true ]; then
                execute_or_simulate "aws rds delete-global-cluster --region $region --global-cluster-identifier $global_cluster_id"
            elif ! delete_err=$(aws rds delete-global-cluster --region "$region" --global-cluster-identifier "$global_cluster_id" 2>&1); then
                # A sibling per-region job may have already deleted it, so only
                # warn (with the AWS error) rather than failing the whole cleanup.
                echo "Warning: delete-global-cluster for $global_cluster_id did not succeed: ${delete_err}"
            fi
            continue
        fi

        # Sort members into secondaries and primary while enforcing the safety guard.
        secondary_arns=()
        primary_arn=""
        primary_region=""
        member_in_region=false
        skip_global_cluster=false

        while IFS=$'\t' read -r member_arn is_writer
        do
            [ -z "$member_arn" ] && continue
            # ARN format: arn:aws:rds:<region>:<account-id>:cluster:<name>
            member_region=$(echo "$member_arn" | cut -d: -f4)

            if [ "$member_region" = "$region" ]; then
                member_in_region=true
            fi

            if ! grep -qwF "$member_region" <<< "$CLEANUP_REGIONS"; then
                echo "Skipping global database $global_cluster_id (member in protected region: $member_region)"
                skip_global_cluster=true
                break
            fi

            if [ "$is_writer" = "True" ]; then
                primary_arn="$member_arn"
                primary_region="$member_region"
            else
                secondary_arns+=("$member_arn")
            fi
        done <<< "$members"

        if [ "$skip_global_cluster" = true ]; then
            continue
        fi

        if [ "$member_in_region" != true ]; then
            echo "Skipping global database $global_cluster_id (no member in $region)"
            continue
        fi

        echo "Tearing down global database: $global_cluster_id"

        # Detach secondaries first; the primary can't be removed while any
        # secondary member is still attached.
        for member_arn in "${secondary_arns[@]}"
        do
            member_region=$(echo "$member_arn" | cut -d: -f4)
            echo "Detaching secondary cluster $member_arn (region $member_region)"
            execute_or_simulate "aws rds remove-from-global-cluster --region $member_region --global-cluster-identifier $global_cluster_id --db-cluster-identifier $member_arn" || true
        done

        if [ -n "$primary_arn" ]; then
            echo "Detaching primary cluster $primary_arn (region $primary_region)"
            if [ "$DRY_RUN" = true ]; then
                execute_or_simulate "aws rds remove-from-global-cluster --region $primary_region --global-cluster-identifier $global_cluster_id --db-cluster-identifier $primary_arn"
            else
                # Secondary removal is asynchronous, so the primary detach can
                # transiently fail with InvalidGlobalClusterStateFault until the
                # secondaries are gone. Retry (capped ~5 min) instead of swallowing
                # the error; otherwise the primary stays attached and keeps
                # blocking cloud-nuke. A sibling per-region job may tear down the
                # same global cluster in parallel, so first check whether the
                # primary is still a member and stop once it is gone.
                primary_detached=false
                for _ in $(seq 1 30)
                do
                    still_member=$(aws rds describe-global-clusters --region "$primary_region" \
                        --global-cluster-identifier "$global_cluster_id" \
                        --query "length(GlobalClusters[0].GlobalClusterMembers[?DBClusterArn=='${primary_arn}'])" \
                        --output text 2>/dev/null || echo "unknown")
                    if [ "$still_member" = "0" ] || [ "$still_member" = "None" ]; then
                        primary_detached=true
                        break
                    fi
                    if detach_err=$(aws rds remove-from-global-cluster --region "$primary_region" \
                        --global-cluster-identifier "$global_cluster_id" \
                        --db-cluster-identifier "$primary_arn" 2>&1); then
                        primary_detached=true
                        break
                    fi
                    echo "Primary detach not ready yet (secondaries still detaching): ${detach_err}"
                    sleep 10
                done
                if [ "$primary_detached" != true ]; then
                    echo "Warning: could not detach primary $primary_arn from $global_cluster_id after ~5 min; leaving it for the next run"
                fi
            fi
        fi

        # Detaching is asynchronous; wait for the global database to be empty
        # before deleting it (best-effort, capped at ~5 minutes).
        delete_region="${primary_region:-$region}"
        # Default to empty so dry-run (which skips the wait loop) still shows the
        # intended deletion via execute_or_simulate.
        remaining=0
        if [ "$DRY_RUN" != true ]; then
            remaining="unknown"
            for _ in $(seq 1 30)
            do
                # A describe failure must stay "unknown" (not be read as 0),
                # otherwise we could delete while members are still attached.
                if ! remaining=$(aws rds describe-global-clusters --region "$delete_region" \
                    --global-cluster-identifier "$global_cluster_id" \
                    --query 'length(GlobalClusters[0].GlobalClusterMembers)' \
                    --output text 2>&1); then
                    echo "Warning: could not check members of $global_cluster_id: ${remaining}"
                    remaining="unknown"
                    break
                fi
                if [ "$remaining" = "0" ] || [ "$remaining" = "None" ] || [ -z "$remaining" ]; then
                    remaining=0
                    break
                fi
                echo "Waiting for $remaining member(s) to detach from $global_cluster_id..."
                sleep 10
            done
        fi

        # Only delete once the database is confirmed empty. Attempting it with
        # members still attached (or after a describe failure left the state
        # "unknown") fails, and because the error is swallowed the cluster would
        # silently survive and keep blocking cloud-nuke.
        if [ "$remaining" = "0" ]; then
            echo "Deleting global database: $global_cluster_id"
            if [ "$DRY_RUN" = true ]; then
                execute_or_simulate "aws rds delete-global-cluster --region $delete_region --global-cluster-identifier $global_cluster_id"
            elif ! delete_err=$(aws rds delete-global-cluster --region "$delete_region" --global-cluster-identifier "$global_cluster_id" 2>&1); then
                # A sibling per-region job may have already deleted it, so only
                # warn (with the AWS error) rather than failing the whole cleanup.
                echo "Warning: delete-global-cluster for $global_cluster_id did not succeed: ${delete_err}"
            fi
        else
            echo "Warning: global database $global_cluster_id not confirmed empty (remaining=$remaining); leaving it for the next run"
        fi
    done
fi

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

echo "Deleting ACM Certificates"
# Delete ACM certificates (public and private) - must be deleted before Private CAs
cert_arns=$(paginate "aws acm list-certificates --region $region" "CertificateSummaryList[].CertificateArn")

if [ -n "$cert_arns" ]; then
    read -r -a cert_arns_array <<< "$cert_arns"

    for cert_arn in "${cert_arns_array[@]}"
    do
        echo "Deleting ACM Certificate: $cert_arn"
        # Note: This will fail if the certificate is in use by another AWS resource
        execute_or_simulate "aws acm delete-certificate --region $region --certificate-arn $cert_arn" || true
    done
fi

echo "Deleting ACM Private Certificate Authorities"
# Delete ACM Private CAs (must disable first, then delete)
# Note: Certificates issued by a Private CA should be deleted first
pca_arns=$(paginate "aws acm-pca list-certificate-authorities --region $region" "CertificateAuthorities[?Status!=\`DELETED\`].Arn") || true

if [ -n "$pca_arns" ]; then
    read -r -a pca_arns_array <<< "$pca_arns"

    for pca_arn in "${pca_arns_array[@]}"
    do
        echo "Processing Private CA: $pca_arn"

        # Get the current status
        pca_status=$(aws acm-pca describe-certificate-authority --region "$region" --certificate-authority-arn "$pca_arn" --query 'CertificateAuthority.Status' --output text || true)

        # Disable the CA first if it's active (required before deletion)
        if [ "$pca_status" = "ACTIVE" ]; then
            echo "Disabling Private CA: $pca_arn"
            execute_or_simulate "aws acm-pca update-certificate-authority --region $region --certificate-authority-arn $pca_arn --status DISABLED"
        fi

        # Delete the CA (permanently after 7-30 day waiting period, or immediately with --permanent-deletion-time-in-days 7)
        echo "Deleting Private CA: $pca_arn"
        execute_or_simulate "aws acm-pca delete-certificate-authority --region $region --certificate-authority-arn $pca_arn --permanent-deletion-time-in-days 7" || true
    done
fi

echo "Deleting unattached Elastic IPs"
# Delete Elastic IPs that are not associated with any instance or network interface
# Unattached EIPs cost ~$3.65/month each
eip_allocations=$(aws ec2 describe-addresses --region "$region" --query 'Addresses[?!AssociationId].AllocationId' --output text || true)

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
log_groups=$(paginate "aws logs describe-log-groups --region $region" "logGroups[].logGroupName")

if [ -n "$log_groups" ]; then
    read -r -a log_groups_array <<< "$log_groups"

    for log_group in "${log_groups_array[@]}"
    do
        # Skip AWS-managed log groups and those marked as DO_NOT_DELETE
        if [[ "$log_group" == /aws/* ]] || \
           [[ "$log_group" == /aws-* ]] || \
           [[ "$log_group" == /elasticbeanstalk/* ]] || \
           [[ "$log_group" == /ecs/* ]] || \
           [[ "$log_group" == *DO_NOT_DELETE* ]]; then
            echo "Skipping log group: $log_group (AWS-managed or protected)"
            continue
        fi

        echo "Deleting CloudWatch Log Group: $log_group"
        execute_or_simulate "aws logs delete-log-group --region $region --log-group-name \"$log_group\""
    done
fi
