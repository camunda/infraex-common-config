#!/bin/bash

set -euxo pipefail

# This script deletes additional AWS resources based on specified criteria.

# Default value for DRY_RUN is false
DRY_RUN=${DRY_RUN:-false}

# Resources younger than this window are left alone.
#
# cloud-nuke runs *after* this script in the same job with the same window and
# deliberately spares resources younger than it. This script must honour the same
# window, otherwise it deletes the dependencies of a cluster cloud-nuke is about
# to keep: the cluster stays up but silently loses IRSA (every pod using a
# service-account role stops getting credentials) with no error surfaced anywhere.
#
# Same value as the cloud-nuke `--older-than` flag, e.g. "12h". "0h" disables the
# filter (weekly regions, where everything is expected to go).
CLEANUP_OLDER_THAN="${CLEANUP_OLDER_THAN:-0h}"

# Check if the region argument is provided
if [ -z "$1" ]; then
    echo "Please provide the AWS region as the first argument."
    exit 1
fi

region="$1"

cleanup_older_than_hours="${CLEANUP_OLDER_THAN%h}"
if ! [[ "$cleanup_older_than_hours" =~ ^[0-9]+$ ]]; then
    echo "CLEANUP_OLDER_THAN must be a number of hours such as '12h' (got '$CLEANUP_OLDER_THAN')."
    exit 1
fi
age_cutoff_epoch=$(( $(date -u +%s) - cleanup_older_than_hours * 3600 ))

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

# Converts an ISO-8601 timestamp to epoch seconds, or prints nothing if it cannot
# be parsed. AWS returns both offset-aware ("...+02:00", "...Z") and naive
# ("2026-08-06T14:39:35", always UTC) forms depending on the API, so naive values
# are pinned to UTC rather than to the runner's local timezone.
# python3 is used instead of `date` because GNU and BSD date disagree on both
# parsing and epoch formatting, and this script runs on CI and on laptops.
iso_to_epoch() {
    python3 -c '
import sys, datetime
try:
    parsed = datetime.datetime.fromisoformat(sys.argv[1].strip().replace("Z", "+00:00"))
except ValueError:
    sys.exit(0)
if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=datetime.timezone.utc)
print(int(parsed.timestamp()))
' "$1" 2>/dev/null || true
}

# Returns 0 when a resource created at $1 (epoch seconds) is old enough to delete.
# An unknown timestamp fails closed and keeps the resource: a missed deletion is
# picked up by the next nightly run, a wrong deletion is not recoverable.
is_old_enough() {
    local created_epoch="$1"

    # No window configured: age is irrelevant, everything is in scope.
    if [ "$cleanup_older_than_hours" -eq 0 ]; then
        return 0
    fi
    if [ -z "$created_epoch" ]; then
        return 1
    fi

    [ "$created_epoch" -le "$age_cutoff_epoch" ]
}

# EKS clusters that still exist in this region, and the OIDC issuer id of each.
# These are the clusters cloud-nuke will keep (too young) or has not reached yet;
# their OIDC provider and control-plane log group must survive with them.
live_eks_clusters=$(aws eks list-clusters --region "$region" --query 'clusters[]' --output text 2>/dev/null || true)

live_oidc_ids=""
for live_cluster in $live_eks_clusters; do
    cluster_issuer=$(aws eks describe-cluster --region "$region" --name "$live_cluster" \
        --query 'cluster.identity.oidc.issuer' --output text 2>/dev/null || true)
    if [ -n "$cluster_issuer" ] && [ "$cluster_issuer" != "None" ]; then
        # Issuer looks like https://oidc.eks.<region>.amazonaws.com/id/<ID>
        live_oidc_ids="$live_oidc_ids ${cluster_issuer##*/}"
    fi
done

# Returns 0 when $1 mentions the name of an EKS cluster that still exists.
# Some resources (VPC peering connections) expose no creation timestamp at all in
# the AWS API, so their Name tag is the only signal available. Best-effort by
# design: it only ever keeps more resources than the age filter would.
references_live_cluster() {
    local haystack="$1"
    local candidate

    if [ -z "$haystack" ] || [ "$haystack" = "None" ]; then
        return 1
    fi
    for candidate in $live_eks_clusters; do
        if [[ "$haystack" == *"$candidate"* ]]; then
            return 0
        fi
    done
    return 1
}

echo "Age filter: keeping resources newer than ${cleanup_older_than_hours}h"
echo "Live EKS clusters in $region: ${live_eks_clusters:-<none>}"

