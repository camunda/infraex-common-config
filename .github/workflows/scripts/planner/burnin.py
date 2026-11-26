"""`planner burnin`: compare the published plan of a pull request with what its suites actually did.

Read-only (`gh` with `actions: read`, `checks: read`, `pull-requests: read`). For the PR's head commit it reads the
check run `planner plan` and, for every suite in the plan, the newest `pull_request` run of that workflow, its jobs
and the log of its `triage` job, where planner-gate prints its answer (`planner-gate: <reason>`, `should_skip=`).
A suite "ran" when a job other than `triage` and the `report*` notification jobs did not end as skipped.

The burn-in in `report` mode proves two things with it. Nothing was skipped by the planner: planner-gate answering
`should_skip=true` while the plan or the gate step is in report mode is a violation, and so is a skipped suite
whose triage log cannot be read and that has no `skip_<suite>` label (exit code 1 for both). And how often the plan
agrees with what happened: a suite the plan would skip that then failed is a would-be escape to look at.
"""

from __future__ import annotations

import json
import re

from . import checkrun
from .gh import Gh, GhError

GATE_LINE = re.compile(r"planner-gate: (.*)$")
SKIP_LINE = re.compile(r"^(?:\S+\s)?should_skip=(true|false)\s*$")


def suite_runs(gh: Gh, repo: str, suite: str, head_sha: str):
    """(run, jobs) of the newest pull_request run of the workflow for the commit; (None, []) when it was not triggered."""
    r = gh.api("GET", "repos/%s/actions/workflows/%s/runs?head_sha=%s&event=pull_request&per_page=20" % (repo, suite, head_sha))
    runs = [x for x in (r or {}).get("workflow_runs", []) if x.get("head_sha") == head_sha]
    if not runs:
        return None, []
    run = max(runs, key=lambda x: (x.get("run_attempt") or 1, x.get("id") or 0))
    j = gh.api("GET", "repos/%s/actions/runs/%d/jobs?per_page=100" % (repo, int(run["id"])))
    return run, (j or {}).get("jobs", [])


# Jobs that do not test anything and can run although triage skipped the suite: notification jobs such as the ROSA
# dual-region `Report success` (`if: !failure() && !cancelled()`, so it succeeds after a skipped chain).
NOT_WORK = re.compile(r"^(triage|report)([\s_-]|$)", re.I)


def execution(run, jobs) -> str:
    """not-triggered | pending | ran | skipped. "ran" = a job other than triage and report jobs did not end as skipped."""
    if run is None:
        return "not-triggered"
    if run.get("status") != "completed":
        return "pending"
    work = [j for j in jobs if not NOT_WORK.match(str(j.get("name", "")))]
    return "ran" if any(j.get("conclusion") != "skipped" for j in work) else "skipped"


def gate_answer(log: str):
    """(should_skip 'true'/'false', reason) printed by planner-gate in a triage log; (None, None) if absent."""
    reason, answer = None, None
    for line in log.splitlines():
        m = GATE_LINE.search(line)
        if m and not m.group(1).startswith("not a same-repository"):
            reason, answer = m.group(1).strip(), None
            continue
        m = SKIP_LINE.search(line)
        if m and reason is not None and answer is None:
            answer = m.group(1)
    return answer, reason


def triage_gate(gh: Gh, repo: str, jobs: list):
    triage = [j for j in jobs if str(j.get("name", "")).startswith("triage")]
    if not triage:
        return None, None
    try:
        return gate_answer(gh.text("repos/%s/actions/jobs/%d/logs" % (repo, int(triage[0]["id"]))))
    except GhError:
        return None, None


