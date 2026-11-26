"""A small synthetic repository shaped like camunda-deployment-references, built with real git."""

from __future__ import annotations

import os
import subprocess
import tempfile

ENV = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")


def git(repo, *args):
    p = subprocess.run(["git", "-C", repo, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false"] + list(args),
                       capture_output=True, text=True, env=ENV)
    if p.returncode != 0:
        raise RuntimeError("git %s: %s" % (args, p.stderr))
    return p.stdout.strip()


def write(repo, path, text):
    p = os.path.join(repo, path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(text)


def workflow(name, paths, cron=None, extra=""):
    on = "on:\n  pull_request:\n    paths:\n" + "".join("      - '%s'\n" % p for p in paths) if paths else "on:\n  pull_request:\n"
    if cron:
        on += "  schedule:\n    - cron: '%s'\n" % cron
    on += "  workflow_dispatch:\n"
    return "name: %s\n%sjobs:\n  triage:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./.github/actions/internal-triage-skip\n%s" % (name, on, extra)


AURORA_VARS = '''variable "engine_version" {
  type = string
  # renovate: datasource=custom.aurora-pg-camunda depName=aurora-postgresql versioning=loose
  default     = "17.9"
  description = "engine"
}

variable "cluster_name" {
  type = string
}
'''
AURORA_OUT = '''output "aurora_endpoint" {
  value = "x"
}

output "aurora_id" {
  value = "y"
}
'''
VPN_VARS = 'variable "vpc_id" {\n  type = string\n}\n'
VPN_OUT = 'output "vpn_id" {\n  value = "v"\n}\n'
RA_DB = '''module "postgresql" {
  source = "../../../../modules/aurora"
  # renovate: datasource=custom.aurora-pg-camunda depName=aurora-postgresql versioning=loose
  engine_version = "17.9"
  cluster_name   = "x"
}
'''
RA_VPN = 'module "vpn" {\n  source = "../../../../modules/vpn"\n  vpc_id = "v"\n}\n'
GO_TEST = 'package test\n\nfunc TestAurora() {\n\ttfModuleAurora := "aurora/"\n\t_ = tfModuleAurora\n}\n'


def make_repo(root: str | None = None) -> dict:
    repo = root or tempfile.mkdtemp(prefix="planner-fixture-")
    git(repo, "init", "-q", "-b", "main")
    wf = ".github/workflows/"
    mod = ["aws/modules/**"]
    write(repo, wf + "internal_global_lint.yml", workflow("Lint", None))
    write(repo, wf + "aws_eks_single_region_golden.yml", workflow("Golden eks", ["aws/kubernetes/eks-single-region/**", "aws/modules/aurora/**", "aws/modules/vpn/**"]))
    write(repo, wf + "aws_ecs_single_region_fargate_golden.yml", workflow("Golden ecs", ["aws/containers/ecs-single-region-fargate/**", "aws/modules/aurora/**"]))
    write(repo, wf + "aws_modules_eks_rds_os_tests.yml", workflow("Modules", mod, "0 1 * * 2", "      # runs aws/modules/.test/src\n"))
    write(repo, wf + "aws_modules_eks_rds_os_create_destruct_tests.yml", workflow("Create destruct", mod, "0 1 * * 2"))
    write(repo, wf + "aws_eks_single_region_tests.yml", workflow("EKS single", ["aws/kubernetes/eks-single-region/**", ".tool-versions", ".github/actions/shared/**"], "0 3 * * 3"))
    write(repo, wf + "aws_ecs_single_region_fargate_tests.yml", workflow("ECS single", ["aws/containers/ecs-single-region-fargate/**", ".tool-versions"], "30 3 * * 3"))
    write(repo, wf + "aws_ecs_dual_region_fargate_tests.yml", workflow("ECS dual", ["aws/containers/ecs-dual-region-fargate/**", "aws/modules/**"]))
    write(repo, wf + "aws_ecs_dual_region_fargate_failover.yml", workflow("ECS failover", ["aws/containers/ecs-dual-region-fargate/**", "aws/modules/**"]))
    write(repo, wf + "aws_openshift_rosa_hcp_dual_region_tests.yml", workflow("ROSA dual", ["aws/openshift/**", ".tool-versions"], "0 3 * * 2"))
    write(repo, wf + "aws_eks_single_region_daily_cleanup.yml", workflow("Cleanup", ["aws/kubernetes/**"], "0 4 * * *"))
    write(repo, wf + "internal_global_maintenance.yml", workflow("Maintenance", [".github/workflows/internal_global_maintenance.yml"]))  # layer ops
    write(repo, ".github/actions/shared/action.yml", "name: shared\nruns:\n  using: composite\n  steps: []\n")
    write(repo, ".github/actions/aws-create/action.yml", "name: create\nruns:\n  using: composite\n  steps:\n    - uses: ./.github/actions/shared\n")
    write(repo, wf + "aws_openshift_rosa_hcp_dual_region_tests.yml", workflow("ROSA dual", ["aws/openshift/**", ".tool-versions", ".github/actions/**"], "0 3 * * 2", "      - uses: ./.github/actions/aws-create\n"))
    write(repo, "aws/modules/aurora/variables.tf", AURORA_VARS)
    write(repo, "aws/modules/aurora/outputs.tf", AURORA_OUT)
    write(repo, "aws/modules/aurora/main.tf", 'resource "x" "y" {}\n')
    write(repo, "aws/modules/vpn/variables.tf", VPN_VARS)
    write(repo, "aws/modules/vpn/outputs.tf", VPN_OUT)
    write(repo, "aws/modules/vpn/main.tf", 'resource "x" "v" {}\n')
    write(repo, "aws/modules/.test/src/aurora_test.go", GO_TEST)
    write(repo, "aws/kubernetes/eks-single-region/terraform/cluster/db.tf", RA_DB)
    write(repo, "aws/kubernetes/eks-single-region/terraform/cluster/vpn.tf", RA_VPN)
    write(repo, "aws/kubernetes/eks-single-region/terraform/cluster/main.tf", 'resource "a" "b" {}\n')
    write(repo, "aws/containers/ecs-single-region-fargate/terraform/cluster/db.tf", RA_DB)
    write(repo, "aws/containers/ecs-single-region-fargate/terraform/cluster/main.tf", 'resource "a" "b" {}\n')
    write(repo, "aws/containers/ecs-dual-region-fargate/terraform/infra/db.tf", RA_DB)
    write(repo, "README.md", "readme\n")
    write(repo, ".tool-versions", "terraform 1.9.0\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "chore: base")
    base = git(repo, "rev-parse", "HEAD")
    return {"repo": repo, "base": base}


def commit_change(fx: dict, name: str, edits: dict) -> str:
    """Branch from base, apply {path: new text or callable(old)->new}, commit, return head sha."""
    repo = fx["repo"]
    git(repo, "checkout", "-q", "-B", name, fx["base"])
    for path, new in edits.items():
        p = os.path.join(repo, path)
        old = open(p).read() if os.path.exists(p) else ""
        write(repo, path, new(old) if callable(new) else new)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fix: %s" % name)
    return git(repo, "rev-parse", "HEAD")
