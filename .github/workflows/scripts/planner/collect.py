"""`planner collect`: outcomes of finished suite runs and confirmed escapes, sent to the Camunda process.

Runs on a schedule in the repository (no PR code, no PR content beyond run metadata). The documents go
to Elasticsearch through the `ci-test-record` process; their ids are stable, so a window that overlaps
the previous one writes nothing twice. If Camunda is unreachable the next run sends them again.

Each outcome carries a class: pass, fail, blocked (the suite could not run), known_failure (listed in
the checked-in catalogue) or cancelled. Only `fail` feeds risk and escapes.
"""

from __future__ import annotations

import re
import time
from datetime import timedelta
from urllib.parse import quote

from . import SCHEMA, checkrun, config, gitutil, known_failures, registry, schema
from .camunda import RECORD_PROCESS_ID, Client, CamundaError
from .gh import Gh, GhError
from .util import SHA_RE, event_id, now_iso, parse_iso

OUT_INDEX, ESC_INDEX = "ci-planner-outcomes", "ci-planner-escapes"


def outcome_from_run(repo: str, run: dict, suite: str, decision_id: str | None, flake_hint: bool, ts: str,
                     outcome: str | None = None, reason: str | None = None, kf_id: str | None = None,
                     modules=None, min_full: float | None = None) -> dict | None:
    if run.get("status") != "completed" or not run.get("conclusion"):
        return None
    start = run.get("run_started_at") or run.get("created_at")
    end = run.get("updated_at")
    if not start or not end or not SHA_RE.match(run.get("head_sha") or ""):
        return None
    prs = run.get("pull_requests") or []
    pr = int(prs[0]["number"]) if prs and isinstance(prs[0], dict) and "number" in prs[0] else 0
    attempt = int(run.get("run_attempt") or 1)
    conclusion = str(run["conclusion"])
    klass = outcome or ("pass" if conclusion == "success" else "fail" if conclusion == "failure" else "cancelled")
    minutes = round(max(0.0, (parse_iso(end) - parse_iso(start)).total_seconds() / 60), 1)
    body = {
        "schema": SCHEMA, "type": "outcome", "ts": ts, "repo": repo, "decision_id": decision_id, "pr": pr,
        "head_sha": run["head_sha"], "suite": suite, "run_id": int(run["id"]), "attempt": attempt,
        "event": str(run.get("event", "")), "branch": str(run.get("head_branch") or "")[:200], "conclusion": conclusion,
        "started": parse_iso(start).strftime("%Y-%m-%dT%H:%M:%SZ"), "completed": parse_iso(end).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "minutes": minutes, "url": str(run.get("html_url", ""))[:300], "flake_hint": bool(flake_hint), "outcome": klass,
    }
    if reason:
        body["reason"] = reason[:200]
    if kf_id:
        body["known_failure_id"] = kf_id
    if modules:
        body["modules"] = list(modules)[:100]
    if min_full is not None:
        body["full"] = minutes >= min_full
    body["id"] = event_id("out", {"run": body["run_id"], "attempt": attempt})
    return body


def decision_from_plan(plan: dict) -> dict:
    """The shape escape attribution reads, from a published planner.plan/1."""
    return {"id": plan.get("decision_id") or "pr-%d" % plan["pr"], "pr": plan["pr"], "decision": plan["decision"],
            "impacted": {"modules": plan["modules"], "consumers": plan["consumers"]}}


