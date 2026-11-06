import argparse
import json
import os

from .. import decide


def args(fx, head, **kw):
    ns = dict(repo=fx["repo"], slug="camunda/example", pr=7, head_sha=head, base_sha=fx["base"], event="pull_request",
              actor="dev", fork="false", labels="[]", mode="gate", base_branch="main", default_branch="main",
              gate_base_branches="main", gate_suites="", weights_pin="", config_path=".github/planner-config.json",
              store_dir="", runs_json="", planner_version="test", now="2026-11-02T20:00:00Z")
    ns.update(kw)
    return argparse.Namespace(**ns)


def decision(fx, head, **kw):
    return decide.run(args(fx, head, **kw))


def runs_json(tmp, items):
    p = os.path.join(tmp, "runs.json")
    with open(p, "w") as fh:
        json.dump(items, fh)
    return p


def run_rec(path, event="schedule", minutes=120, ended="2026-10-28T05:00:00Z", conclusion="success", branch="main", rid=1):
    from datetime import datetime, timedelta
    end = datetime.strptime(ended, "%Y-%m-%dT%H:%M:%SZ")
    start = end - timedelta(minutes=minutes)
    return {"id": rid, "path": ".github/workflows/" + path, "status": "completed", "conclusion": conclusion, "event": event,
            "head_branch": branch, "head_sha": "a" * 40, "run_started_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "created_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "updated_at": ended}