echo "Deleting OIDC Providers"
# Delete OIDC Provider
oidc_providers=$(paginate "aws iam list-open-id-connect-providers" "OpenIDConnectProviderList[?contains(Arn, '$region')].Arn")

if [ -n "$oidc_providers" ]; then
    read -r -a oidc_providers_array <<< "$oidc_providers"

    for oidc_provider in "${oidc_providers_array[@]}"
    do
        # An OIDC provider whose EKS cluster is still alive is load-bearing:
        # removing it breaks IRSA instantly for every pod on that cluster.
        oidc_id="${oidc_provider##*/}"
        if [[ " $live_oidc_ids " == *" $oidc_id "* ]]; then
            echo "Skipping OIDC Provider: $oidc_provider (its EKS cluster still exists)"
            continue
        fi

        oidc_created=$(aws iam get-open-id-connect-provider \
            --open-id-connect-provider-arn "$oidc_provider" \
            --query 'CreateDate' --output text 2>/dev/null || true)
        if ! is_old_enough "$(iso_to_epoch "$oidc_created")"; then
            echo "Skipping OIDC Provider: $oidc_provider (created $oidc_created, too recent)"
            continue
        fi

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
        # Dual-region tests peer the VPC of two live clusters. The EC2 API exposes
        # no creation time for a peering connection, so fall back to its Name tag:
        # tearing this down mid-test breaks cross-region connectivity.
        peering_name=$(aws ec2 describe-vpc-peering-connections --region "$region" \
            --vpc-peering-connection-ids "$peering_connection_id" \
            --query "VpcPeeringConnections[0].Tags[?Key==\`Name\`].Value | [0]" \
            --output text 2>/dev/null || true)
        if references_live_cluster "$peering_name"; then
            echo "Skipping VPC Peering Connection: $peering_connection_id ($peering_name references a live cluster)"
            continue
        fi

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

        cvpn_details=$(aws ec2 describe-client-vpn-endpoints --region "$region" \
            --client-vpn-endpoint-ids "$cvpn_id" \
            --query "ClientVpnEndpoints[0].[CreationTime,Tags[?Key==\`Name\`].Value|[0]]" \
            --output text 2>/dev/null || true)
        cvpn_created=$(echo "$cvpn_details" | cut -f1)
        cvpn_name=$(echo "$cvpn_details" | cut -f2)

        # A Client VPN endpoint is how engineers reach a private cluster. Removing
        # one that belongs to a cluster still running cuts access to a live test.
        if references_live_cluster "$cvpn_name"; then
            echo "Skipping Client VPN Endpoint: $cvpn_id ($cvpn_name references a live cluster)"
            continue
        fi
        if ! is_old_enough "$(iso_to_epoch "$cvpn_created")"; then
            echo "Skipping Client VPN Endpoint: $cvpn_id (created $cvpn_created, too recent)"
            continue
        fi

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

        # Domain and creation date come from the same call: the domain must be
        # deleted before the pool, the date decides whether we touch it at all.
        user_pool_details=$(aws cognito-idp describe-user-pool --region "$region" --user-pool-id "$user_pool_id" --query 'UserPool.[Domain,CreationDate]' --output text 2>/dev/null || true)
        domain=$(echo "$user_pool_details" | cut -f1)
        user_pool_created=$(echo "$user_pool_details" | cut -f2)

        if ! is_old_enough "$(iso_to_epoch "$user_pool_created")"; then
            echo "Skipping Cognito User Pool: $user_pool_id (created $user_pool_created, too recent)"
            continue
        fi

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
        cert_created=$(aws acm describe-certificate --region "$region" --certificate-arn "$cert_arn" \
            --query 'Certificate.CreatedAt' --output text 2>/dev/null || true)
        if ! is_old_enough "$(iso_to_epoch "$cert_created")"; then
            echo "Skipping ACM Certificate: $cert_arn (created $cert_created, too recent)"
            continue
        fi

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
eip_allocations=$(aws ec2 describe-addresses --region "$region" --query "Addresses[?!AssociationId].[AllocationId,Tags[?Key==\`Name\`].Value|[0]]" --output text || true)

if [ -n "$eip_allocations" ]; then
    while IFS=$'\t' read -r allocation_id eip_name
    do
        if [ -z "$allocation_id" ]; then
            continue
        fi

        # An EIP has no creation timestamp in the EC2 API. The Name tag is the
        # only way to tell an address orphaned by a destroyed cluster from one a
        # live cluster has allocated but not yet associated.
        if references_live_cluster "$eip_name"; then
            echo "Skipping Elastic IP: $allocation_id ($eip_name references a live cluster)"
            continue
        fi

        echo "Releasing Elastic IP: $allocation_id"
        execute_or_simulate "aws ec2 release-address --region $region --allocation-id $allocation_id"
    done <<< "$eip_allocations"
