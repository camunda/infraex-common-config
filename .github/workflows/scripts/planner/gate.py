"""`planner gate`: a suite's triage step. Reads the decision, outputs should_skip. Fails open."""

from __future__ import annotations

import json
import os
import sys
import time

from . import store
from .gh import Gh, GhError


def write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as fh:
            fh.write("%s=%s\n" % (name, value))
    print("%s=%s" % (name, value))


def find_decision(st: store.GhStore, pr: int, head_sha: str, timeout: float, interval: float, sleep=time.sleep, clock=time.monotonic):
    """Newest decision file for (pr, head sha); polls until the timeout. None = no decision."""
    deadline = clock() + timeout
    d = "decisions/pr/%d/%s" % (pr, head_sha)
    while True:
        try:
            names = [n for n in st.list_dir(d) if n.endswith(".json")]
        except GhError as e:
            print("::warning::planner-gate: cannot read the store (%s)" % e)
            names = []
        if names:
            raw = st.get("%s/%s" % (d, names[-1]))
            if raw:
                try:
                    return json.loads(raw.decode())
                except ValueError:
                    print("::warning::planner-gate: unreadable decision file")
        if clock() >= deadline:
            return None
        sleep(interval)


def decide_skip(decision: dict | None, suite: str):
    """(should_skip, reason). Skips only a gated suite the plan marked skip, in gate mode."""
    if decision is None:
        return False, "no decision within the timeout: running as before (fail open)"
    if decision.get("mode") != "gate":
        return False, "decision mode is %s: nothing is skipped" % decision.get("mode")
    d = (decision.get("decision") or {}).get(suite)
    if d is None:
        return False, "suite not in the decision: running"
    if d.get("action") == "skip" and d.get("enforced"):
        return True, "planner decision %s: skip (%s)" % (decision.get("id"), "; ".join(d.get("reasons", []))[:200])
    return False, "planner decision %s: run" % decision.get("id")


def run(a, gh: Gh | None = None, sleep=time.sleep, clock=time.monotonic) -> int:
    if a.event != "pull_request" or str(a.fork).lower() == "true" or not a.pr or not a.head_sha:
        write_output("should_skip", "false")
        print("planner-gate: not a same-repository pull_request run, nothing to gate")
        return 0
    st = store.GhStore(gh or Gh(), a.repo, a.branch)
    decision = find_decision(st, int(a.pr), a.head_sha, float(a.timeout), float(a.interval), sleep=sleep, clock=clock)
    skip, reason = decide_skip(decision, a.suite)
    print("planner-gate: %s" % reason)
    write_output("should_skip", "true" if skip else "false")
    return 0
