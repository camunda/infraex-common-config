"""`planner runs`: recent successful full runs of the gated and rotated suites, for memory.

Used by the planner job until the store has outcome events. Read-only API calls, one per suite.
"""

from __future__ import annotations

import json

from . import config, gitutil, registry
from .gh import Gh, GhError


def run(a, gh: Gh | None = None) -> int:
    gh = gh or Gh()
    cfg = config.load(gitutil.show(a.git_dir, a.base_sha, a.config_path))
    reg = registry.load_registry(a.git_dir, a.base_sha, cfg)
    out: list = []
    for name, s in sorted(reg.items()):
        if s.layer not in cfg["expensive_layers"] or s.is_call_only:
            continue
        for event in ("schedule", "workflow_dispatch"):
            path = "repos/%s/actions/workflows/%s/runs?branch=%s&event=%s&status=success&per_page=%d" % (
                a.repo, name, a.default_branch, event, int(a.per_page))
            try:
                r = gh.api("GET", path)
            except GhError as e:
                print("::warning::cannot list runs of %s: %s" % (name, e))
                continue
            for run_ in (r or {}).get("workflow_runs", []):
                run_["path"] = ".github/workflows/%s" % name
                out.append({k: run_.get(k) for k in ("id", "path", "status", "conclusion", "event", "head_branch", "head_sha",
                                                     "created_at", "run_started_at", "updated_at")})
    with open(a.out, "w") as fh:
        json.dump(out, fh)
    print("runs: %d successful runs of %d suites" % (len(out), len({r["path"] for r in out})))
    return 0