fi

echo "Deleting CloudWatch Log Groups"
# Delete CloudWatch Log Groups (they accumulate storage costs). This runs only in
# ephemeral test regions, where log groups left behind by deleted resources
# (EKS control-plane, ECS Container Insights, VPC flow logs, ECS task logs, ...)
# should be cleaned up too. Only skip those explicitly marked DO_NOT_DELETE.
# Name and creation time are fetched together: a log group is skipped when it
# belongs to a cluster that is still running (deleting the control-plane log
# group of a live cluster destroys the only trace of what it is doing) or when
# it is younger than the cleanup window. `creationTime` is in milliseconds.
log_groups=$(paginate "aws logs describe-log-groups --region $region" "logGroups[].[logGroupName,creationTime]")

if [ -n "$log_groups" ]; then
    while IFS=$'\t' read -r log_group log_group_created_ms
    do
        if [ -z "$log_group" ]; then
            continue
        fi

        if [[ "$log_group" == *DO_NOT_DELETE* ]]; then
            echo "Skipping log group: $log_group (protected)"
            continue
        fi

        if references_live_cluster "$log_group"; then
            echo "Skipping log group: $log_group (references a live cluster)"
            continue
        fi

        log_group_created_epoch=""
        if [[ "$log_group_created_ms" =~ ^[0-9]+$ ]]; then
            log_group_created_epoch=$(( log_group_created_ms / 1000 ))
        fi
        if ! is_old_enough "$log_group_created_epoch"; then
            echo "Skipping log group: $log_group (too recent)"
            continue
        fi

        echo "Deleting CloudWatch Log Group: $log_group"
        # Best-effort: a genuinely undeletable / service-managed log group must
        # not abort the rest of the cleanup (set -e).
        execute_or_simulate "aws logs delete-log-group --region $region --log-group-name \"$log_group\"" || true
    done <<< "$log_groups"
fi

echo "Deleting orphaned ELBv2 Target Groups"
# ELBv2 target groups are NOT handled by cloud-nuke. Target groups created by
# in-cluster controllers for Kubernetes Services of type=LoadBalancer (OpenShift
# router, ingress-nginx, Contour, Submariner, ...) are left behind when their
# load balancer or the whole cluster is destroyed. They silently pile up and
# eventually exhaust the regional "Target Groups per Region" quota (default 3000),
# which breaks new ingress load balancer creation with a TooManyTargetGroups error.
#
# Delete every target group that is no longer associated with a load balancer
# (empty LoadBalancerArns). cloud-nuke removes the load balancers (here or on a
# previous nightly run), so the set of orphaned target groups converges over the
# schedule. A target group still referenced by a listener cannot be deleted and
# is skipped (best-effort, must not abort the rest of the cleanup under set -e).
#
# `aws --output text` returns one page (up to 400 items) per line, tab-separated
# within a line, so normalise the result to one ARN per line. A plain `read -a`
# stops at the first newline and would only ever process the first page (~400),
# leaving a large backlog to drain 400 at a time. Deletions run in parallel:
# a leaked backlog can be thousands of target groups, far more than a sequential
# loop can delete within this step's time budget.
#
# No age filter here: ELBv2 exposes no creation timestamp for a target group, and
# resolving each one's owning cluster would cost one describe-tags call per target
# group, which the backlog size above rules out. `LoadBalancerArns == 0` is the
# guard instead - such a target group serves no load balancer, so deleting it
# cannot break live traffic. Residual risk is the few seconds between an
# in-cluster controller creating a target group and attaching it to its load
# balancer; the controller recreates it on the next reconcile.
target_group_arns=$(paginate "aws elbv2 describe-target-groups --region $region" "TargetGroups[?length(LoadBalancerArns)==\`0\`].TargetGroupArn" | tr '\t' '\n' | grep -v '^$' || true)

