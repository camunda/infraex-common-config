#!/bin/bash

set -euo pipefail

# Deletes Route53 records left behind by external-dns when a cluster teardown fails.
#
# external-dns creates one alias record per Kubernetes Service/Ingress plus a TXT
# ownership record in the registry. When a CI run fails before its teardown step,
# external-dns never removes them, and cloud-nuke deletes the load balancer they
# point at minutes later. Nothing else cleans them up, so they accumulate at every
# failed run: the camunda.ie zone had reached 4024 records before this script was
# introduced, i.e. 40% of the hard limit of 10 000 records per hosted zone. Once
# that limit is hit external-dns stops being able to publish anything and every
# ingress-dependent test breaks at once.
#
# A record still pointing at a released AWS endpoint is also a subdomain takeover
# candidate, which is the reason this runs daily rather than weekly.
#
# Three classes of leftover are collected, each self-validating so that no naming
# convention is relied upon and a live environment can never be affected:
#
#   1. alias records whose load balancer no longer exists in this region, plus the
#      external-dns TXT registry entries that track them;
#   2. CNAMEs pointing at an OpenSearch endpoint in this region whose domain is
#      gone, which Terraform publishes and never removes on a failed destroy;
#   3. cert-manager DNS01 challenges and ACM validation CNAMEs whose validated
#      name has disappeared from the zone.
#
# A record whose target is still alive cannot match any of them.
#
# Convergence: this script runs before cloud-nuke in the nightly workflow, so the
# load balancers of the run being torn down are still alive when it executes and
# their records are kept. cloud-nuke then deletes the load balancers, and the next
# nightly run removes the records. Orphans therefore drain with a one-day lag,
# the same way orphaned target groups do in aws_regional_cleanup.sh.

DRY_RUN=${DRY_RUN:-false}

if [ -z "${1:-}" ]; then
    echo "Please provide the AWS region as the first argument."
    exit 1
fi

region="$1"

# Route53 accepts at most 1000 changes per ChangeResourceRecordSets call. 200 keeps
# each request well under the accompanying limit on the total size of the batch.
BATCH_SIZE=200

echo "Deleting orphaned Route53 records for load balancers removed in $region..."

# DNS names of every regional endpoint still alive, normalised the way Route53
# stores targets: lowercase, no trailing dot. Load balancers cover the alias
# records external-dns publishes; OpenSearch covers the CNAMEs Terraform points at
# a domain's VPC endpoint.
#
# Every one of these calls must succeed. This list is what protects live records:
# if a call fails - expired credentials, throttling, a missing permission - it
# comes back empty, every record in the zone then looks orphaned, and the sweep
# below would delete the whole zone. An empty result is only ever legitimate when
# the API genuinely reports no resources, so a non-zero exit aborts the run.
#
# Results are appended to a file rather than piped: an `exit` inside a command
# substitution only terminates the subshell, so a failure there would go unnoticed
# and produce exactly the empty list this guards against.
endpoints_file=$(mktemp)
cleanup_endpoints_file() { rm -f "$endpoints_file"; }
trap cleanup_endpoints_file EXIT

collect_endpoints() {
    local description="$1"
    shift
    local output
    if ! output=$("$@" 2>&1); then
        echo "Cannot list $description in $region, aborting to avoid deleting live records:" >&2
        echo "$output" >&2
        return 1
    fi
    printf '%s\n' "$output" >> "$endpoints_file"
}

collect_endpoints "application and network load balancers" \
    aws elbv2 describe-load-balancers --region "$region" --query 'LoadBalancers[].DNSName' --output text || exit 1
collect_endpoints "classic load balancers" \
    aws elb describe-load-balancers --region "$region" --query 'LoadBalancerDescriptions[].DNSName' --output text || exit 1

if ! opensearch_domains=$(aws opensearch list-domain-names --region "$region" \
        --query 'DomainNames[].DomainName' --output text 2>&1); then
    echo "Cannot list OpenSearch domains in $region, aborting:" >&2
    echo "$opensearch_domains" >&2
    exit 1
fi
for domain in $opensearch_domains; do
    collect_endpoints "OpenSearch domain $domain" \
        aws opensearch describe-domain --region "$region" --domain-name "$domain" \
        --query 'DomainStatus.[Endpoint,Endpoints.vpc]' --output text || exit 1
done

# "None" is what the CLI prints for a domain that has no VPC endpoint.
alive_endpoints_json=$(tr '\t' '\n' < "$endpoints_file" \
    | tr '[:upper:]' '[:lower:]' | sed -e 's/\.$//' -e '/^$/d' -e '/^none$/d' | sort -u \
    | jq -R -s -c 'split("\n") | map(select(length > 0))')
echo "Live endpoints in $region: $(echo "$alive_endpoints_json" | jq 'length')"

# Private zones are excluded: they serve internal names for resources cloud-nuke
# deletes wholesale along with their VPC, and they are not reachable from the
# internet so they carry no takeover risk.
# Failing here is treated the same way: a silent empty list would turn this script
# into a no-op that nobody notices, which is how the leak grew unseen in the first
# place.
if ! hosted_zones_raw=$(aws route53 list-hosted-zones \
        --query "HostedZones[?Config.PrivateZone==\`false\`].Id" --output text 2>&1); then
    echo "Cannot list hosted zones, aborting:" >&2
    echo "$hosted_zones_raw" >&2
    exit 1
fi
hosted_zones=$(printf '%s\n' "$hosted_zones_raw" | tr '\t' '\n' | sed -e 's|^/hostedzone/||' -e '/^$/d')

