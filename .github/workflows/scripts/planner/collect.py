"""`planner collect`: outcomes of finished suite runs, and escape attribution.

Idempotent: an outcome event's id is derived from (run id, attempt), so a run that is seen twice
is written once. The watermark only limits how far back the next call looks.
"""

from __future__ import annotations

import re
import time
from datetime import timedelta
from urllib.parse import quote

from . import SCHEMA, gitutil, registry, schema, store
from .gh import Gh, GhError
from .util import SHA_RE, event_id, now_iso, parse_iso


def outcome_from_run(repo: str, run: dict, suite: str, decision_id: str | None, flake_hint: bool, ts: str) -> dict | None:
    if run.get("status") != "completed" or not run.get("conclusion"):
        return None
    start = run.get("run_started_at") or run.get("created_at")
    end = run.get("updated_at")
    if not start or not end or not SHA_RE.match(run.get("head_sha") or ""):
        return None
    prs = run.get("pull_requests") or []
    pr = int(prs[0]["number"]) if prs and isinstance(prs[0], dict) and "number" in prs[0] else 0
    attempt = int(run.get("run_attempt") or 1)
    body = {
        "schema": SCHEMA, "type": "outcome", "ts": ts, "repo": repo, "decision_id": decision_id, "pr": pr,
        "head_sha": run["head_sha"], "suite": suite, "run_id": int(run["id"]), "attempt": attempt,
        "event": str(run.get("event", "")), "branch": str(run.get("head_branch") or "")[:200],
        "conclusion": str(run["conclusion"]),
        "started": parse_iso(start).strftime("%Y-%m-%dT%H:%M:%SZ"), "completed": parse_iso(end).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "minutes": round(max(0.0, (parse_iso(end) - parse_iso(start)).total_seconds() / 60), 1),
        "url": str(run.get("html_url", ""))[:300], "flake_hint": bool(flake_hint),
    }
    body["id"] = event_id("out", {"run": body["run_id"], "attempt": attempt})
    return body


def latest_decisions(events: list) -> dict:
    """(pr, head_sha) -> newest decision event."""
    out: dict = {}
    for e in events:
        if e.get("type") == "decision":
            k = (e["pr"], e["head_sha"])
            if k not in out or e["ts"] >= out[k]["ts"]:
                out[k] = e
    return out


def is_scheduled_main(o: dict, default_branch: str) -> bool:
    return o["event"] in ("schedule", "workflow_dispatch", "push") and o.get("branch") == default_branch


def attribute_escapes(repo: str, outcomes: list, decisions_by_pr: dict, merged_between, cfg: dict, ts: str) -> list:
    """Escape events for confirmed failures of scheduled full runs.

    outcomes: all outcome events (any suite). merged_between(good_sha, bad_sha) -> [(pr, sha)] of
    pull requests merged in (good, bad]. A failure is confirmed when the previous scheduled run of
    the suite also failed or when this one failed again on a re-attempt. Only decisions that did
    not run the suite and touched a module it consumes become candidates.
    """
    ecfg = cfg["escape"]
    by_suite: dict = {}
    for o in outcomes:
        if o["event"] in ("schedule", "workflow_dispatch", "push") and o["minutes"] >= cfg["memory"]["min_full_minutes"]:
            by_suite.setdefault(o["suite"], []).append(o)
    out = []
    for suite, runs in by_suite.items():
        runs.sort(key=lambda o: o["completed"])
        for i, o in enumerate(runs):
            if o["conclusion"] != "failure":
                continue
            prev_fail = i > 0 and runs[i - 1]["conclusion"] == "failure"
            confirmed_by = None
            if o["attempt"] > 1:
                confirmed_by = "failed again on attempt %d" % o["attempt"]
            elif prev_fail:
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


def run(a, gh: Gh | None = None, sleep=time.sleep) -> int:
    from . import config
    from .store import DirStore
    gh = gh or Gh()
    cfg = config.load(gitutil.show(a.git_dir, "HEAD", a.config_path))
    reg = registry.load_registry(a.git_dir, "HEAD", cfg)
    suites = sorted(n for n, s in reg.items() if s.layer in cfg["expensive_layers"] and not s.is_call_only)
    ds = DirStore(a.store_dir)
    events = ds.events()
    now = a.now or now_iso()
    wm = [e for e in events if e["type"] == "watermark"]
    since = max(e["since"] for e in wm) if wm else (parse_iso(now) - timedelta(days=int(a.backfill_days))).strftime("%Y-%m-%dT%H:%M:%SZ")
    known = {e["id"] for e in events}
    decisions = latest_decisions(events)
    st = store.GhStore(gh, a.repo, a.branch)
    written, new_outcomes = 0, []
    for suite in suites:
        path = "repos/%s/actions/workflows/%s/runs?status=completed&created=%s&per_page=100" % (a.repo, suite, quote(">=" + since))
        try:
            runs = gh.api("GET", path, paginate=True) or []
        except GhError as e:
            print("::warning::cannot list runs of %s: %s" % (suite, e))
            continue
        flat = []
        for r in runs:
            flat += r.get("workflow_runs", [r]) if isinstance(r, dict) else []
        for r in sorted(flat, key=lambda x: x.get("updated_at", "")):
            attempt = int(r.get("run_attempt") or 1)
            oid = event_id("out", {"run": r.get("id"), "attempt": attempt})
            if oid in known:
                continue
            flake = False
            if attempt > 1 and r.get("conclusion") == "success":
                for k in range(1, attempt):
                    try:
                        prior = gh.api("GET", "repos/%s/actions/runs/%s/attempts/%d" % (a.repo, r["id"], k))
                        if prior and prior.get("conclusion") == "failure":
                            flake = True
                    except GhError:
                        pass
            prs = r.get("pull_requests") or []
            pr = int(prs[0]["number"]) if prs and isinstance(prs[0], dict) and "number" in prs[0] else 0
            d = decisions.get((pr, r.get("head_sha")))
            o = outcome_from_run(a.repo, r, suite, d["id"] if d else None, flake, now)
            if not o or schema.validate(o):
                continue
            if written >= int(a.max_events):
                break
            st.put(store.path_for(o), o, "chore(ci-decisions): outcome %s %s" % (suite, o["conclusion"]))
            written += 1
            new_outcomes.append(o)
            known.add(oid)
    outcomes = [e for e in events if e["type"] == "outcome"] + new_outcomes
    existing_esc = {e["id"] for e in events if e["type"] == "escape"}
    by_pr = {k[0]: v for k, v in decisions.items()}
    for esc in attribute_escapes(a.repo, outcomes, by_pr, merged_prs_from_git(a.git_dir), cfg, now):
        if esc["id"] in existing_esc or schema.validate(esc):
            continue
        st.put(store.path_for(esc), esc, "chore(ci-decisions): escape %s" % esc["suite"])
        written += 1
    if written < int(a.max_events):
        wm_body = {"schema": SCHEMA, "type": "watermark", "ts": now, "repo": a.repo, "since": now}
        wm_body["id"] = event_id("wmk", wm_body)
        st.put(store.path_for(wm_body), wm_body, "chore(ci-decisions): collector watermark")
    print("collector: wrote %d events" % written)
    return 0