if [ -n "$target_group_arns" ]; then
    echo "Found $(echo "$target_group_arns" | grep -c .) orphaned target group(s) to delete"
    if [ "$DRY_RUN" = true ]; then
        while IFS= read -r target_group_arn; do
            echo "[DRY RUN] Would execute: aws elbv2 delete-target-group --region $region --target-group-arn $target_group_arn"
        done <<< "$target_group_arns"
    else
        # -P 10: modest parallelism to drain large backlogs within the time budget.
        # Each delete is best-effort: a target group still wired to a listener
        # returns an error which we ignore so a single failure can't abort the sweep.
        # The ARN is passed as a positional arg ($2), never interpolated into the
        # inner script, to avoid any shell injection.
        # shellcheck disable=SC2016
        echo "$target_group_arns" | xargs -P 10 -I{} sh -c \
            'echo "Deleting orphaned Target Group: $2"; aws elbv2 delete-target-group --region "$1" --target-group-arn "$2" || true' _ "$region" {}
    fi
fi

echo "Deleting abandoned ECS clusters"
# cloud-nuke cannot remove these. An ECS cluster exposes no creation timestamp,
# and cloud-nuke excludes every resource without one as soon as a time filter is
# set. Confirmed against v0.52.0, which logs for each cluster:
#   DEBUG  Resource has no creation time but a time filter is set - excluding for safety
# The filter is always set, even when --older-than is omitted, so the ecs-cluster
# resource type is inert for this account and clusters pile up indefinitely.
#
# Age is reconstructed from a first-seen tag, the only way to date a resource the
# API refuses to timestamp. cloud-nuke already writes `cloud-nuke-first-seen` on
# every cluster it scans, and leaves the value untouched on later runs, so it is
# reused here: the clock then starts at the first nightly sweep that ever saw the
# cluster rather than at the first sweep running this code. Our own tag is written
# only as a fallback, so this keeps working if cloud-nuke ever stops tagging.
#
# Two guards keep it safe:
#   - a cluster is only ever a candidate while completely idle, so one sitting
#     between two deployments is never at risk;
#   - it must then stay idle for the whole cleanup window before anything happens.
ECS_CLOUD_NUKE_TAG="cloud-nuke-first-seen"
ECS_FALLBACK_TAG="infraex-cleanup-first-seen"

ecs_cluster_arns=$(paginate "aws ecs list-clusters --region $region" "clusterArns[]" | tr '\t' '\n' | sed '/^$/d')

if [ -n "$ecs_cluster_arns" ]; then
    while IFS= read -r cluster_arn
    do
        if [ -z "$cluster_arn" ]; then
            continue
        fi
        cluster_name="${cluster_arn##*/}"

        # A cluster still holding instances, tasks or services is in use, whatever
        # its age. Counts are read in one call and summed.
        in_use=$(aws ecs describe-clusters --region "$region" --clusters "$cluster_arn" \
            --query 'clusters[0].[registeredContainerInstancesCount,runningTasksCount,pendingTasksCount,activeServicesCount]' \
            --output text 2>/dev/null | tr '\t' '\n' | awk '{ total += $1 } END { print total + 0 }')
        if [ "${in_use:-1}" -ne 0 ]; then
            echo "Skipping ECS cluster: $cluster_name (still in use)"
            continue
        fi

        cluster_tags=$(aws ecs list-tags-for-resource --region "$region" --resource-arn "$cluster_arn" --output json 2>/dev/null || echo '{}')
        first_seen=$(echo "$cluster_tags" | jq -r --arg primary "$ECS_CLOUD_NUKE_TAG" --arg fallback "$ECS_FALLBACK_TAG" \
            '(.tags // []) | (map(select(.key == $primary)) + map(select(.key == $fallback)))[0].value // ""' 2>/dev/null || true)

        if [ -z "$first_seen" ] || [ "$first_seen" = "null" ]; then
            # First idle sighting and cloud-nuke has not tagged it yet, which is
            # expected since it runs after this script. Record the date and leave
            # the cluster alone. Tagging is best-effort: an untaggable cluster is
            # simply reconsidered next run rather than aborting the sweep.
            echo "Recording first idle sighting of ECS cluster: $cluster_name"
            execute_or_simulate "aws ecs tag-resource --region $region --resource-arn $cluster_arn --tags key=$ECS_FALLBACK_TAG,value=$(date -u +%Y-%m-%dT%H:%M:%SZ)" || true
            continue
        fi

        if ! is_old_enough "$(iso_to_epoch "$first_seen")"; then
            echo "Skipping ECS cluster: $cluster_name (idle since $first_seen, too recent)"
            continue
        fi

        echo "Deleting ECS cluster: $cluster_name (idle since $first_seen)"
        # Best-effort: a cluster still referenced by a capacity provider or a
        # draining instance cannot be deleted and must not abort the sweep.
        execute_or_simulate "aws ecs delete-cluster --region $region --cluster $cluster_arn" || true
    done <<< "$ecs_cluster_arns"
fi
