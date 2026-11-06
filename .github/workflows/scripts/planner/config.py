"""Planner configuration: defaults plus the repository's `.github/planner-config.json`.

The file is read from the PR's **base** commit (trusted), never from the PR head.
"""

from __future__ import annotations

import copy
import json

DEFAULT_CONFIG = {
    "schema": "planner.config/1",
    # (regex on workflow file name, layer, kind). First match wins. Layer = what the suite proves.
    "layers": [
        ["_golden\\.yml$", "L1", "golden plan (offline)"],
        ["internal_global_lint_terraform_test", "L1", "terraform test, mocked providers"],
        ["internal_global_(lint|no_demo_credentials|links|pr_todo_checker|pr_changelog_check)", "L0", "static"],
        ["^local_", "L2", "local kind runtime"],
        ["teleport_tests", "L3", "persistent cluster, namespace isolation"],
        ["common_procedure", "L3", "single cloud resource, doc procedure"],
        ["daily_cleanup", "ops", "cleanup"],
        ["internal_global_(release_stable_branches|branches_workflow_scheduler)", "L5", "release scheduling"],
        ["(_tests|_test|_failover|_integration|_byo_vpc|load_tests|create_destruct_tests)\\.yml$", "L4", "from-scratch cloud composition"],
        ["^internal_", "ops", "housekeeping"],
    ],
    # Suites that are never picked as "the one consumer to run" (special lifecycles).
    "primary_exclude": "failover|integration|load_tests|create_destruct|byo_vpc|golden|teleport",
    # How many leading version components count as a major step, per Renovate depName (default 1).
    "major_level": {
        "amazon-eks": 2, "azure-kubernetes-service": 2, "red-hat-openshift": 2,
        "red-hat-openshift-acm": 2, "kindest/node": 2,
    },
    # Suites the planner may skip (workflow file names). Others are recorded but never enforced.
    "gate_suites": [
        "aws_modules_eks_rds_os_create_destruct_tests.yml",
        "aws_openshift_rosa_hcp_dual_region_tests.yml",
        "aws_eks_multi_region_rdbms_tests.yml",
    ],
    # A change to any of these means the judge itself changed: no narrowing at all.
    "judge_paths": [".github/planner-config.json", ".github/workflows/internal_global_planner*.yml"],
    "expensive_layers": ["L3", "L4"],
    "memory": {"min_full_minutes": 10.0},
    "escape": {"confirm_runs": 1, "max_candidates": 5},
    "learn": {
        "window_days": 180, "half_life_days": 60.0, "prior_a": 1.0, "prior_b": 19.0, "escape_factor": 3.0,
        "weight_slope": 5.0, "weight_min": 1.0, "weight_max": 4.0, "max_step": 0.5,
        "breaker_escapes": 2, "breaker_window_days": 90, "breaker_clean_days": 30, "tie_percent": 10.0,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(text: str | None) -> dict:
    """Merge the repository file (JSON text, may be empty) over the defaults."""
    if not text or not text.strip():
        return copy.deepcopy(DEFAULT_CONFIG)
    over = json.loads(text)
    if not isinstance(over, dict):
        raise ValueError("planner config must be a JSON object")
    return _merge(DEFAULT_CONFIG, over)
