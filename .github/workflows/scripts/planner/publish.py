"""`planner publish`: post the plan as a check run, a status and the sticky comment (write job, no Camunda secret)."""

from __future__ import annotations

import json

from . import checkrun, render, schema
from .gh import Gh, GhError


def _comment(gh: Gh, repo: str, pr: int, body: str) -> None:
    existing = None
    for c in gh.api("GET", "repos/%s/issues/%d/comments?per_page=100" % (repo, pr), paginate=True) or []:
        if render.MARKER in (c.get("body") or "") and (c.get("user") or {}).get("type") == "Bot":
            existing = c["id"]
            break
    if existing:
        gh.api("PATCH", "repos/%s/issues/comments/%d" % (repo, existing), {"body": body})
    else:
        gh.api("POST", "repos/%s/issues/%d/comments" % (repo, pr), {"body": body})


def run(a, gh: Gh | None = None) -> int:
    gh = gh or Gh()
    with open(a.plan) as fh:
        plan = json.load(fh)
    errs = schema.validate_plan(plan)
    if errs:
        print("::error::plan rejected: %s" % "; ".join(errs[:5]))
        return 2
    if plan["repo"] != a.repo or plan["pr"] != int(a.pr) or plan["head_sha"] != a.head_sha:
        print("::error::plan does not match the pull request context (repo, pr, head sha)")
        return 2
    md = checkrun.plan_summary(plan)
    if a.summary_file:
        with open(a.summary_file, "a") as fh:
            fh.write(md)
    checkrun.publish(gh, a.repo, plan, md)
    print("published the plan check run (%s)" % plan["source"])
    if not a.no_comment:
        try:
            _comment(gh, a.repo, plan["pr"], md)
        except GhError as e:
            print("::warning::could not post the planner comment: %s" % e)
    if not a.no_status:
        run_n = sum(1 for v in plan["decision"].values() if v["action"] == "run" and v["layer"] in ("L3", "L4"))
        skip_n = sum(1 for v in plan["decision"].values() if v["action"] == "skip")
        desc = "fail open: nothing skipped" if plan["source"] == "fail-open" else "%s: run %d cloud, skip %d" % (plan["mode"], run_n, skip_n)
        try:
            gh.api("POST", "repos/%s/statuses/%s" % (a.repo, plan["head_sha"]),
                   {"state": "success", "context": "planner / decision", "description": desc[:140]})
        except GhError as e:
            print("::warning::could not set the planner status: %s" % e)
    return 0