def compare(plan: dict, rows_in: dict, labels: list) -> tuple:
    """(rows, violations). rows_in: suite -> dict(execution, conclusion, url, gate, gate_reason)."""
    report = plan["source"] != "camunda" or plan["mode"] != "gate"
    rows, violations = [], []
    for suite, d in sorted(plan["decision"].items()):
        got = rows_in.get(suite) or {"execution": "not-triggered"}
        ex, concl, gate = got["execution"], got.get("conclusion"), got.get("gate")
        label = "skip_" + suite.rsplit(".", 1)[0]
        bad = None
        if gate == "true" and (report or "report mode" in (got.get("gate_reason") or "")):
            bad = "VIOLATION: planner-gate skipped in report mode"
        elif gate == "true" and not (d["action"] == "skip" and d["enforced"]):
            bad = "VIOLATION: planner-gate skipped a suite the plan runs"
        elif ex == "skipped" and gate is None and label not in labels:
            bad = "VIOLATION?: skipped, no skip label and no planner-gate answer in the triage log (look at the run)"
        if bad:
            violations.append(suite)
            note = bad
        elif ex == "skipped":
            note = "skipped by %s" % ("the label " + label if label in labels else "triage (label or checklist), planner-gate said %s" % gate)
        elif d["action"] == "skip" and ex == "ran":
            note = "plan would skip; ran: %s" % concl + (" (would-be escape, check it)" if concl == "failure" else "")
        elif d["action"] == "run" and ex == "not-triggered" and d["layer"] not in ("L0", "L1", "L2"):
            note = "plan runs it, the path filter did not trigger it"
        else:
            note = ""
        rows.append({"suite": suite, "plan": d["action"], "enforced": d["enforced"], "layer": d["layer"], "rotation": suite == plan.get("rotation_picked"),
                     "execution": ex, "conclusion": concl, "url": got.get("url", ""), "gate": gate, "note": note})
    return rows, violations


def summary_md(plan: dict, rows: list, violations: list) -> str:
    L = ["## Planner burn-in: PR #%s @ %s" % (plan["pr"], plan["head_sha"][:12]), "",
         "Plan `%s`, source %s, mode %s, rotation %s." % (plan.get("decision_id") or "-", plan["source"], plan["mode"], plan.get("rotation_picked") or "-"), ""]
    L += ["**%d violation(s)**: %s" % (len(violations), ", ".join(violations)) if violations else "No suite was skipped against the plan's mode.", ""]
    agree = sum(1 for r in rows if r["execution"] in ("ran", "skipped") and (r["plan"] == "run") == (r["execution"] == "ran"))
    would_skip_ran = sum(1 for r in rows if r["plan"] == "skip" and r["execution"] == "ran")
    L += ["Triggered suites where plan and execution agree: %d; the plan would skip but it ran: %d." % (agree, would_skip_ran), "",
          "| Suite | Layer | Plan | Enforced | Rotation | Execution | Conclusion | Gate | Note |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append("| `%s` | %s | %s | %s | %s | %s | %s | %s | %s |" % (r["suite"], r["layer"], r["plan"], "yes" if r["enforced"] else "no",
                                                                    "yes" if r["rotation"] else "", r["execution"], r["conclusion"] or "-",
                                                                    r["gate"] or "-", r["note"]))
    return "\n".join(L) + "\n"


def run(a, gh: Gh | None = None) -> int:
    gh = gh or Gh()
    try:
        pull = gh.api("GET", "repos/%s/pulls/%d" % (a.repo, int(a.pr))) or {}
        head = a.head_sha or pull["head"]["sha"]
        labels = [str(x.get("name")) for x in pull.get("labels") or [] if isinstance(x, dict)]
        plan = checkrun.newest(gh, a.repo, head)
        if plan is None:
            print("::error::burnin: no valid plan check run on %s" % head)
            return 2
        got = {}
        for suite in sorted(plan["decision"]):
            run_, jobs = suite_runs(gh, a.repo, suite, head)
            gate, reason = triage_gate(gh, a.repo, jobs) if run_ else (None, None)
            got[suite] = {"execution": execution(run_, jobs), "conclusion": (run_ or {}).get("conclusion"),
                          "url": (run_ or {}).get("html_url", ""), "gate": gate, "gate_reason": reason}
    except (GhError, KeyError, ValueError) as e:
        print("::error::burnin: %s" % e)
        return 2
    rows, violations = compare(plan, got, labels)
    md = summary_md(plan, rows, violations)
    print(md)
    if a.summary_file:
        with open(a.summary_file, "a") as fh:
            fh.write(md)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump({"pr": plan["pr"], "head_sha": head, "decision_id": plan.get("decision_id"), "source": plan["source"], "mode": plan["mode"],
                       "rotation_picked": plan.get("rotation_picked"), "rows": rows, "violations": violations}, fh, indent=1, sort_keys=True)
    return 1 if violations else 0
