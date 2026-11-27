"""The plan travels from the planner workflow to the suites as the output of a check run.

Check runs belong to the commit, are readable by every job of the repository with `checks: read`, and
survive for the life of the repository, so the same record also serves the escape attribution later.
The system of record is Elasticsearch (written by the Camunda process); this is only the transport.
"""

from __future__ import annotations

import json

from . import render, schema
from .gh import Gh, GhError

NAME = "planner plan"


def publish(gh: Gh, repo: str, plan: dict, summary_md: str) -> dict:
    text = json.dumps(plan, sort_keys=True)
    body = {"name": NAME, "head_sha": plan["head_sha"], "status": "completed", "conclusion": "neutral",
            "output": {"title": ("Test plan %s" % (plan.get("decision_id") or "(fail open)"))[:200], "summary": summary_md[:60000], "text": text}}
    return gh.api("POST", "repos/%s/check-runs" % repo, body)


def newest(gh: Gh, repo: str, head_sha: str):
    """Newest plan check run for the commit created by GitHub Actions, parsed and validated; None if absent."""
    r = gh.api("GET", "repos/%s/commits/%s/check-runs?check_name=%s&filter=latest" % (repo, head_sha, NAME.replace(" ", "%20")))
    runs = [c for c in (r or {}).get("check_runs", []) if c.get("name") == NAME and (c.get("app") or {}).get("slug") == "github-actions"]
    if not runs:
        return None
    runs.sort(key=lambda c: (c.get("completed_at") or "", c.get("id") or 0))
    try:
        plan = json.loads(((runs[-1].get("output") or {}).get("text")) or "")
    except ValueError:
        return None
    if schema.validate_plan(plan) or plan.get("head_sha") != head_sha:
        return None
    return plan


def plan_summary(plan: dict) -> str:
    """Markdown for the sticky comment and the job summary, built from the validated plan only."""
    esc = render.esc
    run = [(n, v) for n, v in sorted(plan["decision"].items()) if v["action"] == "run" and v["layer"] not in ("L0", "L1", "L2")]
    skip = [(n, v) for n, v in sorted(plan["decision"].items()) if v["action"] == "skip"]
    L = [render.MARKER, "## Test selection", ""]
    if plan["source"] == "fail-open":
        L += ["The planner could not decide (%s). **Nothing is skipped**: the suites run as the path filters select them." % esc(plan["reason"], 200), ""]
        return "\n".join(L) + "\n"
    L += ["Mode **%s**, decided by the Camunda process (record `%s`%s)." % (esc(plan["mode"]), esc(plan.get("decision_id") or "-", 80),
                                                                          "" if plan["recorded"] else ", not recorded"), ""]
    if plan["mode"] == "report":
        L += ["Report only: nothing is skipped by this decision.", ""]
    L += ["**Runs:** %d cloud suites. **Skips:** %d suites." % (len(run), len(skip)), ""]
    if run:
        L += ["| Suite | Layer | Why |", "|---|---|---|"]
        for n, v in run:
            L.append("| `%s` | %s | %s |" % (esc(n, 100), esc(v["layer"]), esc("; ".join(v["reasons"]), 300)))
        L.append("")
    if skip:
        L += ["<details><summary>Skipped suites</summary>", "", "| Suite | Enforced |", "|---|---|"]
        for n, v in skip:
            L.append("| `%s` | %s |" % (esc(n, 100), "yes" if v["enforced"] else "no"))
        L += ["", "</details>", ""]
    if plan["rules_fired"]:
        L += ["<details><summary>Rules fired</summary>", ""]
        for r in plan["rules_fired"][:30]:
            L.append("- **%s** `%s`: %s" % (esc(r["rule"]), esc(r["subject"], 120), esc(r["reason"], 240)))
        L += ["", "</details>", ""]
    if plan["advisor_added"]:
        L += ["AI advisor added: %s." % ", ".join("`%s`" % esc(x, 100) for x in plan["advisor_added"]), ""]
    L += ["Optional escape hatches: label `planner:run-all` restores the full path-triggered set; `run_<suite>` forces one suite. "
          "No label or approval is needed for the normal path."]
    return "\n".join(L) + "\n"
