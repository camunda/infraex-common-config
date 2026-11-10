"""`planner gate`: a suite's triage step. Reads the published plan, outputs should_skip. Fails open."""

from __future__ import annotations

import os
import time

from . import checkrun
from .gh import Gh, GhError


def write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as fh:
            fh.write("%s=%s\n" % (name, value))
    print("%s=%s" % (name, value))


def find_plan(gh: Gh, repo: str, head_sha: str, timeout: float, interval: float, sleep=time.sleep, clock=time.monotonic):
    """The plan check run of the commit; polls until the timeout. None = no plan (the caller runs the suite)."""
    deadline = clock() + timeout
    while True:
        try:
            plan = checkrun.newest(gh, repo, head_sha)
        except GhError as e:
            print("::warning::planner-gate: cannot read the plan (%s)" % e)
            plan = None
        if plan is not None:
            return plan
        if clock() >= deadline:
            return None
        sleep(interval)


def decide_skip(plan: dict | None, suite: str):
    """(should_skip, reason). Skips only a gated suite the plan marked skip, in gate mode, for a plan Camunda decided."""
    if plan is None:
        return False, "no plan within the timeout: running as before (fail open)"
    if plan.get("source") != "camunda":
        return False, "the planner failed open (%s): running" % plan.get("reason", "")[:120]
    if plan.get("mode") != "gate":
        return False, "plan mode is %s: nothing is skipped" % plan.get("mode")
    d = (plan.get("decision") or {}).get(suite)
    if d is None:
        return False, "suite not in the plan: running"
    if d.get("action") == "skip" and d.get("enforced"):
        return True, "plan %s: skip (%s)" % (plan.get("decision_id"), "; ".join(d.get("reasons", []))[:200])
    return False, "plan %s: run" % plan.get("decision_id")


def run(a, gh: Gh | None = None, sleep=time.sleep, clock=time.monotonic) -> int:
    if a.event != "pull_request" or str(a.fork).lower() == "true" or not a.pr or not a.head_sha:
        write_output("should_skip", "false")
        print("planner-gate: not a same-repository pull_request run, nothing to gate")
        return 0
    plan = find_plan(gh or Gh(), a.repo, a.head_sha, float(a.timeout), float(a.interval), sleep=sleep, clock=clock)
    skip, reason = decide_skip(plan, a.suite)
    print("planner-gate: %s" % reason)
    write_output("should_skip", "true" if skip else "false")
    return 0