def attribute_escapes(repo: str, outcomes: list, decisions_by_pr: dict, merged_between, cfg: dict, ts: str) -> list:
    """Escape events for confirmed failures of scheduled full runs.

    outcomes: outcome events of scheduled/dispatched runs on the default branch. merged_between(good_sha, bad_sha)
    -> [(pr, sha)] of pull requests merged in (good, bad]. A failure is confirmed when the previous scheduled run
    of the suite also failed, or when this one failed again on a re-attempt. Only `fail` outcomes count (not
    blocked, known or cancelled). Only decisions that did not run the suite and touched a module it consumes
    become candidates.
    """
    ecfg = cfg["escape"]
    by_suite: dict = {}
    for o in outcomes:
        if o["event"] in ("schedule", "workflow_dispatch", "push") and o["minutes"] >= cfg["memory"]["min_full_minutes"]:
            by_suite.setdefault(o["suite"], []).append(o)
    is_fail = lambda o: o["conclusion"] == "failure" and o.get("outcome", "fail") == "fail"
    out = []
    for suite, runs in by_suite.items():
        runs.sort(key=lambda o: o["completed"])
        for i, o in enumerate(runs):
            if not is_fail(o):
                continue
            confirmed_by = None
            if o["attempt"] > 1:
                confirmed_by = "failed again on attempt %d" % o["attempt"]
            elif i > 0 and is_fail(runs[i - 1]):
                confirmed_by = "the previous scheduled run failed too"
            if not confirmed_by:
                continue
            good = next((r for r in reversed(runs[:i]) if r["conclusion"] == "success"), None)
            if good is None:
                continue
            cands = []
            for pr, _sha in merged_between(good["head_sha"], o["head_sha"]):
                d = decisions_by_pr.get(pr)
                if not d:
                    continue
                dec = d["decision"].get(suite)
                if dec is not None and dec["action"] == "run":
                    continue  # the suite ran for that PR: caught or independent
                for m in d["impacted"]["modules"]:
                    if suite in d["impacted"].get("consumers", {}).get(m, []):
                        cands.append({"decision_id": d["id"], "pr": pr, "module": m})
            if not cands:
                continue
            cap = int(ecfg["max_candidates"])
            confidence = 1.0 if len(cands) <= cap else round(cap / float(len(cands)), 2)
            cands = cands[:cap]
            for c in cands:
                c["share"] = round(1.0 / len(cands), 3)
            body = {"schema": SCHEMA, "type": "escape", "ts": ts, "repo": repo, "suite": suite,
                    "failing_run": {"id": o["run_id"], "sha": o["head_sha"], "at": o["completed"]},
                    "good_run": {"id": good["run_id"], "sha": good["head_sha"], "at": good["completed"]},
                    "candidates": cands, "confidence": confidence, "confirmed_by": confirmed_by}
            body["id"] = event_id("esc", {"suite": suite, "run": o["run_id"], "attempt": o["attempt"]})
            out.append(body)
    return out


def merged_prs_from_git(git_dir: str):
    def f(good: str, bad: str):
        out = gitutil.run(git_dir, "log", "--format=%H%x09%s", "%s..%s" % (good, bad), check=False)
        res = []
        for line in out.splitlines():
            sha, _, subj = line.partition("\t")
            m = re.search(r"\(#(\d+)\)\s*$", subj)
            if m:
                res.append((int(m.group(1)), sha))
        return res
    return f


# ---------------------------------------------------------------------------------------------
# documents for Elasticsearch

def outcome_doc(o: dict) -> dict:
    return {"outcome_id": o["id"], "ts": o["completed"], "repo": o["repo"], "suite": o["suite"], "run_id": o["run_id"], "attempt": o["attempt"],
            "pr": o["pr"], "head_sha": o["head_sha"], "event": o["event"], "branch": o["branch"], "conclusion": o["conclusion"],
            "outcome": o.get("outcome", "pass"), "reason": o.get("reason"), "known_failure_id": o.get("known_failure_id"),
            "minutes": o["minutes"], "completed": o["completed"], "started": o["started"], "url": o["url"], "flake_hint": o["flake_hint"],
            "decision_id": o.get("decision_id"), "modules": o.get("modules", []), "full": o.get("full", False)}


def escape_docs(e: dict) -> list:
    return [{"escape_id": e["id"], "ts": e["ts"], "repo": e["repo"], "suite": e["suite"], "module": c["module"], "pair": "%s|%s" % (c["module"], e["suite"]),
             "share": c["share"], "pr": c["pr"], "decision_id": c["decision_id"], "confidence": e["confidence"], "confirmed_by": e["confirmed_by"],
             "failing_run": e["failing_run"]["id"], "good_run": e["good_run"]["id"]} for c in e["candidates"]]


def to_batches(docs: list, size: int = 100) -> list:
    return [docs[i:i + size] for i in range(0, len(docs), size)]


# ---------------------------------------------------------------------------------------------

def _runs(gh: Gh, repo: str, suite: str, query: str) -> list:
    path = "repos/%s/actions/workflows/%s/runs?status=completed&per_page=100&%s" % (repo, suite, query)
    flat = []
    for page in gh.api("GET", path, paginate=True) or []:
        flat += page.get("workflow_runs", [page]) if isinstance(page, dict) else []
    return flat


def _failed_names(gh: Gh, repo: str, run_id) -> list:
    try:
        r = gh.api("GET", "repos/%s/actions/runs/%s/jobs?per_page=100&filter=latest" % (repo, run_id))
    except GhError:
        return []
    names = []
    for j in (r or {}).get("jobs", []):
        if j.get("conclusion") == "failure":
            names.append(str(j.get("name", "")))
            names += [str(s.get("name", "")) for s in j.get("steps", []) if s.get("conclusion") == "failure"]
    return names