for zone_id in $hosted_zones; do
    zone_name=$(aws route53 get-hosted-zone --id "$zone_id" --query 'HostedZone.Name' --output text 2>/dev/null || echo "$zone_id")

    records_file=$(mktemp)
    changes_file=$(mktemp)

    if ! aws route53 list-resource-record-sets --hosted-zone-id "$zone_id" --output json > "$records_file" 2>/dev/null; then
        echo "Skipping zone $zone_name (cannot list records)"
        rm -f "$records_file" "$changes_file"
        continue
    fi

    # SOA and NS records carry no AliasTarget and can therefore never be selected
    # here, which keeps the zone delegation intact by construction.
    jq --argjson alive "$alive_endpoints_json" --arg region "$region" '
        # Regional AWS endpoints this script is able to validate. A record is only
        # ever a candidate if its target matches one of these, so records pointing
        # at anything else (the zone apex S3 website, a delegation, a third party)
        # are out of scope by construction, as are SOA and NS which carry neither
        # an AliasTarget nor a CNAME value.
        def in_region_endpoint:
              test("elb\\." + $region + "\\.amazonaws\\.com$")          # ALB / NLB
            or test($region + "\\.elb\\.amazonaws\\.com$")              # classic ELB
            or test($region + "\\.es\\.amazonaws\\.com$");              # OpenSearch

        def target_of:
            if .AliasTarget != null then .AliasTarget.DNSName
            elif .Type == "CNAME" then (.ResourceRecords // [])[0].Value
            else null end
            | if . == null then null else (ascii_downcase | rtrimstr(".")) end;

        [ .ResourceRecordSets[]
          | select(target_of != null)
          # The target is bound to a variable first: inside `index(...)` the input
          # is the $alive array, so referring to the record there would resolve
          # against the array instead.
          | select(target_of | in_region_endpoint)
          | select(target_of as $target | ($alive | index($target)) | not)
        ] as $orphans
        | ($orphans | map(.Name) | unique) as $orphan_names
        # The external-dns TXT registry either reuses the record name as-is or
        # prefixes the record type to it. Only records carrying the external-dns
        # heritage marker are touched, so unrelated TXT records are left alone.
        | [ .ResourceRecordSets[]
            | select(.Type == "TXT")
            | select((.ResourceRecords // []) | map(.Value) | join(" ") | test("heritage=external-dns"))
            | . as $txt
            | select($orphan_names | any(. as $name
                | $txt.Name == $name
                or $txt.Name == ("a-" + $name)
                or $txt.Name == ("aaaa-" + $name)
                or $txt.Name == ("cname-" + $name)))
          ] as $registry
        # Names that will still exist in the zone once the records above are gone.
        | ([ .ResourceRecordSets[].Name ] - ($orphans + $registry | map(.Name)) | unique) as $surviving_names
        # Domain validation records: cert-manager DNS01 challenges and the CNAMEs
        # ACM asks for. Both only make sense while the name they validate exists;
        # once that name is gone they are dead weight and, unlike everything above,
        # nothing else will ever remove them.
        # Matched by their exact purpose rather than by a generic "label starts
        # with an underscore" rule, which would also catch _dmarc, _domainkey and
        # SRV records that are legitimately unaccompanied by an A record.
        # Race: a challenge published seconds before external-dns creates the
        # record it validates would be collected here. The window is minutes
        # against a daily schedule, and both issuers republish the record on their
        # next reconcile, so the failure mode is a retry, not a lost certificate.
        | [ .ResourceRecordSets[]
            | select((.Type == "TXT" and (.Name | startswith("_acme-challenge.")))
                     or (.Type == "CNAME"
                         and ((.ResourceRecords // [])[0].Value // ""
                              | ascii_downcase | rtrimstr(".") | endswith(".acm-validations.aws"))))
            | select((.Name | sub("^_[^.]+\\."; "")) as $validated
                     | ($surviving_names | index($validated)) | not)
          ] as $validations
        | ($orphans + $registry + $validations) | map({Action: "DELETE", ResourceRecordSet: .})' \
        "$records_file" > "$changes_file"

    total=$(jq 'length' "$changes_file")
    if [ "$total" -eq 0 ]; then
        echo "Zone $zone_name: nothing to delete"
        rm -f "$records_file" "$changes_file"
        continue
    fi

    echo "Zone $zone_name: $total orphaned record(s) to delete"

    if [ "$DRY_RUN" = true ]; then
        jq -r '.[] | "[DRY RUN] Would delete \(.ResourceRecordSet.Type) \(.ResourceRecordSet.Name)"' "$changes_file"
        rm -f "$records_file" "$changes_file"
        continue
    fi

    offset=0
    while [ "$offset" -lt "$total" ]; do
        batch_file=$(mktemp)
        jq -c --argjson offset "$offset" --argjson size "$BATCH_SIZE" \
            '{Changes: .[$offset:$offset + $size]}' "$changes_file" > "$batch_file"

        # Best-effort per batch: a record removed by a concurrent external-dns
        # reconcile makes only its own batch fail, and the next run picks up
        # whatever is left rather than aborting the whole sweep under set -e.
        if aws route53 change-resource-record-sets --hosted-zone-id "$zone_id" \
             --change-batch "file://$batch_file" --query 'ChangeInfo.Id' --output text >/dev/null 2>&1; then
            echo "  deleted $(jq '.Changes | length' "$batch_file") record(s)"
        else
            echo "  batch starting at offset $offset failed, continuing"
        fi
        rm -f "$batch_file"

        offset=$((offset + BATCH_SIZE))
        # Route53 throttles mutating calls at 5 requests per second per account.
        sleep 1
    done

    rm -f "$records_file" "$changes_file"
done