def run(a, gh: Gh | None = None, client: Client | None = None, now: str | None = None) -> int:
    gh = gh or Gh()
    now = a.now or now or now_iso()
    cfg = config.load(gitutil.show(a.git_dir, "HEAD", a.config_path))
    reg = registry.load_registry(a.git_dir, "HEAD", cfg)
    kf = known_failures.load(gitutil.show(a.git_dir, "HEAD", a.known_failures))
    suites = sorted(n for n, s in reg.items() if s.layer in cfg["expensive_layers"] and not s.is_call_only)
    since = (parse_iso(now) - timedelta(hours=float(a.since_hours))).strftime("%Y-%m-%dT%H:%M:%SZ")
    min_full = float(cfg["memory"]["min_full_minutes"])
    plans: dict = {}

    def plan_for(sha):
        if sha not in plans:
            try:
                plans[sha] = checkrun.newest(gh, a.repo, sha)
            except GhError:
                plans[sha] = None
        return plans[sha]

    def classify(run_, suite):
        if run_.get("conclusion") != "failure":
            return None
        names = _failed_names(gh, a.repo, run_["id"])
        return known_failures.classify(kf, suite, names)

    outcomes, sched = [], []
    for suite in suites:
        try:
            window = _runs(gh, a.repo, suite, "created=%s" % quote(">=" + since))
            recent_sched = _runs(gh, a.repo, suite, "branch=%s&event=schedule" % quote(a.default_branch))[:10]
        except GhError as e:
            print("::warning::cannot list runs of %s: %s" % (suite, e))
            continue
        for run_ in sorted(window, key=lambda x: x.get("updated_at", "")):
            attempt = int(run_.get("run_attempt") or 1)
            flake = False
            if attempt > 1 and run_.get("conclusion") == "success":
                for k in range(1, attempt):
                    try:
                        prior = gh.api("GET", "repos/%s/actions/runs/%s/attempts/%d" % (a.repo, run_["id"], k))
                        flake = flake or bool(prior and prior.get("conclusion") == "failure")
                    except GhError:
                        pass
            klass, kfid, reason = classify(run_, suite) or (None, None, None)
            prs = run_.get("pull_requests") or []
            pr = int(prs[0]["number"]) if prs and isinstance(prs[0], dict) and "number" in prs[0] else 0
            plan = plan_for(run_.get("head_sha")) if pr and SHA_RE.match(run_.get("head_sha") or "") else None
            o = outcome_from_run(a.repo, run_, suite, (plan or {}).get("decision_id"), flake, now, klass, reason, kfid,
                                 (plan or {}).get("modules"), min_full)
            if o and not schema.validate(o):
                outcomes.append(o)
        for run_ in recent_sched:
            klass, kfid, reason = classify(run_, suite) or (None, None, None)
            o = outcome_from_run(a.repo, run_, suite, None, False, now, klass, reason, kfid, None, min_full)
            if o and not schema.validate(o):
                sched.append(o)
    # escapes: scheduled failures that are confirmed, attributed to merged PRs whose plan skipped the suite
    by_pr: dict = {}
    candidates_pr = set()
    merged = merged_prs_from_git(a.git_dir)
    seen_failures = [o for o in sched if o["conclusion"] == "failure" and o.get("outcome", "fail") == "fail"]
    for o in seen_failures:
        good = next((r for r in sorted(sched, key=lambda x: x["completed"], reverse=True) if r["suite"] == o["suite"] and r["conclusion"] == "success" and r["completed"] < o["completed"]), None)
        if good:
            candidates_pr |= {pr for pr, _ in merged(good["head_sha"], o["head_sha"])}
    for pr in sorted(candidates_pr):
        try:
            head = (gh.api("GET", "repos/%s/pulls/%d" % (a.repo, pr)) or {}).get("head", {}).get("sha")
        except GhError:
            continue
        plan = plan_for(head) if head else None
        if plan and plan.get("source") == "camunda":
            by_pr[pr] = decision_from_plan(plan)
    escapes = attribute_escapes(a.repo, sched, by_pr, merged, cfg, now)
    docs = [{"index": OUT_INDEX, "id": o["id"], "body": outcome_doc(o)} for o in outcomes]
    for e in escapes:
        for i, d in enumerate(escape_docs(e)):
            docs.append({"index": ESC_INDEX, "id": "%s-%d" % (e["id"], i), "body": d})
    docs = docs[:int(a.max_docs)]
    summary = "collector: %d outcomes (%s), %d escape documents, %d suites checked" % (
        len(outcomes), ", ".join("%s %d" % (k, sum(1 for o in outcomes if o.get("outcome") == k)) for k in ("pass", "fail", "blocked", "known_failure", "cancelled")),
        sum(1 for d in docs if d["index"] == ESC_INDEX), len(suites))
    print(summary)
    if a.dry_run or not docs:
        if a.dry_run:
            import json
            print(json.dumps(docs, indent=1)[:20000])
        return 0
    try:
        c = client or Client.from_env()
        for batch in to_batches(docs):
            c.create_instance(RECORD_PROCESS_ID, {"docs": batch})
        print("sent %d documents to Camunda" % len(docs))
    except (CamundaError, OSError) as e:
        print("::warning::could not send the documents to Camunda (the next run sends them again): %s" % e)
    return 0
